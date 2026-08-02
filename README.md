# lol-kde

```
┌──────────────────────────────────────────────────────────┐
│  ╻  ┏━┓╻     ╻┏ ╺┳┓┏━╸                                   │
│  ┃  ┃ ┃┃  ╺━╸┣┻┓ ┃┃┣╸                                    │
│  ┗━╸┗━┛┗━╸   ╹ ╹╺┻┛┗━╸                                   │
│                                                          │
│  NOTICE                                                  │
│                                                          │
│  A Global Theme is a list of up to ten things you may or │
│  may not own.  This program determines which.  We regret │
│  the necessity.                                          │
└──────────────────────────────────────────────────────────┘
```

**KDE global themes declare their dependencies. Nothing installs them. This does.**

A Plasma "Global Theme" is not a theme. It is a small text file of pointers to
other components that must already be installed — up to ten of them:

```ini
# Sweet-Ambar-Blue/contents/defaults -- the entire functional content
[kdeglobals][KDE]        widgetStyle = kvantum
[kdeglobals][General]    ColorScheme = Sweet-Ambar-Blue
[kdeglobals][Icons]      Theme       = candy-icons
[kcminputrc][Mouse]      cursorTheme = Sweet-cursors
[plasmarc][Theme]        name        = Sweet-Ambar-Blue
[kwinrc][…kdecoration2]  theme       = __aurorae__svg__Sweet-ambar-blue
[kwinrc][WindowSwitcher] LayoutName  = org.kde.breeze.desktop
[KSplash]                Theme       = Sweet-Ambar-Blue
[Wallpaper]              Image       = Sweet-Ambar-Blue
```

Download a theme. Apply it. If a pointer does not resolve, KDE **falls back
silently** — no error, no warning, nothing in System Settings. You get a theme
that is 60% applied, no indication which 40% failed, and a growing suspicion
that you have done something wrong.

You have not. The theme shipped a machine-readable dependency list:

```json
"X-KPackage-Dependencies": [
  "kns://xcursor.knsrc/api.kde-look.org/1393084",
  "kns://icons.knsrc/api.kde-look.org/1305251",
  …
]
```

Nothing in the desktop reads it. Discover does not read it. The store page that
served you the theme does not read it. This program reads it, and then goes and
fetches the things.

That is the entire premise. It should not need to exist.

## Install

Python 3.11+, standard library only. No dependencies, no venv, nothing to rot.

```sh
git clone https://github.com/wolframs/lol-kde ~/Projects/lol-kde
cd ~/Projects/lol-kde
make install          # symlinks bin/lol-kde into ~/.local/bin
```

Or just run `./bin/lol-kde` from the checkout — it needs no install step.

It shells out to KDE's own tools (`kwriteconfig6`, `kreadconfig6`,
`kpackagetool6`, `plasma-apply-lookandfeel`, `qdbus6`) and reports what it
cannot find rather than guessing.

## Use

```sh
lol-kde please <store-url>          # install a theme AND everything it needs
lol-kde no-thank-you <store-url>    # resolve and report, install nothing
lol-kde doctor                      # what is applied now, and what is broken
lol-kde list                        # installed global themes
lol-kde check <theme>               # resolve one theme's pointers
lol-kde install <theme>             # fetch its missing dependencies
lol-kde apply <theme>               # apply, then verify it actually took
lol-kde why                         # what a Global Theme actually is
lol-kde legacy [--remove]           # packages using pre-5.19 metadata.desktop

lol-kde snapshot -m "before I break something"
lol-kde snapshot --around 'CMD'     # snapshot, run CMD, wait, snapshot, diff
lol-kde snapshots                   # list them
lol-kde diff [A B] [--changelog]    # what changed, and does it matter
lol-kde history                     # what this tool has done to your machine

lol-kde restore <id> [--apply]      # put a snapshot back, one key at a time
lol-kde prune [--apply] [--drop N]  # remove what a previous Plasma left behind
```

Every command prints a plan and writes nothing unless you pass `--apply`
(`restore`, `prune`), `--remove` (`legacy`), or name a mutating verb outright
(`install`, `apply`, `please`). `-v` after any subcommand shows the paths.

**Full reference, including every refusal each command makes:
[`docs/commands.md`](docs/commands.md).**

## What it tells you

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

| | meaning |
|---|---|
| `ok` | installed and resolving |
| `drift` | resolves, but differs from what the theme declares — something changed it later |
| `unset` | the theme declares this, but nothing set it; a KDE default is in effect |
| `warn` | present, but will not render as intended (usually Kvantum) |
| `MISS` | the configured value names something not installed; KDE is silently falling back |

`9/9 ok` means the metadata agrees with itself. It is **not** evidence that
anything is visible on your screen. When this tool says everything is fine and
your desktop disagrees, your desktop is right.

Nine rows, ten declarable components. The tenth is the wallpaper, and it is
`prune`'s rather than `doctor`'s — see below.

## Why this needs a tool at all

Nothing about KDE theming is one thing.

- **Layers are drawn by different programs.** The titlebar is KWin, the window
  interior is Qt in-process, the panel is plasmashell. "Apply a theme" is four
  unrelated operations in a trenchcoat.
- **Kvantum keeps its own theme selection in its own config file**, which
  applying a global theme does not touch. Every component can report `ok` while
  your windows render a completely different theme.
- **Plasma 6.6 split Aurorae in two**, and every theme in the store still names
  the half that no longer has any themes in it. KWin loads your decoration
  anyway, so it looks right — while System Settings shows nothing selected,
  which looks exactly like "window decorations are broken on this machine".
- **Nine of the thirteen themes installed here declare a task switcher that
  does not exist.** They all name `org.kde.breeze.desktop`, which was a Plasma 5
  spelling; Plasma 6 moved switchers to their own package type and Breeze
  stopped shipping one. `plasma-apply-lookandfeel` copies the dead name into
  your config anyway, and — this is the good part — it copies it into a
  *different group* than the one the theme declared it in. Nothing reports any
  of this, and nothing is actually wrong. Kubuntu's own three themes do it too.
- **`kwriteconfig6` can exit 0 and write nothing**, when the value already
  matches an inherited default. Indistinguishable from a failed write.
- **`kwriteconfig6 --delete` does not delete.** It writes a `Key[$d]` tombstone
  that *blocks* the inherited value instead of revealing it. There is no
  supported way to make a key absent again. This program contains exactly one
  raw file edit, and that is why.

Each of those is a section in [`docs/how-it-works.md`](docs/how-it-works.md),
with what the tool does about it. The measurements behind them are in
[`docs/kde-notes.md`](docs/kde-notes.md).

## It writes to your live desktop, so

- `apply`, `install`, `legacy --remove` and `prune` **snapshot first**, always.
  There is no flag to skip it; the only thing such a flag could do is destroy
  the artifact that makes the operation undoable.
- `prune` never deletes. Removals are *moved* to `~/.lol-kde/pruned/<id>/` with
  a `RESTORE.md` whose undo script actually runs. Emptying that directory is a
  separate decision you make later.
- Nothing is overwritten without `--force`.
- **`apply` never touches your desktop layout.** KDE's own dialog splits a
  global theme into "Appearance settings" (every box ticked) and "Layout
  settings: Desktop layout" (unticked) — the second replaces your panels,
  widgets, their arrangement and the wallpaper with the theme author's. On the
  command line that is `plasma-apply-lookandfeel --resetLayout`. This tool does
  not pass it, and there is no flag to make it: a prompt you can say yes to is
  a thing that gets said yes to, and it is the one part of applying a theme
  that no snapshot here can put back. Use System Settings if you actually want
  the author's panels.
- Third-party archives are unpacked per-member with traversal guards, and every
  name arriving over the network is reduced to a single path component before
  it touches the filesystem. A store entry titled `..` was once enough to make
  `--force` empty `~/.local/share`. It is not any more.

And one thing this program will never do, written into [`CLAUDE.md`](CLAUDE.md)
as a hard rule: **hand-emit a KConfig change notification on the live session
bus.** On 2026-08-02 that was done with a subtly wrong nested type, every KDE
client on the bus allocated several GiB apiece, the kernel killed KWin, and the
session went back to the login screen. The backups, snapshots and read-backs
all worked perfectly and not one of them helped, because a broadcast cannot be
undone by cleanup. Postmortem:
[`docs/incident-2026-08-02-kconfig-oom.md`](docs/incident-2026-08-02-kconfig-oom.md).

## Where this has actually been run

One machine. Everything here describing behaviour rather than design was
measured on a single Kubuntu-derived system running **Plasma 6.6.5 on
Wayland**, with Kvantum as the widget style and one user. The 228 unit tests
run anywhere; the integration facts do not have that backing.

That is not a support boundary — try it wherever you like — it is a statement
about the evidence. Most likely to differ elsewhere:

- **Qt plugin paths.** Style detection globs `/usr/lib/*/qt6/plugins`,
  `/usr/lib64/qt6/plugins` and `/usr/local/lib/qt6/plugins`. On a layout using
  none of those (NixOS, Guix) a working widget style reads as missing, and the
  Plasma 6.6 Aurorae check silently finds nothing to report.
- **`kpackagetool6` output is parsed in English.** Under another locale,
  re-installing a present package reports failure instead of "already installed".
- **Plasma 6.6 specifically.** The Aurorae repair landed for 6.6; on 6.0–6.5 it
  is unnecessary and inert.

If it does something wrong on your machine, that is interesting and worth an
issue — the failure modes above are guesses, not observations.

## Limitations

- SDDM themes install system-wide and need root; `lol-kde` reports them and stops.
- Kvantum themes are usually distributed on GitHub rather than the KDE Store,
  so `install` cannot fetch them.
- Themes declaring no `X-KPackage-Dependencies` cannot be repaired
  automatically — `check` still tells you exactly what is missing.
- Store downloads are signed and time-limited; a failed download is worth retrying.
- Restore covers the theme pointers and the decoration group. Kvantum's own
  config, the panel layout (`appletsrc`) and the generated GTK/xsettingsd
  bridge files are captured but never written back
  ([`docs/restore-design.md`](docs/restore-design.md) §8).
- It is **not a backup system**, and a theme tool that half-implements one is
  more dangerous than one that declines to. Use Timeshift, btrfs snapshots or
  etckeeper.

## Documentation

| file | what is in it |
|---|---|
| [`docs/commands.md`](docs/commands.md) | every command in full, and what each one refuses to do |
| [`docs/how-it-works.md`](docs/how-it-works.md) | why KDE theming needs a tool, and how this one handles it |
| [`docs/kde-notes.md`](docs/kde-notes.md) | measured KDE facts, most of them in no documentation anywhere |
| [`docs/restore-design.md`](docs/restore-design.md) | the restore design, and the measurements that forced it |
| [`docs/open-questions.md`](docs/open-questions.md) | claims not yet verified, each with the command that settles it |
| [`docs/incident-2026-08-02-kconfig-oom.md`](docs/incident-2026-08-02-kconfig-oom.md) | how to lose a Plasma session in one command |
| [`ROADMAP.md`](ROADMAP.md) | built, deferred, blocked — and what is written but never run for real |
| [`CHANGELOG.md`](CHANGELOG.md) | including every change made to a live desktop, with its revert command |

## Licence

MIT.
