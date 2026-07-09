"""The single consumer: drains the bus, runs delta compression, writes XML. Being
the ONLY serializer means ordering + compression have exactly one home, and the
producer threads stay dumb."""

from __future__ import annotations

import threading

from .bus import EventBus
from .delta import DeltaCompressor
from .xml_writer import XmlTimelineWriter


class Recorder:
    def __init__(self, bus: EventBus, delta: DeltaCompressor, writer: XmlTimelineWriter) -> None:
        self._bus = bus
        self._delta = delta
        self._writer = writer
        self._stop = threading.Event()
        self._thread: threading.Thread = None  # type: ignore
        self._count = 0

    def start(self, *, session_id: str, started_at: str, version: str, audio_desc: str) -> None:
        self._writer.start(
            session_id=session_id, started_at=started_at, version=version, audio_desc=audio_desc
        )
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            event = self._bus.get(timeout=0.3)
            if event is not None:
                self._process(event)

    def _process(self, event) -> None:
        self._writer.write_event(event, self._delta.plan(event))
        self._count += 1

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        # Drain anything still queued so the tail of the session isn't lost.
        while True:
            event = self._bus.get(timeout=0.05)
            if event is None:
                break
            self._process(event)
        self._writer.finalize()

    @property
    def count(self) -> int:
        return self._count
