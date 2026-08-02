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

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
UNPINNED = "unpinned"    # the user-layer entry is gone; the cascade resolves it
TOMBSTONED = "tombstoned"  # a [$d] marker now blocks inheritance
STALE = "stale"          # correct on disk; running clients were not told


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

    # The same refusals `unpin()` applies, minus "must already exist" -- a
    # first write legitimately creates the file. Without this, a `kdeglobals`
    # symlinked into a dotfiles repo was written straight through by `write()`
    # while `unpin()` refused it, so a mixed restore half-committed to git and
    # half declined. The guard belongs to the operation, not to one route.
    if user_layer.exists() or user_layer.is_symlink():
        refusal = _safe_to_edit(user_layer)
        if refusal:
            return WriteResult(file, group, key, value or "", FAILED,
                               detail=refusal)
    elif os.geteuid() == 0:
        return WriteResult(file, group, key, value or "", FAILED,
                           detail="refusing to edit config as root")

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
        # --delete does not delete. It writes `key[$d]`, which shadows every
        # lower layer, so the honest report is "tombstoned", not "unchanged".
        if kconfig.tombstoned(user_layer, group, key):
            return WriteResult(file, group, key, "", TOMBSTONED, after or "",
                               layer_name,
                               f"wrote a [$d] tombstone in {user_layer}; the "
                               "key now resolves to nothing")
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


def inherited_value(file: str, group: str, key: str) -> str | None:
    """What the key would resolve to if the user layer said nothing at all.

    Everything below ~/.config: kdedefaults, then XDG_CONFIG_DIRS. This is the
    value "absent, inherited" is supposed to expose.
    """
    layers = paths.config_layers()[:-1]          # drop ~/.config itself
    found = None
    for directory in layers:
        state, option = kconfig._entry(kconfig.read_ini(directory / file),
                                       group, key)
        if state == "deleted":
            found = None
        elif state == "set":
            found = kconfig.get(directory / file, group, key)
    return found


def _safe_to_edit(path: Path) -> str | None:
    """Refuse anything that is not plainly this user's own config file.

    Checked immediately before the write rather than once up front, because
    the gap between the two is a TOCTOU window. Symlinks are refused outright:
    people symlink kdeglobals into a dotfiles repo, and following it writes
    into git.
    """
    if os.geteuid() == 0:
        return "refusing to edit config as root"
    if path.is_symlink():
        return f"{path} is a symlink; refusing to write through it"
    if not path.is_file():
        return f"{path} is not a regular file"
    try:
        info = path.lstat()
    except OSError as exc:
        return str(exc)
    if info.st_uid != os.getuid():
        return f"{path} is not owned by uid {os.getuid()}"
    root = paths.config_home().resolve()
    if not str(path.resolve()).startswith(str(root) + os.sep):
        return f"{path} is outside {root}"
    return None


def _strip_key(path: Path, group: str, key: str) -> list[str]:
    """Remove a key and any `[$…]`-decorated form of it from ONE group.

    This is the only raw file edit in the program, and it exists because no
    KDE command-line tool can express "make this key absent again".
    `kwriteconfig6 --delete` writes a `[$d]` tombstone, which *blocks* the
    inherited value rather than revealing it -- measured 2026-08-02, open
    question C. Removing the line is the only way back.

    Returns the lines removed, so the caller can journal them.

    Works in **bytes**, not text. It used to read with `errors="replace"` and
    write UTF-8 back, which silently rewrote every byte in the file that was
    not valid UTF-8 -- and KDE stores paths as raw bytes, so a wallpaper under
    a filename that is not valid UTF-8 (legal on Linux) came back as U+FFFD.
    Reproduced by review on 2026-08-02: `/home/u/pic/\xff\xfe.png` became
    `/home/u/pic/\xef\xbf\xbd\xef\xbf\xbd.png`. `_verify` only re-reads the one
    key that was unpinned, so nothing noticed. Reading bytes also stops
    `read_text`'s universal-newline translation from converting a CRLF file.

    Only the key *name* is decoded, and only to compare it.
    """
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    header = f"[{group}]".encode()
    out: list[bytes] = []
    removed: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(b"[") and stripped.endswith(b"]"):
            inside = stripped == header
            out.append(line)
            continue
        if inside and stripped:
            candidate = stripped.split(b"=", 1)[0].strip()
            name, _ = kconfig.split_flags(
                candidate.decode("utf-8", errors="replace"))
            if name == key:
                removed.append(line.decode("utf-8", errors="replace"))
                continue
        out.append(line)

    if removed:
        # Atomic, mode-preserving, and in the same directory so os.replace
        # cannot cross a filesystem boundary.
        mode = path.stat().st_mode
        handle, temporary = tempfile.mkstemp(dir=str(path.parent),
                                             prefix=path.name + ".lolkde")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(b"".join(out))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        except OSError:
            Path(temporary).unlink(missing_ok=True)
            raise
    return removed


def unpin(file: str, group: str, key: str, notify: bool = True) -> WriteResult:
    """Restore "absent from ~/.config, inherited from below" for one key.

    The obvious route -- `kwriteconfig6 --delete` -- is wrong: it writes a
    `[$d]` tombstone that blocks the cascade, leaving the key resolving to
    nothing rather than to the inherited value. See open question C.

    The route that works, using only supported writers:

    1. Write the *inherited* value into the user layer with `--notify`. This
       is a real KConfig write, so running clients are told about it through
       KConfig's own correctly-typed signal. After it, everyone agrees the
       key resolves to V.
    2. Delete the user-layer line outright. The key now resolves to V again
       from the layer below -- the same V. No value changed, so nothing needs
       to be notified, and no notification has to be synthesised.

    Step 1 is what makes step 2 safe. Never replace it by hand-emitting
    ConfigChanged: doing that with a wrong signature destroyed a session on
    2026-08-02 (docs/incident-2026-08-02-kconfig-oom.md), and there is no
    signature careful enough to make it allowed.

    `notify=False` keeps step 1 off the session bus. It is for tests running
    against a temporary config tree, where there is no live desktop to tell
    and no reason to broadcast anything at all.
    """
    path = paths.config_home() / file
    inherited = inherited_value(file, group, key)
    pinned = kconfig.get(path, group, key)
    tomb = path.is_file() and kconfig.tombstoned(path, group, key)

    if not path.is_file() or (pinned is None and not tomb):
        return WriteResult(file, group, key, inherited or "", UNCHANGED,
                           inherited or "", "",
                           "already absent from the user layer")

    refusal = _safe_to_edit(path)
    if refusal:
        return WriteResult(file, group, key, inherited or "", FAILED,
                           detail=refusal)

    # Step 1: agree on the value through the supported writer, so that the
    # raw edit that follows changes no resolved value at all.
    if inherited is not None and pinned != inherited:
        write(file, group, key, inherited, notify=notify)

    try:
        removed = _strip_key(path, group, key)
    except OSError as exc:
        # Step 1 may already have pinned the inherited value. Reporting only
        # the exception reads as "nothing was written", which is wrong -- the
        # user layer can now hold a pin they did not have before, and neither
        # `_verify` nor `changelog_row` looks at a FAILED step. Say what is
        # actually in the file.
        now = kconfig.get(path, group, key)
        detail = str(exc)
        if now != pinned:
            was = f"{pinned!r}" if pinned is not None else "no entry"
            detail += (f"; the user layer now holds {key}={now!r} where it had "
                       f"{was} -- step 1 landed and step 2 did not. Re-run to "
                       f"finish, or remove that line by hand")
        return WriteResult(file, group, key, inherited or "", FAILED,
                           now or "", str(path), detail)

    after = kconfig.read_cascade(file).get((file, group), {}).get(key)
    layer = kconfig.origin(file, group, key)
    detail = f"removed {len(removed)} line(s) from {path}"

    if inherited is None:
        # Nothing underneath. The file is right, but no supported writer can
        # announce "this key is gone" -- so say so rather than implying live
        # effect. The alternative is the forbidden one.
        return WriteResult(file, group, key, "", STALE, after or "",
                           str(layer or ""),
                           detail + "; nothing inherits it, so running "
                                    "applications keep the old value until "
                                    "they restart")
    if after != inherited:
        return WriteResult(file, group, key, inherited, FAILED, after or "",
                           str(layer or ""),
                           detail + "; the inherited value did not reappear")
    if layer == path:
        return WriteResult(file, group, key, inherited, FAILED, after,
                           str(layer), detail + "; still pinned in the user layer")
    return WriteResult(file, group, key, inherited, UNPINNED, after,
                       str(layer or ""), detail)


def delete(file: str, group: str, key: str) -> WriteResult:
    """`kwriteconfig6 --delete`, reported honestly.

    Almost never what you want. It does not remove the key, it shadows it:
    the resulting `[$d]` tombstone makes the key resolve to nothing even when
    a lower layer defines it. Use unpin() to expose the inherited value.
    """
    result = write(file, group, key, None, notify=True)
    if result.outcome == TOMBSTONED:
        inherited = inherited_value(file, group, key)
        if inherited is not None:
            result.detail += (f", shadowing the inherited value "
                              f"{inherited!r} -- use unpin() to expose it")
    return result


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
