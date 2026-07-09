"""Smart delta compression — the token-efficiency core.

For each event carrying screen context we decide whether to emit a FULL keyframe
(the whole state) or just the DELTA (only fields that changed since the last
event). Staying on the same file/tab for five minutes therefore logs the file
once, then only cursor/scroll deltas — instead of repeating the same payload.

Two exceptions are always FULL:
  • the very first event (nothing to diff against), and
  • any voice-triggered capture (the whole point is to self-contain what was
    pointed at, incl. the code snippet), plus a periodic keyframe so an agent can
    scrub into the middle of the timeline and still have a nearby anchor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from .events import KIND_CAPTURE, Event, ScreenContext


@dataclass
class RenderPlan:
    mode: str                                   # "full" | "delta" | "none"
    fields: Dict[str, bool] = field(default_factory=dict)  # which ctx fields to write


def _code_key(ctx: ScreenContext):
    c = ctx.code
    return (c.file, c.line) if c else (None, None)


class DeltaCompressor:
    def __init__(self, keyframe_interval_seconds: float) -> None:
        self._interval = keyframe_interval_seconds
        self._last: Optional[ScreenContext] = None
        self._last_keyframe_t = float("-inf")

    def plan(self, event: Event) -> RenderPlan:
        ctx = event.context
        if ctx is None:
            return RenderPlan("none")

        last = self._last
        changed = {
            "app": last is None or ctx.app_name != last.app_name or ctx.bundle_id != last.bundle_id,
            "window": last is None or ctx.window_title != last.window_title,
            "url": last is None or ctx.url != last.url,
            "code": last is None or _code_key(ctx) != _code_key(last),
        }

        key_changed = last is None or ctx.key_fields() != last.key_fields()
        due_keyframe = (event.t - self._last_keyframe_t) >= self._interval
        force_full = event.kind == KIND_CAPTURE  # captures are always self-contained

        self._last = ctx
        if force_full or key_changed or due_keyframe:
            self._last_keyframe_t = event.t
            return RenderPlan(
                "full",
                {
                    "app": ctx.app_name is not None,
                    "window": ctx.window_title is not None,
                    "url": ctx.url is not None,
                    "code": ctx.code is not None,
                },
            )
        # Delta: only the fields that actually changed (cursor/note handled always).
        return RenderPlan("delta", {k: v for k, v in changed.items() if v})
