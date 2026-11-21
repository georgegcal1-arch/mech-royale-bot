"""v9 experiment K (features.ROOFTOP), on the real map: the roofs found, the pick, the climb, the hold, and the
way down only for the zone. Off by default; the golden replays force every V9 switch off, and test_v9's
whole-match / all-on / Hypothesis tests iterate V9_SWITCHES, so they cover K too."""
import math

import pytest

import rooftop as R
from conftest import DECISION_BUDGET_MS, base_obs, new_bot, respond
from test_v9 import _label, _mech, _mode, switches
from test_v9fg import GUN, LASER, _arm

BEST = ("DUEL", "LIGHTNING", "LASER_GUARD", "NO_CHIP")  # the setup K will stack on (if HI wins)
BOT = new_bot()  # the map and roof list are read-only: share them


def _roof_and_street(bot):
    """A roof with open street OFF_M west of its bounding box at about its foot level."""
    w = bot.world
    for cx, cy, top, idx in bot.roofs:
        minx, miny, maxx, maxy = w.prisms[idx][:4]
        if max(maxx - minx, maxy - miny) > 40.0:
            continue
        sx, sy = minx - 4.0, cy
        sz = w.ground(sx, sy)
        if top - sz < 8.0 or any(w.solid_at(sx - k, sy, sz + 1.0) or w.solid_at(sx - k, sy, sz + 8.0) for k in (0, 10, 20)):
            continue
        return (cx, cy, top, idx), (sx, sy, sz)
    pytest.skip("no roof with open street to its west")


def _scene(bot, at, roof, energy=100, weapons=(GUN, LASER)):
    obs = _arm(base_obs(100), *weapons)
    obs["self"]["pos"] = list(at)
    obs["self"]["vel"] = [0.0, 0.0, 0.0]
    obs["self"]["energy"] = energy
    obs["self"]["grounded"] = True
    obs["self"]["in_zone"] = True
    cx, cy = roof[0], roof[1]
    obs["zone"].update(center=[cx, cy], radius=560.0, next_center=[cx, cy], next_radius=300.0,
                       shrink_start=obs["t"] + 60.0, shrink_end=obs["t"] + 110.0)
    return obs


def _reset_roof(bot):
    bot.roof, bot.roof_cool = None, 0.0


def test_roofs_found_on_the_map():
    assert len(BOT.roofs) >= 50
    for cx, cy, top, idx in BOT.roofs:
        assert BOT.on_roof_point(idx, top, cx, cy)
        assert R.ROOF_HEIGHT_M[0] <= top - BOT.world.ground(cx, cy) <= R.ROOF_HEIGHT_M[1]


def test_walks_to_then_climbs_a_near_roof():
    bot = BOT
    _reset_roof(bot)
    roof, street = _roof_and_street(bot)
    far = (street[0] - 20.0, street[1], bot.world.ground(street[0] - 20.0, street[1]))
    obs = _scene(bot, far, roof)
    with switches(*BEST, "ROOFTOP"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "toroof", _label(action)
    sx, sy = bot.roof["spot"]  # the nearest good roof (maybe not ours)
    b = math.atan2(sy - far[1], sx - far[0])
    assert action["move"][0] * math.cos(b) + action["move"][1] * math.sin(b) > 0.99 and not action["boost"], \
        "walk straight to it, no boost"
    assert math.hypot(sx - far[0], sy - far[1]) <= R.ROOF_PICK_M
    _reset_roof(bot)
    obs = _scene(bot, street, roof)  # at the wall
    with switches(*BEST, "ROOFTOP"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "climb" and action["boost"] is True and action["jump"] is True, _label(action)
    obs["self"]["grounded"] = False  # in the air on the way up: keep jetting, no jump
    obs["self"]["pos"][2] += 5.0
    with switches(*BEST, "ROOFTOP"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "climb" and action["boost"] is True and action["jump"] is False
    _reset_roof(bot)


def test_holds_the_roof_and_fights_from_it():
    bot = BOT
    _reset_roof(bot)
    roof, street = _roof_and_street(bot)
    cx, cy, top, idx = roof
    obs = _scene(bot, (cx, cy, top), roof)
    with switches(*BEST, "ROOFTOP"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "roof" and action["move"] == [0.0, 0.0] and not action["jump"], _label(action)
    obs["mechs"] = [_mech(obs, 3, 120.0, 30.0)]
    obs["self"]["aim"] = [math.atan2(30.0, 120.0), 0.0]
    with switches(*BEST, "ROOFTOP"):
        for _ in range(3):
            action, _ = respond(bot, obs)
            obs["self"]["aim"] = list(action["aim"])
    assert _mode(action) == "roof", _label(action)
    mx, my = action["move"]
    if mx or my:  # any move kept on the roof stays on it
        assert bot.on_roof_point(idx, top, cx + R.ROOF_LANE_M * mx, cy + R.ROOF_LANE_M * my)
    assert action["fire"][1] is True, "the laser charges from the roof"
    _reset_roof(bot)


def test_comes_down_only_for_the_zone():
    bot = BOT
    _reset_roof(bot)
    roof, _ = _roof_and_street(bot)
    cx, cy, top, idx = roof
    obs = _scene(bot, (cx, cy, top), roof)
    with switches(*BEST, "ROOFTOP"):
        respond(bot, obs)
        obs["zone"]["next_center"] = [cx + 400.0, cy]  # the next circle moved away: still hold
        assert _mode(respond(bot, obs)[0]) == "roof"
        obs["self"]["in_zone"] = False  # the zone forces it
        action, _ = respond(bot, obs)
    assert _mode(action) == "zone!" and bot.roof is None
    _reset_roof(bot)


@pytest.mark.parametrize("change", ["no_laser", "low_energy", "outside_next"])
def test_no_roof(change):
    bot = BOT
    _reset_roof(bot)
    roof, street = _roof_and_street(bot)
    kw = {}
    if change == "no_laser":
        kw["weapons"] = (GUN, None)
    if change == "low_energy":
        kw["energy"] = 5
    obs = _scene(bot, street, roof, **kw)
    if change == "outside_next":
        obs["zone"]["next_center"] = [roof[0] + 400.0, roof[1]]
        obs["zone"]["next_radius"] = 100.0
    with switches(*BEST, "ROOFTOP"):
        action, _ = respond(bot, obs)
    assert _mode(action) not in ("toroof", "climb", "roof") and bot.roof is None, _label(action)


def test_gives_up_a_climb_that_takes_too_long():
    bot = BOT
    _reset_roof(bot)
    roof, street = _roof_and_street(bot)
    obs = _scene(bot, street, roof)
    with switches(*BEST, "ROOFTOP"):
        respond(bot, obs)
        obs["t"] += R.ROOF_CLIMB_MAX_S + 1.0
        action, _ = respond(bot, obs)
    assert bot.roof is None and bot.roof_cool > obs["t"] and _mode(action) != "climb"
    _reset_roof(bot)


def test_roof_pick_is_fast():
    bot = new_bot()
    worst = 0.0
    roof, street = _roof_and_street(bot)
    for _ in range(20):
        _reset_roof(bot)
        obs = _scene(bot, street, roof)
        with switches(*BEST, "ROOFTOP"):
            _, dt = respond(bot, obs)
        worst = max(worst, dt)
    assert bot.fallbacks == 0 and worst * 1000 < DECISION_BUDGET_MS, f"{worst * 1000:.2f} ms"
