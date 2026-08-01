"""Command line interface.

Output is deliberately terse: one line per component, explanations only for
things that are broken. Nothing here prints a wall of text at you.
"""

from __future__ import annotations

import argparse
import difflib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import banner, catalog
from . import legacy
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

    print(_paint(banner.closing_remark(counts[OK], degraded, unset, broken), "2"))

    if broken or degraded or unset:
        if active:
            print(f"\nRepair: lol-kde install {active}   "
                  f"{_paint('# fetch missing pieces', '2')}")
            print(f"        lol-kde apply {active}     "
                  f"{_paint('# reset unset/drifted pointers', '2')}")
        else:
            print("\nNo global theme is set, so there is nothing to repair against.")
    return 1 if broken else 0


def cmd_legacy(args: argparse.Namespace) -> int:
    """List, and optionally remove, packages using pre-5.19 metadata."""
    found = legacy.scan()
    if not found:
        print("No packages using legacy metadata.desktop. Unexpectedly tidy.")
        return 0

    print(f"{len(found)} package(s) still using legacy metadata.desktop:\n")
    for pkg in found:
        flags = []
        if pkg.active:
            flags.append(_paint("IN USE", "32"))
        if not pkg.user_owned:
            flags.append(_paint("system", "2"))
        if pkg.referenced_by:
            flags.append(_paint(f"needed by {', '.join(pkg.referenced_by)}", "36"))
        suffix = ("  " + " ".join(flags)) if flags else ""
        print(f"  {pkg.kind:<14} {pkg.name:<34}{suffix}")
        if args.verbose:
            print(f"                 {_paint(str(pkg.path), '2')}")

    removable = [p for p in found if p.removable]
    blocked = [p for p in found if not p.removable]

    print(f"\n{len(removable)} removable, {len(blocked)} protected"
          f" (in use, needed by another theme, or installed system-wide).")
    print(_paint("These still work. They are only a problem during live theme "
                 "reloads, where plasmashell has been seen to crash inside KSvg.", "2"))

    if not args.remove:
        if removable:
            print(f"\nTo delete the {len(removable)} removable ones: "
                  f"lol-kde legacy --remove")
        return 0

    if not removable:
        print("\nNothing to remove: every legacy package is in use or system-owned.")
        return 0

    print("\nWould delete:")
    for pkg in removable:
        print(f"  {pkg.path}")

    if not args.yes:
        try:
            answer = input("\nDelete these directories permanently? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Nothing was removed.")
            return 0

    removed = 0
    for pkg in removable:
        try:
            shutil.rmtree(pkg.path)
            print(f"  removed  {pkg.kind}: {pkg.name}")
            removed += 1
        except OSError as exc:
            print(f"  FAILED   {pkg.name}: {exc}", file=sys.stderr)
    print(f"\n{removed} removed, {len(blocked)} left alone.")
    return 0


def cmd_please(args: argparse.Namespace) -> int:
    """Install a store page and everything its description says it needs."""
    content_id = catalog.parse_url(args.url)
    if content_id is None:
        print(f"error: {args.url!r} is not a store page URL or content id",
              file=sys.stderr)
        return 2

    print(f"Resolving {content_id} ...")
    try:
        nodes = catalog.walk(content_id, max_depth=args.depth)
    except Exception as exc:                       # network, XML, anything
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not nodes or nodes[0].item is None:
        print(f"error: content {content_id} could not be looked up", file=sys.stderr)
        return 1

    root = nodes[0]
    extra = len(nodes) - 1
    print(f"\n{root.item.name}  {_paint(root.item.typename, '2')}")
    if extra:
        print(_paint(f"The description names {extra} further "
                     f"component{'s' if extra != 1 else ''}:", "2"))

    for node in nodes[1:]:
        if node.item is None:
            print(f"  {_paint('?', '31')}  {node.content_id}  "
                  f"{_paint('could not be looked up', '2')}")
            continue
        route = node.route
        where = (str(route.target) if route and route.target
                 else "kpackagetool6" if route and route.uses_kpackage else "?")
        mark = _paint("+", "32") if route and route.known else _paint("?", "33")
        print(f"  {mark}  {node.item.name:<30} {node.item.typename:<26} "
              f"{_paint(where, '2')}")

    if args.dry_run:
        print(_paint("\nDry run: nothing downloaded.", "2"))
        return 0

    if not args.yes:
        try:
            answer = input(f"\nDownload and install {len(nodes)} item(s)? [Y/n] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() in ("n", "no"):
            print("Nothing was installed.")
            return 0

    print()
    failures = 0
    root_destination: Path | None = None
    for node in nodes:
        status, detail, destination = installer.install_from_store(node, force=args.force)
        if node is root and destination is not None:
            root_destination = destination
        name = node.item.name if node.item else node.content_id
        mark = {"installed": _paint("ok  ", "32"),
                "skipped": _paint("skip", "33"),
                "failed": _paint("FAIL", "31")}[status]
        print(f"  {mark}  {name}")
        if detail:
            print(f"        {_paint(detail, '2')}")
        if status == "failed":
            failures += 1

    if root.route and root.route.category == "lookandfeel":
        # The installed package name is rarely the store's display name
        # ("Layan look and feel theme" -> com.github.vinceliuice.Layan), so
        # report what actually appeared on disk.
        target = root_destination.name if root_destination else root.item.name

        # The description and X-KPackage-Dependencies are different lists by
        # different authors. Layan's description names 4 things; its manifest
        # names 7, including the cursor theme the description forgets.
        if root_destination:
            try:
                theme = manifest.load(target)
            except (FileNotFoundError, OSError):
                theme = None
            if theme and theme.dependencies:
                missing = [r for r in resolve.resolve_settings(theme.settings)
                           if r.status != OK]
                if missing:
                    print(_paint(f"\nIts manifest declares "
                                 f"{len(theme.dependencies)} dependencies and "
                                 f"{len(missing)} component(s) are still "
                                 f"unresolved. Fetching those too:", "2"))
                    # Ask for the specific variant the theme names, e.g.
                    # 'Layan-white-cursors' out of an entry shipping three.
                    wanted = {r.kind: r.value for r in missing}
                    kinds = {"xcursor": "cursors", "icons": "icons",
                             "colorschemes": "color-scheme",
                             "plasma-themes": "plasma-style",
                             "aurorae": "decoration"}
                    for dep in theme.dependencies:
                        prefer = wanted.get(kinds.get(dep.knsrc, ""), "")
                        result = installer.install_dependency(
                            dep, force=args.force, prefer=prefer)
                        if result.status == "installed":
                            name = result.item.name if result.item else dep.content_id
                            print(f"  {_paint('ok  ', '32')}  {name}")

        print(f"\nTo use it:  {_paint(f'lol-kde apply {target}', '36')}")
    return 1 if failures else 0


def cmd_why(args: argparse.Namespace) -> int:
    """Explain the architecture, since nothing else will."""
    print(banner.render(shutil.get_terminal_size((80, 24)).columns, _COLOR))
    print()
    print(banner.WHY)
    return 0


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
        names = [n for n, _ in manifest.list_installed()]
        close = difflib.get_close_matches(name, names, n=3, cutoff=0.4)
        if close:
            print(f"\ndid you mean:  {'  '.join(close)}", file=sys.stderr)
        if names:
            # Print all of them. A truncated list is not a list.
            print(f"\ninstalled ({len(names)}):", file=sys.stderr)
            for installed in names:
                print(f"  {installed}", file=sys.stderr)
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
    style = theme.settings.get(("plasmarc", "Theme"), {}).get("name", "")
    if style and legacy.is_legacy("plasma/desktoptheme", style):
        print(_paint(
            f"note: Plasma style {style!r} uses legacy metadata.desktop. Live "
            "reloads of these have been seen to crash plasmashell inside KSvg.\n"
            "      The crash is self-healing (systemd restarts it) and your "
            "settings survive. Logging out and back in avoids it entirely.\n", "33"))

    completed = subprocess.run([tool, "--apply", theme.name], text=True)
    if completed.returncode != 0:
        return completed.returncode

    print(f"\nApplied {theme.display_name}. Verifying:")
    rows = resolve.audit(theme.settings, resolve.live_settings())
    if not rows:
        # Never report "0/0 ok". Nothing to check is a failure, not a pass.
        print("  error: the theme applied but not one of its pointers is "
              "readable afterwards.", file=sys.stderr)
        print("  This should not happen; run 'lol-kde doctor -v' and report it.",
              file=sys.stderr)
        return 1

    counts = _print_audit(rows, args.verbose)
    broken, degraded, unset = counts[MISSING], counts[DEGRADED], counts[resolve.UNSET]
    print(f"\n{counts[OK]}/{len(rows)} ok")
    print(_paint(banner.closing_remark(counts[OK], degraded, unset, broken), "2"))
    return 1 if broken or unset else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lol-kde",
        description="Resolve, verify and repair KDE global theme dependencies.",
        epilog="lol-kde why  explains what a Global Theme actually is.",
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show resolved paths and extra detail")
    sub = parser.add_subparsers(dest="command")

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

    leg = sub.add_parser("legacy", help="find packages using pre-5.19 metadata.desktop")
    leg.add_argument("--remove", action="store_true",
                     help="delete removable ones (never the active or system-wide)")
    leg.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    leg.set_defaults(func=cmd_legacy)

    nope = sub.add_parser("no-thank-you",
                          help="resolve a store page and report, installing nothing")
    nope.add_argument("url", help="store page URL, or a bare content id")
    nope.add_argument("--depth", type=int, default=1,
                      help="how far to follow dependency links (default 1)")
    nope.set_defaults(func=cmd_please, dry_run=True, force=False, yes=True)

    pls = sub.add_parser("please", help="install a store page and everything its description names")
    pls.add_argument("url", help="opendesktop/pling/store.kde.org page URL, or a bare content id")
    pls.add_argument("--depth", type=int, default=1,
                     help="how far to follow dependency links (default 1)")
    pls.add_argument("--dry-run", action="store_true", help="resolve but download nothing")
    pls.add_argument("--force", action="store_true", help="replace already-installed content")
    pls.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    pls.set_defaults(func=cmd_please)

    why = sub.add_parser("why", help="explain how KDE theming is actually layered")
    why.set_defaults(func=cmd_why)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # Bare invocation: introduce yourself, then get out of the way.
        print(banner.render(shutil.get_terminal_size((80, 24)).columns, _COLOR))
        print()
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
