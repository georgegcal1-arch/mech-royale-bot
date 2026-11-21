"""features.py switches: all on by default, and each one off still answers every recorded observation
validly while actually turning its feature off."""
import math

import pytest

import features
from conftest import OBS, action_problems, new_bot, respond
from replay import replay

SWITCHES = ["STRAFE", "DODGE", "LASER_FETCH", "CIRCULAR_AIM", "LOWHP_TARGETING"]


def _labels(bot):
    out = []
    for obs in OBS:
        action, _ = respond(bot, obs)
        assert action_problems(action, obs) == [], f"tick {obs['tick']}"
        out.append((action.get("debug") or {}).get("label") or "")
    assert bot.fallbacks == 0
    return out


def test_all_switches_on_by_default():
    assert all(getattr(features, s) is True for s in SWITCHES)


def test_no_timing_replay_is_byte_identical_across_runs(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    replay(str(a), timing=False)
    replay(str(b), timing=False)
    assert a.read_bytes() == b.read_bytes()


@pytest.mark.parametrize("switch, mark", [("STRAFE", "strafe "), ("DODGE", "dodge "), ("LASER_FETCH", "fetch "),
                                          ("LOWHP_TARGETING", " weakest ")])
def test_switch_off_removes_its_behaviour(monkeypatch, switch, mark):
    assert any(mark in lab for lab in _labels(new_bot())), f"the recorded match never shows {mark!r}"
    monkeypatch.setattr(features, switch, False)
    labels = _labels(new_bot())
    assert not any(mark in lab for lab in labels), f"{switch} off but {mark!r} still happens"


def test_circular_aim_off_predicts_a_straight_line(monkeypatch):
    bot = new_bot()
    m = {"id": 3, "kind": "mech", "pos": [100.0, 0.0, 50.0], "vel": [0.0, 11.7, 0.0]}
    bot.motion = {3: (0.0, math.pi / 2, 0.8)}  # turning at 0.8 rad/s
    curved = bot.predict(m, 1.0, 0.0)
    assert abs(curved[0] - 100.0) > 1.0
    monkeypatch.setattr(features, "CIRCULAR_AIM", False)
    assert bot.predict(m, 1.0, 0.0) == pytest.approx((100.0, 11.7, 50.0))
    _labels(new_bot())  # the whole match still answers validly
