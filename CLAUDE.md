# lol-kde — working notes

Everything here was learned the expensive way in one session. Read it before
theorising about KDE theming; most of it is not discoverable from documentation
and several items cost hours.

## Method rules (these matter more than the facts below)

**Name the instrument.** If you measure something, state which tool produced the
number and whether that tool is the one the system actually uses. KWin renders
SVGs through **Qt/KSvg**. `librsvg`/`rsvg-convert` is a *different renderer* and
disagrees on real theme files. Presenting librsvg numbers as "what KWin draws"
wasted most of an evening.

**Cheapest discriminating test first.** Rasterising 29 SVGs is expensive and
indirect. Grepping two config files that differ by one line is free and
decisive. The expensive test was chosen because it matched the theory.

**`7/7 ok` is metadata agreeing with itself.** It is *not* evidence that anything
is visible on screen. When the user says "it doesn't work" and the tool says
everything is fine, the tool is measuring the wrong layer. Believe the user.

**Check the layer you can change before the layer you can measure.** The answer
to "nothing is ever translucent" was one integer in a Kvantum config, in a file
format this repo already had a parser for. Four rendering investigations
preceded looking at it.

**Beware confounding compositor effects when judging translucency.** KWin's
Translucency effect ships `MoveResize=75`, so *any* window goes see-through
while dragged, under any theme. The `scale` effect fades windows in on open. A
window that looks translucent and then "goes back to normal" is almost always
one of those, not the theme.

## KDE architecture facts

**A Global Theme is a manifest of pointers, not a theme.** `contents/defaults`
names up to seven components. If a name does not resolve, KDE substitutes a
default silently — no error, no warning, nothing in System Settings.

**Config is a cascade; global themes write to `kdedefaults`.** Applying a global
theme writes to `~/.config/kdedefaults/*`, not `~/.config/*`, so user overrides
still win. Read order, lowest priority first:

```
$XDG_CONFIG_DIRS (reversed) → ~/.config/kdedefaults → ~/.config
```

Reading only `~/.config` reports a perfectly applied theme as entirely `unset`.

**`kwriteconfig6` can exit 0 and write nothing.** If the value already matches
the inherited default, KConfig does not store it again. This looks exactly like
a failed write.

**Colour scheme filenames are not identifiers.** `Sweet-Ambar-Blue` lives in
`SweetAmbarBlue.colors`. Match on the internal `Name=` field.

**Icons and cursors have six search paths**, including legacy `~/.icons`.
Checking only `~/.local/share/icons` produces false negatives.

**KWin 6.6 loads KDecoration3 plugins but reads the `org.kde.kdecoration2`
config group.** The Window Decorations KCM may show *nothing selected* while
KWin has the decoration correctly loaded. Ask KWin, never the KCM:

```sh
qdbus6 org.kde.KWin /KWin org.kde.KWin.supportInformation | grep -A4 '^Decoration'
```

## Kvantum (source of most confusion)

- It is a **style engine**, not a style. `widgetStyle=kvantum` succeeds with no
  theme installed and renders flat grey.
- It keeps its own theme selection in `~/.config/Kvantum/kvantum.kvconfig`.
  **Applying a global theme does not touch it.** You can have all seven
  components report `ok` while window interiors render a previous theme.
- **`reduce_window_opacity` is the translucency knob.** `translucent_windows=true`
  only enables the mechanism; themes ship it `true` with `reduce_window_opacity=0`
  and render fully opaque. This single integer was the answer to "nothing is ever
  translucent, EVER".
- Themes ship variants that differ *only* by these flags: `Layan` (true/15 after
  our edit, originally true/0) vs `Layan-solid` (false/0).
- Kvantum themes are distributed on **GitHub, not the KDE Store**, so
  `lol-kde install` cannot fetch them.

## KDE Store / OCS

- Authors declare dependencies **in the description prose**, as pling links.
  That is machine-readable and `lol-kde please` uses it.
- The description and `X-KPackage-Dependencies` **disagree**. Layan's description
  names 4 components; its manifest names 7, and only the manifest mentions the
  cursor theme. Use both.
- **One store entry ships several files.** Layan cursors has three variants;
  Layan kvantum has `Layan.tar.xz` and `Layan-solid.tar.xz`. Fetching download #1
  silently installs the wrong one. Match the filename against the wanted
  component name.
- **One archive is not one package.** Tela ships `Tela`, `Tela-dark`,
  `Tela-light` as siblings. Archives whose top level has *files* are single
  packages and must not be split.
- The dependency graph is **cyclic**; record ids on enqueue.
- `kpackagetool6` reports the installed path. Use it — the installed directory
  (`com.github.vinceliuice.Layan`) is rarely the store's display name, and
  diffing installed-themes before/after fails on upgrades.
- Install targets come from `/usr/share/knsrcfiles/*.knsrc`. Do not hardcode.

## Legacy metadata

Plasma styles with `metadata.desktop` and no `metadata.json` predate Plasma 5.19.
They load, but plasmashell was observed aborting inside
`KSvg::FrameSvg::mask()` during a **live** theme reload of one. Self-healing
(systemd restarts it), settings survive. Logging out avoids it.

Aurorae decorations use `metadata.desktop` as their **normal** format — never
flag those as legacy.

## Machine state as of 2026-08-02

- Applied: `com.github.vinceliuice.Layan`, `7/7 ok`
- Kvantum: theme `Layan`, `reduce_window_opacity=15` (backup `.bak` has `0`)
- Window translucency: **working**, confirmed visually
- Compositor: `gl2`, NVIDIA, Wayland. Blur loaded; `contrast` refuses to load.
- Xwayland scale 1.2 (fractional — expect soft edges on SVG decorations)

## Open leads

1. **Window decorations** — the next task. KWin has `org.kde.kwin.aurorae` /
   `__aurorae__svg__Layan` loaded and `alphaChannelSupported: true`, but the
   Window Decorations KCM shows nothing selected. Suspect a KDecoration2/3
   config-group mismatch in the KCM. Decoration *translucency* was never
   settled — the alpha table produced earlier was withdrawn as unreliable and
   must not be trusted. Settling it needs `python3-pyqt6.qtsvg` (QSvgRenderer),
   the engine KSvg actually uses.
2. **Layan button bug** — `maximize.svg` and `restore.svg` contain
   `<use href="#g1000">` where `g1000` is never defined. Qt logs
   `link #g1000 is undefined!` on every repaint. Upstream:
   `github.com/vinceliuice/Layan-kde`. Local patch not yet applied.
3. **`contrast` effect** won't load despite `contrastEnabled=true` and a KWin
   reconfigure. Affects panel frostiness only.
