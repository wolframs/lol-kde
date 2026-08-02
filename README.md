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

A Plasma "Global Theme" is not a theme. It is a small text file of pointers to
other components that must already be installed -- up to seven of them:

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
It shells out to KDE's own tools -- `kwriteconfig6`, `kreadconfig6`,
`kpackagetool6`, `plasma-apply-lookandfeel`, `qdbus6` -- and reports what it
cannot find rather than guessing.

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
lol-kde install <theme> --dry-run   # resolve and report, install nothing
lol-kde apply <theme>               # apply, then verify it actually took
lol-kde why                         # what a Global Theme actually is
lol-kde legacy                      # packages using pre-5.19 metadata.desktop
lol-kde legacy --remove             # delete the orphaned ones (never the needed ones)

lol-kde snapshot -m "before I break something"
lol-kde snapshot --explain          # what a snapshot captures, and how much we trust it
lol-kde snapshot --around 'CMD'     # snapshot, run CMD, wait, snapshot, diff
lol-kde snapshots                   # list them
lol-kde diff                        # latest snapshot vs the live system
lol-kde diff A B                    # two snapshots
lol-kde diff A B --changelog        # paste-ready CHANGELOG table
lol-kde history                     # what this tool has done to your machine

lol-kde restore <id>                # print a plan; writes nothing
lol-kde restore <id> --apply        # actually do it, after confirming
lol-kde restore <id> --component icons,decoration

lol-kde prune                       # what is left over from a previous Plasma
lol-kde prune --apply               # move it to quarantine (never deletes)
lol-kde prune --drop Name,Other     # drop named components instead
```

Every command prints a plan and writes nothing unless you pass `--apply`
(`restore`, `prune`), `--remove` (`legacy`) or name a mutating verb outright
(`install`, `apply`, `please`). `-v` after any subcommand shows the paths.

## `lol-kde restore` — put a snapshot back, one key at a time

Restore has the largest blast radius in this program, so a bare
`lol-kde restore <id>` **prints a plan and writes nothing**. Writing needs
`--apply`, which prompts unless you add `--yes`. Snapshot ids are
prefix-matched and typo-prone, and a mistyped id that happens to resolve to a
*different valid snapshot* is the worst outcome available — and silent. The
plan makes that visible for free, and ends with the exact command to proceed.

It does not copy files back. Byte copies in a snapshot are evidence; the
restore mechanism is replay, one `(file, group, key)` at a time, because
restoring `kwinrc` wholesale to fix a window decoration also reverts your
tiling layouts, your virtual desktops and your Xwayland scale.

Three things it will tell you that a file copy cannot:

- **`pin-lost`** — the value is right but comes from the wrong layer. It works
  today and will not survive the next global-theme apply. Not folded into
  `ok`, and not an error either.
- **`stale`** — correct on disk, but no supported writer can tell the running
  session, so it will not take effect until that component restarts. Said out
  loud rather than quietly hoped over.
- **`XDG_CONFIG_DIRS` changed** — the shape of the config cascade is different
  from when the snapshot was taken, so inherited values may resolve
  differently even after a perfect restore. This is usually the single most
  useful line it prints.

`~/.config/kdedefaults/` is never written. Hand-writing that layer is the
canonical way to manufacture a state that never existed, so restore does not:
it replays into `~/.config`, which sits above `kdedefaults` in the cascade and
therefore wins. The cost is honest and reported — if the snapshot's value came
from `kdedefaults` and the live one does not, you get `pin-lost`, meaning the
value is correct now but is a user-layer pin rather than a theme default. To
get the theme layer itself back, re-apply the package:
`plasma-apply-lookandfeel --apply <package>`. Nothing is ever unlinked;
replaced files go to `~/.lol-kde/restores/<ts>/removed/`. There is no
automatic rollback, on purpose: a rollback is itself a restore, run by the
code path that just demonstrated it can fail, at the moment the state is least
understood. On failure it prints the journal path, the pre-restore snapshot id
and the one command that undoes it.

Scope is the components this tool models. It is not a backup system, and a
theme tool that half-implements one is more dangerous than one that declines
to — for that, use Timeshift, btrfs snapshots or etckeeper.

## `lol-kde snapshot` — a capture that can tell when it failed

A checkpoint is only as good as its file list, and a plausible-looking path can
be a decade out of date. This project learned that the expensive way: a
checkpoint taken before a display-scale change backed up
`~/.local/share/kscreen/` — the **Plasma 5** location, still present on upgraded
systems, still non-empty, and never written to. Plasma 6 keeps output scale in
`~/.config/kwinoutputconfig.json`. The capture copied real files, reported
success, and preserved nothing.

So every snapshot ends by running **coverage probes**: read a fact from a live
instrument, then read the same fact back out of the bytes just written.

```
Snapshot 2026-08-02T16-00-31Z-a59a
  79 files, 130,005 bytes  -> ~/.lol-kde/snapshots/2026-08-02T16-00-31Z-a59a
  ok     coverage 13/13 facts verified
```

If a fact is missing, the snapshot says so *and* goes looking for it, so the
manifest can be fixed instead of silently trusted:

```
  GAP  output.DP-1.scale = 1.2
       config/kwinoutputconfig.json was not captured
       found at: ~/.config/kwinoutputconfig.json  [0].data[0].scale
```

Each cascade-resolved probe also checks, via `kconfig.origin()`, that the layer
that actually *wins* was captured. `cursorTheme` resolves from
`kdedefaults/kcminputrc`, so a `~/.config`-only manifest would pass a naive
"the key exists somewhere" test and still be unrestorable.

Snapshots are ~650 KB and take about two seconds. Nothing is ever pruned
automatically.

## `lol-kde diff` — what changed, and does it matter

A line diff of a KDE config is noise: groups reorder, `[$Version] update_info`
churns on every point release. The diff is key-level, and above that semantic:

```
SEMANTIC
  ~ output DP-1  scale    1.25 -> 1.2
      fractional scale

SETTINGS
  ~ config/kwinoutputconfig.json  [0].data[0].scale    1.25 -> 1.2

UNMANIFESTED
  files that changed and are in no manifest entry
  ~ config  spectaclerc
            looks load-bearing
```

`SEMANTIC` reports status transitions, which need different fixes:
`resolved → missing` means the thing is gone (`lol-kde install`), `drift` means
the pointer changed (`lol-kde apply`), and a **silent content change** — same
pointer, same status, different bytes — is how an edit to
`reduce_window_opacity` shows up at all.

`UNMANIFESTED` is the section whose absence cost this project a pre-change
state: files that changed and are in no manifest entry, KDE-shaped names first.
It comes from an mtime sweep that collapses any subtree over 2,000 entries, so
`~/.local/share/icons` (227k files, 1.8 GB) becomes one row without anyone
maintaining a blocklist.

`apply`, `install` and `legacy --remove` snapshot first, always. There is no
flag to disable that; the only thing it could do is destroy the artifact that
makes the operation undoable.

The design, and the measurements that forced it, are in
[`docs/restore-design.md`](docs/restore-design.md). What is built versus
merely unit-tested is tracked in [`ROADMAP.md`](ROADMAP.md); what is claimed
but unverified is in [`docs/open-questions.md`](docs/open-questions.md).

`doctor` compares the live configuration against what the applied theme declares:

```
Applied global theme: Sweet-Ambar-Blue
  drift  Widget style       Oxygen
         theme declares 'kvantum'; changed since
  ok     Colour scheme      SweetAmbarBlue
  ok     Icon theme         candy-icons
  unset  Cursor theme       (not set)
         theme declares 'Sweet-cursors' but nothing set it; KDE default in use
  ok     Plasma style       Sweet-Ambar-Blue
  ok     Splash screen      Sweet-Ambar-Blue
  unset  Window decoration  (not set)
         theme declares 'Sweet-ambar-blue' but nothing set it; KDE default in use

5/7 ok, 2 unset

Repair: lol-kde install Sweet-Ambar-Blue   # fetch missing pieces
        lol-kde apply Sweet-Ambar-Blue     # reset unset/drifted pointers
```

Seven components, not six -- `doctor` prints one row per pointer a global
theme can declare.

### Statuses

| | meaning |
|---|---|
| `ok` | installed and resolving |
| `drift` | resolves, but differs from what the theme declares — something changed it later |
| `unset` | the theme declares this component, but nothing set it; a KDE default is in effect |
| `warn` | present but will not render as intended (see Kvantum, below) |
| `MISS` | the configured value names something not installed; KDE is silently falling back |

## `lol-kde prune` — remove what a previous Plasma left behind

An upgraded machine accumulates global themes built for the previous Plasma
major version, plus the components only they pointed at. `prune` finds them and
moves them out of the way.

```sh
lol-kde prune                    # a plan, and nothing else
lol-kde prune --apply            # move them to quarantine
lol-kde prune --drop Name,Other  # drop named components instead of sweeping
```

**Nothing is deleted.** Removals are *moved* to
`~/.lol-kde/pruned/<id>/`, keeping their path relative to your home directory,
with a `manifest.json` and a `RESTORE.md` whose undo script actually runs. A
pre-prune snapshot is taken first, unconditionally. Deleting the quarantine
directory is a separate decision you make later.

The generation verdict comes from the Plasma style a theme declares: a style
shipping `metadata.desktop` and no `metadata.json` predates Plasma 5.19. The
style is resolved across every data directory the way KDE resolves it, and a
system-provided style is never evidence about a user-installed theme —
distro packaging is not a statement about your theme. A theme with no verdict
either way is listed as undecided and left alone.

Four refusals, and naming a thing explicitly overrides none of them:

| refused when | why |
|---|---|
| it is the applied global theme | whatever its generation |
| the live configuration points at it | you are looking at it right now |
| any installed theme references it — including one under `/usr` | a distro theme may point at something in your home directory |
| it is outside your own data directories, or not owned by you | not ours to move |

References are compared by **resolved path**, not by name. A colour scheme
called `Sweet-Ambar-Blue` lives in `SweetAmbarBlue.colors`, and comparing the
names lets one spelling slip past a refusal the other triggers.

`--drop` exists because **unreferenced is not unwanted**. `Tela-dark` sitting
next to the `Tela` you use is not garbage, so nothing infers that for you;
removing it is a decision you make by name.

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

It then reconciles that list against the package's `X-KPackage-Dependencies`,
because the two disagree: Layan's description names 4 components, its manifest
names 7, and only the manifest mentions the cursor theme. Result is 6/6
resolved from one URL.

`--dry-run` reconciles the same two lists, so the plan is a forecast rather
than a floor:

```sh
$ lol-kde please 1325243 --dry-run
...
Fetching the package to read its manifest (temporary directory, nothing installed) ...
  its manifest declares 7 dependencies, 5 of which the description does not name:
  +  Layan color schemes    Plasma Color Schemes   ~/.local/share/color-schemes
  +  Layan plasma theme     Plasma Themes          kpackagetool6
  +  Layan aurorae theme    Plasma 6 Window Decs   ~/.local/share/aurorae/themes
  +  Layan sddm theme       SDDM Login Themes      /usr/share/sddm/themes  (needs root; will be skipped)
  +  Layan cursors          Cursors                ~/.icons

Dry run: nothing installed. 10 components in total.
```

The manifest is inside the package, so reading it means fetching the package —
into a temporary directory that is then discarded. **The dry run downloads;
it does not install.** `--no-manifest` skips that and reports the description
only, which is the old, lower number.

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

## Where this has actually been run

One machine. Everything in this README that describes behaviour rather than
design was measured on a single Kubuntu-derived system running **Plasma 6.6.5
on Wayland**, with Kvantum as the widget style and one user. The unit tests
(228 of them) run anywhere; the integration facts do not have that backing.

That is not a support boundary — try it wherever you like — it is a statement
about the evidence. Concretely, things that are likely to differ elsewhere:

- **Qt plugin paths.** Style detection globs `/usr/lib/*/qt6/plugins`,
  `/usr/lib64/qt6/plugins` and `/usr/local/lib/qt6/plugins`. On a layout that
  uses none of those (NixOS, Guix) a working widget style reads as missing,
  and the Plasma 6.6 Aurorae check silently finds nothing to report.
- **`kpackagetool6` output is parsed in English.** Under another locale, a
  re-install of an already-present package reports a failure instead of
  "already installed".
- **Plasma 6.6 specifically.** The Aurorae plugin split this tool repairs
  landed in 6.6; on 6.0–6.5 that repair is unnecessary and inert.

If it does something wrong on your machine, that is interesting and worth an
issue — the failure modes above are guesses, not observations.

## Limitations

- SDDM themes install system-wide and need root; `lol-kde` reports them and stops.
- Kvantum themes are usually not on the KDE Store.
- Themes that declare no `X-KPackage-Dependencies` cannot be repaired automatically —
  `check` still tells you exactly what is missing.
- Store downloads are signed and time-limited; a failed download is worth retrying.
- Restore covers the seven theme pointers and the decoration group. Kvantum's
  own config, the Plasma panel layout (`appletsrc`) and the generated
  GTK/xsettingsd bridge files are captured but never written back — see
  `docs/restore-design.md` §8.
- `prune`'s generation verdict needs a theme to declare a Plasma style. Themes
  that declare none are reported as undecided, not swept.

## Licence

MIT.
