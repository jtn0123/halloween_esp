/**
 * The Tracks panel's status line, with room for more than one thing at a
 * time.
 *
 * `#trkNote` used to be a single string, and every operation wrote it: the
 * "Writing scene … about 3s left" ticker and "Importing — downloading 40%"
 * took turns overwriting each other (judge B, JB1-10). Now there is one
 * headline — the last thing said — and one line per operation still in
 * flight, each keyed so it can update itself and disappear when done.
 * Specs keep reading `#trkNote`'s text; they just see all of it.
 */

export interface TrackStatus {
  /** The headline. `err` colours it. Leaves in-flight lines alone. */
  say(msg: string, err?: boolean): void;
  /** The headline plus a "Reload the desk" button (a result the baked-in
   *  page cannot show until it is rebuilt). */
  sayReload(msg: string): void;
  /** A writer for one in-flight operation's own line. */
  slot(key: string): (msg: string) => void;
  /** That operation is over — its line goes. */
  clear(key: string): void;
}

export function createStatus(host: HTMLElement): TrackStatus {
  const head = document.createElement("span");
  head.className = "trk-note__head";
  const ops = document.createElement("span");
  ops.className = "trk-note__ops";
  host.replaceChildren(head, ops);
  const lines = new Map<string, HTMLElement>();

  const say = (msg: string, err = false): void => {
    head.textContent = msg;
    host.classList.toggle("err", err);
  };

  return {
    say,
    sayReload(msg: string): void {
      say(msg + " ");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "trk-reload";
      b.textContent = "Reload the desk";
      b.addEventListener("click", () => location.reload());
      head.append(b);
    },
    slot(key: string): (msg: string) => void {
      return (msg: string): void => {
        let el = lines.get(key);
        if (!el) {
          el = document.createElement("span");
          el.className = "trk-note__op";
          el.dataset["op"] = key;
          el.style.display = "block";
          lines.set(key, el);
          ops.append(el);
        }
        el.textContent = msg;
      };
    },
    clear(key: string): void {
      lines.get(key)?.remove();
      lines.delete(key);
    },
  };
}
