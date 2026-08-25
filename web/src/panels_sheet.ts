/**
 * The cue sheet's pure half — the sound catalogue, the channel strip
 * vocabulary, and the row/tick/YAML builders that turn a Cue into markup
 * or text. Split from panels.ts at the data/DOM seam (the 500-line cap,
 * grade report C2): nothing here touches the page, so panels.ts keeps the
 * DOM and this stays trivially testable.
 */

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

/** One row of the cue sheet. AUD rows name the file that will play; LED rows
 *  read as "zone → effect", which is the shorthand the scene file uses. */
export function sheetRow(c: Cue, scene: Scene): string {
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
export function tickMark(c: Cue, dur: number): string {
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
