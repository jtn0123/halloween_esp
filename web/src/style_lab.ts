/**
 * The style lab — judging the light engine, not designing with it.
 *
 * Two tools, both feeding the SAME audition the clip editor already runs:
 *
 *   A/B     — one button that swaps the whole strike engine between the
 *             current dynamics and the frozen pre-4K baseline (one flat
 *             colour per band, whole-jewel flashes, no movement, no
 *             sections). "Did the new pass actually help?" used to take two
 *             screenshot sessions; now it is a click mid-audition.
 *
 *   Knobs   — live intensity/tail multipliers per band, re-expanding the
 *             cues instantly. When a setting earns permanence, "Copy as TS"
 *             emits the effective BAND_STYLE literal to paste back into
 *             track_lights.ts — the knobs themselves are session state, and
 *             a knob nobody commits is a knob that resets tomorrow.
 *
 * Exports stay honest: sceneYaml applies the tweaks but never the B variant
 * (see styleFor). The one thing this panel must never do is make the audition
 * show a show the device won't run — B mode says so on the button.
 */

import { BANDS, type BandName } from "./bands.js";
import {
  resetFlavors, resetStyleTweaks, setFlavor, setStyleTweak, setStyleVariant,
  styleAsTs, styleVariant, type Flavors,
} from "./track_lights.js";

export interface StyleLab {
  readonly el: HTMLElement;
  /** Back to variant A with neutral knobs — for a fresh track. */
  reset: () => void;
}

/** @param onStyle re-expand the audition after any change here. */
export function createStyleLab(onStyle: () => void): StyleLab {
  // Styling is inline: previewer/styles.css sits exactly at the 500-line cap,
  // and this panel owns its whole DOM the way waveform.ts's chrome does.
  const el = document.createElement("details");
  el.className = "stylelab";
  el.style.cssText = "margin:6px 0;font:12px/1.7 var(--f-data),ui-monospace,"
    + "monospace;color:var(--ink-2)";
  // Open by default: this panel was invisible in practice — a collapsed fold
  // labelled "Style lab" told nobody that the A/B lives here. Discoverable
  // first, foldable after.
  el.open = true;
  const sum = document.createElement("summary");
  sum.style.cursor = "pointer";
  sum.textContent = "Judge the lights — A/B engine test, flavours, knobs";
  sum.title = "Compare the light engine against the old flat look, and trim "
            + "intensity/tails live. Applies to the audition and to the next "
            + "Make/Update scene — the baked scenes on the left don't change "
            + "until you re-export them.";
  el.append(sum);

  const row = (label: string): HTMLDivElement => {
    const r = document.createElement("div");
    r.className = "stylelab__row";
    r.style.cssText = "display:flex;align-items:center;gap:8px;margin:3px 0";
    const s = document.createElement("span");
    s.className = "stylelab__nm";
    s.style.cssText = "min-width:3.5em";
    s.textContent = label;
    r.append(s);
    return r;
  };

  /* ── A/B ── */
  const abRow = row("engine");
  const ab = document.createElement("button");
  ab.type = "button";
  ab.className = "stylelab__ab";
  const sayAb = (): void => {
    const b = styleVariant() === "classic";
    ab.textContent = b ? "B · classic flat (comparison only)" : "A · current dynamics";
    ab.classList.toggle("on", b);
  };
  ab.title = "Toggle the whole strike engine between the current dynamics "
           + "and the pre-4K baseline. Exports always use A.";
  ab.addEventListener("click", () => {
    setStyleVariant(styleVariant() === "classic" ? "current" : "classic");
    sayAb();
    onStyle();
  });
  sayAb();
  abRow.append(ab);
  el.append(abRow);

  /* ── Flavours: taste features, default off, judged via the A/B above.
     Turning one on colours the audition AND the next export. ── */
  const flavRow = row("flavour");
  const flavBoxes: HTMLInputElement[] = [];
  const FLAVORS: Array<[keyof Flavors, string, string]> = [
    ["drift", "drift", "Palette drift: each band's hues walk around its own "
      + "triad, one lap per minute — a long song never repeats a look"],
    ["takeover", "chorus", "Chorus takeover: during choruses every band "
      + "agrees on one warm gold/flame family, splintering back at the verse"],
    ["swells", "swells", "Sustained swells: loud plateaus bloom the towers "
      + "in slowly (900 ms attack) on top of the strikes"],
  ];
  for (const [key, label, title] of FLAVORS) {
    const wrap = document.createElement("label");
    wrap.style.cssText = "display:inline-flex;align-items:center;gap:3px;cursor:pointer";
    wrap.title = title;
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = `stylelab__flav-${key}`;
    box.addEventListener("change", () => { setFlavor(key, box.checked); onStyle(); });
    flavBoxes.push(box);
    wrap.append(box, document.createTextNode(label));
    flavRow.append(wrap);
  }
  el.append(flavRow);

  /* ── Knobs ── */
  const sliders: HTMLInputElement[] = [];
  const knob = (r: HTMLElement, band: BandName, key: "intensity" | "decay",
                min: number, max: number, title: string): void => {
    const s = document.createElement("input");
    s.type = "range";
    s.className = "stylelab__knob";
    s.style.cssText = "width:90px";
    s.min = String(min);
    s.max = String(max);
    s.step = "0.05";
    s.value = "1";
    s.title = title;
    const val = document.createElement("span");
    val.className = "stylelab__val";
    val.textContent = "×1.00";
    s.addEventListener("input", () => {
      setStyleTweak(band, { [key]: +s.value });
      val.textContent = `×${(+s.value).toFixed(2)}`;
      onStyle();
    });
    sliders.push(s);
    r.append(s, val);
  };

  for (const b of BANDS) {
    const r = row(b.label);
    knob(r, b.name, "intensity", 0.3, 1.3,
         `${b.label} strike intensity, on top of the committed style`);
    knob(r, b.name, "decay", 0.5, 2.0,
         `${b.label} tail length — ×2 rings out twice as long`);
    el.append(r);
  }

  /* ── Reset + copy ── */
  const foot = row("");
  const reset = document.createElement("button");
  reset.type = "button";
  reset.textContent = "Reset";
  reset.title = "Neutral knobs, engine A";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "Copy as TS";
  copy.title = "The effective BAND_STYLE literal, for pasting into "
             + "web/src/track_lights.ts when a knob setting should become law";
  const out = document.createElement("pre");
  out.className = "stylelab__ts";
  out.style.cssText = "max-height:14em;overflow:auto;font-size:10px;"
    + "background:var(--panel-2);padding:6px;border-radius:6px";
  out.hidden = true;

  const doReset = (): void => {
    resetStyleTweaks();
    resetFlavors();
    for (const b of flavBoxes) b.checked = false;
    setStyleVariant("current");
    for (const s of sliders) {
      s.value = "1";
      (s.nextElementSibling as HTMLElement).textContent = "×1.00";
    }
    sayAb();
    out.hidden = true;
    onStyle();
  };
  reset.addEventListener("click", doReset);
  copy.addEventListener("click", () => {
    const ts = styleAsTs();
    out.textContent = ts;
    out.hidden = false;                      // visible even if the copy fails
    void navigator.clipboard?.writeText(ts).catch(() => { /* shown below */ });
  });
  foot.append(reset, copy);
  el.append(foot, out);

  return { el, reset: doReset };
}
