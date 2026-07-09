"""Real-time local speech-to-text over mic + system audio.

Capture: one InputStream on an Aggregate Device (your mic + BlackHole, which
carries system output = the other people on the call). We split the device's
channels into per-SOURCE mono streams ("me" vs "them") using
`config.channel_sources`, so the transcript is speaker-attributed.

Transcription: non-overlapping `block_seconds` chunks per source, resampled to
16 kHz, run through faster-whisper locally (no cloud). Silent chunks are skipped.
This is a pragmatic streaming scheme for a prototype; production would use proper
VAD + endpointing. See `--list-audio` to find your device + channel order.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List

import numpy as np

from .config import AudioConfig
from .transcript import SegmentHandler

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None

_WHISPER_SR = 16000
_SILENCE_RMS = 0.006


_INSTALL_HINT = (
    "audio deps not installed — run inside the project venv:\n"
    "  python3.12 -m venv .venv && source .venv/bin/activate\n"
    "  pip install -r requirements.txt\n"
    "(faster-whisper needs CPython 3.9–3.12; your default python may be newer)."
)


def list_input_devices() -> list:
    """Structured input-device list for the web UI dropdown."""
    if sd is None:
        return []
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            out.append(
                {
                    "index": i,
                    "name": d["name"],
                    "channels": d["max_input_channels"],
                    "samplerate": int(d["default_samplerate"]),
                }
            )
    return out


def list_audio() -> str:
    """Human-readable device list for the README / `--list-audio`."""
    if sd is None:
        return _INSTALL_HINT
    lines = ["Input-capable audio devices (index: name  [in-channels @ default-SR]):"]
    for d in list_input_devices():
        lines.append(f"  {d['index']}: {d['name']}  [{d['channels']}ch @ {d['samplerate']}Hz]")
    return "\n".join(lines)


def _resample_to_16k(audio: np.ndarray, src_sr: int) -> np.ndarray:
    if src_sr == _WHISPER_SR or audio.size == 0:
        return audio.astype(np.float32)
    duration = audio.shape[0] / float(src_sr)
    tgt_len = int(round(duration * _WHISPER_SR))
    if tgt_len <= 1:
        return np.zeros(0, dtype=np.float32)
    xp = np.linspace(0.0, duration, num=audio.shape[0], endpoint=False)
    xt = np.linspace(0.0, duration, num=tgt_len, endpoint=False)
    return np.interp(xt, xp, audio).astype(np.float32)


class SttSource:
    def __init__(
        self, config: AudioConfig, handler: SegmentHandler, raw_wav_path: str = None
    ) -> None:
        self._cfg = config
        self._handler = handler
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stream = None
        self._worker: threading.Thread = None  # type: ignore
        self._model = None

        # source -> [channel indices]; source -> growing mono buffer.
        self._source_channels: Dict[str, List[int]] = {}
        for ch, src in config.channel_sources.items():
            self._source_channels.setdefault(src, []).append(ch)
        self._buffers: Dict[str, np.ndarray] = {
            src: np.zeros(0, dtype=np.float32) for src in self._source_channels
        }
        self._device_index = None
        self._device_sr = _WHISPER_SR
        self._open_channels = (max(config.channel_sources) + 1) if config.channel_sources else 1

        # Test-mode raw recording: a stereo WAV [me, them] written from the same
        # capture callback (no second device open). None disables it.
        self._raw_wav_path = raw_wav_path
        self._raw_sources = sorted(self._source_channels.keys())
        self._wav = None

    # -- setup ----------------------------------------------------------------
    def _resolve_device(self) -> None:
        if sd is None:
            raise RuntimeError(_INSTALL_HINT)
        want = (self._cfg.device or "").lower()
        chosen = None
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) <= 0:
                continue
            if not want or want in d["name"].lower():
                chosen = (i, d)
                break
        if chosen is None:
            # Fall back to the default input device.
            idx = sd.default.device[0]
            chosen = (idx, sd.query_devices(idx))
        self._device_index, d = chosen
        self._device_sr = int(d["default_samplerate"])
        self._open_channels = min(self._open_channels, d["max_input_channels"])

    def _load_model(self) -> None:
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "faster-whisper isn't available (it needs CPython 3.9–3.12). "
                "Use `--mock` for a hardware-free run, or a supported Python."
            ) from exc
        self._model = WhisperModel(
            self._cfg.whisper_model, device="cpu", compute_type=self._cfg.compute_type
        )

    # -- capture --------------------------------------------------------------
    def _callback(self, indata, _frames, _time, status) -> None:  # PortAudio thread
        if status:
            pass  # over/underflows are non-fatal for a prototype
        monos = {}
        with self._lock:
            for src, chans in self._source_channels.items():
                usable = [c for c in chans if c < indata.shape[1]]
                if not usable:
                    continue
                mono = indata[:, usable].mean(axis=1).astype(np.float32)
                monos[src] = mono
                self._buffers[src] = np.concatenate([self._buffers[src], mono])
        # Test-mode raw tap: interleave the per-source monos into a WAV.
        if self._wav is not None and monos:
            cols = [monos.get(s) for s in self._raw_sources if monos.get(s) is not None]
            if cols:
                stacked = np.stack(cols, axis=1)
                pcm = np.clip(stacked, -1.0, 1.0)
                self._wav.writeframes((pcm * 32767.0).astype("<i2").tobytes())

    def _run_worker(self) -> None:
        block = int(self._cfg.block_seconds * self._device_sr)
        while not self._stop.is_set():
            time.sleep(self._cfg.hop_seconds)
            for src in list(self._buffers.keys()):
                with self._lock:
                    buf = self._buffers[src]
                    if len(buf) < block:
                        continue
                    chunk = buf[:block]
                    self._buffers[src] = buf[block:]
                audio16 = _resample_to_16k(chunk, self._device_sr)
                if audio16.size == 0 or float(np.sqrt(np.mean(audio16 ** 2))) < _SILENCE_RMS:
                    continue
                text = self._transcribe(audio16)
                if text:
                    self._handler.handle(src, text)

    def _transcribe(self, audio16: np.ndarray) -> str:
        segments, _info = self._model.transcribe(
            audio16, language="en", vad_filter=True, beam_size=1
        )
        return " ".join(s.text.strip() for s in segments).strip()

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        self._resolve_device()
        self._load_model()
        # Only the sources that ACTUALLY have a channel on this device get written
        # (a mono mic has no "them" channel). The WAV channel count MUST match the
        # columns we write, or players mis-read the interleave and play it at the
        # wrong speed/pitch (the "chipmunk voice" bug).
        self._raw_sources = [
            s for s in sorted(self._source_channels)
            if any(c < self._open_channels for c in self._source_channels[s])
        ] or ["me"]
        if self._raw_wav_path:
            import os
            import wave

            os.makedirs(os.path.dirname(os.path.abspath(self._raw_wav_path)), exist_ok=True)
            self._wav = wave.open(self._raw_wav_path, "wb")
            self._wav.setnchannels(len(self._raw_sources))
            self._wav.setsampwidth(2)  # int16
            self._wav.setframerate(self._device_sr)
        self._stream = sd.InputStream(
            device=self._device_index,
            channels=self._open_channels,
            samplerate=self._device_sr,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()
        self._worker = threading.Thread(target=self._run_worker, name="stt", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None
