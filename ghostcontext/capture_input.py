"""Mouse + keyboard hooks via pynput, all event-driven.

Clicks capture the cursor X/Y (the "pointing" signal) and re-read the active
context (catches browser tab switches). Scrolls are accumulated and only emitted
once they cross a tick threshold, so idle wheel jitter doesn't spam the timeline.
A mouse-up ends either a text SELECTION (read from Accessibility) or a DRAG. The
keyboard hook logs only the copy chord (⌘C/⌘X → the copied text) and, while
typing, the focused field's VALUE — it never records general keystrokes.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

try:
    from pynput import mouse
except Exception:  # pragma: no cover
    mouse = None
try:
    from pynput import keyboard
except Exception:  # pragma: no cover
    keyboard = None

from . import macos
from .bus import ContextTracker, EventBus
from .clock import SessionClock
from .config import Config
from .events import (
    KIND_CLICK,
    KIND_COPY,
    KIND_DRAG,
    KIND_SCROLL,
    KIND_SELECTION,
    KIND_VALUE,
    Event,
    ScreenContext,
)


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
        # When False, actions still keep the context tracker fresh (so voice-
        # triggered captures have accurate state) but are NOT logged as their own
        # timeline events. This is the UI's "action metadata" switch.
        self._emit_actions = emit_actions
        self._listener = None
        self._kb = None
        self._scroll_accum = 0
        self._last_scroll_emit = -1e9
        # selection / drag / value tracking
        self._down_pos: Optional[tuple] = None
        self._last_selection: Optional[str] = None
        self._last_value: Optional[str] = None
        self._last_value_emit = -1e9
        self._cmd_down = False

    def _refresh_context(self) -> ScreenContext:
        ctx, _ = self._enrich()
        self._tracker.update(ctx)
        return ctx

    def _emit(self, kind: str, *, trigger: str, cursor=None, text=None, note=None, meta=None) -> None:
        ctx = self._refresh_context()
        if not self._emit_actions:
            return
        self._bus.emit(
            Event(
                t=self._clock.elapsed(),
                kind=kind,
                trigger=trigger,
                context=ctx,
                cursor=cursor,
                text=text,
                note=note,
                meta=meta or {},
            )
        )

    # -- mouse ----------------------------------------------------------------
    def _on_click(self, x, y, button, pressed) -> None:
        if pressed:
            self._down_pos = (int(x), int(y))
            self._emit(
                KIND_CLICK,
                trigger="click",
                cursor=(int(x), int(y)),
                note=f"{getattr(button, 'name', 'left')} click",
            )
            return
        self._on_mouse_up(int(x), int(y))

    def _on_mouse_up(self, x, y) -> None:
        pid = macos.frontmost_app()[2]
        # A selection takes priority: a highlight is the clearest "look at this".
        if self._config.capture_selection:
            sel = macos.focused_selected_text(pid)
            sel = sel.strip() if sel else None
            if sel and sel != self._last_selection:
                self._last_selection = sel
                self._emit(KIND_SELECTION, trigger="selection", cursor=(x, y), text=sel[:240])
                return
        # Otherwise, a release that moved far enough is a drag.
        if self._config.capture_drag and self._down_pos:
            fx, fy = self._down_pos
            if math.hypot(x - fx, y - fy) > self._config.drag_min_pixels:
                self._emit(
                    KIND_DRAG,
                    trigger="drag",
                    cursor=(x, y),
                    note=f"dragged from ({fx},{fy})",
                    meta={"from": (fx, fy), "to": (x, y)},
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
        direction = "up" if ticks > 0 else "down"
        self._emit(
            KIND_SCROLL,
            trigger="scroll",
            cursor=(int(x), int(y)),
            note=f"scrolled {direction} {abs(ticks)}",
        )

    # -- keyboard (copy chord + field edits only) -----------------------------
    def _is_cmd(self, key) -> bool:
        return keyboard is not None and key in (
            keyboard.Key.cmd,
            getattr(keyboard.Key, "cmd_l", keyboard.Key.cmd),
            getattr(keyboard.Key, "cmd_r", keyboard.Key.cmd),
        )

    def _on_press(self, key) -> None:
        try:
            if self._is_cmd(key):
                self._cmd_down = True
                return
            ch = getattr(key, "char", None)
            if self._cmd_down and ch in ("c", "x") and self._config.capture_copy:
                self._emit_copy()
                return
            if ch and not self._cmd_down and self._config.capture_value:
                self._maybe_emit_value()
        except Exception:
            pass

    def _on_release(self, key) -> None:
        if self._is_cmd(key):
            self._cmd_down = False

    def _emit_copy(self) -> None:
        text = macos.clipboard_text()
        if not text:
            return
        self._emit(KIND_COPY, trigger="copy", text=text[:240], note="copied")

    def _maybe_emit_value(self) -> None:
        now = self._clock.elapsed()
        if (now - self._last_value_emit) < self._config.value_min_interval_seconds:
            return
        val = macos.focused_value(macos.frontmost_app()[2])
        val = val.strip() if val else None
        if not val or val == self._last_value:
            return
        self._last_value = val
        self._last_value_emit = now
        self._emit(KIND_VALUE, trigger="value", text=val[:240], note="field edit")

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        if mouse is not None:
            self._listener = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
            self._listener.start()
        if keyboard is not None and (self._config.capture_copy or self._config.capture_value):
            self._kb = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
            self._kb.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._kb is not None:
            self._kb.stop()
            self._kb = None
