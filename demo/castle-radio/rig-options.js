/* The existing fixture geometry with demo-only per-channel/per-pixel routing. */
/* global $, escapeHTML, lightKey, V, waveformData */
const rig=V.loadRig();
const routeDefaults=()=>({towerL:{source:'backing:left',pixels:{}},door:{source:'vocals:both',pixels:{}},towerR:{source:'backing:right',pixels:{}}});
let routing=routeDefaults(),routingRevision=0,routeLevels=new Map(),routeData=null;
const selectedTargets={towerL:'all',door:'all',towerR:'all'};
const zoneLabels={towerL:'Left tower / channel 1',door:'Doorway / channel 3',towerR:'Right tower / channel 2'};
const sources=[['combined:both','Full song · stereo mix'],['combined:left','Full song · left'],['combined:right','Full song · right'],['vocals:both','Voice · stereo mix'],['vocals:left','Voice · left'],['vocals:right','Voice · right'],['backing:both','Background · stereo mix'],['backing:left','Background · left'],['backing:right','Background · right'],['off','Off']];
try{const saved=JSON.parse(localStorage.getItem('castle-radio-routing')||'null');if(saved){for(const z of V.ZONE_ORDER){if(sources.some(s=>s[0]===saved[z]?.source)){routing[z]=saved[z];}}}}catch{}
function saveRouting(){V.saveRig(rig);localStorage.setItem('castle-radio-routing',JSON.stringify(routing));routingRevision++;lightKey='';$('routing-status').textContent='Saved on this browser · the live preview uses these assignments.';}
function rigLayouts(){return Object.fromEntries(V.ZONE_ORDER.map(z=>[z,V.zoneLayout(rig,z)]));}
function sourceAt(z,pixel){return routing[z].pixels?.[pixel]||routing[z].source;}
function fixtureOptions(fx){
  return V.FIXTURES.map(f=>{
    const selected=fx.id===f.id?'selected':'';
    return `<option value="${f.id}" ${selected}>${f.name} · ${f.count} LEDs</option>`;
  }).join('');
}
function countField(z,fx,layout){
  if(!fx.maxCount){return '';}
  return `<label>Number of LEDs<input data-count="${z}" type="number" min="1" max="${fx.maxCount}" value="${layout.n}"></label>`;
}
function pixelDots(z,layout,target){
  return layout.pos.map((p,i)=>{
    const selected=target===String(i)?'selected':'';
    return `<button class="pixel-dot ${selected}" data-pixel-zone="${z}" data-pixel="${i}" style="left:${p[0]*100}%;top:${p[1]*100}%" aria-label="${zoneLabels[z]} LED ${i+1}">${i+1}</button>`;
  }).join('');
}
function targetOptions(layout,target){
  return layout.pos.map((_,i)=>{
    const selected=target===String(i)?'selected':'';
    const center=i===layout.center?' · center':'';
    return `<option value="${i}" ${selected}>LED ${i+1}${center}</option>`;
  }).join('');
}
function sourceOptions(source){
  return sources.map(([value,label])=>{
    const selected=source===value?'selected':'';
    return `<option value="${value}" ${selected}>${label}</option>`;
  }).join('');
}
function rigCard(z){
  const layout=V.zoneLayout(rig,z),fx=V.fixture(rig.zones[z].fixture),target=selectedTargets[z];
  const source=target==='all'?routing[z].source:sourceAt(z,Number(target));
  const rgbwChecked=V.zoneRgbw(rig,z)?'checked':'';
  const rgbwDisabled=fx.rgbOnly?'disabled':'';
  const centerNote=layout.center===null?'No physical center LED in this fixture.':'LED 1 is the center pixel.';
  const sourceDisabled=layout.n?'':'disabled';
  const overrides=Object.keys(routing[z].pixels||{}).length;
  const defaultLabel=escapeHTML(sources.find(s=>s[0]===routing[z].source)?.[1]||'Off');
  return [
    `<article class="rig-card"><h3>${zoneLabels[z]}</h3>`,
    `<label>LED fixture<select data-fixture="${z}">${fixtureOptions(fx)}</select></label>`,
    countField(z,fx,layout),
    `<label class="switch-row">RGBW variant<input data-rgbw="${z}" type="checkbox" ${rgbwChecked} ${rgbwDisabled}></label>`,
    `<div class="fixture-dots" data-dots="${z}">${pixelDots(z,layout,target)}</div>`,
    `<p class="subtle">${centerNote} Select a numbered LED to override its source.</p>`,
    `<label>Apply to<select data-target="${z}"><option value="all">Whole channel</option>${targetOptions(layout,target)}</select></label>`,
    `<label>Follow this audio<select data-source="${z}" ${sourceDisabled}>${sourceOptions(source)}</select></label>`,
    `<button data-clear-pixels="${z}" class="clear-routing">Clear individual overrides (${overrides})</button>`,
    `<p class="subtle">${defaultLabel} by default</p></article>`,
  ].join('');
}
function drawRig(){
  $('rig-cards').innerHTML=V.ZONE_ORDER.map(rigCard).join('');
  $('rig-summary').textContent=`${V.ZONE_ORDER.reduce((n,z)=>n+V.zoneLayout(rig,z).n,0)} LEDs · 3 lighting channels`;
}
$('rig-cards').onchange=e=>{
  const el=e.target;
  const {fixture,count,rgbw,target,source}=el.dataset;
  if(fixture){
    rig.zones[fixture]={fixture:el.value};routing[fixture].pixels={};selectedTargets[fixture]='all';
  }else if(count){
    rig.zones[count].count=Math.max(1,Math.min(5,Number(el.value)||1));routing[count].pixels={};selectedTargets[count]='all';
  }else if(rgbw){
    rig.rgbw[rig.zones[rgbw].fixture]=el.checked;
  }else if(target){
    selectedTargets[target]=el.value;drawRig();return;
  }else if(source){
    const chosen=selectedTargets[source];
    if(chosen==='all'){routing[source].source=el.value;}else{routing[source].pixels[chosen]=el.value;}
  }
  saveRouting();drawRig();
};
$('rig-cards').onclick=e=>{const b=e.target.closest('button');if(!b){return;}if(b.dataset.pixelZone){selectedTargets[b.dataset.pixelZone]=b.dataset.pixel;drawRig();}if(b.dataset.clearPixels){routing[b.dataset.clearPixels].pixels={};saveRouting();drawRig();}};
$('route-default').onclick=()=>{routing=routeDefaults();saveRouting();drawRig();};
$('route-voice-towers').onclick=()=>{routing.towerL={source:'vocals:left',pixels:{}};routing.towerR={source:'vocals:right',pixels:{}};saveRouting();drawRig();};

function analyzeRoutes(data){
  if(data===routeData){return;}routeData=data;routeLevels.clear();
  if(!data){return;}
  for(const [layer,channels] of Object.entries(data.layers)){for(const [channel,analysis] of Object.entries(channels)){
    const hits=Object.values(analysis.onsets).flat().sort((a,b)=>a[0]-b[0]);
    const top=Math.max(...Object.values(channels).map(c=>c.level||0),.0001);routeLevels.set(`${layer}:${channel}`,{hits,peaks:analysis.peaks,duration:data.duration,gain:(analysis.level||0)/top});
  }}
}
function signalAt(source,time){
  const data=routeLevels.get(source);if(!data){return null;}
  const hits=data.hits;let lo=0,hi=hits.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(hits[mid][0]<=time){lo=mid+1;}else {hi=mid;}}
  let value=0;
  for(let i=lo-1;i>=0&&time-hits[i][0]<1.5;i--){value=Math.max(value,hits[i][1]*Math.exp(-(time-hits[i][0])/($('soften').checked?.3:.15)));}
  const index=Math.min(data.peaks.length-1,Math.max(0,Math.floor(time/data.duration*data.peaks.length)));
  return Math.min(1,(value*.85+(data.peaks[index]||0)*.15)*data.gain);
}
function routePalette(layer,source){
  if(layer==='vocals'){return [1,.28,.48];}
  return source.endsWith(':right')?[.27,.65,1]:[.55,1,.36];
}
function routeStatus(missing){
  if(!waveformData){return 'Loading routing analysis…';}
  if(missing.size){return 'Some assigned sources are unavailable for this song. Import with separation, or choose Full song in Your castle.';}
  return 'Using your channel and LED audio assignments · edit in Your castle';
}
function applyAudioRouting(out,time){
  analyzeRoutes(waveformData);
  const missing=new Set();
  for(const z of V.ZONE_ORDER){const layout=V.zoneLayout(rig,z);out[z].pix=Array.from({length:layout.n},(_,i)=>{
    const source=sourceAt(z,i),layer=source.split(':')[0];
    if(source==='off'){return [0,0,0];}
    if(window.radioLayer!=='combined'&&layer!==window.radioLayer){return [0,0,0];}
    const value=signalAt(source,time);if(value===null){missing.add(source);return [0,0,0];}
    const palette=routePalette(layer,source);
    const strength=value*Number($('intensity').value)/100*Number($('brightness').value)/100;
    return palette.map(c=>Math.sqrt(c*strength));
  });out[z].avg=[0,1,2].map(c=>out[z].pix.reduce((sum,p)=>sum+p[c],0)/Math.max(1,layout.n));}
  $('route-preview-status').textContent=routeStatus(missing);
}
window.radioRig={layouts:rigLayouts,rig,apply:applyAudioRouting,get revision(){return routingRevision;}};
const pixels=new V.PixelInsets($('preview-canvas'),rig);
window.radioRig.drawPixels=out=>{pixels.setRig(rig);pixels.draw(out);};
drawRig();
