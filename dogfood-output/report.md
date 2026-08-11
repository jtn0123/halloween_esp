# Dogfood Report: Castle Cue Desk

| Field | Value |
|-------|-------|
| **Date** | 2026-08-11 |
| **App URL** | http://127.0.0.1:8765 |
| **Session** | castle-cue-desk |
| **Scope** | Tracks panel, transport and scrubbing, scene switching, clip editor |

## Summary

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 3 |
| **Total** | **3** |

## Tooling note

`agent-browser` could not be used for this app. Two blockers, both verified:

- **`screenshot` fails** with `Resource temporarily unavailable (os error 35) —
  daemon may be busy or unresponsive`, across two fresh sessions. `eval` and
  `open` work; image capture does not.
- **`requestAnimationFrame` never fires** — 0 frames in 500 ms with
  `document.hidden === false` and `visibilityState === "visible"`. The browser
  is headless with no frame production, so the app's render loop never ticks.
  Every clock, canvas and meter reading through it is frozen at its initial
  value.

That second one matters more than it looks: a naive pass would have reported
"the transport is broken, the clock never advances, the canvas is black" — all
false. Testing was done through a browser with real frame production instead.

Evidence here is therefore measured DOM and canvas state rather than image
files. For a canvas-driven app that is arguably stronger: an exact pixel value
at a known coordinate is more precise than a screenshot of it.

## Issues

### ISSUE-001 — Negative bitrate renders a malformed capacity readout

| | |
|---|---|
| **Severity** | Low |
| **Area** | Tracks panel — capacity readout |
| **Repro video** | N/A (deterministic, no timing involved) |

The capacity line accepts whatever is typed into `kbps` and does arithmetic on
it without validating. A negative value produces negative durations formatted
through a `mm:ss` helper that was never meant to see them.

**Steps**
1. Open the Tracks panel.
2. Type `-5` into the **kbps** field.

**Observed** — the readout reads:

    -5 kbps mono = -0.6 KB/s · flash, alongside the current show: 0:00
    · SD loaded into PSRAM: -47:-47 (under 4 min)

`-47:-47` is not a time. `-0.6 KB/s` is not a rate.

**Expected** — reject or clamp values below a sensible floor (MPEG-1 Layer III
starts at 32 kbps), or fall back to the default as the empty and non-numeric
cases already do.

**Note** — `0`, `abc` and empty all correctly fall back to 96. Only negatives
slip through, because the code guards with `|| 96`, and `-5` is truthy.

---

### ISSUE-002 — Channel counts other than 1 or 2 are silently shown as mono

| | |
|---|---|
| **Severity** | Low |
| **Area** | Tracks panel — capacity readout / import options |
| **Repro video** | N/A |

**Steps**
1. Type `7` into the **ch** field.

**Observed** — the readout says `96 kbps mono`, identical to `ch=1`. Nothing
indicates the value is invalid.

**Why it matters** — the value is still sent on import, and the importer's
argument parser only accepts 1 or 2, so the job fails at the server with a
parser error rather than being caught in the field where it was typed. The
preview actively reassures you that a value which cannot work is fine.

**Expected** — constrain the field (a two-option select would remove the class
of problem entirely), or show the value as invalid.

---

### ISSUE-003 — The clip editor occupies space before any track is selected

| | |
|---|---|
| **Severity** | Low (cosmetic) |
| **Area** | Tracks panel — clip editor |
| **Repro video** | N/A |

**Steps**
1. Load the page with the studio running and at least one track imported.
2. Do not click a track.

**Observed** — a 132 px empty canvas is rendered, with an **Audition** button
and a sensitivity slider, above the drop zone. The stylesheet has a
`.trk-wave:empty { display: none }` rule intended to collapse it, but the
editor populates its container on init, so the container is never empty and
the rule never applies.

**Mitigating** — the Audition button is correctly `disabled` in this state, so
nothing misleads you into clicking a dead control. It is wasted vertical space
and a slider that adjusts nothing, not a broken interaction.

**Expected** — collapse the editor until a track is chosen.

---

## What was tested and found working

Worth recording, because "no issue" is a result too:

- **Transport** — play/pause, restart, blackout, and the play-button label all
  track state correctly. Stop→Play relights the stage (a regression fixed
  earlier this session, still holding).
- **Scene switching** — picking a scene while stopped stays stopped and
  silent; picking one while playing keeps playing and resets the clock. The
  cue sheet, tick marks and duration all follow.
- **Seeking** — dragging the scrub bar moves light and audio together, and
  clamps correctly at both ends. Keyboard seeking past either end does not
  produce a negative or overrunning clock.
- **Mute** — defaults to muted on every load, and survives scene changes. The
  page made no sound at any point during testing.
- **Import validation** — blank input says "Paste a link first"; a non-URL is
  rejected before any network call, with a readable message rather than raw
  shell output.
- **Clip editor** — opens on a track row click, populates start/take from the
  track, and the Audition control is correctly disabled with nothing loaded.

## Assessment

No critical, high or medium issues found. All three findings are Low: two are
missing input validation on the same options row, and one is cosmetic layout.

The core show-running paths — transport, scene switching, seeking, mute — held
up under deliberate abuse, which is consistent with them being the parts
covered by the 331-test suite. The three findings all sit in the Tracks
panel's option inputs, which is the newest code and the only significant
surface with no automated tests behind it.

The single highest-value fix is constraining that options row: a `number`
input with `min`/`max` and a two-option select for channels would remove
ISSUE-001 and ISSUE-002 together, at the field where the mistake is made
rather than at the server that rejects it later.

