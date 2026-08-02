# How it works, and what KDE does that makes it necessary

The [README](../README.md) says what `lol-kde` is for. This says why it has to
work the way it does. Every item here cost somebody an evening.

For the raw measured facts, including the ones that only matter if you are
modifying this tool, see [`kde-notes.md`](kde-notes.md).

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

**The task switcher is declared in one group and read from another.** A theme
writes `[kwinrc][WindowSwitcher] LayoutName`; `plasma-apply-lookandfeel` reads
that and writes `[kwinrc][TabBox] LayoutName`, which is the only one KWin
consults. No other pointer does this. Comparing the declared group against the
live config in that same group reports every applied switcher as `unset`
forever, so `lol-kde` keeps an explicit declared-to-live map
(`resolve.LIVE_POINTERS`) and `restore` replays the key KWin reads. Confirmed
by disassembling `libklookandfeel.so.6.6.5`, not by reading a live config file:
a `[TabBox]` line there can be residue from an earlier theme, because KConfig
writes are additive.

**Most themes declare a task switcher that does not exist, and that is fine.**
Nine of the fourteen global themes installed on the development
machine name `org.kde.breeze.desktop` here — Kubuntu's own three included.
Under Plasma 5 that named a *look-and-feel* package, which shipped
`contents/windowswitcher/WindowSwitcher.qml`. Plasma 6 **added** a
`KWin/WindowSwitcher` package type but did not retire that path — `libkwin.so.6`
still carries the literal, so a theme shipping the directory still works. What
changed is the payload: no look-and-feel package carries one any more, Breeze
included. `kpackagetool6 --type KWin/WindowSwitcher --show
org.kde.breeze.desktop` answers "Can't find plugin metadata", and KWin looks,
finds nothing, and falls back.

The applier copies the dead id anyway and KWin falls back to its built-in
switcher — which is what the line was asking for, so nothing is lost.
`lol-kde` reports the stock Plasma 5 spellings `ok` rather than `MISS`,
because a permanent red mark on almost every theme in the store, for something
the user cannot fix and has not lost, is worse than silence. The explanation is
there under `-v`. A name that is neither a stock spelling nor an installed
layout is a real miss, and is fetchable from `kwinswitcher.knsrc`.

**The desktop switcher has no consumer on Plasma 6 at all.** Not "no packages
installed" — `kpackagetool6 --type KWin/DesktopSwitcher --list` reports the
package *structure* as unregistered, and the applier drops the key instead of
writing it anywhere. There is no live value, so `lol-kde` never reports it
`unset` (which would mean "something should have set this"). An exotic value
gets `warn` and says plainly that there is nothing to install.

**`apply` never resets your desktop layout.** KDE's own dialog splits a global
theme into "Appearance settings" — colours, application style, window
decoration, icons, Plasma style, cursors, task switcher, splash, every box
ticked — and "Layout settings: Desktop layout", unticked. The second replaces
your panels, widgets, their arrangement and the wallpaper with the theme
author's. On the command line the equivalent is `plasma-apply-lookandfeel
--resetLayout` — an inference from both binaries linking `libklookandfeel.so.6`
and routing through `KLookAndFeelManager::save(package, ContentFlags)`, not
something any readable artifact states outright.

`lol-kde` does not pass it and offers no flag that would. That is deliberate
rather than an omission: it is the one part of applying a theme that no
snapshot here can put back (`appletsrc` is captured and never written —
[`restore-design.md`](restore-design.md) §8), and a prompt you can say yes to
is a thing that gets said yes to. A unit test scans every module to keep the
flag out. Use System Settings if you actually want the author's panels.

The same split is why the wallpaper is the one declarable component `doctor`
does not audit: its live value is a per-containment `file://` URL inside
`plasma-org.kde.plasma.desktop-appletsrc`, which is layout state. `prune`
still tracks it, so removing a theme cannot quarantine the image your desktop
is currently painting.

**Cursors and icons have several search paths**, including the legacy
`~/.icons`, and how many depends on `XDG_DATA_DIRS`. Checking only
`~/.local/share/icons` will tell you something is missing when it is not.

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

Whether an archive has a single top-level directory is *detected* rather than
trusted, because uploads routinely disagree with their category's declared
`Uncompress` mode. Existing content is never overwritten without `--force`.

Everything past this point arrives from a third party, so:

- **Archive members** are filtered per-member with `tarfile.data_filter` and
  explicit zip member checks. Per-member rather than `filter="data"` wholesale,
  because one bad symlink in a 3,000-file icon theme should cost you that
  symlink, not the theme — and certainly not the other eighteen dependencies.
  Skipped entries are counted and reported, never silently dropped.
- **Names** — the store entry's title, and the attachment filename — are
  reduced to a single path component before they touch the filesystem, and the
  result is re-checked to be a direct child of its target. Both, not either: a
  title of `..` was once enough to make `--force` empty `~/.local/share`.
- **Download links** must be `https`, and bodies are capped at 512 MiB.
- **`metadata.json` is an upload too**, so its shape is validated rather than
  assumed.

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

