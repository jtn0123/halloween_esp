/**
 * Ports for the specs that spawn their own studio + emulator (bridge,
 * remote). Derived from the lane's CASTLE_E2E_PORT so two lanes — or a
 * stale run — land on different numbers by construction, and so a CI lane
 * that pins CASTLE_E2E_PORT gets deterministic neighbours. When the wanted
 * port is already taken the OS picks a free one instead of failing.
 *
 * Offsets in use: +1/+2 bridge.spec.ts, +3/+4 remote.spec.ts. The main
 * web server is the base itself (playwright.config.ts).
 */

import { createServer } from "node:net";

export const BASE_PORT = Number(process.env.CASTLE_E2E_PORT || 8799);

/** Try to bind `port` (0 = any); resolve the bound port, reject if taken. */
function bind(port: number): Promise<number> {
  return new Promise((ok, fail) => {
    const srv = createServer();
    srv.once("error", fail);
    srv.listen(port, "127.0.0.1", () => {
      const addr = srv.address();
      const got = typeof addr === "object" && addr ? addr.port : 0;
      srv.close(() => ok(got));
    });
  });
}

/** Whatever the OS has free right now. */
export const freePort = (): Promise<number> => bind(0);

/** BASE_PORT + offset when it is free; otherwise a free one. */
export async function lanePort(offset: number): Promise<number> {
  try { return await bind(BASE_PORT + offset); } catch { return freePort(); }
}
