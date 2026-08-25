# Wiring the castle — build order and fixture choices

Split from [WIRING.md](WIRING.md) alongside
[WIRING-POWER-AUDIO.md](WIRING-POWER-AUDIO.md); original section numbers kept.

## 6. Build order

Do it in this order and each step proves the one before it.

1. **Grounds first.** Supply, Feather, shifter, both amps. Confirm continuity
   with a meter before anything is powered.
2. **Power the shifter alone.** 5 V on 14, GND on 7, the three `OE` pins to
   GND. Check pin 14 reads 5 V.
3. **One zone.** Wire tower L only — GPIO18 → `1A`, `1Y` → 470 Ω → `DIN`, plus
   5 V/GND and the capacitor. Boot it. You already know what a working Jewel
   looks like, so this is the honest test of the shifter.
4. **The other two zones.** Same pattern on GPIO16 and GPIO14. Set the rig in
   the app first so the firmware knows the counts (§7).
5. **One amp.** Three signal wires, 5 V, ground, `SD` left unconnected. Play a
   scene and confirm *full* volume. If it's quiet, suspect `channel:` in the
   speaker block, not the wiring (WIRING-POWER-AUDIO §5).
6. **The second amp.** Same three signal wires. Both should now be equally
   loud.
7. **Measure the real peak.** Run the Storm scene with a clamp meter or an
   inline USB meter on the 5 V bus. Compare it against the app's estimate
   before you close the enclosure.

## 7. Choosing the fixtures without soldering them

The cue desk carries the rig as data now rather than as an assumption. The
**Rig** panel (right-hand column, under Output) has a row per spot:

- Pick a fixture and the stage, the per-pixel view and the channel strip all
  change on the next frame. A chase really does walk sixteen pixels round a
  Ring 16; a meteor really does fall down the FeatherWing's four rows.
- The RGBW tickbox is only offered where the part comes both ways. The
  FeatherWing and the mini PCBs are RGB and say so.
- It totals the peak draw as you go, and warns when the mix needs separate
  data lines or the supply needs to be bigger. The 8 A figure in §4 is that
  calculation.
- **Copy firmware config** emits both halves of the change: the `zones:` block
  for `scenes/scenes.yaml` and the substitutions for `firmware/castle.yaml`.

Then, once you like it:

```bash
make generate && make validate
```

`make generate` rewrites `firmware/generated/lights.yaml` (one strip per zone)
and `firmware/generated/rig.h` (the geometry tables the render loop indexes),
then `make upload` puts it on the castle.

The preview is instant; the castle needs that reflash. Pixel counts are
compiled in, so there is no way around it — but it does mean the only thing
you flash is a rig you have already looked at.

> **The two files move together.** `scenes.yaml` is what the cue generators
> and the desk read; the substitutions are what the chip clocks out. Change
> one without the other and you get a castle whose cues are aimed at pixels it
> does not have. That is why the button emits both.
