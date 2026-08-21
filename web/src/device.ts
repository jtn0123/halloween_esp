/**
 * The device bridge — what makes the cue desk aware a real castle is
 * listening, whether this page is served BY the castle (from its SD card,
 * see firmware/sd_web.h) or by the studio relaying to one (castle_link).
 *
 * The mode switch is one probe: does `/api/status` answer from the page's
 * own origin without the {"studio": true} marker. No build flag, no second
 * bundle — opened as a plain file the probe fails and the desk is a pure
 * simulator.
 *
 * In device mode:
 *   - a status chip appears: version, SD state, what is PLAYING right now,
 *     the castle's volume, and the ♪ sound-route switch;
 *   - the SAME ♪ switch is also mounted in the transport next to Play,
 *     because "where will sound come out" is decided at the moment of
 *     pressing Play, not down in a corner (dogfood 004/006);
 *   - while sound routes to the Mac the castle's volume controls are
 *     disabled, not just ignored — a slider that can silently un-hush the
 *     speaker you turned off is a trap, not a control;
 *   - picking a scene in the desk also fires it on the real castle;
 *   - on load the desk ADOPTS the castle's current scene;
 *   - every action answers with a toast, and the status re-polls ~1 s
 *     later so the chip reflects what the click did instead of waiting for
 *     the slow 15 s cycle.
 *
 * Mirroring is fire-and-forget on purpose. The desk must never stall on the
 * radio link, and a lost POST costs one button press, not state: the device
 * runs its own show engine and this page only nudges it.
 */

import { castleChanged, isCastleBusy, onCastleChanged, setCastleLive }
  from "./castle_bus.js";
import { DevicePanel } from "./device_panel.js";

/** What `deviceBridge()` hands back; every call is safe in simulator mode. */
export interface DeviceLink {
  /** Fire scene `id` on the castle, if one is listening and mirroring is on. */
  scene(id: string): void;
  /** Stop castle audio + scene. */
  stop(): void;
}

interface Status {
  version: string;
  /** Absent on the native-API fallback — render as unknown, never as "no SD"
   *  (dogfood 001: the fallback's missing field displayed as a lie). */
  sd_mounted?: boolean;
  /** KB free on the card — v5.23+; older firmware omits it. */
  sd_free_kb?: number;
  volume?: number;
  scene?: string;
  track?: string;
  /** tools/studio.py answers the probe too (so it isn't a console error),
   *  marked with this so we don't mistake the laptop for the castle. */
  studio?: boolean;
}

/** Room for the studio to try two addresses (1 s connect each, castle_link
 *  PROBE_CONNECT_S) or one slow answer. A castle that is merely rebooting at
 *  page load is not lost either way — see RETRY_MS. */
const PROBE_TIMEOUT_MS = 2500;
/** While no castle answers, re-probe this often: a castle that boots after
 *  the page loaded must still get its chip (pass 1, J1-3). */
const RETRY_MS = 5000;
/** The slow poll once live; actions re-poll sooner via castleAct(). */
const POLL_MS = 15000;

async function probe(): Promise<Status | null> {
  try {
    const r = await fetch("/api/status", {
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    if (!r.ok) return null;
    const s = (await r.json()) as Status;
    return s.studio ? null : s;
  } catch {
    return null;
  }
}

/** "SD ok" / "no SD", or nothing at all when the answer isn't known. */
const sdText = (s: Status): string =>
  s.sd_mounted === undefined ? "" : s.sd_mounted ? " · SD ok" : " · no SD";

/** The chip's richer version: how much room the card actually has. */
const sdChip = (s: Status): string => {
  if (s.sd_mounted === undefined) return "";
  if (!s.sd_mounted) return " · no SD";
  return s.sd_free_kb
    ? ` · SD ${(s.sd_free_kb / 1048576).toFixed(1)} GB free` : " · SD ok";
};

/** Where toasts stack: one fixed column above the dock, newest at the
 *  bottom. Two toasts a second apart used to print on the same pixels
 *  (J2-3); now they stack, identical text is not repeated while it is still
 *  showing, and only the last few stay on screen. */
const TOAST_MAX = 3;
function toastHost(): HTMLDivElement {
  let host = document.getElementById("toasts") as HTMLDivElement | null;
  if (!host) {
    host = document.createElement("div");
    host.id = "toasts";
    host.style.cssText =
      "position:fixed;right:12px;bottom:84px;z-index:41;display:flex;" +
      "flex-direction:column;align-items:flex-end;gap:6px;pointer-events:none";
    document.body.appendChild(host);
  }
  return host;
}

/** One small transient message near the chip. The device queues actions, so
 *  "queued" IS the honest success state — see the interval in castle_sd.yaml. */
export function toast(msg: string, isError = false): void {
  const host = toastHost();
  for (const live of Array.from(host.children)) {
    if (live.textContent === msg) return;       // already saying exactly this
  }
  while (host.children.length >= TOAST_MAX) host.firstElementChild?.remove();
  const el = document.createElement("div");
  el.textContent = msg;
  el.style.cssText =
    "padding:.4rem .7rem;border-radius:8px;font:12px system-ui;max-width:60vw;" +
    "transition:opacity .4s;opacity:1;" +
    (isError ? "background:#5a1a2a;color:#ffd8e0;" : "background:#2a3a1a;color:#e0ffd0;");
  host.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; }, isError ? 3200 : 1400);
  setTimeout(() => el.remove(), isError ? 3700 : 1900);
}

/** Why a castle call failed, in the castle's own words: its error pages are
 *  short plain text ("unknown scene", "need ?v=0..100", "no SD card"), the
 *  studio's relay answers JSON {"error": ...}. "failed" alone cannot tell a
 *  typo from a dead castle (pass 1, J1-6). */
export async function failReason(r: Response): Promise<string> {
  if (r.status === 502) return "castle not reachable";
  if (r.status === 504) return "castle did not answer in time";
  try {
    const text = (await r.text()).trim();
    if (text.startsWith("{")) {
      const j = JSON.parse(text) as { error?: string };
      return j.error || `HTTP ${r.status}`;
    }
    return text.slice(0, 80) || `HTTP ${r.status}`;
  } catch {
    return `HTTP ${r.status}`;
  }
}

export interface ActOpts {
  method?: "POST" | "DELETE";
  /** Toast only on failure — for controls that fire continuously. */
  quiet?: boolean;
}

/** ONE castle action: call, toast the outcome (with the reason when it went
 *  wrong), and announce the change so the chip re-polls about a second
 *  later — instead of waiting out the 15 s cycle. Every castle button in
 *  the desk goes through here: the chip, the panel, the library rows. */
export async function castleAct(path: string, okMsg: string,
                                opts: ActOpts = {}): Promise<boolean> {
  // Re-poll after EVERY outcome: a failure is news too — it is usually how
  // the desk first learns the castle went away, and the masthead/chip
  // should say so within a second rather than at the next 15 s poll.
  let r: Response;
  try {
    r = await fetch(path, { method: opts.method ?? "POST" });
  } catch {
    toast(`${okMsg} failed — no answer from the castle`, true);
    castleChangedSoon();
    return false;
  }
  if (!r.ok) {
    toast(`${okMsg} failed — ${await failReason(r)}`, true);
    castleChangedSoon();
    return false;
  }
  if (!opts.quiet) toast(okMsg);
  castleChangedSoon();
  return true;
}

/** The chip's re-poll, debounced: a burst of clicks is one poll, and it
 *  lands after the castle's queued action + main-loop tick (~200 ms). */
let changedTimer: number | undefined;
function castleChangedSoon(): void {
  clearTimeout(changedTimer);
  changedTimer = window.setTimeout(() => {
    changedTimer = undefined;
    castleChanged();
  }, 900);
}

export interface BridgeOpts {
  /** Called once, on first contact, with the scene the castle is running —
   *  so the desk can open showing reality instead of the default. */
  adoptScene?: (sceneId: string) => void;
  /**
   * The device half of the masthead's status line — "castle v1.4 · SD ok ·
   * mirroring", or "castle not answering" once it stops replying. `ok` is
   * false only in that last case, so the masthead's dot can stop claiming
   * everything is fine. Never called in simulator mode, where the desk's own
   * default stands.
   */
  onStatus?: (line: string, ok: boolean) => void;
  /**
   * The ♪ SOUND switch: one control that routes audio to this browser
   * (true — castle speaker just went to volume 0) or to the castle (false —
   * this browser should hush). Pressing the switch IS the consent the
   * muted-by-default rule wants, so the desk may unmute on `true`.
   */
  onSoundRoute?: (local: boolean) => void;
}

export function deviceBridge(opts: BridgeOpts = {}): DeviceLink {
  let live = false;
  let mirror = true;
  let lastVol = 70;
  // Where sound comes out on wiring-day setups: this browser, or the castle's
  // own amp. Survives reloads — an operator sets it once per bench session.
  let soundRoute: "mac" | "castle" =
    localStorage.getItem("castleSoundRoute") === "castle" ? "castle" : "mac";
  let routeEnforced = false;
  let lastStatus: Status | null = null;

  // One castle home: the dock owns the corner, the chip is its collapsed
  // face and the panel its expanded one — a single widget, not a chip PLUS
  // a separate floating box (the last of the dogfood's "scatterbrained").
  const dock = document.createElement("div");
  dock.id = "castleDock";
  document.body.appendChild(dock);
  const panel = new DevicePanel(dock);

  const chip = document.createElement("div");
  chip.id = "deviceChip";
  chip.style.cssText =
    "display:none;" +
    "background:#241a38;color:#e8e0f0;border:1px solid #503a75;" +
    "border-radius:10px;padding:.5rem .75rem;font:13px system-ui;" +
    "box-shadow:0 4px 16px rgba(0,0,0,.5)";
  dock.appendChild(chip);

  // True while the last poll answered. The masthead must not flip back to
  // a healthy line just because ♪ or the mirror box was toggled while the
  // castle is down (pass 1, J1-4) — the last GOOD status is not the truth.
  let lastOk = true;
  let lastSeen: Date | null = null;
  let lastPlaying = "idle";

  /** POST, toast, then re-poll — the chip shows what the click did about a
   *  second later (queued action + main-loop tick) instead of after 15 s. */
  const act = (path: string, okMsg: string): void => { void castleAct(path, okMsg); };
  // Anything anywhere in the desk that changed the castle (card-row Play,
  // the panel's ▶/delete/light/PIR) lands here — one re-poll for all of it.
  onCastleChanged(() => { void refresh(); });

  /* ── The ♪ route, in both homes ────────────────────────────────────────
     One switch rendered twice: in the transport (where Play is pressed) and
     on the chip (always on screen). Both call applyRoute; syncRouteUI keeps
     every rendering and the volume controls' enabled state agreeing. */
  let routeBtn: HTMLButtonElement | null = null;

  function mountRouteBtn(): void {
    const muteEl = document.getElementById("mute");
    if (!muteEl || routeBtn) return;
    routeBtn = document.createElement("button");
    routeBtn.id = "sndRoute";
    routeBtn.type = "button";
    routeBtn.className = "btn";
    routeBtn.addEventListener("click", () =>
      applyRoute(soundRoute === "mac" ? "castle" : "mac", true));
    muteEl.after(routeBtn);
    syncRouteUI();
  }

  function syncRouteUI(): void {
    const label = `♪ ${soundRoute === "mac" ? "Mac" : "Castle"}`;
    const title = soundRoute === "mac"
      ? "Sound comes out of this Mac; the castle speaker is off. "
        + "Click to send sound to the castle instead. Lights always play on the castle."
      : "Sound comes out of the castle's speaker. Click to play it on this "
        + "Mac instead. Lights always play on the castle.";
    for (const b of [routeBtn, chip.querySelector<HTMLButtonElement>("#devSnd")]) {
      if (b) { b.textContent = label; b.title = title; }
    }
    // The castle-volume controls govern a speaker that ♪ Mac just silenced —
    // disable them rather than let a stray drag un-hush it (route-aware).
    // …and a castle that is not answering has no volume to set (J2-2):
    // flipping ♪ to Castle while it is down must not light the slider up.
    const hushed = soundRoute === "mac";
    const vol = chip.querySelector<HTMLInputElement>("#devVol");
    const muteB = chip.querySelector<HTMLButtonElement>("#devMute");
    if (vol) {
      vol.disabled = hushed || !lastOk;
      vol.title = !lastOk ? "Castle not answering"
        : hushed ? "Castle speaker is off while sound plays on the Mac (♪ switch)"
        : "Castle speaker volume";
    }
    if (muteB) muteB.disabled = hushed || !lastOk;
    for (const id of ["devStop", "devMirror"]) {
      const el = chip.querySelector<HTMLInputElement | HTMLButtonElement>(`#${id}`);
      if (el) el.disabled = !lastOk;
    }
  }

  function applyRoute(route: "mac" | "castle", announce: boolean): void {
    soundRoute = route;
    localStorage.setItem("castleSoundRoute", route);
    const vol = chip.querySelector<HTMLInputElement>("#devVol");
    if (route === "mac") {
      // Hush the porch, sound the desk. Remember the amp level for the
      // flip back so "Castle" restores what the hand last set.
      if (vol && Number(vol.value) > 0) lastVol = Number(vol.value);
      if (vol) vol.value = "0";
      act("/api/volume?v=0",
          announce ? "sound: Mac — castle speaker off" : "castle speaker off");
    } else {
      const to = lastVol || 70;
      if (vol) vol.value = String(to);
      act(`/api/volume?v=${to}`, `sound: castle — volume ${to}`);
    }
    syncRouteUI();
    if (lastStatus) sayStatus(lastStatus);   // the masthead names the route
    if (announce) opts.onSoundRoute?.(route === "mac");
  }

  /** What the masthead says about the castle. Split out of `render` so that
   *  toggling mirroring can refresh the line WITHOUT rebuilding the chip —
   *  a rebuild mid-drag snaps the volume slider back to the last polled
   *  value, which is not what the hand on it just asked for. */
  const sayStatus = (s: Status): void => {
    const route = ` · sound: ${soundRoute === "mac" ? "Mac" : "castle"}`;
    if (!lastOk) {
      opts.onStatus?.(`castle not answering${route}`, false);
      return;
    }
    opts.onStatus?.(`castle v${s.version}${sdText(s)}`
      + ` · ${mirror ? "mirroring" : "not mirroring"}` + route, true);
  };

  const render = (s: Status): void => {
    chip.style.display = "block";
    chip.style.opacity = "1";
    const wasDown = !lastOk;
    lastOk = true;
    lastVol = s.volume ?? lastVol;
    lastStatus = s;
    sayStatus(s);
    // Back from the dead: the open panel was showing "stopped answering".
    if (wasDown) panel.refresh();
    // A track can play with no scene (the card rows, the panel's ▶) — the
    // chip must say so, not "idle" (caught live against the emulator).
    const bits = [s.scene && s.scene !== "stop" ? s.scene : "", s.track ?? ""]
      .filter(Boolean);
    const playing = bits.length ? `▶ ${bits.join(" · ")}` : "idle";
    lastSeen = new Date();
    lastPlaying = playing;
    chip.innerHTML =
      `<div>🏰 castle v${s.version}${sdChip(s)} · ` +
      `<span id="devNow" style="color:#b8a8d8">${playing}</span></div>` +
      `<div style="margin-top:.25rem;display:flex;align-items:center;gap:.4rem">` +
      `<button id="devSnd" ` +
      `style="cursor:pointer;background:#3a2a55;color:inherit;border:0;` +
      `border-radius:6px;padding:.2rem .5rem;white-space:nowrap"></button>` +
      `<button id="devMute" title="Mute the castle speaker" ` +
      `style="cursor:pointer;background:#3a2a55;color:inherit;border:0;` +
      `border-radius:6px;padding:.15rem .45rem">${lastVol === 0 ? "🔇" : "🔊"}</button>` +
      `<input id="devVol" type="range" min="0" max="100" value="${lastVol}" ` +
      `style="width:90px">` +
      `<label style="cursor:pointer;white-space:nowrap" ` +
      `title="Also fire scene picks on the real castle">` +
      `<input type="checkbox" id="devMirror" ${mirror ? "checked" : ""}> on castle</label>` +
      `<button id="devStop" title="Stop the castle: audio and scene" ` +
      `style="cursor:pointer;background:#3a2a55;` +
      `color:inherit;border:0;border-radius:6px;padding:.2rem .5rem">■</button>` +
      `<button id="devMore" title="The castle's own controls: SD library, show, light, motion sensor, boot log" ` +
      `style="cursor:pointer;background:#3a2a55;color:inherit;border:0;` +
      `border-radius:6px;padding:.2rem .5rem">🏰 Castle</button>` +
      `</div>`;

    chip.querySelector<HTMLInputElement>("#devMirror")!
      .addEventListener("change", (e) => {
        mirror = (e.target as HTMLInputElement).checked;
        sayStatus(s);   // the masthead says whether picks reach the porch
      });
    chip.querySelector<HTMLButtonElement>("#devStop")!
      .addEventListener("click", () => act("/api/stop", "stop"));
    chip.querySelector<HTMLButtonElement>("#devMore")!
      .addEventListener("click", () => panel.toggle());
    chip.querySelector<HTMLButtonElement>("#devSnd")!
      .addEventListener("click", () =>
        applyRoute(soundRoute === "mac" ? "castle" : "mac", true));

    let volTimer: number | undefined;
    const vol = chip.querySelector<HTMLInputElement>("#devVol")!;
    vol.addEventListener("input", () => {
      clearTimeout(volTimer);
      volTimer = window.setTimeout(
        () => act(`/api/volume?v=${vol.value}`, `volume ${vol.value}`), 150);
    });
    chip.querySelector<HTMLButtonElement>("#devMute")!
      .addEventListener("click", () => {
        // Mute is volume 0 with memory — the device has no separate flag.
        const to = Number(vol.value) === 0 ? (lastVol || 70) : 0;
        if (to === 0) lastVol = Number(vol.value);
        vol.value = String(to);
        act(`/api/volume?v=${to}`, to === 0 ? "muted" : `volume ${to}`);
      });

    // First contact: make the amp match the remembered route. Only the
    // castle side — the desk's own muted-by-default rule still governs
    // page load, so no browser audio starts without a press.
    if (!routeEnforced) {
      routeEnforced = true;
      if (soundRoute === "mac" && (s.volume ?? 0) > 0) applyRoute("mac", false);
    }
    syncRouteUI();
  };

  /** One poll: render truth, or dim the chip and say the castle went quiet. */
  async function refresh(): Promise<void> {
    if (!live) return;
    const now = await probe();
    if (now !== null) {
      render(now);
      setCastleLive(true);
      return;
    }
    // A castle busy swallowing a multi-MB send is not a castle that left:
    // its one httpd task answers the poll when the bytes are down.
    if (isCastleBusy()) return;
    chip.style.opacity = "0.4";      // castle stopped answering
    lastOk = false;
    if (lastStatus) sayStatus(lastStatus);
    // The controls that would lie: ■/volume/mute/mirror act on a castle
    // that is not there (syncRouteUI reads lastOk). ♪ stays — where sound
    // comes out is this desk's own decision — and 🏰 opens a panel that
    // says what happened. The ▶ line is from the past, and says so.
    syncRouteUI();
    const nowEl = chip.querySelector<HTMLElement>("#devNow");
    if (nowEl && lastSeen) {
      nowEl.textContent = `last seen ${lastSeen.toLocaleTimeString([], { timeStyle: "short" })}`;
      nowEl.title = `What the castle said then: ${lastPlaying}`;
    }
    panel.refresh();
    setCastleLive(false);
  }

  /** First contact, whenever it comes: the chip, the ♪ switch, the castle's
   *  scene. A castle absent at load is not absent for the session — the desk
   *  keeps asking every RETRY_MS until one answers (pass 1, J1-3). */
  function firstContact(s: Status): void {
    live = true;
    render(s);
    mountRouteBtn();
    setCastleLive(true);
    if (s.scene && s.scene !== "stop") opts.adoptScene?.(s.scene);
    // A slow poll keeps the chip honest (version after an OTA, card pulled,
    // a scene the PIR fired while nobody was looking); actions re-poll
    // themselves sooner via castleAct().
    setInterval(() => void refresh(), POLL_MS);
  }

  void probe().then((s) => {
    if (s !== null) { firstContact(s); return; }
    const retry = window.setInterval(() => {
      if (live) { clearInterval(retry); return; }
      void probe().then((now) => {
        if (now !== null && !live) { clearInterval(retry); firstContact(now); }
      });
    }, RETRY_MS);
  });

  return {
    scene(id: string): void {
      if (!live || !mirror) return;
      act(`/api/scene?s=${encodeURIComponent(id)}`, `scene ${id}`);
    },
    stop(): void {
      if (!live) return;
      act("/api/stop", "stop");
    },
  };
}
