# lol-kde

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
git clone https://github.com/<you>/lol-kde ~/Projects/lol-kde
cd ~/Projects/lol-kde
make install          # symlinks bin/lol-kde into ~/.local/bin
```

Or just run `./bin/lol-kde` from the checkout — it needs no install step.

## Use

```sh
lol-kde doctor                      # what is applied right now, and what is broken
lol-kde list                        # installed global themes
lol-kde check <theme>               # resolve one theme's pointers
lol-kde install <theme>             # fetch its missing dependencies from the KDE Store
lol-kde install <theme> --dry-run   # resolve and report, download nothing
lol-kde apply <theme>               # apply, then verify it actually took
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

## Things that are not obvious

**Layers are drawn by different programs.** There is no single "look":

| Layer | Drawn by | Covers |
|---|---|---|
| Window decoration | KWin | Titlebar and borders. Nothing else. |
| Widget style | Qt, in-process | Everything *inside* an app window |
| Colour scheme | palette | Only if the widget style honours it |
| Plasma style | plasmashell | Panel, popups, tray. Never applications. |
| Icons / cursors / fonts / splash | independent | — |

**Kvantum is an engine, not a theme.** Setting `widgetStyle=kvantum` succeeds even
with no Kvantum theme installed; it then renders a flat grey default *and overrides
your colour scheme*. `lol-kde` reports this as `warn` rather than `ok`, because it is
the single most common cause of "I applied the theme and nothing happened".
Kvantum themes are usually distributed on GitHub, not the KDE Store, so `install`
cannot fetch them.

**Colour scheme filenames are not identifiers.** `Sweet-Ambar-Blue` lives in
`SweetAmbarBlue.colors`. Matching on filename produces false negatives; `lol-kde`
matches on the internal `Name=` field, as KDE does.

**Cursors and icons have six search paths**, including the legacy `~/.icons`.
Checking only `~/.local/share/icons` will tell you something is missing when it is not.

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

## Limitations

- SDDM themes install system-wide and need root; `lol-kde` reports them and stops.
- Kvantum themes are usually not on the KDE Store.
- Themes that declare no `X-KPackage-Dependencies` cannot be repaired automatically —
  `check` still tells you exactly what is missing.
- Store downloads are signed and time-limited; a failed download is worth retrying.

## Licence

MIT.
