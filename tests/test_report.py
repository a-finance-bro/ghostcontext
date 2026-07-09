from ghostcontext.report import format_duration, summarize

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ghostcontext_session id="t"><timeline>
<utterance t="00:05.000" source="me">hello</utterance>
<event t="00:10.000" kind="capture" trigger="deictic" phrase="this error"><url>https://x.test/a</url></event>
<event t="00:12.500" kind="scroll" mode="delta"></event>
<event t="00:20.000" kind="focus"><code file="src/x.ts" line="4"/></event>
</timeline></ghostcontext_session>
"""


def test_summarize_counts(tmp_path):
    (tmp_path / "session.xml").write_text(_XML)
    s = summarize(str(tmp_path))
    assert s["utterances"] == 1
    assert s["captures"] == 1
    assert s["scrolls"] == 1
    assert s["focus_changes"] == 1
    assert s["events"] == 3
    assert s["duration_seconds"] == 20.0
    assert s["urls"] == ["https://x.test/a"]
    assert s["files"] == ["src/x.ts"]


def test_summarize_missing_dir_is_safe(tmp_path):
    s = summarize(str(tmp_path / "nope"))
    assert s["events"] == 0 and s["duration_seconds"] == 0.0


def test_format_duration():
    assert format_duration(9) == "9s"
    assert format_duration(65) == "1m 05s"
    assert format_duration(600) == "10m 00s"
