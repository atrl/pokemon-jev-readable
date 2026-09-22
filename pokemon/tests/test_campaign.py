"""Campaign/navigation tests: facts and advisory paths, no game or API calls."""
import sys,json
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign import CampaignPlanner


def state(x=3,y=6,mid=38):
 return {'player':{'map_id':mid,'x':x,'y':y,'facing':'up'},'scene':{'mode':'overworld','verified':True},
         'dialog':{'open':False,'text':''},'milestones':{},
         'world':{'map_id':mid,'name':'TEST','width':8,'height':8,'source_match':True,'player_position_valid':True,'objects':[],
                  'warps':[{'x':7,'y':1,'destination_map_id':37,'quality':'verified_current_ram'}],'connections':[]},
         'local_map':{'verified':True,'player_cell':{'x':4,'y':4},'rows':['..........']*9}}


def target(map_id=38,kind='coordinate',x=5,y=6):
 return {'id':'test_goal','intent':'OFFLINE TEST','target_map_id':map_id,
         'interaction_selectors':[{'kind':kind,'x':x,'y':y}],'completion':False}


class CampaignTests(unittest.TestCase):
 def test_target_path_guidance_is_grounded_and_does_not_choose_input(self):
  planner=CampaignPlanner(selector=lambda *_:target());context=planner.context(state())
  self.assertEqual(context['navigation']['next_button'],'right')
  self.assertEqual(context['navigation']['status'],'path_to_target')
  self.assertEqual(context['roles']['physical_input'],'JEV choice')

 def test_object_target_is_adjacent_and_keeps_object_kind(self):
  s=state(x=5,y=3);s['world']['objects']=[{'x':6,'y':3,'kind':'npc_or_interactive','sprite':'BALL','text_id':2,'active':True}]
  objective=target(kind='object',x=6,y=3);objective['interaction_selectors'][0].update(sprite='BALL',text_id=2)
  planner=CampaignPlanner(selector=lambda *_:objective)
  self.assertEqual(planner.context(s)['navigation']['next_button'],'right')
  s['player']['facing']='right'
  self.assertEqual(planner.context(s)['navigation']['next_button'],'a')
  self.assertEqual(planner.context(s)['navigation']['target']['kind'],'object')

 def test_border_warp_requires_outward_activation(self):
  s=state(x=2,y=7,mid=37);s['world']['warps']=[{'x':2,'y':7,'destination_map_id':0}]
  planner=CampaignPlanner(selector=lambda *_:target(map_id=0))
  nav=planner.context(s)['navigation']
  self.assertEqual((nav['status'],nav['next_button']),('activate_transition','down'))

 def test_transition_and_clues_persist_longer_than_short_action_window(self):
  planner=CampaignPlanner(selector=lambda *_:target())
  a=state(7,2);b=state(7,1,37);planner.record('up',a,b)
  for _ in range(40):planner.record('wait',b,b)
  restored=CampaignPlanner(json.loads(json.dumps(planner.snapshot())),selector=lambda *_:target())
  self.assertEqual(restored.context(b)['observed_map_connections'][0]['to_map'],37)

 def test_unverified_and_attempt_local_facts_are_not_persisted_as_story_completion(self):
  s=state();s['milestones']={'pokedex_received':{'value':True,'verified':True},'elite4_lance_defeated':{'value':True,'verified':True},'hall_of_fame_entered':{'value':True,'verified':False}}
  p=CampaignPlanner(selector=lambda *_:target());p.context(s)
  self.assertEqual(set(p.history_facts),{'pokedex_received'})

 def test_unknown_map_alignment_does_not_produce_a_direction(self):
  s=state();s['world']['source_match']=False
  p=CampaignPlanner(selector=lambda *_:target());n=p.context(s)['navigation']
  self.assertEqual(n['status'],'wait_for_map_transition');self.assertEqual(n['next_button'],'wait')

 def test_battle_blackout_transition_is_not_a_free_walking_edge(self):
  p=CampaignPlanner(selector=lambda *_:target());a=state(mid=51);a['scene']['mode']='battle';b=state(mid=0)
  p.record('a',a,b);self.assertEqual(p.transitions[-1]['kind'],'script_or_battle')

 def test_hidden_target_is_not_reintroduced_from_source_coordinates(self):
  s=state();s['world']['objects']=[{'x':6,'y':3,'sprite':'BALL','text_id':2,'active':False}]
  objective=target(kind='object',x=6,y=3);objective['interaction_selectors'][0].update(sprite='BALL',text_id=2)
  p=CampaignPlanner(selector=lambda *_:objective)
  self.assertEqual(p.context(s)['navigation']['status'],'needs_target')

 def test_turn_and_temporary_failed_edge_do_not_become_permanent_walls(self):
  p=CampaignPlanner(selector=lambda *_:target());a=state();b=state();b['player']['facing']='right'
  p.record('right',a,b);self.assertFalse(p.failed_edges)
  p.record('right',b,b);p.record('right',b,b);self.assertTrue(p.failed_edges)
  restored=CampaignPlanner(p.snapshot(),selector=lambda *_:target())
  for _ in range(65):restored.record('wait',b,b)
  self.assertFalse(restored.failed_edges)

 def test_starter_received_guidance_heads_to_rival_trigger_not_rival_text(self):
  s=state(x=5,y=3,mid=40);s['world'].update(width=10,height=12)
  s['milestones']={'starter_received':{'value':True,'verified':True},'party_count':{'value':1,'verified':True}}
  s['world']['objects']=[{'x':4,'y':3,'sprite':'SPRITE_BLUE','text_id':1,'active':True}]
  c=CampaignPlanner().context(s)
  self.assertEqual(c['active_objective']['id'],'lab_rival')
  self.assertEqual(c['navigation']['next_button'],'down')
  self.assertEqual(c['navigation']['target']['kind'],'coordinate')

 def test_scripted_warp_does_not_publish_invalid_tiles_or_replayable_edges(self):
  p=CampaignPlanner();a=state(x=11,y=12,mid=0);a['world']['input_lock']={'ignored_buttons_mask':252,'scripted_movement_remaining':2}
  b=state(x=12,y=11,mid=40);b['world'].update(source_match=False,player_position_valid=False)
  p.record('right',a,b)
  self.assertNotIn('40',p.tiles);self.assertFalse(p.transitions)
  b=state(x=5,y=11,mid=40);b['world'].update(height=12,width=10)
  p.record('right',a,b)
  self.assertEqual(p.transitions[-1]['kind'],'script_or_battle')

 def test_new_dialogue_counts_as_ui_progress_but_repeated_dialogue_does_not(self):
  p=CampaignPlanner(selector=lambda *_:target());a=state();b=state();b['scene']['mode']='dialog';b['dialog']={'open':True,'text':'A new story explanation'}
  self.assertTrue(p.record('a',a,b)['new_dialog_clue'])
  restored=CampaignPlanner(p.snapshot(),selector=lambda *_:target())
  self.assertFalse(restored.record('a',a,b)['new_dialog_clue'])
  b['dialog']['text']='The next explanation'
  self.assertTrue(restored.record('a',a,b)['new_dialog_clue'])

 def test_nurse_is_interactable_from_the_public_side_of_counter(self):
  s=state(x=3,y=3,mid=41);s['world'].update(width=14,height=8)
  s['world']['objects']=[{'x':3,'y':1,'sprite':'SPRITE_NURSE','text_id':1,'active':True}]
  objective=target(map_id=41,kind='object',x=3,y=1)
  objective['interaction_selectors'][0].update(sprite='SPRITE_NURSE',text_id=1)
  c=CampaignPlanner(selector=lambda *_:objective).context(s)
  self.assertEqual(c['navigation']['status'],'interact')
  self.assertEqual(c['navigation']['next_button'],'a')


if __name__=='__main__':unittest.main()
