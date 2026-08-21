/**
 * The zone designer — try per-pixel texture on the live preview, then take
 * the YAML home.
 *
 * Three rows (towerL / towerR / door), each with the four texture knobs the
 * engine grew: centre-pixel role, overlay, palette, phase. Changes hit the
 * running ShowState immediately, so the stage and the jewel insets show the
 * result in the same frame — and the mirrored castle plays the same scene,
 * so what you dial in here is what the porch would do once the YAML ships.
 *
 * Deliberately edits the PREVIEW, not the file: the output is a `zones:`
 * snippet to paste into scenes/scenes.yaml, where the change becomes real and
 * generated into firmware. Loading another scene resets the knobs to that
 * scene's own texture — the designer says so rather than pretending
 * otherwise.
 */

import type { ShowState } from "./show.js";
import type { EffectName, ZoneId } from "./types.js";
import { OVERLAY_NAMES, PALETTE_NAMES } from "./effects.js";

const ZONES: readonly ZoneId[] = ["towerL", "towerR", "door"];
const EFFECT_CHOICES: readonly string[] = [
  "off", "candle", "ember", "furnace", "spirit", "eyes", "seance", "wisp",
  "mansion", "chill", "throb", "strobe", "blood",
];

export interface ZoneDesigner {
  /** Re-read the (new) scene's texture into the controls. */
  refresh(): void;
}

export function createZoneDesigner(getState: () => ShowState): ZoneDesigner {
  const host = document.getElementById("zoneDesigner");
  if (!host) return { refresh: () => {} };

  const sel = (opts: readonly string[], cur: string, cls: string, z: string): string =>
    `<select class="${cls}" data-z="${z}">` +
    opts.map((o) => `<option${o === cur ? " selected" : ""}>${o}</option>`).join("") +
    `</select>`;

  const render = (): void => {
    const st = getState();
    host.innerHTML =
      `<table class="zd"><tr><th></th><th title="Effect for the centre pixel only">centre</th>` +
      `<th title="Composited over the base effect">overlay</th>` +
      `<th title="Poles for the crossfade effects">palette</th>` +
      `<th title="Seconds added to this zone's clock">phase</th></tr>` +
      ZONES.map((z) =>
        `<tr><td>${z}</td>` +
        `<td>${sel(["ring", ...EFFECT_CHOICES], st.centerEff[z] ?? "ring", "zdC", z)}</td>` +
        `<td>${sel(OVERLAY_NAMES, OVERLAY_NAMES[st.overlay[z]] ?? "none", "zdO", z)}</td>` +
        `<td>${sel(PALETTE_NAMES, PALETTE_NAMES[st.palette[z]] ?? "haunt", "zdP", z)}</td>` +
        `<td><input class="zdF" data-z="${z}" type="number" min="0" max="5" step="0.1"` +
        ` value="${st.phase[z]}" style="width:3.5rem"></td></tr>`).join("") +
      `</table>` +
      `<div><button id="zdYaml" type="button" title="Copies this scene's zone settings ` +
      `as the zones: block for scenes/scenes.yaml">Copy these zone settings</button>` +
      `<span id="zdNote" class="muted"> edits the preview only — paste into ` +
      `scenes.yaml (the Scenes file) to keep</span></div>` +
      `<pre id="zdOut" style="display:none"></pre>`;

    host.querySelectorAll<HTMLSelectElement>(".zdC").forEach((el) =>
      el.addEventListener("change", () => {
        const st2 = getState();
        st2.centerEff[el.dataset.z as ZoneId] =
          el.value === "ring" ? null : (el.value as EffectName);
      }));
    host.querySelectorAll<HTMLSelectElement>(".zdO").forEach((el) =>
      el.addEventListener("change", () => {
        getState().overlay[el.dataset.z as ZoneId] =
          (OVERLAY_NAMES as readonly string[]).indexOf(el.value);
      }));
    host.querySelectorAll<HTMLSelectElement>(".zdP").forEach((el) =>
      el.addEventListener("change", () => {
        getState().palette[el.dataset.z as ZoneId] =
          (PALETTE_NAMES as readonly string[]).indexOf(el.value);
      }));
    host.querySelectorAll<HTMLInputElement>(".zdF").forEach((el) =>
      el.addEventListener("input", () => {
        getState().phase[el.dataset.z as ZoneId] = Number(el.value) || 0;
      }));

    host.querySelector<HTMLButtonElement>("#zdYaml")!
      .addEventListener("click", () => {
        const st2 = getState();
        const lines: string[] = ["    zones:"];
        for (const z of ZONES) {
          const bits: string[] = [];
          if (st2.centerEff[z]) bits.push(`center: ${st2.centerEff[z]}`);
          if (st2.overlay[z]) bits.push(`overlay: ${OVERLAY_NAMES[st2.overlay[z]]}`);
          if (st2.palette[z]) bits.push(`palette: ${PALETTE_NAMES[st2.palette[z]]}`);
          if (st2.phase[z]) bits.push(`phase: ${st2.phase[z]}`);
          if (bits.length) lines.push(`      ${z}: {${bits.join(", ")}}`);
        }
        const out = host.querySelector<HTMLPreElement>("#zdOut")!;
        out.textContent = lines.length > 1 ? lines.join("\n")
                                           : "    # all zones at defaults";
        out.style.display = "block";
        void navigator.clipboard?.writeText(out.textContent);
      });
  };

  render();
  return { refresh: render };
}
