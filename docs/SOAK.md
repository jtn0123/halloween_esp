# Soak and power-cycle — proving a castle before it leaves

Two unattended tools for docs/PRODUCTION-TODO.md §2: `tools/soak.py` leaves
a castle running for days and watches it, `tools/power_cycle.py` switches it
off and on through a smart plug, again and again. Both end in a PASS/FAIL
verdict on screen and in a folder of logs; neither needs anyone watching.
Both have been run against the emulator only (tests/test_soak.py,
tests/test_power_cycle.py). **Neither has been run on hardware yet** — the
first real run is the evidence §2 asks for, and its verdict folder is what
to keep.

Both name the castle the way every tool does (`tools/hosts.py`): an IP, an
mDNS name (`castle-a1b2c3.local` on the buyer build), or a `devices.toml`
name. The castle key, if one is set, is sent from `devices.toml` /
`CASTLE_KEY`; nothing they use needs it.

## The soak

```sh
make soak HOST=192.168.1.20 HOURS=72
make soak HOST=castle-a1b2c3.local HOURS=72 ARGS="--drive show"
.venv/bin/python tools/soak.py 192.168.1.20 --hours 72 --drive scenes --scene-every 600
```

What it asks, every `--interval` (10 s): `/api/status`. Every third poll,
and whenever something changed: `/api/health` and `/api/events`. At the
start and after every reboot: `/api/bootlog`, saved beside the log.

What it writes, under `soak-logs/<host>-<time>/` (`--out` to choose;
`soak-logs/` is gitignored):

| File | What |
| --- | --- |
| `soak.jsonl` | Every reply and every unanswered poll, one JSON object a line, flushed as written. Kinds: `start`, `status`, `health`, `event` (each new ring line, once), `miss`, `gap` (an outage that ended: length, whether it rebooted), `note` (what was printed), `drive`, `disrupt`, `bootlog`, `pause`, `verdict`. |
| `summary.json` | The running numbers, rewritten every 30 polls — a run that is killed still leaves them. |
| `verdict.txt` | The verdict, one line a check. |
| `bootlog-N.txt` | The castle's boot log at the start (1) and after each reboot. |

Exit status: 0 pass, 1 fail, 2 the soak could not run. Ctrl-C (or `kill`)
ends it early and still writes a verdict, with "ran the full time" failed.

**Driving the show.** `--drive show` posts `/api/show/start` (the evening
playlist) and checks `show_on`; it asserts it again after any reboot.
`--drive scenes` starts one scene every `--scene-every` seconds, in turn
(`--scenes a,b` or the castle's own list), and checks each one reaches
`playing` within `--start-timeout`. Either stops what it started at the end.

**This computer.** The soak keeps the monitoring machine awake (macOS
`caffeinate`, Windows `SetThreadExecutionState`; on Linux, run it under
`systemd-inhibit`). If the machine sleeps or stalls anyway, the soak notices
— the clock jumped past its next poll — logs a `pause`, and reports the
minutes it did not watch as INFO rather than blaming the castle. Run it on a
machine that stays plugged in.

### The checks and their defaults

Every limit is a flag; `tools/soak.py --help` lists them all.

| Check | Default | Why |
| --- | --- | --- |
| reboots | `--max-reboots 0` | A castle left alone should never restart. Uptime going back, or `/api/health`'s `boots` rising, is a reboot — including one hidden inside a Wi-Fi outage. |
| crashes | `--max-crashes 0` | A reboot whose reset reason is a panic, a watchdog or a brownout (`castle_health.h` `was_crash`). |
| Wi-Fi outages | `--max-outages 3` | A stretch with no answer of at least `--outage-min-s` (20 s). Shorter is a blip: counted, never judged. |
| longest outage | `--max-outage-s 300` | Five minutes covers a router's own reboot plus the castle rejoining. Longer means it did not come back unaided. |
| answering at the end | — | A castle still gone when the run ends fails, whatever else it did. |
| card read errors | `--max-sd-errors 0` | `sd_read_errors` (v5.61): torn transfers off the card. Counted from the start of the run, per boot. `sd_last_error` names the last one. |
| card unmounted polls | `--max-unmounted 0` | `sd_mounted:false` in status, even once. |
| heap low-water | `--heap-floor-kb 20` | `heap_min_kb` (v5.62), the lowest the heap has been since boot. ~20 KB is where the castle starts failing allocations. |
| heap trend | `--max-heap-fall-kb-h 2` | The slope of `heap_free_kb` over the longest single boot. Judged only once that boot spans `--heap-trend-min-h` (6 h); a steady fall is a leak. |
| weak signal | `--rssi-floor -80`, `--max-weak-rssi-pct 20` | `rssi` (v5.62; 0 = not joined, ignored). Weak for a fifth of the night means the castle sits too far from the router. |
| sync drift | `--max-drift-ms 250` | `sync_drift_ms` (v5.72): how far the lights wandered from the sound. INFO until a cue show has played. |
| light frames evicted | `--max-light-evicted -1` | Reported only: a desk streaming lights drops frames by design. Set a number to judge it. |
| failed show starts | `--max-failed-starts 0` | `--drive` only. A start the castle refused (HTTP code) or that never reached `playing`. |

A reading the firmware does not report is INFO ("not reported"), never a
pass or a fail, so an older image is not judged on a field it lacks.

**Reset reasons.** `/api/health` has carried `last_reset` and `was_crash`
since well before v5.74. Firmware v5.75 adds `reset_reason`; the soak reads
`reset_reason` first, from status or health, and falls back to `last_reset`
(`tools/castle_probe.py`). Each reboot's reason is in the verdict line and
in `summary.json` `reset_reasons`.

## Wi-Fi loss and return (a router reboot)

The soak's outage detection is the test: a router reboot is one outage that
ends with the castle answering again, ideally with no reboot. To make it
happen on a schedule, give the soak the commands that switch the router off
and on — a second smart plug — and the hours to run them:

```sh
.venv/bin/python tools/soak.py 192.168.1.20 --hours 6 --drive show \
    --disrupt-at 1,3.5 --disrupt-hold 30 \
    --disrupt-cmd "kasa --host 192.168.1.31 off" \
    --disrupt-cmd "kasa --host 192.168.1.31 on"
```

Each `--disrupt-cmd` is one step, run in order `--disrupt-hold` seconds
apart (default 30). A step is a program and its arguments with no shell in
between (`tools/operator_cmd.py`), so `&&`, pipes and `sleep` do not work in
one — the hold is the sleep, and anything more is a script the step names.
The steps run on their own thread, so the polls go on while the router is
down. A run that is stopped mid-disruption cuts the hold short and still runs
the rest, so the router is not left off. Each step's exit status and the
output go into the log (`disrupt`), and a step that exits non-zero, hangs or
cannot start fails the verdict. A PASS means: each disruption is one outage, none
longer than `--max-outage-s`, no reboot, and the castle answering at the end.
Its own `wifi_down` / `wifi_up` lines from the event ring are in the log
with the castle's timestamps.

Things to know:

- The monitoring computer loses the network too. Use a wired connection if
  you can, and expect the outage to include the router's own boot (one to
  three minutes on most routers).
- On the **buyer** build the castle puts up its `Castle-XXXX` setup network
  after **3 minutes** without its router (`ap_timeout`, firmware/castle_buyer.yaml)
  and keeps trying the saved network the whole time. A router that takes
  longer than that is the case to try deliberately: hold it off for four or
  five minutes (`--disrupt-hold 300`) and raise `--max-outage-s` to match — the castle
  must still come back on its own once the router does.
- With no second plug, the same run is a soak with the router switched off
  and on by hand; the verdict is the same. The plug is what makes it repeatable.

## The power-cycle torture

```sh
make power-cycle HOST=192.168.1.20 CYCLES=50 \
    OFF='kasa --host 192.168.1.30 off' ON='kasa --host 192.168.1.30 on'
```

`--off-cmd` / `--on-cmd` are any commands, run as a program and its
arguments with no shell (the same rule as `--disrupt-cmd`); nothing is built
in for a particular plug. Some that work:

| Plug | off | on |
| --- | --- | --- |
| TP-Link Kasa / Tapo (`pip install python-kasa`) | `kasa --host 192.168.1.30 off` | `kasa --host 192.168.1.30 on` |
| Shelly (Gen 1 HTTP API) | `curl -fsS "http://192.168.1.30/relay/0?turn=off"` | `curl -fsS "http://192.168.1.30/relay/0?turn=on"` |
| Tasmota | `curl -fsS "http://192.168.1.30/cm?cmnd=Power%20Off"` | `curl -fsS "http://192.168.1.30/cm?cmnd=Power%20On"` |
| Home Assistant | `curl -fsS -X POST http://homeassistant.local:8123/api/webhook/castle-off` | the `castle-on` webhook |

Each cycle: off, wait for the castle to stop answering (`--down-timeout`,
30 s — if it never does, the plug is not switching it and the run stops with
exit 2), hold it off `--off-s` (10 s, so the capacitors drain and the next
start is cold), on, and time until `/api/status` answers. Then the castle has
to come back whole: an uptime that proves it restarted, a firmware version,
the card mounted and the scene list read (`--ready-timeout`), a reset reason
that is not a crash — a `BROWNOUT` at power-up is what this exists to catch —
and a scene (`--scene`, default the castle's first) that reaches `playing`
and stops again. A boot slower than `--max-boot-s` (60 s) fails its cycle.
After `--stop-after` failures in a row (3) the run stops rather than hammer
a castle that is down.

Logs go to `soak-logs/power-<host>-<time>/`: `cycles.jsonl` (one line a
cycle: boot time, ready time, uptime, version, reset reason, scene,
failures), `summary.json` (boot min / median / max) and `verdict.txt`.
Whatever happens — a failure, Ctrl-C, a plug command that errors — the
castle is switched back on before the tool exits.

A power-on boot on the yard build starts the show by itself (boot_play); the
cycle's own scene start replaces it and its stop leaves the castle quiet.
The buyer build boots silent.

## What §2 still needs from a person

Only the hardware: a castle on the bench, a smart plug, and the machine left
running. The tools do the watching and the judging. Record the verdict
folders (they are gitignored) with the unit's record — docs/SUPPORT.md.
