/**
 * What the desk is doing, as one value.
 *
 * Six module-level flags used to say this between them — which sound
 * source, whether the players were built yet, whether a castle scene was
 * being adopted, the clip's preview scene, the scene an audition displaced,
 * the selected track — and the rules that tied them together lived in
 * main.ts's callbacks, where nothing could test them. Now the rules are
 * `transition()`: a pure function from (mode, event) to (mode, scene to
 * load), exercised in web/test/desk_mode.ts, and main.ts keeps one `mode`.
 *
 * The `phase` is the discriminated union; the rest is what every phase
 * needs at hand. The players ride along because "not built yet" is a real
 * state of the desk (they are constructed after the transport that stops
 * them), and carrying them here keeps every access typed against the null.
 */

import type { Scene } from "./types.js";

export type Source = "rendered" | "synth";

/** What the stage is showing, and why. */
export type Phase =
  /** The loaded scene — the desk's own business. */
  | { kind: "show" }
  /** Mid-click, adopting the castle's running scene: the pick must not
   *  mirror back, or the porch restarts a scene it is already playing. */
  | { kind: "adopting" }
  /** A clip's lights, built from its own onsets; `before` is what was
   *  loaded when the audition started and comes back when it stops. */
  | { kind: "audition"; before: Scene };

export interface DeskMode<P = unknown> {
  readonly source: Source;
  readonly phase: Phase;
  /** The scene derived from the clip selection; null with no clip. */
  readonly preview: Scene | null;
  /** The track the clip editor is showing; null when nothing is picked. */
  readonly track: string | null;
  /** The sound-making modules, once they exist. */
  readonly players: P | null;
}

export type DeskEvent<P = unknown> =
  | { type: "source"; source: Source }
  | { type: "ready"; players: P }
  | { type: "adopt-start" }
  | { type: "adopt-end" }
  /** The clip selection changed (or went away). */
  | { type: "clip"; preview: Scene | null }
  /** The audition element started; `current` is what the stage shows now. */
  | { type: "audition-start"; current: Scene }
  | { type: "audition-stop" }
  | { type: "select"; track: string | null };

export interface Transition<P = unknown> {
  mode: DeskMode<P>;
  /** The scene the transport must load as a consequence, if any. */
  load?: Scene;
}

export function initialMode<P>(source: Source): DeskMode<P> {
  return { source, phase: { kind: "show" }, preview: null, track: null, players: null };
}

export function transition<P>(m: DeskMode<P>, e: DeskEvent<P>): Transition<P> {
  switch (e.type) {
    case "source":
      return { mode: { ...m, source: e.source } };
    case "ready":
      return { mode: { ...m, players: e.players } };
    case "adopt-start":
      return { mode: { ...m, phase: { kind: "adopting" } } };
    case "adopt-end":
      return { mode: { ...m, phase: { kind: "show" } } };
    case "select":
      return { mode: { ...m, track: e.track } };
    case "clip": {
      const mode = { ...m, preview: e.preview };
      // Already auditioning: adopt the new selection without stopping.
      return m.phase.kind === "audition" && e.preview
        ? { mode, load: e.preview } : { mode };
    }
    case "audition-start":
      // Starting with no clip scene keeps the show's lights; an audition
      // already running keeps its own `before`.
      if (m.phase.kind === "audition" || !m.preview) return { mode: m };
      return { mode: { ...m, phase: { kind: "audition", before: e.current } },
               load: m.preview };
    case "audition-stop":
      if (m.phase.kind !== "audition") return { mode: m };
      return { mode: { ...m, phase: { kind: "show" } }, load: m.phase.before };
  }
}

/** Something other than the loaded scene is on the stage, so the frame
 *  loop must keep painting even when the show is stopped. */
export const auditioning = (m: DeskMode<unknown>): boolean =>
  m.phase.kind === "audition";
