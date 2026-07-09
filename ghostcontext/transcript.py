"""Turns transcript segments (from real STT or the mock player) into bus events.

For every segment we emit an <utterance>. If it contains a deictic marker we ALSO
emit a voice-triggered <capture>: a snapshot of the last-known screen context
(from the main-thread hooks) plus a FRESH cursor position — i.e. exactly what the
speaker was pointing at when they said "look at this". This is the feature that
replaces the video's manual "take screenshot" button."""

from __future__ import annotations

from typing import Callable, Optional

from . import macos
from .bus import ContextTracker, EventBus
from .clock import SessionClock
from .deictic import DeicticDetector
from .events import KIND_CAPTURE, KIND_UTTERANCE, Event


class SegmentHandler:
    def __init__(
        self,
        bus: EventBus,
        tracker: ContextTracker,
        clock: SessionClock,
        detector: DeicticDetector,
        on_display: Optional[Callable[[dict], None]] = None,
        captures_enabled: bool = True,
        deep_extractor=None,
        cooldown_seconds: float = 1.5,
    ) -> None:
        self._bus = bus
        self._tracker = tracker
        self._clock = clock
        self._detector = detector
        # Optional live-transcript sink (the web UI appends to its transcript).
        self._on_display = on_display
        # Whether deictic markers fire voice-triggered captures (UI toggle).
        self._captures_enabled = captures_enabled
        # DeepExtractor run at capture time (DOM element-under-cursor + screenshot/OCR).
        self._deep = deep_extractor
        # Coalesce rapid marker hits so aggressive markers don't fire a burst.
        self._cooldown = cooldown_seconds
        self._last_capture_t = -1e9

    def handle(self, source: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        now = self._clock.elapsed()

        # 1) The utterance itself.
        self._bus.emit(
            Event(t=now, kind=KIND_UTTERANCE, trigger="speech", source=source, text=text)
        )

        # 2) A voice-triggered capture, if the speaker pointed at something —
        #    unless we're still within the cooldown from the last one.
        phrase = self._detector.find(text) if self._captures_enabled else None
        fired = None
        if phrase and (now - self._last_capture_t) >= self._cooldown:
            fired = phrase
            self._last_capture_t = now
        if self._on_display is not None:
            self._on_display({"t": now, "source": source, "text": text, "deictic": fired})
        if fired:
            phrase = fired
            ctx = self._tracker.snapshot()
            cursor = macos.cursor_position()
            # Deep-extract exactly what was pointed at: DOM element under the
            # cursor + a screenshot/OCR of the cursor region, fused onto the
            # last-known screen context.
            if self._deep is not None:
                try:
                    ctx = self._deep.enrich(ctx, cursor, now, "capture")
                except Exception:
                    pass
            self._bus.emit(
                Event(
                    t=now,
                    kind=KIND_CAPTURE,
                    trigger="deictic",
                    context=ctx,
                    cursor=cursor,
                    text=phrase,
                    source=source,
                )
            )
