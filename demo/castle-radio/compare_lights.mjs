/* Sample actual prepared-cue playback and the webpage renderer; no I/O to a device.
 * Usage: node compare_lights.mjs BASE.show.json CANDIDATE.show.json OUT.json
 */
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const [basePath,candidatePath,outPath]=process.argv.slice(2);
const context=vm.createContext({console});
for(const name of ['visuals.js','cue-playback.js']){
  vm.runInContext(fs.readFileSync(new URL(name,import.meta.url),'utf8'),context);
}
const V=context.CastleVisuals;
const zones=['towerL','towerR','door'];
const scenes=[basePath,candidatePath].map(p=>JSON.parse(fs.readFileSync(p,'utf8')));
const duration=scenes[0].dur;
const strikes=scenes[0].cues.filter(c=>c.op==='strike');
// Pick dense and quieter complete 20-second windows from the middle of the song.
const windows=[];
for(let start=10000;start+20000<duration-10000;start+=10000){
  windows.push({start,count:strikes.filter(c=>c.t>=start&&c.t<start+20000).length});
}
windows.sort((a,b)=>b.count-a.count);
const selected=[windows[0],windows[Math.floor(windows.length*.75)]];
const clips=selected.map((w,i)=>({label:i?'Quieter passage':'Dense passage',start:w.start,end:w.start+20000,frames:[[],[]]}));
const reports=[];
for(const [side,scene] of scenes.entries()){
  for(const soft of [false,true]){
    const state=V.createState(scene,0);V.rebuildLightsAt(state,scene,0);state.soft=soft;
    const params=V.defaultParams();params.soft=soft;
    let recovered=0,samples=0,bright=0,frames=0;
    for(let t=0;t<=Math.ceil(scene.dur/16)*16;t+=16){
      const upcoming=scene.cues.filter(c=>c.op==='strike'&&c.t>t-16&&c.t<=t);
      for(const z of new Set(upcoming.flatMap(c=>c.targets))){
        samples++;if(state.flash[z]<.15){recovered++;}
      }
      context.CastleCuePlayback.fire(state,t);V.decayFlashes(state);
      const rendered=V.renderZones(state,t/1000,params);
      const pixels=zones.flatMap(z=>rendered[z].pix.flat());
      assert(pixels.every(v=>Number.isFinite(v)&&v>=0&&v<=1));
      for(const z of zones){if(state.flash[z]>.5){bright++;}}
      frames++;
      if(!soft&&t%32===0){
        for(const clip of clips){
          if(t>=clip.start&&t<clip.end){clip.frames[side].push(...pixels.map(v=>Math.round(v*255)));}
        }
      }
    }
    assert.equal(state.fired.size,scene.cues.length);
    reports.push({side:side?'option1':'current',soft,frames,cues:state.fired.size,
      recoveredBeforeHitPct:Math.round(1000*recovered/samples)/10,
      timeAboveHalfFlashPct:Math.round(1000*bright/(frames*3))/10});
  }
}
const data={name:scenes[0].name,crc:scenes.map(s=>s.cue_crc32),step:32,zones:[7,7,12],
  clips:clips.map(c=>({...c,frames:c.frames.map(f=>Buffer.from(f).toString('base64'))})),reports};
fs.writeFileSync(outPath,JSON.stringify(data));
console.log(JSON.stringify({name:data.name,reports},null,2));
