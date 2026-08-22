/**
 * The chrome around the stage — everything that is text, table or widget.
 *
 * This module owns the DOM and nothing else. It is handed scenes, frames and
 * timestamps and writes them into the page; it never reads show state and
 * never touches the audio graph, so the panels can be driven from a live
 * clock, a scrub, or a fixture in a test. Anything that reaches back out —
 * seeking, picking a scene, moving a parameter — leaves through a callback.
 *
 * Element ids are the contract with the generated HTML. They are spelled out
 * here rather than passed in, because the page that carries them is generated
 * from one template and a missing id is a build bug, not a runtime condition.
 */

import { req } from "./dom.js";
import { fixture, zonePixels, zoneRgbw, ZONE_PIN, type RigState } from "./rig.js";
import { sceneTint } from "./scene_tint.js";
import type { ZoneRender } from "./show.js";
import type { Cue, Scene, ZoneId } from "./types.js";

/** A sound's home on the SD card, and the file the renderer produced.
 *
 *  This is presentation metadata: the cue sheet shows the label and the YAML
 *  export writes the path, and neither is anything the synth needs to know.
 *  It lives here so there is one copy rather than one per consumer. */
export interface SoundFile {
  /** SD card folder/track, as the DFPlayer addresses it. */
  sd: string;
  label: string;
}

export const SOUNDS: Record<string, SoundFile | undefined> = {
  wind:      { sd: "01/001", label: "wind_bed.mp3" },
  organ:     { sd: "01/002", label: "organ_procession.mp3" },
  waltz:     { sd: "01/003", label: "parlour_waltz.mp3" },
  descent:   { sd: "01/004", label: "descent_dm.mp3" },
  thunder:   { sd: "02/001", label: "thunder_close.mp3" },
  creak:     { sd: "03/001", label: "door_creak.mp3" },
  shriek:    { sd: "03/002", label: "shriek.mp3" },
  toll:      { sd: "03/003", label: "bell_toll.mp3" },
  musicbox:  { sd: "03/004", label: "music_box.mp3" },
  heartbeat: { sd: "04/001", label: "heartbeat.mp3" },
  drone:     { sd: "04/002", label: "tritone_drone.mp3" },
  whispers:  { sd: "04/003", label: "whispers.mp3" },
};

/** How the channel strip names each zone. `ch` is the logical output; what
 *  fixture is on it, which GPIO carries it and how many pixels it has all
 *  come from the rig, because all three change when you swap a fixture —
 *  see renderChannels below and rig.ts. */
export interface Channel {
  id: ZoneId;
  ch: number;
  name: string;
}

export const CHANNELS: readonly Channel[] = [
  { id: "towerL", ch: 1, name: "Tower L" },
  { id: "towerR", ch: 2, name: "Tower R" },
  { id: "door",   ch: 3, name: "Doorway" },
];

/** The strip reads left-to-right the way the castle stands, which is not the
 *  order the zones are declared in. Same three channels, laid out as a person
 *  looking at the porch would find them. */
const STRIP: readonly ZoneId[] = ["towerL", "door", "towerR"];

/** Where a parameter goes once the slider has been read. Values arrive
 *  already converted, so a caller only has to store them. */
export interface SliderHandlers {
  /** 0..1 */ depth: (v: number) => void;
  /** 0..1 */ speed: (v: number) => void;
  /** 0..1, violet .. green */ hue: (v: number) => void;
  /** 0..1 */ bright: (v: number) => void;
  /** 0..1 master level */ vol: (v: number) => void;
  /** Milliseconds of modelled decode latency. */ lat: (ms: number) => void;
  /** 0..1 organ stop depth */ stops: (v: number) => void;
  /** 0..1 reverb wet gain */ hall: (v: number) => void;
  /** Tremolo gain, already scaled to its 0.14 ceiling. */ trem: (v: number) => void;
  soft: (on: boolean) => void;
  /**
   * Rendered-audio mode. Omit the handler entirely when the page carries no
   * rendered audio: the checkbox is then unchecked and disabled, so nobody
   * can select a mode that has no files behind it.
   */
  renderedAudio?: (useRendered: boolean) => void;
}

/** Bytes as the panel prints them: KB under a megabyte, MB above. */
const kb = (n: number): string =>
  n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(2)} MB` : `${Math.round(n / 1024)} KB`;

const must = <T extends HTMLElement = HTMLElement>(id: string): T => req<T>(id, "panels");

/** One row of the cue sheet. AUD rows name the file that will play; LED rows
 *  read as "zone → effect", which is the shorthand the scene file uses. */
function sheetRow(c: Cue, scene: Scene): string {
  let op: string;
  let detail: string;
  let file: string;

  if (c.bus === "AUD") {
    const snd = SOUNDS[c.snd];
    op = c.op;
    detail = snd?.label ?? c.snd;
    // In rendered mode the whole scene is one file, so that name wins over
    // the individual sample's — it is what the speaker is actually playing.
    file = scene.file || (snd?.sd ?? c.snd);
  } else if (c.op === "strike") {
    op = c.zone ? `strike · ${c.zone}` : "strike · all";
    detail = (c.detail ?? "") + (c.ms ? ` · ${c.ms} ms` : "");
    file = "—";
  } else {
    op = `${c.zone} → ${c.eff}`;
    detail = c.detail ?? "";
    file = "—";
  }

  return `<tr data-t="${c.t}" title="Jump to ${(c.t / 1000).toFixed(2)} s">
    <td class="t">${(c.t / 1000).toFixed(2)}</td>
    <td><span class="tag ${c.bus === "AUD" ? "tag--aud" : "tag--led"}">${c.bus}</span></td>
    <td>${op}</td><td>${detail}</td>
    <td>${file}</td>
  </tr>`;
}

/** One tick under the scrub bar. Height carries intensity, so a heartbeat
 *  pulse reads as a stub and full lightning as a full-height mark. */
function tickMark(c: Cue, dur: number): string {
  if (c.bus !== "LED") return "";
  const pct = (c.t / dur) * 100;
  const col = c.op === "strike" ? (c.color ?? [1, 1, 1, 1]) : null;
  const bg = col
    ? `rgb(${Math.round(col[0] * 235)},${Math.round(col[1] * 235)},${Math.round(col[2] * 235)})`
    : "var(--line-2)";
  const h = c.op === "strike" ? Math.max(3, Math.round(7 * (c.intensity ?? 1))) : 7;
  const title = `${(c.t / 1000).toFixed(2)}s · ${c.op}${c.detail ? " · " + c.detail : ""}`;
  return `<b style="left:${pct}%;background:${bg};height:${h}px;top:${7 - h}px" title="${title}"></b>`;
}

/**
 * The fallback scene serialiser, for scenes that arrived without their
 * verbatim YAML slice. It is deliberately lossy — enough to paste back into
 * scenes.yaml and recognise, not a round-trip of every optional field.
 */
export function toYaml(sc: Scene): string {
  const lines = [`scene: ${sc.id}`, `duration_ms: ${sc.dur}`];
  if (sc.loop) lines.push("loop: true");
  lines.push("base:");
  for (const z of CHANNELS) lines.push(`  ${z.id}: ${sc.base[z.id]}`);
  lines.push("cues:");
  for (const c of sc.cues) {
    if (c.bus === "AUD") {
      const snd = SOUNDS[c.snd];
      lines.push(`  - { t: ${c.t}, bus: AUD, op: ${c.op}, file: "${snd?.sd ?? c.snd}" }`
        + `   # ${snd?.label ?? c.snd}`);
    } else if (c.op === "strike") {
      lines.push(`  - { t: ${c.t}, bus: LED, op: strike, zone: ${c.zone ?? "all"}, ms: ${c.ms} }`);
    } else {
      lines.push(`  - { t: ${c.t}, bus: LED, op: set, zone: ${c.zone}, effect: ${c.eff} }`);
    }
  }
  return lines.join("\n");
}

interface Meter {
  sw: HTMLElement;
  val: HTMLElement;
  /** The line under the zone name: fixture, pin and pixel count. Rewritten
   *  whenever the rig changes, which the swatch above it never needs to be. */
  sub: HTMLElement;
}

export class Panels {
  private readonly scenesEl = must("scenes");
  private readonly sceneCount = must("sceneCount");
  private readonly sceneBlurb = must("sceneBlurb");
  private readonly sheet = must("sheet");
  private readonly sheetName = must("sheetName");
  private readonly sheetCount = must("sheetCount");
  private readonly sheetWrap = must("sheetWrap");
  private readonly sheetFold = must<HTMLDetailsElement>("sheetFold");
  /** Index of the row last scrolled to, so we only scroll when it changes. */
  private scrolledTo = -1;
  private readonly yaml = must("yaml");
  private readonly stageNote = must("stageNote");
  private readonly ph = must("ph");
  private readonly tc = must("tc");
  private readonly ticks = must("ticks");
  private readonly scrub = must("scrub");
  private readonly meters: Record<ZoneId, Meter>;

  /** The rows currently in the sheet, cached because the playhead highlight
   *  touches every one of them on every frame. Refreshed by `renderSheet`. */
  private rows: HTMLTableRowElement[] = [];

  private sceneList: readonly Scene[] = [];
  private onPick: ((scene: Scene, index: number) => void) | null = null;

  constructor(private readonly onSeek: (ms: number) => void) {
    // The channel strip is fixed for the life of the page — zones do not come
    // and go — so it is built once and only its numbers change after that.
    // The swatch IS the meter: it carries hue and level at once, which a bar
    // beside a hex readout could only say twice. Built once — zones do not
    // come and go — and only its numbers change after that.
    must("chans").innerHTML = STRIP.map(id => {
      const z = CHANNELS.find(c => c.id === id);
      if (!z) throw new Error(`panels: no channel ${id}`);
      return `
    <div class="chan">
      <div class="chan__sw" id="sw-${z.id}"></div>
      <div class="chan__nm">${z.name}<small id="sub-${z.id}"></small></div>
      <span class="chan__val" id="vl-${z.id}">—</span>
    </div>`;
    }).join("");

    const meters = {} as Record<ZoneId, Meter>;
    for (const z of CHANNELS) {
      meters[z.id] = {
        sw: must(`sw-${z.id}`), val: must(`vl-${z.id}`), sub: must(`sub-${z.id}`),
      };
    }
    this.meters = meters;

    // Clicking a cue row jumps the show to that cue — the quickest way to
    // inspect one moment repeatedly while tuning it.
    this.sheet.addEventListener("click", e => {
      const target = e.target;
      if (!(target instanceof Element)) return;
      const tr = target.closest("tr");
      const t = tr?.dataset.t;
      if (t === undefined || t === "") return;
      this.onSeek(+t);
    });

    // Delegated once, then re-read on every click, so re-rendering the grid
    // never leaves a stale listener behind.
    this.scenesEl.addEventListener("click", e => {
      const target = e.target;
      if (!(target instanceof Element)) return;
      const b = target.closest(".scene");
      if (!b) return;
      this.scenesEl.querySelectorAll(".scene")
        .forEach(x => x.setAttribute("aria-pressed", String(x === b)));
      const i = Number((b as HTMLElement).dataset.i);
      this.markScene(i);
      const sc = this.sceneList[i];
      if (sc && this.onPick) this.onPick(sc, i);
    });
  }

  /** Build the scene grid. `onPick` fires with the scene that was chosen —
   *  what the transport does about it (keep playing, stay quiet) is its call. */
  renderScenes(scenes: readonly Scene[], onPick: (scene: Scene, index: number) => void): void {
    this.sceneList = scenes;
    this.onPick = onPick;
    // The bar across the top is the scene's own light — see scene_tint.ts.
    // Eight identically grey tiles made you read the word to find Séance;
    // a swatch answers "which is the green one" before you have read anything.
    this.scenesEl.innerHTML = scenes.map((s, i) => {
      // Every conditional piece named before the markup rather than nested
      // inside it. The template is then one flat line per element, which is
      // the only way this stays readable at five interpolations a tile.
      const secs = `${(s.dur / 1000).toFixed(0)}s`;
      const loops = s.loop ? " ↻" : "";
      const size = s.bytes ? ` · ${kb(s.bytes)}` : "";
      const file = s.bytes ? `\n\n${s.file} — ${kb(s.bytes)}` : "";
      const title = `${s.kind} · ${secs}${s.loop ? " · loops" : ""} — ${s.blurb}${file}`;
      const tint = sceneTint(s, i === 0 ? 1 : 0.45);
      return `
    <button class="scene" type="button" data-i="${i}" aria-pressed="${i === 0}"
            title="${title}">
      <i class="scene__tint" style="background:${tint}"></i>
      <strong>${s.name}</strong>
      <span class="scene__sz">${secs}${loops}${size}</span>
    </button>`;
    }).join("");

    // The count only. What the show costs, and against which of the two
    // builds, is the Budgets panel's whole job now (budget.ts) — it was never
    // going to fit in a parenthesised percentage.
    this.sceneCount.textContent = `${scenes.length} loaded`;
  }

  /** Pick scene `index` exactly as a click on its tile would — the same
   *  delegated handler runs, so aria-pressed, the tint and `onPick` all
   *  follow. Out of range is a no-op. The two callers that used to reach
   *  into the grid by selector (keyboard 1–9, adopting the castle's scene)
   *  now ask here, and the grid's markup stays this file's business. */
  selectScene(index: number): void {
    this.scenesEl.querySelector<HTMLElement>(`.scene[data-i="${index}"]`)?.click();
  }

  /** Re-tint the grid so the selected scene's swatch is the bright one. */
  markScene(index: number): void {
    this.scenesEl.querySelectorAll<HTMLElement>(".scene").forEach((b, i) => {
      const sc = this.sceneList[i];
      const tint = b.querySelector<HTMLElement>(".scene__tint");
      if (sc && tint) tint.style.background = sceneTint(sc, i === index ? 1 : 0.45);
    });
  }

  /** The cue sheet for one scene, in fire order. */
  renderSheet(scene: Scene): void {
    this.sheet.innerHTML = scene.cues.map(c => sheetRow(c, scene)).join("");
    this.rows = Array.from(this.sheet.querySelectorAll("tr"));
    this.sheetName.textContent = scene.id;
    this.scrolledTo = -1;
    // The summary has to answer "is it worth opening this" on its own — a
    // count of 2238 is itself the reason the panel is collapsed.
    const n = scene.cues.length;
    const led = scene.cues.filter(c => c.bus === "LED").length;
    this.sheetCount.textContent = n === 0
      ? "— none"
      : `— ${n} cue${n === 1 ? "" : "s"}, ${led} light`;
  }

  /** The headings and the source panel, which all change together whenever
   *  the scene does. A scene without a verbatim YAML slice gets a fallback
   *  rather than an empty panel. */
  renderSceneInfo(scene: Scene): void {
    this.stageNote.textContent = scene.bytes
      ? `${scene.name} · ${kb(scene.bytes)}`
      : scene.name;
    this.sceneBlurb.textContent = scene.blurb || "";
    this.yaml.textContent = scene.yaml || toYaml(scene);
  }

  /* Cue ticks under the scrub bar, in the colour of the strike they mark —
     the fastest way to see whether the light is following the audio. */
  renderTicks(scene: Scene): void {
    this.ticks.innerHTML = scene.cues.map(c => tickMark(c, scene.dur)).join("");
  }

  /**
   * Re-label the strip for the rig that is now in the windows.
   *
   * Only the sub-line is rewritten. The swatches are the meters themselves
   * and are looked up once in the constructor — rebuilding the strip here
   * would leave `this.meters` pointing at detached nodes, and the whole strip
   * would freeze at whatever colour it last had.
   */
  renderChannels(rig: RigState): void {
    for (const z of CHANNELS) {
      const fx = fixture(rig.zones[z.id].fixture);
      const n = zonePixels(rig, z.id);
      const kind = n === 0 ? "not wired"
        : `${n}px ${zoneRgbw(rig, z.id) ? "RGBW" : "RGB"}`;
      this.meters[z.id].sub.textContent =
        `ch ${z.ch} · GPIO${ZONE_PIN[z.id]} · ${fx.name} · ${kind}`;
    }
  }

  /** The channel meters. Level is the strongest channel rather than a
   *  luminance mix, because that is what actually clips the LED driver — and
   *  it is what the swatch's own glow is scaled by, so the strip reads at a
   *  glance and still gives you the exact bytes to type into a scene file. */
  updateMeters(out: ZoneRender): void {
    for (const z of CHANNELS) {
      const m = this.meters[z.id];
      const c = out[z.id].avg;
      const r = Math.round(c[0] * 255);
      const g = Math.round(c[1] * 255);
      const b = Math.round(c[2] * 255);
      const lum = Math.max(r, g, b);
      const css = `rgb(${r},${g},${b})`;
      m.sw.style.background = css;
      m.sw.style.boxShadow = `0 0 ${(lum / 255 * 20).toFixed(1)}px ${css}`;
      m.val.textContent = String(lum).padStart(3, " ") + "  "
        + [r, g, b].map(v => v.toString(16).padStart(2, "0")).join("");
    }
  }

  /** Playhead, clock and the lit row in the sheet. The 700 ms window is a
   *  readability choice: a strike is over long before you can look at it. */
  updateTransport(elapsed: number, scene: Scene): void {
    const pct = Math.min(100, elapsed / scene.dur * 100);
    this.ph.style.width = pct.toFixed(1) + "%";
    this.tc.textContent = (Math.min(elapsed, scene.dur) / 1000).toFixed(2) + " / "
      + (scene.dur / 1000).toFixed(2) + " s" + (scene.loop ? " ↻" : "");
    this.scrub.setAttribute("aria-valuenow", String(Math.round(pct)));
    this.scrub.setAttribute("aria-valuetext", (elapsed / 1000).toFixed(1) + " seconds");
    let live = -1;
    scene.cues.forEach((c, i) => {
      const row = this.rows[i];
      if (!row) return;
      const on = elapsed >= c.t && elapsed < c.t + 700;
      row.classList.toggle("on", on);
      if (on) live = i;
    });
    this.followCue(live);
  }

  /**
   * Keep the lit row inside the capped scroll box.
   *
   * Only when it changes, and only when the panel is open — scrolling a
   * closed `<details>` does nothing but cost work every frame, and calling
   * scrollTo on every frame fights the user's own scrollbar.
   */
  private followCue(index: number): void {
    if (index < 0 || index === this.scrolledTo || !this.sheetFold.open) return;
    this.scrolledTo = index;
    const row = this.rows[index];
    if (!row) return;
    const box = this.sheetWrap;
    const top = row.offsetTop - box.clientHeight / 2 + row.offsetHeight / 2;
    box.scrollTop = Math.max(0, top);
  }

  /** Wire one slider to its handler and its readout. */
  private bind(id: string, fn: (v: number) => void, fmt: (v: number) => string): void {
    const el = must<HTMLInputElement>(id);
    const out = must(id + "O");
    // Trim settings survive a reload (round-1 user test: a tuned Master
    // brightness silently snapped back to the markup default). localStorage,
    // per slider, restored before the first run so nothing flashes.
    const key = `castle.trim.${id}`;
    try {
      const saved = localStorage.getItem(key);
      if (saved !== null && Number.isFinite(+saved)) el.value = saved;
    } catch { /* private mode: session-only */ }
    const run = (): void => {
      const v = +el.value;
      fn(v);
      out.textContent = fmt(v);
    };
    el.addEventListener("input", () => {
      try { localStorage.setItem(key, el.value); } catch { /* fine */ }
      run();
    });
    run();   // markup (or the saved value) carries the start; adopt it
  }

  bindSliders(h: SliderHandlers): void {
    this.bind("depth",  v => h.depth(v / 100),  v => v + " %");
    this.bind("speed",  v => h.speed(v / 100),  v => (v / 100).toFixed(2) + "×");
    this.bind("hue",    v => h.hue(v / 100),    v => v < 34 ? "violet" : v > 66 ? "green" : "balanced");
    this.bind("bright", v => h.bright(v / 100), v => v + " %");
    this.bind("vol",    v => h.vol(v / 100),    v => v + " %");
    this.bind("lat",    v => h.lat(v),          v => v + " ms");
    this.bind("stops",  v => h.stops(v / 100),
      v => v < 12 ? "16′ only" : v < 40 ? "dark" : v < 72 ? "chorus" : "full organ");
    this.bind("hall",   v => h.hall(v / 100),
      v => v < 12 ? "dry" : v < 45 ? "chapel" : v < 78 ? "nave" : "cathedral");
    // 0.14 is the tremolo's full-scale depth; past it the wobble stops being
    // a tremulant and starts being a fault.
    this.bind("trem",   v => h.trem((v / 100) * 0.14), v => v < 5 ? "off" : v + " %");

    const softEl = must<HTMLInputElement>("soft");
    // A stated motion preference is honoured before the first frame, not
    // after someone goes looking for the checkbox.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) softEl.checked = true;
    const applySoft = (): void => h.soft(softEl.checked);
    softEl.addEventListener("change", applySoft);
    applySoft();

    const raEl = must<HTMLInputElement>("renderedAudio");
    const onRendered = h.renderedAudio;
    if (!onRendered) {
      raEl.checked = false;
      raEl.disabled = true;
      return;
    }
    raEl.addEventListener("change", () => onRendered(raEl.checked));
    onRendered(raEl.checked);   // the mode is whatever the checkbox already says
  }
}
