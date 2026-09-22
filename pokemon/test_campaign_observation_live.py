"""Isolated scripted RAM regression fixture, never an agent policy."""
import sys,json,hashlib,collections,argparse,tempfile,shutil
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory import Reader,load_profile
from emulator import Emulator

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--checkpoint-dir',type=Path)
    args=parser.parse_args()
    state=args.state
    original=state.read_bytes()
    assert hashlib.sha256(original).hexdigest()=='378853bf409dedb3ea85acc0c5c3df5e24195d500633a560ec8bee0b004283b9'
    temporary=tempfile.TemporaryDirectory(prefix='pokemon-campaign-regression-')
    copy=Path(temporary.name)/'input.state'
    shutil.copyfile(state,copy)
    b=copy.read_bytes()
    e=Emulator(args.rom,load_profile());e.load(b);r=Reader(e,load_profile())
    report={'input_sha256':hashlib.sha256(b).hexdigest(),'jev_calls':0,'production_actions':0,'ram_writes':0,'screenshots':0,'policy':'isolated_scripted_regression_not_jev_or_production', 'created_at':datetime.now(timezone.utc).isoformat(),'rom_sha1':load_profile()['rom_sha1'],'source_commit':load_profile()['source_commit'],'source_binary_match':False,'terminal_validation':'ROM counter instruction confirmed; live Hall of Fame registration NOT reached','checks':{}}
    def snap():return r.snapshot()
    def save(name):
     s=snap()
     assert not s['errors'],s['errors']
     report['checks'][name]={key:s[key] for key in ('player','world','scene','dialog','party','bag','battle','milestones')}
     report['checks'][name]['screen_text_nonblank']=[line for line in s['screen_text']['rows'] if line]
     if args.checkpoint_dir:
      args.checkpoint_dir.mkdir(parents=True,exist_ok=True)
      (args.checkpoint_dir/(name+'.state')).write_bytes(e.save())
     print(name, s['player']['map_id'],s['player']['x'],s['player']['y'],s['scene']['mode'],flush=True)
    def press(k):e.press(k,16,90)
    def dialog_close(maximum=60):
     for _ in range(maximum):
      s=snap()
      if s['scene']['mode']=='overworld':return
      if s['battle']['active']:return
      press('a')
     raise RuntimeError('dialog did not close')
    known={};delta={'up':(0,-1),'down':(0,1),'left':(-1,0),'right':(1,0)}
    def go(mapid,target,limit=150):
     for _ in range(limit):
      s=snap();p=s['player'];mid=p['map_id'];start=(p['x'],p['y'])
      if mid!=mapid:return
      if start==target:return
      if s['scene']['mode']!='overworld':
       if s['battle']['active']:
        for _ in range(150):
         if snap()['battle'].get('menu')=='command' and 'rival_battle_live' not in report['checks']:save('rival_battle_live')
         press('a')
         if not snap()['battle']['active']:break
        continue
       press('a');continue
      grid=s['local_map']['rows'];cells=known.setdefault(mid,{})
      for y,row in enumerate(grid):
       for x,char in enumerate(row):
        pos=(start[0]+x-4,start[1]+y-4)
        if 0<=pos[0]<s['world']['width'] and 0<=pos[1]<s['world']['height']:cells[pos]=char!='#'
      occupied={(o['x'],o['y']) for o in s['world']['objects'] if o['present'] is True}
      blocked_warps={(w['x'],w['y']) for w in s['world']['warps'] if (w['x'],w['y'])!=target}
      queue=collections.deque([(start,[])]);seen={start};best=None
      while queue:
       pos,path=queue.popleft()
       if pos==target:best=path;break
       for k,(dx,dy) in delta.items():
        nxt=(pos[0]+dx,pos[1]+dy)
        if nxt not in seen and cells.get(nxt) and nxt not in occupied and nxt not in blocked_warps:
         seen.add(nxt);queue.append((nxt,path+[k]))
      if best is None:
       # Reach the observed frontier closest to target to expose more map.
       candidates=[p for p in seen if p!=start];candidate=min(candidates,key=lambda p:abs(p[0]-target[0])+abs(p[1]-target[1])) if candidates else None
       if candidate is None:raise RuntimeError(('no route',start,target,s['world']))
       queue=collections.deque([(start,[])]);seen={start}
       while queue:
        pos,path=queue.popleft()
        if pos==candidate:best=path;break
        for k,(dx,dy) in delta.items():
         nxt=pos[0]+dx,pos[1]+dy
         if nxt not in seen and cells.get(nxt) and nxt not in occupied and nxt not in blocked_warps:seen.add(nxt);queue.append((nxt,path+[k]))
      if not best:raise RuntimeError(('empty path',start,target))
      press(best[0]);a=snap()['player']
      if (a['map_id'],a['x'],a['y'])==(mid,*start):
       dx,dy=delta[best[0]];cells[(start[0]+dx,start[1]+dy)]=False
     raise RuntimeError(('navigation budget',mapid,target,snap()['player']))
    try:
     save('initial')
     if snap()['player']['map_id']==38:
      go(38,(7,1));save('house1f')
     if snap()['player']['map_id']==37:
      go(37,(2,7));press('down');save('pallet')
     if snap()['player']['map_id']==0:
      go(0,(10,1));save('oak_trigger');
      for _ in range(70):
       press('a')
       if snap()['player']['map_id']==40 and snap()['scene']['mode']=='overworld':break
      dialog_close();save('oak_lab')
     if snap()['player']['map_id']==40 and not snap()['party']:
      dialog_close();go(40,(7,4));press('up');press('a');save('starter_prompt')
      for _ in range(30):
       press('a')
       if snap()['party']:break
      save('starter_received');
      for _ in range(25):press('a')
      dialog_close();save('after_starter_text')
      try:go(40,(4,11))
      except RuntimeError as ex:
       if not snap()['battle']['active']:raise
      for _ in range(25):
       press('a')
       if snap()['battle'].get('verified') and snap()['battle'].get('menu')=='command':break
      save('rival_battle')
      for _ in range(120):
       press('a')
       if not snap()['battle']['active'] and snap()['milestones']['rival_lab_battled']['value']:break
      save('after_rival')
      dialog_close()
      for _ in range(12):press('wait')
      go(40,(4,11));press('down');save('pallet_after_lab')
      go(0,(10,0));press('up');save('route1')
      go(12,(10,0),limit=300);press('up');save('viridian')
      go(1,(29,19),limit=150);save('mart_entered')
      for _ in range(50):
       press('a')
       if snap()['milestones']['oak_parcel_received']['value']:break
      save('parcel_received')
      dialog_close();go(42,(3,7));press('down');save('viridian_return')
      go(1,(20,35));press('down');save('route1_return')
      go(12,(10,35),limit=300);press('down');save('pallet_return')
      go(0,(12,11));save('lab_return');dialog_close()
      go(40,(5,3));press('up');press('a')
      for _ in range(60):
       press('a')
       if snap()['milestones']['pokedex_received']['value']:break
      save('pokedex_received')
    except Exception as ex:report['error']=repr(ex);save('failure');print('ERROR',repr(ex),flush=True)
    finally:
     args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n');e.close();temporary.cleanup();assert hashlib.sha256(state.read_bytes()).hexdigest()==report['input_sha256']

    assert 'error' not in report,report.get('error')
    assert report['checks']['rival_battle_live']['battle']['verified']
    assert report['checks']['rival_battle_live']['battle']['menu']=='command'
    assert report['checks']['parcel_received']['bag'][0]['item_id']==70
    for key in ('oak_appeared_in_pallet','followed_oak_into_lab','oak_asked_to_choose_mon','starter_received','rival_lab_battled','oak_parcel_received','parcel_delivered','pokedex_received'):
     assert report['checks']['initial']['milestones'][key]['value'] is False,key
     assert report['checks']['pokedex_received']['milestones'][key]['value'] is True,key
    assert report['checks']['pokedex_received']['bag']==[]
    assert report['checks']['pokedex_received']['milestones']['game_completed']['value'] is None
    print('Verified isolated map, hidden sprites, control lock, starter, rival, parcel and Pokedex observations; production state unchanged.')


if __name__ == "__main__":
    main()
