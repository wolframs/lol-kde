"""Repairs that write to the live configuration.

Everything else in this package reads. This module is the only one that
changes your machine, and each function here says exactly what it changed.

Nothing here trusts an exit code. `kwriteconfig6` exits 0 and writes nothing
when the value already matches an inherited default -- this repo's own
`aurorae_plugin()` was caught doing exactly that, reporting a successful write
of a key that never appeared in the file. Every write is followed by a
two-level read-back: did the value resolve, and did it land in the layer we
aimed at.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from . import kconfig, paths, resolve

# KWin reads its decoration settings from a group still named after
# KDecoration *2*, while loading KDecoration *3* plugins. Both are true at
# once; do not "correct" this.
DECO_GROUP = "org.kde.kdecoration2"

# Write outcomes.
WROTE = "wrote"          # the value landed in the layer we aimed at
INHERITED = "inherited"  # resolves correctly, but KConfig stored nothing
UNCHANGED = "unchanged"  # already correct, in the right layer
FAILED = "failed"        # the value does not resolve


@dataclass
class WriteResult:
    file: str
    group: str
    key: str
    wanted: str
    outcome: str
    resolved: str = ""
    layer: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome != FAILED

    @property
    def pinned(self) -> bool:
        """Did the value end up stored in the user layer, or only inherited?

        A pin survives the next global-theme apply. An inherited value does
        not necessarily. The distinction is invisible from the exit code.
        """
        return self.outcome in (WROTE, UNCHANGED)


def _tool() -> str | None:
    return shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")


def write(file: str, group: str, key: str, value: str | None,
          notify: bool = True) -> WriteResult:
    """Set (or, with value=None, delete) one key, then verify what happened."""
    before_layer = kconfig.origin(file, group, key)
    before = kconfig.read_cascade(file).get((file, group), {}).get(key)
    user_layer = paths.config_home() / file
    already_pinned = before_layer == user_layer and before == value

    tool = _tool()
    if tool is None:
        return WriteResult(file, group, key, value or "", FAILED,
                           detail="kwriteconfig6 not found")

    command = [tool, "--file", file, "--group", group, "--key", key]
    if notify:
        command.insert(1, "--notify")
    command += ["--delete"] if value is None else [value]
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return WriteResult(file, group, key, value or "", FAILED, detail=str(exc))

    after = kconfig.read_cascade(file).get((file, group), {}).get(key)
    after_layer = kconfig.origin(file, group, key)
    layer_name = str(after_layer) if after_layer else ""

    if value is None:
        outcome = UNCHANGED if after != before or after is None else FAILED
        return WriteResult(file, group, key, "", outcome, after or "", layer_name)
    if after != value:
        return WriteResult(file, group, key, value, FAILED, after or "", layer_name,
                           "the value does not resolve after writing")
    if after_layer == user_layer:
        return WriteResult(file, group, key, value,
                           UNCHANGED if already_pinned else WROTE, after, layer_name)
    return WriteResult(
        file, group, key, value, INHERITED, after, layer_name,
        "resolves correctly, but KConfig stored nothing -- the value already "
        "matched an inherited default, so it is not pinned in your layer")


def reconfigure_kwin() -> bool:
    """Ask the running KWin to re-read its config. No restart, no flicker."""
    tool = shutil.which("qdbus6") or shutil.which("qdbus")
    if not tool:
        return False
    try:
        done = subprocess.run(
            [tool, "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
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

    result = write("kwinrc", DECO_GROUP, "library", provider)
    if not result.ok:
        return None
    # The theme lives in the same group and is inherited from kdedefaults.
    # Pinning it alongside the library keeps the pair from drifting -- but the
    # write is a no-op when the inherited value already matches, which is the
    # common case and not a failure.
    theme_result = write("kwinrc", DECO_GROUP, "theme", live_theme)

    reconfigure_kwin()
    note = (f"window decoration: {live_library or '(unset)'} -> {provider} "
            f"in {paths.config_home() / 'kwinrc'}")
    if theme_result.outcome == INHERITED:
        note += " (theme left inherited from kdedefaults; nothing to pin)"
    return note


def live_decoration() -> tuple[str, str]:
    """(library, theme) as KDE resolves them across the whole config cascade."""
    return resolve._deco_pointer(resolve.live_settings())
