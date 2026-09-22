"""Continuous H.264 video from raw emulator pixels, with one pending frame.

The emulator owns framebuffer reads. This worker only repeats the most recently
published immutable RGB24 bytes, so network waits remain a connected video feed.
"""
from __future__ import annotations

import os
from collections import deque
from pathlib import Path
import select
import shutil
import subprocess
import threading
import time


class HLSVideo:
    # This bounds a genuinely blocked encoder, not the time spent on one frame.
    # A scheduler pause or slow, progressing partial writes are not a failure.
    WRITE_IDLE_SECONDS = 15.0
    DEGRADED_AFTER_SECONDS = 3.0
    SHUTDOWN_DRAIN_SECONDS = 1.0

    def __init__(self, output: Path, *, width: int = 160, height: int = 144,
                 fps: int = 30, ffmpeg: str | None = None):
        if any(type(n) is not int or n < 1 for n in (width, height, fps)):
            raise ValueError("Video dimensions and fps must be positive integers")
        executable = ffmpeg or os.environ.get("POKEMON_FFMPEG") or shutil.which("ffmpeg")
        if not executable:
            raise RuntimeError("Live video requires ffmpeg with the libx264 encoder")
        self.directory = (Path(output) / "video").resolve()
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
        self._restarts = 0
        self._restart_times: deque[float] = deque()
        self._last_error: str | None = None
        self._recovering = False
        self._last_progress_at: float | None = None
        self._executable = executable
        self._log = self.directory / "ffmpeg.log"
        self._start_encoder()
        self._thread = threading.Thread(target=self._write_frames,
                                        name="pokemon-video", daemon=True)
        self._thread.start()

    def _start_encoder(self, *, resume: bool = False) -> None:
        # Append a discontinuity on recovery: a new encoder starts new media
        # timestamps. Never reuse segment names a browser may already cache.
        segments = [int(path.stem.removeprefix("segment-"))
                    for path in self.directory.glob("segment-*.m4s")
                    if path.stem.removeprefix("segment-").isdigit()]
        start_number = max(segments, default=-1) + 1
        flags = "delete_segments+independent_segments+program_date_time+temp_file+omit_endlist"
        if resume and (self.directory / "index.m3u8").exists():
            flags += "+append_list+discont_start"
        command = [
            self._executable, "-hide_banner", "-loglevel", "warning", "-nostdin", "-y",
            "-f", "rawvideo", "-pixel_format", "rgb24",
            "-video_size", f"{self.width}x{self.height}", "-framerate", str(self.fps),
            "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-tune", "zerolatency", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
            "-crf", "18", "-g", str(self.fps), "-keyint_min", str(self.fps), "-sc_threshold", "0",
            "-f", "hls", "-hls_time", "1", "-hls_list_size", "12",
            "-hls_delete_threshold", "2", "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "init.mp4", "-hls_flags", flags,
            "-start_number", str(start_number),
            "-hls_segment_filename", str(self.directory / "segment-%06d.m4s"),
            str(self.directory / "index.m3u8"),
        ]
        with self._log.open("ab") as log:
            self._process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                             stdout=subprocess.DEVNULL, stderr=log,
                                             bufsize=0, start_new_session=True)
        assert self._process.stdin is not None
        os.set_blocking(self._process.stdin.fileno(), False)

    def _diagnostic(self, error: BaseException | None = None) -> str:
        with self._log.open("rb") as log:
            log.seek(max(0, self._log.stat().st_size - 4096))
            detail = log.read().decode("utf-8", errors="replace").strip()
        return (f"writer={error or self._error}; returncode={self._process.poll()}; "
                f"stderr={detail or '(empty)'}")

    def _failure(self) -> RuntimeError:
        return RuntimeError(f"Live video encoder failed: {self._diagnostic()}")

    def check(self) -> None:
        # The writer supervises ffmpeg and owns bounded recovery. A transient
        # encoder exit must not race a gameplay check and abort the saved game.
        if self._error is not None:
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
        # Health must remain inspectable after a terminal error so the caller
        # can checkpoint and pause decisions without losing the diagnostics.
        with self._lock:
            stalled_for = (max(0, time.monotonic() - self._last_progress_at)
                           if self._last_progress_at is not None else None)
            health = ("failed" if self._error is not None else
                      "closed" if self._closed else
                      "recovering" if self._recovering else
                      "starting" if not self._encoded else
                      "degraded" if stalled_for is not None and stalled_for > self.DEGRADED_AFTER_SECONDS else
                      "live")
            return {"fps": self.fps, "width": self.width, "height": self.height,
                    "format": "hls-fmp4", "codec": "h264",
                    "playlist": "video/index.m3u8", "published_frames": self._published,
                    "encoded_frames": self._encoded, "restart_count": self._restarts,
                    "health": health, "healthy": health == "live",
                    "seconds_without_write_progress": stalled_for,
                    "write_idle_limit_seconds": self.WRITE_IDLE_SECONDS,
                    "last_error": self._last_error,
                    "terminal_error": str(self._error) if self._error is not None else None}

    def _recover_encoder(self, error: BaseException) -> None:
        self._recovering = True
        self._last_error = self._diagnostic(error)
        now = time.monotonic()
        while self._restart_times and self._restart_times[0] < now - 300:
            self._restart_times.popleft()
        assert self._process.stdin is not None
        self._process.stdin.close()
        if self._process.poll() is None:
            self._process.kill()
        self._process.wait(timeout=2)
        if len(self._restart_times) >= 3:
            raise RuntimeError(f"Encoder recovery limit reached (3 in 5 minutes): {self._last_error}")
        if self._stop.wait(0.1):
            return
        self._restart_times.append(now)
        self._restarts += 1
        with self._log.open("a") as log:
            log.write(f"\nEncoder restart {self._restarts}: {error}; previous returncode={self._process.returncode}\n")
        with self._lock:
            if not self._stop.is_set():
                self._start_encoder(resume=True)
        self._recovering = False

    def _write_frame(self, frame: bytes) -> bool:
        assert self._process.stdin is not None
        descriptor = self._process.stdin.fileno()
        remaining = memoryview(frame)
        last_progress = time.monotonic()
        shutdown_deadline: float | None = None
        # A nonblocking pipe prevents a stuck/dead ffmpeg from hanging gameplay
        # or shutdown. At most the current frame and latest frame are retained.
        while remaining:
            # Finish a frame already in the pipe on normal shutdown. Closing
            # halfway through would leave malformed raw video for ffmpeg.
            if self._stop.is_set():
                if shutdown_deadline is None:
                    shutdown_deadline = time.monotonic() + self.SHUTDOWN_DRAIN_SECONDS
                elif time.monotonic() >= shutdown_deadline:
                    return False
            if self._process.poll() is not None:
                raise RuntimeError("ffmpeg exited while streaming")
            if select.select([], [descriptor], [], 0.1)[1]:
                try:
                    written = os.write(descriptor, remaining)
                except BlockingIOError:
                    written = 0
                if written:
                    remaining = remaining[written:]
                    last_progress = time.monotonic()
                    with self._lock:
                        self._last_progress_at = last_progress
                    continue
            # Check only after trying the pipe: if this thread was suspended
            # while ffmpeg remained healthy, a successful write above resets
            # the timer instead of needlessly killing a working encoder.
            if time.monotonic() - last_progress >= self.WRITE_IDLE_SECONDS:
                raise RuntimeError(f"ffmpeg accepted no video bytes for {self.WRITE_IDLE_SECONDS:g} seconds")
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
                try:
                    if frame is not None and not self._write_frame(frame):
                        break
                except (OSError, RuntimeError) as error:
                    if self._stop.is_set():
                        raise
                    self._recover_encoder(error)
                    deadline = time.monotonic()
                    continue
                with self._lock:
                    self._encoded += 1
                deadline += 1 / self.fps
                now = time.monotonic()
                if deadline < now - 1 / self.fps:
                    deadline = now
                self._stop.wait(max(0, deadline - now))
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            self._error = error
        finally:
            assert self._process.stdin is not None
            self._process.stdin.close()

    def close(self) -> None:
        if self._closed:
            return
        with self._lock:
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
        # ffmpeg omits ENDLIST while running so an encoder recovery cannot make
        # an HLS player permanently stop. Only a completed close ends the feed.
        playlist = self.directory / "index.m3u8"
        if playlist.exists():
            content = playlist.read_text()
            temporary = playlist.with_suffix(".m3u8.tmp")
            temporary.write_text(content + ("" if "#EXT-X-ENDLIST" in content else "#EXT-X-ENDLIST\n"))
            temporary.replace(playlist)
