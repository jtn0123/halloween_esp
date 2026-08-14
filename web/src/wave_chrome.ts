/**
 * The clip editor's chrome — the buttons, title, readout and zone mirror.
 *
 * Split from waveform.ts for the 500-line cap; the seam is "what the editor
 * is made of" vs "what it does". Built here rather than in the template
 * because the template is generated and this panel is the only thing that
 * needs any of it. Layout is inline for the same reason: a handful of
 * rules, and no stylesheet to keep in step.
 */

export interface WaveChrome {
  wrap: HTMLDivElement;
  title: HTMLDivElement;
  row: HTMLDivElement;
  play: HTMLButtonElement;
  snap: HTMLButtonElement;
  readout: HTMLSpanElement;
  note: HTMLParagraphElement;
  mirrorDots: Partial<Record<string, HTMLElement>>;
}

export function buildWaveChrome(): WaveChrome {
  const mk = <K extends keyof HTMLElementTagNameMap>(
    tag: K, css: string, text = ""): HTMLElementTagNameMap[K] => {
    const n = document.createElement(tag);
    n.style.cssText = css;
    if (text) n.textContent = text;
    return n;
  };

  const wrap = mk("div", "margin:8px 0");
  // Which song this editor is on. Without it (round-1 user test) the only
  // clue was a thin border on a row that might be scrolled out of view, and
  // the wrong track got edited.
  const title = mk("div", "display:block;font:600 14px/1.4 var(--f-data),"
    + "ui-monospace,monospace;color:var(--ink-0,#fff);margin:0 0 8px");
  title.className = "wave__title";
  const row = mk("div", "display:flex;align-items:center;gap:12px;flex-wrap:wrap;"
    + "margin-top:6px;font:12px/1.6 var(--f-data),ui-monospace,monospace;color:var(--ink-2)");
  const play = mk("button", "min-width:7em", "Audition");
  play.type = "button";
  play.disabled = true;
  play.title = "Loop the selected region WITH its generated lights on the "
             + "stage above. (The row's Play button is the plain audio, "
             + "no lights.)";
  const readout = mk("span", "font-variant-numeric:tabular-nums");
  // A live mirror of the three zones, right next to Audition. "Judge the
  // lights" with the stage 1600px off-screen was judging from memory
  // (round-2 user test): these dots are the castle, at arm's reach.
  const mirror = mk("span", "display:inline-flex;align-items:center;gap:5px");
  mirror.title = "The stage's three zones, live — what the castle is doing "
               + "right now (left tower · door · right tower)";
  const mirrorDots: Partial<Record<string, HTMLElement>> = {};
  for (const z of ["towerL", "door", "towerR"]) {
    const d = mk("span", "width:14px;height:14px;border-radius:50%;"
      + "background:#000;border:1px solid var(--ink-3,#555)");
    d.className = `wave__zone-${z}`;
    mirrorDots[z] = d;
    mirror.append(d);
  }
  const snap = mk("button", "", "Snap to beat");
  snap.type = "button";
  snap.disabled = true;
  snap.title = "Nudge the clip's start/end onto the nearest detected beats, "
             + "so the loop does not click or cut a note at the seam";
  const note = mk("p", "margin:4px 0 0;color:var(--ink-2)");
  row.append(play, snap, mirror, readout);
  return { wrap, title, row, play, snap, readout, note, mirrorDots };
}
