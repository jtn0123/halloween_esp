/**
 * What the show is allowed to cost, in the two builds that can carry it.
 *
 * The flash budget used to be one parenthesised percentage next to the scene
 * count, which is the wrong shape for the question people actually ask —
 * "what is eating it, and what happens if I add one more scene". This panel
 * answers that: every scene is a band you can click, and the band tells you
 * its own size, its share, and what it is.
 *
 * Two builds, because there are two:
 *
 *   Flash (`make build`, firmware/castle_flash.yaml) — one 3.87 MB app
 *     partition (firmware/partitions_single_app.csv), roughly 1.1 MB of it
 *     firmware, leaving ~2.9 MB for every scene combined. `make audio` fails
 *     loudly when the total goes over. This is the real constraint.
 *
 *   SD (`make sd-build`, firmware/castle_sd.yaml) — EXPERIMENTAL, see
 *     PROJECT_NOTES §12.9. tools/sd_sync.py puts the imported library in the
 *     card root, the eight rendered scenes in /sd/scenes and this page in
 *     /sd/site, and the device streams off the card. Storage stops being the
 *     ceiling; the caveats move to the hardware instead.
 *
 * Every figure here is measured rather than quoted, with one stated exception
 * per build — the card's capacity, which nothing reports, and the size of a
 * scene that has not been rendered yet.
 */

import { sceneTint } from "./scene_tint.js";
import type { Scene } from "./types.js";

/** firmware/partitions_single_app.csv — app0, 0x3E0000. */
const APP_PARTITION = 0x3E0000;
/** Flash left for ALL scene audio after the firmware — the figure
 *  tools/render_audio.py budgets against and tracks/README.md quotes. If one
 *  moves, all three have to. */
const FLASH_BUDGET = 2.9 * 1024 * 1024;
/** Whatever is not the scenes' to spend: firmware, effects, the web server. */
const FIRMWARE = APP_PARTITION - FLASH_BUDGET;
/** `hardware.audio.bitrate` in scenes.yaml: 32 kbps mono, so 4 KB per second.
 *  Only used for a scene that has not been rendered — a real file wins. */
const BYTES_PER_SEC = 32_000 / 8;
/** Until a castle reports its card (`sd_total_kb`, v5.23+), the figure the
 *  rest of the project quotes (see import_opts.ts) — labelled as an
 *  assumption for exactly as long as it is one. */
const CARD_ASSUMED = 32 * 1024 * 1024 * 1024;

/** One clickable band, and one legend row. */
interface Item {
  key: string;
  label: string;
  bytes: number;
  color: string;
  /** Where the number comes from — file, tool, or assumption. */
  meta: string;
  blurb: string;
  /** Scene bands are the ones worth hiding from the legend when they are
   *  slivers; firmware and free are always worth naming. */
  scene?: boolean;
}

/** The library, as the SD view needs it. */
export interface LibraryTrack { id: string; kb: number }

export interface BudgetApi {
  /** The imported library changed — it is the SD view's biggest number. */
  setTracks(tracks: readonly LibraryTrack[]): void;
  /** The castle said how big its card is (KB), or stopped saying (null). */
  setCard(totalKb: number | null): void;
}

const MB = 1024 * 1024;

/** Bytes as this panel prints them: KB, then MB, then GB. */
function size(n: number): string {
  if (n >= 1024 * MB) return `${(n / 1024 / MB).toFixed(2)} GB`;
  if (n >= MB) return `${(n / MB).toFixed(2)} MB`;
  return `${Math.round(n / 1024)} KB`;
}

/** A share of the ceiling. "0.0%" of a 32 GB card is a rounding artefact
 *  pretending to be a measurement; below a tenth of a percent, say that. */
function share(part: number, whole: number): string {
  const pct = part / whole * 100;
  if (pct >= 10) return `${Math.round(pct)}%`;
  return pct >= 0.1 ? `${pct.toFixed(1)}%` : "under 0.1%";
}

/** What a scene costs in flash: the rendered file when there is one, and the
 *  bitrate's arithmetic when there is not. `make audio` produces the first. */
const sceneBytes = (s: Scene): number => s.bytes || (s.dur / 1000) * BYTES_PER_SEC;

/**
 * This page's own cost in /sd/site.
 *
 * sd_sync pushes two copies — index.html.gz and a plain fallback for clients
 * that cannot take gzip — so the card holds both, and both are counted. The
 * plain size is exact: it is what this document weighed on the way in. The
 * gzipped size is only knowable when the server actually compressed the
 * response, so when it did not we count a second plain copy and say so. That
 * makes the figure an upper bound rather than a guess.
 */
function siteCost(): { bytes: number; meta: string } {
  const nav = performance.getEntriesByType("navigation")[0] as
    PerformanceNavigationTiming | undefined;
  const plain = nav?.decodedBodySize || document.documentElement.outerHTML.length;
  const encoded = nav?.encodedBodySize ?? 0;
  const compressed = encoded > 0 && encoded < plain;
  return {
    bytes: plain + (compressed ? encoded : plain),
    meta: compressed
      ? `${size(plain)} plain + ${size(encoded)} gzipped · both measured`
      : `${size(plain)} plain × 2 · this server did not gzip, so the .gz is counted at full size`,
  };
}

function must(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`budget: missing #${id}`);
  return el;
}

export function initBudget(scenes: readonly Scene[]): BudgetApi {
  const tabsEl = must("budTabs");
  const headEl = must("budHead");
  const barEl = must("budBar");
  const pickEl = must("budPick");
  const rowsEl = must("budRows");
  const noteEl = must("budNote");

  let build: "flash" | "sd" = "flash";
  let picked: string | null = null;
  let library: readonly LibraryTrack[] = [];
  /** Bytes the castle reports for its card; null = assume. */
  let cardKb: number | null = null;

  /** Everything on the ground, largest ceiling last. */
  function items(): { items: Item[]; capacity: number; used: number } {
    const showBytes = scenes.reduce((a, s) => a + sceneBytes(s), 0);

    if (build === "sd") {
      const lib = library.reduce((a, t) => a + t.kb * 1024, 0);
      const site = siteCost();
      const used = showBytes + lib + site.bytes;
      const card = cardKb ? cardKb * 1024 : CARD_ASSUMED;
      return {
        capacity: card,
        used,
        items: [
          { key: "scenes", label: `/sd/scenes — the show's ${scenes.length} rendered scenes`,
            bytes: showBytes, color: "var(--accent)",
            meta: `${scenes.length} files · tools/sd_sync.py scenes`,
            blurb: "The same rendered show the flash build embeds, streamed off the card instead of out of the app partition." },
          { key: "tracks", label: `Card root — imported library (${library.length} tracks)`,
            bytes: lib, color: "var(--cool)",
            meta: library.length
              ? `${library.length} files · tools/sd_sync.py push`
              : "nothing imported yet, or the studio is not running",
            blurb: "Tracks upload as-is: playback streams off the card through the device's own web server (firmware/sd_web_site.h), so there is no PSRAM ceiling to transcode under." },
          { key: "site", label: "/sd/site — this cue desk", bytes: site.bytes,
            color: "var(--spirit)", meta: site.meta,
            blurb: "One self-contained file, which is the constraint that makes it servable by a microcontroller at all." },
          { key: "free", label: "Free", bytes: Math.max(0, card - used),
            color: "var(--line-2)",
            meta: cardKb
              ? `${size(card)} card — as the castle reports it`
              : "32 GB card, assumed — no castle is answering to say",
            blurb: "The card holds as many tracks as you like. The ceiling that mattered was flash, not storage." },
        ],
      };
    }

    return {
      capacity: APP_PARTITION,
      used: showBytes + FIRMWARE,
      items: [
        { key: "fw", label: "Firmware + effects", bytes: FIRMWARE, color: "var(--line-2)",
          meta: "app0, 3.87 MB single-app partition",
          blurb: "One app partition, so no OTA slot: embedded audio pushed the binary past both 1.75 MB halves." },
        ...scenes.map((s, i) => ({
          key: `s${i}`,
          label: s.name,
          bytes: sceneBytes(s),
          color: sceneTint(s, 0.85),
          meta: `${s.id} · ${(s.dur / 1000).toFixed(0)}s${s.loop ? " ↻" : ""} · ${s.kind}`
            + ` · ${s.file}${s.bytes ? "" : " (not rendered — estimated at 32 kbps mono)"}`,
          blurb: s.blurb,
          scene: true,
        })),
        { key: "free", label: "Free flash", bytes: Math.max(0, FLASH_BUDGET - showBytes),
          color: "var(--line)", meta: `of the ${size(FLASH_BUDGET)} the scenes share`,
          blurb: "`make audio` fails loudly if the total goes over. At 32 kbps mono one minute costs about 235 KB." },
      ],
    };
  }

  function draw(): void {
    const { items: list, capacity, used } = items();

    tabsEl.querySelectorAll<HTMLElement>("button").forEach(b => {
      b.setAttribute("aria-pressed", String(b.dataset["bud"] === build));
    });

    // `make audio` refuses to render a show that does not fit, so this should
    // be unreachable — but a hand-edited scenes.yaml can load one, and a bar
    // that quietly rescales past its own ceiling is the worst way to find out.
    const over = used > capacity;
    headEl.innerHTML = `${size(used)} <span>of ${size(capacity)} · `
      + `${share(used, capacity)}${over ? " — over budget" : ""}</span>`;
    headEl.style.color = over ? "var(--alarm)" : "";

    // Proportional, with a floor so a sliver stays visible. On the card view
    // that means three hairlines against a wall of free space, which is the
    // honest picture and the reason the SD build exists.
    barEl.innerHTML = list.map(it => {
      const w = Math.max(0.4, it.bytes / capacity * 100).toFixed(2);
      return `<i data-key="${it.key}" style="width:${w}%;background:${it.color}"`
        + ` class="${picked === it.key ? "on" : ""}"`
        + ` title="${it.label} — ${size(it.bytes)}"></i>`;
    }).join("");

    const chosen = list.find(it => it.key === picked) ?? null;
    pickEl.hidden = chosen === null;
    if (chosen) {
      pickEl.innerHTML = `<div class="budget__pickhd">`
        + `<span>${chosen.label}</span>`
        + `<b>${size(chosen.bytes)} · ${share(chosen.bytes, capacity)}</b></div>`
        + `<span class="budget__pickmeta">${chosen.meta}</span>`
        + `<span class="budget__pickblurb">${chosen.blurb}</span>`;
    }

    // The legend names what is worth naming: everything that is not a scene,
    // plus the scenes big enough to be worth arguing about. The bar still
    // carries all of them.
    const legend = [
      ...list.filter(it => it.key !== "free" && (!it.scene || it.bytes / capacity > 0.012))
        .slice(0, 6),
      list.find(it => it.key === "free"),
    ].filter((it): it is Item => it !== undefined);
    rowsEl.innerHTML = legend.map(it =>
      `<button type="button" data-key="${it.key}" class="budget__row${picked === it.key ? " on" : ""}">`
      + `<i style="background:${it.color}"></i>`
      + `<span>${it.label}</span><b>${size(it.bytes)}</b></button>`).join("");

    noteEl.textContent = build === "sd"
      ? "Experimental (`make sd-build`, PROJECT_NOTES §12.9): no ESP32-S2 has been shown doing this, and the Feather needs a wing to have a slot at all. The flash scenes keep working with no card in."
      : "Click a band for the scene behind it. `make audio` fails loudly if the total goes over.";
  }

  /** One handler for the bar and the legend — the tiny bands on the card view
   *  are hard to hit, so the legend row has to select too. */
  const pick = (e: Event): void => {
    const key = (e.target as HTMLElement | null)?.closest<HTMLElement>("[data-key]")
      ?.dataset["key"];
    if (!key) return;
    picked = picked === key ? null : key;
    draw();
  };
  barEl.addEventListener("click", pick);
  rowsEl.addEventListener("click", pick);

  tabsEl.addEventListener("click", e => {
    const b = (e.target as HTMLElement | null)?.closest<HTMLElement>("button");
    const to = b?.dataset["bud"];
    if (to !== "flash" && to !== "sd") return;
    build = to;
    picked = null;      // keys do not survive the switch, and neither should the selection
    draw();
  });

  draw();
  return {
    setTracks(tracks: readonly LibraryTrack[]): void {
      library = tracks;
      if (build === "sd") draw();
    },
    setCard(totalKb: number | null): void {
      if (totalKb === cardKb) return;
      cardKb = totalKb;
      if (build === "sd") draw();
    },
  };
}
