"""The "read the pixels" layer: crop a screenshot, then OCR it locally with Apple's
Vision framework (on-device, no cloud, fast + accurate). This reaches text that
Accessibility and the DOM can't — canvas/WebGL, native apps, images, screen-share
tiles, video-call names — turning pixels into token-efficient text.

Design choice (answering "is screenshot scraping a good idea?"): OCR'ing the WHOLE
screen every tick is noisy + token-heavy. Instead we OCR a tight CROP around the
CURSOR on a capture — i.e. exactly what the speaker is pointing at — which is high
signal + tiny. Window/full are available as options for keyframes.
"""

from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Tuple

from . import macos

try:
    import Vision
    from Foundation import NSURL
except Exception:  # pragma: no cover
    Vision = None
    NSURL = None


def _clamp_rect(x: int, y: int, w: int, h: int) -> Tuple[int, int, int, int]:
    size = macos.main_display_size()
    if size:
        sw, sh = size
        x = max(0, min(x, sw - 1))
        y = max(0, min(y, sh - 1))
        w = max(40, min(w, sw - x))
        h = max(40, min(h, sh - y))
    return (x, y, w, h)


def region_rect(
    region: str, cursor: Optional[Tuple[int, int]], box: Tuple[int, int], pid: Optional[int]
) -> Optional[Tuple[int, int, int, int]]:
    """Resolve the (x,y,w,h) to grab. 'cursor' → a box centered on the pointer;
    'window' → the focused window bounds (falls back to full); 'full' → None
    (whole screen)."""
    if region == "full":
        return None
    if region == "window":
        b = macos.focused_window_bounds(pid)
        return _clamp_rect(*b) if b else None
    # cursor (default)
    if not cursor:
        return None
    bw, bh = box
    return _clamp_rect(cursor[0] - bw // 2, cursor[1] - bh // 2, bw, bh)


def grab(out_path: str, rect: Optional[Tuple[int, int, int, int]]) -> bool:
    """Screenshot to `out_path` via the built-in `screencapture` (region if rect,
    else full screen). Requires Screen Recording permission."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    cmd = ["screencapture", "-x"]
    if rect:
        cmd += ["-R", f"{rect[0]},{rect[1]},{rect[2]},{rect[3]}"]
    cmd.append(out_path)
    try:
        subprocess.run(cmd, timeout=4, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception:
        return False


def ocr(path: str, max_chars: int = 1600) -> Optional[str]:
    """Recognized text from a PNG via Apple Vision (accurate, language-corrected).
    Returns lines joined by ' · ', capped, or None."""
    if Vision is None or NSURL is None or not os.path.exists(path):
        return None
    try:
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
            NSURL.fileURLWithPath_(path), None
        )
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(1)  # 1 = accurate, 0 = fast
        req.setUsesLanguageCorrection_(True)
        handler.performRequests_error_([req], None)
        lines: List[str] = []
        for obs in req.results() or []:
            cand = obs.topCandidates_(1)
            if cand and len(cand):
                s = cand[0].string()
                if s:
                    lines.append(str(s))
        text = " · ".join(lines).strip()
        return (text[:max_chars] + "…") if len(text) > max_chars else (text or None)
    except Exception:
        return None
