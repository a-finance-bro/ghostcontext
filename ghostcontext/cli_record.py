"""`gcrecord` — the one-liner recorder.

    gcrecord                 # start the balanced config; press Enter to stop
    gcrecord --test          # also record raw screen + audio + demo-video.mp4
    gcrecord --mock f.jsonl  # replay a transcript instead of live mic
    gcrecord --no-dom --no-ocr --no-screenshots --region window --output ~/recordings

Balanced default = action metadata + voice captures + screenshots/OCR on captures
+ browser DOM, test mode OFF. On stop it prints the output path, length, and a few
metrics, and opens the session folder in Finder.
"""

from __future__ import annotations

import argparse
import select
import subprocess
import sys

from . import macos, report
from .capture_focus import FocusHook
from .config import Config
from .session import SessionController, SessionOptions


def _build_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="gcrecord", description="Record a session to a structured XML timeline.")
    p.add_argument("--test", action="store_true", help="Also record raw screen.mov + audio.wav + demo-video.mp4.")
    p.add_argument("--mock", metavar="FILE", help="Replay a JSONL transcript instead of live mic.")
    p.add_argument("--device", help="Audio input device name (substring match).")
    p.add_argument("--output", "-o", default="sessions", help="Output folder (default ./sessions).")
    p.add_argument("--region", choices=["cursor", "window", "full"], help="Screenshot region (default cursor).")
    p.add_argument("--no-dom", action="store_true", help="Disable the browser-DOM layer.")
    p.add_argument("--no-ocr", action="store_true", help="Disable OCR of screenshots.")
    p.add_argument("--no-screenshots", action="store_true", help="Disable screenshots + OCR entirely.")
    p.add_argument("--no-actions", action="store_true", help="Don't log clicks/scrolls/focus as events.")
    p.add_argument("--no-open", action="store_true", help="Don't open the session folder in Finder on stop.")
    return p.parse_args(argv)


def _config_from_args(args: argparse.Namespace) -> Config:
    cfg = Config()  # the balanced defaults live here
    if args.device:
        cfg.audio.device = args.device
    if args.region:
        cfg.extraction.region = args.region
    if args.no_screenshots:
        cfg.extraction.screenshot_mode = "off"
    if args.no_ocr:
        cfg.extraction.ocr_enabled = False
    if args.no_dom:
        cfg.extraction.dom_enabled = False
    return cfg


def _wait_for_enter() -> None:
    """Pump the macOS run loop (so focus notifications fire) while watching stdin;
    return when the user presses Enter (or Ctrl-C)."""
    try:
        from Foundation import NSDate, NSRunLoop

        rl = NSRunLoop.currentRunLoop()
        while True:
            rl.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if ready:
                sys.stdin.readline()
                return
    except KeyboardInterrupt:
        return
    except Exception:
        try:
            input()  # non-macOS fallback: just block on Enter
        except (EOFError, KeyboardInterrupt):
            return


def main(argv=None) -> int:
    args = _build_args(sys.argv[1:] if argv is None else argv)
    cfg = _config_from_args(args)

    if not macos.accessibility_trusted():
        print("⚠️  Accessibility not granted — titles/lines/URLs will be blank. "
              "Grant it in System Settings → Privacy & Security → Accessibility.\n", file=sys.stderr)

    controller = SessionController(cfg)
    focus = FocusHook(controller.handle_focus_change)
    try:
        focus.start()
    except Exception:
        pass

    opts = SessionOptions(
        action_metadata=not args.no_actions,
        captures_enabled=True,
        test_mode=args.test,
        mock_path=args.mock,
        output_dir=args.output,
    )
    status = controller.start(opts)
    if status.get("error"):
        print(f"couldn't start: {status['error']}", file=sys.stderr)
        return 1

    mode = "TEST (raw screen+audio)" if args.test else "balanced"
    print(f"● gcrecord recording [{mode}] — press Enter to stop.")
    _wait_for_enter()
    print("■ stopping…")

    res = controller.stop()
    focus.stop()
    artifacts = res.get("artifacts", {})
    session_dir = artifacts.get("dir", opts.output_dir)

    s = report.summarize(session_dir)
    print()
    print(f"✓ session → {session_dir}")
    print(f"  length      {report.format_duration(s['duration_seconds'])}")
    print(f"  captures    {s['captures']}   (voice-triggered)")
    print(f"  utterances  {s['utterances']}")
    print(f"  events      {s['events']}   ({s['focus_changes']} focus · {s['clicks']} clicks · {s['scrolls']} scrolls)")
    print(f"  files       {len(s['files'])}   |  urls {len(s['urls'])}  |  screenshots {s['screenshots']}")
    print(f"  xml         {s['xml_bytes'] // 1024} KB  → {s['xml']}")
    if "demo_video" in artifacts:
        print(f"  video       {artifacts['demo_video']}")

    if not args.no_open:
        try:
            subprocess.Popen(["open", session_dir])
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
