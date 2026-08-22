# Reading SonarCloud's findings without SonarCloud

**Why this file exists:** the quality gate on a pull request reports two
letters — a Security rating and a Reliability rating — and nothing else that a
command line can reach. The project is private, so `api/issues/search` answers
*"Project doesn't exist"* to an unauthenticated caller, and the GitHub check
carries at most **50 annotations**, which fill up with maintainability smells
long before they reach the handful of Bug and Vulnerability issues that are
what the two ratings are actually made of. Fixing a gate you cannot read means
pushing a guess and waiting five minutes to learn nothing.

Two ways out, in the order worth trying.

## 1. Run Sonar's own rules locally (works today, no account)

Sonar's JavaScript/TypeScript analyzer is published as an ESLint plugin, and
it carries the same rule ids the dashboard shows (`S2681` and friends). It
found the last bug this way in about a minute.

`web/` pins TypeScript 7, which the plugin's `ts-api-utils` cannot parse, so
run it from a scratch project with TypeScript 5 rather than adding it here:

```bash
mkdir -p /tmp/sonarlint && cd /tmp/sonarlint && npm init -y
npm install eslint@9 eslint-plugin-sonarjs typescript@5.9 typescript-eslint
cat > eslint.config.mjs <<'EOF'
import sonarjs from "eslint-plugin-sonarjs";
import tsparser from "@typescript-eslint/parser";
export default [
  { ignores: ["**/dist/**", "**/node_modules/**"] },
  {
    files: ["**/*.ts", "**/*.mjs", "**/*.js"],
    languageOptions: { parser: tsparser, ecmaVersion: 2023, sourceType: "module" },
    plugins: { sonarjs },
    rules: sonarjs.configs.recommended.rules,
  },
];
EOF
```

ESLint 9 refuses to lint outside its config's directory, so copy the sources
in rather than pointing at the repo:

```bash
cp -R ~/…/halloween_esp/web/src src && cp -R ~/…/halloween_esp/web/test test
npx eslint src test
```

Line numbers match the originals. What the output means:

- **Bug rules** (`no-unenclosed-multiline-block`, `no-identical-expressions`,
  `no-element-overwrite`, `no-one-iteration-loop`, …) are what the
  **Reliability** rating counts. Fix these first.
- **`pseudo-random`** is a Security *Hotspot*, not a vulnerability — it does
  not move the Security rating, and the synth's dice are a visual effect.
- **`cognitive-complexity`, `no-nested-conditional`, `no-nested-template-
  literals`** are maintainability. Worth doing, but they will not turn a
  letter.

The plugin does **not** implement the taint rules (injection, XSS), and it
does not read Python, YAML or C++ — so a clean run here does not mean a clean
gate. What it does mean is that the JS/TS half is no longer guesswork.

## 2. Give CI a token (the durable fix)

Add a `SONAR_TOKEN` repository secret and a scanner step; the analysis then
prints its issues into the job log, where they can be read like any other
failure. That also lets `api/issues/search` work from a script:

```bash
curl -s -u "$SONAR_TOKEN:" \
  "https://sonarcloud.io/api/issues/search?componentKeys=jtn0123_halloween_esp&pullRequest=7&types=BUG,VULNERABILITY"
```

## What the ratings are made of

Worth knowing before chasing one:

| Rating | Counts | A means |
|---|---|---|
| Security | **Vulnerabilities** only | none |
| Reliability | **Bugs** only | none |
| Maintainability | Code smells | ratio of debt to size |
| Security Review | **Hotspots** reviewed | all reviewed |

A single Blocker vulnerability is an E, however clean everything else is —
which is how two obviously-fake `wifi_password:` literals (a CI step and a
tracked template) held the rating down while dozens of smells were being
fixed around them.

## Already dealt with, for the record

Found and fixed while the gate was unreadable, most of them real:

- **XSS in the castle chip and panel** — the desk built markup from names read
  off the SD card, which the accepted-risk unauthenticated `PUT` lets anyone
  write. `web/test/e2e/castle_panel.spec.ts` drops the payload now.
- **Five backtracking regexes** (`S5852`) across the studio, the generators
  and two test harnesses.
- **Log injection** — the request line reached the console verbatim, so a CR
  in a URL forged a second log entry (`studio_http.scrub`).
- **Hardcoded credentials** — the two `wifi_password:` literals above.
- **Bugs** — a sort with no comparator, `.map` handed a callback with an
  index, untyped recorder arrays that made real comparisons read as
  always-false, float equality, a mutable global PRNG state, and three
  one-line `if`s whose second statement was not conditional.
