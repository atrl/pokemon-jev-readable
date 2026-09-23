"""Real-ROM observation/memory smoke, with NO model calls or AI progress claim.

Called by test_rom after its separately labelled scripted bedroom fixture.
The models' serializers run on actual snapshots; hidden-field mutations are
copies used only for counterfactual projection checks, never RAM writes.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
from _bootstrap import ROOT, POKEMON, DEFAULT_ROM
from emulator import Emulator
from memory import Reader, load_profile
from artifacts import load_state, write_json
from perception import project, point
from plan_manager import PlanManager
from model_context import build_situation
from jev import build_request


def verify(rom: Path, checkpoint: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    profile=load_profile()
    state,_=load_state(checkpoint,profile['rom_sha1'])
    world=Emulator(rom,profile)
    report={'kind':'scripted_real_rom_observation_boundary','models_called':False,'game_completed':False,'checks':{}}
    try:
        reader=Reader(world,profile); world.load(state)
        raw=reader.snapshot(); game=project(raw)
        assert not raw['errors'] and game['scene']['mode']=='overworld'
        manager=PlanManager(); manager.model_planning_enabled=True
        context=manager.context(game)
        situation=build_situation(game,context,{'total_steps':0,'visited_tiles':1})
        request=build_request({**game,'campaign':context},'User-defined test goal',[])
        altered=deepcopy(raw)
        for portal in altered['world'].get('warps',[]):
            portal['destination_map_id']=250; portal['destination_name']='UNOBSERVED_DESTINATION'
        altered['world']['script_triggers']=[{'action':'SPOILER'}]
        altered['milestones']['unobserved_event']={'value':True,'verified':True}
        assert project(altered)==game
        assert situation['targets']
        assert 'next_button' not in context['navigation']
        assert 'story_reference' not in situation
        report['checks']['hidden_fields_do_not_change_observation']=True
        report['checks']['both_model_inputs_serialize']=bool(json.dumps(situation) and json.dumps(request))
        report['initial_position']=point(game)
        world.screenshot(output/'agency-before.png')
        world.press('right',16,32)
        after=project(reader.snapshot()); manager.record('right',game,after)
        assert point(game)!=point(after)
        restored=PlanManager(json.loads(json.dumps(manager.snapshot())))
        assert restored.memory.snapshot()==manager.memory.snapshot()
        report['checks']['real_movement_and_experience_roundtrip']=True
        report['after_position']=point(after)
        world.load(state); world.press('start',8,64)
        menu=project(reader.snapshot()); world.press('down',8,24)
        menu_after=project(reader.snapshot())
        assert menu['main_menu_cursor']!=menu_after['main_menu_cursor']
        assert menu['observation_id']!=menu_after['observation_id']
        report['checks']['real_menu_cursor_in_neutral_observation']=True
        report['status']='passed'
        write_json(output/'agency-sample.json',{'observation':game,'system1_request':request,'system2_situation':situation})
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return report
    finally:
        world.close(); write_json(output/'agency-report.json',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom',type=Path,default=DEFAULT_ROM)
    parser.add_argument('--state',type=Path,default=ROOT/'pokemon/.work/bedroom.state')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/tests/rom')
    args=parser.parse_args(); verify(args.rom,args.state,args.output)
