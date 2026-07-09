"""GhostContext entrypoint — wires the pipeline and pumps the macOS run loop.

Usage:
  python -m ghostcontext                         # live: mic+system STT + hooks
  python -m ghostcontext --mock examples/mock_transcript.jsonl   # no hardware
  python -m ghostcontext --list-audio            # find your device + channels
  python -m ghostcontext --output sessions/demo.xml --duration 120
"""

from __future__ import annotations

import argparse
import sys
import uuid

from . import __version__, enrich, macos
from .bus import ContextTracker, EventBus
from .capture_focus import FocusHook
from .capture_input import InputHook
from .clock import SessionClock
from .config import Config
from .deictic import DeicticDetector
from .delta import DeltaCompressor
from .events import KIND_FOCUS, Event
from .mock import MockSource
from .recorder import Recorder
from .transcript import SegmentHandler
from .xml_writer import XmlTimelineWriter


def _parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ghostcontext", description="Agent-friendly session recorder for macOS.")
    p.add_argument("--output", "-o", help="Output XML path (default sessions/session.xml).")
    p.add_argument("--mock", help="Replay a JSONL transcript instead of live STT (no hardware needed).")
    p.add_argument("--device", help="Audio input device name (substring match) for live STT.")
    p.add_argument("--duration", type=float, help="Auto-stop after N seconds (else Ctrl-C).")
    p.add_argument("--list-audio", action="store_true", help="List input devices + channels and exit.")
    p.add_argument("--web", action="store_true", help="Launch the web control panel instead of the CLI recorder.")
    p.add_argument("--port", type=int, default=8765, help="Port for --web (default 8765).")
    p.add_argument("--no-browser", action="store_true", help="With --web, run the HTTP API without opening a browser (used by the MCP server).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if args.list_audio:
        from .audio_stt import list_audio
        print(list_audio())
        return 0

    config = Config()
    if args.output:
        config.output_path = args.output
    if args.device:
        config.audio.device = args.device

    if args.web:
        from .webui import run_web

        return run_web(config, port=args.port, open_browser=not args.no_browser)

    if not macos.accessibility_trusted():
        print(
            "⚠️  Accessibility permission not granted — window titles, URLs, and\n"
            "    editor line numbers will be blank. Grant it to your terminal in\n"
            "    System Settings → Privacy & Security → Accessibility, then re-run.\n",
            file=sys.stderr,
        )

    # --- build the pipeline --------------------------------------------------
    clock = SessionClock()
    bus = EventBus()
    tracker = ContextTracker()
    detector = DeicticDetector(config.markers)
    handler = SegmentHandler(bus, tracker, clock, detector)
    delta = DeltaCompressor(config.keyframe_interval_seconds)
    writer = XmlTimelineWriter(config.output_path)
    recorder = Recorder(bus, delta, writer)

    def on_focus_change(trigger: str = "app_activated") -> None:
        ctx, _pid = enrich.current_screen_context()
        tracker.update(ctx)
        bus.emit(
            Event(
                t=clock.elapsed(),
                kind=KIND_FOCUS,
                trigger=trigger,
                context=ctx,
                cursor=macos.cursor_position(),
            )
        )

    focus_hook = FocusHook(on_focus_change)
    input_hook = InputHook(bus, tracker, clock, config, enrich.current_screen_context)

    if args.mock:
        source = MockSource(args.mock, clock, handler)
        audio_desc = f"mock transcript: {args.mock}"
    else:
        from .audio_stt import SttSource
        source = SttSource(config.audio, handler)
        srcs = ", ".join(f"{ch}:{s}" for ch, s in config.audio.channel_sources.items())
        audio_desc = (
            f"faster-whisper {config.audio.whisper_model} over device "
            f"'{config.audio.device}' channels[{srcs}]"
        )

    # --- start ---------------------------------------------------------------
    recorder.start(
        session_id=uuid.uuid4().hex[:12],
        started_at=clock.started_at_iso(),
        version=__version__,
        audio_desc=audio_desc,
    )
    focus_hook.start()
    input_hook.start()
    try:
        source.start()
    except Exception as exc:
        print(f"⚠️  Audio/STT source failed to start: {exc}\n    (try --mock for a hardware-free run)", file=sys.stderr)

    on_focus_change("session_start")  # open the timeline with a full keyframe
    print(f"● GhostContext recording → {config.output_path}   (Ctrl-C to stop)")

    # --- pump the macOS run loop so NSWorkspace notifications fire -----------
    _run_loop(clock, args.duration)

    # --- stop + flush --------------------------------------------------------
    print("\n■ stopping…")
    try:
        source.stop()
    except Exception:
        pass
    input_hook.stop()
    focus_hook.stop()
    recorder.stop()
    print(f"✓ wrote {recorder.count} events → {config.output_path}")
    return 0


def _run_loop(clock: SessionClock, duration) -> None:
    """Pump the run loop in short slices so notifications fire while staying
    responsive to Ctrl-C and the optional --duration auto-stop."""
    try:
        from Foundation import NSDate, NSRunLoop

        rl = NSRunLoop.currentRunLoop()
        while duration is None or clock.elapsed() < duration:
            rl.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.25))
    except KeyboardInterrupt:
        pass
    except Exception:
        # No AppKit run loop available (non-macOS) — fall back to a plain wait.
        import time

        try:
            while duration is None or clock.elapsed() < duration:
                time.sleep(0.25)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
