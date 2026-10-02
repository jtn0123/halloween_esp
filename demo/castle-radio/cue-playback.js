/* Prepared card cues replace a flash; desk gestures accumulate one.
 * Mirrors firmware/castle_cues.h apply(), leaving the shared pixel renderer
 * and authored desk interaction unchanged. No audio or device operations.
 */
(function(root){
  const modes={all:0,scatter:1,center:2,ring:3};
  function fire(state,elapsed){
    state.scene.cues.forEach((cue,index)=>{
      if(cue.t>elapsed||state.fired.has(index)){return;}
      state.fired.add(index);
      if(cue.op==='set'){
        state.eff[cue.zone]=cue.eff;
        if(cue.level!==undefined){state.level[cue.zone]=cue.level;}
        return;
      }
      if(cue.op!=='strike'){return;}
      for(const zone of cue.targets){
        if(cue.attack>0){
          state.flashTarget[zone]=cue.intensity;
          state.flashRise[zone]=cue.intensity*16/cue.attack;
        }else{
          state.flash[zone]=cue.intensity;
          state.flashTarget[zone]=0;
        }
        state.flashCol[zone]=cue.color;
        state.flashDecay[zone]=cue.decay;
        state.flashMode[zone]=modes[cue.pixels]??0;
        state.flashEpoch[zone]=(state.flashEpoch[zone]+1)%1000;
      }
    });
  }
  root.CastleCuePlayback={fire};
})(globalThis);
