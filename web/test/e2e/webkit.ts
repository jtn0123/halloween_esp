/**
 * What the suite needs from a browser that is not Chromium: two init
 * scripts fixtures.ts adds to every WebKit context. Each is serialised into
 * the page, so neither may reach outside its own body.
 */

/**
 * Silence for a browser that has no --mute-audio.
 *
 * The suite's first rule is that a run never makes a sound (see
 * playwright.config.ts). Chromium enforces it below the page with
 * --mute-audio; WebKit has no such switch, so for WebKit this init script
 * holds the OUTPUT at zero from just under the page instead:
 *
 *   - a media element's real volume is 0 whenever it plays, while the page
 *     reads back exactly the volume it last set (the desk's fades read
 *     `el.volume` to take their next step);
 *   - every live AudioContext's `destination` is a zero gain in front of the
 *     real speakers. An OfflineAudioContext keeps its own: it renders to a
 *     buffer the page reads (onsets.ts), not to anyone's ears.
 *
 * `muted` and `paused` are never touched, so `sounding()` and `playing()`
 * (fixtures.ts) still ask about the app's own muting — the same separation
 * Chromium's flag gives.
 */
export function silenceOutput(): void {
  const media = HTMLMediaElement.prototype;
  const volume = Object.getOwnPropertyDescriptor(media, "volume");
  if (volume?.get && volume.set) {
    const realSet = volume.set;
    const wanted = new WeakMap<HTMLMediaElement, number>();
    Object.defineProperty(media, "volume", {
      configurable: true,
      enumerable: !!volume.enumerable,
      get(this: HTMLMediaElement) { return wanted.get(this) ?? 1; },
      set(this: HTMLMediaElement, v: number) {
        realSet.call(this, v); // an out-of-range value throws exactly as it would
        wanted.set(this, v);
        realSet.call(this, 0);
      },
    });
    const play = media.play;
    media.play = function (this: HTMLMediaElement) {
      realSet.call(this, 0);
      return play.call(this);
    };
    // An element that starts by itself (autoplay) never calls play().
    document.addEventListener("play", (e) => {
      if (e.target instanceof HTMLMediaElement) realSet.call(e.target, 0);
    }, true);
  }

  const Base = (window as { BaseAudioContext?: typeof BaseAudioContext }).BaseAudioContext;
  const dest = Base && Object.getOwnPropertyDescriptor(Base.prototype, "destination");
  if (Base && dest?.get) {
    const realDest = dest.get;
    const quiet = new WeakMap<BaseAudioContext, GainNode>();
    Object.defineProperty(Base.prototype, "destination", {
      configurable: true,
      enumerable: !!dest.enumerable,
      get(this: BaseAudioContext) {
        const speakers = realDest.call(this) as AudioDestinationNode;
        if (typeof OfflineAudioContext !== "undefined" && this instanceof OfflineAudioContext) {
          return speakers;
        }
        let gate = quiet.get(this);
        if (!gate) {
          gate = this.createGain();
          gate.gain.value = 0;
          gate.connect(speakers);
          quiet.set(this, gate);
        }
        return gate;
      },
    });
  }
}

/**
 * A Blob body's size, for a route that is not handed the body.
 *
 * WebKit's Playwright gives a route no body at all for a request whose body
 * is a Blob (an ArrayBuffer's it does): `postDataBuffer()` is null and no
 * Content-Length is listed. Every card upload the desk makes is a Blob
 * (track_send.ts, api.castlePut), and the fake castles answer with the byte
 * count they received — which the desk then verifies. So here each Blob
 * body also carries its size in the header `x-e2e-blob-bytes`, which
 * fixtures.ts `bodyBytes` reads when the body itself is missing. The bytes
 * on the wire and the page's own code are unchanged; Safari uploads the
 * same Blob.
 */
export function tellBlobSizes(): void {
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (
    this: XMLHttpRequest, body?: Document | XMLHttpRequestBodyInit | null,
  ) {
    if (body instanceof Blob) this.setRequestHeader("x-e2e-blob-bytes", String(body.size));
    return send.call(this, body);
  };
  const realFetch = window.fetch;
  window.fetch = function (input: RequestInfo | URL, init?: RequestInit) {
    if (init?.body instanceof Blob) {
      const headers = new Headers(init.headers);
      headers.set("x-e2e-blob-bytes", String(init.body.size));
      return realFetch.call(this, input, { ...init, headers });
    }
    return realFetch.call(this, input, init);
  };
}
