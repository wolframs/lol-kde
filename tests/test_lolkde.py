"""Tests for the parts that are easy to get subtly wrong.

Run with: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys
import http.client
import tarfile
import urllib.error
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lolkde import (banner, catalog, cli, compare, install, journal,  # noqa: E402
                    kconfig, knsrc, legacy, manifest, paths, repair,
                    resolve, restore, snapshot, store)


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

    def test_widget_style_case_is_not_drift(self):
        # Gently-Dark-Global-6 declares `widgetStyle=breeze`, and KDE's own
        # plasma-apply-lookandfeel writes `Breeze`. Seen live 2026-08-02:
        # a theme reported drift immediately after applying it cleanly.
        self.assertTrue(resolve._same_pointer("widget-style", "breeze", "Breeze"))
        self.assertTrue(resolve._same_pointer("widget-style", "kvantum", "Kvantum"))
        # Different styles are still different.
        self.assertFalse(resolve._same_pointer("widget-style", "breeze", "fusion"))

    def test_normalisation_does_not_leak_to_other_kinds(self):
        rows = resolve.audit(
            declared={("kdeglobals", "Icons"): {"Theme": "candy-icons"}},
            live={("kdeglobals", "Icons"): {"Theme": "candyicons"}},
        )
        self.assertIn("candy-icons", rows[0].note)


class TestNoLiveBusEmission(unittest.TestCase):
    """Nothing in this repo may hand-emit an internal KDE D-Bus signal.

    On 2026-08-02 a generic emitter sent KConfig's change-notification
    signal with the wrong nested type; every KDE client on the session bus
    allocated several GiB and the kernel killed the compositor. The full
    account is in docs/incident-2026-08-02-kconfig-oom.md.

    (This docstring deliberately avoids naming the tool and the interface
    together in one breath -- the check below scans this file too, and a
    guard with an exemption for itself is not a guard.)

    This test is the guard, because the mistake is one somebody re-derives
    rather than copies: you watch the real signal go past, notice the tool
    that could replay it, and the wrongness is invisible until it is fatal.
    """

    # Assembled from pieces so that this file, which must talk about the
    # forbidden thing in order to forbid it, does not itself match.
    EMITTERS = ("gdbus" + " emit", "dbus" + "-send")
    KDE_INTERFACE = "org." + "kde."

    # The rule is against *recipes*, not against the words. Prose that names
    # the emitter in order to ban it is exactly what this repo should contain,
    # so only executable context counts: shell fences in Markdown, and every
    # line of an actual program.
    SHELL_FENCES = ("```sh", "```bash", "```shell", "```console")

    def _shell_blocks(self, text):
        inside = False
        for line in text.splitlines():
            if line.startswith("```"):
                inside = line.strip() in self.SHELL_FENCES
                continue
            if inside:
                yield line

    def _executable_lines(self):
        root = Path(__file__).resolve().parent.parent
        for pattern in ("lolkde/*.py", "tests/*.py", "bin/*", "docs/*.md",
                        "*.md"):
            for path in sorted(root.glob(pattern)):
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                text = path.read_text(encoding="utf-8", errors="replace")
                lines = (self._shell_blocks(text) if rel.endswith(".md")
                         else text.splitlines())
                # A shell command can be continued across lines, so a window
                # of a few lines is the unit, not a single line.
                window = []
                for line in lines:
                    window.append(line)
                    if len(window) > 4:
                        window.pop(0)
                    yield rel, "\n".join(window)

    def _is_offender(self, chunk):
        return (any(e in chunk for e in self.EMITTERS)
                and self.KDE_INTERFACE in chunk)

    def test_no_generic_emitter_targets_a_kde_interface(self):
        offenders = sorted({rel for rel, chunk in self._executable_lines()
                            if self._is_offender(chunk)})
        self.assertEqual(offenders, [], "\n".join([
            "A generic D-Bus emitter is aimed at a KDE interface inside an",
            "executable block. This is the combination that destroyed a live",
            "session on 2026-08-02. Use kwriteconfig6 --notify, or a helper",
            "linked against KConfig, or an isolated bus -- see",
            "docs/dbus-harness.md and docs/incident-2026-08-02-kconfig-oom.md.",
            *offenders,
        ]))

    def test_the_guard_would_actually_catch_it(self):
        # A guard nobody has seen fail is a guard nobody knows works. This is
        # the incident command, reassembled at runtime so it exists nowhere
        # in the repo as text.
        fatal = (self.EMITTERS[0] + " --session --object-path /kdeglobals "
                 "--signal " + self.KDE_INTERFACE + "kconfig.notify.ConfigChanged")
        self.assertTrue(self._is_offender(fatal))
        # And that it does not fire on the supported route.
        self.assertFalse(self._is_offender(
            "kwriteconfig6 --file kwinrc --group G --key K V --notify"))
        # Nor on prose that names the emitter without invoking it.
        self.assertFalse(self._is_offender("Never use " + self.EMITTERS[0]))

    def test_the_postmortem_is_still_present_and_still_fenced_as_text(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "docs/incident-2026-08-02-kconfig-oom.md").read_text()
        self.assertIn("DO NOT RUN", text)
        # If someone "helpfully" re-fences the evidence as shell, the rule
        # has quietly become a snippet again.
        self.assertNotIn("```sh\ngdbus emit", text)


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


class TestDeleteMarkers(unittest.TestCase):
    """`Key[$d]` is a tombstone that blocks inheritance, not an absent key.

    Measured 2026-08-02 (open question C): `kwriteconfig6 --delete` never
    removes a line, it writes one. Read naively the tombstone parses as a key
    called "Theme[$d]", the inherited value underneath looks untouched, and
    the tool cheerfully reports a value KDE itself no longer resolves.
    """

    def test_flags_are_split_off_but_locale_suffixes_are_not(self):
        self.assertEqual(kconfig.split_flags("Theme[$d]"), ("Theme", "d"))
        self.assertEqual(kconfig.split_flags("Theme[$di]"), ("Theme", "di"))
        self.assertEqual(kconfig.split_flags("Theme"), ("Theme", ""))
        # A locale variant carries no `$` and must survive intact.
        self.assertEqual(kconfig.split_flags("Name[de_DE]"), ("Name[de_DE]", ""))

    def _layered(self, tmp, lower_text, upper_text):
        lower, upper = Path(tmp) / "kdedefaults", Path(tmp)
        lower.mkdir()
        (lower / "probe").write_text(lower_text)
        (upper / "probe").write_text(upper_text)
        old = os.environ.get("XDG_CONFIG_HOME"), os.environ.get("XDG_CONFIG_DIRS")
        os.environ["XDG_CONFIG_HOME"] = str(upper)
        os.environ["XDG_CONFIG_DIRS"] = str(lower)
        return old

    def _unset(self, old):
        for name, value in zip(("XDG_CONFIG_HOME", "XDG_CONFIG_DIRS"), old):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_a_tombstone_above_removes_the_value_below(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = self._layered(tmp, "[Icons]\nTheme=Tela\n",
                                "[Icons]\nTheme[$d]\n")
            try:
                merged = kconfig.read_cascade("probe")
                self.assertNotIn("Theme", merged[("probe", "Icons")])
                # And it must not leak through under its decorated name.
                self.assertNotIn("Theme[$d]", merged[("probe", "Icons")])
                self.assertIsNone(kconfig.origin("probe", "Icons", "Theme"))
                self.assertTrue(kconfig.tombstoned(
                    Path(tmp) / "probe", "Icons", "Theme"))
            finally:
                self._unset(old)

    def test_without_the_tombstone_the_lower_layer_still_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = self._layered(tmp, "[Icons]\nTheme=Tela\n", "[Icons]\n")
            try:
                merged = kconfig.read_cascade("probe")
                self.assertEqual(merged[("probe", "Icons")]["Theme"], "Tela")
                self.assertIsNotNone(kconfig.origin("probe", "Icons", "Theme"))
            finally:
                self._unset(old)

    def test_get_treats_a_tombstoned_key_as_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "probe"
            path.write_text("[Icons]\nTheme[$d]\n")
            self.assertIsNone(kconfig.get(path, "Icons", "Theme"))
            self.assertTrue(kconfig.tombstoned(path, "Icons", "Theme"))


class TestUnpin(unittest.TestCase):
    """Exposing an inherited value again -- the mechanism test C forced.

    All of this runs against a temporary config tree. Nothing here may touch
    the real session: the raw edit is safe, but the supported writer it calls
    first is a live one.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.user = root / "config"
        self.lower = root / "config" / "kdedefaults"
        self.lower.mkdir(parents=True)
        self.old = (os.environ.get("XDG_CONFIG_HOME"),
                    os.environ.get("XDG_CONFIG_DIRS"))
        os.environ["XDG_CONFIG_HOME"] = str(self.user)
        os.environ["XDG_CONFIG_DIRS"] = str(self.lower)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for name, value in zip(("XDG_CONFIG_HOME", "XDG_CONFIG_DIRS"), self.old):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _write(self, upper, lower="[Icons]\nTheme=Tela\n"):
        (self.lower / "probe").write_text(lower)
        (self.user / "probe").write_text(upper)

    def test_removing_a_pin_exposes_the_inherited_value(self):
        self._write("[Icons]\nTheme=Breeze\nOther=keep\n")
        result = repair.unpin("probe", "Icons", "Theme", notify=False)
        self.assertEqual(result.outcome, repair.UNPINNED)
        self.assertEqual(result.resolved, "Tela")
        self.assertFalse(result.pinned)
        # The neighbouring key in the same group is untouched.
        self.assertEqual(
            kconfig.get(self.user / "probe", "Icons", "Other"), "keep")

    def test_a_tombstone_is_removed_not_added_to(self):
        self._write("[Icons]\nTheme[$d]\n")
        self.assertIsNone(kconfig.read_cascade("probe")[("probe", "Icons")]
                          .get("Theme"))
        result = repair.unpin("probe", "Icons", "Theme", notify=False)
        self.assertEqual(result.outcome, repair.UNPINNED)
        self.assertEqual(result.resolved, "Tela")
        self.assertNotIn("[$d]", (self.user / "probe").read_text())

    def test_the_empty_group_header_is_left_behind_on_purpose(self):
        # Removing the last key leaves `[Icons]` sitting there. It resolves
        # identically to an absent group, and deleting a group is a bigger,
        # less reversible operation than deleting a key -- restore's unit is
        # the key. Pinned as intended behaviour so nobody "tidies" it later.
        self._write("[Icons]\nTheme=Breeze\n")
        repair.unpin("probe", "Icons", "Theme", notify=False)
        text = (self.user / "probe").read_text()
        self.assertIn("[Icons]", text)
        self.assertNotIn("Theme", text)

    def test_nothing_underneath_is_reported_as_stale_not_success(self):
        self._write("[Icons]\nTheme=Breeze\n", lower="[Icons]\n")
        result = repair.unpin("probe", "Icons", "Theme", notify=False)
        # On disk it is right, but no supported writer can announce it, and
        # inventing one is the forbidden thing. Say so instead of claiming
        # the desktop changed.
        self.assertEqual(result.outcome, repair.STALE)
        self.assertIn("restart", result.detail)

    def test_an_absent_key_is_a_no_op(self):
        self._write("[Icons]\n")
        self.assertEqual(repair.unpin("probe", "Icons", "Theme", notify=False).outcome,
                         repair.UNCHANGED)

    def test_only_the_named_group_is_touched(self):
        self._write("[Icons]\nTheme=Breeze\n\n[Other]\nTheme=Breeze\n")
        repair.unpin("probe", "Icons", "Theme", notify=False)
        text = (self.user / "probe").read_text()
        self.assertIn("[Other]\nTheme=Breeze", text)

    def test_bracketed_group_names_still_match_exactly(self):
        # KWin writes groups like [Tiling][uuid][uuid]; configparser reports
        # that section as `Tiling][uuid][uuid`, and the raw editor has to
        # rebuild the header identically or it edits the wrong group.
        self._write("[Tiling][a][b]\nTheme=Breeze\n\n[Icons]\nTheme=Breeze\n")
        repair.unpin("probe", "Tiling][a][b", "Theme", notify=False)
        text = (self.user / "probe").read_text()
        self.assertNotIn("[Tiling][a][b]\nTheme", text)
        self.assertIn("[Icons]\nTheme=Breeze", text)

    def test_a_symlinked_config_is_refused(self):
        self._write("[Icons]\nTheme=Breeze\n")
        real = Path(self.tmp.name) / "elsewhere"
        real.write_text("[Icons]\nTheme=Breeze\n")
        (self.user / "probe").unlink()
        (self.user / "probe").symlink_to(real)
        result = repair.unpin("probe", "Icons", "Theme", notify=False)
        self.assertEqual(result.outcome, repair.FAILED)
        self.assertIn("symlink", result.detail)
        # And the symlink target is untouched.
        self.assertIn("Theme=Breeze", real.read_text())

    def test_the_file_is_replaced_atomically_leaving_no_debris(self):
        self._write("[Icons]\nTheme=Breeze\n")
        repair.unpin("probe", "Icons", "Theme", notify=False)
        leftovers = [p.name for p in self.user.iterdir() if "lolkde" in p.name]
        self.assertEqual(leftovers, [])

    def test_inherited_value_ignores_the_user_layer(self):
        self._write("[Icons]\nTheme=Breeze\n")
        self.assertEqual(repair.inherited_value("probe", "Icons", "Theme"),
                         "Tela")


class _RestoreFixture:
    """A fake snapshot and a fake live tree, both under a temporary HOME.

    A mixin rather than a base TestCase, so that the end-to-end class below
    does not silently re-run every plan test a second time.
    """

    KEY = ("kdeglobals", "Icons", "Theme")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.live = root / "live"
        (self.live / "kdedefaults").mkdir(parents=True)
        self.snap = root / "snap"
        (self.snap / "files").mkdir(parents=True)
        self.old = (os.environ.get("XDG_CONFIG_HOME"),
                    os.environ.get("XDG_CONFIG_DIRS"))
        os.environ["XDG_CONFIG_HOME"] = str(self.live)
        os.environ["XDG_CONFIG_DIRS"] = "/nonexistent-for-tests"
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for name, value in zip(("XDG_CONFIG_HOME", "XDG_CONFIG_DIRS"), self.old):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _make(self, snap_user, snap_defaults, live_user, live_defaults):
        """Write both worlds: user and kdedefaults layers, snapshot and live."""
        manifest_rows = []
        for relative, text in (("config/kdeglobals", snap_user),
                               ("config/kdedefaults/kdeglobals", snap_defaults)):
            if text is None:
                continue
            path = self.snap / "files" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            manifest_rows.append({
                "path": relative, "status": "captured",
                "source": str(self.live / relative[len("config/"):]),
                "sha256": hashlib.sha256(text.encode()).hexdigest()})
        (self.snap / "manifest.json").write_text(json.dumps(manifest_rows))
        (self.snap / "meta.json").write_text(json.dumps(
            {"id": "test-snapshot", "schema": 1}))
        (self.snap / "state").mkdir(exist_ok=True)
        (self.snap / "state" / "env.json").write_text(json.dumps(
            {"XDG_CONFIG_DIRS": os.environ["XDG_CONFIG_DIRS"]}))

        if live_user is not None:
            (self.live / "kdeglobals").write_text(live_user)
        if live_defaults is not None:
            (self.live / "kdedefaults" / "kdeglobals").write_text(live_defaults)

    def _action(self):
        plan = restore.build(self.snap, ["icons"])
        steps = [s for s in plan.steps if (s.file, s.group, s.key) == self.KEY]
        self.assertEqual(len(steps), 1)
        return steps[0]


class TestRestorePlan(_RestoreFixture, unittest.TestCase):
    """The plan is where restore is right or wrong. It writes nothing.

    Each case builds a fake snapshot and a fake live tree, and asserts the
    *action* chosen. The one that matters is the tombstone case: a key the
    snapshot inherited but which the live user layer speaks about at all must
    be un-pinned, not deleted -- `--delete` would tombstone it and it would
    resolve to nothing (open question C).
    """

    def test_a_pin_that_matches_is_left_alone(self):
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n")
        self.assertEqual(self._action().action, restore.SAME)

    def test_a_pin_that_differs_is_written_back(self):
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        step = self._action()
        self.assertEqual(step.action, restore.SET)
        self.assertEqual(step.want, "Papirus")

    def test_inherited_in_the_snapshot_but_pinned_live_is_unpinned(self):
        # The case open question C rewrote. --delete here would write a
        # tombstone and the key would resolve to nothing at all.
        self._make("[Icons]\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        step = self._action()
        self.assertEqual(step.action, restore.UNPIN)
        self.assertEqual(step.want, "Tela")
        self.assertEqual(step.want_layer, restore.DEFAULTS)

    def test_inherited_on_both_sides_is_left_alone(self):
        self._make("[Icons]\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\n", "[Icons]\nTheme=Tela\n")
        self.assertEqual(self._action().action, restore.SAME)

    def test_a_live_tombstone_is_not_mistaken_for_an_inherited_value(self):
        # Live has Theme[$d]: the cascade resolves to nothing, even though
        # kdedefaults still says Tela. Restore must see a difference here.
        self._make("[Icons]\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme[$d]\n", "[Icons]\nTheme=Tela\n")
        step = self._action()
        self.assertEqual(step.have, None)
        self.assertEqual(step.action, restore.UNPIN)
        self.assertEqual(step.want, "Tela")

    def test_absent_everywhere_on_both_sides_is_not_a_write(self):
        self._make("[Icons]\n", "[Icons]\n", "[Icons]\n", "[Icons]\n")
        self.assertEqual(self._action().action, restore.SAME)

    def test_unselected_components_are_reported_as_drift_not_restored(self):
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        plan = restore.build(self.snap, ["cursor"])
        self.assertFalse([s for s in plan.steps
                          if (s.file, s.group, s.key) == self.KEY])
        self.assertTrue(any("Icons" in line for line in plan.drift))
        self.assertTrue(any("will survive this restore" in w
                            for w in plan.warnings))

    def test_a_corrupt_snapshot_blocks_everything(self):
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        (self.snap / "files" / "config" / "kdeglobals").write_text("tampered")
        plan = restore.build(self.snap, ["icons"])
        self.assertTrue(any("corrupt" in b for b in plan.blockers))

    def test_a_changed_cascade_shape_is_warned_about_loudly(self):
        self._make("[Icons]\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\n", "[Icons]\nTheme=Tela\n")
        (self.snap / "state" / "env.json").write_text(json.dumps(
            {"XDG_CONFIG_DIRS": "/somewhere/else"}))
        plan = restore.build(self.snap, ["icons"])
        self.assertTrue(any("XDG_CONFIG_DIRS changed" in w
                            for w in plan.warnings))

    def test_building_a_plan_writes_nothing(self):
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        before = (self.live / "kdeglobals").read_text()
        restore.build(self.snap, list(restore.components()))
        self.assertEqual((self.live / "kdeglobals").read_text(), before)


class TestRestoreEndToEnd(_RestoreFixture, unittest.TestCase):
    """Actually run the writes, against a temporary config tree.

    This exists because ROADMAP.md already carries a "built but not exercised
    end-to-end" section and restore is the last thing that should join it.
    Everything runs with notify=False, so nothing reaches the session bus.
    """

    def setUp(self):
        super().setUp()
        # Patch snapshot.store, not restore.store. Everything under
        # ~/.lol-kde hangs off it -- restores/ *and* journal.jsonl -- and
        # patching only the narrower one let these tests append four entries
        # to the real journal, where `lol-kde history` duly reported them as
        # things that had happened to this machine. They had not.
        fake = Path(self.tmp.name) / "lol-kde"
        real = snapshot.store
        snapshot.store = lambda: fake
        self.addCleanup(lambda: setattr(snapshot, "store", real))

    def _run(self, components=("icons",)):
        plan = restore.build(self.snap, list(components))
        self.assertFalse(plan.blockers, plan.blockers)
        return plan, restore.run(plan, pre_snapshot="fake-id", notify=False)

    def test_a_differing_pin_is_written_and_verifies(self):
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        plan, outcome = self._run()
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(restore.live_facts(*self.KEY).resolved, "Papirus")
        self.assertEqual(restore.live_facts(*self.KEY).layer, restore.USER)

    def test_unpinning_exposes_the_inherited_value_for_real(self):
        self._make("[Icons]\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        plan, outcome = self._run()
        self.assertEqual(outcome.exit_code, 0)
        facts = restore.live_facts(*self.KEY)
        self.assertEqual(facts.resolved, "Tela")
        self.assertEqual(facts.layer, restore.DEFAULTS)
        self.assertFalse(facts.user_entry)
        # And specifically NOT via a tombstone, which is what --delete would
        # have left behind and what would have resolved to nothing.
        self.assertNotIn("[$d]", (self.live / "kdeglobals").read_text())

    def test_a_live_tombstone_is_cleared_rather_than_added_to(self):
        self._make("[Icons]\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme[$d]\n", "[Icons]\nTheme=Tela\n")
        plan, outcome = self._run()
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(restore.live_facts(*self.KEY).resolved, "Tela")
        self.assertNotIn("[$d]", (self.live / "kdeglobals").read_text())

    def test_running_it_twice_changes_nothing_the_second_time(self):
        # Steps are desired end states, not deltas, precisely so that
        # re-running is the recovery mechanism (design section 6.2).
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        self._run()
        after_first = (self.live / "kdeglobals").read_text()
        plan, outcome = self._run()
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(plan.writes, [])
        self.assertEqual((self.live / "kdeglobals").read_text(), after_first)

    def test_the_original_file_is_quarantined_before_being_touched(self):
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        plan, outcome = self._run()
        kept = outcome.directory / "removed" / "kdeglobals"
        self.assertTrue(kept.is_file())
        self.assertIn("Theme=Breeze", kept.read_text())

    def test_nothing_is_written_to_the_real_lol_kde_directory(self):
        # The guard for the bug above: if snapshot.store() is ever consulted
        # unpatched, this test starts touching the user's real journal.
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        self._run()
        self.assertTrue(str(snapshot.store()).startswith(self.tmp.name))
        self.assertTrue(journal.path().is_file())
        self.assertTrue(str(journal.path()).startswith(self.tmp.name))

    def test_the_journal_records_each_step_before_and_after(self):
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        plan, outcome = self._run()
        events = [json.loads(line) for line in
                  (outcome.directory / "journal.jsonl").read_text().splitlines()]
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(kinds[-1], "end")
        self.assertIn("planned", kinds)
        self.assertIn("done", kinds)
        # The record has to say what the value was, or it cannot say where it
        # stopped and what to put back.
        planned = next(e for e in events if e["event"] == "planned")
        self.assertEqual(planned["was"], "Breeze")

    def test_a_changelog_row_is_emitted_for_pasting(self):
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")
        plan, outcome = self._run()
        rows = restore.changelog_row(outcome)
        self.assertTrue(rows)
        self.assertTrue(any("Breeze" in r and "Papirus" in r for r in rows))
        self.assertTrue(any("fake-id" in r for r in rows))

    def test_neighbouring_keys_in_the_same_group_survive(self):
        self._make("[Icons]\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\nSizes=32\n", "[Icons]\nTheme=Tela\n")
        self._run()
        self.assertEqual(
            kconfig.get(self.live / "kdeglobals", "Icons", "Sizes"), "32")


class TestRestoreComponents(unittest.TestCase):
    def test_components_are_derived_from_the_resolver_not_transcribed(self):
        bundles = restore.components()
        pointers = {k for keys in bundles.values() for k in keys}
        for pointer in resolve.SIMPLE_POINTERS:
            self.assertIn(pointer, pointers)

    def test_the_decoration_bundle_carries_bordersize(self):
        # library, theme and BorderSize share a group, and BorderSize in the
        # user layer is a deliberate choice against the theme's declared
        # value. Restoring the pair without it lets them drift.
        keys = restore.components()["decoration"]
        self.assertIn(("kwinrc", repair.DECO_GROUP, "BorderSize"), keys)
        self.assertEqual(len(keys), 3)

    def test_every_component_covers_at_least_one_key(self):
        for name, keys in restore.components().items():
            self.assertTrue(keys, name)


class TestRestoreLock(unittest.TestCase):
    def test_a_second_holder_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock"
            first, second = restore.Lock(path), restore.Lock(path)
            self.assertIsNone(first.acquire())
            self.assertIn("lock held", second.acquire() or "")
            first.release()
            self.assertIsNone(second.acquire())

    def test_a_stale_lock_names_the_flag_that_clears_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock"
            path.write_text("999999")          # a pid that cannot be running
            message = restore.Lock(path).acquire() or ""
            self.assertIn("--break-lock", message)
            self.assertIsNone(restore.Lock(path).acquire(break_stale=True))

    def test_release_does_not_remove_someone_elses_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock"
            path.write_text("999999")
            restore.Lock(path).release()
            self.assertTrue(path.exists())


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


class TestBareFileDownloads(unittest.TestCase):
    """Not every store download is an archive.

    Found live on 2026-08-02: installing Nostrum's dependencies, four of five
    succeeded and the colour scheme failed with "unrecognised archive format:
    Nostrum.colors" -- because the store serves that entry as a bare .colors
    file while the colorschemes knsrc expects a container.
    """

    class _Route:
        uses_kpackage = False
        kpackage_type = ""
        needs_root = False
        known = True
        uncompress = "always"

        def __init__(self, target):
            self.target = target

    def test_a_bare_file_is_recognised_as_not_an_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "Nostrum.colors"
            plain.write_text("[ColorEffects:Disabled]\nColorAmount=0\n")
            self.assertFalse(install.is_archive(plain))

    def test_a_bare_file_is_installed_as_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "Nostrum.colors"
            plain.write_text("[General]\nName=Nostrum\n")
            target = Path(tmp) / "color-schemes"
            status, detail, destination = install.place_archive(
                plain, self._Route(target), "Nostrum", force=False)
        self.assertEqual(status, "installed")
        self.assertEqual(destination.name, "Nostrum.colors")
        self.assertIn("not an archive", detail)

    def test_a_real_archive_still_takes_the_extract_path(self):
        import tarfile as _tar
        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "Theme"
            inner.mkdir()
            (inner / "index.theme").write_text("[Icon Theme]\nName=Theme\n")
            archive = Path(tmp) / "theme.tar"
            with _tar.open(archive, "w") as handle:
                handle.add(inner, arcname="Theme")
            self.assertTrue(install.is_archive(archive))
            target = Path(tmp) / "icons"
            status, _, destination = install.place_archive(
                archive, self._Route(target), "Theme", force=False)
        self.assertEqual(status, "installed")
        self.assertTrue(destination.name)

    def test_both_install_paths_place_files_through_the_same_function(self):
        # The bug was not the missing bare-file branch -- it was that there
        # were two copies of the placement logic and only one got fixed.
        # `install <theme>` (manifest-driven) and `please <url>` (store-page
        # driven) must share place_archive, or the next fix diverges too.
        source = inspect.getsource(install.install_dependency)
        self.assertIn("place_archive(", source)
        for duplicated in ("_safe_extract(", "_install_tree(", "shutil.copy2("):
            self.assertNotIn(duplicated, source,
                             f"install_dependency has its own copy of {duplicated}")

    def test_a_knsrc_spec_exposes_what_place_archive_needs(self):
        # The delegation above only holds if a knsrc spec is route-shaped.
        spec = knsrc.load("colorschemes")
        for attribute in ("target", "uncompress", "uses_kpackage",
                          "kpackage_type"):
            self.assertTrue(hasattr(spec, attribute), attribute)

    def test_an_existing_bare_file_is_skipped_not_clobbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "Nostrum.colors"
            plain.write_text("new")
            target = Path(tmp) / "color-schemes"
            target.mkdir()
            (target / "Nostrum.colors").write_text("existing")
            status, _, _ = install.place_archive(
                plain, self._Route(target), "Nostrum", force=False)
            self.assertEqual(status, "skipped")
            self.assertEqual((target / "Nostrum.colors").read_text(), "existing")


class TestUnsafeArchiveMembers(unittest.TestCase):
    """One bad symlink must not cost you the archive, or the whole run.

    Found live on 2026-08-02 installing Gently: the icon theme
    Noir-Gently-White-Blue-Dark-Icons ships one absolute symlink among
    thousands of files. `filter="data"` raised AbsoluteLinkError, which no
    handler caught, so `please` died with a traceback partway through a
    nineteen-component install.
    """

    def _tar_with_absolute_link(self, path: Path) -> None:
        import tarfile as _tar
        with _tar.open(path, "w") as handle:
            payload = _tar.TarInfo("Theme/index.theme")
            payload.size = 0
            handle.addfile(payload)
            link = _tar.TarInfo("Theme/apps/48/kmousetool.svg")
            link.type = _tar.SYMTYPE
            link.linkname = "/usr/share/icons/hicolor/48x48/apps/kmousetool.svg"
            handle.addfile(link)

    def test_the_unsafe_member_is_skipped_and_the_rest_extracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "icons.tar"
            self._tar_with_absolute_link(archive)
            into, skipped = install._safe_extract(archive, Path(tmp) / "out")
            self.assertEqual(skipped, ["Theme/apps/48/kmousetool.svg"])
            self.assertTrue((into / "Theme" / "index.theme").is_file())
            # The absolute link must not exist -- skipping it is the point.
            self.assertFalse((into / "Theme/apps/48/kmousetool.svg").exists())

    def test_the_skip_is_reported_not_swallowed(self):
        class _Route:
            uses_kpackage = False
            kpackage_type = ""
            uncompress = "always"

            def __init__(self, target):
                self.target = target

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "icons.tar"
            self._tar_with_absolute_link(archive)
            status, detail, _ = install.place_archive(
                archive, _Route(Path(tmp) / "icons"), "Theme", force=False)
        self.assertEqual(status, "installed")
        self.assertIn("1 unsafe entry skipped", detail)
        self.assertIn("kmousetool", detail)

    def test_a_traversal_entry_is_still_refused(self):
        # Relaxing the failure mode must not relax the policy.
        import tarfile as _tar
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "evil.tar"
            with _tar.open(archive, "w") as handle:
                escaping = _tar.TarInfo("../escaped.txt")
                escaping.size = 0
                handle.addfile(escaping)
            into, skipped = install._safe_extract(archive, Path(tmp) / "out")
            self.assertEqual(skipped, ["../escaped.txt"])
            # Assert inside the with-block: once the temp dir is gone, "the
            # escaped file does not exist" passes for the wrong reason.
            self.assertFalse((Path(tmp) / "escaped.txt").exists())
            self.assertEqual(list(into.rglob("*")), [])

    def test_archive_errors_are_caught_by_the_install_paths(self):
        # The traceback happened because AbsoluteLinkError is a TarError, and
        # the handlers only listed OSError/ValueError/RuntimeError.
        source = inspect.getsource(install)
        self.assertIn("tarfile.TarError", source)
        self.assertIn("zipfile.BadZipFile", source)
        self.assertTrue(issubclass(tarfile.AbsoluteLinkError, tarfile.TarError))


class TestStoreUrlEncoding(unittest.TestCase):
    """Uploaders' filenames end up verbatim in signed download URLs.

    Found live on 2026-08-02: Gently ships a wallpaper called
    `Gently-Nebula-Noir No Logo.jpg`, and the space in the signed URL made
    http.client raise InvalidURL. That is an HTTPException, not a URLError,
    so nothing caught it and a nineteen-component install died partway.
    """

    def test_a_space_in_the_filename_is_encoded(self):
        encoded = store.encode_url(
            "https://host/api/files/download/j/TOKEN/Gently-Nebula No Logo.jpg")
        self.assertNotIn(" ", encoded)
        self.assertIn("%20", encoded)

    def test_encoding_is_idempotent(self):
        once = store.encode_url("https://host/a%20b/c d.jpg")
        self.assertEqual(store.encode_url(once), once)

    def test_the_signing_token_survives_untouched(self):
        # JWT-ish tokens carry dots, dashes and underscores in the path.
        token = "eyJ0eXAiOiJKV1Qi.eyJpZCI6MTU4.dSmmTr9E-4Mt_AG6"
        url = f"https://host/api/files/download/j/{token}/name.jpg"
        self.assertIn(token, store.encode_url(url))

    def test_query_strings_keep_their_separators(self):
        url = "https://host/p?a=1&b=2#frag"
        self.assertEqual(store.encode_url(url), url)

    def test_invalid_url_is_an_httpexception_not_a_urlerror(self):
        # The reason the handler had to be widened.
        self.assertTrue(issubclass(http.client.InvalidURL,
                                   http.client.HTTPException))
        self.assertFalse(issubclass(http.client.InvalidURL,
                                    urllib.error.URLError))

    def test_the_store_layer_catches_it(self):
        source = inspect.getsource(store)
        self.assertIn("http.client.HTTPException", source)


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
