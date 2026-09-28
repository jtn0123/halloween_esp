/* The lab's LEDs up close: each castle's real pixels drawn as the boards they
 * are (a Jewel 7 in each tower, a Ring 12 on the door), with a readout under
 * each, the supply current, the pixels where the two shows part ways, and a
 * whole-song colour barcode per show under the timeline. Click a pixel to
 * follow it alone. Runs after the page script and shares its globals; the
 * maths is lab-leds.js. */
'use strict';
const LED={real:false,diffs:false,pick:null,studies:[null,null],gen:0,bar:null,barKey:''};
const STUDY=new WeakMap(),BUCKETS=600;
// Each fixture's board, measured once from its layout: how far its pixels sit
// from the middle, and the 5050 package that fits between neighbours.
const BOARD={};
function board(zone){
  if(BOARD[zone]){return BOARD[zone];}
  const layout=V.zoneLayout(V.DEFAULT_RIG,zone),pos=layout.pos;let reach=0,gap=1;
  pos.forEach((p,i)=>{reach=Math.max(reach,Math.hypot(p[0]-.5,p[1]-.5));
    pos.forEach((q,j)=>{if(j>i){gap=Math.min(gap,Math.hypot(p[0]-q[0],p[1]-q[1]));}});});
  BOARD[zone]={layout,reach,gap,ring:layout.center===null};
  return BOARD[zone];
}
// Where a zone's board sits in the strip, in its 720-wide units.
function place(i,zone){
  const cell=720/3,size=Math.min(cell,tall-26)*.8,b=board(zone);
  return {b,size,x0:i*cell+(cell-size)/2,y0:(tall-26-size)/2+4,pkg:Math.min(17,b.gap*size*.6)};
}
/** What the page draws with: the zones as rendered, or as the LEDs emit them. */
function emitted(out){return LED.real?LabLeds.trueOut(out):out;}

function drawPixels(canvas,out,i,other){
  const k=Math.max(1,canvas.clientWidth)/720*Math.min(devicePixelRatio||1,2),w=720,h=tall;
  if(canvas.width!==Math.round(w*k)||canvas.height!==Math.round(h*k)){canvas.width=Math.round(w*k);canvas.height=Math.round(h*k);}
  const g=canvas.getContext('2d'),legible=canvas.clientWidth>=440,side=sides[i];
  g.setTransform(k,0,0,k,0,0);g.fillStyle='#000';g.fillRect(0,0,w,h);
  order.forEach((zone,z)=>{
    const {b,size,x0,y0,pkg}=place(z,zone),cx=x0+size/2,cy=y0+size/2,r=b.reach*size;
    // The board: a black disc for a jewel, a black annulus for the ring.
    g.fillStyle='#0f0f13';g.strokeStyle='#26262e';g.lineWidth=1.2;g.beginPath();g.arc(cx,cy,r+pkg*.85,0,7);
    if(b.ring){g.arc(cx,cy,Math.max(1,r-pkg*.85),0,7,true);}
    g.fill('evenodd');g.stroke();
    b.layout.pos.forEach((p,n)=>{
      const c=out[zone].pix[n],x=x0+p[0]*size,y=y0+p[1]*size,peak=LabLeds.luma(c),s=pkg*.5;
      g.fillStyle='#2a2a31';g.beginPath();g.roundRect(x-s,y-s,pkg,pkg,2);g.fill();
      g.fillStyle='#3d3d45';g.beginPath();g.arc(x,y,pkg*.34,0,7);g.fill();
      if(peak>.02){
        const hue=c.map(v=>v/peak),rgb=v=>v.map(q=>Math.round(q*255)).join(','),halo=g.createRadialGradient(x,y,1,x,y,pkg*1.7);
        halo.addColorStop(0,`rgba(${rgb(hue)},${.5*peak})`);halo.addColorStop(1,`rgba(${rgb(hue)},0)`);
        g.globalCompositeOperation='lighter';g.fillStyle=halo;g.beginPath();g.arc(x,y,pkg*1.7,0,7);g.fill();
        g.fillStyle=`rgba(${rgb(LabLeds.lens(c))},${Math.min(1,.3+peak)})`;g.beginPath();g.arc(x,y,pkg*.34,0,7);g.fill();
        g.globalCompositeOperation='source-over';
      }
      const differs=LED.diffs&&other&&LabLeds.apart(c,other[zone].pix[n])>.2,picked=LED.pick?.zone===zone&&LED.pick.n===n;
      if(differs||picked){g.strokeStyle=picked?'#fff':'#ff8a1a';g.lineWidth=picked?2:1.6;g.setLineDash(picked?[3,3]:[]);
        g.beginPath();g.arc(x,y,pkg*.78,0,7);g.stroke();g.setLineDash([]);}
    });
    if(!legible){return;}
    // The readout: which light, how bright, what it is running, and a dot that flashes with each hit.
    const lit=Math.round(LabLeds.luma(out[zone].avg)*100),fx=side?.state.eff[zone]||'',hit=side?Math.min(1,side.state.flash[zone]):0;
    g.font='14px system-ui';g.textAlign='center';g.fillStyle=hit>.05?'#ededf0':'#8b8b94';
    const text=`${names[zone]} ${lit}%${fx&&fx!=='off'?' · '+fx:''}`,tw=g.measureText(text).width;
    g.fillText(text,z*240+120,h-8);
    if(hit>.05){g.fillStyle=`rgba(255,138,26,${hit})`;g.beginPath();g.arc(z*240+120-tw/2-9,h-13,4,0,7);g.fill();}
  });
  if(legible){g.font='13px system-ui';g.textAlign='right';const a=LabLeds.amps(out);
    g.fillStyle=a>LabLeds.BUDGET_A?'#ffb27a':'#6b6b74';g.fillText(`≈${a.toFixed(2)} A`,w-8,17);}
}

// A whole song per side, played headless in slices between frames and kept
// per show, castle firmware and Soften setting, so switching back is instant.
function studyShows(){
  const gen=++LED.gen;LED.studies=[null,null];LED.barKey='';
  sides.forEach((side,i)=>{
    const key=side.fw+'|'+params.soft,have=STUDY.get(side.scene)?.get(key);
    if(have){LED.studies[i]=have;note(i);return;}
    const sim=makeSide(side.original);CastleCuePlayback.rebuild(sim.state,sim.scene,0);sim.at=0;
    const sizes=Object.fromEntries(order.map(z=>[z,board(z).layout.n]));
    const it=LabLeds.sweep({dur:duration,tick:TICK,every:4,buckets:BUCKETS,zones:order,sizes,
      step:t=>{while(sim.at+TICK<=t){sim.at+=TICK;CastleCuePlayback.fire(sim.state,sim.at);V.decayFlashes(sim.state);}},
      render:t=>V.renderZones(sim.state,t/1000,params)});
    const slice=()=>{
      if(gen!==LED.gen){return;}
      const until=performance.now()+12;let r=it.next();
      while(!r.done&&performance.now()<until){r=it.next();}
      if(!r.done){setTimeout(slice,0);return;}
      if(!STUDY.has(side.scene)){STUDY.set(side.scene,new Map());}
      STUDY.get(side.scene).set(key,r.value);LED.studies[i]=r.value;LED.barKey='';note(i);if(!playing){draw();}
    };
    setTimeout(slice,0);
  });
}
// The study's two numbers join the show's line: how many hues it holds, and its peak draw.
function note(i){
  const s=LED.studies[i],el=$('info-'+i);if(!s||sides[i].refused){return;}
  el.textContent=el.textContent.replace(/ · \d+ hues? · peak.*$/,'')+` · ${s.hues} hue${s.hues===1?'':'s'} · peak ≈${s.peak.toFixed(1)} A`;
  el.title=`Hues: how many of twelve 30° colour families this show holds for 2 s or more. Peak: the most the LEDs draw at once (≈, from the pixel colours; ${LabLeds.BUDGET_A} A is their share of the supply).`;
}

// The barcodes: under the timeline, one row per show, a lane per light (or
// the picked pixel alone), every bucket its average colour. Drawn once into
// a spare canvas and copied each frame.
function barRows(h){
  const row=(h-3)/2;
  return {row,lanes:!LED.pick&&row>=12?3:1};
}
// The lights a barcode row shows: the picked pixel's zone, all three, or one
// lane (null) carrying whichever light is brightest in each bucket.
function barZones(lanes){
  if(LED.pick){return [LED.pick.zone];}
  return lanes===3?order:[null];
}
function barColour(s,zone,k){
  if(zone){return LabLeds.colourAt(s,zone,k,LED.pick?.n);}
  return order.map(z=>LabLeds.colourAt(s,z,k)).reduce((m,q)=>LabLeds.luma(q)>LabLeds.luma(m)?q:m);
}
function drawBarcodes(g,w,y,h){
  if(blind||h<6||!LED.studies[0]||!LED.studies[1]){return;}
  const key=[w,h,LED.real,LED.pick?.zone,LED.pick?.n,LED.studies[0].peak,LED.studies[1].peak].join('|');
  if(key!==LED.barKey){
    const dpr=Math.min(devicePixelRatio||1,2),bar=LED.bar||document.createElement('canvas');LED.bar=bar;LED.barKey=key;
    bar.width=Math.round(w*dpr);bar.height=Math.round(h*dpr);const b=bar.getContext('2d');b.setTransform(dpr,0,0,dpr,0,0);
    const {row,lanes}=barRows(h),lane=(row-(lanes-1))/lanes;
    LED.studies.forEach((s,r)=>{
      if(!s){return;}
      barZones(lanes).forEach((zone,l)=>{
        const top=r*(row+3)+l*(lane+1);
        for(let k=0;k<s.buckets;k++){
          let c=barColour(s,zone,k);
          if(LED.real){c=LabLeds.trueLevel(c);}
          b.fillStyle=`rgb(${LabLeds.legible(c).map(v=>Math.round(v*255)).join(',')})`;b.fillRect(k/s.buckets*w,top,w/s.buckets+.6,lane);
        }
      });
    });
  }
  g.drawImage(LED.bar,0,y,w,h);
}
// What the pointer is over in a barcode, for the timeline's tooltip.
function barcodeHint(y,t,h){
  const bh=h>=60?30:14,top=bh+16,rows=barRows(h-bh-18); // drawTimeline's rows
  if(blind||y<top||!LED.studies[0]||!LED.studies[1]){return '';}
  const r=Math.min(1,Math.floor((y-top)/(rows.row+3))),s=LED.studies[r],k=Math.min(s.buckets-1,Math.floor(t/duration*s.buckets));
  const lane=Math.max(0,Math.min(rows.lanes-1,Math.floor((y-top-r*(rows.row+3))/(rows.row/rows.lanes))));
  const zone=barZones(rows.lanes)[rows.lanes===3?lane:0],who=r?$('title-1').textContent:'Current prepared show';
  const c=barColour(s,zone,k);
  let what=zone?names[zone]:'brightest light';
  if(LED.pick){what=`${names[zone]} pixel ${LED.pick.n+1}`;}
  return `${who} · ${what} at ${fmt(t)}: ${LabLeds.hex(c)}, ${Math.round(LabLeds.luma(c)*100)}%`;
}

// Called by the page script once its globals exist.
function ledsInit(){
// Click a pixel to follow it alone in the barcodes; click it again, or empty board, to let go.
document.querySelectorAll('canvas.pixels').forEach(canvas=>{canvas.addEventListener('click',e=>{
  const r=canvas.getBoundingClientRect(),x=(e.clientX-r.left)*720/r.width,y=(e.clientY-r.top)*tall/r.height;
  let best=null;
  order.forEach((zone,z)=>{const {b,size,x0,y0,pkg}=place(z,zone);b.layout.pos.forEach((p,n)=>{
    const d=Math.hypot(x-(x0+p[0]*size),y-(y0+p[1]*size));if(d<pkg*1.1&&(!best||d<best.d)){best={zone,n,d};}});});
  if(!best&&!LED.pick){return;} // a phone's tap on empty board still swaps the view
  LED.pick=best&&!(LED.pick?.zone===best.zone&&LED.pick.n===best.n)?{zone:best.zone,n:best.n}:null;
  e.stopPropagation();if(!playing){draw();}
});});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&LED.pick&&$('flag-sheet').hidden){LED.pick=null;if(!playing){draw();}}});
$('real-leds').onchange=e=>{LED.real=e.target.checked;localStorage.setItem('castle.lab.realLeds',LED.real?'1':'');if(!playing){draw();}};
$('diffs').onchange=e=>{LED.diffs=e.target.checked;if(!playing){draw();}};
LED.real=$('real-leds').checked=localStorage.getItem('castle.lab.realLeds')==='1';
}
