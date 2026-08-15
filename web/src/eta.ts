/**
 * Learned ETAs for the studio's slow jobs.
 *
 * "Working…" answers whether something is happening; it does not answer the
 * question actually being asked, which is "do I have time to get coffee".
 * Nothing here can know a job's duration a priori — it depends on this
 * machine — so instead every finished job teaches the next one: the seconds
 * it took (per unit of work, e.g. per second of audio for a Demucs split)
 * are folded into a running estimate kept in localStorage.
 *
 * The very first run of a kind honestly has no estimate and says elapsed
 * time instead. A run that blows past its estimate says so rather than
 * counting down into negative numbers.
 */

/** Half old, half new: adapts in a couple of runs without letting one
 *  outlier (a thermal-throttled split) rewrite history. */
const BLEND = 0.5;

/** localStorage when the browser allows it; private-mode and tests fall
 *  back to a Map, which simply forgets between sessions. */
const mem = new Map<string, string>();
const store = {
  get(k: string): string | null {
    try { return window.localStorage.getItem(k); }
    catch { return mem.get(k) ?? null; }
  },
  set(k: string, v: string): void {
    try { window.localStorage.setItem(k, v); }
    catch { mem.set(k, v); }
  },
};

const rateOf = (key: string): number | null => {
  const raw = store.get(`castleEta:${key}`);
  const n = raw === null ? NaN : Number(raw);
  return Number.isFinite(n) && n > 0 ? n : null;
};

const fmt = (s: number): string => {
  s = Math.max(1, Math.round(s));
  return s < 90 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
};

export interface EtaHandle {
  /** The status line for right now — base plus whatever time truth exists. */
  line(): string;
  /** Stop the ticker. `learn: true` on success folds this run's duration
   *  into the stored estimate; a failed run teaches nothing. Idempotent. */
  stop(learn?: boolean): void;
}

/**
 * Start timing a job and keep its status line current.
 *
 * `units` scales the estimate — pass the clip length for an encode, the
 * track length for a split, nothing for a fixed-shape job. `say` is called
 * once a second with the composed line; a caller that already polls (and
 * would fight the ticker for the status line) can pass `say: null` and
 * render `line()` itself.
 */
export function startEta(key: string, base: string,
                         say: ((msg: string) => void) | null,
                         units = 1): EtaHandle {
  const rate = rateOf(key);
  const t0 = Date.now();
  const estimate = rate === null ? null : rate * Math.max(units, 0.001);

  const line = (): string => {
    const elapsed = (Date.now() - t0) / 1000;
    if (estimate === null)
      return elapsed < 3 ? base
        : `${base} — ${fmt(elapsed)} so far (first run, learning how long this takes)`;
    const left = estimate - elapsed;
    return left > 1 ? `${base} — about ${fmt(left)} left`
      : `${base} — taking longer than usual (${fmt(elapsed)} so far)`;
  };

  let timer = say === null ? 0
    : window.setInterval(() => say(line()), 1000);
  if (say !== null) say(line());

  return {
    line,
    stop(learn = false): void {
      if (timer) { window.clearInterval(timer); timer = 0; }
      if (!learn) return;
      const seconds = (Date.now() - t0) / 1000;
      const fresh = seconds / Math.max(units, 0.001);
      const prev = rateOf(key);
      const next = prev === null ? fresh : prev * (1 - BLEND) + fresh * BLEND;
      store.set(`castleEta:${key}`, String(next));
    },
  };
}
