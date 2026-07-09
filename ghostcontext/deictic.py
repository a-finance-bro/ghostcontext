"""Deictic-marker detection — the "pointer words" that mean the speaker is
referring to something on screen ("this line", "look at this red bit"). A match
is what turns a spoken cue into a precise, coordinate-tagged capture."""

from __future__ import annotations

import re
from typing import List, Optional


class DeicticDetector:
    def __init__(self, markers: List[str]) -> None:
        # Longest first, so "this red line" wins over "this line" for a nicer tag.
        ordered = sorted(markers, key=len, reverse=True)
        self._patterns = [
            (m, re.compile(r"\b" + re.escape(m) + r"\b", re.IGNORECASE)) for m in ordered
        ]

    def find(self, text: str) -> Optional[str]:
        """Return the matched marker phrase (as spoken), or None."""
        if not text:
            return None
        for _marker, pattern in self._patterns:
            match = pattern.search(text)
            if match:
                return match.group(0)
        return None
