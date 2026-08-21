import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * End-to-end tests for the cue desk.
 *
 * These drive the real page against the real tools/studio.py, because the bugs
 * worth catching here are the ones neither half sees alone: a button that
 * posts the wrong shape, a row that never redraws, a Play control that plays
 * nothing. The node tests cover the arithmetic; these cover the wiring.
 *
 * Two rules the suite is built around:
 *
 *   Never make a sound. Chromium is launched with --mute-audio, which the
 *   browser enforces rather than the page under test — so even a regression
 *   that unmuted everything could not come out of the speakers during a run.
 *   The app's whole audio contract is "silent until you ask for it", and a
 *   suite that broke that while checking it would be absurd.
 *
 *   Never touch the real tracks. CASTLE_TRACKS points the server at a scratch
 *   directory, so imports, deletes and the manifest all land somewhere
 *   disposable. The directory is made here rather than in globalSetup because
 *   the web server starts first and needs the variable already set.
 */

/** Fixed rather than ephemeral: webServer needs the URL before it starts. */
const PORT = Number(process.env.CASTLE_E2E_PORT || 8799);

/** Seeded by global-setup, torn down by the function it returns. */
const TRACKS = process.env.CASTLE_TRACKS
  || mkdtempSync(join(tmpdir(), "castle-e2e-"));
process.env.CASTLE_TRACKS = TRACKS;
/* Scene writes redirect too. Without this the sandbox had a hole: a test
   clicking "Make scene" wrote the user's REAL scenes/scenes.yaml — which
   actually happened once (2026-08-13, a stray ghostbusters scene, reverted).
   The suite intercepts /api/scene client-side, but the server-side guard is
   the one that cannot be forgotten by a new spec. */
const SCENES_FILE = join(TRACKS, "scenes.yaml");
process.env.CASTLE_SCENES = SCENES_FILE;

export default defineConfig({
  testDir: "./test/e2e",
  // One server, one tracks directory: parallel workers would be editing the
  // same fixtures underneath each other.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  reporter: "list",
  timeout: 30_000,
  expect: { timeout: 10_000 },

  globalSetup: "./test/e2e/global-setup.ts",

  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    launchOptions: {
      args: [
        // The guarantee, at the browser level.
        "--mute-audio",
        // Media elements otherwise refuse to start without a real gesture,
        // which would make "does Play actually play" untestable. Muted anyway.
        "--autoplay-policy=no-user-gesture-required",
      ],
    },
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],

  webServer: {
    command: `../.venv/bin/python ../tools/studio.py ${PORT} --localhost`,
    url: `http://127.0.0.1:${PORT}/api/tracks`,
    // A studio the user is already running is pointed at their real tracks,
    // which is exactly what this suite must not touch.
    reuseExistingServer: false,
    // CASTLE_HOST="" = explicitly castle-less: the stubs in the specs are the
    // only castle, whether or not the real one is awake on the LAN.
    env: { CASTLE_TRACKS: TRACKS, CASTLE_SCENES: SCENES_FILE, CASTLE_HOST: "" },
    stdout: "pipe",
    stderr: "pipe",
    timeout: 30_000,
  },
});
