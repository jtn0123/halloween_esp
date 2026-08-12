/**
 * Listening to an imported track.
 *
 * The clip editor could already audition a *region* — but only once you knew
 * the row was clickable and the editor existed. There was no way to answer the
 * first question anyone actually has after an import: "is this the right
 * track?" This is that, one element playing one file, driven by a button that
 * sits on the row itself.
 *
 * It is a separate module from tracks.ts for the same reason onsets.ts is:
 * the 500-line ceiling and a seam that happens to be clean. Nothing in here
 * touches the DOM beyond the audio element it owns.
 *
 * Muted by default, like every other thing in this app that can make noise.
 * The element is created muted, is unmuted only inside `play()` — which only
 * ever runs from a click — and is put straight back on every stop. No later
 * code path can leave it able to speak on its own.
 */

export interface PreviewDeps {
  /** Fired whenever the sounding track changes, including to nothing. */
  onChange: (playingId: string | null) => void;
  /** A track that would not load or play. The id is included in the message. */
  onError: (message: string) => void;
  /**
   * Fired just before sound starts, so the host can silence whatever else it
   * owns. Two things playing at once is never what was meant.
   */
  onClaim?: () => void;
}

export interface TrackPreview {
  /** Play `id` from the top. Playing the sounding track stops it instead. */
  toggle: (id: string) => void;
  /** Stop. Safe when nothing is playing. */
  stop: () => void;
  /** The id currently sounding, or null. */
  playing: () => string | null;
  /** Drop the element and any pending playback. */
  destroy: () => void;
}

/** Loud enough to judge a track, quiet enough not to be a surprise. */
const VOLUME = 0.7;

export function createPreview(deps: PreviewDeps): TrackPreview {
  const audio = new Audio();
  audio.preload = "none";
  audio.muted = true;

  let current: string | null = null;
  /**
   * Bumped on every start and stop, so a `play()` promise that loses the race
   * cannot speak for the one that replaced it.
   *
   * `play()` is asynchronous, and pausing an element whose play is still in
   * flight rejects that promise with AbortError. Without this counter the
   * rejection ran the failure path — stopping playback and reporting an error
   * — for a request that had already been superseded, so clicking one track's
   * Play straight after another's silenced both and blamed the second. It
   * showed up as a one-in-three failure of the browser suite's
   * "only one track plays at a time", which is exactly what it broke.
   */
  let epoch = 0;

  const settle = (id: string | null): void => {
    current = id;
    deps.onChange(id);
  };

  function stop(): void {
    epoch++;
    audio.pause();
    audio.muted = true;
    if (current !== null) settle(null);
  }

  function toggle(id: string): void {
    if (current === id) return stop();
    stop();
    const mine = ++epoch;
    deps.onClaim?.();
    // No extension: the studio owns which container this track landed in, so
    // a WAV or FLAC import plays here without the page knowing the difference.
    audio.src = `/api/track/${encodeURIComponent(id)}`;
    audio.currentTime = 0;
    audio.muted = false;                 // the click is the consent
    audio.volume = VOLUME;
    settle(id);
    void audio.play().catch(() => {
      if (mine !== epoch) return;      // a newer click already took over
      stop();
      deps.onError(`Could not play “${id}”.`);
    });
  }

  audio.addEventListener("ended", () => stop());
  audio.addEventListener("error", () => {
    // A src of "" fires this during teardown; only a real track is a failure.
    const id = current;
    if (id === null) return;
    stop();
    deps.onError(`Could not load audio for “${id}”.`);
  });

  return {
    toggle,
    stop,
    playing: () => current,
    destroy(): void {
      stop();
      audio.removeAttribute("src");
      audio.load();
    },
  };
}
