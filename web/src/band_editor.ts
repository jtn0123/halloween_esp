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
  /** Which bands the AUDITION plays; false = muted. Exports ignore this —
   *  mute is a listening tool, not a scene decision. */
  active: () => Record<BandName, boolean>;
}

const clampSens = (v: number): number =>
  Number.isFinite(v) ? Math.min(MAX_SENS, Math.max(MIN_SENS, v)) : DEFAULT_SENS;

/**
 * @param onChange fired after any control moves. Re-analysis is the caller's
 *        job, because only it knows how expensive that is.
 * @param onMix fired when a mute/solo changes — the detection is untouched,
 *        so this needs a scene rebuild, not the re-analysis onChange buys.
 */
export function createBandEditor(onChange: () => void,
                                 onMix?: () => void): BandEditor {
  const zone = {} as Record<BandName, ZoneId>;
  const sens = {} as Record<BandName, number>;
  const found = {} as Record<BandName, HTMLElement>;
  // Mixing-console state: each band's own mute, plus at most one solo that
  // overrides them. Two things, kept as two — a solo spelled as "mute the
  // others" lit the M of every other band and never the S (JB1-11).
  const on = {} as Record<BandName, boolean>;
  let solo: BandName | null = null;
  const muteBtns = {} as Record<BandName, HTMLButtonElement>;
  const soloBtns = {} as Record<BandName, HTMLButtonElement>;
  const rows = {} as Record<BandName, HTMLElement>;

  /** Does the audition play this band right now. */
  const audible = (b: BandName): boolean => solo ? solo === b : on[b];

  const syncMix = (): void => {
    for (const b of BANDS) {
      muteBtns[b.name].classList.toggle("on", !on[b.name]);
      muteBtns[b.name].setAttribute("aria-pressed", String(!on[b.name]));
      soloBtns[b.name].classList.toggle("on", solo === b.name);
      soloBtns[b.name].setAttribute("aria-pressed", String(solo === b.name));
      rows[b.name].style.opacity = audible(b.name) ? "" : "0.45";
    }
    onMix?.();
  };

  const el = document.createElement("div");
  el.className = "bandcfg";

  for (const b of BANDS) {
    zone[b.name] = b.zone;
    sens[b.name] = DEFAULT_SENS;
    on[b.name] = true;

    const row = document.createElement("div");
    row.className = "bandcfg__row";
    rows[b.name] = row;

    const swatch = document.createElement("i");
    swatch.className = "bandcfg__dot";
    swatch.style.background = b.ink;
    swatch.title = `${b.label} band — drawn in this colour on the waveform above`;

    const name = document.createElement("span");
    name.className = "bandcfg__nm";
    name.textContent = b.label;
    name.title = `${b.lo}–${b.hi} Hz · hits no closer together than ${b.minGap}s`;

    // Zone. A select rather than free text: there are exactly three lamps,
    // and a typo here would produce a scene that renders nothing. Labelled
    // as the porch calls them — "towerL"/"towerR" both clipped to "tower"
    // in the select (JB1-9), and the one control that decides which window
    // a beat hits could not be read.
    const zoneSel = document.createElement("select");
    zoneSel.className = "bandcfg__zone";
    zoneSel.title = `Which window the ${b.label} band lights`;
    for (const ch of CHANNELS) {
      const o = document.createElement("option");
      o.value = ch.id;
      o.textContent = ch.name;
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
    read.title = `${b.label} sensitivity (the slider's value)`;
    read.textContent = DEFAULT_SENS.toFixed(2);

    slider.addEventListener("input", () => {
      sens[b.name] = clampSens(+slider.value);
      read.textContent = sens[b.name].toFixed(2);
      onChange();
    });

    const hits = document.createElement("span");
    hits.className = "bandcfg__hits";
    hits.title = `Hits detected in the ${b.label} band at this sensitivity, `
               + "and how many land per second";
    hits.textContent = "—";
    found[b.name] = hits;

    // Mute and solo, mixing-console style, for the AUDITION only. Judging
    // one band's contribution against three at once is guesswork; against
    // silence it is obvious.
    const mute = document.createElement("button");
    mute.type = "button";
    mute.className = "bandcfg__mute";
    // Inline like the style lab's chrome: styles.css is at the 500-line cap.
    mute.style.cssText = "min-width:1.8em;padding:1px 4px";
    mute.textContent = "M";
    mute.setAttribute("aria-pressed", "false");
    mute.title = `Mute the ${b.label} band in the audition (export unaffected)`;
    mute.addEventListener("click", () => {
      on[b.name] = !on[b.name];
      syncMix();
    });
    muteBtns[b.name] = mute;

    const soloBtn = document.createElement("button");
    soloBtn.type = "button";
    soloBtn.className = "bandcfg__solo";
    soloBtn.style.cssText = "min-width:1.8em;padding:1px 4px";
    soloBtn.textContent = "S";
    soloBtn.setAttribute("aria-pressed", "false");
    soloBtn.title = `Audition only the ${b.label} band — press again to hear all`;
    soloBtns[b.name] = soloBtn;
    soloBtn.addEventListener("click", () => {
      solo = solo === b.name ? null : b.name;
      syncMix();
    });

    // One grid cell for both, or they wrap to an implicit row below.
    const mix = document.createElement("span");
    mix.className = "bandcfg__mix";
    mix.style.cssText = "display:inline-flex;gap:3px";
    mix.append(mute, soloBtn);
    row.append(swatch, name, zoneSel, slider, read, hits, mix);
    el.append(row);
  }

  return {
    el,
    settings: () => ({ zone: { ...zone }, sensitivity: { ...sens } }),
    active: () => Object.fromEntries(BANDS.map(b => [b.name, audible(b.name)])) as
      Record<BandName, boolean>,
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
