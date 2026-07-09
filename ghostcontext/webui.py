"""Local web control panel for GhostContext.

A dependency-free (stdlib http.server) control surface: start/stop a session,
watch the live transcript + captures, and flip the recording options. The HTTP
server runs on a background thread; the macOS focus observer + run loop stay on
the main thread (where they must be), so the browser drives recording safely.

    python -m ghostcontext --web        # opens http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .capture_focus import FocusHook
from .config import Config
from .session import SessionController, SessionOptions

_CONTROLLER: Optional[SessionController] = None
_CONFIG: Optional[Config] = None


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>GhostContext</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         background:#0b0d12; color:#e6e8ee; }
  header { display:flex; align-items:center; gap:12px; padding:16px 22px; border-bottom:1px solid #1d212b; }
  header h1 { font-size:18px; margin:0; letter-spacing:.3px; }
  .dot { width:10px; height:10px; border-radius:50%; background:#3a3f4b; }
  .dot.on { background:#f43f5e; box-shadow:0 0 0 4px rgba(244,63,94,.18); animation:p 1.2s infinite; }
  @keyframes p { 50% { opacity:.5; } }
  .timer { font-variant-numeric:tabular-nums; color:#9aa3b2; }
  main { display:grid; grid-template-columns: 340px 1fr; gap:20px; padding:20px 22px; max-width:1100px; }
  @media (max-width:820px){ main { grid-template-columns:1fr; } }
  .card { background:#111420; border:1px solid #1d212b; border-radius:14px; padding:16px; }
  .card h2 { font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:#8a92a3; margin:0 0 12px; }
  button { font:inherit; border:0; border-radius:10px; padding:10px 16px; font-weight:600; cursor:pointer; }
  .go { background:#f59e0b; color:#0b0d12; } .go:hover{ background:#fbbf24; }
  .stop { background:#f43f5e; color:#fff; } .stop:hover{ background:#fb7185; }
  button:disabled { opacity:.4; cursor:not-allowed; }
  .row { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:8px 0; border-top:1px solid #171b26; }
  .row:first-of-type { border-top:0; }
  .row small { color:#7d8697; display:block; font-weight:400; }
  .switch { position:relative; width:42px; height:24px; flex:none; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; inset:0; background:#2a2f3c; border-radius:24px; transition:.15s; }
  .slider:before { content:""; position:absolute; height:18px; width:18px; left:3px; top:3px; background:#e6e8ee; border-radius:50%; transition:.15s; }
  input:checked + .slider { background:#f59e0b; }
  input:checked + .slider:before { transform:translateX(18px); }
  select, input[type=number], textarea, input[type=text] { width:100%; background:#0b0d12; color:#e6e8ee;
    border:1px solid #262b38; border-radius:8px; padding:7px 9px; font:inherit; }
  textarea { resize:vertical; min-height:66px; font-size:12px; }
  .field { padding:8px 0; border-top:1px solid #171b26; }
  .field label { color:#8a92a3; font-size:12px; display:block; margin-bottom:5px; }
  #transcript { max-height:52vh; overflow:auto; display:flex; flex-direction:column; gap:8px; }
  .utt { padding:8px 10px; border-radius:9px; background:#0d1018; border:1px solid #191d28; }
  .utt.them { background:#0d1420; }
  .utt .who { font-size:11px; color:#7d8697; }
  .utt.me .who { color:#f59e0b; } .utt.them .who { color:#38bdf8; }
  .utt .cap { display:inline-block; margin-top:4px; font-size:11px; color:#fbbf24; }
  .ctx { font-size:12px; color:#9aa3b2; margin-top:6px; word-break:break-all; }
  .stat { display:flex; gap:16px; font-size:12px; color:#9aa3b2; margin-top:8px; }
  .warn { color:#fb7185; font-size:12px; margin-top:8px; }
  .art { font-size:12px; color:#9aa3b2; }
  .art code { color:#cbd5e1; }
  a.link { color:#f59e0b; cursor:pointer; }
</style></head>
<body>
<header>
  <span class="dot" id="dot"></span>
  <h1>👻 GhostContext</h1>
  <span class="timer" id="timer">idle</span>
</header>
<main>
  <section class="card" id="controls">
    <h2>Session</h2>
    <div style="display:flex; gap:10px; margin-bottom:10px;">
      <button class="go" id="startBtn">● Start recording</button>
      <button class="stop" id="stopBtn" disabled>■ Stop</button>
    </div>

    <div class="row"><div>Action metadata<small>Log clicks, scrolls, window/tab changes</small></div>
      <label class="switch"><input type="checkbox" id="action" checked><span class="slider"></span></label></div>
    <div class="row"><div>Voice-triggered captures<small>Fire on "this line", "look here", …</small></div>
      <label class="switch"><input type="checkbox" id="captures" checked><span class="slider"></span></label></div>
    <div class="row"><div>Test mode<small>Also records raw screen + audio for debugging</small></div>
      <label class="switch"><input type="checkbox" id="test"><span class="slider"></span></label></div>
    <div class="row"><div>Mock transcript<small>Replay a script instead of live STT (no mic)</small></div>
      <label class="switch"><input type="checkbox" id="mock"><span class="slider"></span></label></div>

    <div class="field" id="mockPathField" style="display:none;">
      <label>Mock transcript file</label>
      <input type="text" id="mockPath" value="examples/mock_transcript.jsonl"/></div>
    <div class="field"><label>Audio input device (live STT)</label>
      <select id="device"><option value="">System default input (built-in mic)</option></select>
      <small style="color:#7d8697">For both sides of a call, pick your BlackHole aggregate device.</small></div>
    <div class="field"><label>Whisper model (live STT)</label>
      <select id="model">
        <option value="tiny.en">tiny.en — fastest</option>
        <option value="base.en" selected>base.en — balanced</option>
        <option value="small.en">small.en — most accurate</option>
      </select></div>
    <div class="field"><label>Keyframe interval (seconds)</label>
      <input type="number" id="keyframe" value="45" min="5" max="600"/></div>
    <div class="field"><label>Output folder</label>
      <input type="text" id="outdir" value="sessions"/></div>
    <div class="field"><label>Deictic markers (one per line)</label>
      <textarea id="markers"></textarea></div>

    <h2 style="margin-top:14px">Extraction layers</h2>
    <div class="field"><label>Screenshots + OCR</label>
      <select id="shotMode">
        <option value="off">Off (AX + DOM text only)</option>
        <option value="captures" selected>On captures — when I point at something</option>
        <option value="events">On captures + window/tab changes</option>
        <option value="all">On every event (heaviest)</option>
      </select></div>
    <div class="field"><label>Screenshot region</label>
      <select id="region">
        <option value="cursor" selected>Around the cursor (high signal, tiny)</option>
        <option value="window">Active window</option>
        <option value="full">Full screen</option>
      </select></div>
    <div class="row"><div>OCR → text (Apple Vision)<small>Read the pixels locally into text</small></div>
      <label class="switch"><input type="checkbox" id="ocr" checked><span class="slider"></span></label></div>
    <div class="row"><div>Keep screenshot PNGs<small>Saved to session/screenshots/</small></div>
      <label class="switch"><input type="checkbox" id="keepImg" checked><span class="slider"></span></label></div>
    <div class="row"><div>Browser DOM extraction<small>Element-under-cursor + errors (Chromium)</small></div>
      <label class="switch"><input type="checkbox" id="dom" checked><span class="slider"></span></label></div>
    <div class="row"><div>Redact secrets<small>Scrub emails/tokens from extracted text</small></div>
      <label class="switch"><input type="checkbox" id="redact"><span class="slider"></span></label></div>
  </section>

  <section class="card">
    <h2>Live transcript &amp; captures</h2>
    <div class="stat"><span id="cEvents">0 events</span><span id="cCtx">—</span></div>
    <div id="warn" class="warn"></div>
    <div id="transcript" style="margin-top:10px;"><div class="art">Press <b>Start recording</b>, then talk + move around. Captures show inline.</div></div>
    <div id="artifacts" style="margin-top:12px;"></div>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
let polling = null;
async function api(path, body){ const r = await fetch(path, {method: body?'POST':'GET',
  headers:{'content-type':'application/json'}, body: body?JSON.stringify(body):undefined}); return r.json(); }

$('mock').addEventListener('change', e => $('mockPathField').style.display = e.target.checked ? 'block':'none');

function fmt(s){ s=Math.floor(s); return String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0'); }

async function refresh(){
  const st = await api('/api/status');
  const rec = !!st.recording;
  $('dot').className = 'dot' + (rec?' on':'');
  $('timer').textContent = rec ? fmt(st.elapsed||0) : 'idle';
  $('startBtn').disabled = rec; $('stopBtn').disabled = !rec;
  if(!rec) return;
  $('cEvents').textContent = (st.events||0) + ' events';
  const c = st.current||{};
  $('cCtx').textContent = c.file ? (c.file.split('/').pop()+' : '+(c.line||'?')) : (c.url || c.app || '—');
  $('warn').textContent = st.audio_error ? ('audio: '+st.audio_error+'  (mock mode still works)') : '';
  const t = $('transcript'); t.innerHTML='';
  (st.transcript||[]).forEach(u => {
    const d = document.createElement('div'); d.className='utt '+(u.source==='them'?'them':'me');
    d.innerHTML = '<div class="who">'+u.source+' · '+u.t+'</div>'+escapeHtml(u.text)+
      (u.deictic?('<span class="cap">⟶ capture: "'+escapeHtml(u.deictic)+'"</span>'):'');
    t.appendChild(d);
  });
  t.scrollTop = t.scrollHeight;
}
function escapeHtml(s){ return (s||'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

$('startBtn').onclick = async () => {
  $('artifacts').innerHTML=''; $('warn').textContent='';
  const opts = {
    action_metadata: $('action').checked, captures_enabled: $('captures').checked,
    test_mode: $('test').checked, mock: $('mock').checked, mock_path: $('mockPath').value,
    device: $('device').value, model: $('model').value, keyframe: parseFloat($('keyframe').value),
    output_dir: $('outdir').value,
    markers: $('markers').value.split('\\n').map(s=>s.trim()).filter(Boolean),
    shot_mode: $('shotMode').value, region: $('region').value,
    ocr: $('ocr').checked, keep_images: $('keepImg').checked,
    dom: $('dom').checked, redact: $('redact').checked,
  };
  await api('/api/start', opts);
  if(!polling) polling = setInterval(refresh, 800);
  refresh();
};
$('stopBtn').onclick = async () => {
  const res = await api('/api/stop', {});
  refresh();
  const a = (res.artifacts)||{};
  const rows = Object.entries(a).map(([k,v])=>'<div><b>'+k+'</b>: <code>'+escapeHtml(v)+'</code></div>').join('');
  $('artifacts').innerHTML = '<div class="card" style="background:#0d1018"><h2>Saved</h2><div class="art">'+rows+
    '<div style="margin-top:8px"><a class="link" onclick="reveal(\\''+(a.dir||'')+'\\')">Open folder in Finder →</a></div></div></div>';
};
async function reveal(dir){ await api('/api/reveal', {dir}); }

async function boot(){
  const cfg = await api('/api/config'); $('markers').value = (cfg.markers||[]).join('\\n');
  const dv = await api('/api/audio-devices'); const sel = $('device');
  (dv.devices||[]).forEach(d => { const o=document.createElement('option');
    o.value=d.name; o.textContent=d.name+' ('+d.channels+'ch)'; sel.appendChild(o); });
  refresh();
}
boot(); setInterval(refresh, 1500);
</script>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):  # noqa: N802
        if self.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._json(_CONTROLLER.status())
        elif self.path == "/api/config":
            self._json({"markers": _CONFIG.markers})
        elif self.path == "/api/sessions":
            self._json({"sessions": _list_sessions()})
        elif self.path == "/api/audio-devices":
            try:
                from .audio_stt import list_input_devices

                self._json({"devices": list_input_devices()})
            except Exception:
                self._json({"devices": []})
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):  # noqa: N802
        body = self._body()
        if self.path == "/api/start":
            # Apply per-run overrides onto the shared config, then start.
            if body.get("markers"):
                _CONFIG.markers = body["markers"]
            if body.get("model"):
                _CONFIG.audio.whisper_model = body["model"]
            if body.get("keyframe"):
                _CONFIG.keyframe_interval_seconds = float(body["keyframe"])
            _CONFIG.audio.device = body.get("device") or None
            ex = _CONFIG.extraction
            if body.get("shot_mode"):
                ex.screenshot_mode = body["shot_mode"]
            if body.get("region"):
                ex.region = body["region"]
            ex.ocr_enabled = bool(body.get("ocr", True))
            ex.keep_images = bool(body.get("keep_images", True))
            ex.dom_enabled = bool(body.get("dom", True))
            ex.redact = bool(body.get("redact", False))
            opts = SessionOptions(
                action_metadata=bool(body.get("action_metadata", True)),
                captures_enabled=bool(body.get("captures_enabled", True)),
                test_mode=bool(body.get("test_mode", False)),
                mock_path=(body.get("mock_path") if body.get("mock") else None),
                output_dir=body.get("output_dir") or "sessions",
            )
            self._json(_CONTROLLER.start(opts))
        elif self.path == "/api/stop":
            self._json(_CONTROLLER.stop())
        elif self.path == "/api/reveal":
            d = body.get("dir")
            if d:
                try:
                    subprocess.Popen(["open", d])
                except Exception:
                    pass
            self._json({"ok": True})
        else:
            self._send(404, b"not found", "text/plain")


def _list_sessions(limit: int = 20) -> list:
    """Newest-first summaries of past sessions under ./sessions (for the local API)."""
    import glob

    from . import report

    dirs = sorted(glob.glob("sessions/session-*"), reverse=True)[:limit]
    return [report.summarize(d) for d in dirs]


def run_web(config: Config, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    """Main-thread entry: register the focus observer here, serve the UI on a
    background thread, and pump the macOS run loop so notifications fire."""
    global _CONTROLLER, _CONFIG
    _CONFIG = config
    _CONTROLLER = SessionController(config)

    # Focus observer must be created on the main thread; it stays active the whole
    # time and only records when a session is live (checked in the controller).
    focus = FocusHook(_CONTROLLER.handle_focus_change)
    try:
        focus.start()
    except Exception as exc:
        print(f"⚠️  focus hook unavailable ({exc}); window-change events won't log.")

    server = ThreadingHTTPServer((host, port), _Handler)
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    url = f"http://{host}:{port}"
    print(f"👻 GhostContext web UI → {url}   (Ctrl-C to quit)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    _pump_runloop()

    if _CONTROLLER.is_recording():
        _CONTROLLER.stop()
    focus.stop()
    server.shutdown()
    return 0


def _pump_runloop() -> None:
    try:
        from Foundation import NSDate, NSRunLoop

        rl = NSRunLoop.currentRunLoop()
        while True:
            rl.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.25))
    except KeyboardInterrupt:
        pass
    except Exception:
        import time

        try:
            while True:
                time.sleep(0.25)
        except KeyboardInterrupt:
            pass
