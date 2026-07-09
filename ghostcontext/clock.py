"""One monotonic session clock so every producer stamps against the same origin.

We keep TWO reads: a wall-clock ISO timestamp (for the session header) and a
monotonic elapsed value (for ordering + the compact `t="mm:ss.mmm"` stamps in
the timeline). Monotonic can't go backwards if the system clock is adjusted
mid-session, which matters when we're interleaving events from four threads.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone


class SessionClock:
    def __init__(self) -> None:
        self._t0_monotonic = time.monotonic()
        self._t0_wall = datetime.now(timezone.utc)

    def elapsed(self) -> float:
        """Seconds since session start (monotonic)."""
        return time.monotonic() - self._t0_monotonic

    @staticmethod
    def stamp(elapsed_seconds: float) -> str:
        """Compact `mm:ss.mmm` used for every timeline entry — cheap for an LLM
        to read and to reason about ordering/duration."""
        if elapsed_seconds < 0:
            elapsed_seconds = 0.0
        minutes, seconds = divmod(elapsed_seconds, 60)
        return f"{int(minutes):02d}:{seconds:06.3f}"

    def started_at_iso(self) -> str:
        return self._t0_wall.replace(microsecond=0).isoformat()
