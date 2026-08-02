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

