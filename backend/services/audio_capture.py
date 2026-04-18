"""
Audio capture service — unified interface for three capture modes:

  1. microphone  — system default or user-selected mic via sounddevice
  2. loopback    — Windows WASAPI loopback via PyAudioWPatch (captures speaker output)
  3. file        — local audio/video file decoded via ffmpeg subprocess

All modes output 16kHz mono float32 PCM into an AudioBuffer.
Capture runs in a background thread; push_samples() bridges to the async world.
"""

import asyncio
import logging
import subprocess
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly
from math import gcd

from backend.config import settings
from backend.pipeline.buffer import AudioBuffer

logger = logging.getLogger(__name__)

TARGET_RATE = settings.audio_sample_rate  # 16000 Hz


# ---------------------------------------------------------------------------
# Device enumeration helpers
# ---------------------------------------------------------------------------

def list_microphone_devices() -> list[dict]:
    """Return available input (microphone) devices."""
    devices = sd.query_devices()
    result = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            result.append({
                "index": i,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "sample_rate": int(dev["default_samplerate"]),
                "type": "microphone",
            })
    return result


def list_loopback_devices() -> list[dict]:
    """Return available WASAPI loopback devices (speaker outputs)."""
    try:
        import pyaudiowpatch as pyaudio
        pa = pyaudio.PyAudio()
        result = []
        try:
            for loopback in pa.get_loopback_device_info_generator():
                result.append({
                    "index": loopback["index"],
                    "name": loopback["name"],
                    "channels": loopback["maxInputChannels"],
                    "sample_rate": int(loopback["defaultSampleRate"]),
                    "type": "loopback",
                })
        finally:
            pa.terminate()
        return result
    except Exception as e:
        logger.warning(f"Failed to enumerate loopback devices: {e}")
        return []


def get_default_loopback_device() -> Optional[dict]:
    """Get the default WASAPI loopback device (mirrors the current default speaker)."""
    try:
        import pyaudiowpatch as pyaudio
        pa = pyaudio.PyAudio()
        try:
            loopback = pa.get_default_wasapi_loopback()
            return {
                "index": loopback["index"],
                "name": loopback["name"],
                "channels": loopback["maxInputChannels"],
                "sample_rate": int(loopback["defaultSampleRate"]),
                "type": "loopback",
            }
        finally:
            pa.terminate()
    except Exception as e:
        logger.warning(f"Failed to get default loopback device: {e}")
        return None


# ---------------------------------------------------------------------------
# Resampling utility
# ---------------------------------------------------------------------------

def _resample_to_target(audio: np.ndarray, orig_rate: int) -> np.ndarray:
    """Resample audio from orig_rate to TARGET_RATE (16kHz) using polyphase filter."""
    if orig_rate == TARGET_RATE:
        return audio

    # resample_poly requires integer up/down factors
    g = gcd(TARGET_RATE, orig_rate)
    up = TARGET_RATE // g
    down = orig_rate // g

    resampled = resample_poly(audio, up, down).astype(np.float32)
    return resampled


def _to_mono_float32(raw_bytes: bytes, channels: int, sample_width: int) -> np.ndarray:
    """Convert raw PCM bytes to mono float32 numpy array."""
    if sample_width == 2:
        dtype = np.int16
    elif sample_width == 4:
        dtype = np.float32
    else:
        dtype = np.int16

    audio = np.frombuffer(raw_bytes, dtype=dtype)

    # Convert to float32 if integer format
    if dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    elif dtype == np.int32:
        audio = audio.astype(np.float32) / 2147483648.0

    # Downmix to mono if multi-channel
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    return audio


# ---------------------------------------------------------------------------
# Capture classes
# ---------------------------------------------------------------------------

class CaptureBase:
    """Base class for audio capture backends."""

    def __init__(self, buffer: AudioBuffer, loop: asyncio.AbstractEventLoop):
        self._buffer = buffer
        self._loop = loop
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def _capture_loop(self) -> None:
        raise NotImplementedError

    def _push_async(self, samples: np.ndarray) -> None:
        """Thread-safe bridge: schedule push_samples() on the async event loop."""
        asyncio.run_coroutine_threadsafe(
            self._buffer.push_samples(samples),
            self._loop,
        )

    @property
    def is_running(self) -> bool:
        return self._running


class MicrophoneCapture(CaptureBase):
    """Capture audio from a microphone via sounddevice."""

    def __init__(
        self,
        buffer: AudioBuffer,
        loop: asyncio.AbstractEventLoop,
        device_index: Optional[int] = None,
    ):
        super().__init__(buffer, loop)
        self._device_index = device_index

    def _capture_loop(self) -> None:
        logger.info(f"Microphone capture started (device={self._device_index})")

        # Query device sample rate
        if self._device_index is not None:
            dev_info = sd.query_devices(self._device_index)
        else:
            dev_info = sd.query_devices(kind="input")
        native_rate = int(dev_info["default_samplerate"])
        channels = min(dev_info["max_input_channels"], 2)  # cap at stereo

        # Read in ~0.5s blocks for responsive stop behavior
        block_samples = int(native_rate * 0.5)

        try:
            with sd.InputStream(
                device=self._device_index,
                samplerate=native_rate,
                channels=channels,
                dtype="float32",
                blocksize=block_samples,
            ) as stream:
                while self._running:
                    data, overflowed = stream.read(block_samples)
                    if overflowed:
                        logger.warning("Microphone input overflow detected")

                    # Downmix to mono
                    if channels > 1:
                        mono = data.mean(axis=1).astype(np.float32)
                    else:
                        mono = data[:, 0].astype(np.float32)

                    # Resample to 16kHz
                    resampled = _resample_to_target(mono, native_rate)
                    self._push_async(resampled)

        except Exception as e:
            logger.error(f"Microphone capture error: {e}", exc_info=True)
        finally:
            logger.info("Microphone capture stopped")
            asyncio.run_coroutine_threadsafe(self._buffer.flush(), self._loop)


class LoopbackCapture(CaptureBase):
    """
    Capture system audio output via WASAPI loopback (Windows only).
    Uses a callback-based stream to avoid blocking when no audio is playing.
    WASAPI loopback's blocking read() will hang indefinitely during silence;
    callback mode receives data (including silence frames) continuously.
    """

    def __init__(
        self,
        buffer: AudioBuffer,
        loop: asyncio.AbstractEventLoop,
        device_index: Optional[int] = None,
    ):
        super().__init__(buffer, loop)
        self._device_index = device_index

    def _capture_loop(self) -> None:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            logger.error("PyAudioWPatch not installed — loopback capture unavailable")
            return

        pa = pyaudio.PyAudio()
        try:
            # Resolve the loopback device
            if self._device_index is not None:
                device_info = pa.get_device_info_by_index(self._device_index)
            else:
                device_info = pa.get_default_wasapi_loopback()

            native_rate = int(device_info["defaultSampleRate"])
            channels = device_info["maxInputChannels"]
            logger.info(
                f"Loopback capture started: {device_info['name']} "
                f"({native_rate}Hz, {channels}ch)"
            )

            frames_per_buffer = int(native_rate * 0.5)  # 0.5s blocks

            # Use callback mode — PyAudioWPatch invokes this on its own thread
            # whenever audio data is available. During silence, WASAPI still
            # delivers zero-filled frames at the configured rate.
            def _stream_callback(in_data, frame_count, time_info, status):
                if not self._running:
                    return (None, pyaudio.paComplete)

                audio = np.frombuffer(in_data, dtype=np.float32)

                # Downmix to mono
                if channels > 1:
                    audio = audio.reshape(-1, channels).mean(axis=1)

                # Resample to 16kHz
                resampled = _resample_to_target(audio, native_rate)
                self._push_async(resampled)

                return (None, pyaudio.paContinue)

            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=native_rate,
                input=True,
                input_device_index=device_info["index"],
                frames_per_buffer=frames_per_buffer,
                stream_callback=_stream_callback,
            )

            stream.start_stream()

            try:
                # Keep the thread alive while the callback does the work
                while self._running and stream.is_active():
                    threading.Event().wait(timeout=0.2)
            finally:
                stream.stop_stream()
                stream.close()

        except Exception as e:
            logger.error(f"Loopback capture error: {e}", exc_info=True)
        finally:
            pa.terminate()
            logger.info("Loopback capture stopped")
            asyncio.run_coroutine_threadsafe(self._buffer.flush(), self._loop)


class FileCapture(CaptureBase):
    """
    Extract and decode audio from a local file (audio or video) via ffmpeg.
    Outputs 16kHz mono float32 PCM without intermediate temp files.

    Args:
        start_offset_ms: If > 0, seek to this position before reading.
                         Used to resume an interrupted pipeline from the last
                         committed segment's end time.
    """

    def __init__(
        self,
        buffer: AudioBuffer,
        loop: asyncio.AbstractEventLoop,
        file_path: str,
        start_offset_ms: int = 0,
    ):
        super().__init__(buffer, loop)
        self._file_path = file_path
        self._start_offset_ms = start_offset_ms

    def _capture_loop(self) -> None:
        file_path = Path(self._file_path)
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            asyncio.run_coroutine_threadsafe(self._buffer.flush(), self._loop)
            return

        if self._start_offset_ms > 0:
            logger.info(
                f"File capture started (resuming from {self._start_offset_ms}ms): {file_path}"
            )
        else:
            logger.info(f"File capture started: {file_path}")

        # Build ffmpeg command; seek with -ss before -i for fast stream seek
        ffmpeg_cmd = [settings.ffmpeg_path]
        if self._start_offset_ms > 0:
            start_s = self._start_offset_ms / 1000.0
            ffmpeg_cmd += ["-ss", f"{start_s:.3f}"]

        ffmpeg_cmd += [
            "-i", str(file_path),
            "-vn",                   # discard video
            "-ar", str(TARGET_RATE), # resample to 16kHz
            "-ac", "1",              # mono
            "-f", "f32le",           # raw float32 little-endian PCM
            "-loglevel", "error",
            "pipe:1",                # output to stdout
        ]

        _no_window = 0x08000000 if subprocess.sys.platform == "win32" else 0
        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_no_window,
            )

            # Read in 0.5-second blocks (16000 samples * 4 bytes = 32000 bytes per 0.5s)
            bytes_per_block = TARGET_RATE * 4  # 0.5s of float32 mono at 16kHz → actually 1s
            # Let's do 0.5s: 16000 * 0.5 * 4 = 32000 bytes
            bytes_per_block = int(TARGET_RATE * 0.5) * 4

            while self._running:
                raw = process.stdout.read(bytes_per_block)
                if not raw:
                    break  # EOF

                audio = np.frombuffer(raw, dtype=np.float32)
                self._push_async(audio)

            # Read any remaining data after loop exit
            if self._running:
                remaining = process.stdout.read()
                if remaining and len(remaining) >= 4:
                    audio = np.frombuffer(remaining, dtype=np.float32)
                    self._push_async(audio)

            process.wait(timeout=10)
            stderr_output = process.stderr.read().decode("utf-8", errors="replace")
            if process.returncode != 0:
                logger.error(f"ffmpeg exited with code {process.returncode}: {stderr_output}")
            elif stderr_output:
                logger.debug(f"ffmpeg stderr: {stderr_output}")

        except FileNotFoundError:
            logger.error(
                f"ffmpeg not found at '{settings.ffmpeg_path}'. "
                "Please install ffmpeg and add it to PATH, or set SS_FFMPEG_PATH."
            )
        except Exception as e:
            logger.error(f"File capture error: {e}", exc_info=True)
        finally:
            logger.info("File capture stopped")
            asyncio.run_coroutine_threadsafe(self._buffer.flush(), self._loop)


# ---------------------------------------------------------------------------
# Convenience: video file preparation for browser playback
# ---------------------------------------------------------------------------

def prepare_video_for_playback(input_path: str, output_dir: str) -> Optional[str]:
    """
    If the video is not MP4(H.264+AAC), remux/transcode it for browser playback.
    Returns the path to the playback-ready MP4, or None on failure.

    This runs synchronously — call from a thread or at project import time.
    """
    input_file = Path(input_path)
    if not input_file.exists():
        return None

    output_path = Path(output_dir) / f"{input_file.stem}_playback.mp4"

    # If already a .mp4 file, try a fast remux first
    # (handles cases where codec is already H.264 — just fixes container)
    cmd = [
        settings.ffmpeg_path,
        "-i", str(input_file),
        "-c:v", "copy",        # try to copy video stream as-is
        "-c:a", "aac",         # ensure audio is AAC
        "-movflags", "+faststart",
        "-y",                  # overwrite output
        "-loglevel", "error",
        str(output_path),
    ]

    _no_window = 0x08000000 if subprocess.sys.platform == "win32" else 0
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                                creationflags=_no_window)
        if result.returncode == 0:
            logger.info(f"Video remuxed for playback: {output_path}")
            return str(output_path)

        # Remux failed (incompatible codec) — full transcode
        logger.info("Remux failed, falling back to full transcode...")
        cmd_transcode = [
            settings.ffmpeg_path,
            "-i", str(input_file),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-movflags", "+faststart",
            "-y",
            "-loglevel", "error",
            str(output_path),
        ]
        result = subprocess.run(cmd_transcode, capture_output=True, text=True, timeout=3600,
                                creationflags=_no_window)
        if result.returncode == 0:
            logger.info(f"Video transcoded for playback: {output_path}")
            return str(output_path)
        else:
            logger.error(f"Video transcode failed: {result.stderr}")
            return None

    except FileNotFoundError:
        logger.error("ffmpeg not found — cannot prepare video for playback")
        return None
    except subprocess.TimeoutExpired:
        logger.error("Video preparation timed out")
        return None


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

def create_capture(
    mode: str,
    buffer: AudioBuffer,
    loop: asyncio.AbstractEventLoop,
    device_index: Optional[int] = None,
    file_path: Optional[str] = None,
    start_offset_ms: int = 0,
) -> CaptureBase:
    """
    Factory: create the appropriate capture backend.

    Args:
        mode: "microphone", "loopback", or "file"
        buffer: AudioBuffer to push samples into
        loop: The running asyncio event loop
        device_index: Audio device index (for mic/loopback modes)
        file_path: Path to audio/video file (for file mode)
        start_offset_ms: Seek offset in ms for file mode (0 = from start).
                         Set to the last committed segment's end_ms to resume.

    Returns:
        A CaptureBase instance (not yet started — call .start())
    """
    if mode == "microphone":
        return MicrophoneCapture(buffer, loop, device_index)
    elif mode == "loopback":
        return LoopbackCapture(buffer, loop, device_index)
    elif mode == "file":
        if not file_path:
            raise ValueError("file_path is required for file capture mode")
        return FileCapture(buffer, loop, file_path, start_offset_ms=start_offset_ms)
    else:
        raise ValueError(f"Unknown capture mode: {mode}")
