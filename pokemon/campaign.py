"""Persistent story intent and observed navigation memory; JEV still chooses input.

Route suggestions are deterministic BFS over observed background cells and
version-labelled map priors. They are disclosed guidance, never executed here.
"""
from __future__ import annotations
from collections import deque
from copy import deepcopy
import hashlib
from campaign_knowledge import objective_for
from world_data import map_prior
from route_regions import plan_route, interaction_positions
from team_strategy import plan_support

DIRECTIONS={'up':(0,-1),'down':(0,1),'left':(-1,0),'right':(1,0)}
COMPASS={'north':'up','south':'down','west':'left','east':'right'}


def position(observation):
    world=observation.get('world') or {}
    if world.get('source_match') is False or world.get('player_position_valid') is False:return None
    p=observation.get('player') or {}
    values=tuple(p.get(k) for k in ('map_id','x','y'))
    return values if all(type(v) is int for v in values) else None


def cell_key(x,y):return f'{x},{y}'

def trustworthy(value):
    return isinstance(value,dict) and value.get('verified') is True


class CampaignPlanner:
    def __init__(self,snapshot=None,selector=None):
        data=snapshot or {}
        if data.get('version',1)!=1:raise ValueError('Unsupported campaign memory version')
        self.tiles=deepcopy(data.get('tiles',{}))
        self.visits=deepcopy(data.get('visits',{}))
        self.transitions=deepcopy(data.get('transitions',[]))[-256:]
        self.clues=deepcopy(data.get('clues',[]))[-80:]
        self.seen_clues=list(data.get('seen_clues',[clue['id'] for clue in self.clues]))[-4096:]
        self.history_facts=deepcopy(data.get('history_facts',{}))
        self.active=deepcopy(data.get('active'))
        self.support=deepcopy(data.get('support'))
        self.objective_history=deepcopy(data.get('objective_history',[]))[-80:]
        self.failed_edges=deepcopy(data.get('failed_edges',{}))
        self.failed_edge_steps=deepcopy(data.get('failed_edge_steps',{}))
        self.steps=int(data.get('steps',0))
        self._last_position=data.get('last_position')
        self.selector=selector or objective_for

    def _observe(self,observation):
        p=position(observation)
        if p is None:return False
        new_clue=False
        map_id,x,y=p;key=str(map_id);world=observation.get('world') or {}
        width=world.get('width');height=world.get('height')
        current=(observation.get('local_map') or {})
        cells=self.tiles.setdefault(key,{})
        if (observation.get('scene') or {}).get('mode')=='overworld' and current.get('verified') and world.get('source_match',True) and world.get('player_position_valid',True):
            center=current.get('player_cell',{'x':4,'y':4})
            for gy,row in enumerate(current.get('rows',[])):
                for gx,tile in enumerate(row):
                    tx=x+gx-center['x'];ty=y+gy-center['y']
                    if tx<0 or ty<0 or (type(width) is int and tx>=width) or (type(height) is int and ty>=height):continue
                    if tile in '.#@':cells[cell_key(tx,ty)]={'passable':tile!='#','source':'observed_background'}
        cells.setdefault(cell_key(x,y),{'passable':True,'source':'observed_player_position'})
        for name,fact in (observation.get('milestones') or {}).items():
            # Store positive verified story evidence, not unverified source bits.
            # Current inventory/HP are intentionally not made permanent.
            if trustworthy(fact) and fact.get('value') is True and not name.startswith('elite4_') and name not in ('champion_defeated','party_fully_healed'):
                self.history_facts[name]=deepcopy(fact)
        dialog=observation.get('dialog') or {}
        if dialog.get('open') is True and dialog.get('text'):
            text=' '.join(dialog['text'].split())[:600]
            fingerprint=hashlib.sha256(f'{map_id}:{text}'.encode()).hexdigest()[:20]
            if fingerprint not in self.seen_clues:
                self.seen_clues.append(fingerprint);self.seen_clues=self.seen_clues[-4096:]
                self.clues.append({'id':fingerprint,'map_id':map_id,'position':[x,y],'text':text,'source':'observed_dialog'})
                self.clues=self.clues[-80:]
                new_clue=True
        return new_clue

    def record(self,button,before,after):
        a,b=position(before),position(after);self.steps+=1
        self._observe(before);new_clue=self._observe(after)
        if b:
            key=f'{b[0]}:{b[1]},{b[2]}'
            self.visits[key]=self.visits.get(key,0)+1
            self._last_position=list(b)
        if a and b and a[0]!=b[0]:
            control=(before.get('world') or {}).get('input_lock') or {}
            manual=button in DIRECTIONS and (before.get('scene') or {}).get('mode')=='overworld' and not control.get('ignored_buttons_mask',0) and not control.get('scripted_movement_remaining',0)
            edge={'from_map':a[0],'from_position':[a[1],a[2]],'button':button,
                  'to_map':b[0],'arrival':[b[1],b[2]],'source':'observed_map_transition','step':self.steps,
                  'kind':'walk' if manual else 'script_or_battle'}
            if edge not in self.transitions:self.transitions.append(edge)
            self.transitions=self.transitions[-256:]
        if a and b and button in DIRECTIONS and (before.get('scene') or {}).get('mode')=='overworld':
            key=f'{a[0]}:{a[1]},{a[2]}:{button}'
            lock=(before.get('world') or {}).get('input_lock') or {}
            facing_changed=(before.get('player') or {}).get('facing')!=(after.get('player') or {}).get('facing')
            stable_scene=(after.get('scene') or {}).get('mode')=='overworld'
            if a==b and stable_scene and not facing_changed and not lock.get('ignored_buttons_mask',0):
                self.failed_edges[key]=self.failed_edges.get(key,0)+1
                self.failed_edge_steps[key]=self.steps
            elif a!=b:
                self.failed_edges.pop(key,None);self.failed_edge_steps.pop(key,None)
        # Moving NPCs and scripted locks must not poison a route for the rest
        # of the campaign. Old failures are retriable, never permanent walls.
        for key in list(self.failed_edges):
            if self.steps-self.failed_edge_steps.get(key,0)>64:
                self.failed_edges.pop(key,None);self.failed_edge_steps.pop(key,None)
        while len(self.visits)>20000:del self.visits[next(iter(self.visits))]
        return {'new_dialog_clue':new_clue}

    def _target_for(self,observation,objective):
        p=position(observation);world=observation.get('world') or {}
        if p is None:return None,[]
        mid,x,y=p;target_map=objective.get('target_map_id')
        if type(target_map) is not int:return None,[]
        selectors=objective.get('interaction_selectors') or objective.get('target')
        routing=plan_route(world,observation.get('player') or {},target_map,
                           target=selectors,facts=observation.get('milestones'))
        route=routing.get('map_route') or []
        if routing.get('status')=='planned':
            return {**routing['next_transition'],'route_quality':routing['quality'],
                    'route_limitations':routing.get('limitations')},route
        if routing.get('status')!='same_region':
            return {'kind':'unknown_route','destination_map':target_map,
                    'reason':routing.get('reason'),'quality':routing.get('quality'),
                    'limitations':routing.get('limitations')},route
        targets=objective.get('interaction_selectors') or ([objective.get('target')] if objective.get('target') else [])
        choices=[]
        for target in targets:
            if not isinstance(target,dict):continue
            if target.get('kind')=='object':
                matches=[obj for obj in world.get('objects',[]) if obj.get('active') is not False
                         and (target.get('text_id') is None or obj.get('text_id')==target['text_id'])
                         and (target.get('sprite') is None or obj.get('sprite')==target['sprite'])]
                for obj in matches:choices.append({**deepcopy(target),**deepcopy(obj),'kind':'object','object_category':obj.get('kind')})
                hidden=any(obj.get('active') is False
                           and (target.get('text_id') is None or obj.get('text_id')==target['text_id'])
                           and (target.get('sprite') is None or obj.get('sprite')==target['sprite'])
                           for obj in world.get('objects',[]))
                if not matches and not hidden and type(target.get('x')) is int:choices.append(deepcopy(target))
            else:choices.append(deepcopy(target))
        if not choices:return None,route or [mid]
        return min(choices,key=lambda t:abs(t.get('x',x)-x)+abs(t.get('y',y)-y)),route or [mid]

    def _navigation(self,observation,target):
        p=position(observation);world=observation.get('world') or {}
        if world.get('source_match') is False or world.get('player_position_valid') is False:
            return {'status':'wait_for_map_transition','next_button':'wait','target':target}
        if p is None or not target:return {'status':'needs_target','next_button':None}
        mid,x,y=p;kind=target.get('kind');width=world.get('width');height=world.get('height')
        if kind=='unknown_route':return {'status':'needs_map_connection','next_button':None,'target':target}
        phase=(observation.get('scene') or {}).get('mode')
        if phase!='overworld':return {'status':'resolve_current_ui','next_button':None,'target':target}
        lock=world.get('input_lock') or {}
        if lock.get('ignored_buttons_mask')==255 or lock.get('scripted_movement_remaining',0)>0:
            return {'status':'script_controls_player','next_button':'wait','target':target,'source':'live game input lock and scripted movement'}
        if type(width) is not int or type(height) is not int:return {'status':'needs_geometry','next_button':None}
        goals=set();activation=None;approach_facings={}
        if kind=='connection':
            direction=COMPASS.get(target.get('direction'));activation=direction
            if type(target.get('x')) is int and type(target.get('y')) is int:goals={(target['x'],target['y'])}
            elif direction=='up':goals={(cx,0) for cx in range(width)}
            elif direction=='down':goals={(cx,height-1) for cx in range(width)}
            elif direction=='left':goals={(0,cy) for cy in range(height)}
            elif direction=='right':goals={(width-1,cy) for cy in range(height)}
        else:
            tx,ty=target.get('x'),target.get('y')
            if type(tx) is not int or type(ty) is not int:return {'status':'needs_target_coordinate','target':target,'next_button':None}
            if kind=='object':
                approaches=interaction_positions(mid,target)
                goals={(row['x'],row['y']) for row in approaches} or {(tx+dx,ty+dy) for dx,dy in DIRECTIONS.values()}
                approach_facings={(row['x'],row['y']):row['facing'] for row in approaches}
            elif target.get('trigger') and 'y' in target['trigger']:
                goals={(cx,target['trigger']['y']) for cx in range(width)}
            else:goals={(tx,ty)}
            if kind=='warp':
                activation='up' if ty==0 else 'down' if ty==height-1 else 'left' if tx==0 else 'right' if tx==width-1 else None
            elif kind=='observed_transition':activation=target.get('button')
            elif kind=='ledge':activation=target.get('button')
        cells=self.tiles.get(str(mid),{})
        blocked={(o['x'],o['y']) for o in world.get('objects',[]) if o.get('active') is True and type(o.get('x')) is int and type(o.get('y')) is int}
        if kind=='object':blocked.add((target['x'],target['y']))
        start=(x,y)
        if start in goals:
            if kind=='object':
                delta=(target['x']-x,target['y']-y);facing=approach_facings.get(start) or next((d for d,v in DIRECTIONS.items() if v==delta),None)
                expected='a' if (observation.get('player') or {}).get('facing')==facing else facing
                return {'status':'interact' if expected=='a' else 'face_object','next_button':expected,'target':target,'source':'target geometry, source-labelled counter interaction cells and observed facing'}
            if activation:return {'status':'activate_transition','next_button':activation,'target':target,'source':'standing on observed/source-labelled map boundary'}
            if kind=='warp':
                for direction,(dx,dy) in DIRECTIONS.items():
                    adjacent=(x+dx,y+dy)
                    if adjacent not in blocked and cells.get(cell_key(*adjacent),{}).get('passable'):
                        return {'status':'reenter_warp','next_button':direction,'target':target,
                                'source':'already standing on an interior warp; step off before re-entering, and inspect the actual result'}
            return {'status':'at_target','next_button':'wait','target':target,'source':'coordinate/script-trigger target reached; observe its effect'}
        queue=deque([start]);parents={start:None};moves={};found=None
        while queue:
            at=queue.popleft()
            if at in goals:found=at;break
            for direction,(dx,dy) in DIRECTIONS.items():
                nxt=(at[0]+dx,at[1]+dy)
                if nxt in parents or nxt in blocked or not (0<=nxt[0]<width and 0<=nxt[1]<height):continue
                if not cells.get(cell_key(*nxt),{}).get('passable'):continue
                if self.failed_edges.get(f'{mid}:{at[0]},{at[1]}:{direction}',0)>=2:continue
                parents[nxt]=at;moves[nxt]=direction;queue.append(nxt)
        status='path_to_target'
        if found is None:
            reachable=[c for c in parents if c!=start]
            if not reachable:return {'status':'no_observed_path','next_button':None,'target':target,'source':'observed background BFS; request re-observation/replan'}
            def distance(c):return min(abs(c[0]-g[0])+abs(c[1]-g[1]) for g in goals) if goals else 0
            frontier=[c for c in reachable if any(cell_key(c[0]+dx,c[1]+dy) not in cells and 0<=c[0]+dx<width and 0<=c[1]+dy<height for dx,dy in DIRECTIONS.values())]
            candidates=frontier or reachable
            found=min(candidates,key=lambda c:(distance(c),self.visits.get(f'{mid}:{c[0]},{c[1]}',0),abs(c[0]-x)+abs(c[1]-y)))
            status='toward_unseen_target' if frontier else 'closest_observed_approach'
        path=[];node=found
        while parents[node] is not None:path.append({'x':node[0],'y':node[1],'button':moves[node]});node=parents[node]
        path.reverse()
        return {'status':status,'next_button':path[0]['button'] if path else None,'next_waypoint':path[0] if path else None,
                'path_preview':path[:8],'target':target,'source':'deterministic BFS guidance over observed background; JEV independently selects the actual input',
                'limitations':'Unknown terrain and moving NPCs may invalidate this guidance; inspect every result.'}

    def context(self,observation):
        self._observe(observation)
        world=observation.get('world') or {}
        facts=dict(observation.get('milestones') or {})
        completed=facts.get('game_completed') or self.history_facts.get('game_completed')
        if trustworthy(completed) and completed.get('value') is True:
            facts['hall_of_fame_entered']=deepcopy(completed)
        objective=self.selector(facts,world,{'facts':self.history_facts})
        story_objective=deepcopy(objective)
        if objective.get('id')!='main_story_complete':
            self.support=plan_support(observation,self.support)
            if self.support:
                objective={**self.support,'story_objective_id':story_objective['id'],
                    'completed_ids':story_objective.get('completed_ids',[]),
                    'completion_evidence':{'party_fully_healed':{
                        'value':self.support['evidence']['full_recovery'],
                        'verified':self.support['evidence']['full_recovery'] is not None,
                        'quality':'verified' if self.support['evidence']['full_recovery'] is not None else 'needs_data',
                        'source':'current valid party HP, status and actual ROM-verified maximum PP'}}}
        previous=self.active.get('id') if self.active else None
        if objective.get('id')!=previous:
            self.objective_history.append({'step':self.steps,'from':previous,'to':objective.get('id'),'reason':'verified facts evaluated against story prerequisites'})
            self.objective_history=self.objective_history[-80:]
        self.active=deepcopy(objective)
        target,route=self._target_for(observation,objective)
        navigation=self._navigation(observation,target)
        return {'overall_goal':'Defeat the League Champion and register in the Hall of Fame',
                'active_objective':objective,'navigation':navigation,
                'story_objective':{'id':story_objective.get('id'),'intent':story_objective.get('intent')},
                'visited_map_ids':sorted(int(mid) for mid,cells in self.tiles.items() if cells),
                'recorded_action_count':self.steps,
                'route_map_ids':route,'route_map_names':[(map_prior(mid) or {}).get('name',str(mid)) for mid in route],
                'observed_map_connections':self.transitions[-24:],
                'relevant_dialog_clues':[c for c in self.clues if c['map_id']==world.get('map_id')][-5:],
                'objective_history':self.objective_history[-6:],
                'knowledge_policy':'Version-pinned source supplies labelled priors; only verified in-game facts complete objectives. Guidance does not execute buttons.',
                'roles':{'story':'data-driven prerequisite planner','navigation':'observed-background BFS guidance','physical_input':'JEV choice'}}

    def snapshot(self):
        return deepcopy({'version':1,'tiles':self.tiles,'visits':self.visits,'transitions':self.transitions,
            'clues':self.clues,'seen_clues':self.seen_clues,'history_facts':self.history_facts,'active':self.active,'support':self.support,'objective_history':self.objective_history,
            'failed_edges':self.failed_edges,'failed_edge_steps':self.failed_edge_steps,
            'steps':self.steps,'last_position':self._last_position})
