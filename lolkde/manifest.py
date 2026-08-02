"""Reading a global theme package: its pointers and its declared dependencies."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import kconfig, paths

# kns://<knsrc>/<provider host>/<content id>
KNS_URI = re.compile(r"^kns://(?P<knsrc>[^/]+?)(?:\.knsrc)?/(?P<host>[^/]+)/(?P<id>\d+)")


@dataclass
class Dependency:
    """One entry from X-KPackage-Dependencies."""
    knsrc: str
    host: str
    content_id: str
    uri: str

    @property
    def store_url(self) -> str:
        return f"https://store.kde.org/p/{self.content_id}"


@dataclass
class GlobalTheme:
    name: str
    path: Path
    settings: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    dependencies: list[Dependency] = field(default_factory=list)
    display_name: str = ""

    @property
    def pointer_count(self) -> int:
        return sum(len(v) for v in self.settings.values())


def find(name: str) -> Path | None:
    """Locate an installed global theme package.

    Matches case-insensitively, and ignores punctuation differences, because
    nobody types 'Sweet-Ambar-Blue' with the capitals in the right places and
    there is no reason to make them.
    """
    def norm(value: str) -> str:
        return value.lower().replace("-", "").replace("_", "").replace(" ", "")

    exact = [base / "plasma/look-and-feel" / name for base in paths.data_dirs()]
    for candidate in exact:
        if candidate.is_dir():
            return candidate

    wanted = norm(name)
    for directory, path in list_installed():
        if norm(directory) == wanted:
            return path
    return None


def list_installed() -> list[tuple[str, Path]]:
    seen: dict[str, Path] = {}
    for base in paths.data_dirs():
        directory = base / "plasma/look-and-feel"
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_dir() and entry.name not in seen:
                seen[entry.name] = entry
    return sorted(seen.items())


def parse_dependencies(metadata: dict) -> list[Dependency]:
    raw = metadata.get("X-KPackage-Dependencies") or []
    deps: list[Dependency] = []
    for uri in raw:
        match = KNS_URI.match(uri.strip())
        if match:
            deps.append(
                Dependency(match["knsrc"], match["host"], match["id"], uri.strip())
            )
    return deps


def find_metadata(root: Path) -> Path | None:
    """Locate `metadata.json` inside a freshly extracted package.

    An upload is either the package directory itself or a wrapper holding one,
    and both shapes occur. Nothing deeper is searched: a `metadata.json` three
    levels down belongs to something else -- a bundled sub-package, a leftover
    example -- and reading it would attribute the wrong dependency list to the
    theme.
    """
    direct = root / "metadata.json"
    if direct.is_file():
        return direct
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        candidate = child / "metadata.json"
        if candidate.is_file():
            return candidate
    return None


def dependencies_in_tree(root: Path) -> list[Dependency]:
    """`X-KPackage-Dependencies` of an extracted package, without installing it.

    This is what makes `please --dry-run` a forecast rather than a floor. The
    description and the manifest are different lists by different authors, and
    the manifest is normally the longer one -- so a plan built from the
    description alone under-reports, which is the worst direction for a preview
    to be wrong in.
    """
    metadata_json = find_metadata(root)
    if metadata_json is None:
        return []
    try:
        data = json.loads(metadata_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    return parse_dependencies(data)


def load(name: str) -> GlobalTheme:
    path = find(name)
    if path is None:
        raise FileNotFoundError(f"global theme {name!r} is not installed")

    # Use the on-disk directory name, not what the user typed: our own lookup
    # is case-insensitive but plasma-apply-lookandfeel is not.
    theme = GlobalTheme(name=path.name, path=path, display_name=path.name)

    defaults = path / "contents/defaults"
    if defaults.is_file():
        theme.settings = kconfig.parse_lookandfeel_defaults(defaults)

    metadata_json = path / "metadata.json"
    if metadata_json.is_file():
        try:
            data = json.loads(metadata_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        theme.dependencies = parse_dependencies(data)
        theme.display_name = data.get("KPlugin", {}).get("Name") or name

    return theme
