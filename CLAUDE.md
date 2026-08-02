# lol-kde — working notes

Everything here was learned the expensive way in one session. Read it before
theorising about KDE theming; most of it is not discoverable from documentation
and several items cost hours.

## Keep the record files current

Every change to Wolfram's **live configuration** — not just repo commits —
goes in `CHANGELOG.md` with the old value, the new value, the backup path and
the exact revert command. Time is counted in **user turns**, not dates.
Back up before writing; record the backup path in the same entry.

Three more files carry work that would otherwise live in someone's memory.
Keeping them current is not optional:

| file | holds |
|---|---|
| `ROADMAP.md` | what is built, deferred, blocked. Anything deferred gets a row **and** a design note, or it does not get deferred |
| `docs/open-questions.md` | every claim asserted but unverified, each with the one command that settles it. Move answers into this file's facts and delete the row |
| `docs/restore-design.md` | the restore design, executable without re-deriving it |

`lol-kde snapshot` before changing anything, and `lol-kde diff --changelog`
emits the CHANGELOG row for you. The turn number is still yours to fill in.

## Hard rules — not advice, not "be careful"

**Never hand-emit a KConfig notification on the live session bus.** No
`gdbus emit`, no `dbus-send`, no generic emitter, for
`org.kde.kconfig.notify.ConfigChanged` or any other internal KDE signal.
Use `kwriteconfig6 --notify`, or a helper linked against KConfig itself.

On 2026-08-02 this destroyed Wolfram's session and every application in it.
A `gdbus emit` of `ConfigChanged` with `a{sas}` where KConfig sends `a{saay}`
made every KDE/Qt client on the bus allocate 4–6 GiB within seconds; the kernel
killed `kwin_wayland` and SDDM returned to the login screen. Full postmortem:
**[`docs/incident-2026-08-02-kconfig-oom.md`](docs/incident-2026-08-02-kconfig-oom.md)**.

The trap is specifically that the mistake looks *correctable*. Having watched
the real signal go by, fixing the type and re-sending is the obvious next move
and it is the same error again. Observing a signal does not make replaying it
safe. There is no signature careful enough; the rule is the emitter, not the
payload. `tests/test_lolkde.py::TestNoLiveBusEmission` fails the build if this
combination reappears anywhere in the repo.

**Protocol experiments go on an isolated bus.** `dbus-run-session` is enough to
check a wire signature. Receiver behaviour needs a nested or disposable Plasma
session, or a VM — never the primary desktop. `docs/dbus-harness.md` has the
harness.

**A broadcast cannot be undone by cleanup.** Backups, snapshots, read-backs and
`trap` all worked perfectly during that incident and none of them helped,
because the signal had already reached every listener. Reversibility is a
property of *file* writes. Before anything that puts a message on the session
bus, the question is not "can I undo this" — it is "may this be sent at all".

**Watch memory across a live KDE action.** For any approved live test, sample
before and for ~15 s after, and abort on multi-process growth:

```sh
ps -C kwin_wayland -C plasmashell -C kded6 -C kwalletd6 -o pid,comm,rss && free -h
```

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

## Machine state as of 2026-08-02 (end of turn 7)

Do not trust this section blind — run `lol-kde doctor -v` and
`lol-kde diff` first. It is a starting point, not a source of truth.

- Applied: `com.github.vinceliuice.Layan`, `7/7 ok`
- Kvantum: theme `Layan`, `reduce_window_opacity=15` (backup `.bak` has `0`)
- Window translucency: **working**, confirmed visually
- Decoration: `org.kde.kwin.aurorae.v2` pinned in `~/.config/kwinrc`;
  `theme=__aurorae__svg__Layan` is *inherited* from `kdedefaults`, not pinned —
  the write no-opped and that is fine (backup: `~/.config/kwinrc.lolkde.bak`)
- Displays: two 2560x1440 at scale **1.25** (2048x1152 logical each, exact on
  both axes), DP-2 at x=2048. `[Xwayland] Scale=1.25`
- Compositor: `gl2`, NVIDIA, Wayland. Blur loaded; `contrast` refuses to load.
- `~/.lol-kde/`: `snapshots/` (several, all 13/13 verified),
  `journal.jsonl`, and the hand-made `checkpoints/turn5-*` including `GAP.md`

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

## Snapshots: things measured while building the capture

**`kwriteconfig6` exits 0 and writes nothing when the value already matches an
inherited default — and this repo walked into it.** On turn 2
`repair.aurorae_plugin()` wrote `theme=__aurorae__svg__Layan` into
`~/.config/kwinrc`, reported success, and the key never appeared. The `library`
pin (which differs from the inherited value) did land, so nothing broke. Never
trust the exit code: `repair.write()` now returns `WROTE` / `INHERITED` /
`UNCHANGED` / `FAILED` from a two-level read-back — did it resolve, and did it
land in the layer we aimed at.

**Bounded walks, measured on this machine.** A full walk of
`~/.local/share/icons` (227k files, 1.8 GB) took a snapshot from 2.5s to 11.3s.
Both the sweep and the package inventory are now capped. The sweep collapses
any top-level subtree over 2,000 entries in a *single* pass — the earlier
version counted the subtree and then walked it again to roll it up.

**Never record a walk-order-dependent number in a snapshot.** The first
collapsed-subtree row included `files_seen`, which depends on where the budget
trips, so every collapsed subtree compared as changed between two snapshots of
an unchanged tree. Collapsed rows now carry the directory's own mtime and
nothing else.

**`diff --live` captures the live system through `capture(into=...)`**, the same
code path as a stored snapshot. Reading live config directly on one side and
captured bytes on the other would compare two different things.

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
