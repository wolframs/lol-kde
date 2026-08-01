"""Command line interface.

Output is deliberately terse: one line per component, explanations only for
things that are broken. Nothing here prints a wall of text at you.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from . import install as installer
from . import manifest, resolve
from .resolve import DEGRADED, MISSING, OK

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


# status -> (label, colour). Labels are padded before colouring, because ANSI
# escapes count toward str width and would wreck column alignment otherwise.
MARKS = {
    OK: ("ok", "32"),
    DEGRADED: ("warn", "33"),
    MISSING: ("MISS", "31"),
    "unset": ("unset", "33"),
    "drift": ("drift", "36"),
}
_MARK_WIDTH = max(len(label) for label, _ in MARKS.values())


def _mark(status: str) -> str:
    label, colour = MARKS.get(status, (status, "0"))
    return _paint(label.ljust(_MARK_WIDTH), colour)


def _print_resolutions(results: list[resolve.Resolution], verbose: bool) -> tuple[int, int]:
    broken = degraded = 0
    for result in results:
        if result.status == MISSING:
            broken += 1
        elif result.status == DEGRADED:
            degraded += 1
        line = f"  {_mark(result.status)}  {result.label:<18} {result.value}"
        print(line)
        if result.detail and (result.status != OK or verbose):
            print(f"{' ' * (4 + _MARK_WIDTH)}{_paint(result.detail, '2')}")
        if verbose and result.path:
            print(f"          {_paint(str(result.path), '2')}")
    return broken, degraded


def _summary(broken: int, degraded: int, total: int) -> str:
    good = total - broken - degraded
    parts = [f"{good}/{total} ok"]
    if degraded:
        parts.append(f"{degraded} degraded")
    if broken:
        parts.append(f"{broken} missing")
    return ", ".join(parts)


def _print_audit(rows: list[resolve.AuditRow], verbose: bool) -> dict[str, int]:
    counts = {MISSING: 0, DEGRADED: 0, resolve.UNSET: 0, OK: 0}
    indent = " " * (2 + _MARK_WIDTH + 2)
    for row in rows:
        status = row.status
        counts[status] = counts.get(status, 0) + 1

        # A row can resolve fine and still have drifted away from the theme.
        drifted = bool(row.note) and status == OK
        mark = _mark("unset" if status == resolve.UNSET
                     else "drift" if drifted else status)
        value = _paint("(not set)", "2") if status == resolve.UNSET else (row.live or "")

        print(f"  {mark}  {row.label:<18} {value}")
        detail = row.note or (row.resolution.detail if row.resolution else "")
        if detail and (status != OK or drifted or verbose):
            print(f"{indent}{_paint(detail, '2')}")
        if verbose and row.resolution and row.resolution.path:
            print(f"{indent}{_paint(str(row.resolution.path), '2')}")
    return counts


def cmd_doctor(args: argparse.Namespace) -> int:
    """Audit the configuration that is applied right now."""
    live = resolve.live_settings()
    active = live.get(("kdeglobals", "KDE"), {}).get("LookAndFeelPackage", "")
    print(f"Applied global theme: {active or '(none)'}")

    declared: dict[tuple[str, str], dict[str, str]] = {}
    if active:
        try:
            declared = manifest.load(active).settings
        except FileNotFoundError:
            print(_paint("  (that package is not installed; comparing live config only)", "2"))

    rows = resolve.audit(declared, live)
    if not rows:
        print("  nothing configured")
        return 0

    counts = _print_audit(rows, args.verbose)
    broken, degraded, unset = counts[MISSING], counts[DEGRADED], counts[resolve.UNSET]
    parts = [f"{counts[OK]}/{len(rows)} ok"]
    if degraded:
        parts.append(f"{degraded} degraded")
    if unset:
        parts.append(f"{unset} unset")
    if broken:
        parts.append(f"{broken} missing")
    print(f"\n{', '.join(parts)}")

    if broken or degraded or unset:
        if active:
            print(f"\nRepair: lol-kde install {active}   "
                  f"{_paint('# fetch missing pieces', '2')}")
            print(f"        lol-kde apply {active}     "
                  f"{_paint('# reset unset/drifted pointers', '2')}")
        else:
            print("\nNo global theme is set, so there is nothing to repair against.")
    return 1 if broken else 0


def cmd_list(args: argparse.Namespace) -> int:
    installed = manifest.list_installed()
    if not installed:
        print("No global themes installed.")
        return 0
    for name, _ in installed:
        try:
            theme = manifest.load(name)
        except FileNotFoundError:
            continue
        deps = len(theme.dependencies)
        note = f"{theme.pointer_count} pointers"
        if deps:
            note += f", {deps} declared deps"
        print(f"  {name:<40} {_paint(note, '2')}")
    return 0


def _load_or_fail(name: str) -> manifest.GlobalTheme | None:
    try:
        return manifest.load(name)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        available = ", ".join(n for n, _ in manifest.list_installed()[:6])
        if available:
            print(f"installed: {available} ...", file=sys.stderr)
        return None


def cmd_check(args: argparse.Namespace) -> int:
    theme = _load_or_fail(args.theme)
    if theme is None:
        return 2

    print(f"{theme.display_name}  {_paint(str(theme.path), '2')}")
    results = resolve.resolve_settings(theme.settings)
    if not results:
        print("  (this package declares no component pointers)")
    broken, degraded = _print_resolutions(results, args.verbose)
    print(f"\n{_summary(broken, degraded, len(results))}")

    if theme.dependencies:
        print(f"\nDeclared dependencies ({len(theme.dependencies)}):")
        for dep in theme.dependencies:
            print(f"  {dep.knsrc:<20} {_paint(dep.store_url, '2')}")
        print(_paint("\nKDE reads none of these. lol-kde install will fetch them.", "2"))
    return 1 if broken else 0


def cmd_install(args: argparse.Namespace) -> int:
    theme = _load_or_fail(args.theme)
    if theme is None:
        return 2

    if not theme.dependencies:
        print(f"{theme.display_name} declares no dependencies.")
        print(_paint("Nothing to fetch. Run 'lol-kde check' to see what is missing anyway.", "2"))
        return 0

    before = {r.kind: r for r in resolve.resolve_settings(theme.settings)}
    needed = [r for r in before.values() if r.status != OK]
    if not needed and not args.force:
        print(f"{theme.display_name}: all components already resolve. Nothing to do.")
        return 0

    verb = "Would install" if args.dry_run else "Installing"
    print(f"{verb} {len(theme.dependencies)} declared dependencies for {theme.display_name}\n")

    failures = 0
    for dep in theme.dependencies:
        result = installer.install_dependency(
            dep, force=args.force, dry_run=args.dry_run
        )
        name = result.item.name if result.item else dep.content_id
        status_mark = {
            "installed": _paint("ok  ", "32"),
            "skipped": _paint("skip", "33"),
            "failed": _paint("FAIL", "31"),
        }[result.status]
        print(f"  {status_mark}  {dep.knsrc:<18} {name}")
        if result.detail:
            print(f"{' ' * (4 + _MARK_WIDTH)}{_paint(result.detail, '2')}")
        if result.status == "failed":
            failures += 1

    if not args.dry_run:
        print("\nRe-checking:")
        after = resolve.resolve_settings(theme.settings)
        broken, degraded = _print_resolutions(after, args.verbose)
        print(f"\n{_summary(broken, degraded, len(after))}")
        if broken or degraded:
            print(_paint(
                "\nRemaining gaps are components the theme never declared, "
                "or that live outside the KDE Store (Kvantum themes usually do).",
                "2",
            ))
    return 1 if failures else 0


def cmd_apply(args: argparse.Namespace) -> int:
    theme = _load_or_fail(args.theme)
    if theme is None:
        return 2
    tool = shutil.which("plasma-apply-lookandfeel")
    if not tool:
        print("error: plasma-apply-lookandfeel not found", file=sys.stderr)
        return 2
    completed = subprocess.run([tool, "--apply", theme.name], text=True)
    if completed.returncode != 0:
        return completed.returncode
    print(f"\nApplied {theme.display_name}. Verifying:")
    results = resolve.resolve_settings(resolve.live_settings())
    broken, degraded = _print_resolutions(results, args.verbose)
    print(f"\n{_summary(broken, degraded, len(results))}")
    return 1 if broken else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lol-kde",
        description="Resolve, verify and repair KDE global theme dependencies.",
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show resolved paths and extra detail")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="audit the currently applied configuration")
    doctor.set_defaults(func=cmd_doctor)

    listing = sub.add_parser("list", help="list installed global themes")
    listing.set_defaults(func=cmd_list)

    check = sub.add_parser("check", help="resolve one global theme's pointers")
    check.add_argument("theme")
    check.set_defaults(func=cmd_check)

    inst = sub.add_parser("install", help="fetch a global theme's missing dependencies")
    inst.add_argument("theme")
    inst.add_argument("--dry-run", action="store_true", help="resolve but download nothing")
    inst.add_argument("--force", action="store_true", help="replace already-installed content")
    inst.set_defaults(func=cmd_install)

    apply_cmd = sub.add_parser("apply", help="apply a global theme, then verify it")
    apply_cmd.add_argument("theme")
    apply_cmd.set_defaults(func=cmd_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
