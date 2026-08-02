"""Tests for the parts that are easy to get subtly wrong.

Run with: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lolkde import (banner, catalog, install, kconfig, knsrc, legacy,  # noqa: E402
                    manifest, paths, repair, resolve, store)


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
