"""Test-mode raw SCREEN recorder — a debugging companion to the structured XML.

Uses the built-in `screencapture -v` (no ffmpeg needed), which records the screen
to a .mov until it receives SIGINT — so we start it as a subprocess and signal it
to stop. Requires Screen Recording permission (System Settings → Privacy &
Security → Screen Recording); without it the file may be black.
"""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Optional


class ScreenRecorder:
    def __init__(self, path: str) -> None:
        self._path = path
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> bool:
        os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
        try:
            # -v: video recording · -x: no shutter/UI sound.
            self._proc = subprocess.Popen(
                ["screencapture", "-v", "-x", self._path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            self._proc = None
            return False

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.send_signal(signal.SIGINT)  # finalizes the .mov
            self._proc.wait(timeout=8)
        except Exception:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None

    @property
    def path(self) -> str:
        return self._path
