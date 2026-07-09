"""Mouse click + scroll hooks via pynput. Clicks capture the cursor X/Y (the
"pointing" signal) and re-read the active context (catches browser tab switches).
Scrolls are accumulated and only emitted once they cross a tick threshold, so
idle wheel jitter doesn't spam the timeline."""

from __future__ import annotations

from typing import Callable, Optional

try:
    from pynput import mouse
except Exception:  # pragma: no cover
    mouse = None

from .bus import ContextTracker, EventBus
from .clock import SessionClock
from .config import Config
from .events import KIND_CLICK, KIND_SCROLL, Event, ScreenContext


class InputHook:
    def __init__(
        self,
        bus: EventBus,
        tracker: ContextTracker,
        clock: SessionClock,
        config: Config,
        enrich: Callable[[], "tuple[ScreenContext, Optional[int]]"],
        emit_actions: bool = True,
    ) -> None:
        self._bus = bus
        self._tracker = tracker
        self._clock = clock
        self._config = config
        self._enrich = enrich
        # When False, clicks/scrolls still keep the context tracker fresh (so
        # voice-triggered captures have accurate state) but are NOT logged as
        # their own timeline events. This is the UI's "action metadata" switch.
        self._emit_actions = emit_actions
        self._listener = None
        self._scroll_accum = 0
        self._last_scroll_emit = -1e9

    def _refresh_context(self) -> ScreenContext:
        ctx, _ = self._enrich()
        self._tracker.update(ctx)
        return ctx

    def _on_click(self, x, y, button, pressed) -> None:
        if not pressed:
            return
        ctx = self._refresh_context()
        if not self._emit_actions:
            return
        self._bus.emit(
            Event(
                t=self._clock.elapsed(),
                kind=KIND_CLICK,
                trigger="click",
                context=ctx,
                cursor=(int(x), int(y)),
                note=f"{getattr(button, 'name', 'left')} click",
            )
        )

    def _on_scroll(self, x, y, dx, dy) -> None:
        # Accumulate ticks, but emit at most once per interval (coalescing the net
        # movement) so continuous scrolling doesn't flood the timeline.
        self._scroll_accum += int(dy)
        if abs(self._scroll_accum) < self._config.scroll_min_ticks:
            return
        now = self._clock.elapsed()
        if (now - self._last_scroll_emit) < self._config.scroll_min_interval_seconds:
            return  # rate-limited — keep accumulating, emit on the next tick past the gap
        ticks = self._scroll_accum
        self._scroll_accum = 0
        self._last_scroll_emit = now
        ctx = self._refresh_context()
        if not self._emit_actions:
            return
        direction = "up" if ticks > 0 else "down"
        self._bus.emit(
            Event(
                t=self._clock.elapsed(),
                kind=KIND_SCROLL,
                trigger="scroll",
                context=ctx,
                cursor=(int(x), int(y)),
                note=f"scrolled {direction} {abs(ticks)}",
            )
        )

    def start(self) -> None:
        if mouse is None:
            return
        self._listener = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
