import json
from collections import deque
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from _paths import ROOT, POKEMON
from emulator import Emulator
from memory import load_profile
from video import HLSVideo


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


class VideoUnitTests(unittest.TestCase):
    def writer(self):
        stream = HLSVideo.__new__(HLSVideo)
        stream._process = MagicMock()
        stream._process.poll.return_value = None
        stream._process.stdin.fileno.return_value = 42
        stream._stop = threading.Event()
        stream._lock = threading.Lock()
        stream._last_progress_at = None
        return stream

    def test_slow_partial_writes_reset_no_progress_deadline(self):
        stream = self.writer()
        clock = [0.0]
        chunks = []

        def writable(*args):
            clock[0] += 6
            return [], [42], []

        def partial(descriptor, remaining):
            self.assertEqual(descriptor, 42)
            chunks.append(bytes(remaining[:2]))
            return min(2, len(remaining))

        with (
            patch("video.time.monotonic", side_effect=lambda: clock[0]),
            patch("video.select.select", side_effect=writable),
            patch("video.os.write", side_effect=partial),
        ):
            self.assertTrue(stream._write_frame(b"abcdef"))
        self.assertEqual(b"".join(chunks), b"abcdef")
        self.assertEqual(stream._last_progress_at, 18)

    def test_scheduler_pause_does_not_restart_encoder_that_accepts_bytes(self):
        stream = self.writer()
        clock = [0.0]

        def resumed(*args):
            clock[0] += 60
            return [], [42], []

        with (
            patch("video.time.monotonic", side_effect=lambda: clock[0]),
            patch("video.select.select", side_effect=resumed),
            patch("video.os.write", return_value=6),
        ):
            self.assertTrue(stream._write_frame(b"abcdef"))

    def test_no_write_progress_times_out_instead_of_hanging(self):
        stream = self.writer()
        clock = [0.0]

        def blocked(*args):
            clock[0] += 5
            return [], [], []

        with (
            patch("video.time.monotonic", side_effect=lambda: clock[0]),
            patch("video.select.select", side_effect=blocked),
            patch("video.os.write") as write,
        ):
            with self.assertRaisesRegex(RuntimeError, "accepted no video bytes for 15 seconds"):
                stream._write_frame(b"abcdef")
        self.assertEqual(clock[0], 15)
        write.assert_not_called()

    def test_writable_pipe_that_would_block_still_has_bounded_deadline(self):
        stream = self.writer()
        clock = [0.0]

        def writable(*args):
            clock[0] += 5
            return [], [42], []

        with (
            patch("video.time.monotonic", side_effect=lambda: clock[0]),
            patch("video.select.select", side_effect=writable),
            patch("video.os.write", side_effect=BlockingIOError),
        ):
            with self.assertRaisesRegex(RuntimeError, "accepted no video bytes"):
                stream._write_frame(b"abcdef")
        self.assertEqual(clock[0], 15)

    def test_shutdown_does_not_wait_for_fifteen_second_idle_deadline(self):
        stream = self.writer()
        stream._stop.set()
        clock = [0.0]

        def blocked(*args):
            clock[0] += 0.5
            return [], [], []

        with (
            patch("video.time.monotonic", side_effect=lambda: clock[0]),
            patch("video.select.select", side_effect=blocked),
        ):
            self.assertFalse(stream._write_frame(b"abcdef"))
        self.assertEqual(clock[0], 1)

    def test_recovery_budget_expires_after_five_minutes(self):
        stream = self.writer()
        stream._restart_times = deque([0.0, 1.0, 2.0])
        stream._restarts = 3
        stream._diagnostic = MagicMock(return_value="recorded encoder failure")
        stream._start_encoder = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            stream._log = Path(directory) / "ffmpeg.log"
            with (
                patch("video.time.monotonic", return_value=400.0),
                patch.object(stream._stop, "wait", return_value=False),
            ):
                stream._recover_encoder(RuntimeError("encoder exited"))
        self.assertEqual(list(stream._restart_times), [400.0])
        self.assertEqual(stream._restarts, 4)
        self.assertFalse(stream._recovering)
        stream._start_encoder.assert_called_once_with(resume=True)

    def test_default_emulation_keeps_fast_batch_ticks(self):
        emulator = Emulator.__new__(Emulator)
        emulator.video = None
        emulator.game = MagicMock()
        emulator.tick(120)
        emulator.game.tick.assert_called_once_with(120, render=True, sound=False)

    def test_video_renders_all_emulated_frames_and_publishes_rgb_on_caller(self):
        import numpy as np

        emulator = Emulator.__new__(Emulator)
        emulator.game = MagicMock()
        emulator.game.screen.ndarray = np.full((144, 160, 4), [11, 22, 33, 255], dtype=np.uint8)
        emulator.video = MagicMock()
        emulator._video_frame_counter = 0
        with patch("emulator.time.sleep"):
            emulator.tick(4)
        self.assertEqual(emulator.game.tick.call_count, 4)
        emulator.game.tick.assert_called_with(1, render=True, sound=False)
        self.assertEqual(emulator.video.publish.call_count, 2)
        self.assertEqual(
            emulator.video.publish.call_args.args[0], bytes([11, 22, 33]) * (144 * 160)
        )
        emulator.game.screen.image.save.assert_not_called()




if __name__ == "__main__":
    unittest.main()
