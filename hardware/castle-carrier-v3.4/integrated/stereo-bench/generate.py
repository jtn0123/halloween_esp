"""Two-channel channel-identification fixture; no device access or flashing."""
from pathlib import Path
import math,array,wave,subprocess,json,hashlib
p=Path(__file__).resolve().parent.parent/'stereo-bench';p.mkdir(exist_ok=True)
sr=44100;duration=9;data=array.array('h')
# 0..2 seconds left only; 3..5 right only; 6..8 identical both.
for i in range(sr*duration):
 t=i/sr;local=t%3
 env=min(1,local/.03,max(0,(2-local)/.03)) if local<2 else 0
 val=int(6500*env*math.sin(2*math.pi*(440 if t<3 or t>=6 else 660)*t))
 data.extend((val if t<3 or t>=6 else 0,val if t>=3 else 0))
with wave.open(str(p/'channel-identification.wav'),'wb') as f:f.setnchannels(2);f.setsampwidth(2);f.setframerate(sr);f.writeframes(data.tobytes())
subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(p/'channel-identification.wav'),'-ar','44100','-ac','2','-codec:a','libmp3lame','-b:a','128k',str(p/'channel-identification.mp3')],check=True)
# Verify the encoded/decoded fixture preserves separated channel energy.
r=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i',str(p/'channel-identification.mp3'),'-f','s16le','-acodec','pcm_s16le','-'],check=True,capture_output=True)
a=array.array('h');a.frombytes(r.stdout);checks={};energies={}
for name,t0,t1,expected in [('left_only',.25,1.75,0),('right_only',3.25,4.75,1),('same_both',6.25,7.75,None)]:
 e=[sum(float(a[i*2+c])**2 for i in range(int(t0*sr),int(t1*sr)))/(int((t1-t0)*sr)) for c in [0,1]];energies[name]=e
 checks[name]=(min(e)/max(e)<.001 and e[expected]>10000) if expected is not None else abs(e[0]-e[1])/max(e)<.01
report={'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'decoded_channel_energy':energies,'audio':{'channels':2,'sample_rate':sr,'sequence_seconds':{'0-2':'LEFT only, 440 Hz','2-3':'silence','3-5':'RIGHT only, 660 Hz','5-6':'silence','6-8':'same 440 Hz on both','8-9':'silence'}},'physical_playback':'NOT_PERFORMED','mp3_sha256':hashlib.sha256((p/'channel-identification.mp3').read_bytes()).hexdigest()}
(p/'fixture-check.json').write_text(json.dumps(report,indent=2)+'\n');assert report['status']=='PASS';print('Verified stereo bench fixture: left only, right only, then identical both.')
