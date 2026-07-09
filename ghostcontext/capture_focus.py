"""Event-driven focus capture via NSWorkspace notifications — fires the instant
the active application changes, instead of polling on a timer.

Note on browser TAB switches: macOS emits no OS event for an in-app tab change,
so we can't hook it directly without a browser extension. In practice a tab
switch is preceded by a click, and the click hook re-reads the active URL — so
tab changes still land in the timeline. (A companion browser extension is the
clean upgrade; called out in the README.)
"""

from __future__ import annotations

from typing import Callable

try:
    from AppKit import NSWorkspace, NSWorkspaceDidActivateApplicationNotification
    from Foundation import NSObject
except Exception:  # pragma: no cover - non-macOS guard
    NSObject = object  # type: ignore


class _FocusObserver(NSObject):
    # pyobjc: set ._callback after alloc().init(); the selector takes the notification.
    def handleActivate_(self, _notification):
        cb = getattr(self, "_callback", None)
        if cb is not None:
            try:
                cb()
            except Exception:
                pass


class FocusHook:
    def __init__(self, on_focus_change: Callable[[], None]) -> None:
        self._on_focus_change = on_focus_change
        self._observer = None

    def start(self) -> None:
        self._observer = _FocusObserver.alloc().init()
        self._observer._callback = self._on_focus_change
        nc = NSWorkspace.sharedWorkspace().notificationCenter()
        nc.addObserver_selector_name_object_(
            self._observer,
            b"handleActivate:",
            NSWorkspaceDidActivateApplicationNotification,
            None,
        )

    def stop(self) -> None:
        if self._observer is not None:
            nc = NSWorkspace.sharedWorkspace().notificationCenter()
            nc.removeObserver_(self._observer)
            self._observer = None
