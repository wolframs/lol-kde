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

## Deferred — designed, not built

### `restore` — put a snapshot back

Fully designed in **[`docs/restore-design.md`](docs/restore-design.md)**. Do
not re-derive it; it is the risky half of the feature and the design encodes
several non-obvious decisions (why `kdedefaults/` is re-derived rather than
written, why deletion goes to quarantine, why there is no automatic rollback).

**Blocked on three tests** in [`docs/open-questions.md`](docs/open-questions.md).
One of them — whether `kwriteconfig6 --delete` reverts to the inherited value
or writes a `[$d]` shadow marker — can invalidate part of the design. Run them
first.

Deferred because restore should not be built before there are real snapshots
to restore from, and because its failure mode is the one thing worse than the
problem it solves.

### Smaller deferrals

| thing | why it is not done | where the detail is |
|---|---|---|
| `--prune` for old snapshots | no pressure yet; ~500 KiB each | `docs/restore-design.md` §retention |
| `--with-assets` full theme capture | ~55 MB/snapshot; needs content-addressing first | same |
| Patching Layan's `#g1000` | cosmetic log spam only, upstream's to fix | `CLAUDE.md` |
| Fixing the `_x1.25` Aurorae variants | detected, not repaired; nobody here uses them | `CLAUDE.md` |

## Blocked

| thing | blocker |
|---|---|
| `restore` | the three tests in `docs/open-questions.md` |
| `contrast` KWin effect | root cause unknown; refuses to load with `contrastEnabled=true` |

## Rules

- Anything deferred gets a row here **and** a design note, or it does not get
  deferred — it gets done or dropped.
- Anything asserted but unverified goes in `docs/open-questions.md` with the
  command that would settle it.
- Anything that changes live configuration goes in `CHANGELOG.md` with a revert
  command. See `CLAUDE.md`.
