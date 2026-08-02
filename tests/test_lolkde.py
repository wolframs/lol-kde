"""Tests for the parts that are easy to get subtly wrong.

Run with: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lolkde import (banner, catalog, cli, compare, install, journal,  # noqa: E402
                    kconfig, knsrc, legacy, manifest, paths, repair,
                    resolve, snapshot, store)


class TestManifestParsing(unittest.TestCase):
    def test_defaults_double_bracket_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "defaults"
            path.write_text(
                "[kdeglobals][KDE]\nwidgetStyle=kvantum\n\n"
                "[kwinrc][org.kde.kdecoration2]\n"
                "theme=__aurorae__svg__Sweet-ambar-blue\n"
            )
            parsed = kconfig.parse_lookandfeel_defaults(path)
        self.assertEqual(parsed[("kdeglobals", "KDE")]["widgetStyle"], "kvantum")
        self.assertEqual(
            parsed[("kwinrc", "org.kde.kdecoration2")]["theme"],
            "__aurorae__svg__Sweet-ambar-blue",
        )

    def test_kns_uri_parsing(self):
        deps = manifest.parse_dependencies({
            "X-KPackage-Dependencies": [
                "kns://xcursor.knsrc/api.kde-look.org/1393084",
                "kns://plasmoids.knsrc/api.kde-look.org/2144212",
                "not-a-kns-uri",
            ]
        })
        self.assertEqual(len(deps), 2)
        self.assertEqual(deps[0].knsrc, "xcursor")
        self.assertEqual(deps[0].host, "api.kde-look.org")
        self.assertEqual(deps[0].content_id, "1393084")
        self.assertEqual(deps[0].store_url, "https://store.kde.org/p/1393084")

    def test_no_dependencies_is_empty_not_error(self):
        self.assertEqual(manifest.parse_dependencies({}), [])


class TestKdeConfigQuirks(unittest.TestCase):
    def test_duplicate_keys_do_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kdeglobals"
            path.write_text("[KDE]\nwidgetStyle=Breeze\nwidgetStyle=Oxygen\n")
            self.assertEqual(kconfig.get(path, "KDE", "widgetStyle"), "Oxygen")

    def test_percent_in_value_is_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rc"
            path.write_text("[General]\nAdoptionCommand=apply %f\n")
            self.assertEqual(kconfig.get(path, "General", "AdoptionCommand"), "apply %f")

    def test_case_sensitive_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rc"
            path.write_text("[General]\nColorScheme=Stone\n")
            self.assertEqual(kconfig.get(path, "General", "ColorScheme"), "Stone")
            self.assertIsNone(kconfig.get(path, "General", "colorscheme"))


class TestResolution(unittest.TestCase):
    def test_missing_components_report_missing(self):
        for result in (
            resolve.icon_theme("definitely-not-installed-xyz"),
            resolve.cursor_theme("definitely-not-installed-xyz"),
            resolve.plasma_style("definitely-not-installed-xyz"),
            resolve.look_and_feel("definitely-not-installed-xyz"),
        ):
            self.assertEqual(result.status, resolve.MISSING, result.label)
            self.assertTrue(result.detail)

    def test_empty_pointer_is_not_a_failure(self):
        self.assertEqual(resolve.icon_theme("").status, resolve.OK)
        self.assertEqual(resolve.color_scheme("").status, resolve.OK)

    def test_aurorae_prefix_is_stripped(self):
        result = resolve.decoration("org.kde.kwin.aurorae", "__aurorae__svg__no-such-theme")
        self.assertEqual(result.status, resolve.MISSING)
        self.assertIn("no-such-theme", result.detail)

    def test_builtin_qt_style_resolves(self):
        self.assertEqual(resolve.widget_style("fusion").status, resolve.OK)


class TestAuroraePlugin(unittest.TestCase):
    """Plasma 6.6 moved the Aurorae SVG themes to a second plugin.

    Every theme in the store still names the old one. KWin loads the theme
    regardless, so the desktop looks correct while System Settings shows no
    decoration selected at all -- which reads as "window decorations are
    broken on this machine" and sent us hunting in the wrong place.
    """

    def test_provider_is_one_of_the_two_known_plugins(self):
        self.assertIn(resolve.aurorae_provider(),
                      (resolve.AURORAE_LEGACY_PLUGIN, resolve.AURORAE_SVG_PLUGIN))

    def test_stale_plugin_name_is_degraded_not_ok(self):
        installed = _any_installed_aurorae_theme()
        if installed is None:
            self.skipTest("no Aurorae theme installed on this machine")
        if resolve.aurorae_provider() == resolve.AURORAE_LEGACY_PLUGIN:
            self.skipTest("this Plasma has no split Aurorae plugin")
        result = resolve.decoration(resolve.AURORAE_LEGACY_PLUGIN, installed)
        self.assertEqual(result.status, resolve.DEGRADED)
        self.assertIn(resolve.AURORAE_SVG_PLUGIN, result.detail)

    def test_correct_plugin_name_is_ok(self):
        installed = _any_installed_aurorae_theme()
        if installed is None:
            self.skipTest("no Aurorae theme installed on this machine")
        result = resolve.decoration(resolve.aurorae_provider(), installed)
        self.assertEqual(result.status, resolve.OK)

    def test_repair_is_a_no_op_when_already_correct(self):
        self.assertIsNone(
            repair.aurorae_plugin(resolve.aurorae_provider(),
                                  "__aurorae__svg__anything"))

    def test_repair_ignores_non_aurorae_decorations(self):
        self.assertIsNone(repair.aurorae_plugin("org.kde.breeze", ""))

    def test_kwin_reads_the_kdecoration2_group(self):
        # KWin loads KDecoration3 plugins out of a group named for
        # KDecoration2. Renaming the group silently unsets the decoration.
        self.assertEqual(repair.DECO_GROUP, "org.kde.kdecoration2")


class TestSnapshotManifest(unittest.TestCase):
    """The manifest is the whole feature. If it is wrong, nothing else matters."""

    def test_every_entry_says_what_it_holds(self):
        for entry in snapshot.MANIFEST:
            self.assertTrue(entry.holds.strip(), f"{entry.pattern} has no description")

    def test_tiers_and_confidence_are_from_the_known_sets(self):
        for entry in snapshot.MANIFEST:
            self.assertIn(entry.tier, (snapshot.CORE, snapshot.CONTEXT, snapshot.LEGACY))
            self.assertIn(entry.confidence,
                          (snapshot.VERIFIED, snapshot.LIKELY, snapshot.UNVERIFIED))

    def test_every_root_is_one_we_can_expand(self):
        known = set(snapshot.roots())
        for entry in snapshot.MANIFEST:
            self.assertIn(entry.root, known, entry.pattern)

    def test_legacy_entries_name_their_replacement(self):
        # A dead path with no successor recorded is how you forget that it is
        # dead. kscreen cost this project a checkpoint exactly that way.
        for entry in snapshot.MANIFEST:
            if entry.tier == snapshot.LEGACY:
                self.assertTrue(entry.superseded_by, entry.pattern)

    def test_the_file_that_caused_the_gap_is_in_the_manifest(self):
        patterns = {e.pattern for e in snapshot.MANIFEST}
        self.assertIn("kwinoutputconfig.json", patterns)

    def test_kdedefaults_layer_is_captured(self):
        # Several pointers resolve only from kdedefaults. A ~/.config-only
        # manifest looks complete and restores to a state that never existed.
        patterns = {e.pattern for e in snapshot.MANIFEST}
        self.assertIn("kdedefaults/*", patterns)

    def test_artwork_is_excluded_from_byte_capture(self):
        self.assertIn("*.svg", snapshot.NEVER_COPY)


class TestSweepBudget(unittest.TestCase):
    """The sweep must stay a fixed size and must not invent changes."""

    def _tree(self, root: Path, name: str, count: int) -> None:
        directory = root / name
        directory.mkdir(parents=True)
        for index in range(count):
            (directory / f"f{index}").write_text("x")

    def test_large_subtree_collapses_to_one_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._tree(base, "huge", 60)
            self._tree(base, "small", 3)
            rows = snapshot.sweep(base, budget=10)
            self.assertIn("huge", rows)
            self.assertTrue(rows["huge"]["collapsed"])
            self.assertNotIn("huge/f0", rows)
            self.assertIn("small/f0", rows)

    def test_collapsed_rows_do_not_record_a_walk_dependent_count(self):
        # Where the budget trips depends on walk order, so recording it would
        # make every collapsed subtree differ between two identical sweeps.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._tree(base, "huge", 60)
            row = snapshot.sweep(base, budget=10)["huge"]
            self.assertNotIn("files_seen", row)
            self.assertNotIn("files", row)

    def test_two_sweeps_of_an_unchanged_tree_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._tree(base, "huge", 60)
            self._tree(base, "small", 3)
            self.assertEqual(snapshot.sweep(base, budget=10),
                             snapshot.sweep(base, budget=10))

    def test_missing_base_is_empty_not_an_error(self):
        self.assertEqual(snapshot.sweep(Path("/definitely/not/here")), {})


class TestSnapshotIdentity(unittest.TestCase):

    def test_ids_sort_chronologically(self):
        first, second = snapshot.new_id(), snapshot.new_id()
        self.assertEqual(sorted([second, first])[0][:19], first[:19])

    def test_label_is_slugified_into_the_id(self):
        self.assertTrue(snapshot.new_id("before scale!").endswith("-before-scale"))

    def test_id_has_no_label_when_none_given(self):
        identifier = snapshot.new_id()
        self.assertRegex(identifier, r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z-[0-9a-f]{4}$")


class TestValueLocator(unittest.TestCase):
    """On a GAP, the snapshot must say where the value actually lives."""

    def test_finds_the_key_holding_a_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            (config / "kwinrc").write_text("[Xwayland]\nScale=1.25\n")
            (config / "other.json").write_text('{"outputs": [{"scale": 1.25}]}')
            saved = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                found = snapshot.locate_value("1.25")
            finally:
                if saved is None:
                    del os.environ["XDG_CONFIG_HOME"]
                else:
                    os.environ["XDG_CONFIG_HOME"] = saved
            self.assertTrue(any("kwinrc" in f for f in found), found)

    def test_empty_value_finds_nothing(self):
        self.assertEqual(snapshot.locate_value(""), [])


class TestCompare(unittest.TestCase):
    """Key-level diffing of two synthetic snapshots."""

    def _snap(self, root: Path, files: dict[str, str], audit=None, outputs=None):
        root.mkdir(parents=True, exist_ok=True)
        entries = []
        for relative, body in files.items():
            target = root / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
            entries.append({"path": relative, "source": f"/live/{relative}",
                            "status": "captured",
                            "sha256": hashlib.sha256(body.encode()).hexdigest()})
        (root / "manifest.json").write_text(json.dumps(entries))
        state = root / "state"
        state.mkdir(exist_ok=True)
        (state / "audit.json").write_text(json.dumps(audit or []))
        (state / "inventory.json").write_text(json.dumps({}))
        if outputs is not None:
            (state / "outputs.json").write_text(json.dumps({"outputs": outputs}))
        return root

    def test_changed_key_is_reported_with_both_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._snap(Path(tmp) / "a", {"config/kwinrc": "[Xwayland]\nScale=1.2\n"})
            b = self._snap(Path(tmp) / "b", {"config/kwinrc": "[Xwayland]\nScale=1.25\n"})
            report = compare.compare(a, b)
            changed = [c for c in report.settings if c.what == "Scale"]
            self.assertEqual(len(changed), 1)
            self.assertEqual((changed[0].before, changed[0].after), ("1.2", "1.25"))

    def test_version_churn_is_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._snap(Path(tmp) / "a", {"config/kwinrc": "[$Version]\nupdate_info=a\n"})
            b = self._snap(Path(tmp) / "b", {"config/kwinrc": "[$Version]\nupdate_info=b\n"})
            self.assertEqual(compare.compare(a, b).settings, [])

    def test_status_change_is_semantic_not_just_a_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._snap(Path(tmp) / "a", {},
                           audit=[{"label": "Icon theme", "live": "Tela", "status": "OK"}])
            b = self._snap(Path(tmp) / "b", {},
                           audit=[{"label": "Icon theme", "live": "Tela", "status": "MISSING"}])
            report = compare.compare(a, b)
            self.assertEqual(len(report.semantic), 1)
            self.assertIn("install", report.semantic[0].note)

    def test_json_diff_reports_a_path_not_a_text_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._snap(Path(tmp) / "a", {"config/x.json": '{"a": [{"scale": 1.2}]}'})
            b = self._snap(Path(tmp) / "b", {"config/x.json": '{"a": [{"scale": 1.25}]}'})
            report = compare.compare(a, b)
            self.assertEqual(len(report.settings), 1)
            self.assertEqual(report.settings[0].what, "a[0].scale")

    def test_output_scale_change_is_semantic(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._snap(Path(tmp) / "a", {}, outputs=[{"name": "DP-1", "scale": 1.25}])
            b = self._snap(Path(tmp) / "b", {}, outputs=[{"name": "DP-1", "scale": 1.2}])
            report = compare.compare(a, b)
            self.assertEqual(len(report.semantic), 1)
            self.assertIn("fractional", report.semantic[0].note)

    def test_identical_snapshots_produce_an_empty_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = {"config/kwinrc": "[Xwayland]\nScale=1.25\n"}
            a = self._snap(Path(tmp) / "a", body)
            b = self._snap(Path(tmp) / "b", body)
            self.assertTrue(compare.compare(a, b).empty)

    def test_integer_scale_is_not_flagged_as_fractional(self):
        self.assertEqual(compare._fractional(2.0), "")
        self.assertTrue(compare._fractional(1.2))


class TestVerboseFlagPosition(unittest.TestCase):
    """-v must work on both sides of the subcommand.

    The subparser's own default overwrote the top-level flag in the shared
    namespace, so `lol-kde -v doctor` silently ran non-verbose.
    """

    def setUp(self):
        self.parser = cli.build_parser()

    def test_before_the_subcommand(self):
        self.assertTrue(self.parser.parse_args(["-v", "doctor"]).verbose)

    def test_after_the_subcommand(self):
        self.assertTrue(self.parser.parse_args(["doctor", "-v"]).verbose)

    def test_absent_is_still_false(self):
        self.assertFalse(self.parser.parse_args(["doctor"]).verbose)

    def test_every_subcommand_accepts_it_afterwards(self):
        for command, extra in (("doctor", []), ("list", []), ("snapshots", []),
                               ("check", ["x"]), ("legacy", []), ("why", [])):
            with self.subTest(command=command):
                self.assertTrue(self.parser.parse_args([command] + extra + ["-v"]).verbose)


class TestWriteResult(unittest.TestCase):
    """An exit code is not evidence that a value was written."""

    def test_inherited_resolves_but_is_not_pinned(self):
        result = repair.WriteResult("kwinrc", "G", "k", "v", repair.INHERITED)
        self.assertTrue(result.ok)
        self.assertFalse(result.pinned)

    def test_wrote_is_pinned(self):
        self.assertTrue(repair.WriteResult("f", "g", "k", "v", repair.WROTE).pinned)

    def test_failed_is_neither(self):
        result = repair.WriteResult("f", "g", "k", "v", repair.FAILED)
        self.assertFalse(result.ok)
        self.assertFalse(result.pinned)


class TestJournal(unittest.TestCase):

    def test_a_corrupt_line_costs_one_entry_not_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = os.environ.get("LOL_KDE_HOME")
            os.environ["LOL_KDE_HOME"] = tmp
            try:
                journal.record("snapshot", snapshot_id="a")
                with open(journal.path(), "a", encoding="utf-8") as handle:
                    handle.write("{not json\n")
                journal.record("apply", theme="b")
                found = journal.entries()
            finally:
                if saved is None:
                    del os.environ["LOL_KDE_HOME"]
                else:
                    os.environ["LOL_KDE_HOME"] = saved
            self.assertEqual([e["action"] for e in found], ["snapshot", "apply"])

    def test_no_journal_is_an_empty_list(self):
        saved = os.environ.get("LOL_KDE_HOME")
        os.environ["LOL_KDE_HOME"] = "/definitely/not/here"
        try:
            self.assertEqual(journal.entries(), [])
        finally:
            if saved is None:
                del os.environ["LOL_KDE_HOME"]
            else:
                os.environ["LOL_KDE_HOME"] = saved


class TestKvantumOpaqueList(unittest.TestCase):
    """Themes exclude specific executables from translucency by name.

    Layan lists 19 -- vlc, VirtualBox, kdenlive and friends. Those apps
    ignore reduce_window_opacity entirely, which looks like a broken theme
    if you do not know the list exists.
    """

    def _cfg(self, body: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "T.kvconfig"
        path.write_text(body)
        return path

    def test_parses_and_strips_the_comma_list(self):
        cfg = self._cfg("[%General]\nopaque=vlc, VirtualBox ,kdenlive\n")
        self.assertEqual(resolve.kvantum_opaque_apps(cfg),
                         ["vlc", "VirtualBox", "kdenlive"])

    def test_absent_key_is_an_empty_list_not_an_error(self):
        cfg = self._cfg("[%General]\ntranslucent_windows=true\n")
        self.assertEqual(resolve.kvantum_opaque_apps(cfg), [])

    def test_empty_value_yields_no_phantom_entries(self):
        cfg = self._cfg("[%General]\nopaque=\n")
        self.assertEqual(resolve.kvantum_opaque_apps(cfg), [])


class TestAuroraeScaledVariants(unittest.TestCase):
    """_x1.25 and _x1.5 variants that scaled the art but not the layout."""

    def _theme(self, name: str, rc_body: str, art: str = "<svg/>") -> Path:
        root = Path(self.tmp.name) / name
        root.mkdir()
        (root / f"{name}rc").write_text(rc_body)
        (root / "decoration.svg").write_text(art)
        return root

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_identical_layout_metrics_are_flagged(self):
        rc = "[Layout]\nTitleHeight=16\nPaddingTop=36\n"
        self._theme("WhiteSur-dark", rc)
        scaled = self._theme("WhiteSur-dark_x1.25", rc)
        detail = resolve.aurorae_scale_mismatch(scaled)
        self.assertIn("1.25", detail)
        self.assertIn("WhiteSur-dark", detail)

    def test_properly_scaled_variant_is_not_flagged(self):
        self._theme("Foo", "[Layout]\nTitleHeight=16\nPaddingTop=36\n")
        scaled = self._theme("Foo_x1.5", "[Layout]\nTitleHeight=24\nPaddingTop=54\n")
        self.assertEqual(resolve.aurorae_scale_mismatch(scaled), "")

    def test_variant_with_no_sibling_is_not_flagged(self):
        lonely = self._theme("Orphan_x1.25", "[Layout]\nTitleHeight=16\n")
        self.assertEqual(resolve.aurorae_scale_mismatch(lonely), "")

    def test_ordinary_theme_name_is_not_flagged(self):
        plain = self._theme("Layan", "[Layout]\nTitleHeight=15\n")
        self.assertEqual(resolve.aurorae_scale_mismatch(plain), "")


def _any_installed_aurorae_theme() -> str | None:
    for base in paths.data_dirs():
        themes = base / "aurorae/themes"
        if not themes.is_dir():
            continue
        for entry in sorted(themes.iterdir()):
            if (entry / "decoration.svg").is_file():
                return resolve.AURORAE_PREFIX + entry.name
    return None


class TestKvantumMatching(unittest.TestCase):
    """Kvantum keeps its own theme selection in its own config file.

    It can be pointed at a completely different theme than the one you
    applied, and every other component still reports ok while your window
    interiors render someone else's design. That happened, and nothing caught it.
    """

    def test_foreign_kvantum_theme_is_degraded_not_ok(self):
        result = resolve.widget_style("kvantum-dark", "Definitely-Not-The-Installed-Theme")
        if result.status == resolve.MISSING:
            self.skipTest("kvantum not installed on this machine")
        self.assertEqual(result.status, resolve.DEGRADED)
        self.assertIn("not part of", result.detail)

    def test_no_expectation_means_no_mismatch_check(self):
        result = resolve.widget_style("kvantum-dark")
        if result.status == resolve.MISSING:
            self.skipTest("kvantum not installed")
        self.assertNotIn("not part of", result.detail)

    def test_reports_the_opacity_knob_not_just_the_boolean(self):
        result = resolve.widget_style("kvantum-dark")
        if result.status != resolve.OK or "Kvantum theme" not in result.detail:
            self.skipTest("kvantum not configured on this machine")
        # translucent_windows=true with reduce_window_opacity=0 renders fully
        # opaque. Reporting only the boolean is how an entire evening was lost.
        self.assertIn("reduce_window_opacity", result.detail)

    def test_non_engine_styles_ignore_the_expectation(self):
        self.assertEqual(resolve.widget_style("fusion", "Anything").status, resolve.OK)


class TestAudit(unittest.TestCase):
    def test_declared_but_unset_is_reported(self):
        rows = resolve.audit(
            declared={("plasmarc", "Theme"): {"name": "Sweet-Ambar-Blue"}},
            live={},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, resolve.UNSET)
        self.assertIn("nothing set it", rows[0].note)

    def test_drift_is_reported_even_when_live_value_resolves(self):
        rows = resolve.audit(
            declared={("kdeglobals", "KDE"): {"widgetStyle": "kvantum"}},
            live={("kdeglobals", "KDE"): {"widgetStyle": "fusion"}},
        )
        self.assertEqual(rows[0].status, resolve.OK)
        self.assertIn("kvantum", rows[0].note)

    def test_agreement_produces_no_note(self):
        rows = resolve.audit(
            declared={("kdeglobals", "KDE"): {"widgetStyle": "fusion"}},
            live={("kdeglobals", "KDE"): {"widgetStyle": "fusion"}},
        )
        self.assertEqual(rows[0].note, "")

    def test_decoration_falls_back_to_kdecoration2_group(self):
        rows = resolve.audit(
            declared={("kwinrc", "org.kde.kdecoration2"):
                      {"theme": "__aurorae__svg__Sweet-ambar-blue"}},
            live={},
        )
        self.assertEqual(rows[0].label, "Window decoration")
        self.assertEqual(rows[0].declared, "Sweet-ambar-blue")


class TestKnsrc(unittest.TestCase):
    def test_known_categories_have_targets(self):
        for name in ("aurorae", "colorschemes", "icons", "xcursor", "wallpaper"):
            spec = knsrc.load(name)
            if not spec.found:
                self.skipTest(f"{name}.knsrc not present on this system")
            self.assertIsNotNone(spec.target, name)

    def test_kpackage_categories_need_no_target(self):
        spec = knsrc.load("plasma-themes")
        if not spec.found:
            self.skipTest("plasma-themes.knsrc not present")
        self.assertTrue(spec.uses_kpackage)
        self.assertEqual(spec.kpackage_type, "Plasma/Theme")

    def test_unknown_category_is_not_found(self):
        self.assertFalse(knsrc.load("no-such-category-xyz").found)


class TestPointerEquivalence(unittest.TestCase):
    """Colour schemes are named two ways; that is not drift."""

    def test_colour_scheme_punctuation_is_not_drift(self):
        rows = resolve.audit(
            declared={("kdeglobals", "General"): {"ColorScheme": "Sweet-Ambar-Blue"}},
            live={("kdeglobals", "General"): {"ColorScheme": "SweetAmbarBlue"}},
        )
        self.assertEqual(rows[0].note, "")

    def test_genuinely_different_scheme_is_still_drift(self):
        rows = resolve.audit(
            declared={("kdeglobals", "General"): {"ColorScheme": "Sweet-Ambar-Blue"}},
            live={("kdeglobals", "General"): {"ColorScheme": "Stone"}},
        )
        self.assertIn("Sweet-Ambar-Blue", rows[0].note)

    def test_normalisation_does_not_leak_to_other_kinds(self):
        rows = resolve.audit(
            declared={("kdeglobals", "Icons"): {"Theme": "candy-icons"}},
            live={("kdeglobals", "Icons"): {"Theme": "candyicons"}},
        )
        self.assertIn("candy-icons", rows[0].note)


class TestBanner(unittest.TestCase):
    def test_narrow_terminal_falls_back_to_one_line(self):
        out = banner.render(width_available=20, color=False)
        self.assertIn(banner.PLAIN, out)
        self.assertNotIn("\u250c", out)
        self.assertEqual(len(out.splitlines()), 1)

    def test_wide_terminal_gets_the_notice(self):
        self.assertIn("\u250c", banner.render(width_available=200, color=False))

    def test_box_is_rectangular(self):
        lines = banner.render(width_available=200, color=False).splitlines()
        self.assertEqual(len({len(line) for line in lines}), 1)
        self.assertTrue(all(line[0] in "\u250c\u2502\u2514" for line in lines))
        self.assertTrue(all(line[-1] in "\u2510\u2502\u2518" for line in lines))

    def test_declared_width_matches_reality(self):
        lines = banner.render(width_available=200, color=False).splitlines()
        self.assertEqual(banner.width(), len(lines[0]))

    def test_no_colour_means_no_escapes(self):
        self.assertNotIn("\033", banner.render(width_available=200, color=False))

    def test_notice_count_matches_what_the_tool_actually_checks(self):
        # The banner once said "six things" while apply verified seven.
        # The number is now computed, and this asserts it stays honest.
        spelled = banner.NUMERALS[resolve.pointer_kinds()]
        self.assertIn(spelled, " ".join(banner.notice()))
        self.assertIn(spelled, banner.subtitle())

    def test_pointer_count_equals_the_audit_row_count(self):
        declared = {("kdeglobals", "KDE"): {"widgetStyle": "fusion"},
                    ("kdeglobals", "General"): {"ColorScheme": "X"},
                    ("kdeglobals", "Icons"): {"Theme": "X"},
                    ("kcminputrc", "Mouse"): {"cursorTheme": "X"},
                    ("plasmarc", "Theme"): {"name": "X"},
                    ("ksplashrc", "KSplash"): {"Theme": "X"},
                    ("kwinrc", "org.kde.kdecoration2"): {"theme": "X"}}
        self.assertEqual(len(resolve.audit(declared, {})), resolve.pointer_kinds())

    def test_every_remark_branch_is_reachable_and_singular_safe(self):
        cases = [(6, 0, 0, 0), (3, 0, 3, 0), (5, 1, 0, 0),
                 (2, 0, 0, 2), (0, 0, 0, 0), (5, 0, 1, 0), (5, 0, 0, 1)]
        seen = {banner.closing_remark(*c) for c in cases}
        # All seven differ: the branches are distinct, and singular/plural
        # wording makes 1-unset and 3-unset different lines too.
        self.assertEqual(len(seen), len(cases))
        for c in cases:
            line = banner.closing_remark(*c)
            self.assertTrue(line.endswith("."))
            self.assertNotIn("!", line)               # the joke is politeness


class TestConfigCascade(unittest.TestCase):
    """A global theme's settings live in ~/.config/kdedefaults, not ~/.config."""

    def test_layers_are_lowest_priority_first(self):
        layers = paths.config_layers()
        self.assertEqual(layers[-1], paths.config_home())
        self.assertIn(paths.config_home() / "kdedefaults", layers)
        self.assertLess(layers.index(paths.config_home() / "kdedefaults"),
                        layers.index(paths.config_home()))

    def test_kdedefaults_included_even_if_absent_from_env(self):
        import os
        old = os.environ.get("XDG_CONFIG_DIRS")
        os.environ["XDG_CONFIG_DIRS"] = "/etc/xdg"
        try:
            self.assertIn(paths.config_home() / "kdedefaults", paths.config_layers())
        finally:
            if old is None:
                del os.environ["XDG_CONFIG_DIRS"]
            else:
                os.environ["XDG_CONFIG_DIRS"] = old


class TestThemeLookup(unittest.TestCase):
    def test_lookup_ignores_case_and_punctuation(self):
        names = [n for n, _ in manifest.list_installed()]
        if not names:
            self.skipTest("no global themes installed")
        target = names[0]
        for variant in (target.lower(), target.upper(), target.replace("-", "")):
            self.assertIsNotNone(manifest.find(variant), variant)

    def test_unknown_theme_still_returns_none(self):
        self.assertIsNone(manifest.find("definitely-not-a-theme-xyz"))

    def test_loaded_name_is_the_on_disk_name_not_the_typed_one(self):
        names = [n for n, _ in manifest.list_installed()]
        if not names:
            self.skipTest("no global themes installed")
        target = next((n for n in names if n.lower() != n), None)
        if target is None:
            self.skipTest("no mixed-case theme installed to test against")
        # plasma-apply-lookandfeel is case-sensitive; we must hand it the
        # real directory name even when the user typed something else.
        self.assertEqual(manifest.load(target.lower()).name, target)


class TestLegacy(unittest.TestCase):
    """The remover must never delete something still needed."""

    def _pkg(self, **kw):
        defaults = dict(kind="Plasma style", name="X",
                        path=paths.data_home() / "plasma/desktoptheme/X")
        return legacy.LegacyPackage(**{**defaults, **kw})

    def test_active_package_is_not_removable(self):
        self.assertFalse(self._pkg(active=True).removable)

    def test_referenced_package_is_not_removable(self):
        self.assertFalse(self._pkg(referenced_by=("Sweet",)).removable)

    def test_system_package_is_not_removable(self):
        self.assertFalse(self._pkg(path=Path("/usr/share/plasma/desktoptheme/X")).removable)

    def test_orphan_user_package_is_removable(self):
        self.assertTrue(self._pkg().removable)

    def test_aurorae_is_not_treated_as_legacy(self):
        # metadata.desktop is Aurorae's normal format, not a legacy marker.
        self.assertNotIn("aurorae/themes", legacy.PACKAGE_KINDS)
        self.assertNotIn("aurorae", " ".join(legacy.PACKAGE_KINDS))

    def test_scan_marks_the_live_plasma_style_as_active(self):
        style = kconfig.read_cascade("plasmarc").get(("plasmarc", "Theme"), {}).get("name")
        if not style:
            self.skipTest("no Plasma style configured")
        for pkg in legacy.scan():
            if pkg.name == style:
                self.assertTrue(pkg.active)
                self.assertFalse(pkg.removable)


class TestStorePages(unittest.TestCase):
    def test_parses_every_federated_front_end(self):
        for url in ("https://www.opendesktop.org/p/1325243",
                    "https://www.pling.com/p/1325243/",
                    "https://store.kde.org/p/1325243",
                    "http://kde-look.org/p/1325243", "1325243"):
            self.assertEqual(catalog.parse_url(url), "1325243", url)

    def test_rejects_non_store_urls(self):
        self.assertIsNone(catalog.parse_url("https://example.com/p/notanid"))
        self.assertIsNone(catalog.parse_url("nonsense"))

    def test_extracts_dependency_links_in_order_without_duplicates(self):
        description = ("kvantum: https://www.pling.com/p/1325246/ "
                       "gtk: https://www.pling.com/p/1309214/ "
                       "again: https://www.pling.com/p/1325246/")
        self.assertEqual(catalog.dependency_ids(description), ["1325246", "1309214"])

    def test_handles_html_escaped_descriptions(self):
        self.assertEqual(
            catalog.dependency_ids("see &lt;a&gt;https://www.pling.com/p/999/&lt;/a&gt;"),
            ["999"])

    def test_routes_by_xdg_type_then_type_id_then_keyword(self):
        def item(**kw):
            base = dict(content_id="1", name="n", typename="", xdg_type="",
                        author="", downloads="", type_id="")
            return store.StoreItem(**{**base, **kw})
        self.assertEqual(catalog.route_for(item(xdg_type="icons")).category, "icons")
        # Plasma 6 categories ship no xdg_type; fall back to the numeric id.
        self.assertEqual(catalog.route_for(item(type_id="722")).category, "lookandfeel")
        self.assertEqual(catalog.route_for(item(typename="Kvantum")).category, "kvantum")
        self.assertFalse(catalog.route_for(item(typename="Mystery Meat")).known)


class TestMultiPackageArchives(unittest.TestCase):
    """One archive is not always one package."""

    def _tree(self, tmp, dirs, files=()):
        root = Path(tmp) / "unpacked"
        root.mkdir()
        for d in dirs:
            (root / d).mkdir()
        for f in files:
            (root / f).write_text("x")
        return root

    def test_single_directory_is_unwrapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, ["Layan"])
            self.assertEqual([p.name for p in install._payloads(root)], ["Layan"])

    def test_sibling_directories_each_install_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, ["Tela", "Tela-dark", "Tela-light"])
            self.assertEqual(sorted(p.name for p in install._payloads(root)),
                             ["Tela", "Tela-dark", "Tela-light"])

    def test_top_level_files_mean_one_package_with_subdirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, ["contents"], ["metadata.json"])
            self.assertIsNone(install._payloads(root))


class TestDownloadVariants(unittest.TestCase):
    FILES = [(1, "01-Layan-border-cursors.tar.xz"),
             (2, "02-Layan-cursors.tar.xz"),
             (3, "03-Layan-white-cursors.tar.xz")]

    def test_picks_the_named_variant(self):
        self.assertEqual(store.best_match(self.FILES, "Layan-white-cursors"), 3)

    def test_punctuation_insensitive(self):
        self.assertEqual(store.best_match(self.FILES, "layan_white_cursors"), 3)

    def test_falls_back_to_first_when_nothing_matches(self):
        self.assertEqual(store.best_match(self.FILES, "Nonexistent"), 1)

    def test_no_preference_takes_the_first(self):
        self.assertEqual(store.best_match(self.FILES), 1)

    def test_empty_list_is_safe(self):
        self.assertEqual(store.best_match([], "anything"), 1)


if __name__ == "__main__":
    unittest.main()
