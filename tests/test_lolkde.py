"""Tests for the parts that are easy to get subtly wrong.

Run with: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import sys
import http.client
import tarfile
import urllib.error
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lolkde import (banner, catalog, cli, compare, install, journal,  # noqa: E402
                    kconfig, knsrc, legacy, manifest, paths, repair,
                    prune, resolve, restore, snapshot, store)


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


class TestPrune(unittest.TestCase):
    """The sharing rule is the whole feature.

    Layan and Stone both point at the Tela icon theme. Removing Stone must
    not take Tela with it, and that case is live on this machine -- which is
    why this is a graph problem and not a list of names.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.data = self.home / ".local/share"
        (self.data / "plasma/look-and-feel").mkdir(parents=True)
        (self.data / "plasma/desktoptheme").mkdir(parents=True)
        (self.data / "icons").mkdir(parents=True)
        self.old_home, self.old_data = paths.HOME, os.environ.get("XDG_DATA_HOME")
        paths.HOME = self.home
        os.environ["XDG_DATA_HOME"] = str(self.data)
        self.addCleanup(self._restore)

    def _restore(self):
        paths.HOME = self.old_home
        if self.old_data is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self.old_data

    def _theme(self, name, style, icons="", modern_style=True):
        d = self.data / "plasma/look-and-feel" / name / "contents"
        d.mkdir(parents=True)
        lines = [f"[plasmarc][Theme]\nname={style}\n"]
        if icons:
            lines.append(f"[kdeglobals][Icons]\nTheme={icons}\n")
        (d / "defaults").write_text("\n".join(lines))
        sd = self.data / "plasma/desktoptheme" / style
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "metadata.desktop").write_text("[Desktop Entry]\n")
        if modern_style:
            (sd / "metadata.json").write_text("{}")
        if icons:
            (self.data / "icons" / icons).mkdir(parents=True, exist_ok=True)
            (self.data / "icons" / icons / "index.theme").write_text("x")

    def _live(self, applied):
        return {("kdeglobals", "KDE"): {"LookAndFeelPackage": applied}}

    def test_a_legacy_style_marks_its_theme_as_previous_generation(self):
        self._theme("Old", "OldStyle", modern_style=False)
        self._theme("New", "NewStyle", modern_style=True)
        lnf = self.data / "plasma/look-and-feel"
        self.assertTrue(prune.is_previous_generation(lnf / "Old"))
        self.assertFalse(prune.is_previous_generation(lnf / "New"))

    def test_a_shared_component_is_protected_from_removal(self):
        self._theme("Old", "OldStyle", icons="Shared", modern_style=False)
        self._theme("New", "NewStyle", icons="Shared", modern_style=True)
        with unittest.mock.patch.object(resolve, "live_settings",
                                        return_value=self._live("New")):
            plan = prune.build()
        removed = {r.name for r in plan.remove}
        self.assertIn("Old", removed)
        self.assertNotIn("Shared", removed)
        self.assertTrue(plan.protected)

    def test_an_exclusive_component_is_removed(self):
        self._theme("Old", "OldStyle", icons="OnlyOld", modern_style=False)
        self._theme("New", "NewStyle", icons="OnlyNew", modern_style=True)
        with unittest.mock.patch.object(resolve, "live_settings",
                                        return_value=self._live("New")):
            plan = prune.build()
        removed = {r.name for r in plan.remove}
        self.assertIn("OnlyOld", removed)
        self.assertNotIn("OnlyNew", removed)

    def test_the_applied_theme_is_never_removed_even_if_legacy(self):
        self._theme("Old", "OldStyle", icons="OldIcons", modern_style=False)
        with unittest.mock.patch.object(resolve, "live_settings",
                                        return_value=self._live("Old")):
            plan = prune.build()
        self.assertEqual([r.name for r in plan.remove], [])
        self.assertIn("Old", plan.kept_themes)

    def test_a_live_component_is_protected_even_with_no_theme_claiming_it(self):
        self._theme("Old", "OldStyle", icons="LiveIcons", modern_style=False)
        self._theme("New", "NewStyle", modern_style=True)
        live = self._live("New")
        live[("kdeglobals", "Icons")] = {"Theme": "LiveIcons"}
        with unittest.mock.patch.object(resolve, "live_settings",
                                        return_value=live):
            plan = prune.build()
        self.assertNotIn("LiveIcons", {r.name for r in plan.remove})

    def test_check_refuses_paths_outside_the_users_theme_directories(self):
        plan = prune.Plan(remove=[prune.Removal("icons", "evil", Path("/usr/share/icons/breeze"), "x")])
        self.assertTrue(any("outside" in p for p in prune.check(plan)))

    def test_check_refuses_a_symlink(self):
        target = self.data / "icons" / "real"
        target.mkdir(parents=True)
        link = self.data / "icons" / "link"
        link.symlink_to(target)
        plan = prune.Plan(remove=[prune.Removal("icons", "link", link, "x")])
        self.assertTrue(any("symlink" in p for p in prune.check(plan)))

    def test_removals_are_moved_not_deleted(self):
        self._theme("Old", "OldStyle", icons="OldIcons", modern_style=False)
        self._theme("New", "NewStyle", modern_style=True)
        store = self.home / ".lol-kde"
        with unittest.mock.patch.object(resolve, "live_settings",
                                        return_value=self._live("New")), \
             unittest.mock.patch.object(snapshot, "store", return_value=store):
            plan = prune.build()
            quarantine, moved, failures = prune.run(plan)
        self.assertEqual(failures, [])
        self.assertTrue(moved)
        # Gone from where it was...
        self.assertFalse((self.data / "icons" / "OldIcons").exists())
        # ...and present in quarantine, path-preserved, with a way back.
        kept = quarantine / ".local/share/icons/OldIcons"
        self.assertTrue(kept.is_dir())
        self.assertTrue((quarantine / "manifest.json").is_file())
        self.assertIn("mv ", (quarantine / "RESTORE.md").read_text())

    def test_the_bulk_undo_snippet_actually_restores(self):
        # It shipped once as a shell loop whose body was `:`. This runs the
        # documented snippet for real and checks the files come back.
        self._theme("Old", "OldStyle", icons="OldIcons", modern_style=False)
        self._theme("New", "NewStyle", modern_style=True)
        store = self.home / ".lol-kde"
        with unittest.mock.patch.object(resolve, "live_settings",
                                        return_value=self._live("New")), \
             unittest.mock.patch.object(snapshot, "store", return_value=store):
            plan = prune.build()
            quarantine, moved, _ = prune.run(plan)

        note = (quarantine / "RESTORE.md").read_text()
        self.assertNotIn("  :  #", note)             # the old no-op body
        body = note.split("```sh", 1)[1].split("```", 1)[0]
        script = body.split("<<'PY'", 1)[1].rsplit("PY", 1)[0]
        import subprocess
        done = subprocess.run([sys.executable, "-c", script], cwd=quarantine,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((self.data / "icons" / "OldIcons").is_dir())
        self.assertTrue((self.data / "plasma/look-and-feel/Old").is_dir())
        # And it is safe to run twice.
        again = subprocess.run([sys.executable, "-c", script], cwd=quarantine,
                               capture_output=True, text=True)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("skipped", again.stdout)

    def test_drop_refuses_a_component_an_installed_theme_still_uses(self):
        # Real case: Beauty-Color-Global-6 uses Slot-Dark-Icons, so naming
        # the whole Slot family must drop four of five and refuse the fifth.
        self._theme("Keeper", "KeeperStyle", icons="Wanted", modern_style=True)
        (self.data / "icons" / "Unwanted").mkdir(parents=True)
        with unittest.mock.patch.object(resolve, "live_settings",
                                        return_value=self._live("Keeper")):
            plan, refusals = prune.build_drop(["Wanted", "Unwanted"])
        self.assertEqual([r.name for r in plan.remove], ["Unwanted"])
        self.assertTrue(any("Wanted" in r and "Keeper" in r for r in refusals))

    def test_drop_refuses_the_live_component(self):
        self._theme("Keeper", "KeeperStyle", modern_style=True)
        (self.data / "icons" / "LiveOnly").mkdir(parents=True)
        live = self._live("Keeper")
        live[("kdeglobals", "Icons")] = {"Theme": "LiveOnly"}
        with unittest.mock.patch.object(resolve, "live_settings",
                                        return_value=live):
            plan, refusals = prune.build_drop(["LiveOnly"])
        self.assertEqual(plan.remove, [])
        self.assertTrue(any("in use" in r for r in refusals))

    def test_drop_reports_a_name_that_does_not_exist(self):
        self._theme("Keeper", "KeeperStyle", modern_style=True)
        with unittest.mock.patch.object(resolve, "live_settings",
                                        return_value=self._live("Keeper")):
            plan, refusals = prune.build_drop(["NoSuchTheme"])
        self.assertEqual(plan.remove, [])
        self.assertTrue(any("not found" in r for r in refusals))

    def test_drop_is_the_only_way_to_remove_unreferenced_content(self):
        # build() must keep ignoring it -- "unreferenced" is not "unwanted",
        # which is why Tela-dark survives next to the Tela in use.
        self._theme("Keeper", "KeeperStyle", modern_style=True)
        (self.data / "icons" / "Loose").mkdir(parents=True)
        with unittest.mock.patch.object(resolve, "live_settings",
                                        return_value=self._live("Keeper")):
            swept = prune.build()
            named, _ = prune.build_drop(["Loose"])
        self.assertNotIn("Loose", {r.name for r in swept.remove})
        self.assertIn("Loose", {r.name for r in named.remove})

    def test_the_colour_scheme_filename_is_not_its_identifier(self):
        # Sweet-Ambar-Blue lives in SweetAmbarBlue.colors; matching on the
        # filename alone would miss it and leave the file behind.
        base = self.data / "color-schemes"
        base.mkdir(parents=True)
        (base / "SweetAmbarBlue.colors").write_text("[General]\nName=Sweet-Ambar-Blue\n")
        found = prune.locate("colour-scheme", "Sweet-Ambar-Blue")
        self.assertIn(base / "SweetAmbarBlue.colors", found)


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


class TestDryRunReadsTheManifest(unittest.TestCase):
    """A preview must not under-report what a real run installs.

    `please --dry-run` used to list only the pling links an author wrote into
    their description, because `X-KPackage-Dependencies` lives inside the
    package and nothing had unpacked it yet. Layan: plan said 4 components, a
    real run fetched 9. The fix downloads the package to a temporary directory
    purely to read its manifest.
    """

    DEPS = ["kns://xcursor.knsrc/api.kde-look.org/1393084",
            "kns://icons.knsrc/api.kde-look.org/1279924",
            "kns://plasma-themes.knsrc/api.kde-look.org/1325243"]

    def _package(self, tmp: Path, *, wrapped: bool, deps=None) -> Path:
        """An extracted look-and-feel package, with or without a wrapper dir."""
        root = tmp / "unpacked"
        pkg = (root / "com.github.vinceliuice.Layan") if wrapped else root
        (pkg / "contents").mkdir(parents=True)
        (pkg / "metadata.json").write_text(json.dumps({
            "KPlugin": {"Name": "Layan"},
            "X-KPackage-Dependencies": self.DEPS if deps is None else deps,
        }))
        return root

    def test_metadata_is_found_at_the_top_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package(Path(tmp), wrapped=False)
            self.assertEqual(manifest.find_metadata(root).parent, root)

    def test_metadata_is_found_one_level_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package(Path(tmp), wrapped=True)
            found = manifest.find_metadata(root)
            self.assertEqual(found.parent.name, "com.github.vinceliuice.Layan")

    def test_metadata_deeper_than_one_level_is_not_claimed(self):
        # A metadata.json three levels down belongs to something else, and
        # reading it would attribute another package's dependencies to this one.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "unpacked"
            deep = root / "a" / "b" / "c"
            deep.mkdir(parents=True)
            (deep / "metadata.json").write_text("{}")
            self.assertIsNone(manifest.find_metadata(root))

    def test_dependencies_are_parsed_out_of_an_extracted_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package(Path(tmp), wrapped=True)
            deps = manifest.dependencies_in_tree(root)
        self.assertEqual([d.content_id for d in deps],
                         ["1393084", "1279924", "1325243"])
        self.assertEqual(deps[0].knsrc, "xcursor")

    def test_a_broken_metadata_file_yields_nothing_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "unpacked"
            root.mkdir()
            (root / "metadata.json").write_text("{not json")
            self.assertEqual(manifest.dependencies_in_tree(root), [])

    def test_a_package_with_no_manifest_yields_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "unpacked"
            root.mkdir()
            self.assertEqual(manifest.dependencies_in_tree(root), [])

    # -- peek_dependencies ------------------------------------------------

    def _archive(self, tmp: Path) -> Path:
        """A real .tar.gz of a look-and-feel package, as the store would serve."""
        staging = tmp / "staging" / "com.github.vinceliuice.Layan"
        (staging / "contents").mkdir(parents=True)
        (staging / "metadata.json").write_text(json.dumps(
            {"X-KPackage-Dependencies": self.DEPS}))
        archive = tmp / "Layan.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            handle.add(staging, arcname="com.github.vinceliuice.Layan")
        return archive

    class _Node:
        content_id = "1325243"
        host = "api.kde-look.org"
        route = None

        def __init__(self, item):
            self.item = item

    def _item(self):
        return store.StoreItem(content_id="1325243", name="Layan", typename="",
                               xdg_type="", author="", downloads="")

    def test_peek_returns_the_declared_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(Path(tmp))
            target = store.DownloadTarget(url="https://host/x.tar.gz",
                                          filename="Layan.tar.gz", mimetype="")
            with unittest.mock.patch.object(store, "choose_download", return_value=1), \
                 unittest.mock.patch.object(store, "fetch_download", return_value=target), \
                 unittest.mock.patch.object(
                     store, "download",
                     side_effect=lambda t, dest: (dest.write_bytes(
                         archive.read_bytes()), dest)[1]):
                deps, note = install.peek_dependencies(self._Node(self._item()))
        self.assertEqual(note, "")
        self.assertEqual([d.content_id for d in deps],
                         ["1393084", "1279924", "1325243"])

    def test_peek_installs_nothing(self):
        # The whole point: bytes cross the network, the disk outside the
        # temporary directory does not change.
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            archive = self._archive(tmpdir)
            home = tmpdir / "home"
            (home / ".local/share/plasma/look-and-feel").mkdir(parents=True)
            before = sorted(p.relative_to(home) for p in home.rglob("*"))
            target = store.DownloadTarget(url="https://host/x.tar.gz",
                                          filename="Layan.tar.gz", mimetype="")
            with unittest.mock.patch.object(store, "choose_download", return_value=1), \
                 unittest.mock.patch.object(store, "fetch_download", return_value=target), \
                 unittest.mock.patch.object(
                     store, "download",
                     side_effect=lambda t, dest: (dest.write_bytes(
                         archive.read_bytes()), dest)[1]):
                install.peek_dependencies(self._Node(self._item()))
            after = sorted(p.relative_to(home) for p in home.rglob("*"))
        self.assertEqual(before, after)

    def test_peek_reports_why_it_found_nothing(self):
        with unittest.mock.patch.object(
                store, "choose_download",
                side_effect=store.StoreError("status 999: unknown request")):
            deps, note = install.peek_dependencies(self._Node(self._item()))
        self.assertEqual(deps, [])
        self.assertIn("999", note)

    def test_peek_needs_a_looked_up_item(self):
        deps, note = install.peek_dependencies(self._Node(None))
        self.assertEqual(deps, [])
        self.assertIn("could not be looked up", note)

    # -- the plan the CLI prints ------------------------------------------

    class _Args:
        no_manifest = False

    def _root(self, category="lookandfeel"):
        node = catalog.Node(content_id="1325243", host="api.kde-look.org")
        node.item = store.StoreItem(content_id="1325243", name="Layan",
                                    typename="Global Theme", xdg_type="",
                                    author="", downloads="")
        node.route = catalog.Route(category=category, target=Path("/tmp/x"))
        return node

    def _dep(self, content_id, knsrc="icons"):
        return manifest.Dependency(knsrc, "api.kde-look.org", content_id,
                                   f"kns://{knsrc}.knsrc/h/{content_id}")

    def test_manifest_ids_already_in_the_description_are_not_repeated(self):
        root = self._root()
        described = catalog.Node(content_id="1279924", host="h")
        described.item = self._item()
        with unittest.mock.patch.object(
                install, "peek_dependencies",
                return_value=([self._dep("1279924"), self._dep("1393084")], "")), \
             unittest.mock.patch.object(
                catalog, "fetch", return_value=(self._item(), "h")), \
             unittest.mock.patch.object(catalog, "route_for", return_value=None):
            extra = cli._manifest_only_components(
                root, [root, described], self._Args())
        self.assertEqual([n.content_id for n in extra], ["1393084"])

    def test_no_manifest_flag_skips_the_fetch_entirely(self):
        args = self._Args()
        args.no_manifest = True
        with unittest.mock.patch.object(install, "peek_dependencies") as peek:
            extra = cli._manifest_only_components(self._root(), [], args)
        peek.assert_not_called()
        self.assertEqual(extra, [])

    def test_non_lookandfeel_roots_are_not_downloaded(self):
        # Only global themes carry X-KPackage-Dependencies. Fetching an icon
        # theme's archive to discover that would cost megabytes for nothing.
        with unittest.mock.patch.object(install, "peek_dependencies") as peek:
            extra = cli._manifest_only_components(
                self._root(category="icons"), [], self._Args())
        peek.assert_not_called()
        self.assertEqual(extra, [])

    def test_a_lookup_failure_still_lists_the_component(self):
        # Better to show "could not be looked up" than to silently drop it and
        # under-report again by a different route.
        with unittest.mock.patch.object(
                install, "peek_dependencies",
                return_value=([self._dep("1393084")], "")), \
             unittest.mock.patch.object(
                catalog, "fetch", side_effect=store.StoreError("gone")):
            extra = cli._manifest_only_components(self._root(), [], self._Args())
        self.assertEqual(len(extra), 1)
        self.assertIsNone(extra[0].item)

    def test_manifest_components_route_by_declared_knsrc_not_store_category(self):
        # Layan's SDDM theme has no xdg_type and no known type id, so routing
        # it from the store category printed "?  unknown content type" -- while
        # the real run routed it correctly, because install_dependency uses
        # knsrc.load(dependency.knsrc). The manifest names the knsrc outright;
        # a forecast that ignores it is guessing against available evidence.
        with unittest.mock.patch.object(
                install, "peek_dependencies",
                return_value=([self._dep("1325235", knsrc="sddmtheme")], "")), \
             unittest.mock.patch.object(
                catalog, "fetch", return_value=(self._item(), "h")):
            extra = cli._manifest_only_components(self._root(), [], self._Args())
        self.assertEqual(len(extra), 1)
        self.assertTrue(extra[0].route.known)
        self.assertTrue(extra[0].route.needs_root)

    def test_needs_root_components_say_they_will_be_skipped(self):
        node = catalog.Node(content_id="1", host="h")
        node.item = self._item()
        node.route = cli._route_from_knsrc("sddmtheme")
        with unittest.mock.patch("sys.stdout", new=io.StringIO()) as out:
            cli._print_component(node)
        self.assertIn("needs root", out.getvalue())

    def test_the_dry_run_no_longer_claims_it_downloads_nothing(self):
        # It does download: the package, to a temp dir, to read the manifest.
        # The help text and the closing line have to say so.
        source = inspect.getsource(cli.cmd_please)
        self.assertNotIn("nothing downloaded", source)
        self.assertIn("nothing installed", source)


class TestHostileStoreNames(unittest.TestCase):
    """Names that arrive over the network are not path components.

    Found by review on 2026-08-02, reproduced before fixing. A store entry's
    title becomes the installed directory name whenever the archive has no
    single top-level directory -- the normal shape for icon, cursor and
    colour-scheme uploads. It was joined onto the install target and passed to
    `shutil.rmtree` under `--force` with no validation at all.
    """

    def _payload(self, tmp: Path) -> Path:
        # A top-level *file* is what makes _payloads() return None and the
        # store title get used as the directory name.
        payload = tmp / "unpacked"
        payload.mkdir()
        (payload / "index.theme").write_text("evil")
        return payload

    def test_a_title_of_dotdot_cannot_empty_the_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "share" / "icons"
            target.mkdir(parents=True)
            (target / "ExistingTheme").mkdir()
            sibling = root / "share" / "OTHER-DATA"
            sibling.mkdir()
            (sibling / "keep.txt").write_text("keep me")

            with self.assertRaises(ValueError):
                install._install_tree(self._payload(root), target, "..",
                                      force=True)
            self.assertTrue((target / "ExistingTheme").is_dir())
            self.assertEqual((sibling / "keep.txt").read_text(), "keep me")

    def test_a_relative_title_lands_inside_the_target(self):
        # Contained, not refused: a title with a slash in it is more likely to
        # be a human writing "Layan GTK/Kvantum" than an attack, and the
        # security property that matters is that nothing lands outside.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "share" / "icons"
            target.mkdir(parents=True)
            destinations = install._install_tree(
                self._payload(root), target, "../../plasma", force=False)
            self.assertFalse((root / "plasma").exists())
            self.assertEqual(destinations, [target / "plasma"])

    def test_an_absolute_title_lands_inside_the_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "icons"
            target.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            destinations = install._install_tree(
                self._payload(root), target, str(outside / "pwned"), force=True)
            self.assertFalse((outside / "pwned").exists())
            self.assertEqual(destinations, [target / "pwned"])

    def test_empty_and_dot_titles_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "icons"
            target.mkdir()
            for name in ("", " ", ".", "..", "   .. "):
                with self.subTest(name=name):
                    with self.assertRaises(ValueError):
                        install._child_of(target, name)

    def test_an_ordinary_title_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "icons"
            target.mkdir()
            destinations = install._install_tree(
                self._payload(root), target, "Tela-dark", force=False)
            self.assertEqual([d.name for d in destinations], ["Tela-dark"])
            self.assertTrue((target / "Tela-dark" / "index.theme").is_file())

    def test_a_symlink_in_the_target_is_not_deleted_through(self):
        # An existing entry that is a symlink elsewhere would otherwise be an
        # rmtree route out of the target.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "icons"
            target.mkdir()
            precious = root / "precious"
            precious.mkdir()
            (precious / "data").write_text("keep")
            (target / "Theme").symlink_to(precious, target_is_directory=True)
            with self.assertRaises(ValueError):
                install._install_tree(self._payload(root), target, "Theme",
                                      force=True)
            self.assertEqual((precious / "data").read_text(), "keep")

    # -- downloadname ------------------------------------------------------

    def test_a_traversing_downloadname_is_reduced_to_a_component(self):
        self.assertEqual(store.safe_filename("../../../.config/kdeglobals", "x"),
                         "kdeglobals")
        self.assertEqual(store.safe_filename("/etc/passwd", "x"), "passwd")

    def test_a_useless_downloadname_falls_back(self):
        for name in ("", "  ", ".", ".."):
            with self.subTest(name=name):
                self.assertEqual(store.safe_filename(name, "download-42"),
                                 "download-42")

    def test_a_hostile_downloadname_cannot_escape_the_callers_directory(self):
        # Exercised the way it actually happens: the value enters at
        # fetch_download, the caller joins it onto its temp dir, download()
        # writes. Guarding only inside download() is not enough -- by then the
        # traversal is in `destination.parent`, which is indistinguishable
        # from a directory the caller meant.
        xml = ("<ocs><meta><statuscode>100</statuscode></meta><data><content>"
               "<downloadlink>https://h/x.tar.gz</downloadlink>"
               "<downloadname>../../../.config/kdeglobals</downloadname>"
               "</content></data></ocs>")
        with unittest.mock.patch.object(store, "_read", return_value=xml.encode()):
            target = store.fetch_download("h", "1")
        self.assertEqual(target.filename, "kdeglobals")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tmpdir").mkdir()
            victim = root / "kdeglobals"
            victim.write_text("original")
            with unittest.mock.patch.object(store, "_read", return_value=b"evil"):
                written = store.download(target, root / "tmpdir" / target.filename)
            self.assertEqual(victim.read_text(), "original")
            self.assertEqual(written.parent.name, "tmpdir")

    def test_a_non_https_download_link_is_refused(self):
        for link in ("file:///etc/passwd", "http://host/x.tar.gz"):
            with self.subTest(link=link):
                xml = ("<ocs><meta><statuscode>100</statuscode></meta><data>"
                       f"<content><downloadlink>{link}</downloadlink>"
                       "</content></data></ocs>")
                with unittest.mock.patch.object(
                        store, "_read", return_value=xml.encode()):
                    with self.assertRaises(store.StoreError) as caught:
                        store.fetch_download("h", "1")
                self.assertIn("non-https", str(caught.exception))

    def test_an_oversized_download_is_refused_rather_than_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "big.tar"
            target = store.DownloadTarget(url="https://h/x", filename="big.tar",
                                          mimetype="")
            oversized = b"x" * (store.MAX_DOWNLOAD + 1)
            with unittest.mock.patch.object(store, "_read", return_value=oversized):
                with self.assertRaises(store.StoreError):
                    store.download(target, destination)
            self.assertFalse(destination.exists())


class TestExceptionsThatEscapedTheirHandlers(unittest.TestCase):
    """This project keeps meeting exceptions in surprising parts of the tree.

    Two cost a whole multi-component install before being found live
    (`tarfile.AbsoluteLinkError` is a `TarError`, not an `OSError`;
    `http.client.InvalidURL` is an `HTTPException`, not a `URLError`). Review
    found four more of the same shape. `cli.main()` catches only
    `KeyboardInterrupt`, so each one is a traceback.
    """

    def test_malformed_xml_becomes_a_StoreError(self):
        # A store that answers with an HTML error page. ET.ParseError
        # subclasses SyntaxError, so it passed every `except StoreError`.
        with unittest.mock.patch.object(store, "_read",
                                        return_value=b"<html>nope</html>"):
            with self.assertRaises(store.StoreError):
                store.fetch_metadata("host", "1")

    def test_a_stalled_read_becomes_a_StoreError(self):
        # TimeoutError is an OSError, not a URLError.
        with unittest.mock.patch.object(store.urllib.request, "urlopen",
                                        side_effect=TimeoutError("timed out")):
            with self.assertRaises(store.StoreError):
                store._read("https://host/x")

    def test_a_malformed_url_becomes_a_StoreError(self):
        # Request() raises ValueError, and used to sit outside the try block.
        for url in ("not a url", "https://[::1/x"):
            with self.subTest(url=url):
                with self.assertRaises(store.StoreError):
                    store._read(url)

    def test_metadata_that_is_not_an_object_yields_no_dependencies(self):
        for payload in ("[{}]", '"a string"', "42", "null"):
            with self.subTest(payload=payload):
                self.assertEqual(manifest.parse_dependencies(json.loads(payload)), [])

    def test_a_scalar_dependency_list_yields_nothing(self):
        self.assertEqual(
            manifest.parse_dependencies({"X-KPackage-Dependencies": "kns://a/b/1"}), [])

    def test_non_string_dependency_entries_are_skipped_not_fatal(self):
        deps = manifest.parse_dependencies({"X-KPackage-Dependencies": [
            None, 42, "kns://icons.knsrc/api.kde-look.org/1279924", {"a": 1}]})
        self.assertEqual([d.content_id for d in deps], ["1279924"])

    def test_the_dry_run_survives_a_hostile_metadata_json(self):
        # Reproduced end to end before the fix: `please --dry-run` against a
        # package whose metadata.json is a list died with AttributeError.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "unpacked"
            root.mkdir()
            (root / "metadata.json").write_text('[{"X-KPackage-Dependencies": []}]')
            self.assertEqual(manifest.dependencies_in_tree(root), [])


class TestRestoreAbortPath(_RestoreFixture, unittest.TestCase):
    """ROADMAP listed this path as never having fired. Now it fires here.

    When a step fails, the run stops -- and everything after it was never
    attempted. `_verify` used to judge those steps anyway, comparing their
    live values against the snapshot and stamping them DIVERGED: the word this
    tool uses for "a write landed and went wrong", applied to keys nothing had
    touched. The printed summary then contradicted the journal, which is what
    ROADMAP nominates as the record of where a run stopped.
    """

    def _two_step_plan(self):
        """Two components differ, in two different files, so two SET steps."""
        self._make("[Icons]\nTheme=Papirus\n", "[Icons]\nTheme=Tela\n",
                   "[Icons]\nTheme=Breeze\n", "[Icons]\nTheme=Tela\n")

        # cursorTheme lives in kcminputrc, not kdeglobals. Add it to both
        # worlds and to the snapshot manifest the way _make does.
        snap_file = self.snap / "files" / "config" / "kcminputrc"
        text = "[Mouse]\ncursorTheme=Snap\n"
        snap_file.write_text(text)
        rows = json.loads((self.snap / "manifest.json").read_text())
        rows.append({"path": "config/kcminputrc", "status": "captured",
                     "source": str(self.live / "kcminputrc"),
                     "sha256": hashlib.sha256(text.encode()).hexdigest()})
        (self.snap / "manifest.json").write_text(json.dumps(rows))
        (self.live / "kcminputrc").write_text("[Mouse]\ncursorTheme=Adwaita\n")

        return restore.build(self.snap, ["icons", "cursor"])

    def test_steps_after_a_failure_are_skipped_not_diverged(self):
        plan = self._two_step_plan()
        writes = [s for s in plan.steps if s.writes]
        if len(writes) < 2:
            self.skipTest("plan did not produce two writing steps")

        calls = {"n": 0}

        def fail_first(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return repair.WriteResult(*args[:3], args[3] if len(args) > 3 else "",
                                          repair.FAILED, detail="disk on fire")
            raise AssertionError("second step must not be attempted")

        with unittest.mock.patch.object(restore, "store",
                                        return_value=Path(self.tmp.name) / "r"), \
             unittest.mock.patch.object(repair, "write", side_effect=fail_first):
            outcome = restore.run(plan, pre_snapshot="", notify=False)

        self.assertTrue(outcome.aborted)
        self.assertEqual(writes[0].outcome, restore.FAILED)
        for step in writes[1:]:
            self.assertEqual(step.outcome, restore.SKIPPED, step.key)
            self.assertIn("not attempted", step.detail)

    def test_an_unattempted_step_produces_no_changelog_row(self):
        # A row reading `Adwaita -> Snap` for a write that never happened is a
        # false record of a change to the machine.
        plan = self._two_step_plan()
        writes = [s for s in plan.steps if s.writes]
        if len(writes) < 2:
            self.skipTest("plan did not produce two writing steps")
        for step in writes:
            step.outcome = restore.SKIPPED
        outcome = restore.Outcome(Path(self.tmp.name), plan.steps, "snap-1")
        self.assertEqual(restore.changelog_row(outcome), [])


class TestPruneCanSeeEveryPointer(unittest.TestCase):
    """Found by review 2026-08-02: prune was blind to two of seven components.

    Every real `contents/defaults` writes the wallpaper and splash as **bare**
    groups -- `[Wallpaper]`, `[KSplash]` -- not `[plasmarc][Wallpaper]`. So
    `components()` returned neither kind for any theme ever installed, and
    `referenced_by()` could not see that a surviving theme needed a wallpaper.
    """

    def _theme(self, root: Path, name: str, body: str) -> Path:
        d = root / name / "contents"
        d.mkdir(parents=True)
        (d / "defaults").write_text(body)
        return root / name

    def test_a_bare_wallpaper_group_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            theme = self._theme(Path(tmp), "T", "[Wallpaper]\nImage=Scenery\n")
            self.assertEqual(prune.components(theme)["wallpaper"][0], "Scenery")

    def test_a_bare_ksplash_group_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            theme = self._theme(Path(tmp), "T", "[KSplash]\nTheme=org.kde.Breeze\n")
            self.assertEqual(prune.components(theme)["splash"][0], "org.kde.Breeze")

    def test_the_qualified_form_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            theme = self._theme(Path(tmp), "T",
                                "[plasmarc][Wallpaper]\nImage=Scenery\n")
            self.assertEqual(prune.components(theme)["wallpaper"][0], "Scenery")

    def test_every_installed_theme_on_this_machine_parses(self):
        # The regression that mattered was silent: no error, just an empty
        # result for two kinds, on every theme.
        root = prune.look_and_feel_dir()
        if not root.is_dir():
            self.skipTest("no user look-and-feel packages installed")
        declared = {}
        for theme in sorted(p for p in root.iterdir() if p.is_dir()):
            for kind, (name, _) in prune.components(theme).items():
                declared.setdefault(kind, []).append(name)
        self.assertIn("wallpaper", declared,
                      "no installed theme declares a wallpaper prune can see")


class TestPruneComparesPathsNotNames(unittest.TestCase):
    """A colour scheme's display name is not its filename.

    `Sweet-Ambar-Blue` lives in `SweetAmbarBlue.colors`. `locate()` matches
    either, but the live config and a theme's defaults only ever store one --
    so `--drop <display name>` slipped past a refusal that `--drop <stem>`
    correctly triggered, for the same file.
    """

    def test_holders_of_matches_by_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schemes = root / "color-schemes"
            schemes.mkdir(parents=True)
            (schemes / "SweetAmbarBlue.colors").write_text(
                "[General]\nName=Sweet-Ambar-Blue\n")
            lnf = root / "plasma/look-and-feel/Sweet/contents"
            lnf.mkdir(parents=True)
            (lnf / "defaults").write_text(
                "[kdeglobals][General]\nColorScheme=SweetAmbarBlue\n")

            with unittest.mock.patch.object(paths, "data_home",
                                            return_value=root), \
                 unittest.mock.patch.object(paths, "data_dirs",
                                            return_value=[root]):
                def existing(name):
                    return {str(p) for p in prune.locate("colour-scheme", name)
                            if p.exists()}

                # Both spellings reach the same file on disk...
                by_stem = existing("SweetAmbarBlue")
                by_display = existing("Sweet-Ambar-Blue")
                self.assertEqual(by_stem, by_display)
                self.assertTrue(by_stem)

                # ...so both must find the theme that needs it. Before the
                # fix, referenced_by("Sweet-Ambar-Blue") returned [] while
                # referenced_by("SweetAmbarBlue") returned ["Sweet"].
                self.assertEqual(prune.holders_of(by_display), ["Sweet"])
                self.assertEqual(prune.holders_of(by_stem), ["Sweet"])
                self.assertEqual(prune.referenced_by("Sweet-Ambar-Blue"), [])


class TestStripKeyKeepsBytes(unittest.TestCase):
    """KDE stores paths as raw bytes; a filename need not be valid UTF-8."""

    def test_non_utf8_bytes_elsewhere_in_the_file_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "kdeglobals"
            f.write_bytes(b"[General]\nWallpaper=/pic/\xff\xfe.png\n"
                          b"[Icons]\nTheme=Tela\n")
            repair._strip_key(f, "Icons", "Theme")
            after = f.read_bytes()
        self.assertIn(b"/pic/\xff\xfe.png", after)
        self.assertNotIn(b"\xef\xbf\xbd", after)      # U+FFFD
        self.assertNotIn(b"Theme=Tela", after)

    def test_crlf_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "kdeglobals"
            f.write_bytes(b"[Icons]\r\nTheme=Tela\r\nOther=x\r\n")
            repair._strip_key(f, "Icons", "Theme")
            self.assertEqual(f.read_bytes(), b"[Icons]\r\nOther=x\r\n")


class TestKioskImmutabilityMarkers(unittest.TestCase):
    """`Key[$i]` is the standard Kiosk lock, and it crashed everything.

    A flagged key is stored under its decorated name, so `entry_state` said
    `set` while `parser.get(group, key)` raised NoOptionError. Any machine with
    a policy-managed `/etc/xdg/kdeglobals` got a traceback out of `restore`,
    `prune` and `repair.inherited_value`.
    """

    def test_an_immutable_key_reads_its_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "kdeglobals"
            f.write_text("[Icons]\nTheme[$i]=BreezeLocked\n")
            self.assertEqual(kconfig.get(f, "Icons", "Theme"), "BreezeLocked")

    def test_an_expanding_key_reads_its_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "scheme.colors"
            f.write_text("[General]\nName[$e]=$HOME theme\n")
            self.assertEqual(kconfig.get(f, "General", "Name"), "$HOME theme")

    def test_a_tombstone_still_reads_as_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "kdeglobals"
            f.write_text("[Icons]\nTheme[$d]\n")
            self.assertIsNone(kconfig.get(f, "Icons", "Theme"))
            self.assertTrue(kconfig.tombstoned(f, "Icons", "Theme"))


class TestWriteRefusesASymlinkedConfig(unittest.TestCase):
    """unpin() refused one; write() wrote straight through it into git."""

    def test_write_refuses_a_symlinked_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config"
            config.mkdir()
            dotfiles = root / "dotfiles"
            dotfiles.mkdir()
            real = dotfiles / "kdeglobals"
            real.write_text("[Icons]\nTheme=Papirus\n")
            (config / "kdeglobals").symlink_to(real)

            with unittest.mock.patch.object(paths, "config_home",
                                            return_value=config):
                result = repair.write("kdeglobals", "Icons", "Theme", "Tela",
                                      notify=False)
            self.assertEqual(result.outcome, repair.FAILED)
            self.assertIn("symlink", result.detail)
            self.assertEqual(real.read_text(), "[Icons]\nTheme=Papirus\n")


class TestReportsThatWereInaccurate(unittest.TestCase):
    """Three findings whose only symptom was a report that lied.

    None lost data. All three would send whoever read them -- person or agent
    -- after the wrong thing, which is its own kind of cost.
    """

    def test_an_empty_lock_file_is_not_silently_taken(self):
        # Exactly what a process killed between O_CREAT and the pid write
        # leaves behind. It used to be unlinked and retried, so a second run
        # would delete a live run's lock and both would think they held it.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock"
            path.write_text("")
            held = restore.Lock(path).acquire()
            self.assertIsNotNone(held)
            self.assertIn("--break-lock", held)
            self.assertEqual(path.read_text(), "")   # not stolen

    def test_break_lock_still_takes_an_empty_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock"
            path.write_text("")
            self.assertIsNone(restore.Lock(path).acquire(break_stale=True))
            self.assertEqual(path.read_text().strip(), str(os.getpid()))

    def test_a_live_holder_is_still_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock"
            path.write_text(str(os.getpid()))
            held = restore.Lock(path).acquire()
            self.assertIn(str(os.getpid()), held)

    def test_prune_and_snapshot_take_the_lock_too(self):
        # The Lock docstring claims a restore racing a snapshot is
        # unreconstructable. That only holds if both sides take it; for a long
        # time only cmd_restore did.
        for handler in (cli.cmd_prune, cli.cmd_snapshot):
            with self.subTest(handler=handler.__name__):
                self.assertIn("Lock()", inspect.getsource(handler))

    def test_unpin_reports_a_pin_its_failure_left_behind(self):
        # Step 1 writes the inherited value through kwriteconfig6; if the raw
        # edit then fails, the user layer holds a pin the user never had, and
        # FAILED steps are skipped by both _verify and changelog_row.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config"
            (config / "kdedefaults").mkdir(parents=True)
            (config / "kdedefaults" / "kdeglobals").write_text(
                "[Icons]\nTheme=Tela\n")
            # Pinned to something else, so step 1 has a real write to do.
            (config / "kdeglobals").write_text("[Icons]\nTheme=Breeze\n")

            def pretend_written(*args, **kwargs):
                # What kwriteconfig6 would have done in step 1.
                (config / "kdeglobals").write_text("[Icons]\nTheme=Tela\n")
                return repair.WriteResult("kdeglobals", "Icons", "Theme",
                                          "Tela", repair.WROTE)

            with unittest.mock.patch.object(paths, "config_home",
                                            return_value=config), \
                 unittest.mock.patch.object(repair, "write",
                                            side_effect=pretend_written), \
                 unittest.mock.patch.object(
                     repair, "_strip_key",
                     side_effect=OSError(28, "No space left on device")):
                result = repair.unpin("kdeglobals", "Icons", "Theme",
                                      notify=False)

        self.assertEqual(result.outcome, repair.FAILED)
        self.assertIn("No space left", result.detail)
        self.assertIn("user layer now holds", result.detail)
        self.assertIn("'Breeze'", result.detail)

    def test_coverage_carries_what_it_could_not_check(self):
        # `13/13` and `8/8` looked identical while meaning very different
        # things, because skipped probes left the denominator.
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(snapshot, "store",
                                            return_value=Path(tmp)):
                meta = snapshot.capture(message="coverage shape", with_sweep=False)
        for field in ("ok", "total", "skipped", "possible"):
            self.assertIn(field, meta["coverage"], field)
        self.assertEqual(meta["coverage"]["possible"],
                         meta["coverage"]["total"] + meta["coverage"]["skipped"])


if __name__ == "__main__":
    unittest.main()
