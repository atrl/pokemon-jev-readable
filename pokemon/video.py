"""Continuous H.264 video from raw emulator pixels, with one pending frame.

The emulator owns framebuffer reads. This worker only repeats the most recently
published immutable RGB24 bytes, so network waits remain a connected video feed.
"""
from __future__ import annotations

import os
from pathlib import Path
import select
import shutil
import subprocess
import threading
import time


class HLSVideo:
    def __init__(self, output: Path, *, width: int = 160, height: int = 144,
                 fps: int = 30, ffmpeg: str | None = None):
        if any(type(n) is not int or n < 1 for n in (width, height, fps)):
            raise ValueError("Video dimensions and fps must be positive integers")
        executable = ffmpeg or os.environ.get("POKEMON_FFMPEG") or shutil.which("ffmpeg")
        if not executable:
            raise RuntimeError("Live video requires ffmpeg with the libx264 encoder")
        self.directory = Path(output) / "video"
        self.directory.mkdir(parents=True, exist_ok=True)
        if (self.directory / "index.m3u8").exists():
            raise ValueError("Video output already exists; use a new run directory")
        self.width, self.height, self.fps = width, height, fps
        self._frame: bytes | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._closed = False
        self._published = 0
        self._encoded = 0
        self._log = self.directory / "ffmpeg.log"
        command = [
            executable, "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pixel_format", "rgb24",
            "-video_size", f"{width}x{height}", "-framerate", str(fps),
            "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-tune", "zerolatency", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
            "-crf", "18", "-g", str(fps), "-keyint_min", str(fps), "-sc_threshold", "0",
            "-f", "hls", "-hls_time", "1", "-hls_list_size", "12",
            "-hls_delete_threshold", "2", "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "init.mp4", "-hls_flags",
            "delete_segments+independent_segments+program_date_time+temp_file",
            "-hls_segment_filename", str(self.directory / "segment-%06d.m4s"),
            str(self.directory / "index.m3u8"),
        ]
        with self._log.open("wb") as log:
            self._process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                             stdout=subprocess.DEVNULL, stderr=log,
                                             bufsize=0)
        assert self._process.stdin is not None
        os.set_blocking(self._process.stdin.fileno(), False)
        self._thread = threading.Thread(target=self._write_frames,
                                        name="pokemon-video", daemon=True)
        self._thread.start()

    def _failure(self) -> RuntimeError:
        detail = self._log.read_bytes()[-4096:].decode("utf-8", errors="replace").strip()
        return RuntimeError(f"Live video encoder failed: {detail or self._error or self._process.returncode}")

    def check(self) -> None:
        if self._error is not None or (not self._closed and self._process.poll() is not None):
            raise self._failure()

    def publish(self, rgb24: bytes) -> None:
        """Publish pixels copied on the emulator thread, never an image file."""
        self.check()
        if self._closed:
            raise RuntimeError("Live video is closed")
        frame = bytes(rgb24)
        if len(frame) != self.width * self.height * 3:
            raise ValueError("Video frame must contain exactly width * height * 3 RGB bytes")
        with self._lock:
            self._frame = frame
            self._published += 1
        self._ready.set()

    def status(self) -> dict:
        self.check()
        with self._lock:
            return {"fps": self.fps, "width": self.width, "height": self.height,
                    "format": "hls-fmp4", "codec": "h264",
                    "playlist": "video/index.m3u8", "published_frames": self._published,
                    "encoded_frames": self._encoded}

    def _write_frame(self, frame: bytes) -> bool:
        assert self._process.stdin is not None
        descriptor = self._process.stdin.fileno()
        remaining = memoryview(frame)
        write_deadline = time.monotonic() + 2
        # A nonblocking pipe prevents a stuck/dead ffmpeg from hanging gameplay
        # or shutdown. At most the current frame and latest frame are retained.
        while remaining:
            # Finish a frame already in the pipe on normal shutdown. Closing
            # halfway through would leave malformed raw video for ffmpeg.
            if time.monotonic() >= write_deadline:
                raise RuntimeError("ffmpeg did not accept a video frame within 2 seconds")
            if self._process.poll() is not None:
                raise RuntimeError("ffmpeg exited while streaming")
            if not select.select([], [descriptor], [], 0.1)[1]:
                continue
            try:
                written = os.write(descriptor, remaining)
            except BlockingIOError:
                continue
            remaining = remaining[written:]
        return True

    def _write_frames(self) -> None:
        try:
            while not self._ready.wait(0.1):
                if self._stop.is_set():
                    return
                if self._process.poll() is not None:
                    raise RuntimeError("ffmpeg exited before its first frame")
            deadline = time.monotonic()
            while not self._stop.is_set():
                with self._lock:
                    frame = self._frame
                if frame is not None and not self._write_frame(frame):
                    break
                with self._lock:
                    self._encoded += 1
                deadline += 1 / self.fps
                now = time.monotonic()
                if deadline < now - 1 / self.fps:
                    deadline = now
                self._stop.wait(max(0, deadline - now))
        except (OSError, RuntimeError) as error:
            self._error = error
        finally:
            assert self._process.stdin is not None
            self._process.stdin.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._ready.set()
        self._thread.join(timeout=2)
        try:
            result = self._process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)
            self._thread.join(timeout=1)
            self._error = RuntimeError("ffmpeg did not finish within the shutdown deadline")
            raise self._failure()
        if self._thread.is_alive():
            self._error = RuntimeError("Video writer did not stop")
        if result != 0 or self._error is not None:
            raise self._failure()
