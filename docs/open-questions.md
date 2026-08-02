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

## Opened by turn 8, settled by turn 9

**`repair.unpin()`'s two-step works on a live desktop.** Run for real on turn 9:
`kdeglobals [Icons] Theme` was pinned to `Fluent-dark`, then
`lol-kde restore <id> --apply --component icons` un-pinned it. Result: the
line was removed (no `[$d]` anywhere in the file), `kreadconfig6` returned the
inherited `Tela`, and — the part that was actually in doubt — **the running
session followed**. `~/.config/gtk-3.0/settings.ini`, `gtk-4.0/settings.ini`,
`xsettingsd/xsettingsd.conf` and `~/.gtkrc-2.0` were all regenerated within
the same second, every one of them reading `Tela`.

Those four files are written by kde-gtk-config inside kded6 in response to
KConfig's change notification, which makes them a **live witness that a
receiver got step 1's signal and acted on it**. Step 2 changed no resolved
value and needed no announcement. `diff` independently reported
`no longer pinned in this layer` and caught `dconf/user` changing too.

| still open | why | settles it |
|---|---|---|
| a `[$d]` tombstone in `kdedefaults` behaves the same as one in `~/.config` | only the user layer was tested | plant one in the `kdedefaults` copy of a throwaway file and read the cascade |
| `please <url>`'s **plan** under-reports what a real run installs | the dry run lists only what the *description* names; the manifest is consulted after the root package is installed, so `--dry-run` cannot see it. For Layan: plan says 4–5 components, a real run also fetches colorschemes, plasma-themes, aurorae, sddmtheme and xcursor | make the dry run fetch the root's `metadata.json` (or read an installed copy) and show the union. Until then the plan is a floor, not a forecast |
| store content `1918450` (Stone's wallpaper) returns `status 999: unknown request` | observed turn 9 on every attempt; other ids on the same host work | try again later — if it persists the entry is gone and Stone's manifest is stale |

Not open questions, but carried here so they are not lost — both are outside
this repo and were surfaced by the incident postmortem:

- the host has 64 GiB RAM and **512 MiB swap**, with no early-OOM policy
- `an unrelated systemd unit` restarts every ten seconds (`node` missing
  from the unit's `PATH`) and floods the journal

## Opened by turn 13 — terminal translucency

Both configs were written and both **parse**, but neither has been seen on
screen: each needs a fresh process, and the kitty instance in question is the
one this session is running inside.

| claim | why it is not verified | settles it |
|---|---|---|
| kitty renders at `background_opacity 0.85` with blur | the running instance started before `dynamic_background_opacity yes` existed, so it cannot change opacity live — only a new process picks it up. Config *parsing* is confirmed: `kitty +runpy` with the user config returns `0.85 / 1 / True` | start a new kitty and look |
| Konsole's `Translucent` profile renders translucent | new file; `konsole --list-profiles` sees it, but a rendered window has not been observed. `Blur=true` in the scheme depends on KWin's blur effect, which *is* loaded | open a new Konsole window |
| `Opacity` in a `.colorscheme` is what Konsole actually reads | taken from the shipped `kubuntu-black.colorscheme`, which carries `Opacity=1`. Consistent, but only one sample | set `Opacity=0.5` briefly and look |

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
