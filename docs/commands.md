# Command reference

The short version is in the [README](../README.md). This is the long
version: what each command does, what it refuses to do, and why.

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

## `LOL_KDE_HOME`

Everything this tool keeps about itself — snapshots, restore records, the
quarantine, `journal.jsonl` — lives under `~/.lol-kde`. Set `LOL_KDE_HOME` to
move it.

Its main use is keeping a run out of your real history: the test suite sets it
for the whole session, because without it every test that drives a mutating
command appends to the journal you read when something breaks. It is also the
honest way to try `prune --apply` or `restore --apply` without the attempt
becoming part of the record.

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
[`restore-design.md`](restore-design.md). What is built versus
merely unit-tested is tracked in [`ROADMAP.md`](../ROADMAP.md); what is claimed
but unverified is in [`open-questions.md`](open-questions.md).

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
  ok     Task switcher      org.kde.breeze.desktop
  ok     Desktop switcher   org.kde.breeze.desktop
  unset  Window decoration  (not set)
         theme declares 'Sweet-ambar-blue' but nothing set it; KDE default in use

7/9 ok, 2 unset

Repair: lol-kde install Sweet-Ambar-Blue   # fetch missing pieces
        lol-kde apply Sweet-Ambar-Blue     # reset unset/drifted pointers
```

Nine pointers, and ten things a `contents/defaults` can name. A row appears
only where a pointer is declared or live, so the printed count varies by theme.
The number is computed from the pointer tables rather than written down,
because it has now drifted twice: the banner said "six" while `apply` verified
seven, and then "seven" while a manifest could name ten.

The tenth is the **wallpaper**, and it belongs to `prune`, not to `doctor`. Its
live value is not a pointer -- it is a per-containment `file://` URL inside
`plasma-org.kde.plasma.desktop-appletsrc`, which is layout state this tool
captures and never writes back. KDE draws the same line, filing the wallpaper
under "Desktop layout" in its own apply dialog rather than under appearance.

The two **switchers** are new as of 2026-08-03 and are worth knowing about:

- `[WindowSwitcher] LayoutName` is the only pointer read from a different group
  than the theme declares it in. `plasma-apply-lookandfeel` reads
  `[WindowSwitcher]` from the manifest and writes `[TabBox]`, which is the only
  one KWin reads. `doctor` compares against `[TabBox]`; `restore` replays it.
- Nine of the fourteen themes installed here declare `org.kde.breeze.desktop`,
  which resolves to no package at all -- it is a Plasma 5 spelling naming a
  global theme, and KWin still looks inside one for a `windowswitcher/`
  directory. None ship it any more, so KWin's built-in switcher is what you
  get, which is exactly what the line was asking for.
  Reported `ok`, with the explanation under `-v`. A name that is neither a
  stock spelling nor an installed layout reports `MISS`, and is fetchable:
  `kwinswitcher.knsrc`.
- `[DesktopSwitcher] LayoutName` has no consumer on Plasma 6 -- the
  `KWin/DesktopSwitcher` package type is unregistered and the applier drops the
  key. It is never reported `unset`, because nothing could have set it. An
  exotic value gets `warn` and says there is nothing to install.

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
The description names 4 further packages:
  +  Layan kvantum theme    Kvantum                 ~/.config/Kvantum
  +  Layan gtk theme        GTK3/4 Themes           ~/.themes
  +  Tela-icon-theme        Full Icon Themes        ~/.local/share/icons
  +  Layan wallpaper        Wallpapers KDE Plasma   ~/.local/share/wallpapers
```

It then reconciles that list against the package's `X-KPackage-Dependencies`,
because the two disagree: Layan's description names 4 packages, its manifest
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

Dry run: nothing installed. 10 packages in total.
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

