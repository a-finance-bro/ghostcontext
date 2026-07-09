"""Build a full ScreenContext for "right now". Called on the MAIN thread by the
focus/input hooks (safe place for AX + AppleScript), so the audio thread never
has to touch macOS APIs — it just reads the tracker's latest snapshot."""

from __future__ import annotations

from typing import Optional, Tuple

from . import ide, macos
from .events import ScreenContext


def current_screen_context() -> Tuple[ScreenContext, Optional[int]]:
    name, bundle, pid = macos.frontmost_app()
    title = macos.focused_window_title(pid)
    url = macos.browser_url(bundle) if macos.is_browser(bundle) else None
    code = ide.capture_code_context(pid, bundle, title)
    ctx = ScreenContext(
        app_name=name,
        bundle_id=bundle,
        window_title=title,
        url=url,
        code=code,
    )
    return ctx, pid
