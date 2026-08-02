# Changelog

Two kinds of change get recorded here, because both need undoing sometimes:

- **repo** — commits in this project
- **machine** — edits made to Wolfram's live KDE configuration, with the
  backup path and the exact command that reverts it

Time is measured in **user turns**, not dates. A turn is one message from
Wolfram. Turn 1 is the first message after the context compaction on
2026-08-02; everything before it is "session 1".

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
