/**
 * Types for legacy_effects.mjs — the pre-migration reference the port is
 * checked against. The .mjs itself stays verbatim JavaScript on purpose
 * (its header says why); this sidecar gives the TypeScript tests typed
 * imports without touching a single line of the fixture.
 */

export interface LegacyParams {
  depth: number;
  speed: number;
  bright: number;
  hue: number;
  soft: boolean;
  stops: number;
}

/** (seconds, seed) → [r, g, b, w] in 0..1 (some effects return 3 channels). */
export type LegacyEffect = (t: number, s: number) => number[];

export const EFFECTS: Record<string, LegacyEffect>;
export const toScreen: (c: readonly number[]) => number[];
export const P: LegacyParams;
export const fbm: (x: number) => number;
export const vnoise: (x: number) => number;
export const hash: (n: number) => number;
