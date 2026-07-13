"""The typed payloads that flow across the bus.

Producers build these; the recorder (single consumer) is the ONLY thing that
serializes them, so ordering + delta compression have one home.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass
class CodeContext:
    """What an IDE was showing at capture time — the text a coding agent needs."""

    file: Optional[str] = None          # path (best-effort from the window title / AX)
    line: Optional[int] = None          # 1-based cursor line (from the editor status bar)
    lang: Optional[str] = None          # inferred from the file extension
    snippet: Optional[str] = None       # ~20 lines around the cursor, when available
    snippet_start_line: Optional[int] = None


@dataclass
class DomElement:
    """One salient DOM node (from the live browser DOM layer)."""

    tag: Optional[str] = None
    text: Optional[str] = None       # trimmed visible text
    role: Optional[str] = None       # aria/native role
    el_id: Optional[str] = None
    classes: Optional[str] = None
    aria: Optional[str] = None       # aria-label / accessible name


@dataclass
class DomContext:
    """Semantic snapshot of the active web page — richer than OS accessibility for
    modern web apps. `pointing` is the element resolved UNDER the cursor (what the
    speaker was actually referring to when they said 'this')."""

    url: Optional[str] = None
    title: Optional[str] = None
    pointing: Optional[DomElement] = None
    selection: Optional[str] = None
    errors: List[DomElement] = field(default_factory=list)   # visible error/alert nodes
    salient: List[DomElement] = field(default_factory=list)  # top headings/buttons/inputs


@dataclass
class OcrResult:
    """Text recognized (locally, Apple Vision) from a screenshot region — the
    'read the pixels' layer for anything AX/DOM can't reach (canvas, native apps,
    images, video call tiles)."""

    text: Optional[str] = None
    region: Optional[str] = None     # "cursor" | "window" | "full"
    image: Optional[str] = None      # relative path to the saved PNG (if kept)


@dataclass
class ScreenContext:
    """A full snapshot of "what was on screen" for one moment. The delta stage
    diffs consecutive snapshots so we only ever serialize what CHANGED. `dom`,
    `ocr`, `screenshot` are the DEEP layers — populated only on high-value moments
    (captures + optional keyframes), never on every scroll."""

    app_name: Optional[str] = None
    bundle_id: Optional[str] = None
    window_title: Optional[str] = None
    url: Optional[str] = None            # only for browsers
    code: Optional[CodeContext] = None
    # deep layers:
    dom: Optional[DomContext] = None
    ocr: Optional[OcrResult] = None
    screenshot: Optional[str] = None     # relative path to a saved full/window PNG
    sources: List[str] = field(default_factory=list)  # extraction layers used, e.g. ["ax","dom","ocr"]

    def key_fields(self) -> Tuple:
        """The identity of a "place" — used to decide keyframe vs delta. Cursor
        movement/scroll doesn't change this; switching file/tab/app does. Deep
        layers are excluded (they're per-moment extras, not place identity)."""
        c = self.code or CodeContext()
        return (self.app_name, self.window_title, self.url, c.file, c.line)


# Event kinds emitted onto the bus.
KIND_FOCUS = "focus"        # app/window/tab changed
KIND_CLICK = "click"        # mouse click
KIND_SCROLL = "scroll"      # meaningful scroll
KIND_SELECTION = "selection"  # text highlighted (mouse-up over a selection)
KIND_COPY = "copy"          # ⌘C / ⌘X — the copied text
KIND_VALUE = "value"        # the focused field's value changed (typing)
KIND_DRAG = "drag"          # a mouse drag from one point to another
KIND_CAPTURE = "capture"    # voice-triggered "smart" capture (deictic marker)
KIND_UTTERANCE = "utterance"  # a transcript segment (speech)


@dataclass
class Event:
    t: float                                  # elapsed seconds (SessionClock)
    kind: str
    trigger: str                              # human-readable cause
    context: Optional[ScreenContext] = None   # enriched screen state (None for pure utterances)
    cursor: Optional[Tuple[int, int]] = None  # global X/Y at event time (for "pointing")
    text: Optional[str] = None                # utterance text OR the deictic phrase
    source: Optional[str] = None              # "me" | "them" for utterances
    note: Optional[str] = None                # short human note, e.g. "scrolled down"
    meta: dict = field(default_factory=dict)
