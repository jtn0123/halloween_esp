/**
 * The merged library view: one list that answers "what is WHERE".
 *
 * Local tracks get their badge in track_rows.ts. This module owns the other
 * half of the reconciliation (dogfood 006): files that exist only on the
 * castle's SD card appear as rows of their own at the bottom of the same
 * list — playable and deletable in place — instead of hiding behind the
 * corner chip. And it owns the Sync button: one press sends every track the
 * show needs that the card does not have, with per-file progress.
 */

import { onCardChanged, onCastlePresence } from "./castle_bus.js";
import { esc } from "./track_rows.js";
import { cardName, cardState, fetchCard, sendable, sendToCastle } from "./track_send.js";
import { castleAct, failReason } from "./device.js";
import type { TrackInfo } from "./tracks.js";

/** What both renderers need to know about the two libraries. */
export interface CardCtx {
  tracks: readonly TrackInfo[];
  sceneIds: ReadonlySet<string>;
  /** name → bytes on the card; null = no castle answering. */
  card: ReadonlyMap<string, number> | null;
  /** Studio mode: pulling a card file INTO the Mac library is possible. */
  canPull: boolean;
}

const AUDIO = /\.(mp3|wav|flac|opus)$/i;

/** Card files with no local twin, i.e. the rows only this module shows. */
export function cardOnly(ctx: CardCtx): { name: string; size: number }[] {
  if (!ctx.card) return [];
  const local = new Set(ctx.tracks.map(cardName));
  return [...ctx.card.entries()]
    .filter(([n]) => AUDIO.test(n) && !local.has(n))
    .map(([name, size]) => ({ name, size }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** In-show tracks the card is missing OR holding stale — what Sync sends.
 *  Stale counts: a re-imported track whose old bytes still sit on the card
 *  would play the wrong version on Halloween. */
export function syncPlan(ctx: CardCtx): TrackInfo[] {
  if (!ctx.card) return [];
  const card = ctx.card;
  return ctx.tracks.filter(t =>
    ctx.sceneIds.has(t.id) && sendable(t) && cardState(t, card) !== "current");
}

/** Rows for the card-only files; "" when there are none to show. */
export function cardRowsHtml(ctx: CardCtx): string {
  const rows = cardOnly(ctx);
  if (rows.length === 0) return "";
  return `<div class="trk-cardhd" title="Files on the castle's SD card that `
    + `have no copy in this library">On the castle's card only</div>`
    + rows.map(f => `
  <div class="trk trk--card" data-card="${esc(f.name)}">
    <div class="trk__nm">🏰 ${esc(f.name)}
      <small>${(f.size / 1024) | 0} KB · castle only</small>
    </div>
    <div class="trk__act">
      <button data-cardact="play" title="Play this file on the castle's speaker">Play on castle</button>
      ${ctx.canPull ? `<button data-cardact="pull" title="Copy this file off the card into the Mac library — it gets imported and analysed like any drop">⬇ to Mac</button>` : ""}
      <button data-cardact="del" class="danger" title="Delete from the SD card">Delete</button>
    </div>
  </div>`).join("");
}

/** Keep the Sync button telling the truth. Call on every list redraw. */
export function renderSyncButton(btn: HTMLButtonElement, ctx: CardCtx): void {
  if (ctx.card === null) {           // no castle: the button would only lie
    btn.hidden = true;
    return;
  }
  const missing = syncPlan(ctx);
  btn.hidden = false;
  btn.disabled = missing.length === 0;
  btn.textContent = missing.length === 0
    ? "show is on the card ✓"
    : `Sync show → castle (${missing.length})`;
  btn.title = missing.length === 0
    ? "Every track the show uses is already on the castle's SD card"
    : `Send to the castle's card: ${missing.map(t => t.id).join(", ")}`;
}

/** The row's "→ Castle" button, with live percent while the bytes move. */
export async function sendAction(btn: HTMLButtonElement, t: TrackInfo,
                                 say: (m: string, err?: boolean) => void,
                                 reloadCard: () => Promise<void>): Promise<void> {
  btn.disabled = true;
  const res = await sendToCastle(t, pct => { btn.textContent = `sending ${pct}%`; });
  say(res.msg, !res.ok);
  await reloadCard();               // redraw fixes the label either way
}

/** Keep the card view honest over time: a castle that comes online after
 *  page load, or files dropped through the panel, show up within a poll.
 *  Applies (and so redraws) ONLY on a real change — an unconditional
 *  redraw every tick would eat clicks mid-flight (the suite's old flake). */
export function watchCard(deps: {
  active: () => boolean;
  card: () => ReadonlyMap<string, number> | null;
  apply: (card: Map<string, number> | null) => void;
}, intervalMs = 20_000): void {
  const key = (m: ReadonlyMap<string, number> | null): string =>
    m === null ? "-" : JSON.stringify([...m.entries()].sort());
  const poll = (): void => {
    if (!deps.active()) return;
    void fetchCard().then(now => {
      if (key(now) !== key(deps.card())) deps.apply(now);
    });
  };
  window.setInterval(poll, intervalMs);
  // The chip's probe and this list must agree about whether a castle is
  // there: the moment device.ts sees it arrive or leave, re-read the card
  // rather than letting the two halves disagree for up to a poll (J1-3).
  onCastlePresence(() => poll());
  // …and a file dropped or deleted through the castle panel is a card
  // change the list must show now, not at the next poll.
  onCardChanged(() => poll());
}

export interface CardActionDeps {
  list: HTMLElement;
  syncBtn: HTMLButtonElement;
  ctx: () => CardCtx;
  say: (m: string, err?: boolean) => void;
  reloadCard: () => Promise<void>;
  /** The tracks panel's own import path — what ⬇ to Mac hands the file to. */
  importFile?: (f: File) => Promise<void>;
}

/** One-time wiring for the Sync button and the card-only rows. */
export function wireCardActions(deps: CardActionDeps): void {
  deps.syncBtn.addEventListener("click", async () => {
    const plan = syncPlan(deps.ctx());
    if (plan.length === 0) return;
    deps.syncBtn.disabled = true;
    let sent = 0;
    for (const [i, t] of plan.entries()) {
      const label = `Sending ${i + 1}/${plan.length}: ${t.id}`;
      deps.say(`${label}…`);
      const res = await sendToCastle(t, pct => deps.say(`${label} — ${pct}%`));
      if (!res.ok) {           // say why and stop; a silent half-sync is worse
        deps.say(res.msg, true);
        await deps.reloadCard();
        return;
      }
      sent++;
    }
    deps.say(`Show synced — ${sent} track${sent === 1 ? "" : "s"} sent to the castle.`);
    await deps.reloadCard();
  });

  deps.list.addEventListener("click", async (e) => {
    const btn = (e.target as HTMLElement | null)?.closest<HTMLButtonElement>("[data-cardact]");
    if (!btn) return;
    const name = btn.closest<HTMLElement>(".trk")?.dataset["card"] ?? "";
    if (!name) return;
    if (btn.dataset["cardact"] === "play") {
      // Through castleAct: the chip re-polls, so it says ▶ not "idle".
      await castleAct(`/api/play?f=${encodeURIComponent(name)}`,
                      `playing ${name} on the castle`);
    } else if (btn.dataset["cardact"] === "pull" && deps.importFile) {
      btn.disabled = true;
      btn.textContent = "pulling…";
      const r = await fetch(`/api/card/${encodeURIComponent(name)}`)
        .catch(() => null);
      if (!r?.ok) {
        deps.say(`Could not pull ${name} off the card — `
          + `${r ? await failReason(r) : "no answer from the castle"}.`, true);
        btn.disabled = false;
        btn.textContent = "⬇ to Mac";
        return;
      }
      // Through the normal import gate: converted, analysed, remembered —
      // a pulled file is a first-class track, not a stray copy.
      await deps.importFile(new File([await r.blob()], name));
      await deps.reloadCard();
    } else if (btn.dataset["cardact"] === "del") {
      if (!confirm(`Delete ${name} from the castle's SD card?`)) return;
      // castleAct: the reason on failure (404 "no such file" vs a dead
      // castle read the same before), the chip re-poll on success.
      const ok = await castleAct(`/api/files/${encodeURIComponent(name)}`,
                                 `deleted ${name} from the card`, { method: "DELETE" });
      deps.say(ok ? `Deleted ${name} from the card.`
                  : `Could not delete ${name} — see the message by the castle chip.`, !ok);
      await deps.reloadCard();
    }
  });
}

/** Everything the Tracks panel hands the card half, in one call: the
 *  first read, the action wiring, the presence/poll watcher. Returns the
 *  reload function so the panel's own handlers (→ Castle) can use it. */
export function mountCard(deps: Omit<CardActionDeps, "reloadCard"> & {
  active: () => boolean;
  apply: (card: Map<string, number> | null) => void;
}): () => Promise<void> {
  const reloadCard = async (): Promise<void> => deps.apply(await fetchCard());
  void reloadCard();
  wireCardActions({ ...deps, reloadCard });
  watchCard({ active: deps.active, card: () => deps.ctx().card, apply: deps.apply });
  return reloadCard;
}
