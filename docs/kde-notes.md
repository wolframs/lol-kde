# KDE theming: facts worth keeping

Everything here was learned the expensive way, mostly by measurement, and most
of it is not discoverable from documentation. It is separated from `CLAUDE.md`
because it is about **KDE**, not about this project or how to work on it — it
stays true whoever is reading, and it outlives the tool.

`CLAUDE.md` imports this file, so an agent working in this repo gets it
automatically.

---

## KDE architecture facts

**A Global Theme is a manifest of pointers, not a theme.** `contents/defaults`
names up to ten components. If a name does not resolve, KDE substitutes a
default silently — no error, no warning, nothing in System Settings.

The ten, measured across all thirteen look-and-feel packages installed here:

| declared as | component |
|---|---|
| `[kdeglobals][KDE] widgetStyle` | widget style |
| `[kdeglobals][General] ColorScheme` | colour scheme |
| `[kdeglobals][Icons] Theme` | icon theme |
| `[kcminputrc][Mouse] cursorTheme` | cursor theme |
| `[plasmarc][Theme] name` | Plasma style |
| `[KSplash] Theme` | splash screen |
| `[kwinrc][org.kde.kdecoration2] theme` + `library` | window decoration |
| `[kwinrc][WindowSwitcher] LayoutName` | task switcher |
| `[kwinrc][DesktopSwitcher] LayoutName` | desktop switcher — see below |
| `[Wallpaper] Image` | wallpaper |

The splash and the wallpaper are written as **bare** groups in every real
manifest, never as `[ksplashrc][KSplash]` / `[plasmarc][Wallpaper]`.

**KDE's own apply dialog splits these into two classes**, and the split is not
cosmetic. "Appearance settings" — colours, application style, window
decoration, icons, Plasma style, cursors, task switcher, splash — ships with
every box ticked. "Layout settings: Desktop layout" ships **unticked**, and it
is the one that replaces your panels, widgets, their arrangement and the
wallpaper with the theme author's. On the command line that box is
`plasma-apply-lookandfeel --resetLayout`.

That is why the wallpaper is in the table but is not a pointer: its live value
is not in `plasmarc` at all. It is a per-containment `file://` URL inside
`plasma-org.kde.plasma.desktop-appletsrc` —

```
[Containments][1][Wallpaper][org.kde.image][General]
Image=file:///home/…/.local/share/wallpapers/Layan
```

— which is layout state, not configuration.

**`[WindowSwitcher] LayoutName` is read from one group and written to another.**
The look-and-feel applier reads `[kwinrc][WindowSwitcher] LayoutName` out of
the manifest and writes `[kwinrc][TabBox] LayoutName`, which is the only one
KWin reads. Auditing the declared group against the live config in that same
group reports every applied switcher as `unset`, forever. No other pointer
does this.

**Nine of the thirteen themes here declare a task switcher that does not
exist.** They all name `org.kde.breeze.desktop`, Kubuntu's own three included.
Under Plasma 5 a `LayoutName` named a *look-and-feel* package, which supplied
`contents/windowswitcher/WindowSwitcher.qml`. Plasma 6 moved switchers to their
own KPackage type (`KWin/WindowSwitcher`, installed to
`<data dir>/kwin/tabbox/<id>/`, sold as `kwinswitcher.knsrc`) and **no
look-and-feel package on this machine ships a `windowswitcher/` directory any
more** — Breeze included, which is why Breeze itself declares no switcher while
every theme copied from a Plasma 5 template still does.

```
$ kpackagetool6 --type KWin/WindowSwitcher --show org.kde.breeze.desktop
Error: Can't find plugin metadata: org.kde.breeze.desktop
$ kpackagetool6 --type KWin/WindowSwitcher --show big_icons
  Name : Large Icons
  Path : /usr/share/kwin/tabbox/big_icons/
```

`plasma-apply-lookandfeel` copies the dead id into `[TabBox] LayoutName`
without checking, KWin cannot load it and uses its built-in switcher, and
nothing anywhere says so. The user loses nothing — the built-in switcher is
what the line was asking for — so this is not a fault, and `resolve.py` treats
the stock Plasma 5 spellings as `ok`. The id is `KPlugin.Id` in the package's
`metadata.json`, which is not obliged to match the directory name.

**`KWin/DesktopSwitcher` does not exist on Plasma 6.6 at all.** Not "no
packages installed" — the package *structure* is unregistered:

```
$ kpackagetool6 --type KWin/DesktopSwitcher --list
kf.package: Invalid metadata for package structure "KWin/DesktopSwitcher"
Package type "KWin/DesktopSwitcher" not found
```

The applier drops the key rather than writing it anywhere, so there is no live
value to compare against and nothing to install. A theme declaring it is
promising something this Plasma cannot deliver.

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

The no-op is keyed on the **resolved** value, not on what `kdedefaults` holds.
Measured on turn 8: with a `Theme[$d]` tombstone in the user layer, writing
`Theme=Tela` — the same value `kdedefaults` already supplies — *did* land as a
real pin, because the tombstone made the resolved value empty first. So
"matches the theme default" is not the condition; "matches what KDE currently
resolves" is.

**`--notify` fires only when something actually changed.** Two identical
`kwriteconfig6 --notify` writes in a row produce exactly one `ConfigChanged`
signal, confirmed on the bus. A silent no-op write is therefore silent twice
over: nothing stored and nobody told. Never treat a notification as evidence
that a write happened.

**A running daemon merges; it does not dump its in-memory config over yours.**
Measured on turn 8, and it settles the biggest assumption in the restore
design. Foreign keys were appended straight into `~/.config/kwinrc` — one in a
group KWin has never heard of, one inside `[Desktops]`, which KWin owns and
writes — *without* `--notify`, so KWin could not have re-read them. Then KWin
itself was made to write the file (`createDesktop` over D-Bus, which triggers
`VirtualDesktopManager::save()`). It wrote `Id_2`, `Name_2`, `Number=2` and
**preserved both foreign keys**, including the one inside the group it was
rewriting. KConfig re-reads the file at `sync()` and writes back only the keys
it holds dirty.

Two riders, both from the same measurement:

- **A daemon write rewrites the whole file in canonical order.** Keys came back
  alphabetically sorted within the group. Byte-comparing two config files is
  therefore not a reliable change detector; compare parsed keys.
- **Removing a virtual desktop leaves its `[Tiling][<desktop-uuid>][<output-uuid>]`
  groups behind.** Orphaned layout state, never cleaned up. Harmless, but it
  means a create/remove round trip is not byte-neutral — it left residue that
  had to be cleaned by hand.

## `kwriteconfig6 --delete` does not delete. It tombstones.

The design blocker, settled on turn 8, and the answer is the bad one.

`--delete` **never removes a line**. It writes `Key[$d]` — a delete marker that
*blocks inheritance*. Measured on `kdeglobals [Icons] Theme`, which was present
only in `kdedefaults` (`Tela`):

| starting state | after `--delete` | resolves to |
|---|---|---|
| absent from user layer, `Tela` inherited | `Theme[$d]` in user layer | **nothing** |
| pinned `Theme=Tela` in user layer | `Theme[$d]` in user layer | **nothing** |

Both cases produce the same tombstone, and in both the inherited `Tela`
stopped resolving. `--delete` does not mean "revert to inherited"; it means
"shadow whatever is underneath". There is **no `kwriteconfig6` flag that
expresses "make this key absent again"** — the whole option list is
`--file --group --key --type --delete --notify`.

The only route back is to **remove the line from the file directly**, verified:
deleting the `Theme[$d]` line restored resolution to `Tela`. `repair.unpin()`
does this, and it is the one raw file edit in the program.

**Doing that safely does not require inventing a notification.** `unpin()`
writes the *inherited* value through `kwriteconfig6 --notify` first, so every
running client is told — by KConfig's own correctly-typed signal — that the
key resolves to V. Only then is the user-layer line removed, which leaves the
key resolving to the same V from the layer below. No resolved value changes at
step two, so nothing needs announcing. See the hard rule at the top of this
file for why the obvious shortcut is forbidden.

**Two tombstones already exist on this machine** — `konsolerc` and
`khotkeysrc` — so this is not a hypothetical format corner.

**A bare `Key[$d]` line has no `=`, and that broke the parser.** Python's
`configparser` treats a valueless line as an error and abandons the *rest of
the file*, so one tombstone made everything below it invisible. `khotkeysrc`
was being read as 492 keys when it holds 644 — 24% of the file silently
missing from every snapshot and diff taken before turn 8. Fixed with
`allow_no_value=True`, plus `kconfig.split_flags()` so `Key[$d]` is understood
as a deletion of `Key` rather than as a key named `Key[$d]`. Locale variants
(`Name[de_DE]`) carry no `$` and must survive intact.

**`plasma-apply-lookandfeel` deletes your user-layer pins for every key the
theme declares.** Measured on turn 9, directly: `~/.config/kwinrc` held
`[org.kde.kdecoration2] library=org.kde.kwin.aurorae.v2` as a deliberate pin.
Running `plasma-apply-lookandfeel --apply com.github.vinceliuice.Layan`
**removed that line**, and `library` fell back to the dead
`org.kde.kwin.aurorae` from `kdedefaults`. `BorderSize=Normal`, in the same
group, survived — because the look-and-feel package does not declare it.

Three consequences, all load-bearing:

- The repair inside `lol-kde apply` is not a one-time turn-2 fix. It runs and
  is needed on **every** apply, because every apply undoes it.
- Restore's ordering rule (`kdedefaults` first, user layer second) is not a
  nicety. Reversed, it silently destroys every pin restore just wrote.
- **KDE itself un-pins without leaving a tombstone.** `kwriteconfig6 --delete`
  writes `Key[$d]`; this removes the line. The C++ route is almost certainly
  `KConfigGroup::revertToDefault()`, which is the API `repair.unpin()`'s
  two-step emulates from outside. A small compiled helper could call it
  directly — noted as an option, not a need; the two-step is verified working.

**Telling Plasma generations apart: use the Plasma style, not the manifest.**
Two plausible signals were tried on turn 11 and both are useless.
`X-Plasma-APIVersion` is absent from current store entries —
`Gently-Dark-Global-6` does not set it and Layan does — so absence proves
nothing. Install mtimes are worthless here because the old themes arrived in a
bulk copy from the Kubuntu 24.04 system, which reset every one of them to the
copy date. What does work: the **Plasma style** the theme points at. A style
with `metadata.desktop` and no `metadata.json` predates Plasma 5.19, which
dates the theme shipping it. `prune.is_previous_generation()` uses exactly
that and returns `None` rather than guessing when a theme declares no style.

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
  **Applying a global theme does not touch it.** Every component can report
  `ok` while window interiors render a previous theme.
- **`reduce_window_opacity` is the translucency knob.** `translucent_windows=true`
  only enables the mechanism; themes ship it `true` with `reduce_window_opacity=0`
  and render fully opaque. This single integer was the answer to "nothing is ever
  translucent, EVER".
- Themes ship variants that differ *only* by these flags: `Layan` (true/15 after
  our edit, originally true/0) vs `Layan-solid` (false/0).
- Kvantum themes are distributed on **GitHub, not the KDE Store**, so
  `lol-kde install` cannot fetch them.
- **`opaque=` is a per-executable opt-out** in the theme's kvconfig. Layan lists
  17 — vlc, VirtualBox, QtCreator, kdenlive, digikam, several video players.
  Those ignore `reduce_window_opacity` entirely. `doctor -v` names them.
  Kvantum's built-in default is `kscreenlocker, wine`.

**Kvantum's translucency is timing-dependent, and that is why a test app will
not reproduce it.** On Wayland a surface's alpha channel is fixed when the
native window is created, so `WA_TranslucentBackground` must be set *before*
that. Kvantum does it in `Style::setSurfaceFormat()`, called from exactly one
place — `Style::styleHint()` — with the author's own comment
`/* FIXME Why here and nowhere else? */`. Qt creates the native window before
`ensurePolished()` runs (`qwidget.cpp`, `QWidgetPrivate::setVisible`), so
`polish()` is already too late and Kvantum's fallback explicitly vetoes.

A rich app (Dolphin: menus, toolbars, KXmlGuiWindow) triggers `styleHint()`
many times during construction and gets translucency. A bare `QMainWindow`
that is constructed and `show()`n does not — measured here as
`WA_TranslucentBackground` unset, `alphaBufferSize()==0`, palette alpha 255,
for `QWidget`, `QMainWindow` and `QDialog` alike. **A minimal reproducer is
not a valid control for Kvantum behaviour.**

`subApp_` is not a heuristic: it is literally
`applicationName() == "Qt-subapplication"`. Kvantum has no desktop-file check.

**Reported but contradicted by observation:** research surfaced a
`nonIntegerScale` check in Kvantum's `ThemeConfig.cpp` said to disable window
translucency at fractional display scale. This desktop runs at scale **1.2**
and Dolphin is translucent, so that reading is wrong or conditional. Do not
repeat it without re-deriving it.

## Translucency is three independent systems, not one setting

Asked on turn 13 as "is translucency being applied too selectively?". It is
not selective — there are three owners and they do not talk to each other.

| layer | owner | knob |
|---|---|---|
| window decoration | KWin + the Aurorae theme | alpha in the theme's SVG |
| window background | the widget style (here Kvantum) | `translucent_windows` + `reduce_window_opacity` |
| content view | **the application** | its own, per-app |

The third one is the answer to "why is the frame translucent but the inside
solid". Kate's editor, Dolphin's file list and Konsole's terminal display each
paint an opaque background over the window background. **A widget style cannot
reach into a view.** What Kvantum makes translucent is the chrome — menubar,
toolbar, statusbar, sidebars, dialogs, menus, tooltips — and nothing else.

Non-Qt apps (kitty) never see Kvantum at all; they get the decoration from
KWin and everything inside from their own config. Konsole is Qt, but its
terminal transparency lives in the **color scheme**, not the profile and not
the widget style: `[General] Opacity` and `Blur` in a `.colorscheme` file.
The profile only points at the scheme by name.

Konsole's built-in schemes (`Breeze` and friends) are compiled into the
binary — `/usr/share/konsole/` holds only what the distro added. To make a
translucent copy you have to write the whole scheme out. The Breeze values
were confirmed here by sampling a screenshot: background `35,38,39`,
`Color2Intense` `28,220,154`, both exact.

## KWin 6.6 has no `contrast` effect. It was not failing to load.

Carried as a blocker for several sessions — "refuses to load with
`contrastEnabled=true`, root cause unknown". The root cause is that the effect
does not exist:

    qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.listOfEffects   # 53 effects, no `contrast`
    qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.isEffectSupported contrast   # false

Nothing on disk either — no plugin, no `kwin_contrast_config.so`, while blur
has both. The background-contrast effect was folded into blur upstream; blur's
config module now offers only blur strength and noise strength.

`contrastEnabled=true` was a Kubuntu 24.04 / Plasma 5 fossil in `[Plugins]`,
and it was the **only** key in that group — every one of the 53 effects KWin
actually has was running on its default, which is why `blur` was loaded and
enabled without ever being named. Removed on turn 13.

**The general check is worth keeping**: cross-reference every `*Enabled` key in
`kwinrc [Plugins]` against `listOfEffects`. A key naming an effect KWin does
not have is silently ignored, so it reads as a broken feature forever.

## KDE Store / OCS

- Authors declare dependencies **in the description prose**, as pling links.
  That is machine-readable and `lol-kde please` uses it.
- The description and `X-KPackage-Dependencies` **disagree**. Layan's description
  names 4 components; its manifest names 7, and only the manifest mentions the
  cursor theme. Use both.
- **Uploads break HTTP and tar assumptions, and the failures are unhandled by
  default.** Two found on turn 10, both fatal to a whole multi-item install:
  a filename with a **space** (`Gently-Nebula-Noir No Logo.jpg`) goes verbatim
  into the signed download URL and `http.client` raises `InvalidURL` — an
  `HTTPException`, not a `URLError`. And an icon theme with one **absolute
  symlink** among thousands of files makes `tarfile`'s `data` filter raise
  `AbsoluteLinkError` — a `TarError`, not an `OSError`. Neither is in the
  exception family anyone thinks to catch. Encode store URLs; skip unsafe
  archive members individually rather than failing the archive.
- **KDE normalises case when applying a theme.** `Gently-Dark-Global-6`
  declares `widgetStyle=breeze`; `plasma-apply-lookandfeel` writes `Breeze`.
  Qt resolves style names case-insensitively, so a raw comparison reports
  drift on a theme that was just applied cleanly.
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

## Pre-scaled Aurorae variants are broken by construction

WhiteSur ships `WhiteSur-dark`, `_x1.25` and `_x1.5`. The SVG artwork really
is scaled — `decoration-top` measures 66 → 83 → 99px, exactly 1.25x and 1.5x.
The `<theme>rc` holding the layout metrics is **byte-identical** across all
three (`md5 31175c98`): `TitleHeight=16`, `PaddingTop=36`, `ButtonWidth=16`.

Aurorae positions the frame from the rc and draws the artwork at its natural
size, so large art lands in a small frame: the titlebar detaches from the
window body and the buttons sit off the bar. `resolve.aurorae_scale_mismatch()`
catches it by comparing the rc against the unsuffixed sibling; it flags
exactly the five broken variants installed here and nothing else.

Fixing one properly means multiplying every `[Layout]` number by the same
factor. Not done — nobody here uses these themes.

## Where display configuration actually lives

**Plasma 6 stores output scale, mode and position in
`~/.config/kwinoutputconfig.json`.** KWin owns outputs now.
`~/.local/share/kscreen/` is the Plasma 5 location; it still exists on
upgraded systems, still looks authoritative, and is **not written to**.
Backing it up and not the JSON produces a checkpoint that silently captures
nothing. That happened here — see
`~/.lol-kde/checkpoints/turn5-before-scale/GAP.md`.

`kscreen-doctor` applies changes live *and* persists them to the JSON.
Changing a scale does not move the other output: after shrinking DP-1 from
2134 to 2048 logical px, DP-2 stayed anchored at x=2134, leaving an 86px
logical gap between the screens. Set `output.<name>.position.<x>,<y>` too.

`[Xwayland] Scale` in `kwinrc` is separate and does not follow the output
scale. Set it explicitly and `reconfigure`.

**Fractional scale arithmetic matters.** On a 2560x1440 panel:

| scale | logical width | logical height | clean? |
|---|---|---|---|
| 1.2 | 2133.33 → rounded to 2134 | 1200 | **no** — 0.8px horizontal overhang |
| 1.25 | 2048 | 1152 | yes, both axes |
| 1.5 | 1706.67 | 960 | no |
| 2.0 | 1280 | 720 | yes |

At 1.2 the logical grid does not land on physical pixels horizontally but
does vertically, which predicts worse artefacts on vertical edges than
horizontal ones. Moved to 1.25 on turn 5.

## KWin's Debug Console

`class DebugConsole : public QWidget`, constructed inside `kwin_wayland` by
`DBusInterface::showDebugConsole()` and surfaced as an `InternalWindow`. Not
GTK, not a separate process.

It is opaque because KWin's QPA `Window::format()` returns a bare `m_format`
that is never populated with an alpha channel, so `alphaBufferSize()` stays
`-1` — and Kvantum's polish-time gate requires exactly `8`. An internal
window can never satisfy it. QtQuick internals (Overview, task switcher) set
their own surface alpha and bypass QStyle entirely, which is why *those* are
translucent. Structural inference from both codebases, not a cited decision.

**No D-Bus method closes it.** `showDebugConsole()` returns no handle,
`killWindow()` needs an interactive click and no-ops on internal windows
anyway (`InternalWindow::killWindow()` is an empty stub). See KDE Bug 502901.
`InternalWindow::closeWindow()` *is* functional and reachable from a KWin
script:

```js
for (const w of workspace.windowList())
  if (w.caption && w.caption.indexOf("Debug Console") >= 0) w.closeWindow();
```

Written and run here, but it matched nothing — the console was already gone,
so this route is **untested against a live console**. Also note
`w.internal` is `undefined` in KWin 6 scripting, so it cannot be used to
confirm whether `windowList()` includes internal windows at all.

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

