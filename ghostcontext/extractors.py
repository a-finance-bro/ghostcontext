"""The layered extraction engine — the "intelligent concept" that fuses every
signal into one ScreenContext, and decides which expensive layers to run for a
given moment.

Layer order (cheap → expensive), each recorded in `ctx.sources`:
  1. ax    — front app / window title / URL / IDE file+line+snippet  (always, elsewhere)
  2. dom   — live browser DOM: element-under-cursor, errors, selection (browsers)
  3. ocr   — screenshot crop + Apple Vision OCR (pixels → text)        (deep only)

Only `capture` moments (and, per settings, focus keyframes / all events) trigger
the deep layers — so we get near-video fidelity where it matters without the token
cost of scraping everything constantly.
"""

from __future__ import annotations

import os
from dataclasses import replace

from . import browser_dom, macos, redact, vision_ocr
from .clock import SessionClock
from .config import ExtractionConfig
from .events import OcrResult, ScreenContext


class DeepExtractor:
    def __init__(self, cfg: ExtractionConfig, session_dir: str) -> None:
        self.cfg = cfg
        self.dir = session_dir
        self.shots_dir = os.path.join(session_dir, "screenshots")

    def _wants_shot(self, reason: str) -> bool:
        m = self.cfg.screenshot_mode
        if m == "off":
            return False
        if m == "captures":
            return reason == "capture"
        if m == "events":
            return reason in ("capture", "focus")
        return m == "all"

    def enrich(self, base: ScreenContext, cursor, t: float, reason: str) -> ScreenContext:
        """Return a NEW ScreenContext with deep layers added for this moment.
        `reason` ∈ {capture, focus, click}."""
        ctx = replace(base)
        sources = list(ctx.sources) if ctx.sources else ["ax"]

        # --- DOM layer (browsers) --------------------------------------------
        if self.cfg.dom_enabled and browser_dom.is_chromium(ctx.bundle_id):
            dom = browser_dom.probe(ctx.bundle_id, cursor)
            if dom is not None:
                ctx.dom = dom
                if ctx.url is None and dom.url:
                    ctx.url = dom.url
                sources.append("dom")

        # --- Screenshot + OCR layer ------------------------------------------
        if self._wants_shot(reason):
            _n, _b, pid = macos.frontmost_app()
            rect = vision_ocr.region_rect(self.cfg.region, cursor, self.cfg.cursor_box, pid)
            name = f"{reason}-{SessionClock.stamp(t).replace(':', '_')}.png"
            path = os.path.join(self.shots_dir, name)
            rel = os.path.join("screenshots", name)
            if vision_ocr.grab(path, rect):
                text = vision_ocr.ocr(path) if self.cfg.ocr_enabled else None
                if text:
                    ctx.ocr = OcrResult(
                        text=text, region=self.cfg.region,
                        image=(rel if self.cfg.keep_images else None),
                    )
                    sources.append("ocr")
                if self.cfg.keep_images:
                    ctx.screenshot = rel
                else:
                    try:
                        os.remove(path)  # OCR-only: don't retain the pixels
                    except Exception:
                        pass

        # Dedupe while preserving order — a capture inherits the focus keyframe's
        # sources (via the tracker snapshot) and may re-run the same layer.
        ctx.sources = list(dict.fromkeys(sources))
        if self.cfg.redact:
            redact.scrub_context(ctx)
        return ctx
