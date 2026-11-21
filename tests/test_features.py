"""features.py switches: the defaults are v6 (all on but HEAL), and each one off still answers every recorded observation
validly while actually turning its feature off."""
import math

import pytest

import features
from conftest import OBS, REPO, action_problems, new_bot, respond, v9_off
from replay import replay

SWITCHES = ["STRAFE", "DODGE", "LASER_FETCH", "CIRCULAR_AIM", "LOWHP_TARGETING", "ECONOMY", "HEAL"]
# ECONOMY never triggers in the recorded match (its crates are outside the next circle or have a mech in
# sight); tests/test_economy.py covers it with built scenarios and checks ECONOMY off == v5.


def _labels(bot):
    out = []
    for obs in OBS:
        action, _ = respond(bot, obs)
        assert action_problems(action, obs) == [], f"tick {obs['tick']}"
        out.append((action.get("debug") or {}).get("label") or "")
    assert bot.fallbacks == 0
    return out


def test_defaults_are_v6():
    """v6's switches; the v9 experiments (features.V9_SWITCHES) are whatever QUEUE.md's current step set."""
    assert all(getattr(features, s) is True for s in SWITCHES if s != "HEAL")
    assert features.HEAL is False, "heal is off by default (v7 2.67, v7b 2.81 < v6 2.92)"
    assert all(isinstance(getattr(features, s), bool) for s in features.V9_SWITCHES)


def test_default_replay_matches_v6_exactly(monkeypatch, tmp_path):
    v9_off(monkeypatch)
    out = tmp_path / "default.csv"
    replay(str(out), timing=False)
    with open(f"{REPO}/tests/golden/v6_replay.csv", "rb") as f:
        assert out.read_bytes() == f.read(), "the default switches must replay exactly as v6"


def test_no_timing_replay_is_byte_identical_across_runs(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    replay(str(a), timing=False)
    replay(str(b), timing=False)
    assert a.read_bytes() == b.read_bytes()


@pytest.mark.parametrize("switch, mark", [("STRAFE", "strafe "), ("DODGE", "dodge "), ("LASER_FETCH", "fetch "),
                                          ("LOWHP_TARGETING", " weakest "), ("HEAL", "heal/")])
def test_switch_off_removes_its_behaviour(monkeypatch, switch, mark):
    v9_off(monkeypatch)  # DUEL would relabel the strafe
    monkeypatch.setattr(features, switch, True)  # HEAL is off by default
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
