/* The light-show lab's LED maths, with no page in it: what the castle's LEDs
 * really emit, what they draw from the supply, and a whole song's colours
 * boiled down to a few hundred buckets. show-lab-leds.js draws with it;
 * test_lab_leds.test.mjs runs it under node. */
(root=>{
'use strict';
// ESPHome gamma-corrects every addressable write (gamma_correct defaults to
// 2.8 and firmware/generated/lights.yaml sets none), so a value v drives its
// die at v^2.8 of full. A screen shows a value e at about e^2.2 of its full
// light, so the screen value that emits what the LED does is v^(2.8/2.2):
// the resting glows get dimmer and a full hit stays full.
const GAMMA=2.8,SCREEN=2.2,TRUE=GAMMA/SCREEN;
// The NeoPixel figures rig.ts rigPower uses: 20 mA a die at full, and about
// 1 mA a pixel for its controller when it is dark. Estimated from the screen
// colour, which folds a jewel's white die into warm RGB, so a warm white
// reads a little high: an estimate, not a meter.
const DIE_A=0.02,IDLE_A=0.001;
// The LEDs' share of the supply: the castle's 5 V 3 A supply
// (docs/notes/02-mockup-and-bom.md) less the amplifiers' 1.6 A of headroom
// (web/src/rig.ts AUDIO_AMPS). A show that peaks above it is worth a look.
const BUDGET_A=1.4;
// A hue counts toward a show's variety once it has been held two seconds.
const HELD_MS=2000;

const clamp=v=>Math.min(1,Math.max(0,v));
const luma=c=>Math.max(c[0],c[1],c[2]);
function trueLevel(c){return [clamp(c[0])**TRUE,clamp(c[1])**TRUE,clamp(c[2])**TRUE];}
function trueOut(out){
  const real={};
  for(const z of Object.keys(out)){real[z]={pix:out[z].pix.map(trueLevel),avg:trueLevel(out[z].avg)};}
  return real;
}
function amps(out){
  let a=0;
  for(const z of Object.keys(out)){
    for(const c of out[z].pix){a+=IDLE_A+DIE_A*(clamp(c[0])**GAMMA+clamp(c[1])**GAMMA+clamp(c[2])**GAMMA);}
  }
  return a;
}
/** Which of twelve 30° hue wedges a colour sits in, or -1 when it is too dim
 *  or too pale to read as a colour at all. */
function wedge(c){
  const hi=luma(c),lo=Math.min(c[0],c[1],c[2]),d=hi-lo;
  if(hi<0.1||d/hi<0.25){return -1;}
  let h=(c[0]-c[1])/d+4; // blue leads
  if(hi===c[0]){h=((c[1]-c[2])/d+6)%6;}
  else if(hi===c[1]){h=(c[2]-c[0])/d+2;}
  return Math.floor(h*2)%12;
}
/** A lit die seen close up: its hue, only a little whitened at full drive, so
 *  the colour still reads in the strip (the castle view's `hot` is the glare). */
function lens(c){
  const l=luma(c),w=0.3*l*l;
  return l>0?c.map(v=>v/l+(1-v/l)*w):[0,0,0];
}
/** A barcode bucket's colour: its hue at 35% brightness or more, so a dim
 *  resting glow still shows WHICH colour it is. Dark stays dark. */
function legible(c){
  const l=luma(c);
  if(l<0.02){return [0,0,0];}
  const k=Math.min(1,0.35+0.65*l)/l;
  return c.map(v=>Math.min(1,v*k));
}
function hex(c){return '#'+c.map(v=>Math.round(clamp(v)*255).toString(16).padStart(2,'0')).join('').toUpperCase();}

/**
 * A whole song, played headless: `step(t)` advances the show to t ms and
 * `render(t)` returns that moment's zones. Every `every`-th tick is sampled
 * into one of `buckets` time buckets: each zone's and each pixel's average
 * colour, the supply current, and how long each hue was held. A generator,
 * so the page can do it in slices between frames; it yields its progress.
 */
function* sweep(o){
  const acc=sums(o);
  let peak=0,total=0,samples=0;
  for(let t=0,k=0;t<=o.dur;t+=o.tick,k++){
    o.step(t);
    if(k%o.every){continue;}
    const out=o.render(t),a=amps(out);
    peak=Math.max(peak,a);total+=a;samples++;
    add(acc,o,out,Math.min(o.buckets-1,Math.floor(t/o.dur*o.buckets)));
    if(k%(o.every*64)===0){yield t/o.dur;}
  }
  average(acc,o);
  return {buckets:o.buckets,zones:acc.zones,pix:acc.pix,sizes:o.sizes,peak,mean:total/Math.max(1,samples),
    hues:acc.held.filter(ms=>ms>=HELD_MS).length};
}
// A sweep's running sums: per bucket, each zone's and each pixel's colour.
function sums(o){
  const acc={count:new Float32Array(o.buckets),held:new Float64Array(12),zones:{},pix:{}};
  for(const z of o.zones){acc.zones[z]=new Float32Array(o.buckets*3);acc.pix[z]=new Float32Array(o.buckets*o.sizes[z]*3);}
  return acc;
}
function add(acc,o,out,b){
  acc.count[b]++;
  for(const z of o.zones){
    const c=out[z].avg,w=wedge(c),n=o.sizes[z];
    for(let j=0;j<3;j++){acc.zones[z][b*3+j]+=c[j];}
    out[z].pix.forEach((p,i)=>{for(let j=0;j<3;j++){acc.pix[z][(b*n+i)*3+j]+=p[j];}});
    if(w>=0){acc.held[w]+=o.tick*o.every;}
  }
}
function average(acc,o){
  for(const z of o.zones){
    const n=o.sizes[z];
    for(let b=0;b<o.buckets;b++){
      const k=acc.count[b]||1;
      for(let j=0;j<3;j++){acc.zones[z][b*3+j]/=k;}
      for(let i=0;i<n*3;i++){acc.pix[z][b*n*3+i]/=k;}
    }
  }
}
/** Drives a sweep to its end in one go — for tests and small songs. */
function run(o){const it=sweep(o);let r=it.next();while(!r.done){r=it.next();}return r.value;}
/** One bucket's colour from a sweep: a zone's average, or pixel `i` of it. */
function colourAt(study,zone,b,i){
  if(i===null||i===undefined){const z=study.zones[zone];return [z[b*3],z[b*3+1],z[b*3+2]];}
  const n=study.sizes[zone],p=study.pix[zone],at=(b*n+i)*3;
  return [p[at],p[at+1],p[at+2]];
}
/** How far apart two pixels are: the largest channel difference. */
function apart(a,b){return Math.max(Math.abs(a[0]-b[0]),Math.abs(a[1]-b[1]),Math.abs(a[2]-b[2]));}

root.LabLeds={GAMMA,TRUE,BUDGET_A,luma,trueLevel,trueOut,amps,wedge,hex,lens,legible,sweep,run,colourAt,apart};
})(globalThis);
