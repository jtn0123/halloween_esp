/* Prepared card cues replace a flash; desk gestures accumulate one.
 * Mirrors firmware/castle_cues.h apply(), leaving the shared pixel renderer
 * and authored desk interaction unchanged. No audio or device operations.
 *
 * Cue format v2 (firmware v5.71), all through the desk's own copy of
 * firmware/castle_layers.h that visuals.js carries:
 *  - a strike with layer 1 lands on the ornament (state.x[zone].orn*),
 *    which the renderer ADDS to layer 0 instead of replacing it;
 *  - masks 4-7 are the left/right/top/bottom halves;
 *  - a look cue sets overlay/palette/centre and the overlay clock;
 *  - every strike books itself on its zone (noteStrike), and only one that
 *    lands < 333 ms after that zone's previous strike is softened.
 * Change this, castle_cues.h and web/src/show_layers.ts together
 * (docs/PARITY.md).
 */
(function(root){
  const modes={all:0,scatter:1,center:2,ring:3,left:4,right:5,top:6,bottom:7,
    arc0:8,arc1:9,arc2:10,arc3:11,arc4:12,arc5:13,arc6:14,arc7:15};
  function land(state,cue,zone){
    const V=root.CastleVisuals;
    const x=state.x[zone];
    // state.softAll (the light-show lab only) renders today's v5.70 castle,
    // whose soften dimmed EVERY strike: every strike counts as a train.
    const train=state.softAll===true||V.noteStrike(x,cue.t);
    const orn=cue.layer===1;
    const mode=modes[cue.pixels]??0;
    if(orn){
      if(cue.attack>0){x.ornTarget=cue.intensity;x.ornRise=cue.intensity*16/cue.attack;}
      else{x.ornFlash=cue.intensity;x.ornTarget=0;}
      x.ornCol=cue.color;x.ornDecay=cue.decay;x.ornMode=mode;
      x.ornEpoch=(x.ornEpoch+1)%1000;x.train1=train;
      return;
    }
    if(cue.attack>0){
      state.flashTarget[zone]=cue.intensity;
      state.flashRise[zone]=cue.intensity*16/cue.attack;
    }else{
      state.flash[zone]=cue.intensity;
      state.flashTarget[zone]=0;
    }
    state.flashCol[zone]=cue.color;
    state.flashDecay[zone]=cue.decay;
    state.flashMode[zone]=mode;
    state.flashEpoch[zone]=(state.flashEpoch[zone]+1)%1000;
    x.train0=train;
  }
  function fire(state,elapsed){
    state.scene.cues.forEach((cue,index)=>{
      if(cue.t>elapsed||state.fired.has(index)){return;}
      state.fired.add(index);
      if(cue.op==='set'){
        state.eff[cue.zone]=cue.eff;
        if(cue.level!==undefined){state.level[cue.zone]=cue.level;}
        return;
      }
      if(cue.op==='look'){look(state,cue);return;}
      if(cue.op!=='strike'){return;}
      for(const zone of cue.targets){land(state,cue,zone);}
    });
  }
  // The light-show lab only: a look record's `glow` is a resting glow in
  // any colour, a pole pair no card can carry (spectrum_glow.py). The page
  // draws it through a preview-only palette (effects.ts previewPalette).
  function look(state,cue){
    const V=root.CastleVisuals;
    if(!cue.glow){V.applyLook(state,cue);return;}
    const at=V.previewPalette(cue.glow[0],cue.glow[1]);
    for(const zone of cue.targets){state.palette[zone]=at;}
  }
  // A seek: the renderer's own rebuild, then every palette and glow up to
  // `t` again in order, since the renderer's rebuild knows no glow.
  function rebuild(state,scene,t){
    root.CastleVisuals.rebuildLightsAt(state,scene,t);
    if(!scene.cues.some(c=>c.glow)){return;}
    for(const cue of scene.cues){
      if(cue.t>t){break;}
      if(cue.op==='look'&&cue.glow){look(state,cue);}
      else if(cue.op==='look'&&cue.palette!==undefined){
        const at=root.CastleVisuals.paletteIndex(cue.palette);
        for(const zone of cue.targets??['towerL','towerR','door']){state.palette[zone]=at;}
      }
    }
  }
  root.CastleCuePlayback={fire,rebuild};
})(globalThis);
