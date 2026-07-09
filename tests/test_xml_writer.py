import time
import xml.dom.minidom as minidom

from ghostcontext.bus import EventBus
from ghostcontext.clock import SessionClock
from ghostcontext.delta import DeltaCompressor
from ghostcontext.events import (
    KIND_CAPTURE,
    DomContext,
    DomElement,
    Event,
    OcrResult,
    ScreenContext,
)
from ghostcontext.recorder import Recorder
from ghostcontext.xml_writer import XmlTimelineWriter


def test_writes_valid_xml_with_all_layers(tmp_path):
    out = str(tmp_path / "session.xml")
    clock, bus = SessionClock(), EventBus()
    rec = Recorder(bus, DeltaCompressor(45.0), XmlTimelineWriter(out))
    rec.start(session_id="t", started_at=clock.started_at_iso(), version="0.3.0", audio_desc="test")

    ctx = ScreenContext(
        app_name="Google Chrome", bundle_id="com.google.Chrome", url="https://x.test/checkout",
        dom=DomContext(pointing=DomElement(tag="div", role="alert", text="Boom")),
        ocr=OcrResult(text="Boom", region="cursor", image="screenshots/c.png"),
        screenshot="screenshots/c.png", sources=["ax", "dom", "ocr"],
    )
    bus.emit(Event(t=1.0, kind=KIND_CAPTURE, trigger="deictic", context=ctx,
                   text="this error right here", cursor=(10, 20)))
    time.sleep(0.5)
    rec.stop()

    xml = open(out).read()
    minidom.parseString(xml)  # raises if malformed
    for needle in [
        'kind="capture"', 'phrase="this error right here"', "<sources>ax, dom, ocr</sources>",
        "<dom>", 'role="alert"', "<ocr", "<screenshot>", '<cursor x="10" y="20"/>', "<digest>",
    ]:
        assert needle in xml, f"missing {needle!r}"
