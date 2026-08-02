# Testing D-Bus protocol shapes without a desktop to lose

Written because the alternative was tried. See
[`incident-2026-08-02-kconfig-oom.md`](incident-2026-08-02-kconfig-oom.md):
one hand-emitted `org.kde.kconfig.notify.ConfigChanged` with a subtly wrong
nested type destroyed the live session and every application in it.

The rule in `CLAUDE.md` is absolute — no generic emitter for internal KDE
signals on the user's session bus, ever. This file is what to do instead.

## Pick the weakest environment that answers the question

| question | environment | why |
|---|---|---|
| What is the wire signature of a signal? | **`dbus-run-session`** | no KDE listeners exist, so nothing can react. Sufficient for every type question |
| Does a *sender* produce the bytes I expect? | `dbus-run-session` | run the real tool against the private bus and monitor it |
| How does a *receiver* behave? | nested Plasma / VM | needs real listeners. Never the primary session |
| Does the desktop apply the change? | primary session, **supported writers only** | `kwriteconfig6 --notify`, `kscreen-doctor`, `plasma-apply-lookandfeel` |

Everything above the last row is free and safe. The last row is the one that
costs a session when it is wrong, so it gets the supported tool and nothing
else.

## Isolated bus

`dbus-run-session` starts a private session bus, runs one command against it,
and tears the bus down on exit. Nothing on the real bus can see it and nothing
on it can see the real bus.

```sh
dbus-run-session -- bash -c '
  timeout 5 dbus-monitor --session "interface=org.kde.kconfig.notify" > /tmp/sig.txt &
  sleep 1
  XDG_CONFIG_HOME=$(mktemp -d) kwriteconfig6 --file probe --group G --key K 1 --notify
  wait
  cat /tmp/sig.txt
'
```

Two things make this safe rather than merely private:

- `dbus-run-session` means no KDE client is listening, so even a malformed
  message reaches nobody.
- `XDG_CONFIG_HOME` points at a throwaway directory, so the writer cannot
  touch real config. Do this even on a private bus — the file layer and the
  bus layer are isolated separately.

## Reading a signature you have captured

`dbus-monitor` prints types in prose, and the prose is where the fatal
distinction hides:

| printed by `dbus-monitor` | D-Bus type | GVariant |
|---|---|---|
| `string "Theme"` | `s` | `'Theme'` |
| `array of bytes "Theme"` | `ay` | `b'Theme'` |

`array [ array of bytes ... ]` is `aay`, not `as`. KConfig's `ConfigChanged`
carries `a{saay}` — a map of group name to a list of key names *as byte
arrays*. The incident payload was `a{sas}`. Nothing in the bus rejects the
mismatch; the receivers do the damage.

Note this only tells you what a *correct* payload looks like. It does not
license sending one. The reason to know the shape is to recognise it in a
capture, not to reproduce it.

## Nested session, if receiver behaviour is genuinely the question

A nested compositor is the only way to watch real KDE clients react without
risking the session you are typing in:

```sh
dbus-run-session -- kwin_wayland --width 1280 --height 720 --xwayland plasmashell
```

Anything killed in there is in there. Treat it as still-dangerous: it is a
smaller blast radius, not a safe one.

## What has no safe environment

Replaying a captured internal signal at real KDE clients. There is no version
of this that belongs in this repo, in any environment reachable from the
user's login. If a restore operation seems to need it, the design is wrong —
see `restore-design.md` §1a, which reaches the same end state using only
`kwriteconfig6 --notify`.
