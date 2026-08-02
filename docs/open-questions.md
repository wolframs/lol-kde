# Open questions

Things asserted somewhere in this project that have **not** been verified on a
live system, each with the cheapest command that settles it. Written down
because the expensive mistakes in this project all began as a plausible claim
nobody checked — see `CLAUDE.md`, "Method rules".

When you settle one: move the answer into `CLAUDE.md`, delete the row here.

---

## Blocking `restore`

**All three are settled** (turn 8). The answers are in `CLAUDE.md`; the
consequences are in `docs/restore-design.md` §1a and §2.

The short version, because C changed the design:

| | question | answer |
|---|---|---|
| A | daemon clobber or merge? | **merge** — KWin rewrote a group it owns and kept both foreign keys planted after its last reparse |
| B | is the silent no-op real? | **yes**, and it is keyed on the *resolved* value, not on `kdedefaults` |
| C | does `--delete` revert to inherited? | **no — it writes a `Key[$d]` tombstone that blocks inheritance**, whether or not the key was pinned. `repair.unpin()` is the replacement mechanism |

---

## Manifest entries with low confidence

Each is currently captured by `snapshot`. The question is whether it is
**load-bearing** or a plausible-looking fossil — the `kscreen` failure mode.
A fossil in the manifest is harmless but misleading; a missing live path is not.

| path | doubt | settles it |
|---|---|---|
| `~/.config/khotkeysrc` | 33 KiB, dated Jun 13. KHotkeys was **removed in Plasma 6** and there is no `khotkeys.so` in the kded plugin dir. Likely a second `kscreen`. | `ls /usr/lib/*/qt6/plugins/kf6/kded/ \| grep -i hotkey` — then add a custom shortcut and watch the mtime |
| `~/.config/kded5rc` (no `kded6rc`) | Plasma 5 name on a Plasma 6 box | same shape of test |
| `~/.config/dconf/user` | kde-gtk-config touches it, but is it load-bearing for GTK4 theming or a mirror of `settings.ini`? `dconf` CLI is not installed here | `gsettings get org.gnome.desktop.interface gtk-theme` before/after a theme apply |
| `~/.config/kcmfonts` | **0 bytes** on this machine | toggle "Force font DPI" in System Settings, re-check |
| `~/.config/kdedefaults/package` | contents observed (`com.github.vinceliuice.Layan`); the writer is not identified | apply a different global theme and watch the file |
| `~/.config/plasmashellrc` | unclear what panel state lives here vs `plasma-org.kde.plasma.desktop-appletsrc` | move a panel, diff both |
| `kdeglobals [KScreen] ScreenScaleFactors` | present and empty; a legacy X11 global-scale key. Reading it as "scale is unset" would be wrong on Wayland | `kreadconfig6 --file kdeglobals --group KScreen --key ScreenScaleFactors` after a scale change |

---

## Opened by turn 8

| claim | why it is not settled | settles it |
|---|---|---|
| `repair.unpin()`'s two-step works **on a live desktop** | unit-tested against a temporary config tree only, and deliberately bus-silent there. The live claim — that step 1's `--notify` leaves running clients holding the inherited value, so step 2's raw removal needs no announcement — has not been observed on a real session | pin `kdeglobals [Icons] Theme` to a *different* icon theme, confirm the desktop changes, `unpin()`, and watch whether icons return to `Tela` without a restart. Snapshot first; this is the mechanism `restore` is built on |
| the notification is unnecessary at step 2 because no resolved value changes | reasoning from KConfig's merge behaviour (question A), not from observation | same test as above |
| a `[$d]` tombstone in `kdedefaults` behaves the same as one in `~/.config` | only the user layer was tested | plant one in the `kdedefaults` copy of a throwaway file and read the cascade |

Not open questions, but carried here so they are not lost — both are outside
this repo and were surfaced by the incident postmortem:

- the host has 64 GiB RAM and **512 MiB swap**, with no early-OOM policy
- `an unrelated systemd unit` restarts every ten seconds (`node` missing
  from the unit's `PATH`) and floods the journal

## Measurement caveats

| claim | caveat |
|---|---|
| `~/.config` sweep is 0.13 s, `~/.local/share` is 2.8–5.3 s | **warm page cache.** Cold will be worse. Do not build anything that depends on the 5 s figure |
| KWin defers writes ~11 s (`kwinrc` at 17:23:41.9, `kwinoutputconfig.json` at 17:23:53.0) | the mtimes are real, but the commands that produced them were not observed. The lag may be two separate manual commands rather than a debounce. The quiescence poll is right either way; do not write "KWin debounces for 11 s" down as fact |
| Kvantum's `nonIntegerScale` disables window translucency | **contradicted by observation** — this desktop ran at scale 1.2 with Dolphin visibly translucent. Either the reading is wrong or it is conditional. Do not repeat it |

---

## Settled — kept as a record of what a real answer looks like

| question | answer | how |
|---|---|---|
| Why does the Window Decorations KCM show nothing selected? | Plasma 6.6 split Aurorae; SVG themes moved to `org.kde.kwin.aurorae.v2` and every store theme still names the old plugin | 30 lines of C++ against the shipped `DecorationThemeProvider` header, mirroring KWin's own `DecorationsModel::init()` — 29 themes under `.v2`, 1 under the old id |
| Is Layan's `#g1000` a broken button? | No — a stray top-level `<use>`. All five button states render | `QSvgRenderer`, the engine Aurorae v2 actually uses |
| Why do `_x1.25` decorations look broken? | Artwork scaled exactly (66→83→99px); the `<theme>rc` layout metrics are byte-identical | `md5sum` + `QSvgRenderer` element bounds |
| Where does Plasma 6 store display scale? | `~/.config/kwinoutputconfig.json`. `~/.local/share/kscreen/` is Plasma 5 and never written | mtimes across a scale change |
