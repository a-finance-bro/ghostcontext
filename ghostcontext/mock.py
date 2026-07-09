"""Hardware-free transcript source: replays a JSONL script over your LIVE screen
activity, so anyone can see GhostContext work without BlackHole, a mic, or STT
model downloads. Each line: {"t": <seconds>, "source": "me"|"them", "text": "..."}

Run with:  python -m ghostcontext --mock examples/mock_transcript.jsonl
Talk-track plays back on schedule while the focus/click/scroll hooks + AX capture
run for real against whatever's actually on your screen.
"""

from __future__ import annotations

import json
import threading
import time
from typing import List, Tuple

from .clock import SessionClock
from .transcript import SegmentHandler


class MockSource:
    def __init__(self, path: str, clock: SessionClock, handler: SegmentHandler) -> None:
        self._path = path
        self._clock = clock
        self._handler = handler
        self._stop = threading.Event()
        self._thread: threading.Thread = None  # type: ignore

    def _load(self) -> List[Tuple[float, str, str]]:
        rows: List[Tuple[float, str, str]] = []
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                obj = json.loads(line)
                rows.append((float(obj["t"]), str(obj.get("source", "me")), str(obj["text"])))
        rows.sort(key=lambda r: r[0])
        return rows

    def _run(self) -> None:
        for at, source, text in self._load():
            if self._stop.is_set():
                return
            wait = at - self._clock.elapsed()
            if wait > 0:
                if self._stop.wait(timeout=wait):
                    return
            self._handler.handle(source, text)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mock-stt", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
