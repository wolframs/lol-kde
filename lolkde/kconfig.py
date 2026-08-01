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
    )
    parser.optionxform = str    # keys are case-sensitive in KDE
    if path.is_file():
        try:
            parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
        except configparser.Error:
            pass
    return parser


def read_cascade(filename: str) -> dict[tuple[str, str], dict[str, str]]:
    """Merge one config file across every layer KDE reads it from.

    Later layers win, matching KDE's own resolution. Returns
    {(filename, group): {key: value}} so callers can treat it like a manifest.
    """
    from . import paths

    merged: dict[tuple[str, str], dict[str, str]] = {}
    for directory in paths.config_layers():
        parser = read_ini(directory / filename)
        for group in parser.sections():
            bucket = merged.setdefault((filename, group), {})
            for key, value in parser.items(group):
                bucket[key] = value.strip()
    return merged


def origin(filename: str, group: str, key: str) -> Path | None:
    """Which layer a resolved value actually came from. For -v output."""
    from . import paths

    found = None
    for directory in paths.config_layers():
        path = directory / filename
        parser = read_ini(path)
        if parser.has_option(group, key):
            found = path
    return found


def get(path: Path, group: str, key: str) -> str | None:
    parser = read_ini(path)
    if parser.has_option(group, key):
        return parser.get(group, key).strip()
    return None


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
