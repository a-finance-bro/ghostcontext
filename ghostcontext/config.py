"""Runtime settings. Everything you'd realistically tweak lives here so the demo
is one edit away from matching your machine (audio device names especially)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# Deictic markers — the "pointer words" that trigger a smart capture. Matched
# case-insensitively as whole words/phrases. Deliberately broad for live screen-
# shares (where "here"/"there" almost always mean "look at this"); a per-capture
# cooldown (Config.capture_cooldown_seconds) keeps aggressive markers from spamming.
DEFAULT_MARKERS: List[str] = [
    # Bare deictics — the catch-alls. In a screen-share, "here"/"there" ~= "look here".
    "here", "there",
    # look / see / watch / check / notice
    "look at this", "look at that", "look here", "look at", "take a look", "have a look",
    "see this", "see that", "see here", "check this", "check this out", "check it out",
    "watch this", "notice this", "notice how", "you can see", "you'll see",
    "as you can see", "you see", "see how",
    # right / over / down / up + here/there
    "right here", "over here", "down here", "up here", "in here", "back here", "come here",
    "right there", "over there", "down there", "up there", "back there",
    # this X
    "this line", "this error", "this bug", "this glitch", "this red line", "this one",
    "this thing", "this part", "this bit", "this piece", "this file", "this function",
    "this button", "this field", "this box", "this section", "this component",
    "this page", "this tab", "this window", "this list", "this card", "this row",
    "this column", "this cell", "this menu", "this dropdown", "this toggle",
    "this setting", "this option", "this value", "this number", "this counter",
    "this warning", "this message", "this code", "this variable", "this text",
    "this word", "this name", "this label", "this icon", "this link", "this element",
    "this area", "this issue", "this problem", "this call", "this data", "this chart",
    "this feature", "this screen", "this view", "this panel", "this result",
    # these / those
    "these", "those", "these boxes", "these three", "this box",
    # that X
    "that line", "that error", "that bug", "that one", "that thing", "that part",
    "that button", "that message", "that file", "that page", "that number",
    # the X (attention grabbers)
    "the error", "the glitch", "the bug", "the issue", "the problem", "the warning",
    # location / pointing phrasing
    "pointing at", "pointing to", "i'm on", "i'm in", "i'm looking at",
    "this is where", "here is", "here's", "there's", "here we", "here you",
    "it's broken", "that's broken", "this is broken", "this shows", "that shows",
]


@dataclass
class AudioConfig:
    # The input device to capture from (substring match on the name). None => the
    # system default input (your built-in mic) — works out of the box. For mic +
    # system audio, create an Aggregate Device in Audio MIDI Setup (mic + BlackHole
    # 2ch) and select it in the web UI's device dropdown (or set its name here).
    device: Optional[str] = None
    samplerate: int = 16000          # Whisper wants 16 kHz mono per source
    block_seconds: float = 4.0       # rolling transcription window
    hop_seconds: float = 2.0         # how often we transcribe (window overlap)
    # Channel → speaker mapping on the aggregate device. Your mic is usually the
    # first channel(s); BlackHole (system audio = "them") the next. Adjust to
    # your device's channel order (see `python -m ghostcontext --list-audio`).
    channel_sources: dict = field(default_factory=lambda: {0: "me", 1: "them"})
    whisper_model: str = "base.en"   # tiny.en/base.en are fast on CPU; small.en better
    compute_type: str = "int8"       # int8 = fast CPU; use "float16" on Apple GPU builds


@dataclass
class ExtractionConfig:
    """The deep-extraction layers (DOM + screenshot + OCR) and how often they run.
    These are the expensive layers, so they default to firing only on captures."""

    # When to run screenshot + OCR: off | captures | events (also on focus) | all.
    screenshot_mode: str = "captures"
    # What to grab: cursor (a box around the pointer — high signal) | window | full.
    region: str = "cursor"
    cursor_box: Tuple[int, int] = (760, 480)
    ocr_enabled: bool = True          # OCR the screenshot into text (Apple Vision)
    keep_images: bool = True          # save the PNGs into <session>/screenshots/
    dom_enabled: bool = True          # live Chromium DOM probe (element-under-cursor, errors)
    redact: bool = False              # scrub emails/tokens/keys from extracted text
    digest: bool = True               # write a session digest header on finalize


@dataclass
class Config:
    output_path: str = "sessions/session.xml"
    markers: List[str] = field(default_factory=lambda: list(DEFAULT_MARKERS))
    audio: AudioConfig = field(default_factory=AudioConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    # Force a full "keyframe" (whole state re-emitted, even if unchanged) at least
    # this often, so an agent scrubbing to the middle of the timeline always has a
    # nearby anchor without replaying from t=0.
    keyframe_interval_seconds: float = 45.0
    # Ignore scrolls smaller than this many wheel ticks (kills idle jitter).
    scroll_min_ticks: int = 2
    # Rate-limit scroll events: emit at most one per this many seconds (coalescing
    # net direction/magnitude). Continuous scrolling otherwise floods the timeline
    # (a real session logged 269 scroll events in ~80s — this caps it to ~1/sec).
    scroll_min_interval_seconds: float = 1.0
    # Minimum gap between voice-triggered captures. Aggressive markers ("here") can
    # match several times a sentence; this coalesces them so we don't fire a
    # screenshot+OCR+DOM burst on every breath. The utterance is still logged.
    capture_cooldown_seconds: float = 1.5
