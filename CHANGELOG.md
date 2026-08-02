# Changelog

Two kinds of change get recorded here, because both need undoing sometimes:

- **repo** — commits in this project
- **machine** — edits made to Wolfram's live KDE configuration, with the
  backup path and the exact command that reverts it

Time is measured in **user turns**, not dates. A turn is one message from
Wolfram. Turn 1 is the first message after the context compaction on
2026-08-02; everything before it is "session 1".

---

## Turn 15 — three reviews, and a README that is true

### repo

No machine changes. Three subagent reviews (store/install security, the
destructive paths, and a fresh-eyes pass) found 20-odd items across three
commits; 13 are fixed. **228 tests.**

### Reports that were inaccurate

Fixed because an inaccurate report sends whoever reads it — person or agent —
after the wrong thing, which is its own cost even when no data moves.

- `restore.Lock` took an **empty lock file** silently, which is exactly what a
  process killed between `O_CREAT` and the pid write leaves behind. A second
  run would unlink a live run's lock and both would believe they held it. Now
  refused with `--break-lock` named. The pid is also written and fsynced
  immediately, shrinking the window that produces the state at all.
- The `Lock` docstring always claimed "a restore racing a snapshot is a state
  nobody can reconstruct". Only `cmd_restore` took it, so that protection did
  not exist for any pairing except restore-vs-restore. `cmd_prune` and
  `cmd_snapshot` take it now.
- `unpin()`'s failure path reported only the exception, reading as "nothing was
  written" — while step 1 may already have pinned a value the user never had.
  It now reports what the user layer actually holds and what it held before.
- Snapshot coverage counted only probes that had something to check, so `8/8`
  on a sparse machine looked identical to `13/13` here. `skipped` and
  `possible` are carried alongside and printed when non-zero.

### README

It said **"Restore is designed but not built"** — false on all three clauses,
and it was the only link the README offered to `ROADMAP.md` and
`open-questions.md`. A reader met the feature section, then a paragraph saying
the feature did not exist.

Also corrected, each verified against the source rather than assumed:

| claim | reality |
|---|---|
| `kdedefaults` "is re-derived by re-applying the look-and-feel package" | `plasma-apply-lookandfeel` appears nowhere in `restore.py` or `repair.py`. Restore replays into `~/.config` and reports `pin-lost`. Now says so, and names the command that does re-derive it |
| `doctor` sample showing `3/6 ok` from four rows | seven components, and the `Repair:` block it always prints |
| `kwinoutputconfig.json` as the `UNMANIFESTED` example | it is a manifest entry, so it can never appear there |
| "six search paths" for icons | derived from `XDG_DATA_DIRS`, not a constant |
| snapshots "~500 KB" | 653 KB mean across the 25 on this machine |
| `prune` | undocumented entirely, despite moving gigabytes. Now has its own section |

New: a **"Where this has actually been run"** section. Not a support boundary —
a statement about evidence. One machine, Plasma 6.6.5 Wayland, Kvantum, one
user; unit tests run anywhere, integration facts do not have that backing. It
names the three things most likely to differ elsewhere (Qt plugin paths,
English `kpackagetool6` parsing, Plasma 6.6-specific Aurorae repair) and says
outright that those are guesses rather than observations.

Verified mechanically afterwards: every verb and flag the README shows exists,
no subcommand is undocumented.

### Then cut it in half

518 lines -> **233**. `docs/` exists for the long version, so:

| moved to | what |
|---|---|
| `docs/commands.md` (264) | the full per-command sections: restore, snapshot, diff, prune, please |
| `docs/how-it-works.md` (128) | "things that are not obvious", installation internals, legacy metadata |

What the README keeps is the pitch, the command list, one `doctor` sample, a
five-bullet summary of why KDE theming needs a tool, the safety properties, the
single-machine scope statement, and an index of everything else. Each of the
five bullets links to the section that used to be inline.

The tone is deliberate. The program exists because a Global Theme is a list of
things you may not own and nothing in the desktop checks — so the README says
"it should not need to exist" and then explains, at length, exactly why it
does. Links re-checked after the move; command/flag coverage re-verified
mechanically across both files.

---

## Turn 14 — `please --dry-run` stops under-reporting

### repo

`bb8fe57..` — no machine changes this turn.

The dry run listed only what a theme's *description* links to. The manifest
(`X-KPackage-Dependencies`) lives inside the package, and nothing had unpacked
the package at plan time, so the plan was a floor. Measured live against Layan
(`1325243`):

| | components in the plan |
|---|---|
| before | **5** |
| after | **10** |

The five it could not see: colorschemes, plasma-themes, aurorae, sddmtheme,
xcursor. A preview that under-reports scope is worse than no preview, which is
why this went ahead of the public-repo review.

- `manifest.find_metadata()` / `manifest.dependencies_in_tree()` — read a
  manifest out of an extracted tree. Searches one level deep and no further:
  a `metadata.json` three levels down belongs to something else.
- `install.peek_dependencies()` — fetch the package to a temporary directory,
  unpack, read, discard. Nothing is installed and nothing is written outside
  the temporary directory; there is a test that asserts exactly that.
- `--no-manifest` on `please` and `no-thank-you` keeps the old behaviour.
- `--dry-run`'s help no longer says "download nothing", because it does
  download. It says "install nothing", which is what is actually true.

### Where the manifest states a fact, do not re-derive it

First cut routed each manifest dependency by looking the id up on the store and
reading its category. Layan's SDDM theme came out as `? unknown content type`,
because that category has no `xdg_type` and no numeric id we map. A real run
was always right — `install_dependency` calls `knsrc.load(dependency.knsrc)`,
and the manifest says `kns://sddmtheme.knsrc/...` outright.

Caught by comparing the live dry run against the live install rather than by a
test, which is the only reason it was caught at all. The plan now routes
through the declared knsrc and reports `needs root; will be skipped` for it.

191 tests.

---

## Turn 13 — terminal translucency, and a blocker that was never a blocker

### machine

Backups: `~/.lol-kde/backups/20260802T182930Z/`.
Snapshot before the `kwinrc` edit: `2026-08-02T18-31-41Z-846b` (13/13 facts).

| what | change | revert |
|---|---|---|
| `~/.config/kitty/kitty.conf` | appended `background_opacity 0.85`, `background_blur 1`, `dynamic_background_opacity yes` | `cp ~/.lol-kde/backups/20260802T182930Z/kitty.conf ~/.config/kitty/kitty.conf` |
| `~/.local/share/konsole/BreezeTranslucent.colorscheme` | **new** — Breeze with `Opacity=0.85`, `Blur=true` | `rm ~/.local/share/konsole/BreezeTranslucent.colorscheme` |
| `~/.local/share/konsole/Translucent.profile` | **new** — `kubuntu.profile` verbatim plus `ColorScheme=BreezeTranslucent` | `rm ~/.local/share/konsole/Translucent.profile` |
| `~/.config/konsolerc` | `[Desktop Entry] DefaultProfile=Translucent.profile` | `cp ~/.lol-kde/backups/20260802T182930Z/konsolerc ~/.config/konsolerc` |
| `~/.config/kwinrc` | removed `[Plugins] contrastEnabled=true` | `kwriteconfig6 --file kwinrc --group Plugins --key contrastEnabled true` |

Neither terminal change is visible yet — both need a fresh process, and one of
the processes is this session's terminal. Logged in `docs/open-questions.md`
rather than reported as done.

### `kwriteconfig6` dropped a tombstone as a side effect

Writing `DefaultProfile` rewrote `konsolerc` and silently discarded a
`State[$d]` line that had been sitting in `[MainWindow]`. Nothing inherits
`State` — there is no `konsolerc` in any lower layer on this machine — so the
marker was shadowing nothing and the drop changes no resolved value. Recorded
because it is the first time a KConfig write has been observed **removing** a
tombstone rather than writing one.

### The `contrast` effect does not exist

`isEffectSupported contrast` returns false and `listOfEffects` names 53
effects, none of them `contrast`; there is no plugin on disk. So the ROADMAP
blocker was a Plasma 5 fossil being ignored, not an effect failing to load.
Detail in `CLAUDE.md`; the row is off the ROADMAP.

It was the only key in `[Plugins]`, which means every effect on this desktop —
including the `blur` that translucency depends on — is running on its default.
The group header is still there, empty: `repair.unpin()` removes lines, not
sections. Known residue, documented in `docs/restore-design.md`.

### Breeze's palette, recovered by measurement

Konsole's built-in colour schemes are compiled into the binary, so there was no
`Breeze.colorscheme` to copy for the translucent variant. The values were
written from source knowledge and then **checked against the screenshot**
Wolfram pasted: background sampled `(35,38,39)`, the prompt's bright green
`(28,220,154)` — both exactly the expected Breeze entries. The same sample also
turned up `(0,255,0)`, which is `kubuntu.profile`'s `CustomCursorColor`, and so
independently confirmed which profile was active.

### A correction

Reported last turn that `DefaultProfile=kubuntu.profile` was a dangling
pointer because `~/.local/share/konsole/` is empty. It is not — the profile
lives at `/usr/share/konsole/kubuntu.profile` and resolves through
`$XDG_DATA_DIRS`. Konsole was working exactly as configured. The profile
change above is a preference, not a repair.

---

## Turn 12 — the Slot icon themes

### machine

| what | change | revert |
|---|---|---|
| 4 icon themes dropped | `Slot-Beauty-Dark-Icons-V-2`, `Slot-Beauty-Light-Icons`, `Slot-Nord-Dark-Colorize-Icons`, `Slot-Spectrum-Dark-Icons` — **621 MB apparent, 786 MB on disk** | `~/.lol-kde/pruned/2026-08-02T18-00-07Z/RESTORE.md` |

`Slot-Dark-Icons` was **refused**: `Beauty-Color-Global-6`, one of the modern
themes kept on turn 11, points at it. Named-but-in-use is still a refusal —
naming a thing is permission, not an override.

`~/.lol-kde/pruned/` held 899 MB across two batches, and Wolfram cleared it at
the end of this turn: **`rm -rf ~/.lol-kde/pruned`**, 309 GB → 310 GB free.
Both revert paths above are therefore **gone** — the 37 packages are now only
recoverable by re-downloading them from the store.

`snapshots/`, `restores/`, `checkpoints/` and `journal.jsonl` are siblings of
`pruned/`, not children, so all 22 snapshots and the journal survived. Checked
before running it rather than after.

### Icon themes are not Plasma-generation-specific

Wolfram asked, and he is right. They are freedesktop `index.theme` packages
with no Plasma version dependency; the only occurrences of "Plasma" across all
25 installed themes are *comments* (`# KDE/Plasma Stuff`, "Icon(s) for Plasma
theme/System Tray"). So none of that 1.8 GB was legacy in the sense turn 11
was pruning, and `prune`'s refusal to touch it was correct.

**Two size corrections.** The figure quoted during turn 11 was 1.6 GB, then
1.8 GB — both from block-based `du` at different scopes. The honest pair is
**1.2 GB apparent, 1.8 GB on disk**, and the 600 MB gap is block overhead
across **532,023 inodes** (224,791 files + 307,232 symlinks). Icon themes are
pathologically many tiny files, which is the same property that took a
snapshot from 2.5 s to 11.3 s and forced the bounded walk.

The Slot family alone was 781 MB and 221,227 files — two thirds of the total
size and 42% of the inodes, across five themes, one of which is in use.

### repo

- `prune --drop NAME[,NAME...]` — the deliberate escape hatch from `build()`'s
  rule that unreferenced content is left alone. It still refuses anything live
  or referenced by an installed theme, including system packages under `/usr`.
  A test asserts `build()` keeps ignoring what only `--drop` will take, so the
  "unreferenced is not unwanted" rule cannot erode: `Tela-dark` sits next to
  the `Tela` in use and must survive a generation sweep.
- 170 → 174 tests.

---

## Turn 11 — back to Layan, and the Plasma 5 sweep

### machine

| what | change | revert |
|---|---|---|
| global theme | `Gently-Dark-Global-6` → `com.github.vinceliuice.Layan`, `7/7 ok` | `lol-kde apply Gently-Dark-Global-6` — but Gently is now in quarantine |
| **33 packages pruned** | 7 previous-generation global themes, 23 components only they referenced, 2 orphaned legacy Plasma styles. **80 MB** | `~/.lol-kde/pruned/2026-08-02T17-49-58Z/RESTORE.md` — everything was *moved*, nothing deleted |

Removed themes: `Ant-Dark`, `Gently-Dark-Global-6`, `Stone`, `Sweet`,
`Sweet-Ambar-Blue`, `Sweet-Mars`, `com.github.yeyushengfan258.WinSur-dark`.

`lol-kde legacy` now reports **"No packages using legacy metadata.desktop"**
for the first time. Snapshot `…-4067` taken automatically before the move.

Wolfram chose the conservative scope: only previous-generation themes and
what they exclusively own. **Left alone deliberately:** ~1.6 GB of icon
themes, Aurorae decorations and one cursor set that no installed global theme
references — they may well be things picked by hand in System Settings, and
"unreferenced" is not "unwanted". Also left: `Gently-Splash-6`, a splash-only
package orphaned by Gently's removal but modern, and `org.magpie.nostrum.desktop`.

The sharing rule earned its place on the first run: `Tela` is referenced by
both `Stone` (removed) and `Layan` (applied), and was correctly held back.

### repo

- **`lolkde/prune.py` + `lol-kde prune`** — plan by default, `--apply` to act.
  A component goes only if no surviving theme references it *and* it is not
  live. Removals are **moved** to `~/.lol-kde/pruned/<ts>/`, path-preserved,
  with a manifest and a `RESTORE.md`. Quarantining a gigabyte costs no extra
  disk, unlike `legacy --remove`'s `rmtree`.
- **How to tell the generations apart**, having tried and discarded two
  plausible signals: `X-Plasma-APIVersion` is absent from current store
  entries (`Gently-Dark-Global-6` does not set it, Layan does), and install
  dates are worthless because these came across in a bulk copy that reset
  every mtime. What works is the **Plasma style** a theme points at: no
  `metadata.json` means pre-5.19, which dates the theme that ships it.
- The bulk undo snippet in `RESTORE.md` shipped once as a shell loop whose
  body was `:` — it looked like a restore and did nothing. Now a real script,
  idempotent, and a test runs it for real and checks the files come back.
- 160 → 170 tests.

### not done

The titlebar-icon antialiasing Wolfram asked about first. Diagnosed but not
fixed, because he decided Gently was not worth the effort. The finding, kept
because it generalises: it is **not** an antialiasing setting — AA is working,
125 distinct edge levels in the screenshot. `ButtonWidth=22` at display scale
1.25 lands on **27.5 device pixels**, and measuring with `QSvgRenderer` (the
engine Aurorae v2 actually uses) showed that size is a trough for two of the
three glyphs: ~43% of touched pixels fully covered, against ~58% at 35 device
px. Measured button pitch was 37 then 40 device px for evenly-spaced buttons,
which is the same fractional layout showing up directly.

**A correction worth keeping:** the first reading was that crispness peaks at
integer multiples of the artwork's natural 26px. That holds for `minimize` and
not for the other two — the peaks are per-glyph, set by where each glyph's
strokes fall. The recommendation stands only on the measured average.

---

## Turn 10 — Gently, and three crashes on the way to it

Wolfram asked for Gently to be found on opendesktop.org, installed with
`please`, and applied if it reported no errors. It took three attempts,
because each one hit a different unhandled failure. The third was clean.

### machine

| what | change | revert |
|---|---|---|
| global theme | `com.github.vinceliuice.Layan` → **`Gently-Dark-Global-6`**, applied and verified `7/7 ok` | `lol-kde apply com.github.vinceliuice.Layan` |
| installed packages | `Gently-Dark-Global-6` and 19 declared components: Gently Plasma style (the one turn 9 deleted, now back), Kvantum theme, colour scheme, splash, two Aurorae decorations, four icon themes, GTK theme, six wallpapers | `lol-kde legacy` will not remove these; delete by hand under `~/.local/share` |
| decoration pin | rewritten again by `apply`'s repair, exactly as turn 9 predicted it would be on every apply | — |

Snapshots: `…-3ad0` (manual, pre-install) plus automatic ones from each
`please` attempt and the `apply`.

`plasmashell` survived the apply — same pid, nothing in the journal. Worth
recording because Gently's Plasma style **is** legacy `metadata.desktop`, so
this was the documented `KSvg::FrameSvg::mask()` crash path and it did not
fire. One observation, not a guarantee.

The legacy scanner's reference tracking works: `Gently` now reports as
`needed by Gently-Dark-Global-6` and is protected from `--remove`.

### repo — three crashes, three fixes

**1. `tarfile.AbsoluteLinkError`, unhandled.** Gently's
`Noir-Gently-White-Blue-Dark-Icons` ships one symlink to an absolute path
among thousands of files. `filter="data"` is the right policy with the wrong
failure mode: it raises on the first member it dislikes, killing the archive —
and since `AbsoluteLinkError` is a `TarError`, not an `OSError`, no handler
caught it and the whole nineteen-component install died with a traceback.

Now the same policy is applied per member: anything the filter rejects is
skipped and *reported* (`1 unsafe entry skipped (e.g. …)`), never silently.
The zip path got the same treatment. Handlers widened to `tarfile.TarError`
and `zipfile.BadZipFile`. A traversal entry is still refused — the failure
mode changed, the policy did not.

**2. `http.client.InvalidURL`, unhandled.** Gently ships a wallpaper called
`Gently-Nebula-Noir No Logo.jpg`, and the store puts that filename verbatim
into the signed download URL. A raw space cannot go in an HTTP request line,
so `http.client` raised — and `InvalidURL` is an `HTTPException`, not a
`URLError`, so it too sailed past every handler and aborted the run.

`store.encode_url()` now percent-encodes the path and query, leaves the
signing token alone, and is idempotent. Both request paths use it, and both
catch `HTTPException`.

**3. False drift on widget style.** `Gently-Dark-Global-6` declares
`widgetStyle=breeze`; `plasma-apply-lookandfeel` writes `Breeze`. Qt resolves
style names case-insensitively, so the audit was reporting drift on a theme it
had just applied cleanly — and would have on every theme spelling it
lowercase. `_same_pointer()` now folds case for widget styles.

149 → 160 tests.

---

## Turn 9 — every critical path run for real

Wolfram idled the machine deliberately so the destructive paths could be
exercised. Memory was sampled before and after every live action; nothing
grew.

### machine

| what | change | revert |
|---|---|---|
| `kdeglobals [Icons] Theme` | pinned to `Fluent-dark`, then un-pinned by `lol-kde restore --apply` back to the inherited `Tela` | already reverted. Backup `~/.config/kdeglobals.lolkde-turn9.bak`; `lol-kde restore 2026-08-02T17-03-04Z-f593 --apply --yes` |
| `kwinrc [org.kde.kdecoration2] library` | `plasma-apply-lookandfeel` **deleted** the `org.kde.kwin.aurorae.v2` pin; `lol-kde apply` put it back | already reverted; group byte-identical to `~/.config/kwinrc.lolkde-turn9.bak` |
| global theme | `lol-kde apply com.github.vinceliuice.Layan` run twice (already the applied theme) | none needed; `7/7 ok` both times, plasmashell survived |
| installed packages | `lol-kde install org.magpie.nostrum.desktop` added Nostrum's aurorae theme, Plasma style, wallpaper and colour scheme under `~/.local/share` | `rm -rf ~/.local/share/{aurorae/themes/nostrum,plasma/desktoptheme/nostrum,wallpapers/Nostrum,color-schemes/Nostrum.colors}` |
| removed package | `lol-kde legacy --remove` deleted the `Gently` Plasma style (5.0 MB, legacy `metadata.desktop`, unused) | re-download from the KDE Store; snapshots do not carry theme assets |

Snapshots taken: `…-66f1` (turn 9 baseline) plus five automatic ones from
`install`, `apply`, `restore` and `legacy --remove`.

Only one of the three removable legacy styles was deleted, at Wolfram's
instruction. The other two were moved out of the scan path so the tool's own
code path — scan, snapshot, prompt, `rmtree` — ran against exactly one
package, then moved back.

### Findings

**`plasma-apply-lookandfeel` deletes user-layer pins.** The big one. A
deliberate `library=org.kde.kwin.aurorae.v2` pin was *removed from
`~/.config/kwinrc`* by applying the look-and-feel, and resolution fell back to
the dead plugin name. `BorderSize`, in the same group but not declared by the
package, survived. So the repair inside `apply` is needed on every apply, and
restore's "kdedefaults first, user layer second" ordering is not a nicety —
reversed, it erases exactly the set restore just wrote. Recorded in
`CLAUDE.md` and `restore-design.md` §3.5.

It also un-pins *without* a tombstone, which is `KConfigGroup::revertToDefault()`
— the C++ API that `repair.unpin()`'s two-step emulates from outside.

**`unpin()` verified on the live desktop**, settling turn 8's open question.
After the restore, all four GTK bridge files (`gtk-3.0`, `gtk-4.0`,
`xsettingsd`, `.gtkrc-2.0`) were regenerated within the same second, every one
reading `Tela`. Those are written by kde-gtk-config in response to KConfig's
notification, so they are a live witness that a receiver got step 1's signal.
No tombstone was written. `diff` independently reported `no longer pinned in
this layer`.

**`please`'s dry run under-reports.** Aimed at Layan's Global Theme page it
lists 4 components (5 at `--depth 2`) — the description's links. The
manifest's 7 are fetched only *after* the root package installs, so
`--dry-run` structurally cannot see them. The real run is complete; the plan
is a floor, not a forecast. Logged in `docs/open-questions.md`.

### repo

- **Bare non-archive downloads.** `install org.magpie.nostrum.desktop` failed
  on `unrecognised archive format: Nostrum.colors` — the store serves that
  colour scheme as a bare file while the knsrc expects a container. The bytes
  now win over the category's expectation.
- **The root cause was a duplicate.** `install_dependency()` carried its own
  copy of `place_archive()`'s logic, so the first fix landed in one path only
  and `install` kept failing while `please` worked. The copy is gone;
  a test asserts it cannot come back. Nostrum went 2/6 → 4/6.
- **Unit tests were writing to the real journal.** They patched
  `restore.store` but not `snapshot.store`, which is where `journal.path()`
  derives from, so `lol-kde history` reported 27 test runs as things that had
  happened to this machine. Fixed, guarded by a test, and the entries removed.
- `legacy` said "the 1 removable ones".
- Known residue documented: `unpin()` leaves an empty `[Group]` header. It
  resolves identically and restore's unit is the key, not the group.
- 142 → 149 tests.

**Store defect, not ours:** content `1918450` (Stone's wallpaper) returns
`status 999: unknown request` on every attempt.

---

## Turn 8 — the three open questions, and a lost session

### machine

| what | change | revert |
|---|---|---|
| `~/.config/kwinrc` | probe keys added and removed for question A: `[LolKdeProbe]`, plus `BazInOwnedGroup`/`FreshInOwnedGroup` inside `[Desktops]` | reverted; verified byte-identical to `~/.config/kwinrc.lolkde-turn8.bak` (md5 `d2046250…`) |
| virtual desktops | one created (`lolkde-probe`) and removed, to make KWin itself write `kwinrc` | reverted; count back to 1. Removal left orphaned `[Tiling][<uuid>][…]` groups behind, cleaned by hand |
| `~/.config/kdeglobals` `[Icons] Theme` | question C: `--delete` (tombstone), re-pin to `Tela`, `--delete` again, then raw removal of the `Theme[$d]` line | reverted; md5 back to `77a29dfb…`. Now differs only by `ColorSchemeHash`, rewritten by the post-incident login |
| probe key `[LolKdeProbe] Ping` in `kdeglobals` | added to capture the `--notify` signal shape, removed by raw line edit | reverted, no residue |

Backups taken before any write: `~/.config/kwinrc.lolkde-turn8.bak`,
`~/.config/kdeglobals.lolkde-turn8.bak`. Snapshot first:
`2026-08-02T16-11-26Z-d2dd` ("before open-question tests", 13/13 verified).

**A `gdbus emit` of `org.kde.kconfig.notify.ConfigChanged` with the wrong
nested type destroyed the Plasma session.** Every KDE/Qt client on the bus
allocated 4–6 GiB within seconds and the kernel killed `kwin_wayland`; SDDM
returned to the login screen and every open application was lost. Not a
reboot, not the NVIDIA BAR1 issue. Full postmortem:
[`docs/incident-2026-08-02-kconfig-oom.md`](docs/incident-2026-08-02-kconfig-oom.md).

No revert exists. A broadcast cannot be undone by cleanup, and every safeguard
this project has — backup, snapshot, read-back, bounded monitors — worked and
none of them helped. The config files were already back to their pre-test
hashes when the session died.

### repo

**Answers.** All three blocking open questions are settled and the rows are
gone from `docs/open-questions.md`:

- **A — daemons merge.** KWin rewrote `[Desktops]`, a group it owns, and kept
  both foreign keys planted after its last reparse. Riders: a daemon write
  re-sorts the whole file canonically (so byte-comparing config files is not a
  change detector), and removing a virtual desktop orphans its `[Tiling]`
  groups.
- **B — the no-op is keyed on the *resolved* value**, not on `kdedefaults`.
  `--notify` fires only when something actually changed.
- **C — `--delete` does not delete.** It writes a `Key[$d]` tombstone that
  blocks inheritance, in both the pinned and the already-absent case. No
  `kwriteconfig6` flag expresses "make this key absent again".

**Two real bugs, both found by C:**

- `configparser` treats a bare `Key[$d]` line as a fatal parse error and
  abandons the rest of the file. `khotkeysrc` was being read as 492 keys of
  644 — **24% of that file was missing from every snapshot and diff taken
  before this turn**. Fixed with `allow_no_value=True`.
- `read_cascade()`/`origin()` parsed `Theme[$d]` as a key *named* `Theme[$d]`,
  so a tombstoned key was reported as inherited-and-fine while KDE resolved it
  to nothing. Now `kconfig.split_flags()` understands the marker.

**New:**

- `lolkde/restore.py` + `lol-kde restore` — plan by default, `--apply` to
  write, per `docs/restore-design.md`. Lock, integrity check, environment and
  package comparison, quarantine, fsynced journal, final verify pass,
  paste-ready CHANGELOG row.
- `repair.unpin()` — the mechanism C forced (`restore-design.md` §1a): write
  the inherited value through `kwriteconfig6 --notify`, *then* remove the
  user-layer line, so no resolved value changes at the raw edit and no
  notification has to be invented. New outcomes `UNPINNED`, `TOMBSTONED`,
  `STALE`; `write(value=None)` no longer reports `UNCHANGED` after planting a
  tombstone.
- `CLAUDE.md` "Hard rules" — the P0 ban on hand-emitted KDE signals.
  `docs/dbus-harness.md` — how to test protocol shapes on an isolated bus.
  `tests/…::TestNoLiveBusEmission` fails the build if a generic emitter is
  ever aimed at a KDE interface inside an executable block.
- 101 → 141 tests. The restore write path runs for real in the suite, against
  a temporary `XDG_CONFIG_HOME` with the bus switched off.

**Not done:** `restore --apply` has never written to this desktop, and
`unpin()`'s live behaviour is inferred rather than observed. Both are in
ROADMAP's "built but not exercised" table with the experiment that settles
them. `origin` was not pushed — the Forgejo box is down for hardware
maintenance.

---

## Turn 7 — documentation pass, end of session

### machine

No changes.

### repo

- `CLAUDE.md` machine state refreshed (scale 1.25; the decoration `theme` key
  is inherited, not pinned) and marked as a starting point rather than truth.
- `docs/open-questions.md`: question B settled and marked so.
- `ROADMAP.md`: new "built but not exercised end-to-end" section, and the
  Forgejo push recorded as blocked on an unreachable host.

**`origin` (Forgejo) is one or more commits behind.** The private mirror was
unreachable at the end of this session. `github` is current. Push to `origin`
when the box is back up; do not re-point remotes or force.

---

## Turn 6 — snapshot / diff / history

### machine

| what | change | revert |
|---|---|---|
| display scale | briefly set DP-1 to `1.2` and back to `1.25` as the regression test for the new `diff` | already reverted; verified `2048x1152 @ 1.25` |

New directory: `~/.lol-kde/snapshots/` — 6 snapshots, ~500 KB each. Nothing is
pruned automatically. `~/.lol-kde/journal.jsonl` records what the tool has done.
The hand-made `~/.lol-kde/checkpoints/turn5-*` are untouched.

### repo

- `lolkde/snapshot.py` — declarative manifest (36 entries, 79 files here) with
  a confidence column, byte capture across **all** cascade layers, a `state/`
  directory of interpretable state, and **coverage probes**: read a fact from a
  live instrument, read it back out of the captured bytes, report `GAP` with the
  path where the value actually lives.
- `lolkde/compare.py` — key-level and semantic diff, five sections.
- `lolkde/journal.py` — append-only JSONL; a corrupt line costs one entry.
- `lolkde/repair.py` — `write()` verifies by read-back instead of exit code and
  distinguishes `WROTE` from `INHERITED`. Fixes a real silent no-op from turn 2.
- `lolkde/cli.py` — `snapshot`, `snapshots`, `diff`, `history`; auto-snapshot
  before `apply` / `install` / `legacy --remove`, with no opt-out; fixed
  `lol-kde -v doctor` silently running non-verbose.
- `ROADMAP.md`, `docs/restore-design.md`, `docs/open-questions.md` — restore is
  designed, not built, and the deferral lives in the repo rather than anyone's
  memory.
- 69 → 101 tests.

**Regression test, run for real:** `lol-kde snapshot --around
'kscreen-doctor output.DP-1.scale.1.2'` reports the change under both
`SEMANTIC` and `SETTINGS`. Turn 5's hand-made checkpoint missed exactly this.

---

## Turn 5 — display scale 1.2 → 1.25

### machine

| what | file | old value | new value | revert |
|---|---|---|---|---|
| DP-1 scale | `~/.config/kwinoutputconfig.json` | `1.2` | `1.25` | `kscreen-doctor output.DP-1.scale.1.2` |
| DP-2 scale | same | `1.2` | `1.25` | `kscreen-doctor output.DP-2.scale.1.2` |
| DP-2 position | same | `2134,0` | `2048,0` | `kscreen-doctor output.DP-2.position.2134,0` |
| Xwayland scale | `~/.config/kwinrc` `[Xwayland] Scale` | `1.2` | `1.25` | `kwriteconfig6 --file kwinrc --group Xwayland --key Scale 1.2 && qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure` |

Checkpoints: `~/.lol-kde/checkpoints/turn5-before-scale/` and
`turn5-after-scale/`.

**The before-checkpoint is incomplete.** It captured
`~/.local/share/kscreen/` — the Plasma 5 location, which no longer receives
writes — and missed `~/.config/kwinoutputconfig.json`, where Plasma 6
actually stores this. The pre-change file is gone; the values are recorded in
`outputs-before.txt` and the revert commands above. See that checkpoint's
`GAP.md`.

Why: at 1.2, `2560/1.2 = 2133.33` does not divide evenly, so the logical grid
missed physical pixels horizontally. At 1.25, `2560/1.25 = 2048` and
`1440/1.25 = 1152` — exact on both axes.

### repo

No changes.

---

## Turn 4 — Kvantum's opaque= list

### machine

No changes.

### repo

- `48bdeee` — `resolve.kvantum_opaque_apps()`, `cli._detail()` multi-line
  indentation, and the research findings on Kvantum's pre-creation
  translucency timing and KWin's Debug Console.
- 66 → 69 tests

---

## Turn 3 — pre-scaled Aurorae variants

### machine

No changes.

### repo

- `resolve.aurorae_scale_mismatch()` — flags `_x1.25` / `_x1.5` Aurorae
  variants whose `<theme>rc` is byte-identical to their unscaled sibling.
  Catches the five broken WhiteSur variants installed here, and nothing else.
- `resolve.kvantum_opaque_apps()` — `doctor -v` now names the 17 executables
  Layan excludes from translucency by name.
- `cli._detail()` — multi-line details now indent their continuation lines.
- 62 → 69 tests
- `CLAUDE.md`: Kvantum's translucency is set before window creation via a
  single `styleHint()` call, so minimal test apps never reproduce it; KWin's
  Debug Console is a `QWidget` inside `kwin_wayland` whose surface has no
  alpha channel; a KWin script can close it where D-Bus cannot.

---

## Turn 2 — Aurorae plugin split

### machine

| what | file | old value | new value | revert |
|---|---|---|---|---|
| decoration plugin | `~/.config/kwinrc` `[org.kde.kdecoration2] library` | *(inherited `org.kde.kwin.aurorae` from `~/.config/kdedefaults/kwinrc`)* | `org.kde.kwin.aurorae.v2` | `cp ~/.config/kwinrc.lolkde.bak ~/.config/kwinrc && qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure` |
| decoration theme | `~/.config/kwinrc` `[org.kde.kdecoration2] theme` | *(inherited `__aurorae__svg__Layan`)* | `__aurorae__svg__Layan` *(same value, now pinned in the user layer)* | as above |

Backup: `~/.config/kwinrc.lolkde.bak` (taken before either write).

Reversible, no logout needed. The only visible consequence of reverting is
that System Settings goes back to showing no window decoration selected.

**Left on screen:** KWin's Debug Console window, opened via
`qdbus6 org.kde.KWin /KWin org.kde.KWin.showDebugConsole`. No D-Bus method
closes it; close the window by hand.

### repo

- `a065db0` — Point Aurorae themes at the plugin that still has themes in it
  - new `lolkde/repair.py` — the only module that writes to live config
  - `resolve.aurorae_provider()`, `decoration()` now reports a stale plugin
    name as DEGRADED
  - `apply` repairs the plugin name before verifying
  - 56 → 62 tests

---

## Turn 1 — context compaction

No changes. (Reported completion of session 1 work.)

---

## Session 1 — before the compaction

Reconstructed from commits and from `CLAUDE.md`; less precise than the
entries above, which were written as the changes were made.

### machine

| what | file | old value | new value | revert |
|---|---|---|---|---|
| window opacity | `~/.config/Kvantum/Layan/Layan.kvconfig` `[%General] reduce_window_opacity` | `0` | `15` | `cp ~/.config/Kvantum/Layan/Layan.kvconfig.bak ~/.config/Kvantum/Layan/Layan.kvconfig` |
| menu opacity | same file, `reduce_menu_opacity` | `0` | `10` | as above |
| global theme | applied `com.github.vinceliuice.Layan` via `plasma-apply-lookandfeel` | *(Sweet-Ambar-Blue)* | Layan | `plasma-apply-lookandfeel --apply <previous>` |
| installed packages | Layan look-and-feel + 6 dependencies (kvantum theme, GTK theme, Tela icons, Layan cursors, Plasma style, wallpaper) | — | installed under `~/.local/share` and `~/.config/Kvantum` | `lol-kde legacy --remove` does not cover these; delete by hand |

Backup: `~/.config/Kvantum/Layan/Layan.kvconfig.bak`.

`reduce_window_opacity=0 -> 15` is the change that made anything on this
desktop translucent at all. It is the single most consequential edit in the
whole exercise.

### repo

- `2b7b548` — Report the opacity knob, accept `-v` after the subcommand, add `CLAUDE.md`
- `23f40f1` — Catch Kvantum pointing at a theme that is not the one you applied
- `b124462` — Generate the README banner from the code that prints it
- …and everything before it: the initial build of `lol-kde`
