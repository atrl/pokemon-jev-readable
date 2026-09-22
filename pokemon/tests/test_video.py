import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emulator import Emulator
from memory import load_profile
from video import HLSVideo


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


class VideoUnitTests(unittest.TestCase):
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
        self.assertEqual(emulator.video.publish.call_args.args[0], bytes([11, 22, 33]) * (144 * 160))
        emulator.game.screen.image.save.assert_not_called()


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are required for video integration")
class VideoIntegrationTests(unittest.TestCase):
    def test_h264_hls_repeats_idle_frame_and_encodes_changed_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = HLSVideo(Path(directory), ffmpeg=FFMPEG)
            try:
                stream.publish(bytes([255, 0, 0]) * (160 * 144))
                time.sleep(1.2)
                stream.publish(bytes([0, 0, 255]) * (160 * 144))
                time.sleep(1.2)
                status = stream.status()
            finally:
                stream.close()
            playlist = Path(directory) / "video/index.m3u8"
            text = playlist.read_text()
            self.assertIn("#EXT-X-INDEPENDENT-SEGMENTS", text)
            self.assertIn("#EXT-X-PROGRAM-DATE-TIME", text)
            self.assertIn('URI="init.mp4"', text)
            self.assertIn("#EXT-X-ENDLIST", text)
            self.assertEqual(status["published_frames"], 2)
            self.assertGreaterEqual(status["encoded_frames"], 60)
            probe = json.loads(subprocess.check_output([
                FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(playlist),
            ]))
            self.assertEqual(probe["streams"][0]["codec_name"], "h264")
            self.assertEqual(probe["streams"][0]["pix_fmt"], "yuv420p")
            self.assertEqual(probe["streams"][0]["width"], 160)
            self.assertEqual(probe["streams"][0]["height"], 144)
            self.assertGreaterEqual(float(probe["format"]["duration"]), 2)
            decoded = subprocess.check_output([
                FFMPEG, "-v", "error", "-i", str(playlist), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ])
            self.assertGreater(decoded[0], 240)
            self.assertLess(decoded[2], 15)
            self.assertLess(decoded[-3], 15)
            self.assertGreater(decoded[-1], 240)
            self.assertFalse(list(Path(directory).rglob("*.png")))
            self.assertFalse(list(Path(directory).rglob("*.jpg")))

    def test_encoder_exit_recovers_without_ending_live_playlist(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = HLSVideo(Path(directory), ffmpeg=FFMPEG)
            try:
                stream.publish(bytes([255, 0, 0]) * (160 * 144))
                time.sleep(1.3)
                playlist = Path(directory) / "video/index.m3u8"
                before = playlist.read_text()
                self.assertNotIn("#EXT-X-ENDLIST", before)
                first_process = stream._process
                first_process.kill()
                first_process.wait(timeout=2)
                stream.publish(bytes([0, 0, 255]) * (160 * 144))
                deadline = time.monotonic() + 3
                while stream.status()["restart_count"] == 0 and time.monotonic() < deadline:
                    time.sleep(.05)
                time.sleep(1.3)
                status = stream.status()
                after = playlist.read_text()
                self.assertEqual(status["restart_count"], 1)
                self.assertIn("returncode=-9", status["last_error"])
                self.assertIn("writer=", status["last_error"])
                self.assertIn("stderr=", status["last_error"])
                self.assertIn("#EXT-X-DISCONTINUITY", after)
                self.assertNotIn("#EXT-X-ENDLIST", after)
                self.assertIsNone(stream._process.poll())
            finally:
                stream.close()
            self.assertIn("#EXT-X-ENDLIST", playlist.read_text())
            decoded = subprocess.check_output([
                FFMPEG, "-v", "error", "-i", str(playlist), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ])
            self.assertGreater(decoded[0], 240)
            self.assertGreater(decoded[-1], 240)

    def test_repeated_encoder_death_fails_visibly_at_bounded_recovery_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = HLSVideo(Path(directory), ffmpeg=FFMPEG)
            stream.publish(bytes(160 * 144 * 3))
            for restart in range(4):
                process = stream._process
                process.kill()
                process.wait(timeout=2)
                deadline = time.monotonic() + 3
                while stream._process is process and stream._error is None and time.monotonic() < deadline:
                    time.sleep(.02)
            with self.assertRaisesRegex(RuntimeError, "recovery limit reached"):
                stream.publish(bytes(160 * 144 * 3))
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "recovery limit reached"):
                stream.close()
            self.assertLess(time.monotonic() - started, 2)
            self.assertFalse(stream._thread.is_alive())

    def test_recovery_subprocess_timeout_is_reported_instead_of_silent_writer_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = HLSVideo(Path(directory), ffmpeg=FFMPEG)
            stream.publish(bytes(160 * 144 * 3))
            with patch.object(stream, "_recover_encoder", side_effect=subprocess.TimeoutExpired("ffmpeg", 2)):
                stream._process.kill()
                stream._process.wait(timeout=2)
                deadline = time.monotonic() + 2
                while stream._error is None and time.monotonic() < deadline:
                    time.sleep(.02)
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                stream.status()
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                stream.close()
            self.assertFalse(stream._thread.is_alive())

    def test_real_rom_framebuffer_produces_playable_video(self):
        rom = Path(__file__).resolve().parents[2] / "red-star-2020-08-18.gb"
        if not rom.exists():
            self.skipTest("Verified Pokémon Red Star ROM is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            emulator = Emulator(rom, load_profile())
            try:
                # Boot quickly before starting the live clock; no API or policy.
                emulator.tick(180)
                initial = emulator.game.screen.ndarray[:, :, :3].tobytes()
                self.assertGreater(len(set(initial)), 1)
                metadata = emulator.enable_video(Path(directory), ffmpeg=FFMPEG)
                self.assertEqual((metadata["fps"], metadata["format"]), (30, "hls-fmp4"))
                started = time.monotonic()
                emulator.tick(90)
                self.assertGreaterEqual(time.monotonic() - started, 1.45)
                self.assertGreaterEqual(emulator.video_status()["published_frames"], 46)
            finally:
                emulator.close()
            playlist = Path(directory) / "video/index.m3u8"
            decoded = subprocess.check_output([
                FFMPEG, "-v", "error", "-i", str(playlist), "-frames:v", "1",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ])
            self.assertEqual(len(decoded), 160 * 144 * 3)
            self.assertGreater(len(set(decoded)), 1)


if __name__ == "__main__":
    unittest.main()
