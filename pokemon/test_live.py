"""Real-ROM regression test. Scripted setup is a test fixture, NOT a Jev policy."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from emulator import Emulator
from memory import Reader, load_profile
from run import write_json


def verify(rom: Path, output: Path, checkpoint: Path) -> dict:
    output.mkdir(parents=True,exist_ok=True)
    profile=load_profile()
    world=Emulator(rom,profile)
    reader=Reader(world,profile)
    report={'rom_sha1':profile['rom_sha1'],'policy':'scripted_regression_not_jev','checks':{},'jev_called':False}
    history=[]
    try:
        world.tick(600)
        world.press('start',8,120)
        seen_dialog=False
        for i in range(120):
            observation=reader.snapshot()
            rows=observation['screen_text']['rows']
            text='\n'.join(rows)
            history.append({'step':i,'observation':observation})
            if 'Hello there' in text or 'Welcome' in text:
                seen_dialog=True
                world.screenshot(output/'intro-dialog.png')
                write_json(output/'intro-dialog.json',observation)
            if reader.byte('wCurMap')==38 and reader.data('wTileMap')[0]==16 and reader.byte('wFontLoaded')==0 and reader.byte('wTilesetBank')>0:
                break
            if 'NEW NAME' in text:
                world.press('down',8,24)
                world.press('a',8,120)
            else:
                world.press('a',8,120)
        else:
            raise AssertionError('Test fixture did not reach the starting room; do not report a pass')
        world.tick(60)
        initial=reader.snapshot()
        assert not initial['errors'],initial['errors']
        assert initial['player']['name']=='RED',initial['player']
        assert initial['party']==[] and initial['bag']==[]
        baseline=world.save()
        initial_hash=world.screenshot(output/'bedroom.png')
        write_json(output/'bedroom.json',initial)
        movements=[]
        for button,axis,sign in [('right','x',1),('left','x',-1),('down','y',1),('up','y',-1)]:
            world.load(baseline)
            before=reader.snapshot()['player']
            world.press(button,16,32)
            after=reader.snapshot()['player']
            moved=(before['x'],before['y']) != (after['x'],after['y'])
            frame_hash=world.screenshot(output/f'move-{button}.png')
            if moved:
                assert before['map_id']==after['map_id']
                other='y' if axis=='x' else 'x'
                assert after[other]==before[other]
                assert (after[axis]-before[axis])*sign>0
                assert frame_hash!=initial_hash
            movements.append({'button':button,'before':before,'after':after,'moved':moved})
        assert any(m['moved'] for m in movements),'No observed position responds to any direction'
        report['checks']['direction_coordinate_consistency']=movements
        world.load(baseline)
        world.press('start',8,64)
        menu=reader.snapshot()
        menu_text='\n'.join(menu['screen_text']['rows'])
        # Red Star really displays PACK, not vanilla Red's ITEM.
        assert 'PACK' in menu_text and 'SAVE' in menu_text,menu_text
        before_cursor=menu['menu_cursor_raw']
        world.screenshot(output/'menu-before.png')
        write_json(output/'menu-before.json',menu)
        world.press('down',8,24)
        after_menu=reader.snapshot()
        assert after_menu['menu_cursor_raw']!=before_cursor
        world.screenshot(output/'menu-after.png')
        write_json(output/'menu-after.json',after_menu)
        report['checks']['menu_cursor']={'before':before_cursor,'after':after_menu['menu_cursor_raw'],'visible_menu':True}
        world.load(baseline)
        world.press('right',16,32)
        first=reader.snapshot()
        pixel_first=world.game.screen.image.tobytes()
        world.load(baseline)
        world.press('right',16,32)
        second=reader.snapshot()
        first.pop('frame');second.pop('frame')
        assert first==second
        assert pixel_first==world.game.screen.image.tobytes()
        world.load(baseline)
        checkpoint.parent.mkdir(parents=True,exist_ok=True)
        checkpoint.write_bytes(baseline)
        write_json(checkpoint.with_suffix(checkpoint.suffix+'.json'),{
            'rom_sha1':profile['rom_sha1'],'state_sha256':hashlib.sha256(baseline).hexdigest(),
            'source':'scripted regression fixture; not Jev gameplay',
        })
        report['checks'].update(name='RED',dialog_decoded=seen_dialog,empty_party_and_bag=True,state_replay=True)
        assert seen_dialog
        report.update(status='passed',not_tested=['nonempty party','nonempty bag','battles','Jev gameplay','story completion'])
    except Exception as exc:
        report.update(status='failed',error=str(exc))
        world.screenshot(output/'failed.png')
        write_json(output/'failed-observation.json',reader.snapshot())
        raise
    finally:
        write_json(output/'report.json',report)
        write_json(output/'setup-trace.json',{'source':'scripted fixture','steps':history})
        world.close()
    print(json.dumps(report,indent=2))
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--rom',type=Path,default=Path('red-star-2020-08-18.gb'))
    ap.add_argument('--output',type=Path,default=Path('pokemon-evidence/live'))
    ap.add_argument('--checkpoint',type=Path,default=Path('pokemon/.work/bedroom.state'))
    a=ap.parse_args();verify(a.rom,a.output,a.checkpoint)
