# lol-kde — how to work on this

Rules and method. The KDE facts that used to live here are in
@docs/kde-notes.md, which this file imports — read them before theorising
about KDE theming, because most of them are not discoverable from
documentation and several cost hours to find.

Machine-specific state lives in `CLAUDE.local.md`, which is gitignored and
loads alongside this file. It is not in the repository and should not be:
it is true of one box, it goes stale fastest, and it is nobody else's
business.

## Keep the record files current

Every change to Wolfram's **live configuration** — not just repo commits —
goes in `CHANGELOG.md` with the old value, the new value, the backup path and
the exact revert command. Time is counted in **user turns**, not dates.
Back up before writing; record the backup path in the same entry.

Three more files carry work that would otherwise live in someone's memory.
Keeping them current is not optional:

| file | holds |
|---|---|
| `ROADMAP.md` | what is built, deferred, blocked. Anything deferred gets a row **and** a design note, or it does not get deferred |
| `docs/open-questions.md` | every claim asserted but unverified, each with the one command that settles it. Move answers into this file's facts and delete the row |
| `docs/restore-design.md` | the restore design, executable without re-deriving it |

`lol-kde snapshot` before changing anything, and `lol-kde diff --changelog`
emits the CHANGELOG row for you. The turn number is still yours to fill in.

## Hard rules — not advice, not "be careful"

**Never hand-emit a KConfig notification on the live session bus.** No
`gdbus emit`, no `dbus-send`, no generic emitter, for
`org.kde.kconfig.notify.ConfigChanged` or any other internal KDE signal.
Use `kwriteconfig6 --notify`, or a helper linked against KConfig itself.

On 2026-08-02 this destroyed Wolfram's session and every application in it.
A `gdbus emit` of `ConfigChanged` with `a{sas}` where KConfig sends `a{saay}`
made every KDE/Qt client on the bus allocate 4–6 GiB within seconds; the kernel
killed `kwin_wayland` and SDDM returned to the login screen. Full postmortem:
**[`docs/incident-2026-08-02-kconfig-oom.md`](docs/incident-2026-08-02-kconfig-oom.md)**.

The trap is specifically that the mistake looks *correctable*. Having watched
the real signal go by, fixing the type and re-sending is the obvious next move
and it is the same error again. Observing a signal does not make replaying it
safe. There is no signature careful enough; the rule is the emitter, not the
payload. `tests/test_lolkde.py::TestNoLiveBusEmission` fails the build if this
combination reappears anywhere in the repo.

**Protocol experiments go on an isolated bus.** `dbus-run-session` is enough to
check a wire signature. Receiver behaviour needs a nested or disposable Plasma
session, or a VM — never the primary desktop. `docs/dbus-harness.md` has the
harness.

**A broadcast cannot be undone by cleanup.** Backups, snapshots, read-backs and
`trap` all worked perfectly during that incident and none of them helped,
because the signal had already reached every listener. Reversibility is a
property of *file* writes. Before anything that puts a message on the session
bus, the question is not "can I undo this" — it is "may this be sent at all".

**Watch memory across a live KDE action.** For any approved live test, sample
before and for ~15 s after, and abort on multi-process growth:

```sh
ps -C kwin_wayland -C plasmashell -C kded6 -C kwalletd6 -o pid,comm,rss && free -h
```

## Method rules (these matter more than any fact in `docs/kde-notes.md`)

**Name the instrument.** If you measure something, state which tool produced the
number and whether that tool is the one the system actually uses. KWin renders
SVGs through **Qt/KSvg**. `librsvg`/`rsvg-convert` is a *different renderer* and
disagrees on real theme files. Presenting librsvg numbers as "what KWin draws"
wasted most of an evening.

**Cheapest discriminating test first.** Rasterising 29 SVGs is expensive and
indirect. Grepping two config files that differ by one line is free and
decisive. The expensive test was chosen because it matched the theory.

**`7/7 ok` is metadata agreeing with itself.** It is *not* evidence that anything
is visible on screen. When the user says "it doesn't work" and the tool says
everything is fine, the tool is measuring the wrong layer. Believe the user.

**Check the layer you can change before the layer you can measure.** The answer
to "nothing is ever translucent" was one integer in a Kvantum config, in a file
format this repo already had a parser for. Four rendering investigations
preceded looking at it.

**Beware confounding compositor effects when judging translucency.** KWin's
Translucency effect ships `MoveResize=75`, so *any* window goes see-through
while dragged, under any theme. The `scale` effect fades windows in on open. A
window that looks translucent and then "goes back to normal" is almost always
one of those, not the theme.

## Snapshots: things measured while building the capture

**`kwriteconfig6` exits 0 and writes nothing when the value already matches an
inherited default — and this repo walked into it.** On turn 2
`repair.aurorae_plugin()` wrote `theme=__aurorae__svg__Layan` into
`~/.config/kwinrc`, reported success, and the key never appeared. The `library`
pin (which differs from the inherited value) did land, so nothing broke. Never
trust the exit code: `repair.write()` now returns `WROTE` / `INHERITED` /
`UNCHANGED` / `FAILED` from a two-level read-back — did it resolve, and did it
land in the layer we aimed at.

**Bounded walks, measured on this machine.** A full walk of
`~/.local/share/icons` (227k files, 1.8 GB) took a snapshot from 2.5s to 11.3s.
Both the sweep and the package inventory are now capped. The sweep collapses
any top-level subtree over 2,000 entries in a *single* pass — the earlier
version counted the subtree and then walked it again to roll it up.

**Never record a walk-order-dependent number in a snapshot.** The first
collapsed-subtree row included `files_seen`, which depends on where the budget
trips, so every collapsed subtree compared as changed between two snapshots of
an unchanged tree. Collapsed rows now carry the directory's own mtime and
nothing else.

**`diff --live` captures the live system through `capture(into=...)`**, the same
code path as a stored snapshot. Reading live config directly on one side and
captured bytes on the other would compare two different things.

## Open leads

1. **`contrast` effect** won't load despite `contrastEnabled=true` and a KWin
   reconfigure. Affects panel frostiness only.
2. **`BorderSize` drift.** Layan declares `None`; `~/.config/kwinrc` carries
   `Normal` in the user layer and wins. `audit()` does not compare BorderSize
   at all — it is in the same group as `library`/`theme` and would be a cheap
   addition.
3. The KCM's own previews log `qt.svg: Could not resolve property:
   #linearGradient…` — that is one of the other 28 installed themes, not
   Layan, whose `decoration.svg` has no dangling references. Unidentified.

## Untrusted names from the store are not path components

Found by review on 2026-08-02, reproduced before fixing, and the worst defect
this project has had.

When an archive has no single top-level directory -- the normal shape for
icon, cursor and colour-scheme uploads -- the installed directory is named
after the **store entry's title**, whatever the uploader typed. That title was
joined onto the install target and passed to `shutil.rmtree` under `--force`.

A title of `..` was enough. `rmtree("~/.local/share/icons/..")` empties
`~/.local/share`. No slash required, so it did not depend on what pling
permits in a name; `../../plasma` escaped the target with no `--force` at all.
`please` takes no auto-snapshot, unlike `install`, so nothing was recoverable.

The rule, now enforced by `install._child_of()` and `store.safe_filename()`:

**Every name that arrives over the network gets reduced to a single path
component before it touches the filesystem, and the result is re-checked to be
a direct child of its target.** Both guards, not one -- the component check
alone misses an existing symlink in the target, and the containment check
alone accepts a name that resolves to the target itself.

Three places take untrusted names: `StoreItem.name` (entry title),
`downloadname` (attachment filename), and archive member paths (already
handled by `_safe_extract`). Guard at the point the value enters, not at the
point it is used -- `store.download()` cannot help, because by then the
traversal is in `destination.parent` and is indistinguishable from a directory
the caller meant.

Adjacent, same review: **`metadata.json` is an upload too.** A top level that
is a list rather than an object raised `AttributeError` straight out of
`please --dry-run`. `isinstance` before `.get()`, always.
