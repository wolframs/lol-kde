"""Removing themes of a previous Plasma generation, and only what they own.

A machine upgraded from one Plasma major version to the next accumulates
global themes built for the old one. They still *appear* in System Settings,
they still half-apply, and their components are indistinguishable from the
current ones by name alone. This works out which are which, and -- the part
that takes the care -- which of their components are safe to take with them.

The safety rule is one sentence: **a component is removable only if no theme
that survives references it, and it is not live.** Layan and Stone both point
at the `Tela` icon theme; removing Stone must not take Tela with it. That case
is real on this machine and is why this is a graph problem rather than a list.

Nothing is unlinked. Removals are *moved* into `~/.lol-kde/pruned/<ts>/`,
path-preserved, with a manifest -- so undoing is a move back, and quarantining
a gigabyte costs no extra disk. `legacy --remove` uses `shutil.rmtree` and
that was defensible for packages you can refetch one at a time; a bulk sweep
across four component kinds is not the same bet.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import journal, kconfig, paths, resolve, snapshot

# (kind, (config file, group, key)) -- where a look-and-feel manifest names
# each component. Derived from the same pointers `resolve` audits, plus the
# wallpaper, which is not a pointer but is owned the same way.
POINTERS = [
    ("plasma-style", ("plasmarc", "Theme", "name")),
    ("colour-scheme", ("kdeglobals", "General", "ColorScheme")),
    ("icons", ("kdeglobals", "Icons", "Theme")),
    ("cursors", ("kcminputrc", "Mouse", "cursorTheme")),
    ("decoration", ("kwinrc", "org.kde.kdecoration2", "theme")),
    ("splash", ("ksplashrc", "KSplash", "Theme")),
    ("wallpaper", ("plasmarc", "Wallpaper", "Image")),
    ("switcher", ("kwinrc", "WindowSwitcher", "LayoutName")),
]

# The wallpaper and the splash are written as **bare** groups in every real
# `contents/defaults` -- `[Wallpaper]` and `[KSplash]`, not `[plasmarc][Wallpaper]`
# and `[ksplashrc][KSplash]`. `parse_lookandfeel_defaults` keys a bare group as
# `(group, "")`, which matched no POINTERS entry, so `components()` returned
# neither kind for any theme ever installed.
#
# Consequence, found by review on 2026-08-02 and reproduced: `referenced_by()`
# could not see that a surviving theme needed a wallpaper, so `prune` would
# quarantine the image the desktop was currently painting and refuse nothing.
# Checked against all 10 packages on this machine and the shipped Breeze: 10
# bare `[Wallpaper]`, 6 bare `[KSplash]`, zero in the qualified form.
BARE_GROUP_ALIASES = {
    ("plasmarc", "Wallpaper"): ("Wallpaper", ""),
    ("ksplashrc", "KSplash"): ("KSplash", ""),
}


def live_pointers() -> list[tuple[str, tuple[str, str, str]]]:
    """(kind, the key the *live* value is under) for every pointer that has one.

    `POINTERS` holds the group a **manifest** declares each component in, which
    is not always where KDE stores the applied value. Using it to read the live
    config silently found nothing for the task switcher, which is declared as
    `[WindowSwitcher] LayoutName` and only ever written to `[TabBox] LayoutName`.

    That mattered because the live value feeds the guard that protects whatever
    is *in use right now*, independently of which theme declared it. With the
    lookup coming back empty, `--drop <layout>` would quarantine the switcher
    the session is using and refuse nothing. Found by review on 2026-08-03.
    """
    out: list[tuple[str, tuple[str, str, str]]] = []
    for kind, pointer in POINTERS:
        live = resolve.LIVE_POINTERS.get(pointer, pointer)
        if live is not None:
            out.append((kind, live))
    return out


def live_wallpapers() -> list[Path]:
    """Every wallpaper the desktop is painting right now.

    The same defect as the switcher above, and worse, because every machine
    has a wallpaper while few have a user-installed switcher. `[plasmarc]
    [Wallpaper] Image` is where a *manifest* declares one; there is no live
    plasmarc key at all. The applied value is a `file://` URL per containment
    inside `plasma-org.kde.plasma.desktop-appletsrc`:

        [Containments][1][Wallpaper][org.kde.image][General]
        Image=file:///home/…/.local/share/wallpapers/Layan

    So the live-value guard never covered the wallpaper either, and on a
    machine whose only *declared* reference to an image was a theme being
    pruned, `prune` would move the image on screen into quarantine.

    Returned as the package directory (`…/wallpapers/<name>`) where the URL
    points inside one, because that is the granularity `locate` removes at.
    """
    parser = kconfig.read_ini(
        paths.config_home() / "plasma-org.kde.plasma.desktop-appletsrc")
    found: list[Path] = []
    for section in parser.sections():
        if "][Wallpaper][" not in f"[{section}]":
            continue
        for option in parser.options(section):
            if kconfig.split_flags(option)[0] != "Image":
                continue
            raw = (parser.get(section, option) or "").strip()
            if not raw.startswith("/") and not raw.startswith("file://"):
                continue        # a plugin id or a colour, not a path
            path = Path(unquote(urlparse(raw).path if "://" in raw else raw))
            for candidate in [path, *path.parents]:
                if candidate.parent.name == "wallpapers":
                    path = candidate
                    break
            if path not in found:
                found.append(path)
    return found


def look_and_feel_dir() -> Path:
    return paths.data_home() / "plasma/look-and-feel"


def style_dir() -> Path:
    return paths.data_home() / "plasma/desktoptheme"


def locate(kind: str, name: str) -> list[Path]:
    """Every user-level path a component name could refer to.

    System paths are deliberately absent: `/usr` is the package manager's and
    nothing here may touch it.
    """
    data = paths.data_home()
    if kind == "plasma-style":
        return [style_dir() / name]
    if kind == "splash":
        return [look_and_feel_dir() / name]
    if kind == "icons" or kind == "cursors":
        return [data / "icons" / name, paths.HOME / ".icons" / name]
    if kind == "decoration":
        return [data / "aurorae/themes" / name.replace(resolve.AURORAE_PREFIX, "")]
    if kind == "wallpaper":
        return [data / "wallpapers" / name]
    if kind == "switcher":
        # The declared name is a KPackage id, and a package is not obliged to
        # live in a directory of that name. Ask `resolve` for the mapping and
        # keep only what is under the user's data home -- a theme naming the
        # Plasma 5 stock switcher resolves to nothing at all, which is right:
        # there is no directory to quarantine.
        hit = resolve.tabbox_layouts().get(name)
        return [hit] if hit and data in hit.parents else []
    if kind == "colour-scheme":
        base = data / "color-schemes"
        hits = [base / f"{name}.colors"]
        if base.is_dir():
            # Colour scheme filenames are not identifiers: `Sweet-Ambar-Blue`
            # lives in `SweetAmbarBlue.colors`. Match the internal Name=.
            hits += [f for f in sorted(base.glob("*.colors"))
                     if kconfig.get(f, "General", "Name") == name]
        return hits
    return []


def components(theme_dir: Path) -> dict[str, tuple[str, list[Path]]]:
    """{kind: (declared name, existing user-level paths)} for one package."""
    declared = kconfig.parse_lookandfeel_defaults(theme_dir / "contents/defaults")
    out: dict[str, tuple[str, list[Path]]] = {}
    for kind, (file, group, key) in POINTERS:
        section = declared.get((file, group))
        if not section:
            section = declared.get(BARE_GROUP_ALIASES.get((file, group), ()))
        name = (section or {}).get(key, "").strip()
        if name:
            out[kind] = (name, [p for p in locate(kind, name) if p.exists()])
    return out


def is_previous_generation(theme_dir: Path) -> bool | None:
    """Was this global theme built for the previous Plasma major version?

    The honest signal is the Plasma style it points at. A Plasma style with
    `metadata.desktop` and no `metadata.json` predates Plasma 5.19, which puts
    the theme that ships it firmly in the previous generation.

    Things that do *not* work, both checked on a real machine:

    - `X-Plasma-APIVersion`. `Gently-Dark-Global-6` is a current store entry
      and does not set it; Layan does. Absence proves nothing.
    - Install dates. These themes came across in a bulk copy from the old
      system, so every mtime is the date of the copy.

    Returns None when there is no evidence either way -- a theme that declares
    no Plasma style, or one whose style is not installed.
    """
    declared = kconfig.parse_lookandfeel_defaults(theme_dir / "contents/defaults")
    name = (declared.get(("plasmarc", "Theme")) or {}).get("name", "").strip()
    if not name:
        return None

    # Resolve the style the way KDE does -- across every data dir, highest
    # priority first -- not just in the user directory. Two false positives
    # were reproduced by review on 2026-08-02 by looking only at the user copy:
    #
    # - Every Breeze-derived theme declares `name=default`. One Plasma 5
    #   leftover at `~/.local/share/plasma/desktoptheme/default` shadows the
    #   system style of that name, and condemned *every* such theme at once,
    #   current ones included, along with the components they exclusively owned.
    # - A current store theme whose style still ships only `metadata.desktop`
    #   -- `legacy.py`'s own docstring concedes these exist and still work.
    #
    # So: the *resolved* style must lack `metadata.json`, and a system-provided
    # style is never evidence about a user-installed theme's generation.
    resolved = None
    for base in paths.data_dirs():
        candidate = base / "plasma/desktoptheme" / name
        if candidate.is_dir():
            resolved = candidate
            break
    if resolved is None:
        return None
    if (resolved / "metadata.json").is_file():
        return False
    if resolved.parent != style_dir():
        # The winning style is system-provided. Distro packaging is not a
        # statement about this theme, and it is not ours to judge either way.
        return None
    return True


@dataclass
class Removal:
    kind: str            # "global-theme" | a component kind | "orphan-style"
    name: str
    path: Path
    reason: str
    size: int = 0


@dataclass
class Plan:
    remove: list[Removal] = field(default_factory=list)
    kept_themes: list[str] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    applied: str = ""
    undecided: list[str] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(r.size for r in self.remove)


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _, files in os.walk(path, onerror=lambda _: None):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                pass
    return total


def build(include_orphan_styles: bool = True) -> Plan:
    """Work out what may go. Reads only."""
    plan = Plan()
    root = look_and_feel_dir()
    if not root.is_dir():
        return plan

    live = resolve.live_settings()
    plan.applied = (live.get(("kdeglobals", "KDE")) or {}).get(
        "LookAndFeelPackage", "")

    themes = {d.name: d for d in sorted(root.iterdir()) if d.is_dir()}
    doomed: set[str] = set()
    for name, directory in themes.items():
        verdict = is_previous_generation(directory)
        if verdict is True:
            doomed.add(name)
        elif verdict is None:
            plan.undecided.append(name)

    # The applied theme is never removable, whatever its generation.
    doomed.discard(plan.applied)
    kept = set(themes) - doomed
    plan.kept_themes = sorted(kept)

    # A splash-only package orphaned by a doomed theme goes with it: it is a
    # look-and-feel package that declares nothing but a splash screen, so no
    # surviving theme can be pointing at it.
    for name in sorted(kept):
        if name == plan.applied:
            continue
        declared = components(themes[name])
        if set(declared) <= {"splash"} and declared:
            splash_name = declared.get("splash", ("", []))[0]
            if splash_name == name and any(
                    components(themes[d]).get("splash", ("", []))[0] == name
                    for d in doomed):
                doomed.add(name)
    kept = set(themes) - doomed
    plan.kept_themes = sorted(kept)

    # Everything a surviving theme -- or the live configuration -- points at.
    #
    # "Surviving theme" includes system-wide packages under /usr. They are not
    # removable, but they hold references, and `build()` used to consider only
    # the user-level directory while `referenced_by()` walked both. The two
    # disagreed, so `--drop X` was refused while a plain `prune` removed the
    # same X as unreferenced. Reproduced by review on 2026-08-02.
    protected: set[str] = set()
    survivors = [themes[name] for name in kept]
    survivors += [d for d in all_theme_dirs() if d.name not in themes]
    for directory in survivors:
        for _, (_, found) in components(directory).items():
            protected.update(str(p) for p in found)
    for kind, (file, group, key) in live_pointers():
        value = (live.get((file, group)) or {}).get(key, "").strip()
        if value:
            protected.update(str(p) for p in locate(kind, value) if p.exists())
    protected.update(str(p) for p in live_wallpapers() if p.exists())

    for name in sorted(doomed):
        plan.remove.append(Removal("global-theme", name, themes[name],
                                   "previous-generation Plasma style"))
        for kind, (component, found) in components(themes[name]).items():
            for path in found:
                if str(path) in protected:
                    plan.protected.append(f"{kind} {component} (kept by another theme)")
                elif not any(r.path == path for r in plan.remove):
                    plan.remove.append(
                        Removal(kind, component, path, f"only {name} referenced it"))

    if include_orphan_styles:
        referenced = set()
        for name in themes:
            found = components(themes[name]).get("plasma-style")
            if found:
                referenced.update(str(p) for p in found[1])
        for directory in sorted(style_dir().iterdir()) if style_dir().is_dir() else []:
            if not directory.is_dir() or str(directory) in referenced:
                continue
            if (directory / "metadata.json").is_file():
                continue          # modern and unreferenced: not our business
            plan.remove.append(Removal("orphan-style", directory.name, directory,
                                       "legacy, referenced by no global theme"))

    for removal in plan.remove:
        removal.size = _size(removal.path)
    return plan


def all_theme_dirs() -> list[Path]:
    """Every installed global theme, user-level and system-wide.

    System packages are not ours to remove, but they are very much allowed to
    be *using* something -- a distro theme under `/usr` pointing at an icon set
    in `~/.local/share` is an ordinary arrangement, and dropping that icon set
    breaks it.
    """
    roots = [look_and_feel_dir()]
    roots += [d / "plasma/look-and-feel" for d in paths.data_dirs()[1:]]
    out: list[Path] = []
    for root in roots:
        if root.is_dir():
            out += [d for d in sorted(root.iterdir()) if d.is_dir()]
    return out


def referenced_by(name: str) -> list[str]:
    """Which installed global themes point at this component, by any kind."""
    holders = []
    for theme in all_theme_dirs():
        for _, (component, _) in components(theme).items():
            if component == name and theme.name not in holders:
                holders.append(theme.name)
    return holders


def holders_of(wanted: set[str]) -> list[str]:
    """Which themes point at any of these *paths*.

    Names are not identifiers and comparing them misses real references.
    A colour scheme called `Sweet-Ambar-Blue` lives in `SweetAmbarBlue.colors`;
    `locate()` deliberately matches either form, but a theme's `defaults` and
    `kdeglobals` only ever store one of them. So `--drop SweetAmbarBlue` was
    correctly refused while `--drop Sweet-Ambar-Blue` found the same file and
    was refused by nothing -- reproduced by review on 2026-08-02.

    Resolve both sides to paths first, then ask whether the sets intersect.
    """
    holders: list[str] = []
    for theme in all_theme_dirs():
        for _, (_, found) in components(theme).items():
            if any(str(p) in wanted for p in found) and theme.name not in holders:
                holders.append(theme.name)
    return holders


def build_drop(names: list[str]) -> tuple[Plan, list[str]]:
    """A plan for explicitly named components. Returns (plan, refusals).

    This is the deliberate escape hatch from `build()`'s rule that anything
    unreferenced is left alone. "Unreferenced" is not "unwanted" -- Tela-dark
    sits next to the Tela in use -- so removing that content is a decision a
    person makes by name, never one this program infers. What it still will
    not do is drop something a surviving theme or the live configuration is
    using; naming a thing is permission, not an override.
    """
    plan = Plan()
    refusals: list[str] = []
    live = resolve.live_settings()
    plan.applied = (live.get(("kdeglobals", "KDE")) or {}).get(
        "LookAndFeelPackage", "")

    # Both sides as paths, not names -- see holders_of(). A name comparison
    # let `--drop <display name>` past a refusal that `--drop <file stem>`
    # correctly triggered, for the same file.
    live_paths: set[str] = set()
    for kind, (file, group, key) in live_pointers():
        value = (live.get((file, group)) or {}).get(key, "").strip()
        if value:
            value = value.replace(resolve.AURORAE_PREFIX, "")
            live_paths.update(str(p) for p in locate(kind, value) if p.exists())
    live_paths.update(str(p) for p in live_wallpapers() if p.exists())

    for name in names:
        found: list[tuple[str, Path]] = []
        for kind, _ in POINTERS:
            for path in locate(kind, name):
                if path.exists() and not any(p == path for _, p in found):
                    found.append((kind, path))
        if not found:
            refusals.append(f"{name}: not found in any user theme directory")
            continue
        in_use = sorted({str(p) for _, p in found} & live_paths)
        if in_use:
            refusals.append(f"{name}: currently in use ({Path(in_use[0]).name})")
            continue
        holders = holders_of({str(p) for _, p in found})
        if holders:
            refusals.append(f"{name}: referenced by {', '.join(holders)}")
            continue
        for kind, path in found:
            plan.remove.append(Removal(kind, name, path, "named explicitly"))

    for removal in plan.remove:
        removal.size = _size(removal.path)
    return plan, refusals


def check(plan: Plan) -> list[str]:
    """Refuse anything that is not plainly this user's own theme content."""
    problems = []
    if os.geteuid() == 0:
        problems.append("refusing to run as root")
    allowed = (paths.data_home().resolve(), (paths.HOME / ".icons").resolve())
    for removal in plan.remove:
        try:
            real = removal.path.resolve()
        except OSError as exc:
            problems.append(f"{removal.path}: {exc}")
            continue
        if not any(str(real).startswith(str(base) + os.sep) for base in allowed):
            problems.append(f"{removal.path} is outside {', '.join(map(str, allowed))}")
        if removal.path.is_symlink():
            problems.append(f"{removal.path} is a symlink")
        try:
            if removal.path.lstat().st_uid != os.getuid():
                problems.append(f"{removal.path} is not owned by you")
        except OSError as exc:
            problems.append(f"{removal.path}: {exc}")
    if plan.applied and any(r.kind == "global-theme" and r.name == plan.applied
                            for r in plan.remove):
        problems.append(f"the applied theme {plan.applied} is in the removal set")
    return problems


def _quarantine_relative(path: Path) -> Path:
    """Where a removal is filed inside the quarantine directory.

    Relative to whichever allowed root contains it, not blindly to `$HOME`:
    with `XDG_DATA_HOME` set outside the home directory, `relative_to(HOME)`
    raised `ValueError`, which no handler caught -- a traceback after the
    snapshot and after the prompt, part way through moving files, and before
    `manifest.json` was written.

    Home-relative in the ordinary case, so the quarantine layout and the
    RESTORE.md wording stay as documented.
    """
    try:
        return path.relative_to(paths.HOME)
    except ValueError:
        pass
    try:
        return Path("xdg-data-home") / path.relative_to(paths.data_home())
    except ValueError:
        raise ValueError("outside every directory prune is allowed to touch") from None


def run(plan: Plan) -> tuple[Path, list[Removal], list[str]]:
    """Move the removals into quarantine. Returns (dir, moved, failures)."""
    # A second-resolution timestamp with exist_ok=True let two runs in the same
    # second share a directory, and the second one's manifest.json overwrote
    # the first's -- leaving the first batch's files on disk, absent from the
    # only document that says how to put them back, under a heading that tells
    # you to delete the directory. Use the snapshot id (timestamp + uuid) and
    # refuse to reuse a directory at all.
    quarantine = snapshot.store() / "pruned" / snapshot.new_id()
    quarantine.mkdir(parents=True, exist_ok=False)
    moved: list[Removal] = []
    failures: list[str] = []
    records = []

    for removal in plan.remove:
        try:
            relative = _quarantine_relative(removal.path)
        except ValueError as exc:
            failures.append(f"{removal.path}: {exc}")
            continue
        target = quarantine / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        record = {"kind": removal.kind, "name": removal.name,
                  "from": str(removal.path), "to": str(target),
                  "bytes": removal.size, "reason": removal.reason}
        try:
            shutil.move(str(removal.path), str(target))
        except (OSError, shutil.Error) as exc:
            # A cross-device move is copy-then-delete, so a failure can leave
            # the content *in quarantine* while reporting that nothing moved.
            # RESTORE.md would then omit it and tell the user to delete the
            # directory. Ask the filesystem rather than assuming.
            failures.append(f"{removal.path}: {exc}")
            if target.exists():
                record["partial"] = True
                record["note"] = f"move reported {exc}; found in quarantine anyway"
                records.append(record)
            continue
        moved.append(removal)
        records.append(record)

    (quarantine / "manifest.json").write_text(
        json.dumps({"moved": records, "failures": failures,
                    "applied": plan.applied, "kept": plan.kept_themes},
                   indent=2), encoding="utf-8")
    (quarantine / "RESTORE.md").write_text(_restore_note(quarantine, records),
                                           encoding="utf-8")
    journal.record("prune", quarantine=str(quarantine), removed=len(moved),
                   bytes=sum(r.size for r in moved), failures=len(failures))
    return quarantine, moved, failures


def _restore_note(quarantine: Path, records: list[dict]) -> str:
    """The undo instructions. They have to actually run.

    An earlier version printed a shell loop whose body was `:` -- it looked
    like a bulk restore and did nothing. In the one document whose entire job
    is "here is how to get your files back", a decorative snippet is worse
    than no snippet.
    """
    lines = [
        "# Undoing this prune",
        "",
        "Nothing was deleted. Every path below was **moved** here, keeping its",
        "location relative to your home directory.",
        "",
        "## Put everything back",
        "",
        "```sh",
        f"cd {quarantine} && python3 - <<'PY'",
        "import json, pathlib, shutil",
        "records = json.load(open('manifest.json'))['moved']",
        "for record in records:",
        "    source, destination = pathlib.Path(record['to']), pathlib.Path(record['from'])",
        "    if not source.exists():",
        "        print('missing, skipped:', source); continue",
        "    if destination.exists():",
        "        print('already there, skipped:', destination); continue",
        "    destination.parent.mkdir(parents=True, exist_ok=True)",
        "    shutil.move(str(source), str(destination))",
        "    print('restored', destination)",
        "PY",
        "```",
        "",
        "It skips anything already back in place, so running it twice is safe.",
        "",
        "## Or one at a time",
        "",
    ]
    for record in records[:200]:
        lines.append(f"    mv '{record['to']}' '{record['from']}'")
    if len(records) > 200:
        lines.append(f"    … and {len(records) - 200} more; see manifest.json")
    lines += ["", f"{len(records)} item(s). Delete this directory to make the",
              "removal permanent and reclaim the space."]
    return "\n".join(lines) + "\n"
