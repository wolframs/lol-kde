# Open questions

Things asserted somewhere in this project that have **not** been verified on a
live system, each with the cheapest command that settles it. Written down
because the expensive mistakes in this project all began as a plausible claim
nobody checked — see `CLAUDE.md`, "Method rules".

When you settle one: move the answer into `CLAUDE.md`, delete the row here.

---

## Blocking `restore`

These three must be answered before `docs/restore-design.md` is implemented.
The third can invalidate part of that design.

### A. Does a running KDE daemon clobber a foreign edit, or merge with it?

The design assumes KConfig **merges** on `sync()` — it re-reads the file and
writes back only keys it holds dirty. If instead daemons dump their whole
in-memory config at exit, byte-level restore is far more dangerous than the
design allows and every tier needs to be more conservative.

```sh
printf '\n[LolKdeProbe]\nBaz=2\n' >> ~/.config/kwinrc
kwriteconfig6 --file kwinrc --group Windows --key Placement Centered --notify
sleep 2 && grep -c 'Baz=2' ~/.config/kwinrc      # 1 => merge; 0 => clobber
kwriteconfig6 --file kwinrc --group LolKdeProbe --key Baz --delete
```

### B. Is the silent `kwriteconfig6` no-op real and observable?

**Believed answer: yes, already observed.** On 2026-08-02 `repair.aurorae_plugin()`
wrote `theme=__aurorae__svg__Layan` into `~/.config/kwinrc`, exited 0, and wrote
nothing — the value matched the inherited `kdedefaults` value. Re-confirm, then
this row becomes a `CLAUDE.md` fact rather than a question.

```sh
kwriteconfig6 --file kwinrc --group org.kde.kdecoration2 \
              --key theme __aurorae__svg__Layan; echo "exit=$?"
grep -c '^theme=' ~/.config/kwinrc               # expect exit=0 and count=0
```

### C. Does `--delete` revert to the inherited value, or write a `[$d]` shadow?

**The design blocker.** Restore needs to express "this key was *not* pinned in
the user layer; it was inherited". If `--delete` writes a `key[$d]` tombstone
instead of removing the line, that shadow *blocks* inheritance and produces a
state that never existed — and the whole "absent, inherited" row of the restore
state table needs a different mechanism.

Pick a key present only in `kdedefaults` (`kdeglobals [Icons] Theme` is one
today):

```sh
kreadconfig6 --file kdeglobals --group Icons --key Theme        # before
kwriteconfig6 --file kdeglobals --group Icons --key Theme --delete
grep -n 'Theme' ~/.config/kdeglobals | grep -i 'icons' -A2      # shadow marker?
grep -n '\[\$d\]' ~/.config/kdeglobals
kreadconfig6 --file kdeglobals --group Icons --key Theme        # inherited again?
```

Restore the prior state afterwards, and snapshot first.

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
