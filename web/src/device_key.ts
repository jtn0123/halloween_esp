/**
 * The castle key (firmware v5.74) — the 🏰 panel's "settings" section, which
 * is where KEY_REQUIRED ("enter it in Settings") sends the operator.
 *
 * A castle with a key refuses changes — card writes and deletes, motion
 * settings, updates — from anything that does not send it. The desk never
 * holds the key: it reaches the castle through the studio's relay, and the
 * studio sends the key its store remembers for that castle (docs/notes/
 * 06-buyer-build.md, "The castle key's one store"). So this section only
 * hands a typed key to the studio, once, and empties the field:
 *
 *   use    — the studio checks the key against the castle, then remembers it
 *   set    — the castle's key becomes the typed one (sent with the held key)
 *   clear  — the castle has no key any more; the studio forgets it
 *
 * The answers (core/src/studio_key.rs) say whether a key is remembered and
 * whether CASTLE_KEY pins it — never the key — and neither does this page.
 * It is shown only through the studio (`bridged`): Castle Radio's own card
 * (demo/castle-radio/castle-key.js) is the castle-served page's.
 */

import { api, type CastleKeyAction, type CastleKeyState } from "./api.js";
import { toast } from "./castle_act.js";
import { reqIn } from "./dom.js";
import type { DeviceStatus } from "./device_panel_view.js";
import { sectionHead } from "./device_tests.js";

const DONE: Record<CastleKeyAction, string> = {
  use: "Key accepted · remembered for this castle",
  set: "The castle has its new key · remembered for this castle",
  clear: "The castle has no key now",
};

/** What the castle's own status says about its lock. */
function lockLine(st: DeviceStatus): string {
  if (st.locked === true) return "This castle has a key.";
  if (st.locked === false) return "This castle has no key.";
  return "This castle's firmware has no key (v5.74 and newer do).";
}

/** What the studio's store says, after the castle's own line. */
export function describeKey(st: DeviceStatus, k: CastleKeyState): string {
  if (k.error) return k.error;
  if (k.pinned) {
    return "This castle's key is set in the app's settings file (castle_key) · change it there";
  }
  return `${lockLine(st)} ${k.remembered
    ? "A key is remembered for it on this computer."
    : "No key is remembered for it on this computer."}`;
}

/** The section's markup: nothing unless the studio is the one relaying. */
export function keyMarkup(st: DeviceStatus): string {
  if (!st.bridged) return "";
  return `<div class="dp__sec">` +
    sectionHead("🔑", "settings", "the castle key — who may change this castle") +
    `<div id="dpKeyState" class="dp__note dp__note--tight">${lockLine(st)}</div>` +
    `<div class="dp__row dp__row--tight">` +
    `<input id="dpKey" class="dp__key" type="password" autocomplete="off" ` +
    `spellcheck="false" maxlength="64" placeholder="castle key" aria-label="Castle key" ` +
    `title="1-64 printable characters, no spaces">` +
    `<button id="dpKeyUse" class="dp__btn" title="Check this key against the castle, ` +
    `then remember it on this computer">use</button>` +
    `<button id="dpKeySet" class="dp__ghost" title="Make this the castle's new key ` +
    `(sent with the one remembered now)">set new</button>` +
    `<button id="dpKeyClear" class="dp__ghost" title="Take the castle's key away — ` +
    `anything on the network can change it again">clear</button>` +
    `</div>` +
    `<div id="dpKeyMsg" class="dp__note dp__note--tight" role="status" aria-live="polite"></div>` +
    `</div>`;
}

/** Wire the section keyMarkup drew into `body`, if it drew one. */
export function wireKey(body: HTMLElement, st: DeviceStatus): void {
  const field = body.querySelector<HTMLInputElement>("#dpKey");
  if (!field) return;
  const state = reqIn<HTMLDivElement>(body, "#dpKeyState");
  const msg = reqIn<HTMLDivElement>(body, "#dpKeyMsg");
  void api.castleKey()
    .then((k) => { state.textContent = describeKey(st, k); })
    .catch(() => { /* the lock line stands */ });

  const act = async (action: CastleKeyAction): Promise<void> => {
    const key = field.value.trim();
    // Only the clear needs no key: the one remembered is what the castle gets.
    if (action !== "clear" && !key) { msg.textContent = "Type the key first"; return; }
    msg.textContent = "Asking the castle…";
    field.value = "";
    try {
      const k = await api.castleKeyAct(action, key);
      if (k.error) {
        msg.textContent = k.error;
        toast(`castle key — ${k.error}`, true);
        return;
      }
      // A set or a clear just changed the lock: say the new truth, not the
      // last poll's.
      const now = action === "use" ? st : { ...st, locked: action === "set" };
      state.textContent = describeKey(now, k);
      msg.textContent = DONE[action];
      toast(DONE[action]);
    } catch {
      msg.textContent = "the studio is not answering";
    }
  };
  reqIn<HTMLButtonElement>(body, "#dpKeyUse").addEventListener("click", () => void act("use"));
  reqIn<HTMLButtonElement>(body, "#dpKeySet").addEventListener("click", () => void act("set"));
  reqIn<HTMLButtonElement>(body, "#dpKeyClear").addEventListener("click", () => void act("clear"));
}
