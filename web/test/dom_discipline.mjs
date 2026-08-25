// The desk reaches into its page through dom.ts (el/req/val), not through
// document.getElementById — req names the missing id instead of throwing
// TypeError on null. The convention was written down and then lost ground
// (58 raw sites at one audit, 74 at the next), so now it is a check:
// grade report C1.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("../src", import.meta.url).pathname;
const offenders = [];
for (const f of readdirSync(SRC)) {
  if (!f.endsWith(".ts") || f === "dom.ts") continue;
  const text = readFileSync(join(SRC, f), "utf8");
  let i = 0;
  for (const line of text.split("\n")) {
    i++;
    if (line.includes("document.getElementById")) offenders.push(`${f}:${i}`);
  }
}
if (offenders.length) {
  console.error("dom discipline: document.getElementById outside dom.ts —"
    + " use el/req/val from dom.ts instead:\n  " + offenders.join("\n  "));
  process.exit(1);
}
console.log("dom discipline: every by-id lookup goes through dom.ts");
