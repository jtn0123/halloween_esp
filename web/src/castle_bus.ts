/**
 * The one place the desk's castle-aware halves meet.
 *
 * device.ts (the chip, the masthead line, the probe) and the track library
 * (badges, card-only rows, Sync — track_card.ts / track_send.ts) each used
 * to keep their own idea of whether a castle was listening, and they
 * disagreed in exactly the ways an operator notices: the library grew
 * "→ Castle" buttons while the masthead still said simulator, a card-row
 * Play never nudged the chip off "idle", and a poll that landed mid-upload
 * flipped the masthead to "castle not answering" in the middle of a Sync
 * that was going fine (pass-1 findings J1-3/J1-7/J1-8).
 *
 * Three tiny signals, no DOM, no fetch — so either side can import this
 * without dragging the other in:
 *   - presence: device.ts says when the castle is (not) answering; the
 *     library re-reads the card the moment that changes instead of waiting
 *     out its own slow poll.
 *   - changed: anything that just POSTed/PUT/DELETEd says so; device.ts
 *     re-polls status about a second later so the chip shows the result.
 *   - busy: a send in flight counts as liveness — a castle that is busy
 *     taking bytes is not a castle that went away.
 */

type Fn = () => void;
const presenceFns = new Set<(live: boolean) => void>();
const changedFns = new Set<Fn>();
let live = false;
let busy = 0;

/** Is a castle answering right now, as far as the probe knows. */
export const castleLive = (): boolean => live;

/** device.ts calls this on first contact, on loss, and on recovery. */
export function setCastleLive(now: boolean): void {
  if (now === live) return;
  live = now;
  for (const f of presenceFns) f(now);
}

export function onCastlePresence(f: (live: boolean) => void): void {
  presenceFns.add(f);
}

/** "I just changed something on the castle" — the chip should re-poll. */
export function castleChanged(): void {
  for (const f of changedFns) f();
}

export function onCastleChanged(f: Fn): void {
  changedFns.add(f);
}

/** Count a long transfer as liveness while it runs. */
export function castleBusy<T>(p: Promise<T>): Promise<T> {
  busy++;
  return p.finally(() => { busy--; });
}

export const isCastleBusy = (): boolean => busy > 0;
