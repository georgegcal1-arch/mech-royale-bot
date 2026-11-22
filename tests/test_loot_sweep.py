"""v9 experiment L (features.LOOT_SWEEP): walk to cubes and better weapons nearby with no enemy close, death-drop
piles first; abort on 2+ attackers or a laser charging at us. Off by default; test_v9's whole-match / all-on /
Hypothesis tests iterate V9_SWITCHES, so they cover L too. Also the scorecard (tools/scorecard.py)."""
import math
import os
import sys

import pytest

import loot_sweep as LS
from conftest import REPO, base_obs, new_bot, reset, respond
from test_v9 import _label, _mech, _mode, switches
from test_v9fg import GUN, LASER, _arm
from test_v9hij import Box

sys.path.insert(0, os.path.join(REPO, "tools"))
import scorecard as SC  # noqa: E402

BEST = ("DUEL", "LIGHTNING", "LASER_GUARD", "NO_CHIP", "NPC_AWARE", "STUCK_ESCAPE")


def _scene(bot, *weapons):
    """Flat open ground (a box far away), the next circle around us, nothing else."""
    obs = _arm(base_obs(100), *(weapons or (GUN, None)))
    x, y, z = obs["self"]["pos"]
    bot.world = Box(z, x + 900, y + 900, x + 901, y + 901)
    bot.navgrid = None  # this fake world has no grid: straight walks
    obs["zone"].update(center=[x, y], radius=500.0, next_center=[x, y], next_radius=300.0,
                       shrink_start=obs["t"] + 60.0, shrink_end=obs["t"] + 100.0)
    obs["self"]["in_zone"] = True
    return obs


def _cube(obs, cid, dx, dy):
    x, y, z = obs["self"]["pos"]
    return {"id": cid, "pos": [x + dx, y + dy, z]}


def _pickup(obs, pid, kind, dx, dy):
    x, y, z = obs["self"]["pos"]
    return {"id": pid, "kind": kind, "pos": [x + dx, y + dy, z]}


def _toward(action, dx, dy):
    a = math.atan2(dy, dx)
    return action["move"][0] * math.cos(a) + action["move"][1] * math.sin(a)


@pytest.fixture
def bot():
    return new_bot()


def test_sweeps_a_cube_with_an_enemy_far_away(bot):
    obs = _scene(bot)
    obs["cubes"] = [_cube(obs, 5001, 30, 0)]
    obs["mechs"] = [_mech(obs, 7, 0, 150)]
    with switches(*BEST, "LOOT_SWEEP"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "loot", _label(action)
    assert _toward(action, 30, 0) > 0.9
    reset(bot)
    bot.world, bot.navgrid = Box(obs["self"]["pos"][2], 1e5, 1e5, 1e5 + 1, 1e5 + 1), None
    with switches(*BEST):
        assert _mode(respond(bot, obs)[0]) not in ("loot", "cube"), "off: economy waits for quiet (a mech is seen)"


@pytest.mark.parametrize("case", ["enemy_close", "too_far", "roof", "outside_next", "near_drone"])
def test_no_sweep(bot, case):
    obs = _scene(bot)
    dx = 55 if case == "too_far" else 30
    cube = _cube(obs, 5001, dx, 0)
    if case == "roof":
        cube["pos"][2] += 12.0
    obs["cubes"] = [cube]
    if case == "enemy_close":
        obs["mechs"] = [_mech(obs, 7, -30, 0)]
    if case == "outside_next":
        x, y, _ = obs["self"]["pos"]
        obs["zone"].update(next_center=[x - 200, y], next_radius=210.0)
    if case == "near_drone":
        x, y, z = obs["self"]["pos"]
        obs["mechs"] = [{"id": 104, "kind": "npc", "pos": [x + 50, y, z], "vel": [0, 0, 0], "aim": [0, 0],
                         "hp": 350, "max_hp": 350}]
    assert bot.sweep_goal(obs, [m for m in obs["mechs"] if m["kind"] == "mech"]) is None


def test_prefers_the_death_pile(bot):
    obs = _scene(bot)
    x, y, _ = obs["self"]["pos"]
    bot.kill_spots = [(obs["t"] - 2.0, x - 32.0, y)]
    obs["cubes"] = [_cube(obs, 5001, 15, 0)] + [_cube(obs, 5002 + k, -30 - k, 1) for k in range(3)]
    goal = bot.sweep_goal(obs, [])
    assert goal is not None and goal[0] < x, f"the pile (west), not the single cube 15 m east: {goal}"


def test_walks_to_a_fresh_kill_spot_before_the_pile_is_in_view(bot):
    obs = _scene(bot)
    x, y, _ = obs["self"]["pos"]
    obs["events"] = [{"type": "kill", "target": 7}]
    bot.last_pos[7] = (x + 50.0, y, obs["self"]["pos"][2])
    bot.note_kills(obs)
    assert bot.sweep_goal(obs, []) == (x + 50.0, y)
    obs["t"] += LS.SWEEP_AFTER_KILL_S + 1.0
    obs["events"] = []  # a later observation: the kill event was in the earlier one
    bot.note_kills(obs)
    assert bot.sweep_goal(obs, []) is None, "old news"


def test_better_weapons_only(bot):
    obs = _scene(bot, GUN, None)
    obs["pickups"] = [_pickup(obs, 8001, "laser", 30, 0)]
    assert bot.sweep_goal(obs, []) is not None, "a laser into the empty slot"
    obs = _scene(bot, GUN, LASER)
    obs["pickups"] = [_pickup(obs, 8002, "mortar", 30, 0)]
    assert bot.sweep_goal(obs, []) is None, "loot.py wouldn't take a mortar here"
    assert LS.would_take("lightning", [dict(GUN), dict(LASER)]) is True, "completes laser + lightning"


@pytest.mark.parametrize("case", ["two_attackers", "laser_charging"])
def test_aborts(bot, case):
    obs = _scene(bot)
    obs["cubes"] = [_cube(obs, 5001, 30, 0)]
    t = obs["t"]
    if case == "two_attackers":
        bot.hits = [(t - 1.0, 7, 45, 0.0), (t - 0.5, 8, 45, 1.0)]
        enemies = []
    else:
        m = _mech(obs, 7, 0, 120, weapons=("gun", "laser"))
        m["charging"] = True
        obs["mechs"] = [m]
        enemies = [m]
    assert bot.sweep_goal(obs, enemies) is None
    assert bot.sweep_cool > t, "and no sweep for a while"


def test_scorecard_on_the_recorded_match():
    """The scorecard runs on any obs stream; on check.py's recorded match (no sent actions) it still reports
    the fighting and defense numbers from the events."""
    from conftest import HELLO, OBS
    c = SC.scorecard([HELLO] + OBS)
    assert c["taken_mechs"] == 1665.0 and c["laser_dmg"] == 1260.0 and c["laser_hits"] == 4
    assert c["rear_dmg"] == 0.0 and c["hits"].get("gun") == 10
    assert c["shots"].get("gun", 0) >= 10
    assert SC._would_take("laser", [dict(GUN), None]) and not SC._would_take("mortar", [dict(GUN), dict(LASER)])
