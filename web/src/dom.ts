/**
 * The three ways the desk reaches into its own page, by id.
 *
 * A leaf: nothing imported, so any module may use it without creating a
 * cycle. `el` when absence is an ordinary state (kiosk strips controls),
 * `req` when the page cannot run without the element — the throw names the
 * id, which beats the TypeError on `null.addEventListener` that a missing
 * element used to produce. `val` is the form read every option row does:
 * the input's value, trimmed, or "" when the input is not there.
 */

export const el = <T extends HTMLElement = HTMLElement>(id: string): T | null =>
  document.getElementById(id) as T | null;

export const input = (id: string): HTMLInputElement | null =>
  el<HTMLInputElement>(id);

export function req<T extends HTMLElement = HTMLElement>(id: string, who = "desk"): T {
  const e = el<T>(id);
  if (!e) throw new Error(`${who}: missing #${id}`);
  return e;
}

export const val = (id: string): string => input(id)?.value.trim() ?? "";

/**
 * The same two moods, one CSS selector deep instead of one id.
 *
 * Most of the desk's lookups are not by id at all: a panel that just wrote
 * its own innerHTML reaches back into that subtree by class. Those used to
 * be raw `root.querySelector(...)!` — the non-null assertion re-creating
 * exactly the TypeError `req` exists to prevent, only now with the selector
 * nowhere in the message (grade report 2026-08-31 C4/C5). `sel` when absence is an
 * ordinary state, `reqIn` when the panel cannot work without it.
 *
 * `root` is a ParentNode, so an element subtree, a DocumentFragment or the
 * document itself all work; it defaults to the document for the handful of
 * page-wide lookups that are genuinely not by id.
 */
export const sel = <T extends HTMLElement = HTMLElement>(
  css: string, root: ParentNode = document,
): T | null => root.querySelector<T>(css);

export function reqIn<T extends HTMLElement = HTMLElement>(
  root: ParentNode, css: string, who = "desk",
): T {
  const e = root.querySelector<T>(css);
  if (!e) throw new Error(`${who}: missing ${css}`);
  return e;
}

/**
 * The third mood: upwards, not downwards.
 *
 * A delegated click handler has the button and wants the row that owns it.
 * That was `btn.closest(".trk")!` — the same non-null assertion `reqIn`
 * exists to retire, and with the same failure: a TypeError somewhere later
 * that never mentions the selector nobody matched (grade report
 * 2026-09-01 C3). The throw names it, and names the element it started from,
 * because "no `.trk` above this button" is the whole diagnosis.
 */
export function closestIn<T extends HTMLElement = HTMLElement>(
  from: Element, css: string, who = "desk",
): T {
  const e = from.closest<T>(css);
  if (!e) throw new Error(`${who}: no ${css} above <${from.tagName.toLowerCase()}>`);
  return e;
}

/** Text into markup. Everything the desk splices into innerHTML that came
 *  from OUTSIDE it — a track name, a file name off the castle's card, the
 *  castle's own version string — goes through here first. A name is a name,
 *  not an element (web/test/e2e/castle_panel.spec.ts holds that line).
 *
 *  Lives in the leaf module because three unrelated panels need it and none
 *  of them should have to import another panel to get it.
 */
const ESCAPES: Record<string, string> =
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };

export const esc = (s: unknown): string =>
  String(s).replace(/[&<>"]/g, c => ESCAPES[c] as string);
