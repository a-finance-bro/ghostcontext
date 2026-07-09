"""Summarize a finished session folder — the metrics printed by `gcrecord` on stop
and returned by the MCP `stop_recording` tool."""

from __future__ import annotations

import glob
import os
import xml.etree.ElementTree as ET
from typing import Dict


def _t_to_seconds(stamp: str) -> float:
    try:
        mm, ss = stamp.split(":")
        return int(mm) * 60 + float(ss)
    except Exception:
        return 0.0


def summarize(session_dir: str) -> Dict:
    """Counts + duration + assets for a session directory. Robust to a partially
    written file (a crash mid-session still summarizes what's there)."""
    xml_path = os.path.join(session_dir, "session.xml")
    out: Dict = {
        "dir": session_dir,
        "xml": xml_path,
        "duration_seconds": 0.0,
        "events": 0,
        "captures": 0,
        "utterances": 0,
        "clicks": 0,
        "scrolls": 0,
        "focus_changes": 0,
        "files": [],
        "urls": [],
        "screenshots": 0,
        "xml_bytes": 0,
    }
    try:
        out["xml_bytes"] = os.path.getsize(xml_path)
    except Exception:
        pass
    out["screenshots"] = len(glob.glob(os.path.join(session_dir, "screenshots", "*.png")))

    files, urls = set(), set()
    last_t = 0.0
    try:
        root = ET.parse(xml_path).getroot()
        timeline = root.find("timeline")
        for el in list(timeline) if timeline is not None else []:
            t = el.get("t")
            if t:
                last_t = max(last_t, _t_to_seconds(t))
            if el.tag == "utterance":
                out["utterances"] += 1
            elif el.tag == "event":
                out["events"] += 1
                kind = el.get("kind")
                if kind == "capture":
                    out["captures"] += 1
                elif kind == "click":
                    out["clicks"] += 1
                elif kind == "scroll":
                    out["scrolls"] += 1
                elif kind == "focus":
                    out["focus_changes"] += 1
                code = el.find("code")
                if code is not None and code.get("file"):
                    files.add(code.get("file"))
                url = el.find("url")
                if url is not None and url.text:
                    urls.add(url.text)
    except Exception:
        pass

    out["duration_seconds"] = round(last_t, 1)
    out["files"] = sorted(files)
    out["urls"] = sorted(urls)
    return out


def format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"
