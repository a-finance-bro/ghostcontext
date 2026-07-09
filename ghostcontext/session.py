"""A recording Session + the SessionController the web UI / CLI drive.

A Session owns one run's whole pipeline and writes its artifacts into a
timestamped folder under `sessions/`:
  session.xml      — the structured, delta-compressed timeline (always)
  transcript.txt   — human-readable speaker-attributed transcript (always)
  screen.mov       — raw screen recording   (test mode only)
  audio.wav        — raw mic+system audio    (test mode only)

The SessionController is a thin, thread-safe start/stop wrapper so the HTTP
handlers (on server threads) can drive recording while the macOS focus
notifications + run loop live on the main thread.
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from . import __version__, enrich, macos
from .bus import ContextTracker, EventBus
from .capture_input import InputHook
from .clock import SessionClock
from .config import Config
from .deictic import DeicticDetector
from .delta import DeltaCompressor
from .events import KIND_FOCUS, Event
from .extractors import DeepExtractor
from .mock import MockSource
from .recorder import Recorder
from .transcript import SegmentHandler
from .xml_writer import XmlTimelineWriter


@dataclass
class SessionOptions:
    action_metadata: bool = True     # log clicks/scrolls/focus as timeline events
    captures_enabled: bool = True    # deictic markers fire voice-triggered captures
    test_mode: bool = False          # also record raw screen.mov + audio.wav
    mock_path: Optional[str] = None  # replay a transcript instead of live STT
    output_dir: str = "sessions"


class Session:
    def __init__(self, config: Config, opts: SessionOptions) -> None:
        self.config = config
        self.opts = opts
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.id = uuid.uuid4().hex[:12]
        self.dir = os.path.join(opts.output_dir, f"session-{stamp}")
        self.xml_path = os.path.join(self.dir, "session.xml")
        self.transcript_path = os.path.join(self.dir, "transcript.txt")
        self.screen_path = os.path.join(self.dir, "screen.mov")
        self.audio_path = os.path.join(self.dir, "audio.wav")
        self.demo_video_path = os.path.join(self.dir, "demo-video.mp4")

        self.clock = SessionClock()
        self.bus = EventBus()
        self.tracker = ContextTracker()
        self._transcript: List[dict] = []
        self._tlock = threading.Lock()
        self.audio_error: Optional[str] = None

        self.deep = DeepExtractor(config.extraction, self.dir)
        self.handler = SegmentHandler(
            self.bus, self.tracker, self.clock,
            DeicticDetector(config.markers),
            on_display=self._on_display,
            captures_enabled=opts.captures_enabled,
            deep_extractor=self.deep,
            cooldown_seconds=config.capture_cooldown_seconds,
        )
        self.recorder = Recorder(
            self.bus, DeltaCompressor(config.keyframe_interval_seconds),
            XmlTimelineWriter(self.xml_path, digest=config.extraction.digest),
        )
        self.input_hook = InputHook(
            self.bus, self.tracker, self.clock, config,
            enrich.current_screen_context, emit_actions=opts.action_metadata,
        )
        self.audio = None
        self.screen = None

    # -- live transcript sink -------------------------------------------------
    def _on_display(self, row: dict) -> None:
        with self._tlock:
            self._transcript.append(row)

    def transcript_snapshot(self) -> List[dict]:
        with self._tlock:
            return list(self._transcript)

    # -- focus (called by the global observer via the controller) -------------
    def on_focus_change(self, trigger: str = "app_activated") -> None:
        ctx, _pid = enrich.current_screen_context()
        cursor = macos.cursor_position()
        # Enrich the keyframe: DOM for browsers, + a screenshot/OCR when the
        # screenshot mode includes focus events. No-op cost when nothing applies.
        try:
            ctx = self.deep.enrich(ctx, cursor, self.clock.elapsed(), "focus")
        except Exception:
            pass
        self.tracker.update(ctx)  # always keep context fresh (captures need it)
        if self.opts.action_metadata:
            self.bus.emit(
                Event(t=self.clock.elapsed(), kind=KIND_FOCUS, trigger=trigger,
                      context=ctx, cursor=cursor)
            )

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        self.recorder.start(
            session_id=self.id, started_at=self.clock.started_at_iso(),
            version=__version__, audio_desc=self._audio_desc(),
        )
        self.input_hook.start()

        if self.opts.mock_path:
            self.audio = MockSource(self.opts.mock_path, self.clock, self.handler)
        else:
            from .audio_stt import SttSource

            self.audio = SttSource(
                self.config.audio, self.handler,
                raw_wav_path=self.audio_path if self.opts.test_mode else None,
            )
        try:
            self.audio.start()
        except Exception as exc:
            self.audio_error = str(exc)

        if self.opts.test_mode:
            from .screen import ScreenRecorder

            self.screen = ScreenRecorder(self.screen_path)
            self.screen.start()

        self.on_focus_change("session_start")

    def stop(self) -> dict:
        for comp in (self.audio, self.input_hook, self.screen):
            if comp is not None:
                try:
                    comp.stop()
                except Exception:
                    pass
        self.recorder.stop()
        self._write_transcript_txt()
        # Test mode: mux the raw screen + audio into one shareable demo-video.mp4
        # (the separate screen.mov / audio.wav are kept too).
        if self.opts.test_mode:
            try:
                from . import media

                media.combine_av(self.screen_path, self.audio_path, self.demo_video_path)
            except Exception:
                pass
        return self.artifacts()

    def _write_transcript_txt(self) -> None:
        try:
            with open(self.transcript_path, "w", encoding="utf-8") as fh:
                fh.write(f"# GhostContext transcript — session {self.id}\n\n")
                for row in self.transcript_snapshot():
                    t = SessionClock.stamp(row["t"])
                    tag = f'  ⟶ capture: "{row["deictic"]}"' if row.get("deictic") else ""
                    fh.write(f"[{t}] {row['source']}: {row['text']}{tag}\n")
        except Exception:
            pass

    # -- introspection --------------------------------------------------------
    def _audio_desc(self) -> str:
        if self.opts.mock_path:
            return f"mock transcript: {self.opts.mock_path}"
        a = self.config.audio
        srcs = ", ".join(f"{ch}:{s}" for ch, s in a.channel_sources.items())
        return f"faster-whisper {a.whisper_model} over '{a.device}' channels[{srcs}]"

    def artifacts(self) -> dict:
        out = {"dir": self.dir, "xml": self.xml_path, "transcript": self.transcript_path}
        if self.opts.test_mode:
            if os.path.exists(self.demo_video_path):
                out["demo_video"] = self.demo_video_path
            out["screen"] = self.screen_path
            out["audio"] = self.audio_path
        return out

    def status(self) -> dict:
        ctx = self.tracker.snapshot()
        return {
            "id": self.id,
            "elapsed": round(self.clock.elapsed(), 1),
            "events": self.recorder.count,
            "transcript": [
                {"t": SessionClock.stamp(r["t"]), "source": r["source"],
                 "text": r["text"], "deictic": r.get("deictic")}
                for r in self.transcript_snapshot()
            ],
            "current": {
                "app": ctx.app_name, "window": ctx.window_title, "url": ctx.url,
                "file": (ctx.code.file if ctx.code else None),
                "line": (ctx.code.line if ctx.code else None),
            },
            "audio_error": self.audio_error,
            "options": {
                "action_metadata": self.opts.action_metadata,
                "captures_enabled": self.opts.captures_enabled,
                "test_mode": self.opts.test_mode,
                "mock": bool(self.opts.mock_path),
            },
        }


class SessionController:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._session: Optional[Session] = None
        self._lock = threading.Lock()

    def start(self, opts: SessionOptions) -> dict:
        with self._lock:
            if self._session is not None:
                return {"error": "already recording"}
            session = Session(self.config, opts)
            session.start()
            self._session = session
            return session.status()

    def stop(self) -> dict:
        with self._lock:
            if self._session is None:
                return {"error": "not recording"}
            artifacts = self._session.stop()
            self._session = None
            return {"stopped": True, "artifacts": artifacts}

    def is_recording(self) -> bool:
        return self._session is not None

    def handle_focus_change(self) -> None:
        session = self._session
        if session is not None:
            try:
                session.on_focus_change()
            except Exception:
                pass

    def status(self) -> dict:
        session = self._session
        if session is None:
            return {"recording": False}
        st = session.status()
        st["recording"] = True
        return st
