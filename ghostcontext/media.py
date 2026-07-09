"""Test-mode post-processing: mux the raw screen recording + audio into a single
shareable `demo-video.mp4` (while keeping the separate screen.mov / audio.wav).
Uses ffmpeg if present; degrades gracefully (returns False) otherwise."""

from __future__ import annotations

import os
import shutil
import subprocess


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def combine_av(video_path: str, audio_path: str, out_path: str) -> bool:
    """Mux `video_path` (silent screen.mov) + `audio_path` (mic/system wav) into
    `out_path`. Tries stream-copy first (fast), falls back to re-encoding the video
    if the container/codec won't copy. Returns True on a non-empty output."""
    if not ffmpeg_available():
        return False
    if not (os.path.exists(video_path) and os.path.exists(audio_path)):
        return False

    base = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path, "-c:a", "aac", "-shortest"]
    for video_args in (["-c:v", "copy"], ["-c:v", "libx264", "-pix_fmt", "yuv420p"]):
        try:
            subprocess.run(
                base + video_args + [out_path],
                timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return True
        except Exception:
            continue
    return False
