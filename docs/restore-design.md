# `lol-kde restore` — design

**Unblocked on turn 8.** All three tests in
[`open-questions.md`](open-questions.md) are answered. Two of the answers
changed this document:

- **Test C invalidated §2's "absent, inherited" row.** `kwriteconfig6 --delete`
  writes a `Key[$d]` tombstone that *blocks* inheritance rather than revealing
  it. The replacement mechanism is §1a.
- **The 2026-08-02 session-loss incident constrains how §1a may be
  implemented.** Read
  [`incident-2026-08-02-kconfig-oom.md`](incident-2026-08-02-kconfig-oom.md)
  before touching notification. A raw file edit is safe; synthesising the
  notification that "should" accompany it is what destroyed a desktop.

Test A confirmed §1's premise — KConfig merges on `sync()`, measured against a
daemon that rewrote a group it owns and kept foreign keys planted after its
last reparse. §1 stands as written.

This is written so restore can be built without re-deriving any of it. Every
non-obvious decision below has its reason attached, because the reasons are
the part that gets lost.

---

## 1. Byte-restoring KDE config while the session runs is not safe

The usual explanation — "daemons dump their config at logout and clobber you" —
is probably wrong (test A settles it). KConfig merges on `sync()`: it re-reads
the file and writes back only keys it holds dirty. That is why two apps writing
different groups of `kdeglobals` do not destroy each other.

What actually goes wrong is worse, because it is partial and silent:

1. **The running session does not notice.** KConfig re-reads only on
   `reparseConfiguration()`, driven by the `org.kde.kconfig.notify` D-Bus
   signal. A plain `write()` emits nothing. The desktop looks unchanged, which
   reads as "restore didn't work", so the user runs it again.
2. **Then it partially un-notices.** Any key a running component holds dirty is
   written back over the restored file at its next `sync()`. The result is
   neither the snapshot nor the previous state — a state that never existed.
   You cannot enumerate dirty keys from outside the process.
3. **Some files are not KConfig at all.** `kwinoutputconfig.json` is serialised
   by KWin from its in-memory output list; `plasma-org.kde.plasma.desktop-appletsrc`
   likewise by plasmashell. Restoring these by copy while their owner runs is
   not risky so much as futile — with the hazard that a futile write can land
   halfway.

### The mechanism: replay, not copy

**Byte copies in a snapshot are evidence and diff material. They are not the
restore mechanism.** Every manifest path carries a tier, stored as data:

| tier | paths | mechanism | live-safe |
|---|---|---|---|
| **A — replay** | the seven component pointers across `kdeglobals`, `kwinrc`, `plasmarc`, `kcminputrc`, `ksplashrc` | `plasma-apply-lookandfeel` for the `kdedefaults` layer; `kwriteconfig6 --notify` / `--delete` for the user layer; then `KWin.reconfigure` | yes |
| **A — replay** | `kwinoutputconfig.json` | `kscreen-doctor output.<n>.scale.X` / `.position.X,Y` / `.mode.N`, plus `kwinrc [Xwayland] Scale` | yes |
| **B — byte, owner must be stopped** | `~/.config/Kvantum/**/*.kvconfig` | atomic write. No KDE daemon owns these; the app must restart to notice | file yes, effect needs restart |
| **C — capture only, never write** | `plasma-org.kde.plasma.desktop-appletsrc`, `gtkrc*`, `gtk-3.0/`, `gtk-4.0/`, `Trolltech.conf`, `xsettingsd/`, `dconf/user` | none — snapshot, diff, print, refuse | n/a |

Tier C splits into two reasons that must be **reported differently**:

- *derived* (`gtk*`, `xsettingsd`, `Trolltech.conf`) — regenerated from
  `kdeglobals` by kded6, observably within the same second. Restoring them
  creates a state contradicting its own source.
- *daemon-owned* (`appletsrc`) — plasmashell will overwrite you, and losing
  that race costs the panel layout.

**Do not make "log out first" the primary mechanism.** It is honest and it makes
restore useless for the case it exists for. Replay handles the seven components
live. Reserve session-off for tiers B and C, delivered as `--script`.

**Never end the session yourself.** No `ksmserver.logout`. Print the
instruction.

### Replay's own failure mode

Replay inherits the `kwriteconfig6` silent no-op (question B). If the snapshot
pins `theme=X` in `~/.config/kwinrc` and `kdedefaults` already yields `X`, the
write does nothing. The *resolved* value is right; the *pin* is lost — and the
pin is what survives the next global-theme apply.

So every write is followed by a **two-level read-back**:

- resolved — does `kconfig.read_cascade()` now yield the snapshot's value?
  → `ok` / `DIVERGED`
- layer — does `kconfig.origin()` point at the same layer the snapshot recorded?
  → `ok` / `pin-lost`

`pin-lost` is a distinct visible status. Not folded into `ok`, not into `FAIL`.
It means "correct today, fragile tomorrow".

---

## 1a. Un-pinning: the one raw file edit, and why it needs no new signal

**Added turn 8, replacing the mechanism §2 originally assumed.**

Restore has to express *"this key was not pinned in the user layer; it was
inherited"*. The obvious primitive does not do that:

> `kwriteconfig6 --delete` never removes a line. It writes `Key[$d]`, a
> tombstone that blocks the cascade, so the key resolves to **nothing** —
> not to the inherited value. Measured both ways round: from pinned, and from
> already-absent. Same tombstone, same dead key.

There is no flag for it. The whole option list is `--file --group --key --type
--delete --notify`. So the only route is to remove the line from the file, and
that is what `repair.unpin()` does. It is the single raw config write in the
program and it lives in `repair.py` with the rest, per §9.

### The two-step, and why the order is the whole point

```
1. kwriteconfig6 --file F --group G --key K <inherited value> --notify
2. remove the K= line (and any K[$…] line) from ~/.config/F
```

Step 1 pins the value the cascade would supply anyway. It is a real KConfig
write, so every running client learns the key resolves to V — through
KConfig's own signal, emitted by KConfig's own writer, with the type KConfig
chose.

Step 2 then removes the pin. The key resolves to V again, from the layer
below. **No resolved value changes at step 2**, so there is nothing to
announce, and no notification has to be invented.

That last clause is the design. The naive version — edit the file, then tell
everyone — requires emitting `ConfigChanged` by hand, and:

> **That is forbidden, and not because it is difficult to get right.** On
> 2026-08-02 exactly that command, with `a{sas}` where KConfig sends
> `a{saay}`, made every KDE client on the session bus allocate 4–6 GiB in
> seconds. The kernel killed `kwin_wayland` and the session was lost.
> Getting the signature right is not the fix; not being the one to send it is
> the fix. See `CLAUDE.md`, "Hard rules", and `docs/dbus-harness.md`.

The two-step is what lets restore be correct *and* stay inside the supported
writer. It was chosen for safety and turns out to be simpler.

### When there is nothing underneath

If no lower layer defines the key, step 1 has nothing to write. The line is
removed and the key now resolves to nothing — correct on disk, but running
clients still hold the old value and no supported writer can tell them.

Restore reports that as **`stale`**, distinct from success. It is the honest
answer: the file is right, the session is not, and the fix is a restart of the
affected component rather than another write. Do not paper over it by
reaching for the forbidden emitter — the whole `stale` status exists so that
nobody has to.

### Consequences elsewhere in the codebase

- `kconfig.read_cascade()` and `origin()` must treat `Key[$d]` as a deletion
  of `Key`, not as a key called `Key[$d]`. Before turn 8 they did the latter,
  so a tombstoned key was reported as inherited-and-fine while KDE resolved it
  to nothing.
- A bare `Key[$d]` line has no `=`, which `configparser` treats as a fatal
  parse error, silently discarding **the rest of the file**. `khotkeysrc` was
  being read as 492 keys of 644. Any snapshot taken before turn 8 is
  incomplete for files containing a tombstone.
- `repair.write(value=None)` returns `TOMBSTONED`, never `UNCHANGED`. The
  previous report was a lie in the most dangerous direction.

---

## 2. Granularity: per-key storage, per-component selection

Store and compute **per-key**. Select **per-component**. Offer per-file only as
a named escape hatch that is never the default.

Why not the others, from the live files:

- **Per-file is disqualified.** Restoring `kwinrc` to fix a decoration also
  reverts `[Tiling]` layouts, `[Desktops] Id_1`, `[Plugins] contrastEnabled`,
  `[Xwayland] Scale`. Restoring `kcminputrc` reverts per-USB-device pointer
  acceleration. Restoring `kdeglobals` reverts fonts and the whole palette.
- **Per-group is disqualified where it matters most.**
  `[org.kde.kdecoration2]` holds `library`, `theme` *and* `BorderSize` —
  and `BorderSize=Normal` in the user layer is a deliberate choice against the
  theme's declared `None`.
- **Per-key** is the only granularity at which "put back exactly what changed"
  is expressible, and it maps 1:1 onto `kwriteconfig6`, the only live-safe
  primitive.
- **Per-key is an unusable interface.** Nobody types
  `--key kdeglobals:KDE:widgetStyle` to fix their cursor.

A **component** is therefore a named bundle of `(file, group, key)` tuples plus
a write strategy and a verifier. Derive the bundles from
`resolve.SIMPLE_POINTERS` plus the decoration pair — do not transcribe them.
`resolve.pointer_kinds()` exists precisely because a hardcoded count drifted.

Components: the seven pointers, plus `kvantum`, `outputs`, `lookandfeel`.

Every key records **four** facts, not one: resolved value, originating layer,
user-layer value or `ABSENT`, `kdedefaults`-layer value or `ABSENT`. That gives
four restorable states:

| snapshot state | action | verify |
|---|---|---|
| pinned in `~/.config` with V | `repair.write(… V, notify=True)` | resolved == V **and** layer is `~/.config` |
| absent from `~/.config`, inherited V | `repair.unpin()` — §1a. **Not `--delete`** | resolved == V and layer is *below* `~/.config` |
| absent from every layer | `repair.unpin()`; expect `stale` | line gone; report that the session still holds the old value |
| present in `kdedefaults` | not writable by `kwriteconfig6` at all → §3 | — |

The second row is the one test C rewrote. `--delete` there would have produced
a tombstone: resolved value `""`, cascade blocked, and a state the snapshot
never contained. It would have verified as "key absent from the user layer",
which is true and useless — the check that catches it is *resolved == V*, not
*absent*.

---

## 3. `~/.config/kdedefaults/` is re-derived, never written

The correct model: **`kdedefaults/` is not a config directory anyone edits. It
is a projection of one fact — which global theme is applied — emitted by
`plasma-apply-lookandfeel`.** `kdedefaults/package` holds that fact; the other
files are its effects.

1. **Restore never writes into `~/.config/kdedefaults/`.** Not one byte, not
   behind a flag. Hand-writing that layer is the canonical way to manufacture a
   state that never existed.
2. Restore the layer by running
   `plasma-apply-lookandfeel --apply <kdedefaults/package from the snapshot>`,
   then **verify the regenerated files against the snapshot's byte copies**.
3. **If the regenerated bytes differ, stop** and print the diff, before touching
   the user layer, unless `--accept-drift`. This is the "system update made my
   config invalid" case rendered mechanically: the package changed and the
   state you asked for is no longer producible. Show that; do not paper over it.
4. **If the package is not installed, refuse.** Point at `lol-kde install`.
   Never fabricate the layer.
5. **Ordering is fixed: `kdedefaults` first, user layer second.**
   `plasma-apply-lookandfeel` also writes `LookAndFeelPackage` into the *user*
   layer, so applying afterwards would silently overwrite restored keys.
6. **Never pass `--resetLayout`.** It wipes the desktop layout.
7. If the applied package is unchanged, **skip step 2 entirely**. Re-applying a
   look-and-feel is heavyweight and partially side-effecting — including a live
   Plasma style reload, which has been seen to abort plasmashell inside
   `KSvg::FrameSvg::mask()`. Reuse `cmd_apply`'s existing warning verbatim.

Invariant, for the help text: *`kdedefaults` is restored by re-deriving it,
`~/.config` by replay. They cannot disagree because only one is ever written.*

---

## 4. Deletion

1. **Per-key, never per-file, by default.** A file that exists now and not in
   the snapshot is usually some unrelated subsystem's; deleting it is out of
   scope for a theme tool.
2. **Only keys inside the components being restored.** A new key in
   `[org.kde.kdecoration2]` is in scope for `--component decoration`. A key in
   `[Tiling]` is in scope for nothing.
3. **`[$Version]` / `update_info` is never written, deleted or restored.** Hard
   carve-out. An older `update_info` re-arms the ~20 `kconf_update` scripts in
   `/usr/share/kconf_update/` at next login — a real way to *cause* the
   "an update invalidated my config" failure while trying to undo it.
4. **Opt-in: `--delete-extra`, default off.** Without it, extra keys are
   reported and left alone. The default restore is additive and corrective,
   never subtractive.
5. **Nothing is unlinked, ever.** Removals go to
   `~/.lol-kde/restores/<ts>/removed/`, path-preserved, with a manifest — so the
   deletion is itself reversible. Deliberately *not* following `legacy --remove`'s
   `shutil.rmtree` precedent: that deletes packages you can reinstall; this
   deletes state you cannot.
6. **Path safety, checked immediately before each write** (not once up front —
   TOCTOU): resolved real path under `$XDG_CONFIG_HOME`/`$XDG_DATA_HOME`;
   regular file; **refuse symlinks** (people symlink `kdeglobals` into a
   dotfiles repo, and following it writes into git); owned by the current uid.
7. **Refuse to run as root.** `os.geteuid() == 0` → exit 2. There is no
   legitimate root invocation, and `sudo lol-kde restore` writing into
   `/root/.config` is a plausible 2am mistake.
8. Whole-file deletion only inside `~/.config/Kvantum/<theme>/`, only under
   `--delete-extra`, never for `*.bak`.
9. A key absent from the snapshot whose live value already equals the inherited
   default is a **no-op** — report `same`, do not churn. Pointless writes
   accumulate pins nobody asked for.

---

## 5. Preconditions — all before the first write, any failure aborting clean

1. Refuse root.
2. **Acquire `~/.lol-kde/lock`** via `os.open(O_CREAT|O_EXCL)` containing the
   pid. A restore racing a snapshot is a state nobody can reconstruct. Stale
   pid → report, offer `--break-lock`.
3. **Integrity-check the snapshot** — manifest parses, schema version
   understood, every recorded hash matches. Restoring from a corrupt snapshot
   is strictly worse than doing nothing.
4. **Environment comparison, reported first.** `XDG_CONFIG_DIRS`,
   `XDG_DATA_DIRS`, Plasma/KWin/KF6 versions, distro. If `XDG_CONFIG_DIRS`
   differs, say so prominently: the cascade's shape changed and inherited values
   may resolve differently even after a perfect restore. Naming this is the
   single most useful thing the command can print.
5. **Package inventory comparison.** Lead with what is no longer installed.
6. Tool availability — only what the computed plan needs.
7. Session sanity — `XDG_CURRENT_DESKTOP` contains KDE. **Warn if
   `systemsettings` is running**: it holds KCM state in memory and writes it
   back on the next Apply, silently undoing the restore.
8. **Warn about unselected drift** — components differing from the snapshot but
   out of scope will survive, giving a mixture. Say so, with counts.
9. **Auto-snapshot, unconditional, no flag to disable.** Taken to completion and
   integrity-verified *before* the first write; if it fails, abort.

### Bare `lol-kde restore <id>` prints a plan and writes nothing

Writing requires `--apply`; `--apply` prompts unless `--yes`.

Every read-only verb here is read-only, and both writing verbs already gate.
Restore has the largest blast radius in the program and should not be the least
guarded. Snapshot ids are prefix-matched and typo-prone; a mistyped id that
resolves to a *different valid snapshot* is the worst outcome available and it
is silent. The plan output makes that visible for free.

The counter-argument — *"it's the emergency button, dry-run-by-default means it
doesn't work when you need it"* — is answered by ending the plan with the exact
command on one copy-pasteable line.

---

## 6. Failure mid-restore

**Do not promise a transaction. There isn't one.** `plasma-apply-lookandfeel` is
multi-file and side-effecting; `kscreen-doctor` changes hardware state.

1. **Move failure out of the write phase.** Nearly every realistic failure is
   detectable in §5. The write phase should contain only operations already
   proven possible. This is the main defence.
2. **Every step is idempotent, so recovery is to run the same command again.**
   This is why the plan is a set of *desired end states*, not a sequence of
   deltas. Idempotency beats a resume mechanism and is free if you never
   express a step as a diff.
3. **A journal**, appended and `fsync`ed before each step, updated after:
   `~/.lol-kde/restores/<ts>/journal.jsonl`, one record per step with
   `planned → running → done|failed`. If killed, it states exactly where it
   stopped and what the value was before.
4. **Stop at the first failure.** Later steps' preconditions are no longer known
   to hold. No `--continue-on-error`.
5. **No automatic rollback.** It is itself a restore, executed by the code path
   that just demonstrated it can fail, when the state is least understood.
   On failure print exactly three lines: the journal path, the pre-restore
   snapshot id, and the command to undo.
6. **Final verification pass, always.** Re-run `resolve.audit()` and compare
   against the snapshot's recorded audit; print per-component
   `restored / same / pin-lost / extra / DIVERGED`. Exit non-zero if any
   *selected* component missed. Verifying with the same instrument `doctor`
   reports with means the two cannot disagree — and `CLAUDE.md`'s
   "`7/7 ok` is metadata agreeing with itself" applies identically here.

---

## 7. CLI

```
lol-kde restore <id> [--apply] [--yes] [--component NAME[,...]] [--key F:G:K]
                     [--all] [--delete-extra] [--accept-drift] [--files]
                     [--script] [--no-reconfigure] [--break-lock] [-v]
```

Default: print the plan, write nothing, exit 0.

`--script` earns its place: for tiers B and C it is the *only* safe route, it
converts an unsafe capability into an auditable artifact, and it gives the user
something to paste into `CHANGELOG.md` — which this project already requires
for every live-config change.

**No generic `--force`.** Every override is named after the specific thing it
overrides, so it cannot be cargo-culted from a forum post.

### Exit codes

| code | meaning | guarantee |
|---|---|---|
| 0 | plan printed, or restore completed and verified | — |
| 1 | completed, verification found divergence | writes happened |
| 2 | usage error, unknown/ambiguous id, corrupt snapshot, missing tool, root, lock held | **nothing was written** |
| 3 | aborted mid-restore | **partially written**; journal + pre-restore id printed |
| 130 | interrupted | same as 3 |

The 2-vs-3 split is the most valuable thing these codes carry: "fix the typo and
retry" versus "your desktop is in an unknown state, here is the way back".

---

## 8. Deliberately not doing

1. No `--no-snapshot`. Its only function is removing the artifact that makes the
   operation undoable.
2. No automatic rollback (§6.5).
3. No restore of `plasma-org.kde.plasma.desktop-appletsrc`. Capture, diff,
   print, refuse.
4. No restore of the GTK/xsettingsd/Trolltech derived files.
5. No file-copy restore of `kwinoutputconfig.json` — route through
   `kscreen-doctor` or refuse. This project already burned one checkpoint on
   that file.
6. No writes into `~/.config/kdedefaults/`.
7. No touching `[$Version]` / `update_info`.
8. **No general-purpose backup ambitions.** Restore is scoped to the components
   this tool models. Say so in the help and point at Timeshift / btrfs
   snapshots / etckeeper. A theme tool that half-implements a backup system is
   more dangerous than one that declines to.
9. No root, no sudo, no system paths.
10. No logging the user out, and no offering to.
11. No silent installation during restore. A snapshot naming a missing package
    is a report, not a download.
12. No `--continue-on-error`, no `--resume`. Idempotency makes both
    unnecessary; shipping them would signal robustness the design lacks.
13. **No hand-emitted D-Bus notifications, under any circumstance.** Not for a
    raw edit, not "just this once with the right signature", not behind a
    flag. §1a reaches the same end state through `kwriteconfig6 --notify`;
    where it cannot, the answer is the `stale` status, not a broadcast. This
    is the one rule in this document that is not a trade-off — see
    `incident-2026-08-02-kconfig-oom.md` for what it cost to learn.
14. **No merge / three-way restore** ("apply the snapshot's changes on top of my
    current state"). Everyone will ask for it; there is no defensible semantics
    for it on a cascade with silent inheritance. `diff` plus `--key` gives the
    same power, explicitly.

---

## 9. Implementation notes

- **Keep `repair.py` the only module that writes live config.** Its docstring
  says so and it should stay true. Write primitives there; plan, journal and
  verify in a new `restore.py` that calls them.
- `repair.write()` already takes a `file` parameter, supports `--notify` and
  `--delete`, and returns a read-back result rather than an exit code — that
  groundwork is done. Turn 8 added `unpin()` (§1a), `delete()` (honest about
  the tombstone), `inherited_value()`, and the outcomes `UNPINNED`,
  `TOMBSTONED` and `STALE`.
- **`unpin()` takes `notify=False` for tests.** Unit tests run against a
  temporary `XDG_CONFIG_HOME` and must not put anything on the real session
  bus — not even a correctly-typed signal for a config file nothing reads.
  Production callers leave the default alone.
- `paths.CONFIG_FILES` is the shared manifest root. Snapshot, diff and restore
  must consume the same list, with a test that they cannot diverge.
- Every `restore` run should print a paste-ready `CHANGELOG.md` table row. It
  fits the project's existing discipline exactly.
