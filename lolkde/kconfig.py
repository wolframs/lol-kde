"""Reading KDE's INI-ish config files.

KDE config files are *nearly* INI but violate it in ways configparser hates:
duplicate keys, section names containing brackets, values containing '='.
Everything here is tuned to read them without throwing.
"""

from __future__ import annotations

import configparser
from pathlib import Path


def read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(
        strict=False,           # KDE writes duplicate keys
        interpolation=None,     # '%' appears literally in values
        delimiters=("=",),
        comment_prefixes=("#",),
        # A `[$d]` tombstone is written bare, with no `=` and no value.
        # Without this, that one line is a parse error and configparser
        # abandons the rest of the file -- so a single deleted key made
        # everything below it in the file invisible.
        allow_no_value=True,
    )
    parser.optionxform = str    # keys are case-sensitive in KDE
    if path.is_file():
        try:
            parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
        except configparser.Error:
            pass
    return parser


# KConfig hangs option flags off the key name: `Theme[$d]` is a *deleted*
# marker for `Theme`, `[$i]` is immutable, `[$e]` means expand. Locale variants
# look similar (`Name[de_DE]`) but carry no `$`, so the `$` is the discriminator.
#
# `[$d]` matters far more than it looks. It is not "the key is absent" -- it is
# a tombstone that *blocks inheritance*, so the cascade below it stops
# resolving. Read naively, `Theme[$d]` parses as a key literally named
# "Theme[$d]", the real `Theme` looks untouched in the layer underneath, and
# the tool reports a value that KDE itself no longer resolves. Measured on
# 2026-08-02: see docs/open-questions.md, question C.
DELETED_FLAG = "d"


def split_flags(key: str) -> tuple[str, str]:
    """`Theme[$d]` -> `("Theme", "d")`. `Name[de_DE]` -> `("Name[de_DE]", "")`."""
    if key.endswith("]") and "[$" in key:
        name, _, flags = key.rpartition("[$")
        return name, flags[:-1]
    return key, ""


def read_cascade(filename: str) -> dict[tuple[str, str], dict[str, str]]:
    """Merge one config file across every layer KDE reads it from.

    Later layers win, matching KDE's own resolution -- including a `[$d]`
    tombstone in a higher layer, which removes the key rather than adding one.
    Returns {(filename, group): {key: value}} so callers can treat it like a
    manifest.
    """
    from . import paths

    merged: dict[tuple[str, str], dict[str, str]] = {}
    for directory in paths.config_layers():
        parser = read_ini(directory / filename)
        for group in parser.sections():
            bucket = merged.setdefault((filename, group), {})
            for raw, value in parser.items(group):
                name, flags = split_flags(raw)
                if DELETED_FLAG in flags:
                    bucket.pop(name, None)
                else:
                    bucket[name] = (value or "").strip()
    return merged


def origin(filename: str, group: str, key: str) -> Path | None:
    """Which layer a resolved value actually came from. For -v output.

    None if nothing resolves -- including when the winning layer tombstones
    the key, which is a different thing from the key never having existed.
    """
    from . import paths

    found = None
    for directory in paths.config_layers():
        path = directory / filename
        parser = read_ini(path)
        state = entry_state(parser, group, key)
        if state == "deleted":
            found = None
        elif state == "set":
            found = path
    return found


def entry_state(parser: configparser.ConfigParser, group: str,
                key: str) -> str:
    """`set`, `deleted` (a `[$d]` tombstone), or `absent`, within one layer."""
    if not parser.has_section(group):
        return "absent"
    for raw in parser.options(group):
        name, flags = split_flags(raw)
        if name != key:
            continue
        if DELETED_FLAG in flags:
            return "deleted"
        return "set"
    return "absent"


def tombstoned(path: Path, group: str, key: str) -> bool:
    """Does this one file carry a `[$d]` marker for the key?

    A tombstone is the residue of `kwriteconfig6 --delete`, which never
    removes a line -- it writes one. Restoring "absent, inherited" means
    getting rid of this, and no KDE command-line tool can.
    """
    return entry_state(read_ini(path), group, key) == "deleted"


def get(path: Path, group: str, key: str) -> str | None:
    parser = read_ini(path)
    if entry_state(parser, group, key) != "set":
        return None
    return (parser.get(group, key) or "").strip()


def parse_lookandfeel_defaults(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Parse a look-and-feel `contents/defaults` file.

    Its section headers look like `[kdeglobals][KDE]` -- a config file name
    followed by a group. configparser reads that as the literal section name
    `kdeglobals][KDE`, which we split back apart.

    Returns {(config_file, group): {key: value}}.
    """
    parser = read_ini(path)
    out: dict[tuple[str, str], dict[str, str]] = {}
    for section in parser.sections():
        parts = section.split("][")
        if len(parts) == 2:
            config_file, group = parts[0].strip("[]"), parts[1].strip("[]")
        else:
            config_file, group = section.strip("[]"), ""
        out[(config_file, group)] = {k: v.strip() for k, v in parser.items(section)}
    return out
