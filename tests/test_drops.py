"""v9 DROP_CAPTURE and EARLY_ARM (drops.py), on top of LC; both off by default."""
import math

import pytest

import drops
from conftest import base_obs, new_bot, respond
from test_brackets import _flat
from test_v9 import _mech, _mode, switches
from test_v9fg import ARC, GUN, LASER, _arm

LC = ("DUEL", "LIGHTNING", "LASER_GUARD", "NO_CHIP", "NPC_AWARE", "STUCK_ESCAPE", "NAV_ROUTE", "LOOT_SWEEP",
      "NO_MORTAR", "AGGRO_CLOSE", "HEAL_QUIET", "AGGRO_RUSH", "LASER_COUNTER")


@pytest.fixture
def bot():
    return new_bot()


def _scene(bot, t=100.0):
    """Flat open ground, a big calm zone centred on us, match time t, gun + laser."""
    obs = _flat(bot, _arm(base_obs(), GUN, LASER))
    x, y, _ = obs["self"]["pos"]
    obs["t"] = t
    obs["self"]["in_zone"] = True
    obs["zone"].update(center=[x, y], radius=560.0, next_center=[x, y], next_radius=380.0,
                       shrink_start=t + 60, shrink_end=t + 110)
    return obs


def _drop(obs, dx, dy, lands_in=0.0, holder=None, did=20000):
    x, y, z = obs["self"]["pos"]
    d = {"id": did, "pos": [x + dx, y + dy, z], "radius": 8, "lands_in": lands_in, "capture_s": 4,
         "progress": 0, "holder": holder}
    obs["drops"].append(d)
    return d


def _heading(action):
    m = action["move"]
    return math.atan2(m[1], m[0])


def _run(bot, obs, *on):
    with switches(*(LC + on)):
        a, _ = respond(bot, obs)
    return a


# --- DROP_CAPTURE ------------------------------------------------------------------------------------
def test_drop_route_before_it_lands(bot):
    obs = _scene(bot)
    _drop(obs, 120, 0, lands_in=12.0)  # 120 m / 13 m/s = 9.2 s < 12 s
    a = _run(bot, obs, "DROP_CAPTURE")
    assert _mode(a) == "drop"
    assert abs(_heading(a)) < 0.3
    assert a["boost"] is True  # far, with energy


def test_drop_off_by_default(bot):
    obs = _scene(bot)
    _drop(obs, 120, 0, lands_in=12.0)
    assert not _mode(_run(bot, obs)).startswith("drop")


def test_drop_landed_and_contested_far_away_is_skipped(bot):
    obs = _scene(bot)
    _drop(obs, 150, 0, lands_in=0.0)
    obs["mechs"].append(_mech(obs, 1, 170, 30, aim=0.0))  # near the drop, not aimed at us
    assert not _mode(_run(bot, obs, "DROP_CAPTURE")).startswith("drop")


def test_drop_landed_and_uncontested_is_taken(bot):
    obs = _scene(bot)
    _drop(obs, 150, 0, lands_in=0.0)
    assert _mode(_run(bot, obs, "DROP_CAPTURE")) == "drop"


def test_drop_outside_next_circle_or_held_by_another_is_skipped(bot):
    obs = _scene(bot)
    obs["zone"]["next_radius"] = 100.0
    _drop(obs, 95, 0, lands_in=10.0)                 # inside, but within DROP_EDGE_M of the edge
    _drop(obs, 0, 50, lands_in=0.0, holder=obs["self"]["id"] + 1, did=20001)  # someone else's clock is running
    assert not _mode(_run(bot, obs, "DROP_CAPTURE")).startswith("drop")


def test_drop_hold_circles_inside_the_ring(bot):
    obs = _scene(bot)
    d = _drop(obs, 3, 0, lands_in=0.0)
    x, y, _ = obs["self"]["pos"]
    a = _run(bot, obs, "DROP_CAPTURE")
    assert _mode(a) == "drop/hold"
    assert math.hypot(*a["move"]) == pytest.approx(1.0, abs=1e-6)
    # at the orbit radius the move is tangential: it doesn't leave the ring
    rx, ry = x - d["pos"][0], y - d["pos"][1]
    assert abs(a["move"][0] * rx + a["move"][1] * ry) < 0.2 * math.hypot(rx, ry)


def test_drop_waits_in_the_ring_before_landing(bot):
    obs = _scene(bot)
    _drop(obs, 2, 0, lands_in=5.0)
    assert _mode(_run(bot, obs, "DROP_CAPTURE")) == "drop/wait"


def test_drop_aborts_with_two_engaging(bot):
    obs = _scene(bot)
    _drop(obs, 60, 0, lands_in=3.0)
    obs["mechs"] += [_mech(obs, 1, -80, 40), _mech(obs, 2, -80, -40)]  # gun mechs aimed at us, in range
    a = _run(bot, obs, "DROP_CAPTURE")
    assert not _mode(a).startswith("drop")
    assert bot.drop_state["abort_until"] == pytest.approx(obs["t"] + drops.DROP_ABORT_S)


def test_drop_aborts_on_a_ready_laser_with_a_line(bot):
    obs = _scene(bot)
    _drop(obs, 60, 0, lands_in=3.0)
    obs["mechs"].append(_mech(obs, 1, -150, 0, aim=0.5, weapons=("laser",)))  # not aimed at us: not an engager
    assert not _mode(_run(bot, obs, "DROP_CAPTURE")).startswith("drop")


def test_drop_ignores_a_laser_in_its_cooldown_window(bot):
    obs = _scene(bot)
    _drop(obs, 60, 0, lands_in=3.0)
    obs["mechs"].append(_mech(obs, 1, -150, 0, aim=0.5, weapons=("laser",)))
    obs["beams"].append({"kind": "laser", "by": 1, "age": 0.1, "from": [0, 0, 0], "to": [1, 1, 1]})
    _run(bot, obs, "DROP_CAPTURE")  # (the legs go to LC's lc/close in its window; the drop isn't aborted)
    assert bot.drop_state == {"id": 20000, "abort_until": 0.0}


def test_zone_hurry_beats_the_drop(bot):
    obs = _scene(bot)
    obs["self"]["in_zone"] = False
    _drop(obs, 60, 0, lands_in=3.0)
    assert _mode(_run(bot, obs, "DROP_CAPTURE")) == "zone!"


# --- EARLY_ARM ---------------------------------------------------------------------------------------
def _pickup(obs, kind, dx, dy, pid=8000):
    x, y, z = obs["self"]["pos"]
    obs["pickups"].append({"id": pid, "kind": kind, "pos": [x + dx, y + dy, z]})


def test_early_arm_routes_to_the_nearest_laser_or_lightning(bot):
    obs = _arm(_scene(bot, t=5.0), GUN, None)
    _pickup(obs, "lightning", 0, 90)
    _pickup(obs, "laser", 0, -70, pid=8001)
    a = _run(bot, obs, "EARLY_ARM")
    assert _mode(a) == "arm"
    assert abs(_heading(a) + math.pi / 2) < 0.3
    assert a["boost"] is True


@pytest.mark.parametrize("held", [LASER, ARC])
def test_early_arm_not_when_already_armed(bot, held):
    obs = _arm(_scene(bot, t=5.0), GUN, held)
    _pickup(obs, "laser" if held is ARC else "lightning", 0, 60)
    assert _mode(_run(bot, obs, "EARLY_ARM")) != "arm"


def test_early_arm_only_early_but_finishes_a_started_walk(bot):
    obs = _arm(_scene(bot, t=25.0), GUN, None)
    _pickup(obs, "laser", 0, 60)
    assert _mode(_run(bot, obs, "EARLY_ARM")) != "arm"  # never started
    obs = _arm(_scene(bot, t=15.0), GUN, None)
    _pickup(obs, "laser", 0, 60)
    assert _mode(_run(bot, obs, "EARLY_ARM")) == "arm"
    obs["t"] = 25.0
    assert _mode(_run(bot, obs, "EARLY_ARM")) == "arm"  # started in time: carry on
    obs["t"] = drops.ARM_KEEP_S + 1
    assert _mode(_run(bot, obs, "EARLY_ARM")) != "arm"


def test_early_arm_skips_a_pickup_an_enemy_is_nearer_to(bot):
    obs = _arm(_scene(bot, t=5.0), GUN, None)
    _pickup(obs, "laser", 0, 80)
    obs["mechs"].append(_mech(obs, 1, 10, 100, aim=0.0))
    assert _mode(_run(bot, obs, "EARLY_ARM")) != "arm"
