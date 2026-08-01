"""Finding KPackage components still shipping pre-Plasma-5.19 metadata.

KSvg and plasma-core both warn about these on every load:

    kf.svg: The theme "X" uses the legacy metadata.desktop. Consider
    contacting the author and asking them update it to use the newer
    JSON format.

They still work, but the live theme-reload path through them is evidently
not well exercised -- plasmashell has been observed aborting inside
KSvg::FrameSvg::mask() while reloading one.

Scope note: Aurorae window decorations use metadata.desktop as their NORMAL
format and are deliberately not checked here. Only KPackage types that
migrated to metadata.json are legacy when they lack it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import kconfig, paths

# KPackage directory -> human label. These migrated to metadata.json.
PACKAGE_KINDS = {
    "plasma/desktoptheme": "Plasma style",
    "plasma/look-and-feel": "Global theme",
    "plasma/plasmoids": "Widget",
}


@dataclass
class LegacyPackage:
    kind: str
    name: str
    path: Path
    active: bool = False
    referenced_by: tuple[str, ...] = ()

    @property
    def user_owned(self) -> bool:
        """Only things under the user's data dir are ours to remove."""
        try:
            return self.path.is_relative_to(paths.data_home())
        except ValueError:
            return False

    @property
    def removable(self) -> bool:
        """Safe to delete: ours, not in use, and nothing else depends on it."""
        return self.user_owned and not self.active and not self.referenced_by


def _active_names() -> set[str]:
    """Everything currently referenced by the live configuration."""
    live: set[str] = set()
    cascade = {}
    for config_file in ("kdeglobals", "plasmarc"):
        cascade.update(kconfig.read_cascade(config_file))
    for key, group in ((("plasmarc", "Theme"), "name"),
                       (("kdeglobals", "KDE"), "LookAndFeelPackage")):
        value = cascade.get(key, {}).get(group)
        if value:
            live.add(value)
    return live


def _references() -> dict[str, list[str]]:
    """Which installed global themes point at which component.

    Deleting a Plasma style that is merely not-currently-active would silently
    break every other installed theme that names it. Those themes are the whole
    reason the style is on disk.
    """
    from . import manifest

    out: dict[str, list[str]] = {}
    for name, _ in manifest.list_installed():
        try:
            theme = manifest.load(name)
        except (FileNotFoundError, OSError):
            continue
        for group, key in ((("plasmarc", "Theme"), "name"),
                           (("kdeglobals", "KDE"), "widgetStyle"),
                           (("kdeglobals", "General"), "ColorScheme")):
            value = theme.settings.get(group, {}).get(key)
            if value:
                out.setdefault(value, []).append(theme.name)
    return out


def scan(kinds: dict[str, str] | None = None) -> list[LegacyPackage]:
    """Every installed package that has metadata.desktop but no metadata.json."""
    kinds = kinds or PACKAGE_KINDS
    active = _active_names()
    refs = _references()
    found: list[LegacyPackage] = []
    seen: set[tuple[str, str]] = set()

    for base in paths.data_dirs():
        for subdir, label in kinds.items():
            directory = base / subdir
            if not directory.is_dir():
                continue
            for entry in sorted(directory.iterdir()):
                if not entry.is_dir() or (label, entry.name) in seen:
                    continue
                has_desktop = (entry / "metadata.desktop").is_file()
                has_json = (entry / "metadata.json").is_file()
                if has_desktop and not has_json:
                    seen.add((label, entry.name))
                    # No self-reference filtering: a global theme named 'Sweet'
                    # pointing at a Plasma style named 'Sweet' is a genuine
                    # dependency between two different packages, not a loop.
                    users = refs.get(entry.name, [])
                    found.append(LegacyPackage(
                        kind=label, name=entry.name, path=entry,
                        active=entry.name in active,
                        referenced_by=tuple(sorted(set(users))),
                    ))
    return found


def is_legacy(subdir: str, name: str) -> bool:
    """Is one specific package legacy? Used to warn before applying a theme."""
    for base in paths.data_dirs():
        entry = base / subdir / name
        if entry.is_dir():
            return ((entry / "metadata.desktop").is_file()
                    and not (entry / "metadata.json").is_file())
    return False
