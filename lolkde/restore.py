"""Putting a snapshot back.

The design is `docs/restore-design.md` and it is not re-derived here. What
this module adds is the arithmetic: read the four facts a snapshot records
about each key, compare them with the four facts that are live now, and emit a
plan of *desired end states* -- never a sequence of deltas, so that running the
same command twice is the recovery mechanism (design section 6.2).

Two things are worth knowing before reading further, because both are
counter-intuitive and both cost something to learn:

* **Byte copies in a snapshot are evidence, not the restore mechanism.**
  Restoring KDE config by copying files back over a running session produces a
  state that is neither the snapshot nor what you had. Every write here goes
  through `repair.py`, one key at a time.

* **"Absent, inherited" cannot be expressed with `kwriteconfig6 --delete`.**
  It writes a tombstone that blocks the cascade. `repair.unpin()` is the
  mechanism; see restore-design section 1a, and the hard rule in CLAUDE.md
  about what may never be used to announce it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import journal, kconfig, paths, repair, resolve, snapshot

# Where a key's value came from. The distinction is the whole point: a value
# that resolves correctly from the wrong layer is right today and fragile
# tomorrow, because the next global-theme apply rewrites kdedefaults.
USER = "user"            # ~/.config -- pinned, survives a theme apply
DEFAULTS = "defaults"    # ~/.config/kdedefaults -- owned by the applied theme
SYSTEM = "system"        # XDG_CONFIG_DIRS
ABSENT = "absent"        # resolves nowhere

# What a step will do.
SET = "set"              # pin a value in the user layer
UNPIN = "unpin"          # remove the user-layer entry, exposing the cascade
SAME = "same"            # already correct, in the right layer -- no write
EXTRA = "extra"          # live has a key the snapshot does not (reported only)

# What a step did.
OK = "ok"
PIN_LOST = "pin-lost"    # correct value, wrong layer
STALE = "stale"          # correct on disk, running session not told
DIVERGED = "diverged"
FAILED = "failed"
SKIPPED = "skipped"      # the run aborted before reaching this step


def store() -> Path:
    return snapshot.store() / "restores"


# ---------------------------------------------------------------- components


def components() -> dict[str, list[tuple[str, str, str]]]:
    """Named bundles of (file, group, key), derived rather than transcribed.

    `resolve.SIMPLE_POINTERS` is the source of truth for six of the seven
    pointers; the decoration pair lives in its own group and is added here.
    A hardcoded list drifted once already, which is why `pointer_kinds()`
    exists at all.
    """
    bundles: dict[str, list[tuple[str, str, str]]] = {}
    slug = {"Widget style": "widget-style", "Colour scheme": "colour-scheme",
            "Icon theme": "icons", "Cursor theme": "cursor",
            "Plasma style": "plasma-style", "Splash screen": "splash"}
    for pointer in resolve.SIMPLE_POINTERS:
        label = resolve.POINTER_LABELS[pointer]
        bundles[slug.get(label, label.lower().replace(" ", "-"))] = [pointer]
    bundles["decoration"] = [
        ("kwinrc", repair.DECO_GROUP, "library"),
        ("kwinrc", repair.DECO_GROUP, "theme"),
        # BorderSize sits in the same group and is a deliberate user choice
        # against the theme's declared value. Restoring the pair without it
        # is how the two drift apart.
        ("kwinrc", repair.DECO_GROUP, "BorderSize"),
    ]
    bundles["lookandfeel"] = [("kdeglobals", "KDE", "LookAndFeelPackage")]
    return bundles


def all_keys(selected: list[str]) -> list[tuple[str, str, str]]:
    bundles = components()
    keys: list[tuple[str, str, str]] = []
    for name in selected:
        keys.extend(bundles.get(name, []))
    return keys


# ------------------------------------------------------------------ snapshot


@dataclass
class Layered:
    """The facts about one key, in one state of the world.

    `user_entry` is not redundant with `user`. A `[$d]` tombstone is an entry
    in the user layer that carries no value and blocks the cascade, so the key
    resolves to nothing and `layer` is ABSENT -- yet there is very much
    something in ~/.config that has to be removed. Asking "is it pinned here"
    misses it; asking "does this layer say anything about it" does not.
    """
    resolved: str | None
    layer: str
    user: str | None
    defaults: str | None
    user_entry: bool = False

    @property
    def pinned(self) -> bool:
        return self.layer == USER


def _read(locate, file: str, group: str, key: str) -> Layered:
    """Walk the cascade lowest-first, letting each layer overwrite the last.

    `locate(directory)` returns the file holding that layer's copy, or None.
    The snapshot view and the live system differ only in that function, so
    they cannot end up disagreeing about what "inherited" means.
    """
    resolved, layer = None, ABSENT
    user_value, defaults_value, user_entry = None, None, False
    home = paths.config_home()

    for directory in paths.config_layers():
        path = locate(directory)
        if path is None or not path.is_file():
            continue
        parser = kconfig.read_ini(path)
        state, option = kconfig._entry(parser, group, key)
        # Read through the option name it was found under, not the bare key:
        # a `[$i]`-flagged entry is stored decorated and `parser.get(group,
        # key)` raises NoOptionError on it.
        value = ((parser.get(group, option) or "").strip()
                 if state == "set" else None)
        kind = (USER if directory == home
                else DEFAULTS if directory == home / "kdedefaults" else SYSTEM)

        if kind == USER:
            user_value, user_entry = value, state != "absent"
        elif kind == DEFAULTS:
            defaults_value = value

        if state == "deleted":
            resolved, layer = None, ABSENT
        elif state == "set":
            resolved, layer = value, kind

    return Layered(resolved, layer, user_value, defaults_value, user_entry)


class View:
    """A snapshot's captured bytes, read as a config cascade.

    The captured files are used as *evidence about layers* -- which is what
    the byte copies are for. Nothing here writes them anywhere.
    """

    def __init__(self, root: Path):
        self.root = root
        self.meta = _json(root / "meta.json") or {}
        self.by_source: dict[str, Path] = {}
        for entry in _json(root / "manifest.json") or []:
            if entry.get("status") == "captured" and entry.get("source"):
                self.by_source[entry["source"]] = root / "files" / entry["path"]

    def _captured(self, layer: Path, filename: str) -> Path | None:
        """Where this layer's copy of the file landed inside the snapshot.

        Computed with `snapshot._relative_capture_path`, the same function
        that put it there, so capture and restore cannot drift apart --
        design section 9 asks for exactly this. The recorded absolute source
        is the fallback, for snapshots whose layer layout no longer matches.
        """
        guess = self.root / "files" / snapshot._relative_capture_path(
            layer / filename)
        if guess.is_file():
            return guess
        return self.by_source.get(str(layer / filename))

    def facts(self, file: str, group: str, key: str) -> Layered:
        return _read(lambda directory: self._captured(directory, file),
                     file, group, key)

    @property
    def applied_theme(self) -> str:
        return self.meta.get("applied_theme", "")

    def env(self) -> dict:
        return _json(self.root / "state" / "env.json") or {}


def live_facts(file: str, group: str, key: str) -> Layered:
    """The same facts, read from the live cascade."""
    return _read(lambda directory: directory / file, file, group, key)


# ---------------------------------------------------------------------- plan


@dataclass
class Step:
    component: str
    file: str
    group: str
    key: str
    action: str
    want: str | None
    want_layer: str
    have: str | None
    have_layer: str
    note: str = ""
    outcome: str = ""
    detail: str = ""

    @property
    def writes(self) -> bool:
        return self.action in (SET, UNPIN)

    def describe(self) -> str:
        where = f"{self.file}:{self.group}:{self.key}"
        if self.action == SAME:
            return f"same     {where} = {self.have!r} ({self.have_layer})"
        if self.action == SET:
            return (f"set      {where} = {self.want!r} "
                    f"(was {self.have!r} from {self.have_layer})")
        if self.action == UNPIN:
            return (f"unpin    {where} -> inherit {self.want!r} "
                    f"(was pinned {self.have!r})")
        return f"extra    {where} = {self.have!r} -- not in the snapshot"


@dataclass
class Plan:
    snapshot_id: str
    root: Path
    selected: list[str]
    steps: list[Step] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)

    @property
    def writes(self) -> list[Step]:
        return [s for s in self.steps if s.writes]

    @property
    def unchanged(self) -> int:
        return sum(1 for s in self.steps if s.action == SAME)


def build(root: Path, selected: list[str]) -> Plan:
    """Compute desired end states. Reads only; writes nothing, ever."""
    view = View(root)
    plan = Plan(view.meta.get("id", root.name), root, selected)

    for component, keys in components().items():
        for file, group, key in keys:
            want = view.facts(file, group, key)
            have = live_facts(file, group, key)
            if component not in selected:
                if want.resolved != have.resolved:
                    plan.drift.append(
                        f"{component}: {file}:{group}:{key} is "
                        f"{have.resolved!r}, snapshot has {want.resolved!r}")
                continue
            plan.steps.append(_step(component, file, group, key, want, have))

    _preconditions(view, plan)
    return plan


def _step(component: str, file: str, group: str, key: str,
          want: Layered, have: Layered) -> Step:
    common = dict(component=component, file=file, group=group, key=key,
                  want=want.resolved, want_layer=want.layer,
                  have=have.resolved, have_layer=have.layer)

    if want.layer == USER:
        if have.resolved == want.resolved and have.layer == USER:
            return Step(action=SAME, **common)
        return Step(action=SET, **common)

    if want.resolved is None and not want.user_entry:
        # Absent everywhere in the snapshot. If it is also absent live, there
        # is nothing to do; a pointless write would pin a value nobody asked
        # for (design section 4.9).
        if have.resolved is None and not have.user_entry:
            return Step(action=SAME, **common)
        return Step(action=UNPIN, note="nothing inherits this key", **common)

    # Inherited in the snapshot. The user layer must say *nothing* -- not
    # "nothing that resolves". A `[$d]` tombstone resolves to nothing while
    # still being an entry that has to be removed, and asking about the
    # resolved value alone walks straight past it.
    if not have.user_entry and have.resolved == want.resolved:
        return Step(action=SAME, **common)
    if have.user_entry:
        return Step(action=UNPIN, **common)
    # The value differs and neither side is a user pin: the layer below moved
    # under us. That is section 3's territory -- kdedefaults is re-derived by
    # re-applying the look-and-feel package, never written by hand.
    return Step(action=SET,
                note="value differs in a layer restore must not write; "
                     "re-applying the look-and-feel package is the only "
                     "supported fix (restore-design section 3)",
                **common)


def _preconditions(view: View, plan: Plan) -> None:
    """Everything checkable before the first write. Design section 5."""
    if os.geteuid() == 0:
        plan.blockers.append(
            "running as root. There is no legitimate root invocation, and "
            "`sudo lol-kde restore` writes into /root/.config")

    if not (plan.root / "manifest.json").is_file():
        plan.blockers.append(f"{plan.root} has no manifest.json")
        return

    bad = _integrity(plan.root)
    if bad:
        plan.blockers.append(
            f"snapshot is corrupt: {len(bad)} file(s) do not match their "
            f"recorded hash ({', '.join(bad[:3])})")

    schema = view.meta.get("schema")
    known = getattr(snapshot, "SCHEMA", 1)
    if isinstance(schema, int) and schema > known:
        plan.blockers.append(
            f"snapshot schema {schema} is newer than this tool understands "
            f"({known}); restoring from it would be guesswork")

    # The single most useful thing this command can print: if the cascade's
    # shape changed, inherited values resolve differently even after a
    # perfect restore, and nothing in the plan can say so.
    before = view.env()
    for key in ("XDG_CONFIG_DIRS", "XDG_DATA_DIRS"):
        was, now = before.get(key), os.environ.get(key, "")
        if was is not None and was != now:
            plan.warnings.append(
                f"{key} changed since the snapshot:\n"
                f"    then: {was}\n    now:  {now}\n"
                "    inherited values may resolve differently even after a "
                "perfect restore")

    if _running("systemsettings"):
        plan.warnings.append(
            "systemsettings is running. It holds KCM state in memory and "
            "writes it back on the next Apply, silently undoing this restore")

    if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        plan.warnings.append("XDG_CURRENT_DESKTOP does not contain KDE")

    for step in plan.writes:
        if step.action == SET and repair._tool() is None:
            plan.blockers.append("kwriteconfig6 not found")
            break

    missing = _missing_packages(view)
    if missing:
        plan.warnings.append(
            "no longer installed: " + ", ".join(missing[:5])
            + ("" if len(missing) <= 5 else f" (+{len(missing) - 5} more)")
            + "\n    restore will put the pointer back; it cannot make it resolve")

    if plan.drift:
        plan.warnings.append(
            f"{len(plan.drift)} key(s) differ from the snapshot but are not "
            "selected, and will survive this restore, giving a mixture:\n    "
            + "\n    ".join(plan.drift[:4]))


def _integrity(root: Path) -> list[str]:
    bad = []
    for entry in _json(root / "manifest.json") or []:
        if entry.get("status") != "captured":
            continue
        path = root / "files" / entry["path"]
        if not path.is_file():
            bad.append(entry["path"])
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
            bad.append(entry["path"])
    return bad


def _missing_packages(view: View) -> list[str]:
    inventory = _json(view.root / "state" / "inventory.json") or {}
    gone = []
    for kind, items in inventory.items():
        if not isinstance(items, list):
            continue
        for item in items:
            name, path = item.get("name"), item.get("path")
            if path and not Path(path).exists():
                gone.append(f"{kind}/{name or path}")
    return gone


def _running(name: str) -> bool:
    try:
        done = subprocess.run(["pgrep", "-x", name], capture_output=True,
                              timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------- lock


class Lock:
    """A restore racing a snapshot is a state nobody can reconstruct."""

    def __init__(self, path: Path | None = None):
        self.path = path or (snapshot.store() / "lock")

    def acquire(self, break_stale: bool = False) -> str | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            holder = self._holder()
            if holder is not None and _pid_alive(holder) and not break_stale:
                return (f"lock held by pid {holder} ({self.path}). "
                        "If that process is gone, pass --break-lock")
            if holder is not None and not _pid_alive(holder) and not break_stale:
                return (f"lock held by pid {holder}, which is not running "
                        f"({self.path}). Pass --break-lock to take it")
            self.path.unlink(missing_ok=True)
            return self.acquire(break_stale=False)
        except OSError as exc:
            return str(exc)
        with os.fdopen(handle, "w") as stream:
            stream.write(str(os.getpid()))
        return None

    def release(self) -> None:
        if self._holder() == os.getpid():
            self.path.unlink(missing_ok=True)

    def _holder(self) -> int | None:
        try:
            return int(self.path.read_text().strip())
        except (OSError, ValueError):
            return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# --------------------------------------------------------------------- apply


@dataclass
class Outcome:
    directory: Path
    steps: list[Step]
    pre_snapshot: str = ""
    aborted: bool = False

    def tally(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for step in self.steps:
            counts[step.outcome or SAME] = counts.get(step.outcome or SAME, 0) + 1
        return counts

    @property
    def exit_code(self) -> int:
        if self.aborted:
            return 3
        if any(s.outcome in (DIVERGED, FAILED) for s in self.steps):
            return 1
        return 0


def run(plan: Plan, pre_snapshot: str = "", notify: bool = True) -> Outcome:
    """Execute a plan. Every step is a desired end state, so re-running is
    the recovery mechanism -- there is no resume and no rollback (section 6).

    `notify=False` keeps the whole run off the session bus and away from
    KWin: no `--notify` on the writes, no reconfigure. It exists so the tests
    can exercise this function for real against a temporary config tree
    instead of leaving it in the "built but never exercised" column.
    """
    directory = store() / time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "removed").mkdir(exist_ok=True)
    log = directory / "journal.jsonl"
    outcome = Outcome(directory, plan.steps, pre_snapshot)

    _append(log, {"event": "start", "snapshot": plan.snapshot_id,
                  "pre_snapshot": pre_snapshot,
                  "components": plan.selected,
                  "steps": len(plan.writes)})

    stopped_at: int | None = None
    for index, step in enumerate(plan.steps):
        if not step.writes:
            step.outcome = OK if step.action == SAME else EXTRA
            continue
        _append(log, {"event": "planned", **_record(step)})
        _quarantine(directory, step)
        try:
            result = (repair.write(step.file, step.group, step.key, step.want,
                                   notify=notify)
                      if step.action == SET
                      else repair.unpin(step.file, step.group, step.key,
                                        notify=notify))
        except Exception as exc:                       # noqa: BLE001
            step.outcome, step.detail = FAILED, str(exc)
            _append(log, {"event": "failed", **_record(step)})
            outcome.aborted = True
            stopped_at = index
            break

        step.detail = result.detail
        step.outcome = _judge(step, result)
        _append(log, {"event": "done", **_record(step)})

        # Stop at the first failure: later steps' preconditions are no longer
        # known to hold, and there is no --continue-on-error by design.
        if step.outcome == FAILED:
            outcome.aborted = True
            stopped_at = index
            break

    if stopped_at is not None:
        # Everything after the break was never attempted. Saying so is not
        # cosmetic: _verify used to compare those steps' live values against
        # the snapshot and stamp them DIVERGED -- the word this tool uses for
        # "a write landed and went wrong" -- for keys nothing had touched. The
        # printed summary then contradicted the journal, which is what ROADMAP
        # nominates as the record of where a run stopped.
        _mark_unreached(plan, from_index=stopped_at + 1)

    # Only if a kwinrc write was actually *attempted*. Asking KWin to re-read
    # its config after aborting before any kwinrc step is noise at best.
    if notify and any(s.file == "kwinrc" and s.writes
                      and s.outcome not in ("", SKIPPED) for s in plan.steps):
        repair.reconfigure_kwin()

    _verify(plan)
    _append(log, {"event": "end", "tally": outcome.tally()})
    journal.record("restore", snapshot=plan.snapshot_id,
                   directory=str(directory), tally=outcome.tally())
    return outcome


def _mark_unreached(plan: Plan, from_index: int) -> None:
    """Label steps the run never got to, so nothing else judges them."""
    for step in plan.steps[from_index:]:
        if step.writes and not step.outcome:
            step.outcome = SKIPPED
            step.detail = "not attempted -- the run stopped at an earlier step"


def _judge(step: Step, result: repair.WriteResult) -> str:
    if result.outcome == repair.FAILED:
        return FAILED
    if result.outcome == repair.STALE:
        return STALE
    if result.outcome == repair.TOMBSTONED:
        # Should be unreachable: restore never calls --delete. If it happens,
        # it is a real divergence and must not be reported as success.
        return DIVERGED
    return OK


def _record(step: Step) -> dict:
    return {"file": step.file, "group": step.group, "key": step.key,
            "action": step.action, "want": step.want, "was": step.have,
            "was_layer": step.have_layer, "outcome": step.outcome,
            "detail": step.detail}


def _quarantine(directory: Path, step: Step) -> None:
    """Nothing is unlinked, ever. Copy the file before touching it.

    `legacy --remove` uses rmtree, deliberately not followed here: that
    deletes packages you can reinstall, this touches state you cannot.
    """
    source = paths.config_home() / step.file
    if not source.is_file():
        return
    target = directory / "removed" / step.file
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)


def _verify(plan: Plan) -> None:
    """Final pass, always. Re-read the live cascade and compare.

    Verifying with the same instrument doctor uses means the two cannot
    disagree -- and CLAUDE.md's "7/7 ok is metadata agreeing with itself"
    applies here identically. This checks the cascade, not the screen.
    """
    for step in plan.steps:
        if step.outcome in (FAILED, EXTRA, SKIPPED):
            continue
        now = live_facts(step.file, step.group, step.key)
        if now.resolved != step.want:
            step.outcome = DIVERGED
            step.detail = (f"expected {step.want!r}, cascade resolves "
                           f"{now.resolved!r}")
        elif now.layer != step.want_layer and step.outcome != STALE:
            step.outcome = PIN_LOST
            step.detail = (f"correct value, but from {now.layer} where the "
                           f"snapshot had {step.want_layer} -- it will not "
                           "survive the next global-theme apply")


def _append(path: Path, record: dict) -> None:
    """Appended and fsynced before each step, so a kill still says where."""
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        pass


def changelog_row(outcome: Outcome) -> list[str]:
    """A paste-ready CHANGELOG.md row. This project requires one per change."""
    rows = ["| what | file | old value | new value | revert |",
            "|---|---|---|---|---|"]
    for step in outcome.steps:
        # Only steps that actually landed. A row claiming `Adwaita -> Layan`
        # for a step the run never reached is a false record of a change.
        if not step.writes or step.outcome not in (OK, STALE):
            continue
        revert = (f"`lol-kde restore {outcome.pre_snapshot} --apply --yes`"
                  if outcome.pre_snapshot else "*(no pre-restore snapshot)*")
        rows.append(f"| {step.component} `{step.group}/{step.key}` "
                    f"| `~/.config/{step.file}` | `{step.have}` "
                    f"| `{step.want}` | {revert} |")
    return rows if len(rows) > 2 else []
