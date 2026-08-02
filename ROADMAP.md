# Roadmap

An index of what exists, what is deliberately deferred, and what is blocked.
It exists so that deferred work lives in the repo rather than in somebody's
memory. If you defer something, it goes here.

## Built

| feature | command | notes |
|---|---|---|
| Resolve a theme's pointers | `check`, `doctor` | seven components, cascade-aware |
| Install dependencies from a store URL | `please`, `install` | walks the description *and* the manifest |
| Report and remove legacy metadata | `legacy [--remove]` | never removes anything still referenced |
| Repair the Aurorae plugin name | inside `apply` | Plasma 6.6 split `org.kde.kwin.aurorae` |
| Capture system state | `snapshot`, `snapshots` | with coverage probes — see below |
| Compare two states | `diff` | key-level and semantic |
| Put a snapshot back | `restore` | plan by default, `--apply` to write. See caveat below |

## Deferred — designed, not built

### Smaller deferrals

| thing | why it is not done | where the detail is |
|---|---|---|
| `--prune` for old snapshots | no pressure yet; ~500 KiB each | `docs/restore-design.md` §retention |
| `--with-assets` full theme capture | ~55 MB/snapshot; needs content-addressing first | same |
| Patching Layan's `#g1000` | cosmetic log spam only, upstream's to fix | `CLAUDE.md` |
| Fixing the `_x1.25` Aurorae variants | detected, not repaired; nobody here uses them | `CLAUDE.md` |

## Built but not exercised end-to-end

Written, unit-tested, and wired in — but never run for real, so treat the
first run as a test rather than a routine.

| thing | why not | what to run |
|---|---|---|
| auto-snapshot inside `legacy --remove` | destructive, and the three removable packages are re-downloadable but not by this tool | `lol-kde legacy` first, then `--remove` |
| `restore --apply` for **more than one component at once** | turn 9 exercised `--component icons`. A multi-component run, and one that hits `SET` rather than `UNPIN`, are still only covered by unit tests | pin two pointers by hand, restore both |
| `restore` aborting mid-run | the `stop at first failure` path, exit code 3 and the three-line recovery message have never fired for real | make one step fail (e.g. chmod a config file read-only) and check the journal names where it stopped |

Cleared on turn 9 — all run for real against this desktop:

- **`restore --apply --component icons`** — un-pinned a hand-pinned icon
  theme, exposed the inherited value with no tombstone, and the live session
  followed (the GTK bridge files were regenerated within the second). This
  also settled `repair.unpin()`'s live behaviour; see `docs/open-questions.md`.
- **auto-snapshot inside `apply`** — `lol-kde apply com.github.vinceliuice.Layan`,
  snapshot line then `7/7 ok`, plasmashell survived (same pid), no memory growth.
- **auto-snapshot inside `install`** — `lol-kde install org.magpie.nostrum.desktop`,
  which also found a real bug (bare non-archive downloads) and took the theme
  from 2/6 to 4/6.

## Blocked

| thing | blocker |
|---|---|
| `contrast` KWin effect | root cause unknown; refuses to load with `contrastEnabled=true` |
| pushing to `origin` (Forgejo) | the box at `the private mirror` is **down for hardware maintenance** (confirmed turn 8; turn 7 saw it as `No route to host`). `github` is current, **`origin` is behind by 2+ commits**. Not an auth problem — do not re-point the remote and do not force. Just push when it is back |
| restore of tiers B and C | unchanged by turn 8. Kvantum needs the app restarted; `appletsrc` and the GTK/xsettingsd derived files are capture-only by design (`restore-design.md` §8) |

Test C's answer forced a new mechanism rather than a smaller feature — see
`docs/restore-design.md` §1a. The 2026-08-02 session-loss incident constrains
how that mechanism may be implemented; the rule is in `CLAUDE.md` and the
postmortem is `docs/incident-2026-08-02-kconfig-oom.md`.

## Rules

- Anything deferred gets a row here **and** a design note, or it does not get
  deferred — it gets done or dropped.
- Anything asserted but unverified goes in `docs/open-questions.md` with the
  command that would settle it.
- Anything that changes live configuration goes in `CHANGELOG.md` with a revert
  command. See `CLAUDE.md`.
