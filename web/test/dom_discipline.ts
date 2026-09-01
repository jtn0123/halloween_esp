// The desk reaches into its page through dom.ts (el/req/val/sel/reqIn), not
// through document.getElementById or document.querySelector — req and reqIn
// name what was missing instead of throwing TypeError on null. The
// convention was written down and then lost ground (58 raw sites at one
// audit, 74 at the next), so now it is a check: grade report 2026-08-24 C1,
// widened to querySelector by grade report 2026-08-31 C4.
//
// The scan walks src/ recursively — a rule that stopped at the top level
// would be a rule that a subdirectory silently repeals.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("../src", import.meta.url).pathname;
// "document.querySelector" also catches querySelectorAll, which is the same
// page-wide reach by another name. Subtree lookups (root.querySelector) are
// what dom.ts's sel/reqIn wrap, and are not banned outright — C5 converts
// the ones that carried a `!`.
const BANNED = ["document.getElementById", "document.querySelector"];

const offenders: string[] = [];
function scan(dir: string, rel: string): void {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, e.name);
    const name = rel ? `${rel}/${e.name}` : e.name;
    if (e.isDirectory()) { scan(path, name); continue; }
    if (!e.name.endsWith(".ts") || name === "dom.ts") continue;
    const text = readFileSync(path, "utf8");
    let i = 0;
    for (const line of text.split("\n")) {
      i++;
      for (const b of BANNED) {
        if (line.includes(b)) offenders.push(`${name}:${i}  (${b})`);
      }
    }
  }
}
scan(SRC, "");

if (offenders.length) {
  console.error("dom discipline: page-wide lookup outside dom.ts —"
    + " use el/req/val/sel/reqIn from dom.ts instead:\n  "
    + offenders.join("\n  "));
  process.exit(1);
}
console.log("dom discipline: every page-wide lookup goes through dom.ts");
