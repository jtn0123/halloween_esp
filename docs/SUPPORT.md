# Supporting a castle someone else owns

For the seller (docs/PRODUCTION-TODO.md §8). The owner's side is
docs/OWNER-GUIDE.md; the operator's everyday workflows are docs/RUNBOOK.md;
cutting a release is docs/RELEASING.md. Everything here is about a castle
you cannot walk over to.

## What the owner sends you

**Report a problem** on the castle's own page (`/owner`, v5.75) saves one
file, `castle-report-<time>.txt`: when it was made, the version, board and
build, then the four replies below under `== /api/status ==` headings. Ask
for that file. A castle on v5.74 or older has no such button: ask for the
same four replies, each opened in a browser on the owner's Wi-Fi and saved
or screenshotted:

```
http://castle-xxxxxx.local/api/status
http://castle-xxxxxx.local/api/health
http://castle-xxxxxx.local/api/events
http://castle-xxxxxx.local/api/bootlog
```

All four are open on a castle with a key. Field meanings, and the firmware
version that added each, are in docs/API.md; the ones that answer most
questions:

| Look at | What it tells you |
| --- | --- |
| status `version`, `fw_variant`, `board` | Which image, which build (`buyer`), which board. Compare with the unit's record below. |
| status `uptime_s` | Seconds since it last started. Small when the owner says "it's been on all day" = it rebooted. |
| health `boots`, `crashes`, `last_reset`, `was_crash` | How many starts this season, how many were crashes, and why it last started. `power-on` is a plug; `software` is a restart or an update; `PANIC`, the watchdogs and `BROWNOUT` are crashes, and since v5.75 `power-glitch` and `cpu-lockup` too. Brownouts point at the power supply. |
| status `sd_mounted`, `missing`; health `sd_read_errors`, `sd_last_error` | The card. Unmounted = reseat or replace it. A missing name = a publish that did not finish. Read errors on one file (`sd_last_error` is `<path>@<offset>`) = that file; on many = the card is dying. |
| status `heap_free_kb`; health `heap_min_kb` | Free memory now, and the lowest it has been since boot. Under ~20 KB is trouble (RUNBOOK, "When a scene will not play"). |
| status `rssi` | Signal in dBm; 0 = not on Wi-Fi. Worse than about -80 = the castle is too far from the router. |
| status `sync_lead_ms`, `sync_drift_ms` | How far the lights would have drifted from the sound on the last card show; -1 until one has played. |
| status `light_evicted` | Light frames a page streamed that the castle dropped. Nonzero during a desk session is normal. |
| events | The last 64 things the castle did, oldest first, stamped in milliseconds since boot: `scene_start`, `stop`, `wifi_down` / `wifi_up` (with rssi), `http_err`, `scene_missing`, `restart`, `sound` / `silent`. Read it against `uptime_s` to put times on them. |
| bootlog | The card mount and listing from the last start; after a crash, the tail of the previous life's event ring. |

The quickest triage:

- **Does not answer at all:** power, then Wi-Fi. If the owner can see a
  `Castle-XXXX` network, the castle is up and has lost its router: they set
  the Wi-Fi again (Owner's guide, "New router").
- **Answers, uptime small, `was_crash` true:** a crash. Note `last_reset`;
  a `BROWNOUT` with the show at full volume and brightness is the supply.
- **Red status light / `sd_mounted:false`:** the card.
- **A show starts and stops at the same place:** `sd_read_errors` and
  `sd_last_error` name the file.

## Reading a soak or power-cycle log

docs/SOAK.md explains the runs. A verdict folder holds:

1. `verdict.txt` — read this first: one line a check, in the order a bad
   night is read (reboots, crashes, outages, card, heap, radio, drift).
2. `summary.json` — the numbers behind each line (`reset_reasons`,
   `outages` with their lengths and whether each hid a reboot, the heap
   trend over the longest boot, the event counts).
3. `soak.jsonl` — everything, in time order. With `jq`:

```sh
jq -r 'select(.kind=="note") | "\(.at)  \(.text)"' soak.jsonl      # the story
jq -c 'select(.kind=="gap")' soak.jsonl                            # each outage
jq -r 'select(.kind=="health") | [.at, .reply.heap_min_kb, .reply.sd_read_errors] | @tsv' soak.jsonl
jq -c 'select(.kind=="event")' soak.jsonl                          # the castle's own record
```

4. `bootlog-N.txt` — the boot log after each restart; a crash's tail is
   here.

A power-cycle folder has `cycles.jsonl` (one line a cycle: boot time, card
ready time, reset reason, the scene it played, and why it failed) and the
boot-time spread in `summary.json`.

## The per-unit record — kept by you, never in this repo

The repo is public; a file inside the checkout is one `git add -A` away
from being published, so there is deliberately no place for this here. Keep
one record per castle in your own notes, and attach its soak and
power-cycle verdict folders (`soak-logs/` is gitignored — copy them out).

```
Unit:            CASTLE-2026-___        (your own serial, also on the label)
MAC:             __:__:__:__:__:__      (castle-xxxxxx is its last six hex
                                         digits; `esptool.py read_mac` over
                                         USB prints the whole thing)
Board / carrier: Feather S3 #5477 (feather-s3-4m2p) in carrier v3.__
Firmware:        release v_._._  =  castle v5.__ (status `version`)
Card:            __ GB, published from commit ________ on ____-__-__
Power supply:    ________ (rating)
Soak:            ____-__-__, __ h, PASS/FAIL  (folder: ______________)
Power cycles:    ____-__-__, __ of __ whole, boot max __ s
Castle key:      set by the owner? yes / no   (never write the key down here)
Handed over:     ____-__-__
Owner:           name, how to reach them
Notes:
```

## Before a castle leaves

- The card: what is on it, and which page the castle will serve. A card
  with a published `/site/` page shows Castle Radio's page at `/`; without
  one the castle shows its built-in owner page there. The owner page, which
  the Owner's guide describes (Settings, Factory reset, Report a problem),
  is at `/owner` either way. Check that the guide matches.
  `make buyer-card TAG=<the image's release tag>` writes the card into
  `buyer-card/`: the shipped show (no songs), no `/site/`, and the licence notices
  plus the written source offer under `licenses/`, which the owner page
  links (docs/LICENSING.md decision 2). Copy the directory's contents onto
  the root of a FAT32 card.
- The image: the buyer build (`fw_variant: buyer` in status) from a tagged
  release, not a local build.
- The record above, filled in, and the label printed (PRODUCTION-TODO §3).

## Release branches

- **Buyer releases are tagged from `main`** (docs/RELEASING.md): `make check`
  green, the firmware workflow green on that commit, then `git tag vX.Y.Z`.
- **A fix that must reach a buyer without everything `main` has gained
  since** goes on a `release/X.Y` branch cut from their tag, and only fixes
  go there:

  ```sh
  git switch -c release/0.1 v0.1.0       # once per release line
  git cherry-pick -x <sha-of-the-fix>    # the fix lands on main FIRST
  git tag v0.1.1 && git push origin release/0.1 v0.1.1
  ```

  The release workflow builds from the tag, so nothing else changes. The
  branch never merges back (main already has the fix); delete it when no
  castle runs that line.
- **A patch on an older line becomes GitHub's "latest" release** when it is
  published, and the web flasher redeploys from "latest". If a newer full
  release exists, put it back: `gh release edit vNEWER --latest`, then
  `gh workflow run pages.yml`. The app's updater only ever moves forward, so
  it does not offer the older patch to a castle already past it.
