"""Tests for the parts that are easy to get subtly wrong.

Run with: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lolkde import banner, kconfig, knsrc, manifest, paths, resolve  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
