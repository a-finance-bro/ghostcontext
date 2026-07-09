"""IDE-aware snippet extraction (VS Code, Xcode) — Accessibility-only.

Strategy that survives the AX-only constraint:
  1. FILE  ← the focused window's kAXDocumentAttribute (full path) when present,
            else the file basename parsed out of the window title.
  2. LINE  ← the editor status-bar "Ln N, Col M" label, read from the AX tree.
  3. SNIPPET ← read straight FROM DISK around the line (reliable), because
            scraping Monaco's virtualized text via AX is lossy. If we only have a
            basename (no path) we fall back to the AX editor text.

Honest caveat: Xcode's AX exposes far less than VS Code — expect file+line to
work but the on-disk snippet only when kAXDocumentAttribute resolves.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from . import macos
from .events import CodeContext

# Bundle ids we treat as IDEs.
IDE_BUNDLES = {
    "com.microsoft.VSCode": "VS Code",
    "com.microsoft.VSCodeInsiders": "VS Code Insiders",
    "com.vscodium": "VSCodium",
    "com.todesktop.230313mzl4w4u92": "Cursor",   # Cursor (VS Code fork)
    "com.apple.dt.Xcode": "Xcode",
}

_EXT_LANG = {
    ".ts": "ts", ".tsx": "tsx", ".js": "js", ".jsx": "jsx", ".py": "python",
    ".swift": "swift", ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".m": "objc", ".mm": "objc",
    ".css": "css", ".scss": "scss", ".html": "html", ".json": "json", ".md": "md",
    ".sql": "sql", ".sh": "bash", ".yml": "yaml", ".yaml": "yaml",
}

SNIPPET_RADIUS = 10  # lines above/below the cursor → a ~21-line window


def is_ide(bundle_id: Optional[str]) -> bool:
    return bundle_id in IDE_BUNDLES


def _lang_for(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return _EXT_LANG.get(os.path.splitext(path)[1].lower())


def _file_from_title(title: Optional[str]) -> Optional[str]:
    """VS Code title looks like '● app.tsx — myproject — Visual Studio Code'.
    Pull out the file basename (dropping the dirty-dot and trailing chrome)."""
    if not title:
        return None
    head = title.split(" — ")[0].split(" - ")[0].strip()
    head = head.lstrip("●•*").strip()
    return head or None


def _snippet_from_disk(path: str, line: int, radius: int = SNIPPET_RADIUS) -> Optional[Tuple[str, int]]:
    """(snippet_text, start_line) — `radius` lines either side of `line`, read
    from disk. Returns None if the file can't be read."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except Exception:
        return None
    if not lines:
        return None
    idx = max(1, min(line, len(lines)))
    start = max(1, idx - radius)
    end = min(len(lines), idx + radius)
    return ("\n".join(lines[start - 1:end]), start)


def capture_code_context(
    pid: Optional[int], bundle_id: Optional[str], window_title: Optional[str]
) -> Optional[CodeContext]:
    """Build a CodeContext for the focused IDE window, or None if not an IDE."""
    if not is_ide(bundle_id):
        return None

    path = macos.focused_document_path(pid)
    display_file = path or _file_from_title(window_title)
    line = macos.find_editor_line(pid)

    snippet: Optional[str] = None
    start: Optional[int] = None
    if path and line and os.path.exists(path):
        got = _snippet_from_disk(path, line)
        if got:
            snippet, start = got
    if snippet is None:
        # Last resort: whatever the editor exposes via AX (selected text / value).
        text = macos.focused_element_text(pid)
        if text:
            snippet = text[:2000]

    return CodeContext(
        file=display_file,
        line=line,
        lang=_lang_for(display_file),
        snippet=snippet,
        snippet_start_line=start,
    )
