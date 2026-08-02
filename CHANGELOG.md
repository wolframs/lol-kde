# Changelog

Two kinds of change get recorded here, because both need undoing sometimes:

- **repo** — commits in this project
- **machine** — edits made to Wolfram's live KDE configuration, with the
  backup path and the exact command that reverts it

Time is measured in **user turns**, not dates. A turn is one message from
Wolfram. Turn 1 is the first message after the context compaction on
2026-08-02; everything before it is "session 1".

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
