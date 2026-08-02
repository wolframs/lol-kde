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
]


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
        name = (declared.get((file, group)) or {}).get(key, "").strip()
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
    path = style_dir() / name
    if not path.is_dir():
        return None
    return not (path / "metadata.json").is_file()


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
    protected: set[str] = set()
    for name in kept:
        for _, (_, found) in components(themes[name]).items():
            protected.update(str(p) for p in found)
    for kind, (file, group, key) in POINTERS:
        value = (live.get((file, group)) or {}).get(key, "").strip()
        if value:
            protected.update(str(p) for p in locate(kind, value) if p.exists())

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


def run(plan: Plan) -> tuple[Path, list[Removal], list[str]]:
    """Move the removals into quarantine. Returns (dir, moved, failures)."""
    quarantine = (snapshot.store() / "pruned" /
                  time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime()))
    quarantine.mkdir(parents=True, exist_ok=True)
    moved: list[Removal] = []
    failures: list[str] = []
    records = []

    for removal in plan.remove:
        relative = removal.path.relative_to(paths.HOME)
        target = quarantine / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(removal.path), str(target))
        except (OSError, shutil.Error) as exc:
            failures.append(f"{removal.path}: {exc}")
            continue
        moved.append(removal)
        records.append({"kind": removal.kind, "name": removal.name,
                        "from": str(removal.path), "to": str(target),
                        "bytes": removal.size, "reason": removal.reason})

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
