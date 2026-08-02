"""Comparing two captured states.

A line diff of a KDE config file is noise: groups reorder, comments move,
`[$Version] update_info` churns on every point release. The signal is at the
key level, and above that at the level of "which component stopped working".

Five sections, most-signal-first:

  SEMANTIC      a component's status changed, or its contents changed under a
                stable name -- the class that is otherwise invisible
  SETTINGS      key-level, per-layer, with the resolved value alongside so an
                un-pinned-but-still-inherited key does not read as a loss
  UNMANIFESTED  files that changed and are in no manifest. The section whose
                absence cost this project a pre-change state
  BYTES         unparseable entries, hash only
  INVENTORY     packages appeared, vanished, or changed under a stable name
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import kconfig

# update_info churns on every KDE point release and means nothing to a human.
NOISE = {("$Version", "update_info")}


@dataclass
class Change:
    kind: str                      # + | - | ~
    where: str
    what: str
    before: str = ""
    after: str = ""
    note: str = ""


@dataclass
class Report:
    semantic: list[Change] = field(default_factory=list)
    settings: list[Change] = field(default_factory=list)
    unmanifested: list[Change] = field(default_factory=list)
    byte_only: list[Change] = field(default_factory=list)
    inventory: list[Change] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.semantic or self.settings or self.unmanifested
                    or self.byte_only or self.inventory)

    def count(self) -> int:
        return (len(self.semantic) + len(self.settings) + len(self.unmanifested)
                + len(self.byte_only) + len(self.inventory))


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _manifest_paths(root: Path) -> set[str]:
    entries = _load(root / "manifest.json") or []
    return {e["source"] for e in entries if e.get("source")}


def _captured(root: Path) -> dict[str, dict]:
    entries = _load(root / "manifest.json") or []
    return {e["path"]: e for e in entries if e.get("path")}


def compare(before: Path, after: Path) -> Report:
    report = Report()
    _semantic(before, after, report)
    _settings(before, after, report)
    _inventory(before, after, report)
    _unmanifested(before, after, report)
    return report


# ---------------------------------------------------------------------------

def _semantic(before: Path, after: Path, report: Report) -> None:
    old = {r["label"]: r for r in (_load(before / "state/audit.json") or [])}
    new = {r["label"]: r for r in (_load(after / "state/audit.json") or [])}

    for label in sorted(set(old) | set(new)):
        a, b = old.get(label), new.get(label)
        if a is None:
            report.semantic.append(Change("+", label, b.get("live") or "", note="newly configured"))
            continue
        if b is None:
            report.semantic.append(Change("-", label, a.get("live") or "", note="no longer configured"))
            continue
        if a.get("status") != b.get("status"):
            # These three need three different fixes, so name them apart.
            note = {"MISSING": "the thing it points at is gone -- lol-kde install",
                    "DEGRADED": "resolves but will not render as intended",
                    "UNSET": "nothing sets it; a KDE default is in effect"}.get(
                        b.get("status", ""), "")
            report.semantic.append(Change("~", label, "status",
                                          a.get("status", ""), b.get("status", ""), note))
        elif a.get("live") != b.get("live"):
            report.semantic.append(Change("~", label, "value",
                                          a.get("live") or "", b.get("live") or "",
                                          "pointer changed -- lol-kde apply"))

    _outputs(before, after, report)

    # Contents changed under a stable name. Nothing above catches this; it is
    # how an edit to reduce_window_opacity would otherwise go unreported.
    old_files, new_files = _captured(before), _captured(after)
    for path in sorted(set(old_files) & set(new_files)):
        a, b = old_files[path], new_files[path]
        if a.get("sha256") and a.get("sha256") != b.get("sha256"):
            if path.endswith(".kvconfig"):
                report.semantic.append(
                    Change("~", path, "contents", note="same theme, different settings"))


def _fractional(scale) -> str:
    """A scale that does not divide 2560x1440 evenly costs you sharp edges.

    The logical grid has to land on physical pixels. 2560/1.2 is 2133.33, so
    it does not; 2560/1.25 is 2048, so it does. This is why a display can look
    subtly badly antialiased on one axis and fine on the other.
    """
    try:
        value = float(scale)
    except (TypeError, ValueError):
        return ""
    return "" if value in (1.0, 2.0, 3.0) else "fractional scale"


def _outputs(before: Path, after: Path, report: Report) -> None:
    """Display geometry is semantic state, not just a JSON key."""
    def index(root: Path) -> dict[str, dict]:
        data = _load(root / "state/outputs.json") or {}
        return {o.get("name", str(i)): o
                for i, o in enumerate(data.get("outputs", []))}

    old, new = index(before), index(after)
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name), new.get(name)
        if a is None:
            report.semantic.append(Change("+", f"output {name}", "connected"))
            continue
        if b is None:
            report.semantic.append(Change("-", f"output {name}", "disconnected"))
            continue
        for field_name in ("scale", "enabled", "rotation"):
            if a.get(field_name) != b.get(field_name):
                report.semantic.append(
                    Change("~", f"output {name}", field_name,
                           str(a.get(field_name)), str(b.get(field_name)),
                           _fractional(b.get(field_name)) if field_name == "scale" else ""))
        for field_name in ("geometry", "pos", "size"):
            if a.get(field_name) != b.get(field_name):
                report.semantic.append(
                    Change("~", f"output {name}", field_name,
                           json.dumps(a.get(field_name), sort_keys=True),
                           json.dumps(b.get(field_name), sort_keys=True)))


def _settings(before: Path, after: Path, report: Report) -> None:
    old_files, new_files = _captured(before), _captured(after)
    for path in sorted(set(old_files) | set(new_files)):
        if path.endswith(".json"):
            _json_settings(before, after, path, report)
            continue
        a_path, b_path = before / "files" / path, after / "files" / path
        if not a_path.is_file() and not b_path.is_file():
            continue
        if not _parseable(path):
            a, b = old_files.get(path, {}), new_files.get(path, {})
            if a.get("sha256") != b.get("sha256"):
                report.byte_only.append(Change("~", path, "bytes",
                                               (a.get("sha256") or "")[:12],
                                               (b.get("sha256") or "")[:12]))
            continue
        _ini_settings(a_path, b_path, path, report)


def _parseable(path: str) -> bool:
    return not path.endswith((".png", ".svg", "/user", ".gz"))


def _read(path: Path) -> dict[tuple[str, str], str]:
    if not path.is_file():
        return {}
    try:
        parsed = kconfig.read_ini(path)
    except Exception:
        return {}
    out = {}
    for group in parsed.sections():
        for key, value in parsed.items(group):
            out[(group, key)] = value
    return out


def _ini_settings(a_path: Path, b_path: Path, label: str, report: Report) -> None:
    a, b = _read(a_path), _read(b_path)
    for group, key in sorted(set(a) | set(b)):
        if (group, key) in NOISE:
            continue
        old, new = a.get((group, key)), b.get((group, key))
        if old == new:
            continue
        where = f"{label} [{group}]"
        if old is None:
            report.settings.append(Change("+", where, key, "", new or ""))
        elif new is None:
            report.settings.append(Change("-", where, key, old, "",
                                          "no longer pinned in this layer"))
        else:
            report.settings.append(Change("~", where, key, old, new))


def _json_settings(before: Path, after: Path, label: str, report: Report) -> None:
    a = _load(before / "files" / label)
    b = _load(after / "files" / label)
    if a is None and b is None:
        return
    flat_a, flat_b = dict(_flatten(a)), dict(_flatten(b))
    for key in sorted(set(flat_a) | set(flat_b)):
        old, new = flat_a.get(key), flat_b.get(key)
        if old == new:
            continue
        kind = "+" if old is None else "-" if new is None else "~"
        report.settings.append(Change(kind, label, key,
                                      "" if old is None else str(old),
                                      "" if new is None else str(new)))


def _flatten(node, trail: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{trail}.{key}" if trail else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _flatten(value, f"{trail}[{index}]")
    else:
        yield trail or ".", node


def _inventory(before: Path, after: Path, report: Report) -> None:
    old = _load(before / "state/inventory.json") or {}
    new = _load(after / "state/inventory.json") or {}
    for kind in sorted(set(old) | set(new)):
        a = {p["name"]: p for p in old.get(kind, [])}
        b = {p["name"]: p for p in new.get(kind, [])}
        for name in sorted(set(a) - set(b)):
            report.inventory.append(Change("-", kind, name, note="removed"))
        for name in sorted(set(b) - set(a)):
            size = b[name].get("size", 0)
            report.inventory.append(Change("+", kind, name, note=f"{size} bytes"))
        for name in sorted(set(a) & set(b)):
            if a[name].get("identity") and a[name].get("identity") != b[name].get("identity"):
                report.inventory.append(
                    Change("~", kind, name, note="content changed, name unchanged"))


# ---------------------------------------------------------------------------
# The section whose absence cost a pre-change state

KDE_SHAPED = ("rc", ".conf", ".json", ".kvconfig", ".ini", ".css")


def _sweep(root: Path) -> dict[str, dict]:
    path = root / "sweep.json.gz"
    if not path.is_file():
        return {}
    try:
        return json.loads(gzip.decompress(path.read_bytes()).decode())
    except (OSError, ValueError):
        return {}


def _unmanifested(before: Path, after: Path, report: Report) -> None:
    old, new = _sweep(before), _sweep(after)
    if not old or not new:
        return
    known = _manifest_paths(before) | _manifest_paths(after)
    known_relative = {Path(p).name for p in known} | {
        str(Path(p)).split("/.config/", 1)[-1] for p in known if "/.config/" in p}

    for area in ("config", "data"):
        a, b = old.get(area, {}), new.get(area, {})
        for relative in sorted(set(a) | set(b)):
            if relative in known_relative or Path(relative).name in known_relative:
                continue
            entry_a, entry_b = a.get(relative), b.get(relative)
            if entry_a == entry_b:
                continue
            kind = "+" if entry_a is None else "-" if entry_b is None else "~"
            note = "collapsed subtree" if (entry_b or {}).get("collapsed") else ""
            report.unmanifested.append(
                Change(kind, area, relative, note=note or _rank_note(relative)))

    # KDE-shaped names first: those are the ones that might belong in the
    # manifest. Everything else is application noise.
    report.unmanifested.sort(key=lambda c: (not _looks_like_config(c.what), c.what))


def _looks_like_config(relative: str) -> bool:
    name = Path(relative).name
    shallow = len(Path(relative).parts) <= 2
    return shallow and (name.endswith(KDE_SHAPED) or name.startswith(
        ("kde", "kwin", "plasma", "kcm", "ksplash", "kscreen")))


def _rank_note(relative: str) -> str:
    return "looks load-bearing" if _looks_like_config(relative) else ""
