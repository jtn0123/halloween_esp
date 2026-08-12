/**
 * Which zone each band lights, and how hard each one has to hit to count.
 *
 * Both were fixed before this existed: low always drove the door, mid always
 * towerL, high always towerR, and one sensitivity slider governed all three.
 * That is the wrong shape for real material. A track can have a crisp kick
 * under a wash of cymbals, and a single threshold either drowns the top end in
 * false hits or throws the bass away. And which lamp a band should drive is a
 * staging decision, not a property of the audio.
 *
 * The editor owns its own DOM and nothing else. It is mounted by the clip
 * editor and read by the scene generator, which is why it is neither of them.
 */

import { BANDS, type BandName } from "./bands.js";
import { CHANNELS } from "./panels.js";
import type { ZoneId } from "./types.js";

/** Detection threshold bounds. Outside these the detector is useless in one
 *  direction or the other: everything is an onset, or nothing is. */
const MIN_SENS = 0.3, MAX_SENS = 3.0, DEFAULT_SENS = 1.1;

export interface BandSettings {
  zone: Record<BandName, ZoneId>;
  sensitivity: Record<BandName, number>;
}

export interface BandEditor {
  /** Mount this where the bands should appear. */
  readonly el: HTMLElement;
  settings: () => BandSettings;
  /** Zone per band, in the shape sceneFromTrack and sceneYaml want. */
  zones: () => Partial<Record<BandName, ZoneId>>;
  /** `sens_low=..&sens_mid=..&sens_high=..` for GET /api/waveform. */
  query: () => string;
  /** True when any band differs from the default — worth writing out. */
  customised: () => boolean;
  /** Report what the last analysis found, per band, so the rows can say. */
  report: (counts: Readonly<Record<string, number>>, durationSec?: number) => void;
}

const clampSens = (v: number): number =>
  Number.isFinite(v) ? Math.min(MAX_SENS, Math.max(MIN_SENS, v)) : DEFAULT_SENS;

/**
 * @param onChange fired after any control moves. Re-analysis is the caller's
 *        job, because only it knows how expensive that is.
 */
export function createBandEditor(onChange: () => void): BandEditor {
  const zone = {} as Record<BandName, ZoneId>;
  const sens = {} as Record<BandName, number>;
  const found = {} as Record<BandName, HTMLElement>;

  const el = document.createElement("div");
  el.className = "bandcfg";

  for (const b of BANDS) {
    zone[b.name] = b.zone;
    sens[b.name] = DEFAULT_SENS;

    const row = document.createElement("div");
    row.className = "bandcfg__row";

    const swatch = document.createElement("i");
    swatch.className = "bandcfg__dot";
    swatch.style.background = b.ink;

    const name = document.createElement("span");
    name.className = "bandcfg__nm";
    name.textContent = b.label;
    name.title = `${b.lo}–${b.hi} Hz · hits no closer together than ${b.minGap}s`;

    // Zone. A select rather than free text: there are exactly three lamps,
    // and a typo here would produce a scene that renders nothing.
    const zoneSel = document.createElement("select");
    zoneSel.className = "bandcfg__zone";
    zoneSel.title = `Which zone the ${b.label} band lights`;
    for (const ch of CHANNELS) {
      const o = document.createElement("option");
      o.value = ch.id;
      o.textContent = ch.id;
      zoneSel.append(o);
    }
    zoneSel.value = b.zone;
    zoneSel.addEventListener("change", () => {
      zone[b.name] = zoneSel.value as ZoneId;
      onChange();
    });

    const slider = document.createElement("input");
    slider.type = "range";
    slider.className = "bandcfg__sens";
    slider.min = String(MIN_SENS);
    slider.max = String(MAX_SENS);
    slider.step = "0.05";
    slider.value = String(DEFAULT_SENS);
    slider.title = `Onset threshold for ${b.label} — lower finds more, and `
                 + "more of what it finds is noise";

    const read = document.createElement("span");
    read.className = "bandcfg__val";
    read.textContent = DEFAULT_SENS.toFixed(2);

    slider.addEventListener("input", () => {
      sens[b.name] = clampSens(+slider.value);
      read.textContent = sens[b.name].toFixed(2);
      onChange();
    });

    const hits = document.createElement("span");
    hits.className = "bandcfg__hits";
    hits.textContent = "—";
    found[b.name] = hits;

    row.append(swatch, name, zoneSel, slider, read, hits);
    el.append(row);
  }

  return {
    el,
    settings: () => ({ zone: { ...zone }, sensitivity: { ...sens } }),
    zones: () => ({ ...zone }),
    query: () => BANDS
      .map(b => `sens_${b.label}=${encodeURIComponent(sens[b.name].toFixed(2))}`)
      .join("&"),
    customised: () => BANDS.some(b =>
      zone[b.name] !== b.zone || Math.abs(sens[b.name] - DEFAULT_SENS) > 1e-9),
    report(counts, durationSec) {
      for (const b of BANDS) {
        const n = counts[b.name];
        const cell = found[b.name];
        if (!cell) continue;
        if (n === undefined) {
          // The band fell back to following loudness, which is a different
          // thing from finding no beats and should not read as a failure.
          cell.textContent = counts[`level_${b.label}`] !== undefined
            ? "follows loudness" : "—";
          continue;
        }
        cell.textContent = durationSec && durationSec > 0
          ? `${n} · ${(n / durationSec).toFixed(1)}/s`
          : `${n}`;
      }
    },
  };
}
