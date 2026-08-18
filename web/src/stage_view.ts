/**
 * Castle / Pixels / Both — which of the two stage renders is on screen.
 *
 * They answer different questions and neither replaces the other. The castle
 * canvas is honest about the LOOK: glow, spill, bloom, what a person on the
 * pavement sees. The jewel row (insets.ts) is honest about the DATA: 21 dots,
 * one per real pixel, which is the only place per-pixel work — roles, chases,
 * sparkles, scattered strikes — can actually be reviewed.
 *
 * Showing both costs vertical space that a laptop does not always have, so
 * this is a choice rather than a layout. It is CSS-only on purpose: both
 * canvases keep drawing, so switching back is instant and nothing has to know
 * the mode exists. The choice survives a reload, like the trim sliders.
 */

const KEY = "castle.stageView";
const MODES = ["castle", "pixels", "both"] as const;
type Mode = (typeof MODES)[number];

const isMode = (v: string | null): v is Mode =>
  v !== null && (MODES as readonly string[]).includes(v);

export function initStageView(): void {
  const sel = document.getElementById("viewSel");
  if (!sel) return;                  // kiosk or a stripped page: nothing to bind

  let mode: Mode = "both";
  try {
    const saved = localStorage.getItem(KEY);
    if (isMode(saved)) mode = saved;
  } catch { /* private mode: session-only */ }

  const apply = (): void => {
    for (const m of MODES) document.body.classList.toggle(`view-${m}`, m === mode);
    sel.querySelectorAll<HTMLElement>("button").forEach(b => {
      b.setAttribute("aria-pressed", String(b.dataset["view"] === mode));
    });
  };

  sel.addEventListener("click", e => {
    const to = (e.target as HTMLElement | null)
      ?.closest<HTMLElement>("button")?.dataset["view"];
    if (!isMode(to ?? null)) return;
    mode = to as Mode;
    try { localStorage.setItem(KEY, mode); } catch { /* fine */ }
    apply();
  });

  apply();
}
