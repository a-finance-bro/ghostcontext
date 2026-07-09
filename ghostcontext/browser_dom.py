"""Live browser-DOM extraction for Chromium browsers (Chrome/Edge/Brave/Arc).

Why: the macOS accessibility tree is clunky + shallow for modern web apps. Reading
the actual DOM gives a far richer, more accurate picture — and lets us do the thing
OS metadata can't: resolve the exact ELEMENT UNDER THE CURSOR (what the speaker
meant by "this"), plus visible errors, the current selection, and salient controls.

How: a JXA (`osascript -l JavaScript`) bridge asks the active tab to run a small
page script (`document.elementFromPoint`, error/selection scraping) and return JSON.

Requires: Automation permission for the browser, and Chrome's
View → Developer → "Allow JavaScript from Apple Events". If either is off, this
layer fails gracefully and we fall back to AX + OCR.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Optional, Tuple

from .events import DomContext, DomElement

_APP_FOR_BUNDLE = {
    "com.google.Chrome": "Google Chrome",
    "com.microsoft.edgemac": "Microsoft Edge",
    "com.brave.Browser": "Brave Browser",
    "company.thebrowser.Browser": "Arc",
}

# Page script (runs in the tab). Returns a JSON string. SX/SY are the screen-space
# cursor coords, converted to viewport coords to find the element under the pointer.
_PAGE_JS = r"""
(function(sx, sy){
  function desc(el){
    if(!el) return null;
    var t=((el.innerText||el.value||(el.getAttribute&&el.getAttribute('aria-label'))||'')+'').trim().replace(/\s+/g,' ').slice(0,140);
    return {tag: el.tagName? el.tagName.toLowerCase():null, text: t||null,
            role: (el.getAttribute&&el.getAttribute('role'))||null,
            el_id: el.id||null,
            classes: (el.className && typeof el.className==='string')? el.className.slice(0,90):null,
            aria: (el.getAttribute&&el.getAttribute('aria-label'))||null};
  }
  var vx = sx - (window.screenX||0);
  var vy = sy - (window.screenY||0) - ((window.outerHeight||0)-(window.innerHeight||0));
  var pointing=null; try { pointing = desc(document.elementFromPoint(vx, vy)); } catch(e){}
  var sel=''; try { sel=((window.getSelection&&window.getSelection().toString())||'').trim().slice(0,300); } catch(e){}
  var errs=[]; try {
    var ns=document.querySelectorAll('[role=alert],[aria-invalid=true],.error,.text-red-400,.text-red-500,.text-danger,.invalid');
    for(var i=0;i<ns.length && errs.length<6;i++){ if(ns[i].offsetParent!==null){ var d=desc(ns[i]); if(d&&d.text) errs.push(d); } }
  } catch(e){}
  var sal=[]; try {
    var s=document.querySelectorAll('h1,h2,button,[role=button]');
    for(var j=0;j<s.length && sal.length<8;j++){ if(s[j].offsetParent!==null){ var d2=desc(s[j]); if(d2&&d2.text) sal.push(d2); } }
  } catch(e){}
  return JSON.stringify({url:location.href, title:document.title, pointing:pointing,
                         selection: sel||null, errors:errs, salient:sal});
})(SX, SY)
"""


def _element(d: Optional[dict]) -> Optional[DomElement]:
    if not d:
        return None
    return DomElement(tag=d.get("tag"), text=d.get("text"), role=d.get("role"),
                      el_id=d.get("el_id"), classes=d.get("classes"), aria=d.get("aria"))


def is_chromium(bundle_id: Optional[str]) -> bool:
    return bundle_id in _APP_FOR_BUNDLE


def probe(bundle_id: Optional[str], cursor: Optional[Tuple[int, int]]) -> Optional[DomContext]:
    app = _APP_FOR_BUNDLE.get(bundle_id or "")
    if not app:
        return None
    sx, sy = (cursor or (0, 0))
    page_js = _PAGE_JS.replace("SX", str(int(sx))).replace("SY", str(int(sy)))
    jxa = (
        "function run(argv){\n"
        f'  var app = Application("{app}");\n'
        "  if(!app.running()) return '';\n"
        "  var win = app.windows[0]; if(!win) return '';\n"
        "  var tab = win.activeTab;\n"
        "  var js = `" + page_js + "`;\n"
        "  try { return tab.execute({javascript: js}); } catch(e){ return ''; }\n"
        "}\n"
    )
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(jxa)
            path = fh.name
        out = subprocess.run(
            ["osascript", "-l", "JavaScript", path],
            capture_output=True, text=True, timeout=2.5,
        )
        raw = out.stdout.strip()
        if not raw:
            return None
        data = json.loads(raw)
        return DomContext(
            url=data.get("url"), title=data.get("title"),
            pointing=_element(data.get("pointing")),
            selection=data.get("selection"),
            errors=[e for e in (_element(x) for x in data.get("errors", [])) if e],
            salient=[e for e in (_element(x) for x in data.get("salient", [])) if e],
        )
    except Exception:
        return None
    finally:
        if path:
            try:
                os.remove(path)
            except Exception:
                pass
