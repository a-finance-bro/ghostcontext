"""Thin, defensive wrappers over the macOS Accessibility (AX), Quartz, and
AppleScript surfaces we need. Every call is best-effort: on any failure (missing
permission, an app that doesn't expose AX, a scripting timeout) we return None
rather than raise, because a recorder must never crash mid-session.

Permissions this file depends on (System Settings → Privacy & Security):
  • Accessibility        — AX tree reads (window titles, editor status bar, …)
  • Automation           — AppleScript to browsers (URL of the active tab)
Grant them to your TERMINAL (or whatever runs Python).
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional, Tuple

# --- Quartz: cursor position (thread-safe, no run loop needed) ----------------
try:
    import Quartz
except Exception:  # pragma: no cover - non-macOS import guard
    Quartz = None

# --- AppKit: frontmost application --------------------------------------------
try:
    from AppKit import NSWorkspace
except Exception:  # pragma: no cover
    NSWorkspace = None

# --- Accessibility (AXUIElement) ----------------------------------------------
try:
    import ApplicationServices as AX
except Exception:  # pragma: no cover
    AX = None


def accessibility_trusted() -> bool:
    """True if this process is allowed to read the AX tree. If False, window
    titles / editor line numbers will be blank until you grant Accessibility."""
    try:
        return bool(AX.AXIsProcessTrusted())
    except Exception:
        return False


def cursor_position() -> Optional[Tuple[int, int]]:
    """Global screen coordinates of the mouse — this is the "pointing" signal
    that maps a spoken 'look at this' to a spot on screen. Thread-safe."""
    if Quartz is None:
        return None
    try:
        loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return (int(round(loc.x)), int(round(loc.y)))
    except Exception:
        return None


def frontmost_app() -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """(localized name, bundle id, pid) of the app the user is actively in."""
    if NSWorkspace is None:
        return (None, None, None)
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return (None, None, None)
        return (app.localizedName(), app.bundleIdentifier(), int(app.processIdentifier()))
    except Exception:
        return (None, None, None)


# --- Low-level AX helpers -----------------------------------------------------

def _ax_value(element, attribute):
    """AXUIElementCopyAttributeValue returns (error, value) under pyobjc."""
    if AX is None or element is None:
        return None
    try:
        err, value = AX.AXUIElementCopyAttributeValue(element, attribute, None)
        if err != 0:
            return None
        return value
    except Exception:
        return None


def _app_element(pid: int):
    if AX is None or pid is None:
        return None
    try:
        return AX.AXUIElementCreateApplication(pid)
    except Exception:
        return None


def focused_window_title(pid: Optional[int]) -> Optional[str]:
    """Title of the app's focused window (e.g. the file name in VS Code's title
    bar, or a browser tab's page title)."""
    if pid is None:
        return None
    app_el = _app_element(pid)
    window = _ax_value(app_el, AX.kAXFocusedWindowAttribute)
    title = _ax_value(window, AX.kAXTitleAttribute)
    return str(title) if title else None


def focused_window_bounds(pid: Optional[int]):
    """(x, y, w, h) of the focused window in screen points, or None — used to crop
    a screenshot to just the active window."""
    if pid is None or AX is None:
        return None
    try:
        app_el = _app_element(pid)
        win = _ax_value(app_el, AX.kAXFocusedWindowAttribute)
        if win is None:
            return None
        pos = _ax_value(win, AX.kAXPositionAttribute)
        size = _ax_value(win, AX.kAXSizeAttribute)
        if pos is None or size is None:
            return None
        pt_type = getattr(AX, "kAXValueCGPointType", getattr(AX, "kAXValueTypeCGPoint", 1))
        sz_type = getattr(AX, "kAXValueCGSizeType", getattr(AX, "kAXValueTypeCGSize", 2))
        okp, p = AX.AXValueGetValue(pos, pt_type, None)
        oks, s = AX.AXValueGetValue(size, sz_type, None)
        if not (okp and oks):
            return None
        return (int(p.x), int(p.y), int(s.width), int(s.height))
    except Exception:
        return None


def main_display_size():
    """(width, height) of the main display in points."""
    if Quartz is None:
        return None
    try:
        b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return (int(b.size.width), int(b.size.height))
    except Exception:
        return None


def focused_element_text(pid: Optional[int]) -> Optional[str]:
    """Best-effort text of the focused UI element — the editor's selected text if
    any, else its value. Monaco/Electron editors expose this inconsistently, so
    treat it as a bonus, not a guarantee (this is the honest limitation of the
    AX-only route)."""
    if pid is None:
        return None
    app_el = _app_element(pid)
    focused = _ax_value(app_el, AX.kAXFocusedUIElementAttribute)
    if focused is None:
        return None
    sel = _ax_value(focused, AX.kAXSelectedTextAttribute)
    if sel:
        return str(sel)
    val = _ax_value(focused, AX.kAXValueAttribute)
    return str(val) if val else None


def _focused_element(pid: Optional[int]):
    if pid is None:
        return None
    return _ax_value(_app_element(pid), AX.kAXFocusedUIElementAttribute)


def focused_selected_text(pid: Optional[int]) -> Optional[str]:
    """The selected (highlighted) text of the focused element, or None."""
    sel = _ax_value(_focused_element(pid), AX.kAXSelectedTextAttribute)
    return str(sel) if sel else None


def focused_value(pid: Optional[int]) -> Optional[str]:
    """The value of the focused element (e.g. what's typed in a field), or None."""
    val = _ax_value(_focused_element(pid), AX.kAXValueAttribute)
    return str(val) if val else None


def clipboard_text() -> Optional[str]:
    """The general pasteboard's string contents — read after a copy, or None."""
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        s = NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
        return str(s) if s else None
    except Exception:
        return None


def focused_document_path(pid: Optional[int]) -> Optional[str]:
    """Full path of the document in the focused window, via the window's
    kAXDocumentAttribute (a file:// URL that many document apps — including VS
    Code — expose). This is the reliable way to get the FULL path AX-only; we
    then read the code snippet straight off disk instead of scraping the editor."""
    if pid is None or AX is None:
        return None
    app_el = _app_element(pid)
    window = _ax_value(app_el, AX.kAXFocusedWindowAttribute)
    doc_attr = getattr(AX, "kAXDocumentAttribute", "AXDocument")
    doc = _ax_value(window, doc_attr)
    if not doc:
        return None
    doc = str(doc)
    if doc.startswith("file://"):
        from urllib.parse import unquote, urlparse
        return unquote(urlparse(doc).path)
    return doc or None


_LN_RE = re.compile(r"\bLn\s+(\d+)\b(?:,\s*Col\s+(\d+))?", re.IGNORECASE)


def find_editor_line(pid: Optional[int], max_nodes: int = 4000) -> Optional[int]:
    """Walk the AX tree looking for the editor's status-bar "Ln N, Col M" label
    (VS Code renders it as static text). This is the most reliable AX-only way to
    get the cursor LINE without a companion extension. Bounded so a deep Electron
    tree can't stall the capture."""
    if pid is None or AX is None:
        return None
    root = _app_element(pid)
    if root is None:
        return None

    stack = [root]
    seen = 0
    while stack and seen < max_nodes:
        node = stack.pop()
        seen += 1
        value = _ax_value(node, AX.kAXValueAttribute)
        if isinstance(value, str):
            m = _LN_RE.search(value)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass
        children = _ax_value(node, AX.kAXChildrenAttribute)
        if children:
            # Reverse so we explore in roughly natural order under a LIFO stack.
            try:
                stack.extend(list(children))
            except TypeError:
                pass
    return None


# --- Browser URL via AppleScript (Automation permission) ----------------------

_BROWSER_SCRIPTS = {
    "com.google.Chrome": 'tell application "Google Chrome" to return URL of active tab of front window',
    "com.microsoft.edgemac": 'tell application "Microsoft Edge" to return URL of active tab of front window',
    "com.brave.Browser": 'tell application "Brave Browser" to return URL of active tab of front window',
    "company.thebrowser.Browser": 'tell application "Arc" to return URL of active tab of front window',
    "com.apple.Safari": 'tell application "Safari" to return URL of front document',
}


def is_browser(bundle_id: Optional[str]) -> bool:
    return bundle_id in _BROWSER_SCRIPTS


def browser_url(bundle_id: Optional[str]) -> Optional[str]:
    """URL of the active tab, via AppleScript. Returns None for non-browsers or
    if Automation permission hasn't been granted."""
    script = _BROWSER_SCRIPTS.get(bundle_id or "")
    if not script:
        return None
    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=1.5,
        )
        url = out.stdout.strip()
        return url or None
    except Exception:
        return None
