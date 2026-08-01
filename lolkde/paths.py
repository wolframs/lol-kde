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


def config_layers() -> list[Path]:
    """Every directory KDE reads a config file from, LOWEST priority first.

    KDE does not store a global theme's settings in ~/.config/kdeglobals. It
    writes them to a ~/.config/kdedefaults/ layer so that explicit user choices
    in ~/.config still override the theme, and "reset to theme defaults" stays
    possible. Reading only ~/.config therefore reports a correctly applied
    theme as entirely unset.

    XDG_CONFIG_DIRS normally already lists kdedefaults first; we add it
    explicitly as a fallback for sessions where it does not.
    """
    raw = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
    system = [Path(p) for p in raw.split(":") if p]
    defaults = config_home() / "kdedefaults"
    if defaults not in system:
        system.insert(0, defaults)
    # XDG_CONFIG_DIRS is highest-priority-first; we want lowest-first.
    return list(reversed(system)) + [config_home()]


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
