import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jev import BUTTONS, build_request, validate_response, choose
from memory import Reader, decode_text, load_profile
from emulator import Emulator
from run import run

class FakeMemory:
    def __init__(self):self.memory=bytearray(65536)
    def read(self,address,length=1):return bytes(self.memory[address:address+length])

class CoreTests(unittest.TestCase):
    def test_name_terminates_before_residual_bytes(self):
        self.assertEqual(decode_text(bytes([0x91,0x84,0x83,0x50,0x80])),'RED')
    def test_unknown_text_is_not_invented(self):
        self.assertEqual(decode_text(b'\x01\x50'),'�')
    def test_profile_is_redstar_not_original_red(self):
        self.assertEqual(load_profile()['rom_sha1'],'e2d564a5172d38e44b4f4b8f7d9db92f644cf5e9')
    def test_all_buttons_remain_available(self):
        request=build_request({'anything':1},'Explore',[{'button':'a'}]*20)
        self.assertEqual(set(request['questions']['button']['criteria']),set(BUTTONS))
        self.assertEqual(len(request['state']['recent_actions']),12)
    def answer(self):
        return {'answers':{'button':{'type':'choice','choice':'a','confidence':0.7,
                'probabilities':{b:(1.0 if b=='a' else 0.0) for b in BUTTONS}}}}
    def test_valid_answer(self):self.assertEqual(validate_response(self.answer())['choice'],'a')
    def test_unknown_action_rejected(self):
        a=self.answer();a['answers']['button']['choice']='go_to_lab'
        with self.assertRaises(ValueError):validate_response(a)
    def test_nonfinite_rejected(self):
        a=self.answer();a['answers']['button']['probabilities']['a']=math.nan
        with self.assertRaises(ValueError):validate_response(a)
    def test_missing_option_rejected(self):
        a=self.answer();del a['answers']['button']['probabilities']['wait']
        with self.assertRaises(ValueError):validate_response(a)
    def test_missing_key_never_calls_network(self):
        with patch.dict('os.environ',{},clear=True),patch('urllib.request.urlopen') as network:
            with self.assertRaises(RuntimeError):choose({},'Explore',[])
            network.assert_not_called()
    def test_empty_party(self):
        self.assertEqual(Reader(FakeMemory(),load_profile()).party(),[])
    def test_nonempty_party_fixture(self):
        m=FakeMemory();p=load_profile();a=p['addresses']
        m.memory[a['wPartyCount']]=1
        b=a['wPartyMon1'];m.memory[b]=176
        m.memory[b+1:b+3]=(18).to_bytes(2,'big');m.memory[b+33]=5
        m.memory[b+34:b+36]=(20).to_bytes(2,'big');m.memory[b+8]=10;m.memory[b+29]=35
        m.memory[a['wPartyMonNicks']:a['wPartyMonNicks']+4]=bytes([0x91,0x84,0x83,0x50])
        mon=Reader(m,p).party()[0]
        self.assertEqual((mon['level'],mon['hp'],mon['max_hp']),(5,18,20))
        self.assertEqual(mon['moves'],[{'move_id':10,'pp':35}])
    def test_invalid_party_count_rejected(self):
        m=FakeMemory();p=load_profile();m.memory[p['addresses']['wPartyCount']]=7
        with self.assertRaises(ValueError):Reader(m,p).party()
    def test_wrong_rom_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'wrong.gb';f.write_bytes(b'not a rom')
            with self.assertRaises(ValueError):Emulator(f,load_profile())
    def test_ci_reports_missing_key_instead_of_faking_play(self):
        with tempfile.TemporaryDirectory() as d,patch.dict('os.environ',{},clear=True):
            result=run(Path('absent.gb'),Path(d)/'run',goal='Explore',steps=12,allow_missing_key=True)
            self.assertEqual(result['status'],'blocked_missing_key')
            self.assertEqual(result['executed_actions'],0)

if __name__=='__main__':unittest.main()
