# Open issue — the door ring flickers

**Status:** open, narrowed to the door's signal path. Not a show-stopper: the
ring lights and animates correctly, it just corrupts a frame now and then.
**Opened:** 2026-08-21 · **Firmware at the time:** v5.33 · **Rig:** 2× Jewel 7
(RGBW, towers) + Ring 12 (RGB, door), 74AHCT125 level shifter, ESP32-S2 Feather.

Read this before spending an evening on the same theories. Everything below
that says "ruled out" has evidence next to it, and the evidence is repeatable.

## The symptom, in the operator's words

> when running the ring on the larger circle sometimes a random red, blue,
> green, yellow light will show up not on the actual pattern … bars also will
> get a random colour change on the large ring every 30 seconds or so, two or
> three pixels will flicker and flicker back, sometimes it is more often

Only the **door ring** does this. The two tower jewels look right. It happens
under a static test pattern (`bars`, redrawn every 500 ms) and under `chase`,
so it is not a scene, a cue, or an effect: a frame arrives wrong, and the next
frame — half a second later — puts it right.

## What it is not

Each line was tested, not reasoned about.

| Suspect | Evidence it is not this |
|---|---|
| A blocked component loop (long `light` operations) | 90 s of live logs streamed over the native API while `bars` ran: **silent**. The "took a long time for an operation" warnings only ever appear during boot (eInk refresh + WiFi connect). |
| The RMT refill ISR dying in a flash-cache blackout | `CONFIG_RMT_ISR_IRAM_SAFE=y` is already set by ESPHome (see `sdkconfig.castle-sd`), along with `RMT_TX_ISR_HANDLER_IN_IRAM` and `RMT_ENCODER_FUNC_IN_IRAM`. The ISR runs from IRAM and survives cache disables. |
| GPIO16 being the S2's `XTAL_32K_N` pin | `CONFIG_RTC_CLK_SRC_INT_RC=y` — the RTC runs off the internal RC oscillator, so GPIO15/16 are plain GPIOs. Nothing else is driving the pad. |
| ESPHome's `esp32_rmt_led_strip` misusing the peripheral | Read the driver: it calls `rmt_tx_wait_all_done()` before every frame, waits the 50 µs WS2812 latch, uses a queue depth of 1, and its encoder callback is `IRAM_ATTR`. One buffer per strip. Nothing overlaps. |
| The scene's 30 s audio re-trigger starving the ISR | **Not actually ruled out — re-test.** v5.33 made every manual override run `scene_stop` first, but until v5.35 `scene_stop` never stopped the scene *scripts*: Vigil's pending 30 s delay survived it and re-fired, audio and all, under every "quiet board" test (found 2026-08-22, with the castle reporting `scene: vigil` minutes after a stop). The 30 s cadence may not have been a coincidence. Test 1 below still stands on its own: a lone channel corrupted with the towers off. |
| Contention between the three strips' RMT channels | **The decisive test.** Both towers driven `off` (no RMT traffic at all from them), ring alone on `bars@50`: *still flickers*. One channel transmitting by itself cannot be starved by the other two. |

## What is left

The corruption is on the **door's own path between GPIO16 and the ring's DIN**,
or in the ring itself. Everything upstream of the pad is now accounted for.

The prime suspect is the level shifter. From `castle-hardware-state` and
`docs/WIRING.md`: this **74AHCT125 has already lost channel 3** (its output
stage read dead on the bench, and tower R was moved to channel 4 because of
it). The door runs on **channel 2** of that same chip. A part that has killed
one channel is a part to distrust; a marginal output stage gives exactly this
picture — mostly-good edges with an occasional bit that lands wrong.

Runners-up, in order: the door's wire run (it is the longest, and it is the
one that leaves the board area), its 470 Ω series resistor / DIN joint, the
ground return between the ring and the shifter, and last, the ring itself.

## The next tests, in the order worth doing

1. **The swap test — highest information, no parts needed.** Move the door's
   data wire to tower L's shifter channel and tower L's to the door's, leaving
   both strips physically where they are, and update `pin_towerL` / `pin_door`
   in `firmware/castle.yaml` to match. Then run `bars`.
   *Flicker follows the channel* → the 74AHCT125 is failing, replace it (it has
   form). *Flicker follows the ring* → the ring, its wire or its joints.
2. **Bypass the shifter.** GPIO16 → 470 Ω → ring DIN, direct. 3.3 V data is out
   of spec for a 5 V WS2812 but works at short range far more often than not.
   Clean output here indicts the shifter; unchanged output indicts the wire or
   the ring.
3. **Shorten and separate.** Run the door on the shortest wire that reaches,
   away from the speaker leads and the 5 V run. WS2812 data is a 800 kHz square
   wave with ~5 ns edges; a long unshielded run beside a switching load picks up
   exactly the kind of noise that flips one bit per few seconds.
4. **Decouple the ring.** 100 nF across the ring's own 5 V/GND right at the
   board, plus the bulk cap if it is not already there. A ring that sags its own
   rail on a colour change can misread the next bit.

A note for whoever runs these: **read the fingerprint, not just "it glitched".**
A lost or extra bit on the wire shifts every pixel *after* the fault — under
`bars` (R,G,B repeating) the whole ring past that point appears to rotate for
one frame. A starved refill instead leaves the *tail* of the ring holding its
previous frame. The two look different once you know to look, and they point at
different halves of the system.

## The software lever held in reserve

The ESP32-S2's whole RMT peripheral is 4 channels × 64 symbols = **256, no
DMA**. `tools/gen_rig.py` now spends that budget explicitly, per zone, and
refuses a total the hardware cannot back (`RMT_TOTAL_SYMBOLS`, and the
`RMT: 192 of 256 symbols spent, 1 block(s) spare` line it writes into
`firmware/generated/lights.yaml`).

Giving the door a **second block** halves how often its refill ISR must run —
the deadline goes from ~40 µs to ~80 µs:

```yaml
# scenes/scenes.yaml, the door's zone entry
- {id: door, channel: 3, name: "Doorway, centre", pin: 16,
   fixture: ring12, rgbw: false, rmt_symbols: 128}
```

The only free block belongs to the SD build's **status pixel** (the onboard
NeoPixel, `castle_sd.yaml`), so this is a straight trade: **flicker margin on
the ring, or the onboard status LED**. It is not the leading fix — test 1 above
proved a lone channel still corrupts, and more buffer does not fix a wire — but
it is one edit, and worth trying if the hardware tests come back clean.

## Reproducing it in ten seconds

Desk → **🏰 Castle** → **strip test**. Or from a terminal:

```bash
curl -X POST "http://10.27.27.247/api/light?c=bars@50"
```

`towerL:off` / `towerR:off` isolate the ring; `c=show` hands the pixels back to
the scene engine. Any override except `show` stops the scene first, so the
board is quiet while you watch. Patterns: `bars` (R/G/B repeating — colour
order, pixel count, dead pixels), `chase` (one dot walking — where it stops is
where the data stops), `ends` (first red, last blue — which end the data goes
in). `@25`…`@100` sets brightness.

## Related fixes that landed while chasing this

They are separate bugs found on the way, all on the porch build and verified:

- **v5.31** — `castle_sd.yaml` still carried bench.yaml's `pin_towerL: "33"`,
  so the porch build drove the Feather's onboard NeoPixel as tower L and the
  real left jewel got no data at all. That was the "left tower shows garbage
  and strobes white" report.
- **v5.31** — parking a colour left the RGBW white channel at whatever the
  effect last wrote, so "red" came out pink on the jewels.
- **v5.35** — `scene_stop` now stops the scene scripts as well as their
  output; before, a stop was a 30 s pause before Vigil came back.
- **v5.33** — the onboard NeoPixel's power rail stayed on with nothing driving
  its data line, so it held power-up garbage. It is now a status-only pixel
  (blue booting, amber OTA, red no-card, dark otherwise) that no scene and no
  `/api/light` can address.
- **v5.30** — unrelated to light, found in the same session: ESPHome walks a
  script's action chain recursively to stop it, so `run_scene` stopping the
  457-action Citizens scene overflowed the 8 KB loop stack and **panicked the
  castle on every scene change**. Scenes are emitted in ≤32-action chunks now.
