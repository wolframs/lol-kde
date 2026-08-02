# lol-kde — working notes

Everything here was learned the expensive way in one session. Read it before
theorising about KDE theming; most of it is not discoverable from documentation
and several items cost hours.

## Keep CHANGELOG.md current

Every change to Wolfram's **live configuration** — not just repo commits —
goes in `CHANGELOG.md` with the old value, the new value, the backup path and
the exact revert command. Time is counted in **user turns**, not dates.
Back up before writing; record the backup path in the same entry.

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
config group.** Both are true at once. Do not "correct" the group name.
Ask KWin, never the KCM:

```sh
qdbus6 org.kde.KWin /KWin org.kde.KWin.supportInformation | grep -A4 '^Decoration'
```

**Plasma 6.6 split Aurorae into two plugins, and every theme in the store
still names the dead one.** `org.kde.kwin.aurorae` is now only the QML
renderer and offers exactly *one* theme — Plastik. All SVG themes moved to
the native `org.kde.kwin.aurorae.v2`. Measured here: 29 themes under `.v2`,
1 under the old id.

KWin still *loads* an SVG theme under the old plugin name, so the desktop
looks entirely correct. But the Window Decorations page matches its list on
the pair `(plugin, theme)`, finds no row with the old plugin, and shows
**nothing selected** — which reads as "window decorations are broken" and is
not. 11 of the 12 global themes installed on this machine ship the old name.

`resolve.aurorae_provider()` detects which plugin serves SVG themes by
looking for `org.kde.kwin.aurorae.v2.so`; `repair.aurorae_plugin()` rewrites
`library` and calls `KWin.reconfigure`. No logout needed, no flicker.

**To see what the decoration KCM actually contains, ask the plugins.** This
is ~30 lines of C++ against the shipped public header
`/usr/include/KDecoration3/kdecoration3/decorationthemeprovider.h`, mirroring
KWin's `DecorationsModel::init()`: `KPluginMetaData::findPlugins("org.kde.kdecoration3")`,
then `KPluginFactory::instantiatePlugin<KDecoration3::DecorationThemeProvider>`
on each, then dump `themes()`. Build with
`g++ $(pkg-config --cflags --libs Qt6Core) -I/usr/include/KF6/KCoreAddons
-I/usr/include/KDecoration3 -lKF6CoreAddons -lkdecorations3`. Plugins that
fail to instantiate (breeze, oxygen, darkly) are normal — they have no theme
list and the model adds them as themselves.

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
- Decoration: `org.kde.kwin.aurorae.v2` / `__aurorae__svg__Layan`, written into
  the `~/.config` user layer (backup: `~/.config/kwinrc.lolkde.bak`)
- Compositor: `gl2`, NVIDIA, Wayland. Blur loaded; `contrast` refuses to load.
- Xwayland scale 1.2 (fractional — expect soft edges on SVG decorations)

## Measuring SVG themes: use QSvgRenderer, nothing else

`librsvg`/`rsvg-convert` disagrees with Qt on real theme files, and an alpha
table produced with it was withdrawn as unreliable. Aurorae v2 renders with
**QSvgRenderer**, so measure with QSvgRenderer. `pkg-config Qt6Svg` is enough;
no PyQt needed. Render `boundsOnElement(id)` into an `ARGB32_Premultiplied`
image at 8× and report mean alpha, covered %, fully-opaque %.

Numbers for Layan, from that method — these are trustworthy:

| element | mean alpha | covered | fully opaque |
|---|---|---|---|
| `decoration-center` | 203/255 (≈80%) | 100% | 0% |
| `decoration-top` | 98 | 82.6% | 0% |
| `mask-center` | 255 | 100% | **100%** |

So Layan's titlebar is designed ~20% see-through with the *entire* titlebar
area declared a blur region. Nothing in the artwork is fully opaque anywhere.

**`#g1000` is noise, not a broken button.** `maximize.svg` and `restore.svg`
carry a stray top-level `<use id="use1002" href="#g1000">` where `g1000` was
never defined, so Qt logs `link #g1000 is undefined!` on every parse. Aurorae
renders buttons by state id, and all five states — `active-center`,
`inactive-center`, `hover-center`, `pressed-center`, `deactivated-center` —
measure ~80% covered with real artwork. Cosmetic log spam only. Deleting the
one `<use>` line silences it; upstream is `github.com/vinceliuice/Layan-kde`.

## Measuring anything on screen under Wayland

**A Wayland client cannot know where it is.** `QWidget::setGeometry()` position
is ignored and `windowHandle()->position()` is meaningless. To crop a
screenshot to a specific window, ask KWin via a script:

```js
for (const w of workspace.windowList())
  if (w.caption.indexOf("marker") >= 0)
    print("GEOM " + w.frameGeometry.x + " " + w.frameGeometry.y
          + " " + w.frameGeometry.width + " " + w.frameGeometry.height);
```
```sh
qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript /path/geom.js uniquename
qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.start
journalctl --user --since "20 sec ago" | grep -o 'GEOM.*'
```

Use a **unique script name each time** — `start()` only runs newly loaded
scripts, so reusing a name silently prints nothing.

**KWin reports LOGICAL pixels; screenshots are DEVICE pixels.** This desktop
is 4268x1200 logical at scale 1.2, i.e. 5120x1440 device. Multiply KWin
geometry by the output scale (`kscreen-doctor -o`) before cropping, or you
will crop a completely different window and conclude something false. This
cost four attempts.

**`w.alpha` does not exist in KWin 6 scripting** — it returns `undefined`, not
false. Do not test surface transparency that way.

**Do not judge translucency against a background of the same colour.** A dark
window over a dark window is not a measurement. Put the probe over the
wallpaper, or measure alpha directly and skip the screenshot.

## Open leads

1. **`contrast` effect** won't load despite `contrastEnabled=true` and a KWin
   reconfigure. Affects panel frostiness only.
2. **`BorderSize` drift.** Layan declares `None`; `~/.config/kwinrc` carries
   `Normal` in the user layer and wins. `audit()` does not compare BorderSize
   at all — it is in the same group as `library`/`theme` and would be a cheap
   addition.
3. The KCM's own previews log `qt.svg: Could not resolve property:
   #linearGradient…` — that is one of the other 28 installed themes, not
   Layan, whose `decoration.svg` has no dangling references. Unidentified.
