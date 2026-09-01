/**
 * The castle panel — extra chrome the cue desk grows when it is served by the
 * device itself (device.ts's probe decides; this file never runs on a laptop
 * build unless something answers /api/status).
 *
 * Everything here talks to firmware/sd_web.h and is deliberately thin: the
 * desk is the same single file in both worlds, so every feature added here
 * ships to the Mac build too and simply stays dormant. Keep it that way —
 * device-only behaviour behind the probe, shared behaviour in the desk proper.
 *
 * This file is the panel's BIND half: fetch, mount, restore the reader's
 * place, wire every control. What it says — the markup and the status shape —
 * is device_panel_view.ts, and the test bench is device_tests.ts; the three
 * together are one panel kept under the 500-line cap.
 *
 * What it offers, in the order the panel shows it:
 *   - health: firmware version, uptime, and the three numbers that go wrong
 *     (card room, PSRAM — the SD turntable's budget — and internal heap,
 *     which is what a decoder actually starves on)
 *   - the show: the evening playlist, the phone remote, the light override
 *   - the test bench: strip test and speaker test (device_tests.ts)
 *   - the card: every track on it, with play and delete, plus whether the
 *     show's own scene tracks are all present
 *   - the motion sensor, the drop zone, and the boot log
 * Volume lives on the chip (device.ts) alone — the same slider twice was
 * the scatter the dogfood pass called out, and two sliders drift.
 *
 * The intended way to use it with the stage: leave the desk's own audio muted
 * (it is by default), press play here — the castle makes the sound, the canvas
 * above renders the full 21-pixel show the finished rig would do, and the one
 * soldered pixel plays its part in the corner of the room.
 */

import { api } from "./api.js";
import { castleAct } from "./castle_act.js";
import { cardChanged } from "./castle_bus.js";
import { el as byId, reqIn, sel } from "./dom.js";
import { panelMarkup, type DeviceStatus, type SdFile } from "./device_panel_view.js";
import { testPct, wireTests } from "./device_tests.js";

export class DevicePanel {
  private root: HTMLDivElement;
  private body: HTMLDivElement;
  private open = false;
  /** What had focus when the panel opened — focus goes back there (C4). */
  private opener: HTMLElement | null = null;
  /** The last payload rendered, so an unchanged poll skips the rebuild (G4). */
  private lastKey = "";

  constructor(parent?: HTMLElement) {
    this.root = document.createElement("div");
    // Styled in previewer/panels.css — as tokens, not a private palette
    // hardcoded here — one palette, one place to change it.
    // Lives inside the castle dock when device.ts provides one, so chip and
    // panel are one widget rather than two floating boxes.
    this.root.id = "devicePanel";
    // A modal-ish overlay with the semantics to match (C4): named, closable
    // from the keyboard, and focus goes in on open and back out on close.
    this.root.setAttribute("role", "dialog");
    this.root.setAttribute("aria-modal", "true");
    this.root.setAttribute("aria-label", "Castle controls");
    this.root.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.open) this.toggle();
    });
    this.body = document.createElement("div");
    this.root.appendChild(this.body);
    (parent ?? document.body).appendChild(this.root);
  }

  toggle(): void {
    this.open = !this.open;
    this.root.style.display = this.open ? "block" : "none";
    if (this.open) {
      this.opener = document.activeElement instanceof HTMLElement
        ? document.activeElement : null;
      void this.render().then(() =>
        sel<HTMLButtonElement>("#dpClose", this.body)?.focus());
    } else {
      this.lastKey = "";             // a re-open renders fresh
      (this.opener ?? byId("devMore"))?.focus();
    }
  }

  /** Re-render only while open — a closed panel that polls is a panel lying
   *  from a castle that is no longer there (pass 1, J1-4). */
  refresh(): void {
    if (this.open) void this.render();
  }

  private async render(): Promise<void> {
    // C2: while the operator is typing in a panel field, a poll-driven
    // rebuild would eat the caret — skip it; the next poll catches up.
    const focused = document.activeElement;
    if (focused instanceof HTMLElement && this.body.contains(focused)
        && /^(INPUT|SELECT|TEXTAREA)$/.test(focused.tagName)) return;
    let st: DeviceStatus;
    let files: SdFile[] = [];
    try {
      st = await api.castleGet<DeviceStatus>("/api/status");
      // The studio answers a castle-less probe 200 {"studio":true}: an
      // empty object that rendered as "vundefined · NaN MB · no SD card"
      // — a plausible, invented control panel (J2-1). Treat it as down.
      if (st.studio || !st.version) throw new Error("no castle");
      if (st.sd_mounted) files = await api.castleGet<SdFile[]>("/api/files");
    } catch {
      this.body.innerHTML =
        `<div class="dp__hd"><span class="dp__grow">castle stopped answering</span>` +
        `<button id="dpClose" class="dp__x" title="Close this panel" aria-label="Close">✕</button></div>`;
      reqIn<HTMLButtonElement>(this.body, "#dpClose")
        .addEventListener("click", () => this.toggle());
      return;
    }
    const tracks = files.filter((f) => !f.dir && /\.(mp3|wav)$/i.test(f.name));
    const onCard = new Set(tracks.map((f) => f.name));

    // G4: the poll answered with the same truth — keep the DOM it already
    // has (and the scroll, focus and open boot log with it).
    const key = JSON.stringify([st, files]);
    if (key === this.lastKey && this.body.childElementCount) return;
    this.lastKey = key;

    // C2: an innerHTML swap resets everything the reader was holding —
    // scroll position, keyboard focus, an opened boot log. Save it all,
    // rebuild, put it back.
    const keepFocus = document.activeElement instanceof HTMLElement
      && this.body.contains(document.activeElement)
      ? document.activeElement.id : "";
    const keepScroll = { root: this.root.scrollTop, body: this.body.scrollTop };
    const openDetails = new Set(
      Array.from(this.body.querySelectorAll("details"))
        .flatMap((d, i) => (d.open ? [i] : [])));
    const logOut0 = sel<HTMLPreElement>("#dpLogOut", this.body);
    const keepLog = logOut0 && !logOut0.hidden ? logOut0.textContent : null;

    this.body.innerHTML = panelMarkup(st, tracks, onCard);

    reqIn<HTMLButtonElement>(this.body, "#dpClose")
      .addEventListener("click", () => this.toggle());

    // The reader's place, restored (C2).
    this.body.querySelectorAll("details").forEach((d, i) => {
      if (openDetails.has(i)) d.open = true;
    });
    if (keepLog !== null) {
      const logOut1 = sel<HTMLPreElement>("#dpLogOut", this.body);
      const logBtn1 = sel<HTMLButtonElement>("#dpLog", this.body);
      if (logOut1 && logBtn1) {
        logOut1.hidden = false;
        logOut1.textContent = keepLog;
        logBtn1.textContent = "boot log ▾";
      }
    }
    this.root.scrollTop = keepScroll.root;
    this.body.scrollTop = keepScroll.body;
    if (keepFocus) sel(`#${keepFocus}`, this.body)?.focus();

    // Every control below goes through castleAct (device.ts): toast with
    // the castle's reason on failure, and a chip re-poll on success — the
    // panel used to fire-and-forget, so a 404 delete still "succeeded" and
    // the chip said "idle" while the castle played (pass 1, J1-6/J1-7).
    // The playlist toggle re-renders after the queued action lands (the
    // 200 ms bridge plus a beat), so the button reflects the device's own
    // idea of the show, not the click's.
    reqIn<HTMLButtonElement>(this.body, "#dpPlaylist")
      .addEventListener("click", () => {
        void castleAct(`/api/show/${st.show_on ? "stop" : "start"}`,
                       st.show_on ? "show stopped" : "show started")
          .then(() => new Promise(r => setTimeout(r, 600)))
          .then(() => this.render());
      });

    // The light override: a colour parks the chain, "resume show" gives it
    // back. The strip and speaker benches wire themselves (device_tests.ts).
    reqIn<HTMLInputElement>(this.body, "#dpColor")
      .addEventListener("input", (e) => {
        const hex = (e.target as HTMLInputElement).value.slice(1);
        // quiet: the picker fires continuously while the hand drags; the
        // fixed wording lets toast() fold a whole drag's failures into one.
        void castleAct(`/api/light?c=${hex}@${testPct}`, "lights colour", { quiet: true });
      });
    reqIn<HTMLButtonElement>(this.body, "#dpShow")
      .addEventListener("click", () =>
        void castleAct("/api/light?c=show", "lights back to the show"));
    reqIn<HTMLButtonElement>(this.body, "#dpOff")
      .addEventListener("click", () =>
        void castleAct("/api/light?c=off", "lights off"));
    wireTests(this.body);

    this.body.querySelectorAll<HTMLButtonElement>("[data-play]").forEach((b) =>
      b.addEventListener("click", () => {
        const f = tracks[Number(b.dataset.play)];
        if (f === undefined) return;
        void castleAct(`/api/play?f=${encodeURIComponent(f.name)}`,
                       `playing ${f.name} on the castle`);
      }));

    this.body.querySelectorAll<HTMLButtonElement>("[data-del]").forEach((b) =>
      b.addEventListener("click", () => {
        const f = tracks[Number(b.dataset.del)];
        if (f === undefined) return;
        // A deliberate two-step: deleting from a 30 GB card is cheap to undo
        // (push again), but "the show's track vanished on Halloween" is not.
        if (!confirm(`Delete ${f.name} from the castle's SD card?`)) return;
        void castleAct(`/api/files/${encodeURIComponent(f.name)}`,
                       `deleted ${f.name} from the card`, { method: "DELETE" })
          .then((ok) => { if (ok) { cardChanged(); void this.render(); } });
      }));

    // PIR settings: each control posts just its own field; the device's
    // main loop applies them to the persisted entities.
    reqIn<HTMLInputElement>(this.body, "#dpPirArm")
      .addEventListener("change", (e) => {
        const on = (e.target as HTMLInputElement).checked;
        void castleAct(`/api/pir?armed=${on ? 1 : 0}`,
                       on ? "motion sensor armed" : "motion sensor off");
      });
    reqIn<HTMLSelectElement>(this.body, "#dpPirScene")
      .addEventListener("change", (e) => {
        const sc = (e.target as HTMLSelectElement).value;
        void castleAct(`/api/pir?scene=${encodeURIComponent(sc)}`,
                       `motion sensor plays ${sc}`);
      });
    reqIn<HTMLInputElement>(this.body, "#dpPirCool")
      .addEventListener("change", (e) => {
        const v = (e.target as HTMLInputElement).value;
        void castleAct(`/api/pir?cooldown=${v}`, `motion cooldown ${v} s`);
      });

    // Drag-drop upload: the last terminal-only workflow, gone. Files land in
    // the card root, same as tools/sd_sync.py push.
    const drop = reqIn<HTMLDivElement>(this.body, "#dpDrop");
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("dp__drop--over");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("dp__drop--over"));
    drop.addEventListener("drop", async (e) => {
      e.preventDefault();
      drop.classList.remove("dp__drop--over");
      for (const f of Array.from(e.dataTransfer?.files ?? [])) {
        drop.textContent = `uploading ${f.name} (${(f.size / 1024) | 0} KB)…`;
        const r = await api.castlePut(f.name, f);
        drop.textContent = r.ok ? `✓ ${f.name}` : `✗ ${f.name} failed`;
      }
      cardChanged();                 // the Library below re-reads the card now
      void this.render();
    });

    const logBtn = reqIn<HTMLButtonElement>(this.body, "#dpLog");
    const logOut = reqIn<HTMLPreElement>(this.body, "#dpLogOut");
    logBtn.addEventListener("click", async () => {
      const showing = !logOut.hidden;
      logOut.hidden = showing;
      logBtn.textContent = showing ? "boot log ▸" : "boot log ▾";
      if (!showing) {
        logOut.textContent = "loading…";
        try {
          // The ring keeps ANSI colour codes out already; strip any stragglers.
          logOut.textContent =
            (await api.castleBootlog()).replace(/\x1b\[[0-9;]*m/g, "");
        } catch {
          logOut.textContent = "could not fetch the boot log";
        }
      }
    });
  }
}
