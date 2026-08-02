"""Resolving theme pointers against what is actually installed.

A KDE global theme is a manifest of pointers. Each pointer names a component
by string; nothing verifies the string resolves. This module does that check,
one resolver per component kind.

Three outcomes:
  OK        the component is installed and usable
  DEGRADED  it exists but will not render as intended (see: kvantum)
  MISSING   nothing on disk matches; KDE will silently fall back
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import kconfig, paths

OK = "OK"
DEGRADED = "DEGRADED"
MISSING = "MISSING"

AURORAE_PREFIX = "__aurorae__svg__"

# Qt styles that are always available without a plugin file on disk.
BUILTIN_QT_STYLES = {"fusion", "windows"}

# Style engines that resolve as a plugin but render nothing useful until a
# separate, differently-packaged theme is installed for them.
ENGINE_STYLES = {"kvantum", "kvantum-dark"}


@dataclass
class Resolution:
    kind: str            # e.g. "widget-style"
    label: str           # human-readable component name
    value: str           # the raw pointer value from the manifest
    status: str          # OK | DEGRADED | MISSING
    detail: str = ""     # explanation, shown for non-OK results
    path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.status == OK


def _search(subdir: str, name: str) -> Path | None:
    """Find <data dir>/<subdir>/<name> in any XDG data directory."""
    if not name:
        return None
    return paths.first_existing([d / subdir / name for d in paths.data_dirs()])


def widget_style(name: str, expect: str = "") -> Resolution:
    """Qt widget style. Draws everything *inside* an application window.

    `expect` is the global theme's name. Kvantum keeps its own theme selection
    in its own config file, so it can happily be pointed at a completely
    different theme than the one you applied -- and every other component will
    still report ok while your window interiors render someone else's design.
    """
    r = Resolution("widget-style", "Widget style", name, MISSING)
    if not name:
        return Resolution("widget-style", "Widget style", name, OK, "unset (Qt default)")

    key = name.lower()
    plugin = None
    for plugin_dir in paths.qt_plugin_dirs():
        styles = plugin_dir / "styles"
        if not styles.is_dir():
            continue
        for so in styles.glob("*.so"):
            # breeze6.so -> breeze, libkvantum.so -> kvantum
            stem = so.stem.removeprefix("lib").rstrip("0123456789")
            if stem.lower() == key.removesuffix("-dark"):
                plugin = so
                break
        if plugin:
            break

    if not plugin and key not in BUILTIN_QT_STYLES:
        r.detail = "no Qt style plugin by this name; Qt falls back to Breeze/Fusion"
        return r

    r.path = plugin
    r.status = OK

    if key in ENGINE_STYLES:
        # Kvantum is an engine, not a theme. It needs its own config plus its
        # own theme package, neither of which ship with a global theme.
        kv_config = paths.config_home() / "Kvantum" / "kvantum.kvconfig"
        kv_dir = paths.config_home() / "Kvantum"
        if not kv_dir.is_dir():
            return Resolution(
                "widget-style", "Widget style", name, DEGRADED,
                "Kvantum engine installed but ~/.config/Kvantum does not exist; "
                "it renders its built-in grey default and overrides your colour scheme",
                plugin,
            )
        theme = kconfig.get(kv_config, "General", "theme") if kv_config.is_file() else None
        if not theme:
            return Resolution(
                "widget-style", "Widget style", name, DEGRADED,
                "Kvantum has no theme selected in kvantum.kvconfig", plugin,
            )
        installed = any(
            (kv_dir / theme).is_dir() or (d / "Kvantum" / theme).is_dir()
            for d in paths.data_dirs()
        )
        if not installed:
            return Resolution(
                "widget-style", "Widget style", name, DEGRADED,
                f"Kvantum theme {theme!r} is selected but not installed", plugin,
            )
        r.detail = f"Kvantum theme: {theme}"
        if expect:
            norm = lambda v: v.lower().replace("-", "").replace("_", "").replace(" ", "")
            base = norm(theme)
            wanted = norm(expect)
            if wanted not in base and base not in wanted:
                return Resolution(
                    "widget-style", "Widget style", name, DEGRADED,
                    f"Kvantum is set to {theme!r}, which is not part of "
                    f"{expect!r}; window interiors render that other theme",
                    plugin,
                )

        # Translucency lives in the Kvantum config, not in any SVG.
        # reduce_window_opacity is the knob that decides; translucent_windows
        # merely enables the mechanism and is true on themes that render fully
        # opaque. Layan ships true/0. Reported unconditionally: this is the
        # single number that answers "why is nothing translucent".
        for base_dir in [paths.config_home() / "Kvantum"] + [
                d / "Kvantum" for d in paths.data_dirs()]:
            cfg = base_dir / theme / f"{theme}.kvconfig"
            if cfg.is_file():
                opacity = kconfig.get(cfg, "%General", "reduce_window_opacity")
                enabled = kconfig.get(cfg, "%General", "translucent_windows")
                if opacity is not None:
                    r.detail += f", reduce_window_opacity={opacity}"
                    if opacity.strip().rstrip("%") in ("", "0"):
                        r.detail += " (windows will be fully opaque)"
                elif enabled is not None:
                    r.detail += f", translucent_windows={enabled}"
                break
    return r


def color_scheme(name: str) -> Resolution:
    """Colour scheme.

    The filename is NOT the identifier -- `Sweet-Ambar-Blue` lives in
    `SweetAmbarBlue.colors`. KDE matches on the internal Name= field, so a
    naive filename check reports a false negative.
    """
    r = Resolution("color-scheme", "Colour scheme", name, MISSING)
    if not name:
        r.status = OK
        return r

    key = name.lower().replace("-", "").replace(" ", "").replace("_", "")
    for base in paths.data_dirs():
        directory = base / "color-schemes"
        if not directory.is_dir():
            continue
        for scheme in sorted(directory.glob("*.colors")):
            internal = kconfig.get(scheme, "General", "Name") or ""
            stem = scheme.stem
            for candidate in (internal, stem):
                norm = candidate.lower().replace("-", "").replace(" ", "").replace("_", "")
                if norm == key:
                    r.status, r.path = OK, scheme
                    if stem.lower() != name.lower():
                        r.detail = f"file: {scheme.name}"
                    return r
    r.detail = "no .colors file declares this name; previous colours persist"
    return r


def icon_theme(name: str) -> Resolution:
    r = Resolution("icons", "Icon theme", name, MISSING)
    if not name:
        r.status = OK
        return r
    hit = paths.first_existing([d / name / "index.theme" for d in paths.icon_dirs()])
    if hit:
        r.status, r.path = OK, hit.parent
    else:
        r.detail = "not installed; icons fall back to the hicolor/Breeze chain"
    return r


def cursor_theme(name: str) -> Resolution:
    r = Resolution("cursors", "Cursor theme", name, MISSING)
    if not name:
        r.status = OK
        return r
    for directory in paths.icon_dirs():
        candidate = directory / name
        if (candidate / "cursors").is_dir() or (candidate / "index.theme").is_file():
            r.status, r.path = OK, candidate
            return r
    r.detail = "not installed; cursors silently fall back to Breeze"
    return r


def plasma_style(name: str) -> Resolution:
    """Plasma style. Panel, popups and system tray only -- never applications."""
    r = Resolution("plasma-style", "Plasma style", name, MISSING)
    if not name:
        r.status = OK
        return r
    hit = _search("plasma/desktoptheme", name)
    if hit:
        r.status, r.path = OK, hit
    else:
        r.detail = "not installed; panel falls back to Breeze"
    return r


AURORAE_LEGACY_PLUGIN = "org.kde.kwin.aurorae"
AURORAE_SVG_PLUGIN = "org.kde.kwin.aurorae.v2"


def aurorae_provider() -> str:
    """The decoration plugin that actually offers Aurorae SVG themes here.

    Plasma 6.6 split Aurorae in two. 'org.kde.kwin.aurorae' is now only the QML
    renderer and offers exactly one theme -- Plastik. Every SVG theme moved to
    the native 'org.kde.kwin.aurorae.v2'.

    KWin still loads an SVG theme under the old plugin name, so nothing appears
    broken. But the Window Decorations page matches its list on the pair
    (plugin, theme), finds no row where plugin is the old name, and therefore
    shows *nothing selected* -- with your decoration visibly on screen behind
    the dialog. Every Aurorae theme published to the KDE Store still writes the
    old name, so this hits essentially all of them.
    """
    for plugin_dir in paths.qt_plugin_dirs():
        for api in ("org.kde.kdecoration3", "org.kde.kdecoration2"):
            if (plugin_dir / api / f"{AURORAE_SVG_PLUGIN}.so").is_file():
                return AURORAE_SVG_PLUGIN
    return AURORAE_LEGACY_PLUGIN


SCALED_VARIANT = re.compile(r"^(?P<base>.+?)_x(?P<factor>[0-9]+(?:\.[0-9]+)?)$")


def _aurorae_rc(theme_dir: Path) -> Path | None:
    """An Aurorae theme's layout file: <dir>/<dirname>rc."""
    candidate = theme_dir / f"{theme_dir.name}rc"
    if candidate.is_file():
        return candidate
    rest = sorted(theme_dir.glob("*rc"))
    return rest[0] if rest else None


def aurorae_scale_mismatch(theme_dir: Path) -> str:
    """A pre-scaled Aurorae variant whose layout numbers were never scaled.

    WhiteSur ships WhiteSur-dark alongside WhiteSur-dark_x1.25 and _x1.5. The
    SVG artwork genuinely is scaled -- measured with QSvgRenderer, the
    decoration-top element goes 66px -> 83px -> 99px, exactly 1.25x and 1.5x.
    The <theme>rc that tells Aurorae where to *put* that artwork is
    byte-identical in all three: TitleHeight=16, PaddingTop=36, ButtonWidth=16.

    So Aurorae lays the frame out with the small numbers and draws the large
    artwork into it. The titlebar detaches from the window body and the
    buttons sit off the bar. Every _x variant in that package has it.

    Returns an explanation, or "" if the theme is fine.
    """
    match = SCALED_VARIANT.match(theme_dir.name)
    if not match:
        return ""
    sibling = theme_dir.parent / match.group("base")
    if not sibling.is_dir():
        return ""
    mine, theirs = _aurorae_rc(theme_dir), _aurorae_rc(sibling)
    if not mine or not theirs:
        return ""
    try:
        if mine.read_bytes() != theirs.read_bytes():
            return ""
    except OSError:
        return ""
    return (f"artwork is pre-scaled to {match.group('factor')}x but its layout "
            f"metrics are byte-identical to {sibling.name!r}; Aurorae will "
            f"place large artwork in a small frame and the titlebar will "
            f"detach from the window")


def decoration(library: str, theme: str) -> Resolution:
    """Window decoration. The titlebar and borders, and nothing else."""
    value = theme or library
    r = Resolution("decoration", "Window decoration", value, MISSING)
    if not value:
        r.status = OK
        return r

    if theme.startswith(AURORAE_PREFIX):
        aurorae_name = theme[len(AURORAE_PREFIX):]
        hit = _search("aurorae/themes", aurorae_name)
        if not hit:
            r.detail = (f"Aurorae theme {aurorae_name!r} not installed; "
                        "falls back to Breeze")
            return r
        r.status, r.path = OK, hit
        if not (hit / "decoration.svg").is_file():
            r.status = DEGRADED
            r.detail = "theme directory exists but has no decoration.svg"
            return r
        mismatch = aurorae_scale_mismatch(hit)
        if mismatch:
            r.status = DEGRADED
            r.detail = mismatch
            return r
        provider = aurorae_provider()
        if library and library != provider:
            r.status = DEGRADED
            r.detail = (
                f"library={library}, but this Plasma serves Aurorae SVG themes "
                f"from {provider}. KWin loads the theme anyway, so it looks "
                f"right; System Settings shows no decoration selected. "
                f"'lol-kde apply' rewrites it.")
        return r

    for plugin_dir in paths.qt_plugin_dirs():
        for api in ("org.kde.kdecoration3", "org.kde.kdecoration2"):
            candidate = plugin_dir / api / f"{library}.so"
            if candidate.is_file():
                r.status, r.path = OK, candidate
                return r
    r.detail = f"no decoration plugin named {library!r}"
    return r


def look_and_feel(name: str) -> Resolution:
    r = Resolution("look-and-feel", "Global theme", name, MISSING)
    if not name:
        r.status = OK
        return r
    hit = _search("plasma/look-and-feel", name)
    if hit:
        r.status, r.path = OK, hit
    else:
        r.detail = "global theme package not installed"
    return r


def splash(name: str) -> Resolution:
    r = Resolution("splash", "Splash screen", name, MISSING)
    if not name or name in ("None", "org.kde.breeze.desktop"):
        r.status = OK
        return r
    hit = _search("plasma/look-and-feel", name)
    if hit:
        r.status, r.path = OK, hit
    else:
        r.detail = "splash package not installed; boot splash falls back to Breeze"
    return r


def wallpaper(name: str) -> Resolution:
    r = Resolution("wallpaper", "Wallpaper", name, MISSING)
    if not name:
        r.status = OK
        return r
    if Path(name).is_file() or Path(name).is_dir():
        r.status, r.path = OK, Path(name)
        return r
    hit = _search("wallpapers", name)
    if hit:
        r.status, r.path = OK, hit
    else:
        r.detail = "wallpaper not installed"
    return r


# Which manifest keys map to which resolver.
# (config file, group, key) -> resolver taking a single string
SIMPLE_POINTERS = {
    ("kdeglobals", "KDE", "widgetStyle"): widget_style,
    ("kdeglobals", "General", "ColorScheme"): color_scheme,
    ("kdeglobals", "Icons", "Theme"): icon_theme,
    ("kcminputrc", "Mouse", "cursorTheme"): cursor_theme,
    ("plasmarc", "Theme", "name"): plasma_style,
    ("ksplashrc", "KSplash", "Theme"): splash,
}

# Labels for pointers that are declared but unset, where we have no value to
# resolve and therefore no Resolution object to carry the label.
POINTER_LABELS = {
    ("kdeglobals", "KDE", "widgetStyle"): "Widget style",
    ("kdeglobals", "General", "ColorScheme"): "Colour scheme",
    ("kdeglobals", "Icons", "Theme"): "Icon theme",
    ("kcminputrc", "Mouse", "cursorTheme"): "Cursor theme",
    ("plasmarc", "Theme", "name"): "Plasma style",
    ("ksplashrc", "KSplash", "Theme"): "Splash screen",
}


def resolve_settings(settings: dict[tuple[str, str], dict[str, str]]) -> list[Resolution]:
    """Resolve a bundle of {(config_file, group): {key: value}} settings.

    Used for both a theme's declared manifest and the live system config, so
    `check` and `doctor` agree on what counts as broken.
    """
    results: list[Resolution] = []
    for (config_file, group, key), resolver in SIMPLE_POINTERS.items():
        value = settings.get((config_file, group), {}).get(key)
        if value:
            results.append(resolver(value))

    deco_group = settings.get(("kwinrc", "org.kde.kdecoration3")) or settings.get(
        ("kwinrc", "org.kde.kdecoration2")
    )
    if deco_group:
        library = deco_group.get("library", "")
        theme = deco_group.get("theme", "")
        if library or theme:
            results.append(decoration(library, theme))
    return results


UNSET = "UNSET"
DRIFT = "DRIFT"


def pointer_kinds() -> int:
    """How many component pointers a global theme can declare.

    Computed, never written down. A hardcoded count in the banner drifted from
    the code the moment a splash-screen pointer was added, and the tool ended
    up describing six things while verifying seven.
    """
    return len(SIMPLE_POINTERS) + 1        # + the window decoration


def _same_pointer(kind: str, a: str, b: str) -> bool:
    """Do two pointer values name the same component?

    Colour schemes are referred to by both their file stem and their internal
    Name=, which differ in punctuation (SweetAmbarBlue vs Sweet-Ambar-Blue).
    Comparing them raw reports drift where none exists.
    """
    if a == b:
        return True
    if kind == "color-scheme":
        norm = lambda s: s.lower().replace("-", "").replace(" ", "").replace("_", "")
        return norm(a) == norm(b)
    return False


@dataclass
class AuditRow:
    """One component, compared between what a theme declares and what is live."""
    label: str
    declared: str | None
    live: str | None
    resolution: Resolution | None
    note: str = ""

    @property
    def status(self) -> str:
        if self.live is None:
            return UNSET
        return self.resolution.status if self.resolution else OK


def _deco_pointer(settings: dict[tuple[str, str], dict[str, str]]) -> tuple[str, str]:
    group = settings.get(("kwinrc", "org.kde.kdecoration3")) or settings.get(
        ("kwinrc", "org.kde.kdecoration2")
    ) or {}
    return group.get("library", ""), group.get("theme", "")


def _pretty_deco(library: str, theme: str) -> str:
    if theme.startswith(AURORAE_PREFIX):
        return theme[len(AURORAE_PREFIX):]
    return theme or library


def audit(
    declared: dict[tuple[str, str], dict[str, str]],
    live: dict[tuple[str, str], dict[str, str]],
) -> list[AuditRow]:
    """Compare a theme's declared pointers against the live configuration.

    Reports three distinct problems that all look identical in System Settings:
      MISSING  the live value names something that is not installed
      UNSET    the theme declares a component but nothing set it
      DRIFT    live and declared disagree (a later manual change won)
    """
    rows: list[AuditRow] = []

    theme_name = live.get(("kdeglobals", "KDE"), {}).get("LookAndFeelPackage", "")
    for key, resolver in SIMPLE_POINTERS.items():
        config_file, group, option = key
        declared_value = declared.get((config_file, group), {}).get(option)
        live_value = live.get((config_file, group), {}).get(option)
        if not declared_value and not live_value:
            continue

        if live_value and resolver is widget_style:
            resolution = widget_style(live_value, theme_name)
        else:
            resolution = resolver(live_value) if live_value else None
        kind = resolution.kind if resolution else ""
        note = ""
        if declared_value and not live_value:
            note = f"theme declares {declared_value!r} but nothing set it; KDE default in use"
        elif (declared_value and live_value
              and not _same_pointer(kind, declared_value, live_value)):
            note = f"theme declares {declared_value!r}; changed since"

        rows.append(AuditRow(
            label=POINTER_LABELS[key],
            declared=declared_value,
            live=live_value,
            resolution=resolution,
            note=note,
        ))

    declared_lib, declared_theme = _deco_pointer(declared)
    live_lib, live_theme = _deco_pointer(live)
    if declared_lib or declared_theme or live_lib or live_theme:
        resolution = decoration(live_lib, live_theme) if (live_lib or live_theme) else None
        note = ""
        declared_pretty = _pretty_deco(declared_lib, declared_theme)
        live_pretty = _pretty_deco(live_lib, live_theme)
        if declared_pretty and not live_pretty:
            note = f"theme declares {declared_pretty!r} but nothing set it; KDE default in use"
        elif declared_pretty and live_pretty and declared_pretty != live_pretty:
            note = f"theme declares {declared_pretty!r}; changed since"
        rows.append(AuditRow(
            label="Window decoration",
            declared=declared_pretty or None,
            live=live_pretty or None,
            resolution=resolution,
            note=note,
        ))

    return rows


def live_settings() -> dict[tuple[str, str], dict[str, str]]:
    """Read the configuration KDE actually resolves, across every layer.

    Not just ~/.config: a global theme's settings live in
    ~/.config/kdedefaults/, so reading the user layer alone reports a working
    theme as completely unset.
    """
    out: dict[tuple[str, str], dict[str, str]] = {}
    for config_file in ("kdeglobals", "kcminputrc", "plasmarc", "ksplashrc", "kwinrc"):
        out.update(kconfig.read_cascade(config_file))
    return out
