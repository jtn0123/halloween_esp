/**
 * The castle's test bench: the strip test and the speaker test, the two rows
 * of the device panel that exist to answer "is the hardware alright?" rather
 * than to run the show.
 *
 * Split out of device_panel.ts (500-line cap) on the seam the panel already
 * had: everything here drives ONE subsystem at a time with the scene halted,
 * and none of it touches the card, the motion sensor or the playlist. The
 * panel renders `lightsMarkup()` + `speakerMarkup()` into its own body and
 * calls `wireTests()` once afterwards.
 *
 * Both benches speak the vocabulary the firmware already understands, so
 * nothing here needs a reflash:
 *   lights   POST /api/light?c=[zone:]RRGGBB|white|bars|chase|ends|show|off[@pct]
 *   speaker  POST /api/volume?v=<pct> then POST /api/play?f=<tone>.mp3
 * See tools/gen_rig.py (lights_override) and docs/WIRING.md §5.
 */

import { castleAct } from "./castle_act.js";
import { ZONE_ORDER } from "./rig.js";

/** The strips in porch order (left, door, right) for the per-line test. */
const STRIPS = ZONE_ORDER;

/** Brightness for the strip test and the colour picker; survives a
 *  re-render, not a reload. 100 % on a tower is a lot of LED in a dark room. */
export let testPct = 100;
const PCTS = [25, 50, 75, 100] as const;

/** The colour buttons on every strip row: the spec to send, the label, and
 *  the swatch to paint it (so R/G/B read as colours, not initials). */
const CHANNELS: readonly (readonly [string, string, string])[] = [
  ["ff0000", "R", "#ff2b2b"], ["00ff00", "G", "#2bff5a"], ["0000ff", "B", "#4d6bff"],
  ["white", "W", "#fff6e0"], ["off", "off", ""],
];

/** What one strip-test button promises, for its tooltip. */
function stripTitle(zone: string, label: string): string {
  if (label === "off") return `${zone}: off`;
  if (label === "W") return `${zone}: white channel — the one a colour never lights`;
  return `${zone}: solid ${label}`;
}

/** The bench patterns, on every strip at once — each answers a different
 *  question than a solid colour does. Names match gen_rig's TEST_EFFECTS. */
const PATTERNS: readonly (readonly [string, string])[] = [
  ["bars", "R G B repeating: colour order, pixel count, dead pixels"],
  ["chase", "One dot walking: where it stops is where the data stops"],
  ["ends", "First pixel red, last blue: which end the data goes in"],
];

/** The sequences: a walk the firmware has no effect for, driven from here
 *  as plain /api/light calls a beat apart. Each one terminates. */
const SEQUENCES: readonly (readonly [string, string, string])[] = [
  ["cycle", "R → G → B → W on every strip, a beat apart: watch one strip " +
    "through all four channels without four clicks",
    "cycling the channels"],
  ["ramp", "White from 10 % to 100 % in five steps: banding, and a 5 V rail " +
    "that sags as the pixels draw more",
    "ramping the brightness"],
];

/** The speaker test's tones — files `make audio` renders into audio/test/
 *  and `sd_sync tones` pushes to the card root: what to play, the button,
 *  and the one question each answers (the porch diagnosis, 2026-08-22). */
const TONES: readonly (readonly [string, string, string])[] = [
  ["test_sweep", "sweep", "200 Hz → 10 kHz in 12 s: static that comes and goes with pitch, a top end that vanishes"],
  ["test_1k", "1 kHz", "A steady reference tone: should be a smooth whistle — anything else is distortion"],
  ["test_200", "200 Hz", "Bass pulls the current: crackle here and not on 4 kHz is the 5 V rail sagging"],
  ["test_4k", "4 kHz", "Nearly no current: crackle here too is data or wiring, not power"],
  ["test_silence", "silence", "Nothing should be heard: hiss or hum here is ground or supply noise"],
];
/** Level for the tones; the firmware clamps at scenes.yaml's ceiling anyway. */
let tonePct = 50;
const TONE_PCTS = [25, 50, 80, 100] as const;

/** One section heading, so every block of the panel is introduced the same
 *  way: an icon, a name, and the sentence that says what the block is for. */
export const sectionHead = (icon: string, name: string, hint: string): string =>
  `<div class="dp__sec-hd"><span class="dp__sec-ic">${icon}</span>` +
  `<b>${name}</b><small class="dp__muted">${hint}</small></div>`;

const pcts = (attr: string, list: readonly number[], now: number): string =>
  `<span class="dp__pills">` + list.map((p) =>
    `<button class="dp__ghost dp__btn--sm" data-${attr}="${p}" ` +
    `aria-pressed="${p === now}">${p}%</button>`).join("") + `</span>`;

/** The strip test: one row per data line, the same five buttons on each so
 *  the eye can run down a column, then the all-strips row and the patterns. */
export function lightsMarkup(): string {
  const row = (zone: string, label: string, why: string): string =>
    `<div class="dp__zone" title="${why}"><small>${label}</small>` +
    CHANNELS.map(([spec, txt, swatch]) =>
      `<button class="dp__ghost dp__btn--sm${swatch ? " dp__sw" : ""}" ` +
      (swatch ? `style="--sw:${swatch}" ` : "") +
      `data-zl="${zone}:${spec}" title="${stripTitle(label, txt)}">${txt}</button>`)
      .join("") + `</div>`;

  return `<div class="dp__sec">` +
    sectionHead("🔌", "strip test", "one data line · scene stops first") +
    `<div class="dp__zones">` +
    STRIPS.map((z) => row(z, z, `Drive ${z} alone: a strip that will not show ` +
      `solid red here is wiring, shifter or power — not a scene`)).join("") +
    row("", "all", "Every strip at once: the quick 'is anything alive' pass") +
    `</div>` +
    `<div class="dp__row dp__row--tight">` +
    `<small class="dp__muted">brightness</small>${pcts("pct", PCTS, testPct)}` +
    `</div>` +
    `<div class="dp__row dp__row--tight"><small class="dp__muted">patterns</small>` +
    `<span class="dp__pills">` +
    PATTERNS.map(([spec, why]) =>
      `<button class="dp__ghost dp__btn--sm" data-zl=":${spec}" title="${why}">` +
      `${spec}</button>`).join("") +
    SEQUENCES.map(([id, why]) =>
      `<button class="dp__ghost dp__btn--sm" data-seq="${id}" title="${why}">` +
      `${id}</button>`).join("") +
    `</span></div></div>`;
}

/** The speaker test: the audio twin of the strip test. A tone the card does
 *  not have is offered disabled with the command that puts it there — an
 *  enabled button that 404s is a worse answer than a greyed one. */
export function speakerMarkup(onCard: Set<string>): string {
  const missing = TONES.some(([f]) => !onCard.has(`${f}.mp3`));
  return `<div class="dp__sec">` +
    sectionHead("🔊", "speaker test", "both amps · scene halted first") +
    `<div class="dp__row dp__row--tight">` +
    `<small class="dp__muted">level</small>${pcts("tpct", TONE_PCTS, tonePct)}` +
    `</div>` +
    `<div class="dp__row dp__row--tight"><span class="dp__pills">` +
    TONES.map(([file, label, why]) =>
      `<button class="dp__ghost dp__btn--sm" data-tone="${file}" title="${why}"` +
      (onCard.has(`${file}.mp3`) ? "" : " disabled") + `>${label}</button>`).join("") +
    `<button id="dpToneStop" class="dp__ghost dp__btn--sm" ` +
    `title="Silence the test">stop</button></span></div>` +
    (missing
      ? `<div class="dp__note dp__note--tight" title="make audio renders ` +
        `audio/test/; sd_sync puts it on the card">tones not on the card: ` +
        `<code>tools/sd_sync.py &lt;ip&gt; tones</code></div>`
      : "") +
    `</div>`;
}

/** Sequences run a beat at a time and must be interruptible: a second click
 *  (or any other test) supersedes the first rather than interleaving with
 *  it. One token, bumped on every start — a step whose token is stale exits. */
let seqToken = 0;
const beat = (ms: number): Promise<void> =>
  new Promise((r) => setTimeout(r, ms));

async function runSequence(id: string): Promise<void> {
  const mine = ++seqToken;
  const steps: readonly (readonly [string, number])[] = id === "cycle"
    ? [["ff0000", 1200], ["00ff00", 1200], ["0000ff", 1200], ["white", 1600]]
    : [10, 30, 55, 80, 100].map((p) => [`white@${p}`, 900] as const);
  for (const [spec, hold] of steps) {
    if (seqToken !== mine) return;
    const arg = spec.includes("@") ? spec : `${spec}@${testPct}`;
    if (!await castleAct(`/api/light?c=${arg}`, `test ${arg}`, { quiet: true })) return;
    await beat(hold);
  }
  if (seqToken === mine)
    void castleAct("/api/light?c=off", id === "cycle"
      ? "channel cycle done — strips off" : "brightness ramp done — strips off");
}

/** Wire both benches. Called once per panel render, after the markup lands. */
export function wireTests(body: HTMLElement): void {
  const press = (attr: string, b: HTMLButtonElement): void =>
    body.querySelectorAll<HTMLButtonElement>(`[data-${attr}]`).forEach((o) =>
      o.setAttribute("aria-pressed", String(o === b)));

  body.querySelectorAll<HTMLButtonElement>("[data-zl]").forEach((b) =>
    b.addEventListener("click", () => {
      seqToken++;                                  // a click supersedes a run
      const spec = b.dataset.zl!.replace(/^:/, "");   // ":bars" = all strips
      const arg = spec.endsWith("off") ? spec : `${spec}@${testPct}`;
      void castleAct(`/api/light?c=${arg}`, `strip ${arg}`);
    }));
  body.querySelectorAll<HTMLButtonElement>("[data-seq]").forEach((b) =>
    b.addEventListener("click", () => void runSequence(b.dataset.seq!)));
  body.querySelectorAll<HTMLButtonElement>("[data-pct]").forEach((b) =>
    b.addEventListener("click", () => { testPct = Number(b.dataset.pct); press("pct", b); }));

  body.querySelectorAll<HTMLButtonElement>("[data-tpct]").forEach((b) =>
    b.addEventListener("click", () => { tonePct = Number(b.dataset.tpct); press("tpct", b); }));
  body.querySelectorAll<HTMLButtonElement>("[data-tone]").forEach((b) =>
    b.addEventListener("click", () => void (async () => {
      // Two commands, spaced: the castle's mailbox is ONE slot applied every
      // 200 ms, and the later of two in a tick overwrites the first
      // (castle_emu.py) — volume then play inside one tick loses the level.
      if (!await castleAct(`/api/volume?v=${tonePct}`, "test level", { quiet: true })) return;
      await beat(300);
      void castleAct(`/api/play?f=${b.dataset.tone}.mp3`,
                     `${b.textContent} tone on the castle at ${tonePct}%`);
    })()));
  body.querySelector<HTMLButtonElement>("#dpToneStop")
    ?.addEventListener("click", () => void castleAct("/api/stop", "speakers silent"));
}
