"""Repairs that write to the live configuration.

Everything else in this package reads. This module is the only one that
changes your machine, and each function here says exactly what it changed.
"""

from __future__ import annotations

import shutil
import subprocess

from . import paths, resolve

# KWin reads its decoration settings from a group still named after
# KDecoration *2*, while loading KDecoration *3* plugins. Both are true at
# once; do not "correct" this.
DECO_GROUP = "org.kde.kdecoration2"


def _kwriteconfig(group: str, key: str, value: str) -> bool:
    tool = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")
    if not tool:
        return False
    done = subprocess.run(
        [tool, "--file", "kwinrc", "--group", group, "--key", key, value],
        capture_output=True, text=True)
    return done.returncode == 0


def reconfigure_kwin() -> bool:
    """Ask the running KWin to re-read its config. No restart, no flicker."""
    tool = shutil.which("qdbus6") or shutil.which("qdbus")
    if not tool:
        return False
    done = subprocess.run(
        [tool, "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"],
        capture_output=True, text=True)
    return done.returncode == 0


def aurorae_plugin(live_library: str, live_theme: str) -> str | None:
    """Point an Aurorae SVG theme at the plugin that actually provides it.

    Returns a description of what changed, or None if nothing needed changing.
    See resolve.aurorae_provider() for why this is necessary.
    """
    if not live_theme.startswith(resolve.AURORAE_PREFIX):
        return None
    provider = resolve.aurorae_provider()
    if live_library == provider:
        return None
    if not _kwriteconfig(DECO_GROUP, "library", provider):
        return None
    # The theme lives in the same group and is inherited from the kdedefaults
    # layer. Write it into the user layer alongside the library, so the two
    # cannot drift apart later.
    _kwriteconfig(DECO_GROUP, "theme", live_theme)
    reconfigure_kwin()
    return (f"window decoration: {live_library or '(unset)'} -> {provider} "
            f"in {paths.config_home() / 'kwinrc'}")


def live_decoration() -> tuple[str, str]:
    """(library, theme) as KDE resolves them across the whole config cascade."""
    return resolve._deco_pointer(resolve.live_settings())
