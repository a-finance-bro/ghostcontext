"""Streaming XML timeline writer. Appends each event as it's recorded (so a crash
still leaves a readable file), then closes the tags on finalize. The schema is
deliberately flat + attribute-heavy so it's cheap for an LLM to parse and to
reason about ordering/duration from the `t="mm:ss.mmm"` stamps."""

from __future__ import annotations

import os
from typing import Optional, TextIO
from xml.sax.saxutils import escape, quoteattr

from .clock import SessionClock
from .delta import RenderPlan
from .events import (
    KIND_CAPTURE,
    KIND_UTTERANCE,
    Event,
)


def _attr(value) -> str:
    return quoteattr("" if value is None else str(value))


class XmlTimelineWriter:
    def __init__(self, path: str, digest: bool = True) -> None:
        self._path = path
        self._fh: Optional[TextIO] = None
        self._digest = digest
        # Accumulators for the closing <digest> — a compact TL;DR an agent can read
        # first (apps touched, files/URLs visited, the deictic captures).
        self._apps: set = set()
        self._files: set = set()
        self._urls: set = set()
        self._captures: list = []

    def start(self, *, session_id: str, started_at: str, version: str, audio_desc: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
        self._fh = open(self._path, "w", encoding="utf-8")
        w = self._fh.write
        w('<?xml version="1.0" encoding="UTF-8"?>\n')
        w(
            f"<ghostcontext_session id={_attr(session_id)} "
            f"started_at={_attr(started_at)} app_version={_attr(version)}>\n"
        )
        w("  <meta>\n")
        w(f"    <audio>{escape(audio_desc)}</audio>\n")
        w(
            "    <legend>Event-driven timeline. Full keyframes carry the whole "
            "state; deltas carry only what changed since the previous event. "
            "&lt;capture&gt; entries are voice-triggered (a deictic phrase) and are "
            "always self-contained.</legend>\n"
        )
        w("  </meta>\n")
        w("  <timeline>\n")
        self._fh.flush()

    def write_event(self, event: Event, plan: RenderPlan) -> None:
        if self._fh is None:
            return
        self._accumulate(event)
        if event.kind == KIND_UTTERANCE:
            self._write_utterance(event)
        else:
            self._write_context_event(event, plan)
        self._fh.flush()

    def _accumulate(self, event: Event) -> None:
        ctx = event.context
        if ctx is not None:
            if ctx.app_name:
                self._apps.add(ctx.app_name)
            if ctx.url:
                self._urls.add(ctx.url)
            if ctx.code and ctx.code.file:
                self._files.add(ctx.code.file)
        if event.kind == KIND_CAPTURE and event.text:
            self._captures.append((SessionClock.stamp(event.t), event.text))

    # -- renderers ------------------------------------------------------------
    def _write_utterance(self, event: Event) -> None:
        w = self._fh.write
        t = SessionClock.stamp(event.t)
        w(f"    <utterance t={_attr(t)} source={_attr(event.source or 'me')}>")
        w(escape(event.text or ""))
        w("</utterance>\n")

    def _write_context_event(self, event: Event, plan: RenderPlan) -> None:
        w = self._fh.write
        t = SessionClock.stamp(event.t)
        ctx = event.context
        fields = plan.fields

        open_tag = f"    <event t={_attr(t)} kind={_attr(event.kind)} trigger={_attr(event.trigger)}"
        if event.kind == KIND_CAPTURE and event.text:
            open_tag += f" phrase={_attr(event.text)}"
        if plan.mode == "delta":
            open_tag += ' mode="delta"'
        w(open_tag + ">\n")

        if ctx is not None:
            if fields.get("app") and ctx.app_name:
                w(f"      <app name={_attr(ctx.app_name)} bundle={_attr(ctx.bundle_id)}/>\n")
            if fields.get("window") and ctx.window_title:
                w(f"      <window title={_attr(ctx.window_title)}/>\n")
            if fields.get("url") and ctx.url:
                w(f"      <url>{escape(ctx.url)}</url>\n")
            if fields.get("code") and ctx.code is not None:
                self._write_code(ctx.code)
            self._write_deep(ctx)

        if event.cursor is not None:
            w(f'      <cursor x="{event.cursor[0]}" y="{event.cursor[1]}"/>\n')
        if event.note:
            w(f"      <note>{escape(event.note)}</note>\n")
        w("    </event>\n")

    def _write_code(self, code) -> None:
        w = self._fh.write
        attrs = ""
        if code.file:
            attrs += f" file={_attr(code.file)}"
        if code.line is not None:
            attrs += f' line="{code.line}"'
        if code.lang:
            attrs += f" lang={_attr(code.lang)}"
        if code.snippet:
            w(f"      <code{attrs}>\n")
            start = code.snippet_start_line or 1
            # CDATA so code punctuation never needs escaping; guard the rare "]]>".
            safe = code.snippet.replace("]]>", "]]&gt;")
            w(f'        <snippet start_line="{start}"><![CDATA[\n{safe}\n]]></snippet>\n')
            w("      </code>\n")
        else:
            w(f"      <code{attrs}/>\n")

    def _el_tag(self, name: str, el) -> str:
        a = ""
        if el.tag:
            a += f" tag={_attr(el.tag)}"
        if el.role:
            a += f" role={_attr(el.role)}"
        if el.el_id:
            a += f" id={_attr(el.el_id)}"
        if el.classes:
            a += f" class={_attr(el.classes)}"
        if el.aria:
            a += f" aria={_attr(el.aria)}"
        text = escape(el.text) if el.text else ""
        return f"<{name}{a}>{text}</{name}>"

    def _write_deep(self, ctx) -> None:
        """Render the deep layers (only present on captures / deep keyframes)."""
        w = self._fh.write
        if ctx.sources:
            w(f"      <sources>{escape(', '.join(ctx.sources))}</sources>\n")
        dom = ctx.dom
        if dom is not None:
            w("      <dom>\n")
            if dom.pointing is not None:
                w("        " + self._el_tag("pointing", dom.pointing) + "\n")
            if dom.selection:
                w(f"        <selection>{escape(dom.selection)}</selection>\n")
            if dom.errors:
                w("        <errors>\n")
                for e in dom.errors:
                    w("          " + self._el_tag("el", e) + "\n")
                w("        </errors>\n")
            if dom.salient:
                w("        <salient>\n")
                for e in dom.salient:
                    w("          " + self._el_tag("el", e) + "\n")
                w("        </salient>\n")
            w("      </dom>\n")
        ocr = ctx.ocr
        if ocr is not None and ocr.text:
            attrs = ""
            if ocr.region:
                attrs += f" region={_attr(ocr.region)}"
            if ocr.image:
                attrs += f" image={_attr(ocr.image)}"
            w(f"      <ocr{attrs}>{escape(ocr.text)}</ocr>\n")
        if ctx.screenshot:
            w(f"      <screenshot>{escape(ctx.screenshot)}</screenshot>\n")

    def _write_digest(self) -> None:
        w = self._fh.write
        w("    <digest>\n")
        if self._apps:
            w(f"      <apps>{escape(', '.join(sorted(self._apps)))}</apps>\n")
        for f in sorted(self._files):
            w(f"      <file>{escape(f)}</file>\n")
        for u in sorted(self._urls):
            w(f"      <url>{escape(u)}</url>\n")
        for t, phrase in self._captures:
            w(f"      <capture t={_attr(t)}>{escape(phrase)}</capture>\n")
        w("    </digest>\n")

    def finalize(self) -> None:
        if self._fh is None:
            return
        if self._digest:
            self._write_digest()
        self._fh.write("  </timeline>\n")
        self._fh.write("</ghostcontext_session>\n")
        self._fh.flush()
        self._fh.close()
        self._fh = None
