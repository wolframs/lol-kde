# Incident report: malformed KConfig D-Bus signal caused Plasma session OOM

**Date:** 2026-08-02  
**Host:** Kubuntu 26.04, Plasma Wayland, single-user workstation  
**Impact:** The active KDE Plasma user session was destroyed and all applications in it were lost. The computer did **not** reboot.  
**Causal confidence:** High for the triggering action; medium-high for the exact library-level failure mechanism.

## Executive summary

At 18:15:11 CEST, an interactive Claude Code experiment manually emitted
`org.kde.kconfig.notify.ConfigChanged` on the live user-session D-Bus. The
payload had the wrong D-Bus signature:

```text
sent:     a{sas}   {'Icons': ['Theme']}       # array of strings
expected: a{saay}  {'Icons': [byte-array]}    # array of byte arrays
```

The session bus permits a signal sender to choose its own signature; it does
not enforce the signature expected by KDE receivers. Within 14 seconds,
KWallet invoked the kernel's global OOM killer. The kernel snapshot shows that
many unrelated KDE/Qt processes had simultaneously grown to approximately
4–6 GiB RSS each. At 18:15:49 the OOM killer killed `kwin_wayland`; SDDM then
closed the Plasma session and returned to the login screen.

This was not a reboot and was not the previously seen NVIDIA BAR1 failure.
The system has remained in the same kernel boot since 2026-07-31 23:07 CEST,
and the incident window contains none of the prior NVIDIA signatures.

This was also not primarily a forgotten `dbus-monitor` process. Both monitors
were bounded by `timeout`, and the modified config file had been restored to
its exact pre-test MD5 before the malformed broadcast. The causal mistake was
performing a protocol-shape experiment on the live desktop bus. A broadcast
cannot be undone by later cleanup.

## User-visible sequence

1. Claude performed live KConfig/restore-design experiments against the active
   desktop session.
2. A malformed `ConfigChanged` signal was broadcast to KDE applications.
3. Many KDE/Qt processes allocated several GiB each in seconds.
4. The kernel killed nine Chromium processes while attempting to recover.
5. The kernel then killed KWin, the Wayland compositor.
6. Plasma stopped, SDDM closed the login session, and a new login screen
   appeared. This looked similar to a reboot because the entire graphical
   session and all applications vanished.

## Timeline (CEST)

| Time | Event | Evidence |
|---|---|---|
| 18:10:01 | Machine healthy: about 9.6 GiB RAM used, 23.1 GiB physically free, swap unused | `sar -r ALL` and `sar -S`, `/var/log/sysstat/sa02` |
| 18:13:41 | Claude creates `~/.config/kdeglobals.lolkde-turn8.bak` | File mtime and Claude transcript |
| 18:14:43–18:14:55 | Claude observes the real `kwriteconfig6 --notify` signal. It contains `array of bytes "Ping"` | Claude transcript lines 131–132 |
| 18:15:09 | Claude starts the manual-emission test using `{'Icons': ['Theme']}` | Claude transcript line 134 |
| 18:15:11.738 | Malformed `ConfigChanged` is emitted successfully as an array containing `string "Theme"` | Claude transcript line 138 |
| 18:15:15 | Test command completes; config MD5 is back to the pre-test value | Claude transcript line 138 |
| 18:15:25.813 | `kwalletd6` invokes the global kernel OOM killer | kernel journal |
| 18:15:49 | Repeated OOM kills; nine Chromium processes are killed | kernel journal |
| 18:15:49.573 | Claude attempts a second command using a purported corrected `b'Theme'` payload, but the session is already collapsing; no tool result is recorded | Claude transcript line 141 |
| 18:15:49.644 | Kernel kills `kwin_wayland`, which has 6,536,904 KiB anonymous RSS | kernel journal |
| 18:15:50.416 | `plasma-kwin_wayland.service` fails with `oom-kill` | user journal |
| 18:15:50.846 | SDDM closes the session for `wolfram` | system journal |
| 18:16:06 | Old user manager is forcibly terminated; user slice reports 60.1 GiB memory peak and 511.1 MiB swap peak | system journal |
| 18:16:49 | A fresh Plasma login session starts | SDDM/logind journal |

## Direct evidence

### 1. The sent signal did not match the observed real signal

Claude first captured the signal produced by the supported writer:

```text
kwriteconfig6 --file kdeglobals --group LolKdeProbe --key Ping 1 --notify

interface=org.kde.kconfig.notify; member=ConfigChanged
array [
  dict entry(
    string "LolKdeProbe"
    array [
      array of bytes "Ping"
    ]
  )
]
```

Claude then manually emitted:

<!-- DO NOT RUN. Reproduced as evidence only; this command destroyed the
     session. See "Required prevention changes" below. Fenced as `text`, not
     `sh`, so nothing can lift it out of here as a recipe. -->

```text
gdbus emit --session \
  --object-path /kdeglobals \
  --signal org.kde.kconfig.notify.ConfigChanged \
  "{'Icons': ['Theme']}"
```

The monitor showed a different inner type:

```text
array [
  dict entry(
    string "Icons"
    array [
      string "Theme"
    ]
  )
]
```

The session transcript was kept locally and is not published with this
report.

The key records are lines 131–132 (supported signal capture), 134 and 138
(malformed emission and observed shape), and 141 (uncompleted corrected retry).
Transcript timestamps are UTC; add two hours for CEST.

### 2. Memory pressure was sudden, not an accumulated normal workload

The last sysstat sample before the incident, at 18:10:01, reported:

```text
kbmemused=9,586,772 KiB
kbmemfree=23,099,116 KiB
kbavail=54,011,864 KiB
kbswpused=0 KiB
```

Five minutes later the user slice peaked at 60.1 GiB and had consumed nearly
all of the configured 512 MiB swap. A normal browser workload does not explain
that step function.

### 3. The OOM affected many KDE receivers at once

At the first kernel OOM process-table snapshot, approximate RSS included:

| Process | Approx. RSS |
|---|---:|
| `kwin_wayland` | 5.69 GiB initially; 6.2 GiB service peak |
| `kcmshell6` | 4.64 GiB |
| `plasmashell` | 4.62 GiB |
| `systemsettings` | 4.43 GiB |
| `kwalletd6` | 4.34 GiB |
| `org_kde_powerdevil` | 4.18 GiB |
| `kded6` | 4.17 GiB |
| `DiscoverNotifier` | 4.14 GiB |
| `kate` | 4.11 GiB |
| `kactivitymanagerd` | 4.05 GiB |
| all Chromium processes combined | about 1.44 GiB |

The common property of the dominant consumers is that they are KDE/Qt clients
which can receive KConfig notifications. Chromium was selected first by the
OOM killer because its processes had a higher `oom_score_adj` (300 versus 200),
not because Chromium caused the exhaustion.

### 4. KWin death directly caused the logout

The decisive journal chain is:

```text
18:15:49.644 Out of memory: Killed process 2653 (kwin_wayland) ...
18:15:50.416 plasma-kwin_wayland.service: Failed with result 'oom-kill'.
18:15:50.846 pam_unix(sddm:session): session closed for user <user>
18:15:50.988 systemd-logind: Removed session 3.
```

## Causal assessment

### Confirmed

- No reboot occurred.
- A global RAM OOM occurred.
- A malformed KConfig signal was broadcast on the live session bus 14 seconds
  before OOM handling began.
- Many KDE/Qt receivers simultaneously held anomalously large private/anonymous
  memory allocations.
- Killing KWin caused Plasma and the SDDM session to terminate.
- The config probes themselves were cleaned up before the OOM: `kdeglobals`
  matched its pre-test MD5.
- The checked-in `lol-kde` repository does not currently contain a manual
  `gdbus emit` of this signal. Its implementation uses
  `kwriteconfig6 --notify`, which is the supported route.

### Strong inference

The malformed `a{sas}` signal reached listeners expecting `a{saay}` and exposed
a bad deserialization/conversion path in QtDBus/KConfig, causing each receiver
to interpret data or a length incorrectly and allocate several GiB. The timing,
receiver population, and allocation pattern make this the overwhelmingly most
likely trigger.

The exact faulty function and whether the bug sits in QtDBus, KConfig, or a
generated adaptor have not been proven with a debugger. That distinction is
not needed to prevent recurrence.

### Ruled out or non-causal

- **Reboot/power loss:** ruled out by boot ID and uptime.
- **Earlier NVIDIA BAR1 issue:** no `dmaAllocMapping_GM107`,
  `NV_ERR_NO_MEMORY`, `NvKmsKapiMemory`, Xid, or MMU-fault signature in the
  incident window.
- **Chromium as primary consumer:** contradicted by the OOM process table.
- **Unbounded `dbus-monitor`:** monitors used `timeout` and exited.
- **Config residue:** the tested file matched the pre-test MD5 before the
  malformed broadcast.
- **An unrelated systemd unit in a restart loop:** noisy and worth fixing,
  but it had been occurring every ten seconds for many hours while memory was
  healthy and did not account for the KDE-wide multi-GiB allocations.

## Required prevention changes for Claude/lol-kde work

### P0: ban manual KConfig signal emission on the live session bus

Add an explicit repository rule to `CLAUDE.md`:

> Never use `gdbus emit`, `dbus-send`, or an equivalent generic emitter for
> `org.kde.kconfig.notify.ConfigChanged` on the user's live session bus. Use
> `kwriteconfig6 --notify` or a correctly typed KConfig API. Observing a signal
> does not make replaying it with inferred GVariant syntax safe.

This should be a hard rule, not a reminder to be careful.

### P0: change the restore design

`docs/restore-design.md` currently discusses raw file edits followed by
KConfig notification. Do not implement that as raw edit plus generic D-Bus
signal replay.

Use one of these approaches, in descending preference:

1. Replay individual keys with `kwriteconfig6 --notify`, verifying both the
   resolved value and the intended config layer.
2. Use a small compiled helper linked to KConfig if an operation cannot be
   represented by `kwriteconfig6`.
3. If byte-exact raw restoration is unavoidable, restore while the affected
   desktop components are stopped and restart them cleanly. Do not synthesize
   an internal notification protocol with a generic emitter.

### P0: isolate protocol experiments

Experiments involving custom D-Bus messages must use an isolated bus such as
`dbus-run-session`, a nested/disposable Plasma session, or a VM. An isolated
bus without real KDE listeners is adequate for checking wire signatures; a
nested or disposable desktop is required for receiver behavior.

Do not test receiver behavior against the primary desktop session.

### P1: add a static regression guard

Add a test or CI grep which fails if production code, docs with executable
recipes, or Claude automation introduces a generic emitter for this interface.
For example, reject a match combining:

```text
gdbus emit|dbus-send
org.kde.kconfig.notify.ConfigChanged
```

Documentation may retain the incident command only inside a clearly marked
non-executable postmortem.

### P1: treat live-desktop commands as consequential actions

Before any command that writes KDE config, changes outputs, creates/removes
virtual desktops, reloads a theme, or broadcasts on the session bus:

- state the expected impact;
- create and name the backup/snapshot;
- define the exact rollback;
- use `trap` for file and process cleanup;
- verify effective state after the command;
- verify that memory has not begun growing unexpectedly;
- avoid combining mutation, protocol discovery, and cleanup in one opaque
  shell block.

`trap` remains important, but it would not have prevented this incident: the
signal had already reached every listener.

### P1: add a live-session resource canary

For approved live KDE tests, sample the principal receivers before and for at
least 15 seconds after the action:

```sh
ps -C kwin_wayland -C plasmashell -C kded6 -C kwalletd6 \
   -o pid,comm,rss,vsz
free -h
```

Abort the broader workflow on rapid multi-process growth. This is a secondary
control, not permission to perform unsafe bus experiments.

### P2: improve host resilience, without mistaking it for a fix

The host has 64 GiB RAM but only 512 MiB swap. Increasing swap and enabling an
early userspace OOM policy could make ordinary pressure less destructive.
Neither would reliably contain a 50 GiB cross-process allocation burst in
seconds, so this is defense in depth only.

## Suggested repository follow-up checklist

- [ ] Add the P0 live-bus prohibition to `CLAUDE.md`.
- [ ] Amend `docs/restore-design.md` so raw restore never implies generic
      manual KConfig signal emission.
- [ ] Add a static test rejecting that emitter/interface combination.
- [ ] Document an isolated `dbus-run-session` protocol-test harness.
- [ ] Add optional before/after memory sampling around approved live actions.
- [ ] Keep the existing backup, snapshot, read-back, and bounded-monitor rules;
      they worked and made this incident diagnosable.
- [ ] Separately fix the unrelated systemd restart storm noted above, since
      it floods the journal and makes incident triage harder than it needs
      to be.

## Useful verification commands

```sh
# Prove the machine did not reboot
uptime -s
journalctl --list-boots --no-pager

# Reconstruct the incident boundary
journalctl -b --since '2026-08-02 18:15:05' \
  --until '2026-08-02 18:16:55' -o short-iso-precise

# Kernel OOM evidence
journalctl -b -k --since '2026-08-02 18:15:20' \
  --until '2026-08-02 18:15:52' -o short-iso-precise

# Pre-incident memory time series
sar -r ALL -f /var/log/sysstat/sa02 -s 18:00:00 -e 18:20:00
sar -S -W  -f /var/log/sysstat/sa02 -s 18:00:00 -e 18:20:00

# Transcript anchors (do not rerun the emitted command)
rg -n 'Capture the KConfig notify|hand-emitted|corrected type|gdbus emit' \
  <your Claude Code session transcript>.jsonl
```

## Bottom line

The desktop session died because a generic D-Bus tool emitted a KConfig signal
with a subtly wrong nested type to every live KDE client. KDE/Qt receivers then
ballooned until the kernel killed KWin. The right corrective action is not
merely better cleanup: it is to forbid hand-emitting internal KConfig signals
on the primary session bus and move all protocol experiments to an isolated
environment.
