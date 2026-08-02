"""Command line interface.

Output is deliberately terse: one line per component, explanations only for
things that are broken. Nothing here prints a wall of text at you.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import banner, catalog, compare, journal, snapshot
from . import legacy
from . import install as installer
from . import manifest, repair, resolve
from . import restore as restorer
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
            pad = ' ' * (4 + _MARK_WIDTH)
            print(f"{pad}{_detail(result.detail, pad)}")
        if verbose and result.path:
            print(f"          {_paint(str(result.path), '2')}")
    return broken, degraded


def _detail(text: str, indent: str) -> str:
    """Indent a detail block, including its continuation lines.

    Some details are lists long enough to need their own line -- Kvantum's
    opaque= exclusions, for instance. Without this they hang off the left
    margin and read as separate output.
    """
    return _paint(f"\n{indent}".join(text.splitlines()), "2")


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
            print(f"{indent}{_detail(detail, indent)}")
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
            # "the 1 removable ones" -- seen on turn 9, once the count could
            # actually be one. The rest of this program spells its counts.
            what = ("it" if len(removable) == 1
                    else f"the {len(removable)} removable ones")
            print(f"\nTo delete {what}: lol-kde legacy --remove")
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

    auto_snapshot("before legacy --remove")
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
    if not args.dry_run:
        auto_snapshot(f"before install {theme.name}")
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
            pad = ' ' * (4 + _MARK_WIDTH)
            print(f"{pad}{_detail(result.detail, pad)}")
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

    auto_snapshot(f"before apply {theme.name}")
    completed = subprocess.run([tool, "--apply", theme.name], text=True)
    if completed.returncode != 0:
        return completed.returncode

    # plasma-apply-lookandfeel copies the theme's pointers verbatim, including
    # the Aurorae plugin name, which every published theme still gets wrong on
    # Plasma 6.6. Fix it before verifying, so the audit reflects reality.
    fixed = repair.aurorae_plugin(*repair.live_decoration())
    if fixed:
        print(_paint(f"  repaired {fixed}", "36"))

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


def auto_snapshot(reason: str) -> dict | None:
    """Capture before mutating. Deliberately has no opt-out flag.

    The only thing a --no-snapshot switch could do is destroy the artifact
    that makes the operation undoable.
    """
    try:
        meta = snapshot.capture(reason=reason, with_sweep=False)
    except OSError as exc:
        print(_paint(f"  warning: could not snapshot first ({exc})", "33"),
              file=sys.stderr)
        return None
    coverage = meta["coverage"]
    print(_paint(f"  snapshot  {meta['id']}  ({reason})", "36"))
    line = (f"            {meta['files_captured']} files, {meta['bytes']} bytes, "
            f"coverage {coverage['ok']}/{coverage['total']}")
    if coverage["gaps"]:
        line += _paint(f"  GAPS: {', '.join(coverage['gaps'])}", "33")
    print(_paint(line, "2"))
    journal.record("snapshot", snapshot_id=meta["id"], reason=reason,
                   coverage=coverage)
    return meta


def cmd_snapshot(args: argparse.Namespace) -> int:
    if args.explain:
        return _explain_manifest(args)
    if args.around:
        return _snapshot_around(args)

    meta = snapshot.capture(message=args.message, label=args.label,
                            with_sweep=not args.no_sweep)
    journal.record("snapshot", snapshot_id=meta["id"], reason="manual",
                   message=args.message, coverage=meta["coverage"])
    return _report_snapshot(meta, args.verbose)


def _report_snapshot(meta: dict, verbose: bool) -> int:
    coverage = meta["coverage"]
    print(f"Snapshot {meta['id']}")
    print(f"  {meta['files_captured']} files, {meta['bytes']:,} bytes  "
          f"-> {meta['path']}")
    gaps = coverage["gaps"]
    mark = _mark(OK if not gaps else DEGRADED)
    print(f"  {mark}  coverage {coverage['ok']}/{coverage['total']} facts verified")

    if gaps:
        detail = json.loads((Path(meta["path"]) / "coverage.json").read_text())
        print()
        print(_paint("  A gap means this snapshot does NOT contain a fact the live", "33"))
        print(_paint("  system has. Fix the manifest in lolkde/snapshot.py, re-snapshot.", "33"))
        for row in detail:
            if row["status"] != "GAP":
                continue
            print(f"\n  GAP  {row['probe']} = {row['live']}")
            print(_paint(f"       {row['detail']}", "2"))
            for where in row["found_at"]:
                print(_paint(f"       found at: {where}", "36"))
            if not row["found_at"]:
                print(_paint("       could not locate this value anywhere in ~/.config", "2"))
    for warning in meta.get("warnings", []):
        print(_paint(f"\n  warn  {warning}", "33"))
    if verbose:
        print(_paint(f"\n  read it with: lol-kde diff {meta['id']}", "2"))
    return 1 if gaps else 0


def _explain_manifest(args: argparse.Namespace) -> int:
    print("What a snapshot captures, and how much we trust each entry.\n")
    tiers: dict[str, list] = {}
    for entry in snapshot.MANIFEST:
        tiers.setdefault(entry.tier, []).append(entry)
    for tier in ("core", "context", "legacy"):
        rows = tiers.get(tier, [])
        if not rows:
            continue
        print(_paint(f"{tier}:", "1"))
        for entry in rows:
            hits = snapshot.expand(entry)
            state = f"{len(hits)} file(s)" if hits else _paint("absent", "2")
            colour = {"verified": "32", "likely": "33", "unverified": "31"}[entry.confidence]
            print(f"  {_paint(entry.confidence.ljust(10), colour)} "
                  f"{entry.root}/{entry.pattern:<45} {state}")
            if args.verbose:
                print(_paint(f"             {entry.holds}", "2"))
                if entry.superseded_by:
                    print(_paint(f"             superseded by {entry.superseded_by}", "36"))
        print()
    print(_paint("Low-confidence entries are listed in docs/open-questions.md,", "2"))
    print(_paint("each with the command that would settle it.", "2"))
    return 0


def _snapshot_around(args: argparse.Namespace) -> int:
    """Snapshot, run a command, wait for KDE to settle, snapshot again.

    Doubles as a manifest-learning loop: every deliberate change reports which
    paths moved, split into ones we capture and ones we do not.
    """
    before = snapshot.capture(message=f"before: {args.around}", reason="around")
    print(f"before  {before['id']}")
    print(_paint(f"running: {args.around}", "2"))
    completed = subprocess.run(args.around, shell=True)
    waited = snapshot.wait_for_quiescence()
    print(_paint(f"waited {waited:.1f}s for ~/.config to settle", "2"))
    after = snapshot.capture(message=f"after: {args.around}", reason="around")
    print(f"after   {after['id']}")
    journal.record("around", command=args.around, before=before["id"],
                   after=after["id"], exit_code=completed.returncode)

    report = compare.compare(Path(before["path"]), Path(after["path"]))
    print()
    _print_report(report, args.verbose)
    if report.empty:
        print(_paint("\nNothing changed. If you expected a change, the manifest is "
                     "missing the file that holds it.", "33"))
        return 1
    return 0


def cmd_snapshots(args: argparse.Namespace) -> int:
    found = snapshot.listing()
    if not found:
        print("No snapshots yet.  lol-kde snapshot -m \"baseline\"")
        return 0
    for meta in found:
        coverage = meta.get("coverage", {})
        gaps = coverage.get("gaps") or []
        mark = _mark(DEGRADED if gaps else OK)
        theme = meta.get("applied_theme") or "(none)"
        print(f"  {mark}  {meta['id']:<32} {theme}")
        note = meta.get("message") or meta.get("reason", "")
        detail = f"        {coverage.get('ok', 0)}/{coverage.get('total', 0)} verified"
        if note:
            detail += f"  {note}"
        print(_paint(detail, "2"))
    old = snapshot.store() / "checkpoints"
    if old.is_dir():
        count = len([p for p in old.iterdir() if p.is_dir()])
        print(_paint(f"\n  plus {count} hand-made checkpoint(s) in {old}", "2"))
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    before = snapshot.find(args.before)
    if before is None:
        return _no_such_snapshot(args.before)

    if args.after:
        after = snapshot.find(args.after)
        if after is None:
            return _no_such_snapshot(args.after)
        after_id = after.name
        report = compare.compare(before, after)
    else:
        # Capture the live system through exactly the same code path, so the
        # two sides can never be compared unfairly.
        with tempfile.TemporaryDirectory(prefix="lol-kde-live-") as tmp:
            live = Path(tmp) / "live"
            meta = snapshot.capture(reason="diff --live", into=live)
            after_id = "live"
            report = compare.compare(before, live)
            _ = meta

    print(f"{before.name}  ->  {after_id}\n")
    if args.changelog:
        for row in journal.changelog_rows(report, before.name, after_id):
            print(row)
        return 0
    _print_report(report, args.all)
    return 1 if not report.empty else 0


def _no_such_snapshot(reference: str) -> int:
    candidates = snapshot.ambiguous(reference)
    if len(candidates) > 1:
        print(f"error: {reference!r} matches {len(candidates)} snapshots:", file=sys.stderr)
        for name in candidates:
            print(f"  {name}", file=sys.stderr)
    else:
        print(f"error: no snapshot matching {reference!r}", file=sys.stderr)
        print("       lol-kde snapshots   lists what you have", file=sys.stderr)
    return 2


_SECTION_COLOUR = {"SEMANTIC": "36", "SETTINGS": "0", "UNMANIFESTED": "33",
                   "BYTES": "2", "INVENTORY": "0"}


def _print_report(report: compare.Report, show_all: bool) -> None:
    if report.empty:
        print("No differences.")
        return
    sections = (
        ("SEMANTIC", report.semantic),
        ("SETTINGS", report.settings),
        ("UNMANIFESTED", report.unmanifested),
        ("BYTES", report.byte_only),
        ("INVENTORY", report.inventory),
    )
    for name, changes in sections:
        if not changes:
            continue
        shown = changes if show_all else changes[:20]
        print(_paint(name, _SECTION_COLOUR.get(name, "0")))
        if name == "UNMANIFESTED":
            print(_paint("  files that changed and are in no manifest entry", "2"))
        for change in shown:
            head = f"  {change.kind} {change.where}"
            body = change.what
            if change.before or change.after:
                body += f"    {change.before or '(absent)'} -> {change.after or '(absent)'}"
            print(f"{head}  {body}")
            if change.note:
                print(_paint(f"      {change.note}", "2"))
        if len(changes) > len(shown):
            print(_paint(f"  … {len(changes) - len(shown)} more (--all)", "2"))
        print()


_RESTORE_MARK = {
    restorer.OK: ("ok", "32"), restorer.SAME: ("same", "2"),
    restorer.PIN_LOST: ("pin-lost", "33"), restorer.STALE: ("stale", "33"),
    restorer.DIVERGED: ("DIVERGED", "31"), restorer.FAILED: ("FAILED", "31"),
    restorer.EXTRA: ("extra", "2"),
}


def cmd_restore(args: argparse.Namespace) -> int:
    root = snapshot.find(args.snapshot)
    if root is None:
        return _no_such_snapshot(args.snapshot)

    known = list(restorer.components())
    selected = known if args.all or not args.component else [
        name.strip() for name in args.component.split(",") if name.strip()]
    unknown = [name for name in selected if name not in known]
    if unknown:
        print(f"error: unknown component(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"       known: {', '.join(known)}", file=sys.stderr)
        return 2

    plan = restorer.build(root, selected)
    _print_plan(plan, args.verbose)

    if plan.blockers:
        return 2
    if not args.apply:
        if plan.writes:
            print(_paint("\nNothing was written. To do it:", "1"))
            print(f"  lol-kde restore {plan.snapshot_id} --apply"
                  + ("" if args.all or not args.component
                     else f" --component {','.join(selected)}"))
        return 0
    if not plan.writes:
        print("\nNothing to do.")
        return 0

    if not args.yes:
        try:
            answer = input(f"\nApply {len(plan.writes)} change(s)? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing was written.")
            return 0

    lock = restorer.Lock()
    held = lock.acquire(break_stale=args.break_lock)
    if held:
        print(f"error: {held}", file=sys.stderr)
        return 2
    try:
        # Unconditional, and there is no flag to skip it: its only function
        # would be to remove the artifact that makes this undoable.
        print("\nSnapshotting first…")
        before = snapshot.capture(reason="before restore",
                                  message=f"before restore of {plan.snapshot_id}")
        print(f"  {before['id']}")

        outcome = restorer.run(plan, pre_snapshot=before["id"])
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    finally:
        lock.release()

    _print_outcome(outcome)
    return outcome.exit_code


def _print_plan(plan, verbose: bool) -> None:
    print(f"{plan.snapshot_id}  ->  live")
    print(_paint(f"  components: {', '.join(plan.selected)}\n", "2"))

    for step in plan.steps:
        if step.action == restorer.SAME and not verbose:
            continue
        colour = "2" if step.action == restorer.SAME else "0"
        print("  " + _paint(step.describe(), colour))
        if step.note:
            print(_detail(step.note, "      "))

    if plan.unchanged and not verbose:
        print(_paint(f"  ({plan.unchanged} already correct, -v to show)", "2"))

    for warning in plan.warnings:
        print("\n" + _paint("warning: ", "33") + warning)
    for blocker in plan.blockers:
        print("\n" + _paint("error: ", "31") + blocker)

    if not plan.blockers:
        print(f"\n  {len(plan.writes)} write(s), {plan.unchanged} unchanged")


def _print_outcome(outcome) -> None:
    print()
    for step in outcome.steps:
        if step.outcome in ("", restorer.EXTRA):
            continue
        label, colour = _RESTORE_MARK.get(step.outcome, (step.outcome, "0"))
        print(f"  {_paint(label, colour):<20} "
              f"{step.file}:{step.group}:{step.key}")
        if step.detail and step.outcome != restorer.OK:
            print(_detail(step.detail, "      "))

    tally = "  ".join(f"{k}={v}" for k, v in sorted(outcome.tally().items()))
    print(f"\n  {tally}")
    print(_paint(f"  journal: {outcome.directory}/journal.jsonl", "2"))

    if outcome.aborted:
        # Three lines, and no automatic rollback: that would itself be a
        # restore, run by the code path that just failed, when the state is
        # least understood.
        print(_paint("\n  aborted mid-restore. Nothing rolled back.", "31"))
        print(f"  pre-restore snapshot: {outcome.pre_snapshot}")
        print(f"  undo: lol-kde restore {outcome.pre_snapshot} --apply --yes")
        return

    rows = restorer.changelog_row(outcome)
    if rows:
        print(_paint("\n  CHANGELOG.md row:", "2"))
        for row in rows:
            print("  " + row)


def cmd_history(args: argparse.Namespace) -> int:
    found = journal.entries(args.n)
    if not found:
        print("Nothing recorded yet.")
        return 0
    for entry in found:
        when = entry.get("at", "")[:19].replace("T", " ")
        action = entry.get("action", "?")
        rest = {k: v for k, v in entry.items()
                if k not in ("at", "action", "pid")}
        summary = "  ".join(f"{k}={v}" for k, v in sorted(rest.items()))
        print(f"  {when}  {action:<10} {_paint(summary, '2')}")
    print(_paint(f"\n  {journal.path()}", "2"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lol-kde",
        description="Resolve, verify and repair KDE global theme dependencies.",
        epilog="lol-kde why  explains what a Global Theme actually is.",
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show resolved paths and extra detail")

    # Accept -v after the subcommand too: "lol-kde doctor -v" is what people
    # actually type, and argparse only allows it before by default.
    #
    # default=SUPPRESS matters. Without it the subparser's own default (False)
    # is written into the shared namespace *after* the top-level flag is
    # parsed, so "lol-kde -v doctor" silently turned verbose back off.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true",
                        default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", parser_class=lambda **kw:
                                argparse.ArgumentParser(parents=[common], **kw))

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

    snap = sub.add_parser("snapshot", help="capture the current KDE state")
    snap.add_argument("-m", "--message", default="", help="why you took it")
    snap.add_argument("--label", default="", help="short slug appended to the id")
    snap.add_argument("--explain", action="store_true",
                      help="print the manifest instead of capturing")
    snap.add_argument("--around", metavar="CMD",
                      help="snapshot, run CMD, wait for KDE to settle, snapshot again")
    snap.add_argument("--no-sweep", action="store_true",
                      help="skip the mtime sweep (faster, loses UNMANIFESTED)")
    snap.set_defaults(func=cmd_snapshot)

    snaps = sub.add_parser("snapshots", help="list captured snapshots")
    snaps.set_defaults(func=cmd_snapshots)

    dif = sub.add_parser("diff", help="compare two snapshots, or one against live")
    dif.add_argument("before", nargs="?", default="latest")
    dif.add_argument("after", nargs="?", default="",
                     help="a snapshot id; omit to compare against the live system")
    dif.add_argument("--changelog", action="store_true",
                     help="emit a paste-ready CHANGELOG.md table")
    dif.add_argument("--all", action="store_true",
                     help="include noisy sections and low-signal paths")
    dif.set_defaults(func=cmd_diff)

    # Restore prints a plan and writes nothing unless asked twice. It has the
    # largest blast radius in the program and should not be the least guarded;
    # a mistyped id that resolves to a *different valid snapshot* is the worst
    # outcome available, and it is silent. The plan makes that visible free.
    res = sub.add_parser(
        "restore", help="put a snapshot's theme settings back (prints a plan by default)",
        epilog="kdedefaults is restored by re-deriving it, ~/.config by replay. "
               "They cannot disagree because only one is ever written.")
    res.add_argument("snapshot", help="snapshot id, or a unique prefix of one")
    res.add_argument("--apply", action="store_true",
                     help="actually write. Without this, nothing is written")
    res.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    res.add_argument("--component", default="",
                     help="comma-separated; default is all of them")
    res.add_argument("--all", action="store_true", help="every component (the default)")
    res.add_argument("--break-lock", action="store_true",
                     help="take the lock from a process that is no longer running")
    res.set_defaults(func=cmd_restore)

    hist = sub.add_parser("history", help="what this tool has done to your machine")
    hist.add_argument("-n", type=int, default=20, help="how many entries (0 = all)")
    hist.set_defaults(func=cmd_history)
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
