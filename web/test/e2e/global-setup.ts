/**
 * Seed the scratch tracks directory the e2e suite runs against.
 *
 * The directory itself is made in playwright.config.ts — the web server starts
 * before this hook and needs CASTLE_TRACKS already set. This fills it.
 *
 * The tracks are real audio, not stubs: the endpoints under test decode what
 * they are given, so a zero-byte file would pass a route check while proving
 * nothing about the thing the route exists for.
 */

import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { existsSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";

const ROOT = resolve(__dirname, "../../..");
const PY = join(ROOT, ".venv", "bin", "python");
const PAGE = join(ROOT, "previewer", "castle-cue-desk.html");

/** Ids the specs expect to find. Seeded below; nothing else creates them. */
export const MP3_ID = "e2e_beats";
export const WAV_ID = "e2e_lossless";
/** Exists to be deleted, so the delete test cannot depend on running last. */
export const DOOMED_ID = "e2e_doomed";

export default function globalSetup(): () => void {
  const dir = process.env.CASTLE_TRACKS;
  if (!dir) throw new Error("CASTLE_TRACKS unset — playwright.config.ts sets it");
  if (!existsSync(PY)) throw new Error(`no .venv at ${PY} — run the project setup`);
  if (!existsSync(PAGE)) {
    throw new Error(`no built page at ${PAGE} — run \`make preview\` first`);
  }

  // cwd is this directory, not ROOT, and the script gets ROOT by env: the
  // seeding must not care where it runs from (and macOS can wedge a
  // directory's TCC tag so that getcwd() fails there — seen live on the
  // repo root; a process may not even start with that cwd).
  // The scratch show file the server's scene writes land in (see the
  // CASTLE_SCENES note in playwright.config.ts).
  writeFileSync(join(dir, "scenes.yaml"), "scenes:\n");
  execFileSync(PY, ["-c", SEED], {
    cwd: __dirname,
    env: { ...process.env, CASTLE_TRACKS: dir, CASTLE_ROOT: ROOT },
    stdio: "inherit",
  });

  return () => rmSync(dir, { recursive: true, force: true });
}

/**
 * Runs in the project's venv with the repo's own fixture helpers.
 *
 * One MP3 and one WAV. The second is the point of the format work: every
 * endpoint has to cope with a container that is not MP3, and that is exactly
 * the case that used to vanish from the panel without a word.
 */
const SEED = `
import os, sys, tempfile, shutil
from pathlib import Path
root = os.environ["CASTLE_ROOT"]
sys.path.insert(0, os.path.join(root, "tools"))
sys.path.insert(0, os.path.join(root, "tests"))
import import_track as it
from helpers import make_click_track

dest = Path(os.environ["CASTLE_TRACKS"])
dest.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp())
try:
    src = tmp / "src.wav"
    make_click_track(src, seconds=4.0)
    it.convert(src, dest / "${MP3_ID}.mp3", {
        "start": 0, "take": None, "fade_in": None, "fade_out": None,
        "bitrate": 96, "channels": 1, "sample_rate": 44100,
        "normalize": False, "gain_db": None})
    make_click_track(dest / "${WAV_ID}.wav", seconds=2.0)
    make_click_track(dest / "${DOOMED_ID}.wav", seconds=1.0)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("e2e fixtures ->", dest)
`;
