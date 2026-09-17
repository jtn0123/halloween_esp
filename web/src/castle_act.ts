/**
 * One castle action, and the toasts that answer for it — split out of
 * device.ts (500-line cap) on the seam "talking to the castle" vs "knowing
 * whether one is there". Everything here is stateless beyond the toast
 * column and the debounced re-poll timer; the probe, the route and the
 * chip's own state stay in device.ts.
 */

import { api } from "./api.js";
import { castleChanged } from "./castle_bus.js";
import { el as byId } from "./dom.js";

/** Where toasts stack: one fixed column above the dock, newest at the
 *  bottom. Two toasts a second apart used to print on the same pixels
 *  (J2-3); now they stack, identical text is not repeated while it is still
 *  showing, and only the last few stay on screen. */
const TOAST_MAX = 3;
function toastHost(): HTMLDivElement {
  let host = byId<HTMLDivElement>("toasts");
  if (!host) {
    host = document.createElement("div");
    host.id = "toasts";              // styled in previewer/panels.css
    // A live region: a screen reader hears "scene failed — …" the way a
    // sighted operator sees it (grade report 2026-08-21 C1). Polite for the host;
    // each error toast is its own role="alert" so it interrupts.
    host.setAttribute("role", "status");
    host.setAttribute("aria-live", "polite");
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
  el.className = isError ? "toast toast--err" : "toast";
  if (isError) el.setAttribute("role", "alert");
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
    r = await api.castleAction(path, opts.method ?? "POST");
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
