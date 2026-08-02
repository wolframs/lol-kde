"""The banner, and the closing remarks.

Register note for future edits: the joke is politeness, not volume. These lines
work because they are factually accurate and delivered in the tone of a
compliance letter. Nothing here should ever shout, swear, or use an exclamation
mark. The situation is absurd without assistance.

The box is generated rather than hardcoded so the wording can be edited without
anyone having to count dashes.
"""

from __future__ import annotations

# "lol-kde", toilet -f future
WORDMARK = [
    "╻  ┏━┓╻     ╻┏ ╺┳┓┏━╸",
    "┃  ┃ ┃┃  ╺━╸┣┻┓ ┃┃┣╸",
    "┗━╸┗━┛┗━╸   ╹ ╹╺┻┛┗━╸",
]

PLAIN = "lol-kde"

NUMERALS = {5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def _count() -> str:
    """How many things a Global Theme *names* -- not how many we resolve.

    The notice is a claim about the theme, so it counts what a
    `contents/defaults` can declare. `pointer_kinds()` is one lower: the
    wallpaper is `prune`'s to determine, not `doctor`'s.
    """
    from . import resolve
    n = resolve.declarable_kinds()
    return NUMERALS.get(n, str(n))


def notice() -> list[str]:
    return [
        "NOTICE",
        "",
        f"A Global Theme is a list of up to {_count()} things you may",
        "or may not own.  This program determines which.  We",
        "regret the necessity.",
    ]


def subtitle() -> str:
    return (f"A Global Theme is a list of up to {_count()} things "
            "you may or may not own.")

# Shown under `why`. The whole architecture, at the altitude people actually
# need it, which is not the altitude the documentation offers it at.
WHY = """\
There is no single "look". There are layers, drawn by different programs:

  Window decoration   KWin           the titlebar and borders, and nothing else
  Widget style        Qt, in-process  everything inside an application window
  Colour scheme       a palette      honoured only if the widget style feels like it
  Plasma style        plasmashell    panel, popups, tray. never applications.
  Icons, cursors, fonts, splash      independent of all of the above

A Global Theme does not contain any of these. It contains their names.

If a name does not resolve, KDE substitutes a default and says nothing, because
reporting it would require somewhere to report it, and that would be a menu
point, and we are trying to have fewer of those."""

PAD = 2


def _box(blocks: list[list[str]]) -> str:
    """Frame groups of lines, blank-separated, in a single rounded box."""
    lines: list[str] = []
    for i, block in enumerate(blocks):
        if i:
            lines.append("")
        lines.extend(block)
    inner = max(len(line) for line in lines) + PAD * 2
    out = ["┌" + "─" * inner + "┐"]
    out += ["│" + " " * PAD + line.ljust(inner - PAD) + "│" for line in lines]
    out.append("└" + "─" * inner + "┘")
    return "\n".join(out)


def width() -> int:
    """Columns the full banner needs."""
    return len(_box([WORDMARK, notice()]).splitlines()[0])


def render(width_available: int = 80, color: bool = True) -> str:
    """The notice, or a modest substitute when the terminal is too narrow."""
    if width_available < width():
        head = f"{PLAIN} -- {subtitle()}"
        return f"\033[36m{head}\033[0m" if color else head
    body = _box([WORDMARK, notice()])
    if not color:
        return body
    return "\n".join(f"\033[36m{line}\033[0m" for line in body.splitlines())


def closing_remark(ok: int, degraded: int, unset: int, missing: int) -> str:
    """One deadpan line summarising the audit. Deterministic, never random."""
    if missing:
        return (f"{missing} component{'s' if missing != 1 else ''} named in your "
                f"configuration {'are' if missing != 1 else 'is'} not installed. "
                f"KDE did not think to mention this.")
    if degraded:
        return ("One component is installed, resolvable, and inert. "
                "All three at once, which is the interesting part.")
    if unset:
        return (f"{unset} component{'s were' if unset != 1 else ' was'} declared by "
                f"the theme and then left to chance.")
    if ok:
        return "Nothing to report. Enjoy the brief sensation of things working."
    return "Nothing is configured, which is at least internally consistent."
