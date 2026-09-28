/* The lab's LED maths (lab-leds.js): real LED levels, supply current, hue
 * variety and the whole-song sweep, on made-up frames. No page, no device. */
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const context = {};
vm.runInNewContext(readFileSync(new URL('./lab-leds.js', import.meta.url), 'utf8'), {globalThis: context});
const L = context.LabLeds;
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} vs ${b}`);
const frame = (c, n = {towerL: 7, door: 12, towerR: 7}) =>
  Object.fromEntries(Object.entries(n).map(([z, k]) => [z, {pix: Array.from({length: k}, () => c), avg: c}]));

test('real LED levels dim a resting glow and leave a full hit alone', () => {
  const [r, g, b] = L.trueLevel([1, 0.3, 0]);
  near(r, 1);
  near(g, 0.3 ** (2.8 / 2.2));
  assert.ok(g < 0.3 * 0.75, 'a 30% glow emits well under what a screen shows');
  near(b, 0);
  assert.deepEqual([...L.trueLevel([1.4, -0.2, 0.5])].map(v => +v.toFixed(3)), [1, 0, +(0.5 ** L.TRUE).toFixed(3)]);
  const real = L.trueOut(frame([0.5, 0.5, 0.5]));
  near(real.door.avg[0], 0.5 ** L.TRUE);
  assert.equal(real.door.pix.length, 12);
});

test('the supply estimate is 20 mA a die at full through the gamma, plus the idle draw', () => {
  near(L.amps(frame([0, 0, 0])), 26 * 0.001);
  near(L.amps(frame([1, 1, 1])), 26 * (0.001 + 0.06));
  near(L.amps(frame([0.5, 0, 0])), 26 * (0.001 + 0.02 * 0.5 ** 2.8));
  assert.ok(L.amps(frame([1, 1, 1])) > L.BUDGET_A, 'full white on every pixel is over the LEDs\' share');
});

test('hue wedges: twelve families of 30 degrees, and dim or pale is no colour', () => {
  assert.equal(L.wedge([1, 0, 0]), 0);
  assert.equal(L.wedge([1, 1, 0]), 2);
  assert.equal(L.wedge([0, 1, 0]), 4);
  assert.equal(L.wedge([0, 0, 1]), 8);
  assert.equal(L.wedge([1, 0, 0.5]), 11);
  assert.equal(L.wedge([0.05, 0, 0]), -1, 'too dim');
  assert.equal(L.wedge([1, 0.9, 0.85]), -1, 'too pale');
});

test('a readable colour keeps its hue, lifts a dim glow, and leaves black black', () => {
  assert.deepEqual([...L.legible([0, 0, 0])], [0, 0, 0]);
  const dim = L.legible([0.1, 0, 0.05]);
  near(dim[0], 0.35 + 0.065, 1e-9);
  near(dim[2] / dim[0], 0.5, 1e-9);
  assert.deepEqual([...L.legible([1, 0.5, 0])], [1, 0.5, 0]);
  const lens = L.lens([1, 0, 0]);
  near(lens[0], 1);
  near(lens[1], 0.3);
  assert.equal(L.hex([1, 0.5, 0]), '#FF8000');
  near(L.apart([1, 0.2, 0], [0.5, 0.2, 0.1]), 0.5);
});

test('a sweep averages each bucket, counts held hues and finds the peak draw', () => {
  const sizes = {towerL: 7, door: 12, towerR: 7};
  let at = -1;
  // Red for the first half of a 4 s song, blue for the second, a white hit at 3 s.
  const colourAt = t => t >= 3000 && t < 3064 ? [1, 1, 1] : t < 2000 ? [0.5, 0, 0] : [0, 0, 0.5];
  const study = L.run({dur: 4000, tick: 16, every: 4, buckets: 8, zones: Object.keys(sizes), sizes,
    step: t => { assert.ok(t > at, 'time only moves forward'); at = t; },
    render: t => frame(colourAt(t), sizes)});
  assert.equal(study.buckets, 8);
  assert.deepEqual([...L.colourAt(study, 'door', 0)], [0.5, 0, 0]);
  assert.deepEqual([...L.colourAt(study, 'towerL', 7, 3)], [0, 0, 0.5]);
  near(study.peak, 26 * 0.061);
  assert.ok(study.mean < study.peak);
  // Red and blue are each held 2 s on three zones; the white hit is no hue.
  assert.equal(study.hues, 2);
  const it = L.sweep({dur: 60000, tick: 16, every: 4, buckets: 10, zones: ['door'], sizes: {door: 1},
    step() {}, render: () => frame([0.2, 0.2, 0.2], {door: 1})});
  const first = it.next();
  assert.equal(first.done, false, 'a long song yields, so the page can draw between slices');
  assert.ok(first.value >= 0 && first.value < 1);
});

// The page is seven classic scripts sharing one global scope, so a name two of
// them declare is a SyntaxError that blanks the whole lab — and no suite opens
// the lab in a browser. Assemble it as show_lab.page does and let V8 judge.
function labScripts(read = name => readFileSync(new URL(name, import.meta.url), 'utf8')) {
  let html = read('show-lab.template.html');
  for (const [hole, name] of html.matchAll(/\/\*\{\{([\w.-]+\.js)\}\}\*\//g)) {html = html.replace(hole, read(name));}
  return [...html.replace('/*{{songs}}*/null', '[]').matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
}
function syntaxErrors(scripts) {
  const ctx = vm.createContext({});
  const errors = [];
  for (const code of scripts) {
    // A missing DOM is a runtime error and expected here; only parse and
    // declaration errors, which happen before a line runs, are failures.
    try {vm.runInContext(code, ctx);} catch (e) {if (e.name === 'SyntaxError') {errors.push(e.message);}}
  }
  return errors;
}

test('the lab page parses, and no two of its scripts declare the same name', () => {
  const scripts = labScripts();
  assert.equal(scripts.length, 7);
  assert.ok(scripts.some(s => s.includes('LabLeds.sweep')), 'the LED script is inlined');
  assert.deepEqual(syntaxErrors(scripts), []);
  const clash = [...scripts, 'let shown=1;'];
  assert.match(syntaxErrors(clash).join(), /shown/, 'the check does catch a name declared twice');
});
