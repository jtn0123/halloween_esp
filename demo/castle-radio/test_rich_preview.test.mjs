/* Run the real preview controller: native data, checksum refusal and silent clock. */
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {read, page, element, settle} from './test_support.mjs';

async function previewContext(checksum = '1234') {
  const {$} = page();
  $('show-preview-mode').value = 'prepared';
  $('split-preview').querySelector = () => ({after() {}});
  const prepared = {id:'radio_test',cue_crc32:checksum,base:{towerL:'chill'},
    cues:[{t:10,op:'strike',targets:['door'],color:[.1,.4,.8,0],decay:.87,pixels:'scatter'}]};
  const context = {
    $, prepared, current:0, queue:[0], history:[], imported:new Map(),
    tracks:[{id:0,key:'radio_test',title:'Test',duration:10,cues:[],
      prepared_show:{crc32:'1234',url:'/radio/audio/test.show.json',records:1}}],
    audio:{currentTime:0,duration:10,paused:true,pause(){this.paused=true;},
      addEventListener(){}, removeEventListener(){}, play(){throw new Error('Unexpected audio');}},
    performance:{now:()=>context.now},now:0,blacked:false,stopped:false,
    window:{radioLayer:'combined',addEventListener(){}},location:{hash:'#play'},
    document:{createElement:()=>element('new'),querySelectorAll:()=>[]},
    CastleVisuals:{Stage:class {},defaultParams:()=>({})},
    ResizeObserver:class {observe(){}},
    REQUEST_MS:{act:1000,analysis:1000},AbortSignal,
    fetch:async()=>({ok:true,json:async()=>[]}),
    request:async path=>path.endsWith('.show.json')?prepared:{layers:{}},
    localStorage:{getItem:()=>null},requestAnimationFrame(){},
    fmt:String,updatePlayer(){},toggle(){},toast(){},
    stop(){context.stopped=true;context.audio.pause();context.audio.currentTime=0;},
  };
  vm.createContext(context);
  vm.runInContext(read('preview.js'),context);
  await settle();
  return context;
}

test('prepared preview uses exported cues instead of style or routing experiments',async()=>{
  const c=await previewContext();
  assert.equal(vm.runInContext('makeScene(tracks[0]) === prepared',c),true);
  assert.equal(vm.runInContext('preparedError',c),'');
});

test('mismatched preview is refused and cannot substitute the old simplified show',async()=>{
  const c=await previewContext('wrong');
  assert.match(vm.runInContext('preparedError',c),/does not match/);
  assert.equal(vm.runInContext('makeScene(tracks[0]).cues.length',c),0);
});

test('silent simulation advances and scrubs without starting audio, and Stop clears it',async()=>{
  const c=await previewContext();
  c.$('simulate-lights').onclick();c.now=1500;
  assert.equal(vm.runInContext('localTime()',c),1.5);
  assert.equal(c.audio.paused,true);
  vm.runInContext('seekTo(6)',c);c.now=2000;
  assert.equal(vm.runInContext('localTime()',c),6.5);
  c.$('simulate-lights').onclick();
  assert.equal(c.audio.currentTime,6.5);
  assert.equal(c.audio.paused,true);
  c.$('simulate-lights').onclick();c.$('split-stop').onclick();
  assert.equal(vm.runInContext('simulation',c),null);
  assert.equal(c.audio.currentTime,0);
});

test('card playback replaces competing strikes, including soft mode and attacks',()=>{
  const c=vm.createContext({console});
  vm.runInContext(read('visuals.js'),c);
  vm.runInContext(read('cue-playback.js'),c);
  const V=c.CastleVisuals;
  const scene={id:'test',dur:2000,base:{},cues:[
    {t:0,op:'strike',targets:['towerL'],intensity:.8,attack:0,decay:.9,pixels:'ring',color:[1,0,0,0]},
    {t:16,op:'strike',targets:['towerL'],intensity:.2,attack:0,decay:.8,pixels:'center',color:[0,1,0,0]},
    {t:32,op:'strike',targets:['towerL'],intensity:.6,attack:96,decay:.9,pixels:'scatter',color:[0,0,1,0]},
  ]};
  for(const soft of [false,true]){
    const state=V.createState(scene,0);state.soft=soft;
    c.CastleCuePlayback.fire(state,0);V.decayFlashes(state);
    c.CastleCuePlayback.fire(state,16);
    assert.equal(state.flash.towerL,.2,'replace, never add or soften the cue intensity');
    assert.equal(state.flashMode.towerL,2);
    c.CastleCuePlayback.fire(state,32);
    assert.equal(state.flashTarget.towerL,.6);
    assert.equal(state.flashRise.towerL,.6*16/96);
    assert.equal(state.flashEpoch.towerL,3);
    c.CastleCuePlayback.fire(state,32);
    assert.equal(state.flashEpoch.towerL,3,'never repeat a fired cue');
    assert.equal(state.flash.towerR,0);
  }
});

test('card v2: ornament adds, looks lock the head, only a train is softened',()=>{
  const c=vm.createContext({console});
  vm.runInContext(read('visuals.js'),c);
  vm.runInContext(read('cue-playback.js'),c);
  const V=c.CastleVisuals;
  const white=[1,1,1,1];
  const hit=(t,extra)=>({t,op:'strike',targets:['door'],intensity:.5,attack:0,decay:.9,
    pixels:'all',color:white,...extra});
  const scene={id:'v2',dur:4000,base:{},cues:[
    hit(0),hit(100,{layer:1,intensity:.3,pixels:'left'}),hit(500),
    {t:600,op:'look',targets:['towerL'],overlay:'chase',rate:2},
    {t:900,op:'look',targets:['towerL'],rate:.5},
  ]};
  const state=V.createState(scene,0);state.soft=true;
  c.CastleCuePlayback.fire(state,100);
  assert.equal(state.flash.door,.5,'layer 0 stands');
  assert.equal(state.x.door.ornFlash,.3,'layer 1 lands beside it');
  assert.equal(state.x.door.ornMode,4);
  assert.equal(state.x.door.train0,false,'the first strike is never a train');
  assert.equal(state.x.door.train1,true,'100 ms later is');
  c.CastleCuePlayback.fire(state,500);
  assert.equal(state.x.door.train0,false,'400 ms after the last is not');
  c.CastleCuePlayback.fire(state,600);
  assert.equal(state.overlay.towerL,2);
  const before=V.overlayHead(state.x.towerL,.9);
  c.CastleCuePlayback.fire(state,900);
  assert.ok(Math.abs(V.overlayHead(state.x.towerL,.9)-before)<1e-12,'no jump on a rate change');
  assert.equal(state.x.towerL.rate,.5);
  const lit=V.renderZones(state,1,V.defaultParams()).door.pix[0][0];
  assert.ok(lit>.5*.92,'the ornament adds light on top of layer 0');
});

test('the lab renders today\'s castle: softAll softens every strike, isolated or not',()=>{
  const c=vm.createContext({console});
  vm.runInContext(read('visuals.js'),c);
  vm.runInContext(read('cue-playback.js'),c);
  const V=c.CastleVisuals;
  const hit=(t,extra)=>({t,op:'strike',targets:['door'],intensity:.5,attack:0,decay:.9,
    pixels:'all',color:[1,1,1,1],...extra});
  const scene={id:'v1',dur:4000,base:{},cues:[hit(0),hit(1000,{layer:1})]};
  for(const softAll of [false,true]){
    const state=V.createState(scene,0);state.soft=true;state.softAll=softAll;
    c.CastleCuePlayback.fire(state,1000);
    assert.equal(state.x.door.train0,softAll,'a lone strike is a train only on v5.70');
    assert.equal(state.x.door.train1,softAll);
  }
});
