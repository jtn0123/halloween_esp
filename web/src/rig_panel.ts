/**
 * The rig panel — say what is in each window, see it immediately.
 *
 * Deciding which fixture belongs in which spot used to mean soldering one in
 * and looking at the porch. This makes the cheap half of that loop instant:
 * pick a Ring 16 for the doorway and the stage, the pixel view and the
 * channel strip all change on the next frame, along with what the rig will
 * draw from the supply.
 *
 * The expensive half is still real, and the panel says so rather than
 * implying otherwise. Pixel counts live in the firmware's `light:` blocks, so
 * the castle itself needs `make generate && make upload` before it agrees —
 * which is why the panel's other job is emitting exactly the config that
 * makes it agree. Preview freely, flash once.
 */

import {
  AUDIO_AMPS, FIXTURES, ZONE_DECL, ZONE_ORDER, ZONE_PIN, chainRanges, fixture,
  layoutOf, rigPower, rigProblems, saveRig, zoneLayout, zoneRgbw,
  type RigState,
} from "./rig.js";
import type { ZoneId } from "./types.js";

const SPOT: Record<ZoneId, string> = {
  towerL: "Tower L",
  door: "Doorway",
  towerR: "Tower R",
};

export interface RigPanel {
  /** Re-read the model into the controls (after an external change). */
  refresh(): void;
}

export interface RigHooks {
  /** Something changed: re-derive layouts, labels and pixel ranges. */
  onChange(rig: RigState): void;
}

export function createRigPanel(rig: RigState, hooks: RigHooks): RigPanel {
  const found = document.getElementById("rigPanel");
  if (!found) return { refresh: () => {} };
  // Retyped rather than used directly: `render` and `rowFor` are hoisted
  // declarations, and TypeScript will not carry a narrowing from the guard
  // above into a function that could in principle be called before it.
  const host: HTMLElement = found;

  const opts = (cur: string): string => FIXTURES.map((f) =>
    `<option value="${f.id}" title="${f.full}"${f.id === cur ? " selected" : ""}>`
    + `${f.name}${f.count ? ` · ${f.count}px` : ""}</option>`).join("");

  const commit = (): void => {
    saveRig(rig);
    hooks.onChange(rig);
    render();
  };

  function rowFor(z: ZoneId): string {
    const slot = rig.zones[z];
    const fx = fixture(slot.fixture);
    const L = zoneLayout(rig, z);
    const rgbw = zoneRgbw(rig, z);

    // A fixture that only ever shipped RGB gets a fixed label, not a control
    // you can set wrongly. The rest genuinely need answering, because the
    // packet width depends on it and only you can see which one you bought.
    const kind = fx.rgbOnly
      ? `<span class="rig__fixed" title="${fx.name} was only ever made RGB">RGB</span>`
      : `<label class="rig__rgbw" title="Tick if yours is the RGBW variant — `
        + `it changes the packet from 24 to 32 bits per pixel">`
        + `<input type="checkbox" class="rigW" data-z="${z}"${rgbw ? " checked" : ""}>`
        + `<span>RGBW</span></label>`;

    const count = fx.maxCount
      ? `<input class="rigN" type="number" data-z="${z}" min="1" max="${fx.maxCount}"`
        + ` value="${L.n}" title="How many singles are in this spot">`
      : `<span class="rig__px">${L.n}px</span>`;

    return `<tr>`
      + `<td class="rig__spot">${SPOT[z]}<small>GPIO${ZONE_PIN[z]}</small></td>`
      + `<td><select class="rigF" data-z="${z}" `
      + `title="Which fixture is in the ${SPOT[z]} spot">${opts(slot.fixture)}</select></td>`
      + `<td>${count}</td>`
      + `<td>${kind}</td></tr>`;
  }

  /** The masthead's one-line description of the hardware. It used to be
   *  "three zones · 21 px" in the template, which stopped being true the
   *  moment a fixture could be swapped. */
  function retitleMasthead(pixels: number): void {
    const eyebrow = document.getElementById("rigEyebrow");
    if (!eyebrow) return;
    eyebrow.textContent = `three zones · ${pixels} px`;
    eyebrow.title = "ESP32-S2 Feather · MAX98357A I²S amp · "
      + ZONE_ORDER.map((z) =>
        `${SPOT[z]}: ${fixture(rig.zones[z].fixture).name}`).join(" · ");
  }

  function render(): void {
    const { amps, pixels } = rigPower(rig);
    const total = amps + AUDIO_AMPS;
    const problems = rigProblems(rig);
    retitleMasthead(pixels);

    host.innerHTML =
      `<table class="rig"><thead><tr>`
      + `<th>Spot</th><th>Fixture</th><th>Pixels</th><th>Colour</th>`
      + `</tr></thead><tbody>`
      + ZONE_ORDER.map(rowFor).join("")
      + `</tbody></table>`
      // The number that decides the power supply, not the average — a
      // lightning cue drives every zone to full white at once.
      + `<p class="rig__sum" id="rigSum">${pixels} pixels · `
      + `<b>${amps.toFixed(1)} A</b> at full white, ${total.toFixed(1)} A with both amps`
      + `</p>`
      + problems.map((p) =>
        `<p class="rig__note rig__note--${p.level}">${p.text}</p>`).join("")
      + `<div class="rig__acts">`
      + `<button id="rigYaml" type="button" title="Copies the two config blocks `
      + `that make the castle agree with this preview: the zones: block for `
      + `scenes/scenes.yaml and the light: blocks for firmware/castle.yaml. `
      + `Then: make generate &amp;&amp; make upload">Copy settings for the castle</button>`
      + `<span class="muted" title="The firmware carries its own pixel counts, so `
      + `the castle only agrees after a reflash: make generate &amp;&amp; make upload">`
      + ` the preview changes now; the castle after its next reflash</span></div>`
      + `<pre id="rigOut" hidden></pre>`;

    host.querySelectorAll<HTMLSelectElement>(".rigF").forEach((el) =>
      el.addEventListener("change", () => {
        const z = el.dataset["z"] as ZoneId;
        const fx = fixture(el.value);
        // A scatter fixture keeps whatever count it had; everything else has
        // its count fixed by the part, so a stale one must not survive.
        rig.zones[z] = fx.maxCount
          ? { fixture: fx.id, count: Math.min(fx.maxCount, rig.zones[z].count ?? fx.count) }
          : { fixture: fx.id };
        commit();
      }));

    host.querySelectorAll<HTMLInputElement>(".rigW").forEach((el) =>
      el.addEventListener("change", () => {
        rig.rgbw[rig.zones[el.dataset["z"] as ZoneId].fixture] = el.checked;
        commit();
      }));

    host.querySelectorAll<HTMLInputElement>(".rigN").forEach((el) =>
      el.addEventListener("change", () => {
        const z = el.dataset["z"] as ZoneId;
        const max = fixture(rig.zones[z].fixture).maxCount ?? 1;
        rig.zones[z] = {
          fixture: rig.zones[z].fixture,
          count: Math.max(1, Math.min(max, Math.round(Number(el.value) || 1))),
        };
        commit();
      }));

    host.querySelector<HTMLButtonElement>("#rigYaml")?.addEventListener("click", () => {
      const out = host.querySelector<HTMLPreElement>("#rigOut");
      if (!out) return;
      out.textContent = emitConfig(rig);
      out.hidden = false;
      void navigator.clipboard?.writeText(out.textContent);
    });
  }

  render();
  return { refresh: render };
}

/**
 * The two blocks that make the castle agree with the preview.
 *
 * Emitted together on purpose. They have to be changed in the same breath —
 * `scenes.yaml` is what the cue generators and this desk read, and the
 * `light:` blocks are what the chip actually clocks out. Changing one alone
 * gives you a castle whose cues are aimed at pixels it does not have.
 */
export function emitConfig(rig: RigState): string {
  const single = new Set(ZONE_DECL
    .filter((z) => zoneLayout(rig, z).n > 0)
    .map((z) => zoneRgbw(rig, z))).size <= 1;
  const ranges = chainRanges(rig);

  const lines: string[] = [
    "# ─── scenes/scenes.yaml ───────────────────────────────────────────",
    "# Replaces the whole `zones:` block. `pixels_per_zone` no longer",
    "# applies once a zone declares its own count.",
    "zones:",
  ];
  ZONE_DECL.forEach((z, i) => {
    const fx = fixture(rig.zones[z].fixture);
    const L = zoneLayout(rig, z);
    lines.push(`  - {id: ${z}, channel: ${i + 1}, pin: ${ZONE_PIN[z]}, `
      + `fixture: ${fx.id}, pixels: ${L.n}, rgbw: ${zoneRgbw(rig, z)}}`
      + `   # ${fx.full}`);
  });

  lines.push(
    "",
    "# ─── firmware/castle.yaml ─────────────────────────────────────────",
    "# One strip per zone. They cannot be merged into one chain unless every",
    single
      ? "# zone is the same colour type — which, as configured here, they are."
      : "# zone is the same colour type, and this rig mixes RGBW with RGB.",
    "light:",
  );
  for (const z of ZONE_DECL) {
    const L = zoneLayout(rig, z);
    if (L.n === 0) {
      lines.push(`  # ${z}: nothing wired — no strip emitted.`);
      continue;
    }
    lines.push(
      `  - platform: esp32_rmt_led_strip`,
      `    id: zone_${z}`,
      `    pin: GPIO${ZONE_PIN[z]}`,
      `    num_leds: ${L.n}`,
      `    chipset: WS2812`,
      `    rgb_order: GRB`,
      `    is_rgbw: ${zoneRgbw(rig, z)}`,
      `    rmt_symbols: 64`,
      `    use_psram: false`,
      `    default_transition_length: 0s`,
    );
  }

  if (single) {
    lines.push(
      "",
      "# Every zone is the same colour type, so ONE chain would also work:",
      `#   num_leds: ${ZONE_DECL.reduce((n, z) => n + zoneLayout(rig, z).n, 0)}`,
      ...ZONE_DECL.map((z) => `#   ${z}: ${ranges[z][0]}–${ranges[z][1]}`),
      "# Three pins is still the better default: swapping one fixture then",
      "# renumbers nothing else. See docs/WIRING.md §1.",
    );
  }

  const { amps } = rigPower(rig);
  lines.push(
    "",
    `# Peak draw ${amps.toFixed(1)} A for the pixels at full white, plus about`,
    `# ${AUDIO_AMPS.toFixed(1)} A for the two amps. Size the 5 V supply above`,
    `# ${Math.ceil((amps + AUDIO_AMPS) * 1.3)} A. See docs/WIRING.md §4.`,
  );
  return lines.join("\n");
}

/** The catalogue as the picker shows it, for tests and for the channel strip
 *  tooltip — exported so neither has to re-derive the pixel arithmetic. */
export const fixtureSummary = (id: string): string => {
  const fx = fixture(id);
  return `${fx.name} — ${layoutOf(fx).n} pixels`;
};
