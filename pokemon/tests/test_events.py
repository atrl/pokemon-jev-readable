"""Event protocol checks with explicit emulator and HTTP test doubles; no game runs."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jev import BUTTONS, choose
from run import run

TEST_KEY = 'fixture-only-not-a-real-key'


def answer():
    return {'answers': {'button': {'type': 'choice', 'choice': 'a', 'confidence': 0.7,
            'probabilities': {button: (1.0 if button == 'a' else 0.0) for button in BUTTONS}}}}


def observation():
    return {'game': 'TEST DOUBLE - no live game', 'errors': [],
            'player': {'name': 'TEST', 'map_id': 38, 'x': 3, 'y': 6, 'money': 9999},
            'party': [{'unverified': True}], 'bag': [],
            'screen_text': {'rows': ['TEST DOUBLE']}, 'battle_type_raw': 2}


def response(payload):
    result = io.BytesIO(json.dumps(payload).encode())
    result.status = 200
    return result


def events(path):
    return [json.loads(line) for line in (path / 'events.jsonl').read_text().splitlines()]


class EventTests(unittest.TestCase):
    def test_retries_emit_one_request_per_actual_attempt_without_credentials(self):
        output = []
        payload = answer()
        payload['debug'] = {'nested': [f'echo {TEST_KEY}'], 'Authorization': 'Bearer another-secret'}
        busy = urllib.error.HTTPError('https://example.invalid', 503, 'busy', {}, None)
        with patch.dict('os.environ', {'TYPESAFE_API_KEY': TEST_KEY}), \
             patch('urllib.request.urlopen', side_effect=[busy, response(payload)]) as network, \
             patch('jev.time.sleep') as sleep:
            result = choose(observation(), 'Explore', [], on_event=output.append)
        self.assertEqual(network.call_count, 2)
        self.assertEqual([row['type'] for row in output], ['jev_request', 'jev_response', 'jev_error', 'jev_request', 'jev_response'])
        self.assertEqual([row['attempt'] for row in output if row['type'] == 'jev_request'], [1, 2])
        sleep.assert_called_once_with(1)
        self.assertEqual(output[-1]['httpStatus'], 200)
        self.assertNotIn(TEST_KEY, json.dumps([output, result]))
        self.assertNotIn('another-secret', json.dumps([output, result]))
        self.assertNotIn('money', output[0]['request']['state']['game']['player'])

    def test_invalid_response_and_network_errors_are_safe(self):
        for payload in [None, {'answers': []}, {'answers': {'button': 'invalid'}}, {'answers': {'button': {}}}]:
            output = []
            with self.subTest(payload=payload), patch.dict('os.environ', {'TYPESAFE_API_KEY': TEST_KEY}), \
                 patch('urllib.request.urlopen', return_value=response(payload)):
                with self.assertRaises(ValueError):
                    choose(observation(), 'Explore', [], on_event=output.append)
                self.assertEqual(output[-1]['type'], 'jev_error')
                self.assertEqual(output[-1]['phase'], 'validation')
        output = []
        with patch.dict('os.environ', {'TYPESAFE_API_KEY': TEST_KEY}), \
             patch('urllib.request.urlopen', side_effect=urllib.error.URLError(TEST_KEY)):
            with self.assertRaisesRegex(RuntimeError, 'connection failed'):
                choose({}, 'Explore', [], on_event=output.append)
        self.assertNotIn(TEST_KEY, json.dumps(output))

    def test_run_streams_decision_before_action_and_never_requires_images(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run'
            world = Mock()
            world.save.return_value = b'explicit emulator test double'
            def pressed(*args, **kwargs):
                self.assertEqual(events(path)[-1]['type'], 'executing')
                self.assertTrue((path / '0000-decision.json').exists())
                self.assertEqual(json.loads((path / 'report.json').read_text())['jev_calls'], 1)
            world.press.side_effect = pressed
            reader = Mock()
            reader.snapshot.return_value = observation()
            with patch.dict('os.environ', {'TYPESAFE_API_KEY': TEST_KEY}), \
                 patch('run.Emulator', return_value=world), patch('run.Reader', return_value=reader), \
                 patch('urllib.request.urlopen', return_value=response(answer())):
                result = run(Path('TEST-DOUBLE.gb'), path, goal='Explore', steps=1)
            rows = events(path)
            self.assertEqual([row['type'] for row in rows],
                             ['started', 'observation', 'jev_request', 'jev_response', 'decision', 'executing', 'result', 'finished'])
            self.assertEqual(result['status'], 'budget_reached')
            self.assertIsInstance(rows[0]['pid'], int)
            self.assertEqual(result['jev_http_attempts'], 1)
            self.assertEqual(result['executed_actions'], 1)
            self.assertEqual(rows[4]['answer']['choice'], 'a')
            self.assertEqual(rows[4]['source'], 'jev')
            self.assertEqual(rows[5]['action'], 'a')
            self.assertTrue(rows[6]['success'])
            for row in rows:
                self.assertEqual(row['game'], 'pokemon')
                self.assertTrue(row['time'].endswith('Z'))
                if 'step' in row:
                    self.assertEqual(row['step'], 1)
                if 'observation' in row:
                    self.assertNotIn('money', row['observation']['player'])
                    self.assertIsNone(row['observation']['party'])
                    self.assertNotIn('battle_type_raw', row['observation'])
            world.screenshot.assert_not_called()
            world.close.assert_called_once()
            self.assertFalse(list(path.glob('*.png')))

    def test_missing_key_finishes_cleanly_with_and_without_exception(self):
        for allow in [True, False]:
            with self.subTest(allow=allow), tempfile.TemporaryDirectory() as directory, \
                 patch.dict('os.environ', {}, clear=True), patch('run.Emulator') as emulator:
                path = Path(directory) / 'run'
                if allow:
                    run(Path('absent.gb'), path, goal='Explore', steps=1, allow_missing_key=True)
                else:
                    with self.assertRaises(RuntimeError):
                        run(Path('absent.gb'), path, goal='Explore', steps=1)
                self.assertEqual(events(path)[-1]['type'], 'finished')
                self.assertEqual(events(path)[-1]['status'], 'blocked_missing_key')
                self.assertEqual(json.loads((path / 'report.json').read_text())['executed_actions'], 0)
                emulator.assert_not_called()

    def test_failed_and_interrupted_attempts_finish_without_fallback(self):
        for error, expected in [(urllib.error.URLError(TEST_KEY), 'failed'), (KeyboardInterrupt(), 'interrupted')]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'run'
                world = Mock()
                world.save.return_value = b'test double'
                reader = Mock()
                reader.snapshot.return_value = observation()
                with patch.dict('os.environ', {'TYPESAFE_API_KEY': TEST_KEY}), \
                     patch('run.Emulator', return_value=world), patch('run.Reader', return_value=reader), \
                     patch('urllib.request.urlopen', side_effect=error):
                    if expected == 'failed':
                        with self.assertRaises(RuntimeError):
                            run(Path('TEST-DOUBLE.gb'), path, goal='Explore', steps=1)
                    else:
                        run(Path('TEST-DOUBLE.gb'), path, goal='Explore', steps=1)
                self.assertEqual(events(path)[-1]['status'], expected)
                self.assertNotIn(TEST_KEY, (path / 'events.jsonl').read_text())
                world.press.assert_not_called()
                world.screenshot.assert_not_called()

    def test_unverified_result_is_not_streamed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run'
            world = Mock()
            world.save.return_value = b'test double'
            reader = Mock()
            bad = {**observation(), 'errors': ['invalid memory']}
            reader.snapshot.side_effect = [observation(), bad, bad]
            with patch.dict('os.environ', {'TYPESAFE_API_KEY': TEST_KEY}), \
                 patch('run.Emulator', return_value=world), patch('run.Reader', return_value=reader), \
                 patch('urllib.request.urlopen', return_value=response(answer())):
                with self.assertRaisesRegex(RuntimeError, 'after action'):
                    run(Path('TEST-DOUBLE.gb'), path, goal='Explore', steps=1)
            result_event = next(row for row in events(path) if row['type'] == 'result')
            self.assertFalse(result_event['success'])
            self.assertIsNone(result_event['result']['after'])
            self.assertNotIn('observation', result_event)
            self.assertEqual(events(path)[-1]['report']['executed_actions'], 1)
            self.assertEqual(events(path)[-1]['status'], 'failed')

    def test_screenshot_evidence_is_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run'
            world = Mock()
            world.save.return_value = b'test double'
            world.screenshot.return_value = 'test-image-hash'
            reader = Mock()
            reader.snapshot.return_value = observation()
            with patch.dict('os.environ', {'TYPESAFE_API_KEY': TEST_KEY}), \
                 patch('run.Emulator', return_value=world), patch('run.Reader', return_value=reader), \
                 patch('urllib.request.urlopen', return_value=response(answer())):
                run(Path('TEST-DOUBLE.gb'), path, goal='Explore', steps=1, screenshots=True)
            self.assertEqual(world.screenshot.call_count, 2)
            self.assertNotIn('screen_sha256', (path / 'events.jsonl').read_text())
            self.assertEqual(json.loads((path / '0000-decision.json').read_text())['screen_sha256'], 'test-image-hash')

    def test_cleanup_failure_still_finishes_and_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run'
            world = Mock()
            world.save.side_effect = RuntimeError(TEST_KEY)
            reader = Mock()
            reader.snapshot.return_value = observation()
            with patch.dict('os.environ', {'TYPESAFE_API_KEY': TEST_KEY}), \
                 patch('run.Emulator', return_value=world), patch('run.Reader', return_value=reader), \
                 patch('urllib.request.urlopen', return_value=response(answer())):
                with self.assertRaisesRegex(RuntimeError, 'finalization failed'):
                    run(Path('TEST-DOUBLE.gb'), path, goal='Explore', steps=1)
            self.assertEqual(events(path)[-1]['status'], 'failed')
            self.assertNotIn(TEST_KEY, (path / 'events.jsonl').read_text())
            self.assertNotIn(TEST_KEY, (path / 'report.json').read_text())
            world.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
