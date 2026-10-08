"""Native pad-aware USB route lengths; current copper geometry only."""
from pathlib import Path
import pcbnew as p,json,math,heapq,os,sys
A=Path.cwd()/'out'; A.mkdir(exist_ok=True);ROOT=Path.cwd()
b=p.LoadBoard(str(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'castle-carrier.kicad_pcb'));fps={f.GetReference():f for f in b.GetFootprints()}
xy=lambda q:(q.x,q.y)
report={}
for net,pin,targets in [('USB_DP','14',['A6','B6']),('USB_DM','13',['A7','B7'])]:
 graph={};pads=[q for f in fps.values() for q in f.Pads() if q.GetNetname()==net]
 def edge(a,z,length):graph.setdefault(a,[]).append((length,z));graph.setdefault(z,[]).append((length,a))
 for t in b.GetTracks():
  if t.GetNetname()==net and not isinstance(t,p.PCB_VIA):edge(xy(t.GetStart()),xy(t.GetEnd()),t.GetLength()/1e6)
 for q in pads:graph.setdefault(xy(q.GetPosition()),[])
 links=[]
 for q in pads:
  nodes=[point for point in list(graph) if q.HitTest(p.VECTOR2I(*point))]
  for i,a in enumerate(nodes):
   for z in nodes[i+1:]:
    if a!=z:
     length=math.dist(a,z)/1e6;edge(a,z,length);links.append({'reference':q.GetParentFootprint().GetReference(),'pad':q.GetNumber(),'length_mm':length})
 def route(start,end):
  queue=[(0,start)];seen=set()
  while queue:
   length,node=heapq.heappop(queue)
   if node==end:return length
   if node in seen:continue
   seen.add(node)
   for w,n in graph.get(node,[]):heapq.heappush(queue,(length+w,n))
  raise AssertionError('Disconnected USB path')
 pad=lambda ref,num:next(q for q in fps[ref].Pads() if q.GetNumber()==num)
 report[net]={'routes_mm':{end:route(xy(pad('U2',pin).GetPosition()),xy(pad('J16',end).GetPosition())) for end in targets},'pad_metal_graph_links':links}
report['orientation_pair_mismatch_mm']={'A_contacts':abs(report['USB_DP']['routes_mm']['A6']-report['USB_DM']['routes_mm']['A7']),'B_contacts':abs(report['USB_DP']['routes_mm']['B6']-report['USB_DM']['routes_mm']['B7'])}
report['scope']='Shortest copper centerline routes including native pad-area joins. No eye diagram, field solver, USB connector/contact model or runtime enumeration test.'
report['target_mm']=1.0
report['status']='PASS' if max(report['orientation_pair_mismatch_mm'].values())<=1.0 else 'FAIL'
(A/'usb-route-check.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));sys.stdout.flush();os._exit(0 if report['status']=='PASS' else 1)
