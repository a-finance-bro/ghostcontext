from ghostcontext.delta import DeltaCompressor
from ghostcontext.events import (
    KIND_CAPTURE,
    KIND_FOCUS,
    KIND_SCROLL,
    Event,
    ScreenContext,
)


def _ev(t, kind, ctx, trigger="x"):
    return Event(t=t, kind=kind, trigger=trigger, context=ctx)


def test_first_event_is_a_full_keyframe():
    dc = DeltaCompressor(45.0)
    plan = dc.plan(_ev(0, KIND_FOCUS, ScreenContext(app_name="A", window_title="W")))
    assert plan.mode == "full"


def test_same_place_is_a_delta():
    dc = DeltaCompressor(45.0)
    ctx = ScreenContext(app_name="A", window_title="W")
    dc.plan(_ev(0, KIND_FOCUS, ctx))
    plan = dc.plan(_ev(1, KIND_SCROLL, ctx, trigger="scroll"))
    assert plan.mode == "delta"


def test_changed_place_is_full():
    dc = DeltaCompressor(45.0)
    dc.plan(_ev(0, KIND_FOCUS, ScreenContext(app_name="A", window_title="W1")))
    plan = dc.plan(_ev(1, KIND_FOCUS, ScreenContext(app_name="A", window_title="W2")))
    assert plan.mode == "full"


def test_capture_is_always_full():
    dc = DeltaCompressor(45.0)
    ctx = ScreenContext(app_name="A", window_title="W")
    dc.plan(_ev(0, KIND_FOCUS, ctx))
    plan = dc.plan(_ev(1, KIND_CAPTURE, ctx, trigger="deictic"))
    assert plan.mode == "full"


def test_periodic_keyframe_after_interval():
    dc = DeltaCompressor(10.0)
    ctx = ScreenContext(app_name="A", window_title="W")
    dc.plan(_ev(0, KIND_FOCUS, ctx))
    assert dc.plan(_ev(5, KIND_SCROLL, ctx, trigger="scroll")).mode == "delta"
    assert dc.plan(_ev(20, KIND_SCROLL, ctx, trigger="scroll")).mode == "full"
