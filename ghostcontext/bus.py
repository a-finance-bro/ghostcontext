"""The event bus + the shared context tracker.

Threading model (this is the crux of the whole app):
  - MAIN thread runs the AppKit run loop → NSWorkspace focus notifications fire
    here, and this is where it's safe to call Accessibility / AppleScript.
  - A pynput listener thread reports clicks/scrolls (with X/Y).
  - An audio thread runs STT and the deictic trigger.
  - A single RECORDER thread drains the queue and writes XML.

Producers never serialize anything. The focus/input producers (on the main
thread) enrich + update the ContextTracker; the audio producer (off the main
thread) only READS the tracker's latest snapshot + a fresh, thread-safe cursor
position — so we never call the Accessibility API off the main thread.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional

from .events import Event, ScreenContext


class EventBus:
    """A thread-safe FIFO. Producers `emit`, the recorder `drain`s."""

    def __init__(self) -> None:
        self._q: "queue.Queue[Optional[Event]]" = queue.Queue()

    def emit(self, event: Event) -> None:
        self._q.put(event)

    def get(self, timeout: float = 0.5) -> Optional[Event]:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        """Sentinel that tells the recorder loop to flush + exit."""
        self._q.put(None)


class ContextTracker:
    """Latest known screen context, updated by the main-thread hooks and read by
    the audio thread at deictic-trigger time. Guarded by a lock because it's the
    one piece of state genuinely shared across threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current = ScreenContext()

    def update(self, ctx: ScreenContext) -> None:
        with self._lock:
            self._current = ctx

    def snapshot(self) -> ScreenContext:
        with self._lock:
            return self._current
