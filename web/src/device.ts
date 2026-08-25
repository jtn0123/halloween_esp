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

import { api } from "./api.js";
import { isCastleBusy, onCastleChanged, setCastleLive } from "./castle_bus.js";
import { castleAct } from "./castle_act.js";
import { chipHtml, nowLine, sdText, wireChip } from "./device_chip.js";
import { esc } from "./dom.js";
import { DevicePanel } from "./device_panel.js";

// The action layer (toast, failReason, castleAct) lives in castle_act.ts
// now; re-exported so the panel, the library rows and the tests keep their
// import path.
export { toast, failReason, castleAct, type ActOpts } from "./castle_act.js";

/** What `deviceBridge()` hands back; every call is safe in simulator mode. */
export interface DeviceLink {
  /** Fire scene `id` on the castle, if one is listening and mirroring is on. */
  scene(id: string): void;
  /** Stop castle audio + scene — mirrored like a scene pick, so mirroring
   *  off keeps Stop local too. */
  stop(): void;
}

interface Status {
  version: string;
  /** Absent on the native-API fallback — render as unknown, never as "no SD"
   *  (dogfood 001: the fallback's missing field displayed as a lie). */
  sd_mounted?: boolean;
  /** KB free on the card — v5.23+; older firmware omits it. */
  sd_free_kb?: number;
  /** The card's size in KB — v5.23+. The SD budget reads it (JB1-11). */
  sd_total_kb?: number;
  volume?: number;
  scene?: string;
  track?: string;
  /** tools/studio.py answers the probe too (so it isn't a console error),
   *  marked with this so we don't mistake the laptop for the castle. */
  studio?: boolean;
  /** On the studio's marker answer: the castle host it is configured to
   *  relay to — present means "a castle is expected and not answering". */
  castle?: string;
  /** Comma-joined scene ids the firmware was BUILT with (v5.42+) — the
   *  desk diffs them against its own list to spot a stale board (C6). */
  scenes?: string;
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

/** Set when the studio answered FOR a castle it could not reach: the host
 *  it was trying. That is the one no-castle case worth surfacing (C3) — a
 *  page opened from disk, or a studio with no castle configured, is a
 *  simulator on purpose and gets no placeholder. */
let expectedCastle: string | null = null;

async function probe(): Promise<Status | null> {
  try {
    const r = await api.castleProbe(PROBE_TIMEOUT_MS);
    if (!r.ok) return null;
    const s = (await r.json()) as Status;
    if (s.studio) {
      expectedCastle = typeof s.castle === "string" && s.castle ? s.castle : null;
      return null;
    }
    return s;
  } catch {
    return null;
  }
}

export interface BridgeOpts {
  /** Called on first contact with the scene the castle is running — so the
   *  desk can open showing reality instead of the default. With `follow`
   *  it is also called on every later change, "" meaning the castle went
   *  idle. */
  adoptScene?: (sceneId: string) => void;
  /** Keep adopting after first contact (the kiosk: a display that tracks
   *  the porch). Default false — the desk's operator picks their own. */
  follow?: boolean;
  /** Start with mirroring off: nothing this page does reaches the castle
   *  until the chip's "on castle" box is ticked. The kiosk passes false. */
  mirror?: boolean;
  /** How often to re-poll once live. Default POLL_MS. */
  pollMs?: number;
  /** The card's reported size, KB, on every answer — null when the castle
   *  does not say (older firmware, no card) or has stopped answering. */
  onCard?: (totalKb: number | null) => void;
  /** The scene ids the castle's FIRMWARE was built with, on every answer —
   *  null when the firmware predates the field. The desk diffs this against
   *  its own list and dims scenes the board cannot play (C6). */
  onScenes?: (ids: string[] | null) => void;
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
  let mirror = opts.mirror ?? true;
  let lastVol = 70;
  // The scene the castle was last seen running ("" = idle); null before
  // first contact. Only `follow` acts on a change.
  let followed: string | null = null;
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
  chip.id = "deviceChip";          // styled in previewer/panels.css; hidden until contact
  dock.appendChild(chip);

  /* ── The hand wins (C1) ──────────────────────────────────────────────
     The poll must not rebuild the chip while a pointer or keyboard focus is
     on it: replacing #devVol mid-drag kills the drag and snaps the value
     back to the last polled level. While the chip is busy the fresh status
     is parked and only the read-only ▶ line is updated in place; the parked
     render lands on pointerup/focusout. The markup memo below also skips
     the rebuild entirely when nothing visible changed (G4) — on a kiosk
     left open all night that is every poll. */
  let pointerOnChip = false;
  let pendingStatus: Status | null = null;
  let lastMarkup = "";
  const chipBusy = (): boolean => {
    // The seeking placeholder has no control worth preserving — first
    // contact must replace it even though its Retry button holds focus.
    // (By the button, not the class: render() strips `seeking` before it
    // asks, so the class is already gone when this runs.)
    const a = document.activeElement;
    if (a !== null && a.id === "devRetry") return false;
    return pointerOnChip || chip.contains(a);
  };
  const flushPending = (): void => {
    if (pendingStatus && !chipBusy()) {
      const s = pendingStatus;
      pendingStatus = null;
      render(s);
    }
  };
  chip.addEventListener("pointerdown", () => { pointerOnChip = true; });
  window.addEventListener("pointerup", () => {
    pointerOnChip = false;
    flushPending();
  });
  // focusout fires before the new focus target is set; defer the check.
  chip.addEventListener("focusout", () => setTimeout(flushPending, 0));

  // True while the last poll answered. The masthead must not flip back to
  // a healthy line just because ♪ or the mirror box was toggled while the
  // castle is down (pass 1, J1-4) — the last GOOD status is not the truth.
  let lastOk = true;
  let lastSeen: Date | null = null;
  let lastPlaying = "idle";

  /** POST, toast, then re-poll — the chip shows what the click did about a
   *  second later (queued action + main-loop tick) instead of after 15 s. */
  const act = (path: string, okMsg: string, quiet = false): void => {
    void castleAct(path, okMsg, { quiet });
  };
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
      // Unannounced = merely enforcing the remembered route at first
      // contact: the POST goes, the toast does not — on every page open
      // "castle speaker off" read like something had just happened (J3-3).
      act("/api/volume?v=0",
          announce ? "sound: Mac — castle speaker off" : "castle speaker off",
          !announce);
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
    chip.classList.add("live");
    chip.classList.remove("down", "seeking");
    const wasDown = !lastOk;
    lastOk = true;
    lastVol = s.volume ?? lastVol;
    lastStatus = s;
    sayStatus(s);
    opts.onCard?.(s.sd_total_kb || null);
    const running = s.scene && s.scene !== "stop" ? s.scene : "";
    if (followed === null) followed = running;          // firstContact adopts
    else if (opts.follow && running !== followed) {
      followed = running;
      opts.adoptScene?.(running);
    }
    // Back from the dead: the open panel was showing "stopped answering".
    if (wasDown) panel.refresh();
    lastSeen = new Date();
    lastPlaying = nowLine(s);
    opts.onScenes?.(s.scenes === undefined
      ? null : s.scenes.split(",").filter(Boolean));
    const markup = chipHtml(s, lastVol, mirror);
    if (chipBusy()) {
      // A hand or focus is on the chip: park the render, refresh only the
      // read-only now-playing line so the words stay honest.
      pendingStatus = s;
      const nowEl = chip.querySelector<HTMLElement>("#devNow");
      if (nowEl) { nowEl.textContent = nowLine(s); nowEl.title = ""; }
      syncRouteUI();
      return;
    }
    if (markup === lastMarkup) {
      // Nothing visible changed (G4) — but the down-state may have written
      // "last seen …" into #devNow directly, so restore the live line.
      const nowEl = chip.querySelector<HTMLElement>("#devNow");
      if (nowEl && wasDown) { nowEl.textContent = nowLine(s); nowEl.title = ""; }
      syncRouteUI();
      afterRender(s);
      return;
    }
    lastMarkup = markup;
    chip.innerHTML = markup;
    wireChip(chip, {
      mirror: (on) => {
        mirror = on;                 // the masthead says whether picks reach the porch
        if (lastStatus) sayStatus(lastStatus);
      },
      stop: () => act("/api/stop", "stop"),
      more: () => panel.toggle(),
      route: () => applyRoute(soundRoute === "mac" ? "castle" : "mac", true),
      volume: (v) => act(`/api/volume?v=${v}`, `volume ${v}`),
      mute: (vol) => {
        // Mute is volume 0 with memory — the device has no separate flag.
        const to = Number(vol.value) === 0 ? (lastVol || 70) : 0;
        if (to === 0) lastVol = Number(vol.value);
        vol.value = String(to);
        act(`/api/volume?v=${to}`, to === 0 ? "muted" : `volume ${to}`);
      },
    });

    afterRender(s);
  };

  /** Render's tail, shared with the skipped-rebuild path: first-contact
   *  route enforcement plus the controls' enabled state. */
  const afterRender = (s: Status): void => {
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
      // Back after a reboot: the castle forgot the hush (speaker_hush is
      // not persisted) and its boot scene set the amp to its own level.
      // The route is this desk's decision, so the desk restates it.
      const back = !lastOk;
      render(now);
      if (back && soundRoute === "mac") act("/api/volume?v=0", "castle speaker off", true);
      setCastleLive(true);
      return;
    }
    // A castle busy swallowing a multi-MB send is not a castle that left:
    // its one httpd task answers the poll when the bytes are down.
    if (isCastleBusy()) return;
    chip.classList.add("down");      // text dims; the ground stays opaque (J3-2)
    lastOk = false;
    if (lastStatus) sayStatus(lastStatus);
    opts.onCard?.(null);
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
    // A follower meeting an IDLE porch is told so: the kiosk otherwise sat
    // on the default scene, paused at frame 0, until the castle had been
    // seen playing once (judge B, JB2-5b). The desk keeps its default.
    else if (opts.follow) opts.adoptScene?.("");
    // A slow poll keeps the chip honest (version after an OTA, card pulled,
    // a scene the PIR fired while nobody was looking); actions re-poll
    // themselves sooner via castleAct().
    setInterval(() => void refresh(), opts.pollMs ?? POLL_MS);
  }

  /** C3: after three missed probes, an EXPECTED castle stops being a blank
   *  box — the chip says which host it is trying and offers a retry now.
   *  Only when the studio names a configured castle (expectedCastle): a
   *  desk opened from disk, or a studio with no castle set, is a simulator
   *  on purpose and stays quiet. */
  function seeking(): void {
    if (live || !expectedCastle) return;
    chip.classList.add("seeking");
    chip.innerHTML =
      `<div>🏰 looking for the castle… <small class="chip__seek">` +
      `no answer from ${esc(expectedCastle)} — retrying every ` +
      `${RETRY_MS / 1000} s</small></div>` +
      `<button id="devRetry" class="chip__btn" type="button" ` +
      `title="Probe the castle again right now">Retry</button>`;
    chip.querySelector<HTMLButtonElement>("#devRetry")!
      .addEventListener("click", () =>

        void probe().then((now) => {
          if (now !== null && !live) firstContact(now);
        }));
  }

  void probe().then((s) => {
    if (s !== null) { firstContact(s); return; }
    let misses = 1;
    const retry = window.setInterval(() => {
      if (live) { clearInterval(retry); return; }
      void probe().then((now) => {
        if (now !== null && !live) { clearInterval(retry); firstContact(now); }
        else if (now === null && !live && ++misses === 3) seeking();
      });
    }, RETRY_MS);
  });

  return {
    scene(id: string): void {
      if (!live || !mirror) return;
      act(`/api/scene?s=${encodeURIComponent(id)}`, `scene ${id}`);
    },
    stop(): void {
      if (!live || !mirror) return;
      act("/api/stop", "stop");
    },
  };
}
