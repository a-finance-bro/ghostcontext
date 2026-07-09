"""Optional redaction pass — scrub obvious secrets/PII from extracted text before
it's written, since sessions get shared. Conservative: only high-confidence
patterns (emails, API keys/tokens, bearer headers, long hex/base64 blobs)."""

from __future__ import annotations

import re
from typing import Optional

from .events import ScreenContext

_PATTERNS = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),          # email
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|xox[baprs])[-_][A-Za-z0-9]{16,}\b"),  # api keys
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                     # AWS access key
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{12,}", re.IGNORECASE),            # bearer token
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\b"),  # JWT
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),                                     # long hex blob
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+"),
]


def scrub(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    out = text
    for pat in _PATTERNS:
        out = pat.sub("[redacted]", out)
    return out


def scrub_context(ctx: ScreenContext) -> None:
    """Mutate a ScreenContext in place, redacting its text-bearing fields."""
    ctx.window_title = scrub(ctx.window_title)
    ctx.url = scrub(ctx.url)
    if ctx.code and ctx.code.snippet:
        ctx.code.snippet = scrub(ctx.code.snippet)
    if ctx.ocr and ctx.ocr.text:
        ctx.ocr.text = scrub(ctx.ocr.text)
    if ctx.dom:
        ctx.dom.selection = scrub(ctx.dom.selection)
        for group in (ctx.dom.errors, ctx.dom.salient, [ctx.dom.pointing] if ctx.dom.pointing else []):
            for el in group:
                if el:
                    el.text = scrub(el.text)
                    el.aria = scrub(el.aria)
