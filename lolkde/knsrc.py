"""Reading .knsrc files -- KDE's own spec for where downloaded content goes.

Every KNewStuff category ships a .knsrc declaring its install directory and
how its archives should be unpacked. Rather than hardcoding install paths,
we read the same files KDE reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import kconfig, paths

# knsrc name -> KPackage structure, for entries installed via kpackagetool6.
# kpackagetool6 can usually infer this from the archive's own metadata.json;
# this is the fallback when it cannot.
KPACKAGE_TYPES = {
    "plasma-themes": "Plasma/Theme",
    "lookandfeel": "Plasma/LookAndFeel",
    "ksplash": "Plasma/LookAndFeel",
    "plasmoids": "Plasma/Applet",
    "kwinscripts": "KWin/Script",
    "kwineffect": "KWin/Effect",
    "kwinswitcher": "KWin/WindowSwitcher",
    "wallpaperplugin": "Plasma/Wallpaper",
    "comic": "Plasma/Comic",
}

# Categories with no .knsrc install target on this system, or none at all.
FALLBACK_TARGETS = {
    "sddmtheme": ("/usr/share/sddm/themes", True),   # (path, needs root)
}


@dataclass
class KnsrcSpec:
    name: str
    target: Path | None = None       # absolute install directory
    uncompress: str = "archive"      # archive | subdir-archive | kpackage | never | always
    adoption_command: str = ""
    needs_root: bool = False
    kpackage_type: str = ""
    found: bool = True

    @property
    def uses_kpackage(self) -> bool:
        return self.uncompress == "kpackage"


def _locate(name: str) -> Path | None:
    for directory in paths.knsrc_dirs():
        candidate = directory / f"{name}.knsrc"
        if candidate.is_file():
            return candidate
    return None


def load(name: str) -> KnsrcSpec:
    """Resolve a knsrc category name into a concrete install spec."""
    spec = KnsrcSpec(name=name, kpackage_type=KPACKAGE_TYPES.get(name, ""))
    path = _locate(name)

    if path is None:
        return _apply_fallback(spec)

    parser = kconfig.read_ini(path)
    section = "KNewStuff3" if parser.has_section("KNewStuff3") else "KNewStuff"

    def value(key: str) -> str:
        return parser.get(section, key).strip() if parser.has_option(section, key) else ""

    # A knsrc may alias another category (window-decorations <- aurorae).
    alias = value("AliasFor")
    if alias and alias != name:
        aliased = load(alias)
        if aliased.found and aliased.target:
            spec.target = aliased.target

    spec.uncompress = (value("Uncompress") or "archive").lower()
    spec.adoption_command = value("AdoptionCommand")

    # InstallPath is relative to $HOME; TargetDir/XDGTargetDir to XDG_DATA_HOME.
    install_path = value("InstallPath")
    target_dir = value("TargetDir") or value("XDGTargetDir")
    if install_path:
        spec.target = paths.HOME / install_path
    elif target_dir:
        spec.target = paths.data_home() / target_dir
    elif spec.uses_kpackage:
        spec.target = None       # kpackagetool6 decides
    elif spec.target is None:
        # A .knsrc can exist yet declare no target at all (sddmtheme does).
        spec = _apply_fallback(spec)

    if spec.uncompress in ("true", "1", "yes"):
        spec.uncompress = "always"
    return spec


def _apply_fallback(spec: KnsrcSpec) -> KnsrcSpec:
    fallback = FALLBACK_TARGETS.get(spec.name)
    if fallback:
        spec.target, spec.needs_root = Path(fallback[0]), fallback[1]
        spec.found = True
    else:
        spec.found = False
    return spec
