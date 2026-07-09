from ghostcontext.config import DEFAULT_MARKERS, Config


def test_markers_are_comprehensive_and_include_bare_deictics():
    assert "here" in DEFAULT_MARKERS
    assert "there" in DEFAULT_MARKERS
    assert "look at this" in DEFAULT_MARKERS
    assert len(DEFAULT_MARKERS) >= 100


def test_balanced_defaults():
    c = Config()
    assert c.extraction.screenshot_mode == "captures"
    assert c.extraction.region == "cursor"
    assert c.extraction.ocr_enabled is True
    assert c.extraction.dom_enabled is True
    assert c.capture_cooldown_seconds > 0
    assert c.scroll_min_interval_seconds > 0
