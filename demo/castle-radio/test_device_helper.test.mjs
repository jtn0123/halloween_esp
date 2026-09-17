import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {read, page, settle} from './test_support.mjs';

function boot() {
  const {$} = page();
  const listeners = new Map(), posted = [], direct = [], controls = [{disabled:true}];
  const popup = {closed:false, postMessage:(message,origin)=>posted.push({message,origin})};
  $('import-form').querySelectorAll = () => controls;
  const window = {
    castleDirect:{library:[]}, open:()=>popup,
    fetch:async (...args)=>{direct.push(args);return new Response('{}');},
    addEventListener:(name,fn)=>listeners.set(name,fn), dispatchEvent() {},
  };
  const ctx = {window, document:{getElementById:$},location:{origin:'http://10.27.27.81',href:'http://10.27.27.81/'},
    URL, Response, CustomEvent:class {}, setTimeout:()=>1, clearTimeout(){}, setInterval(){}};
  vm.runInNewContext(read('device-helper.js'), ctx);
  window.castleDesktop.connect();
  const deliver = (data, origin='http://127.0.0.1:8871', source=popup)=>listeners.get('message')({data,origin,source});
  const ready = {type:'castle-tools-ready',service:'castle-radio',protocol:1,status:{castle_origin:'http://10.27.27.81'}};
  return {window,posted,direct,controls,deliver,ready,popup};
}

test('only the opened helper and exact castle identity can enable imports',()=>{
  const t=boot();
  t.deliver(t.ready,'http://wrong');
  t.deliver(t.ready,undefined,{});
  t.deliver({...t.ready,status:{castle_origin:'http://another-castle'}});
  assert.equal(t.window.castleDesktop.connected,false);
  assert.equal(t.controls[0].disabled,true);
  t.deliver(t.ready);
  assert.equal(t.window.castleDesktop.connected,true);
  assert.equal(t.controls[0].disabled,false);
});

test('upload bytes go to Mac but physical commands stay directly on the castle',async()=>{
  const t=boot();t.deliver(t.ready);
  const body=new Uint8Array([1,2,3]).buffer;
  const result=t.window.fetch('/radio/import',{method:'POST',body});
  assert.equal(t.posted[0].message.body,body);
  assert.equal(t.posted[0].origin,'http://127.0.0.1:8871');
  t.deliver({type:'castle-tools-response',id:t.posted[0].message.id,status:202,headers:{'Content-Type':'application/json'},body:new TextEncoder().encode('{"id":"job"}').buffer});
  assert.equal((await result).status,202);
  await t.window.fetch('/radio/device/command',{method:'POST',body:'{"action":"stop"}'});
  assert.equal(t.direct.length,1);
  assert.equal(t.posted.length,1);
});

test('prepared cue frames reach the direct physical player with the catalog',async()=>{
  const t=boot();t.deliver(t.ready);
  const result=t.window.fetch('/radio/library');
  const row={key:'radio_a',filename:'radio_a.mp3',frames:[[0,'door:ff0000@50']]};
  t.deliver({type:'castle-tools-response',id:t.posted[0].message.id,status:200,headers:{'Content-Type':'application/json'},body:new TextEncoder().encode(JSON.stringify([row])).buffer});
  await result;
  assert.equal(t.window.castleDirect.library[0].key,'radio_a');
  assert.deepEqual(t.window.castleDirect.library[0].frames,row.frames);
});

test('closing helper disables imports and leaves castle routes available',async()=>{
  const t=boot();t.deliver(t.ready);t.popup.closed=true;
  await assert.rejects(t.window.castleDesktop.request('/radio/jobs'),/Connect Mac/);
  assert.equal(t.controls[0].disabled,true);
  await t.window.fetch('/radio/device');
  assert.equal(t.direct.length,1);
  await settle();
});
