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
