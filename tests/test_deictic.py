from ghostcontext.config import DEFAULT_MARKERS
from ghostcontext.deictic import DeicticDetector


def test_fires_on_bare_here():
    d = DeicticDetector(DEFAULT_MARKERS)
    assert d.find("okay so I am here now looking at the dashboard") == "here"


def test_longest_match_wins():
    d = DeicticDetector(["here", "this line", "this red line"])
    assert d.find("check this red line over here") == "this red line"


def test_case_insensitive():
    d = DeicticDetector(["look at this"])
    assert d.find("LOOK AT THIS button") == "LOOK AT THIS"


def test_no_false_positive():
    d = DeicticDetector(DEFAULT_MARKERS)
    assert d.find("let me open the file and run the build") is None
