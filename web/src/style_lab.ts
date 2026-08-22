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

/**
 * Knobs and flavours survive a reload (round-1 user test: someone tuned the
 * show for ten minutes, reloaded, and lost everything with no warning).
 * localStorage, this browser only — the A/B variant is deliberately NOT
 * saved: B is a comparison stance, not a setting.
 */
const LS_KEY = "castle.styleLab";

interface SavedLab {
  tweaks?: Partial<Record<BandName, { intensity: number; decay: number }>>;
  flavors?: Partial<Flavors>;
}

function loadSaved(): SavedLab {
  try { return JSON.parse(localStorage.getItem(LS_KEY) ?? "{}") as SavedLab; }
  catch { return {}; }
}

/** @param onStyle re-expand the audition after any change here. */
export function createStyleLab(onStyle: () => void): StyleLab {
  // Styled by class in previewer/panels.css (.stylelab*) — this panel owns
  // its whole DOM the way waveform.ts's chrome does, but not its colours.
  const el = document.createElement("details");
  el.className = "stylelab";
  // Open by default: this panel was invisible in practice — a collapsed fold
  // labelled "Style lab" told nobody that the A/B lives here. Discoverable
  // first, foldable after.
  el.open = true;
  const sum = document.createElement("summary");
  // Operator words on the outside (JB1-9): what you can do to the look.
  // The engine A/B and the code export are developer tools and live in
  // the fold at the bottom, named as such.
  sum.textContent = "Light style — flavours, brightness and tails";
  sum.title = "Shape how the lights follow this track: optional flavours, "
            + "and how hard and how long each band strikes. Applies to the "
            + "audition and to the next Make/Update scene — the baked scenes "
            + "on the left don't change until you re-export them.";
  el.append(sum);

  const row = (label: string): HTMLDivElement => {
    const r = document.createElement("div");
    r.className = "stylelab__row";
    const s = document.createElement("span");
    s.className = "stylelab__nm";
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
  ab.title = "Toggle the whole light engine between A (current) and B (the "
           + "old flat look, kept as a reference point). B is for judging "
           + "whether A is actually better — it can't be shipped, so exports "
           + "always use A. If B ever looks better, that's a bug to report, "
           + "not a setting to keep.";
  ab.addEventListener("click", () => {
    setStyleVariant(styleVariant() === "classic" ? "current" : "classic");
    sayAb();
    onStyle();
  });
  sayAb();
  abRow.append(ab);

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
    wrap.className = "stylelab__flav";
    wrap.title = title;
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = `stylelab__flav-${key}`;
    box.addEventListener("change", () => {
      setFlavor(key, box.checked);
      saveState();
      onStyle();
    });
    flavBoxes.push(box);
    wrap.append(box, document.createTextNode(label));
    flavRow.append(wrap);
  }
  el.append(flavRow);
  // Expectations, or the boxes read as dead (round 2: a tester turned all
  // three on, auditioned ten seconds of ambience, and measured no change —
  // correctly, because nothing they gate had happened yet). Its own
  // paragraph under the row: inside the flex row it collapsed to a
  // one-word-per-line column on a phone (JB1-7).
  const flavHint = document.createElement("div");
  flavHint.className = "stylelab__flavhint";
  flavHint.textContent = "drift shows over ~a minute · chorus fires only in "
    + "loud chorus sections · swells need long sustained louds — audition a "
    + "loud stretch of the song to see them";
  el.append(flavHint);

  /* ── Knobs ── */
  const sliders: HTMLInputElement[] = [];
  const knobRefs: Array<{ band: BandName; key: "intensity" | "decay";
                          input: HTMLInputElement }> = [];

  const saveState = (): void => {
    const tweaks: SavedLab["tweaks"] = {};
    for (const k of knobRefs) {
      const v = +k.input.value;
      if (v !== 1) (tweaks[k.band] ??= { intensity: 1, decay: 1 })[k.key] = v;
    }
    const flavors: Flavors = {
      drift: !!flavBoxes[0]?.checked,
      takeover: !!flavBoxes[1]?.checked,
      swells: !!flavBoxes[2]?.checked,
    };
    try {
      localStorage.setItem(LS_KEY,
        JSON.stringify({ tweaks, flavors } satisfies SavedLab));
    } catch { /* private mode: settings just stay session-only */ }
  };

  const knob = (r: HTMLElement, band: BandName, key: "intensity" | "decay",
                min: number, max: number, title: string): void => {
    const s = document.createElement("input");
    s.type = "range";
    s.className = "stylelab__knob";
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
      saveState();
      onStyle();
    });
    sliders.push(s);
    knobRefs.push({ band, key, input: s });
    r.append(s, val);
  };

  // Column headers: two anonymous "×1.00" sliders per band read as noise
  // without them (round 1).
  const head = row("");
  const th = (label: string, title: string): HTMLSpanElement => {
    const s = document.createElement("span");
    s.className = "stylelab__th";
    s.textContent = label;
    s.title = title;
    return s;
  };
  head.append(th("brightness", "How hard this band's hits strike"),
              th("tail", "How long each hit rings out"));
  el.append(head);

  for (const b of BANDS) {
    const r = row(b.label);
    knob(r, b.name, "intensity", 0.3, 1.3,
         `${b.label} strike intensity, on top of the committed style`);
    knob(r, b.name, "decay", 0.5, 2.0,
         `${b.label} tail length — ×2 rings out twice as long`);
    el.append(r);
  }

  /* ── Reset, then the developer fold: engine A/B and the code export ── */
  const foot = row("");
  const reset = document.createElement("button");
  reset.type = "button";
  reset.textContent = "Reset";
  reset.title = "Neutral brightness and tails, no flavours, engine A";
  const dev = document.createElement("details");
  dev.className = "stylelab__dev";
  const devSum = document.createElement("summary");
  devSum.textContent = "developer — compare light engines, copy the style as code";
  devSum.title = "Tools for working on the light engine itself, not on a show";
  dev.append(devSum);
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "Copy as TS";
  copy.title = "Your knobs and flavours are already remembered in this "
             + "browser and ship with every scene you export. This copies "
             + "them as code, for making them the project-wide default.";
  const out = document.createElement("pre");
  out.className = "stylelab__ts";
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
    try { localStorage.removeItem(LS_KEY); } catch { /* fine */ }
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
  foot.append(reset);
  const devFoot = row("");
  devFoot.append(copy);
  dev.append(abRow, devFoot, out);
  el.append(foot, dev);

  // Restore last session's knobs and flavours — the settings someone spent
  // ten minutes finding must not evaporate on reload.
  const saved = loadSaved();
  for (const k of knobRefs) {
    const v = saved.tweaks?.[k.band]?.[k.key];
    if (v === undefined || v === 1 || !Number.isFinite(v)) continue;
    k.input.value = String(v);
    (k.input.nextElementSibling as HTMLElement).textContent = `×${v.toFixed(2)}`;
    setStyleTweak(k.band, { [k.key]: v });
  }
  FLAVORS.forEach(([key], i) => {
    if (saved.flavors?.[key]) {
      flavBoxes[i]!.checked = true;
      setFlavor(key, true);
    }
  });

  return { el, reset: doReset };
}
