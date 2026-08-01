"""XDG search paths and KDE config file locations."""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")


def data_dirs() -> list[Path]:
    """Every directory KDE searches for themes, most-specific first."""
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [data_home()] + [Path(p) for p in raw.split(":") if p]


def icon_dirs() -> list[Path]:
    """Icon and cursor themes live in an extra legacy location."""
    dirs = [data_home() / "icons", HOME / ".icons"]
    dirs += [d / "icons" for d in data_dirs()[1:]]
    return dirs


def qt_plugin_dirs() -> list[Path]:
    """Where Qt6 style and decoration plugins are installed."""
    found: list[Path] = []
    roots = ["/usr/lib", "/usr/lib64", "/usr/local/lib"]
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for candidate in list(base.glob("*/qt6/plugins")) + [base / "qt6/plugins"]:
            if candidate.is_dir():
                found.append(candidate)
    return found


def knsrc_dirs() -> list[Path]:
    return [d / "knsrcfiles" for d in data_dirs()]


# Live KDE config files, keyed by the name used in look-and-feel manifests.
CONFIG_FILES = {
    "kdeglobals": config_home() / "kdeglobals",
    "plasmarc": config_home() / "plasmarc",
    "kwinrc": config_home() / "kwinrc",
    "kcminputrc": config_home() / "kcminputrc",
    "ksplashrc": config_home() / "ksplashrc",
}


def first_existing(candidates: list[Path]) -> Path | None:
    for path in candidates:
        if path.exists():
            return path
    return None
