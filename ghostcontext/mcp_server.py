"""GhostContext MCP server — let an AI agent drive recordings and read the result.

Exposes tools over MCP (stdio):
  • start_recording / stop_recording / recording_status
  • list_sessions
  • read_session  → the structured session.xml text, so the agent can ingest
    exactly what happened on screen (the whole point).

Architecture: this is a thin bridge. The recorder itself needs the macOS run loop,
so on first use we spawn the GhostContext HTTP backend
(`python -m ghostcontext --web --no-browser`) as a subprocess and proxy tool calls
to its local API; session files are read straight off disk. Install with the extra:

    pip install "ghostcontext[mcp]"       # or: pip install mcp

then register it with your MCP client, command: `ghostcontext-mcp`.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

from . import report

_PORT = int(os.environ.get("GHOSTCONTEXT_PORT", "8765"))
_BASE = f"http://127.0.0.1:{_PORT}"
_backend_proc = None


def _get(path: str) -> dict:
    with urllib.request.urlopen(_BASE + path, timeout=5) as r:
        return json.loads(r.read())


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        _BASE + path, data=data, headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _backend_up() -> bool:
    try:
        _get("/api/status")
        return True
    except Exception:
        return False


def _ensure_backend() -> None:
    """Start the headless GhostContext backend if it isn't already running."""
    global _backend_proc
    if _backend_up():
        return
    _backend_proc = subprocess.Popen(
        [sys.executable, "-m", "ghostcontext", "--web", "--no-browser", "--port", str(_PORT)],
        cwd=os.getcwd(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):  # up to ~10s
        if _backend_up():
            return
        time.sleep(0.25)


def _latest_session_dir() -> str:
    dirs = sorted(glob.glob("sessions/session-*"), reverse=True)
    return dirs[0] if dirs else ""


try:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("ghostcontext")

    @mcp.tool()
    def start_recording(
        test_mode: bool = False,
        mock_path: str = "",
        dom: bool = True,
        ocr: bool = True,
        screenshots: str = "captures",
    ) -> dict:
        """Start a GhostContext recording of the user's screen + narration.

        Use this before asking the user to do or show something; it captures window/
        app changes, clicks, scrolls, their spoken transcript, and — when they say a
        pointer phrase like "look at this" — the app/URL/code/DOM/OCR of what they
        pointed at. Call stop_recording when done, then read_session to ingest it.

        Args:
            test_mode: also save raw screen video + audio + a combined demo-video.mp4.
            mock_path: replay a JSONL transcript instead of the live mic (testing).
            dom: enable the live browser-DOM layer.
            ocr: OCR the cursor-region screenshots into text.
            screenshots: when to grab screenshots — "off" | "captures" | "events" | "all".
        """
        _ensure_backend()
        return _post("/api/start", {
            "action_metadata": True, "captures_enabled": True,
            "test_mode": test_mode, "mock": bool(mock_path), "mock_path": mock_path,
            "dom": dom, "ocr": ocr, "shot_mode": screenshots,
        })

    @mcp.tool()
    def stop_recording() -> dict:
        """Stop the current recording. Returns the artifact paths plus a summary
        (length, capture/event counts, files, URLs). Follow with read_session."""
        res = _post("/api/stop", {})
        d = (res.get("artifacts") or {}).get("dir") or _latest_session_dir()
        return {"artifacts": res.get("artifacts"), "summary": report.summarize(d) if d else {}}

    @mcp.tool()
    def recording_status() -> dict:
        """Whether a recording is active, plus elapsed time, counts, and the live
        transcript so far."""
        _ensure_backend()
        return _get("/api/status")

    @mcp.tool()
    def list_sessions(limit: int = 10) -> dict:
        """List recent recorded sessions (newest first) with their summaries."""
        dirs = sorted(glob.glob("sessions/session-*"), reverse=True)[:limit]
        return {"sessions": [report.summarize(x) for x in dirs]}

    @mcp.tool()
    def read_session(name: str = "", max_chars: int = 60000) -> str:
        """Return the structured session.xml text so you can ingest what happened on
        screen. Omit `name` for the most recent session, or pass a folder name from
        list_sessions. Truncated to max_chars."""
        d = os.path.join("sessions", name) if name else _latest_session_dir()
        path = os.path.join(d, "session.xml")
        if not os.path.exists(path):
            return f"No session.xml at {path}"
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return text if len(text) <= max_chars else text[:max_chars] + "\n<!-- truncated -->"

    def main() -> int:
        mcp.run()
        return 0

except Exception:  # mcp not installed
    def main() -> int:  # type: ignore
        print(
            "GhostContext MCP server needs the 'mcp' package:\n"
            "  pip install 'ghostcontext[mcp]'   (or: pip install mcp)",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
