# lol-kde

```
┌──────────────────────────────────────────────────────────┐
│  ╻  ┏━┓╻     ╻┏ ╺┳┓┏━╸                                   │
│  ┃  ┃ ┃┃  ╺━╸┣┻┓ ┃┃┣╸                                    │
│  ┗━╸┗━┛┗━╸   ╹ ╹╺┻┛┗━╸                                   │
│                                                          │
│  NOTICE                                                  │
│                                                          │
│  A Global Theme is a list of up to seven things you may  │
│  or may not own.  This program determines which.  We     │
│  regret the necessity.                                   │
└──────────────────────────────────────────────────────────┘
```

**KDE global themes declare their dependencies. Nothing installs them. This does.**

A Plasma "Global Theme" is not a theme. It is a small text file of pointers to six
other components that must already be installed:

```ini
# Sweet-Ambar-Blue/contents/defaults -- the entire functional content
[kdeglobals][KDE]        widgetStyle = kvantum
[kdeglobals][General]    ColorScheme = Sweet-Ambar-Blue
[kdeglobals][Icons]      Theme       = candy-icons
[kcminputrc][Mouse]      cursorTheme = Sweet-cursors
[plasmarc][Theme]        name        = Sweet-Ambar-Blue
[kwinrc][…kdecoration2]  theme       = __aurorae__svg__Sweet-ambar-blue
```

If a pointer does not resolve, KDE **falls back silently** — no error, no warning,
no indication in System Settings. You get a theme that is 60% applied and no way
to tell which 40% failed or why.

Themes even ship a machine-readable dependency list:

```json
"X-KPackage-Dependencies": [
  "kns://xcursor.knsrc/api.kde-look.org/1393084",
  "kns://icons.knsrc/api.kde-look.org/1305251",
  …
]
```

Discover does not read it. `lol-kde` does.

## Install

Python 3.11+, standard library only. No dependencies, no venv, nothing to rot.

```sh
git clone https://github.com/wolframs/lol-kde ~/Projects/lol-kde
cd ~/Projects/lol-kde
make install          # symlinks bin/lol-kde into ~/.local/bin
```

Or just run `./bin/lol-kde` from the checkout — it needs no install step.

## Use

```sh
lol-kde please <store-url>          # install a theme AND everything it needs
lol-kde no-thank-you <store-url>    # resolve and report, install nothing
lol-kde doctor                      # what is applied right now, and what is broken
lol-kde list                        # installed global themes
lol-kde check <theme>               # resolve one theme's pointers
lol-kde install <theme>             # fetch its missing dependencies from the KDE Store
lol-kde install <theme> --dry-run   # resolve and report, download nothing
lol-kde apply <theme>               # apply, then verify it actually took
lol-kde why                         # what a Global Theme actually is
lol-kde legacy                      # packages using pre-5.19 metadata.desktop
lol-kde legacy --remove             # delete the orphaned ones (never the needed ones)
```

`doctor` compares the live configuration against what the applied theme declares:

```
Applied global theme: Sweet-Ambar-Blue
  drift  Widget style       Oxygen
         theme declares 'kvantum'; changed since
  ok     Colour scheme      SweetAmbarBlue
  unset  Cursor theme       (not set)
         theme declares 'Sweet-cursors' but nothing set it; KDE default in use
  unset  Window decoration  (not set)
         theme declares 'Sweet-ambar-blue' but nothing set it; KDE default in use

3/6 ok, 3 unset
```

### Statuses

| | meaning |
|---|---|
| `ok` | installed and resolving |
| `drift` | resolves, but differs from what the theme declares — something changed it later |
| `unset` | the theme declares this component, but nothing set it; a KDE default is in effect |
| `warn` | present but will not render as intended (see Kvantum, below) |
| `MISS` | the configured value names something not installed; KDE is silently falling back |

## `lol-kde please <url>`

Theme authors *do* declare their dependencies. They write them in the description,
in prose, with links, because the packaging format gave them nowhere else:

> For better experience you need this kvantum theme: Layan https://www.pling.com/p/1325246/
> gtk theme: Layan https://www.pling.com/p/1309214/
> Icon theme: Tela icon theme https://www.pling.com/p/1279924/

That is machine-readable enough.

```sh
$ lol-kde please https://www.opendesktop.org/p/1325243

Layan look and feel theme  Global Themes (Plasma 6)
The description names 4 further components:
  +  Layan kvantum theme    Kvantum                 ~/.config/Kvantum
  +  Layan gtk theme        GTK3/4 Themes           ~/.themes
  +  Tela-icon-theme        Full Icon Themes        ~/.local/share/icons
  +  Layan wallpaper        Wallpapers KDE Plasma   ~/.local/share/wallpapers
```

It then reconciles that list against the installed package's
`X-KPackage-Dependencies`, because the two disagree: Layan's description names 4
components, its manifest names 7, and only the manifest mentions the cursor theme.
Result is 6/6 resolved from one URL.

Three things this has to get right, each learned the hard way:

- **The graph is cyclic.** A theme's kvantum dependency links back to the theme.
  Ids are recorded on enqueue, not on visit.
- **One archive is not one package.** Tela ships `Tela`, `Tela-dark` and
  `Tela-light` side by side; wrapping them in one folder puts every icon theme at
  the wrong path. An archive with top-level *files* is a single package and is
  never split.
- **One store entry is not one file.** Layan cursors ships
  `01-Layan-border-cursors`, `02-Layan-cursors` and `03-Layan-white-cursors`.
  The theme wants the third. Fetching download #1 silently installs the wrong
  variant and leaves the component "missing" with no explanation.

## Things that are not obvious

**Layers are drawn by different programs.** There is no single "look":

| Layer | Drawn by | Covers |
|---|---|---|
| Window decoration | KWin | Titlebar and borders. Nothing else. |
| Widget style | Qt, in-process | Everything *inside* an app window |
| Colour scheme | palette | Only if the widget style honours it |
| Plasma style | plasmashell | Panel, popups, tray. Never applications. |
| Icons / cursors / fonts / splash | independent | — |

**Kvantum keeps its own theme selection, in its own config file.** Applying a
global theme does not change it. You can therefore have every component report
`ok` while your window interiors render a *different theme entirely* -- the
global theme sets `widgetStyle=kvantum`, and Kvantum quietly carries on with
whatever was in `~/.config/Kvantum/kvantum.kvconfig`. `lol-kde` now reports that
as `warn` rather than `ok`.

**A Kvantum theme's translucency is a config flag, not artwork.** Entries often
ship two variants that differ by one line:

```ini
Layan/Layan.kvconfig              translucent_windows=true
Layan-solid/Layan-solid.kvconfig  translucent_windows=false
```

Both are in the same store entry. Installing the first download silently gets you
the opaque one. `doctor -v` now prints the flag.

**Kvantum is an engine, not a theme.** Setting `widgetStyle=kvantum` succeeds even
with no Kvantum theme installed; it then renders a flat grey default *and overrides
your colour scheme*. `lol-kde` reports this as `warn` rather than `ok`, because it is
the single most common cause of "I applied the theme and nothing happened".
Kvantum themes are usually distributed on GitHub, not the KDE Store, so `install`
cannot fetch them.

**Plasma 6.6 split Aurorae in two, and every theme in the store still names the
half that no longer has any themes in it.** `org.kde.kwin.aurorae` is now only
the QML renderer, offering exactly one theme -- Plastik. Every SVG theme moved
to `org.kde.kwin.aurorae.v2`.

KWin still *loads* your theme under the old name, so your desktop looks
correct. But System Settings matches its list on the pair (plugin, theme),
finds no such row, and shows **nothing selected at all** -- which looks
precisely like "window decorations are broken on this machine".

```
  warn   Window decoration  Layan
         library=org.kde.kwin.aurorae, but this Plasma serves Aurorae SVG
         themes from org.kde.kwin.aurorae.v2. KWin loads the theme anyway, so
         it looks right; System Settings shows no decoration selected.
```

`lol-kde apply` rewrites it and asks KWin to reload. No logout, no flicker.

**Colour scheme filenames are not identifiers.** `Sweet-Ambar-Blue` lives in
`SweetAmbarBlue.colors`. Matching on filename produces false negatives; `lol-kde`
matches on the internal `Name=` field, as KDE does.

**Cursors and icons have six search paths**, including the legacy `~/.icons`.
Checking only `~/.local/share/icons` will tell you something is missing when it is not.

**A global theme's settings are not in `~/.config/kdeglobals`.** Applying one writes
to a `~/.config/kdedefaults/` layer, so explicit user choices in `~/.config` still
override the theme and "reset to defaults" remains possible. Read only the user
layer and a perfectly applied theme looks entirely unset. `lol-kde` reads the whole
cascade in KDE's own order:

```
/usr/share/<distro>-default-settings/  ->  /etc/xdg  ->  ~/.config/kdedefaults  ->  ~/.config
```

This also explains why `kwriteconfig6` can exit 0 and appear to write nothing: if the
value already matches the inherited default, KConfig does not store it again.

## How installation works

Install targets are not hardcoded. `lol-kde` reads the same `.knsrc` files KDE reads
(`/usr/share/knsrcfiles/*.knsrc`), which declare per-category install directory,
unpacking mode and adoption command. Content comes from the OCS API
(`/ocs/v1/content/data/<id>` for metadata, `/ocs/v1/content/download/<id>/1` for a
signed link).

Archives are unpacked with path-traversal guards (`tarfile` `filter="data"`, explicit
zip member checks). Whether an archive has a single top-level directory is *detected*
rather than trusted, because uploads routinely disagree with their category's
declared `Uncompress` mode.

Existing content is never overwritten without `--force`.

## Legacy metadata

Plasma styles that ship `metadata.desktop` without `metadata.json` predate Plasma
5.19. They still load, but the *live reload* path through them is poorly exercised:
plasmashell has been observed aborting inside `KSvg::FrameSvg::mask()` while
reapplying one. The crash is self-healing (systemd restarts it) and settings
survive, but `apply` now warns first, and logging out avoids it entirely.

`lol-kde legacy` lists them. `--remove` deletes only packages that are all three of:
under your own data dir, not currently applied, and **not referenced by any other
installed global theme**. A style named `Sweet` is needed by the global theme named
`Sweet` -- those are two different packages, and deleting the first breaks the second.

Aurorae window decorations are deliberately excluded: `metadata.desktop` is their
normal format, not a legacy marker.

## Limitations

- SDDM themes install system-wide and need root; `lol-kde` reports them and stops.
- Kvantum themes are usually not on the KDE Store.
- Themes that declare no `X-KPackage-Dependencies` cannot be repaired automatically —
  `check` still tells you exactly what is missing.
- Store downloads are signed and time-limited; a failed download is worth retrying.

## Licence

MIT.
