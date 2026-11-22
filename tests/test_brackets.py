"""Bracket experiments on top of L, each behind a switch (off by default): B1 UPGRADES_OFFENSE, B2 AGGRO_CLOSE,
B3 AGGRO_HOLD, B4 HEAL_QUIET. test_v9's whole-match / all-on / Hypothesis tests iterate V9_SWITCHES, so they
cover these too."""
import math

import pytest

import features
import safety
from conftest import base_obs, new_bot, reset, respond
from test_v9 import _label, _mech, _mode, _sky, switches
from test_v9fg import ARC, GUN, LASER, _arm
from test_v9hij import Box, _hunt_scene

BEST = ("DUEL", "LIGHTNING", "LASER_GUARD", "NO_CHIP", "NPC_AWARE", "STUCK_ESCAPE", "NAV_ROUTE", "LOOT_SWEEP")


@pytest.fixture
def bot():
    return new_bot()


def _flat(bot, obs):
    x, y, z = obs["self"]["pos"]
    bot.world = Box(z, x + 900, y + 900, x + 901, y + 901)  # flat and open
    bot.navgrid = None
    return obs


# --- B1 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("choices, on, want", [
    (["plating", "gun_damage", "armor"], True, "gun_damage"),
    (["plating", "gun_damage", "armor"], False, "plating"),
    (["armor", "lightning_range", "laser_damage"], True, "laser_damage"),
    (["sensors", "nanites", "servos"], True, "nanites"),  # no offense offered: v6's order
    (["sensors", "servos", "gyros"], True, "sensors"),    # nothing we want: choice 0
])
def test_b1_offense_first(monkeypatch, choices, on, want):
    monkeypatch.setattr(features, "UPGRADES_OFFENSE", on)
    offer = {"id": 1, "choices": [{"id": c} for c in choices]}
    assert choices[safety.pick_upgrade(offer)] == want


# --- B2 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("weapons", [(GUN, None), (GUN, LASER)], ids=["gun", "gun+laser"])
def test_b2_closes_in_on_a_target_at_mid_range(bot, weapons):
    obs = _flat(bot, _arm(base_obs(100), *weapons))
    obs["mechs"] = [_mech(obs, 7, 80, 0)]
    with switches(*BEST, "AGGRO_CLOSE"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "approach", _label(action)
    assert action["move"][0] > 0.4, "toward it"
    reset(bot)
    _flat(bot, obs)
    with switches(*BEST):
        assert _mode(respond(bot, obs)[0]) != "approach", "off: 80 m is inside NO_CHIP's 100 m, no approach"


def test_b2_holds_the_fight_close(bot):
    obs = _arm(_sky(base_obs(100)), GUN, LASER)
    with switches(*BEST, "AGGRO_CLOSE"):
        assert bot.duel_range(obs["self"]) == 35.0
        obs = _arm(_sky(base_obs(100)), LASER, ARC)
        assert bot.duel_range(obs["self"]) == 30.0
    with switches(*BEST):
        assert bot.duel_range(_arm(_sky(base_obs(100)), GUN, LASER)["self"]) == 140.0


# --- B3 -------------------------------------------------------------------------------------------
def test_b3_takes_cover_and_waits_without_lightning(bot):
    obs = _hunt_scene(bot)
    obs["self"]["weapons"] = [dict(GUN), dict(LASER)]  # no lightning: J alone would not ambush
    with switches(*BEST, "AGGRO_HOLD"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "ambush", _label(action)
    assert action["fire"] == [False, False]
    reset(bot)
    obs = _hunt_scene(bot)
    obs["self"]["weapons"] = [dict(GUN), dict(LASER)]
    with switches(*BEST):
        assert _mode(respond(bot, obs)[0]) != "ambush"


def test_b3_two_in_sight_still_holds_three_do_not(bot):
    obs = _hunt_scene(bot)
    obs["self"]["weapons"] = [dict(GUN), None]
    obs["mechs"].append(_mech(obs, 4, -150, 90))
    with switches(*BEST, "AGGRO_HOLD"):
        assert _mode(respond(bot, obs)[0]) == "ambush"
        reset(bot)
        obs = _hunt_scene(bot)
        obs["self"]["weapons"] = [dict(GUN), None]
        obs["mechs"] += [_mech(obs, 4, -150, 90), _mech(obs, 5, 150, 120)]
        assert _mode(respond(bot, obs)[0]) != "ambush"


# --- B4 -------------------------------------------------------------------------------------------
def _hurt(bot, frac=0.5):
    obs = _flat(bot, _arm(base_obs(100), GUN, None))
    obs["self"]["hp"] = frac * obs["self"]["max_hp"]
    obs["self"]["aim"] = [0.0, 0.0]
    obs["mechs"] = [_mech(obs, 7, 150, 0, aim=math.pi / 2)]  # in sight, gun range, looking elsewhere
    return obs


def _settle(bot, obs, *on):
    with switches(*on):
        for _ in range(3):
            action, _ = respond(bot, obs)
            obs["self"]["aim"] = list(action["aim"])
    return action


def test_b4_holds_fire_while_hurt_and_unengaged(bot):
    obs = _hurt(bot)
    obs["mechs"][0]["pos"][0] -= 70  # 80 m: inside NO_CHIP's 100 m, so without B4 the gun would fire
    action = _settle(bot, obs, *BEST, "HEAL_QUIET")
    assert action["fire"] == [False, False] and " quiet" in _label(action), _label(action)
    reset(bot)
    obs = _hurt(bot)
    obs["mechs"][0]["pos"][0] -= 70  # 80 m: inside NO_CHIP's 100 m, so v6+ would shoot
    assert _settle(bot, obs, *BEST)["fire"][0] is True, "off: it shoots"


def test_b4_never_in_the_final_zone(bot):
    obs = _hurt(bot)
    x, y, _ = obs["self"]["pos"]
    obs["zone"].update(center=[x, y], radius=features.HEAL_FINAL_R, next_center=[x, y], next_radius=25.0,
                       shrink_start=obs["t"] + 30, shrink_end=obs["t"] + 55)
    with switches(*BEST, "HEAL_QUIET"):
        assert bot.heal_quiet(obs, obs["mechs"]) is False, "the last phases: fight"


@pytest.mark.parametrize("case", ["healthy", "hit", "aimed", "close"])
def test_b4_fights_when_engaged_or_healthy(bot, case):
    obs = _hurt(bot, 0.9 if case == "healthy" else 0.5)
    m = obs["mechs"][0]
    m["pos"][0] -= 70  # 80 m away (NO_CHIP lets the gun shoot)
    if case == "hit":
        obs["events"] = [{"type": "damaged", "amount": 45, "weapon": "gun", "bearing": 0.0, "by": 7}]
    if case == "aimed":
        m["aim"] = [math.pi, 0.0]
    if case == "close":
        m["pos"][0] -= 45
    action = _settle(bot, obs, *BEST, "HEAL_QUIET")
    assert " quiet" not in _label(action), _label(action)


# --- B5 -------------------------------------------------------------------------------------------
MORTAR = {"kind": "mortar", "cooldown": 0, "stats": {"speed": 72.0, "gravity": 22.0}}


def _take(bot, obs, *on):
    act = {}
    with switches(*on):
        bot.take_loot(obs, act)
    return act


def _pickup_here(obs, pid, kind):
    x, y, z = obs["self"]["pos"]
    return {"id": pid, "kind": kind, "pos": [x + 2.0, y, z]}


def test_b5_drops_a_mortar_and_never_takes_one(bot):
    obs = _arm(base_obs(100), GUN, MORTAR)
    assert _take(bot, obs, *BEST, "NO_MORTAR") == {"drop": 1}
    assert _take(bot, obs, *BEST) == {}, "off: keep it"
    obs = _arm(base_obs(100), GUN, None)
    obs["pickups"] = [_pickup_here(obs, 8001, "mortar")]
    assert "take" not in _take(bot, obs, *BEST, "NO_MORTAR")
    assert _take(bot, obs, *BEST).get("take") == 8001, "off: an empty slot takes a mortar"


def test_b5_a_laser_still_swaps_the_mortar_out(bot):
    obs = _arm(base_obs(100), GUN, MORTAR)
    obs["pickups"] = [_pickup_here(obs, 8002, "laser")]
    assert _take(bot, obs, *BEST, "NO_MORTAR") == {"take": 8002, "drop": 1}


def test_b5_loot_sweep_never_walks_to_a_mortar(monkeypatch):
    import loot_sweep as LS
    monkeypatch.setattr(features, "NO_MORTAR", True)
    assert LS.would_take("mortar", [dict(GUN), None]) is False
    monkeypatch.setattr(features, "NO_MORTAR", False)
    assert LS.would_take("mortar", [dict(GUN), None]) is True


# --- B2b ------------------------------------------------------------------------------------------
WINNERS = BEST + ("NO_MORTAR", "AGGRO_CLOSE")  # L + B5 + B2


def test_b2b_closes_further_than_b2(bot):
    obs = _flat(bot, _arm(base_obs(100), GUN, None))
    obs["mechs"] = [_mech(obs, 7, 37, 0)]  # inside B2's 40 m, outside B2b's 32 m
    with switches(*WINNERS):
        assert _mode(respond(bot, obs)[0]) != "approach"
    reset(bot)
    _flat(bot, obs)
    with switches(*WINNERS, "AGGRO_RUSH"):
        assert _mode(respond(bot, obs)[0]) == "approach"
        assert bot.duel_range(obs["self"]) == 30.0
        assert bot.duel_range(_arm(base_obs(100), GUN, ARC)["self"]) == 25.0


@pytest.mark.parametrize("dist, boost", [(120, True), (50, False)])
def test_b2b_boosts_to_close_the_gap(bot, dist, boost):
    obs = _flat(bot, _arm(base_obs(100), GUN, None))
    obs["self"]["energy"] = 100.0
    obs["mechs"] = [_mech(obs, 7, dist, 0)]
    with switches(*WINNERS, "AGGRO_RUSH"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "approach" and bool(action.get("boost")) is boost, (_label(action), action.get("boost"))


def test_b2b_closes_on_the_nearest_hunter(bot):
    obs = _flat(bot, _arm(base_obs(100), GUN, None))
    weak = _mech(obs, 7, 0, 150, hp=300, aim=0.0)   # the weakest, north, looking away
    hunter = _mech(obs, 8, 90, 0)                    # nearer, east, aimed at us
    obs["mechs"] = [weak, hunter]
    with switches(*WINNERS, "AGGRO_RUSH"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "approach" and action["move"][0] > 0.7, (_label(action), action["move"])


def test_b2b_prefers_lightning_pickups(bot):
    obs = _arm(base_obs(100), GUN, None)
    obs["pickups"] = [_pickup_here(obs, 8001, "laser"), _pickup_here(obs, 8002, "lightning")]
    assert _take(bot, obs, *WINNERS).get("take") == 8001, "off: laser first"
    assert _take(bot, obs, *WINNERS, "AGGRO_RUSH").get("take") == 8002, "rush: lightning first"


B2B = WINNERS + ("AGGRO_RUSH",)


def test_b2c_closes_further_than_b2b(bot):
    obs = _flat(bot, _arm(base_obs(100), GUN, None))
    obs["mechs"] = [_mech(obs, 7, 30, 0)]  # inside B2b's 32 m, outside B2c's 27 m
    with switches(*B2B):
        assert _mode(respond(bot, obs)[0]) != "approach"
    reset(bot)
    _flat(bot, obs)
    with switches(*B2B, "AGGRO_CLOSER"):
        assert _mode(respond(bot, obs)[0]) == "approach"
        assert bot.duel_range(obs["self"]) == 25.0
        assert bot.duel_range(_arm(base_obs(100), GUN, ARC)["self"]) == 20.0


@pytest.mark.parametrize("dist, energy, boost", [(50, 30.0, True), (120, 20.0, False), (40, 26.0, True)])
def test_b2c_boosts_whenever_energy_above_25(bot, dist, energy, boost):
    obs = _flat(bot, _arm(base_obs(100), GUN, None))
    obs["self"]["energy"] = energy
    obs["mechs"] = [_mech(obs, 7, dist, 0)]
    with switches(*B2B, "AGGRO_CLOSER"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "approach" and bool(action.get("boost")) is boost, (_label(action), action.get("boost"))


# --- F1 THREAT_TARGET / F2 MULTI_GUARD --------------------------------------------------------------
B2C = B2B + ("AGGRO_CLOSER", "LASER_COUNTER")


def _threat_scene(bot, weak_hp):
    obs = _flat(bot, _arm(base_obs(100), GUN, LASER))
    obs["self"]["aim"] = [0.0, 0.0]  # facing east
    weak = _mech(obs, 7, 30, 0, hp=weak_hp, aim=math.pi / 2)            # ahead, looking away
    arc = _mech(obs, 8, -14, 3, weapons=("gun", "lightning"))            # behind us, lightning in reach
    obs["mechs"] = [weak, arc]
    bot.hits = [(obs["t"] - 0.5, 8, 160.0, math.pi)]
    bot.target_id = 7
    return obs


def test_f1_shoots_the_lightning_behind_us_not_the_weak_one(bot):
    obs = _threat_scene(bot, 380)  # under FINISH_FRAC: v5 targeting finishes it first
    with switches(*B2C):
        assert bot.choose_target(obs, obs["mechs"])[0]["id"] == 7
    bot.target_id = 7
    with switches(*B2C, "THREAT_TARGET"):
        target, _, why = bot.choose_target(obs, obs["mechs"])
    assert (target["id"], why) == (8, "threat")


def test_f1_finishes_a_target_that_dies_within_a_second(bot):
    obs = _threat_scene(bot, 150)
    with switches(*B2C, "THREAT_TARGET"):
        target, _, why = bot.choose_target(obs, obs["mechs"])
    assert (target["id"], why) == (7, "finish")


def test_f2_no_rush_into_a_crowd(bot):
    obs = _flat(bot, _arm(base_obs(100), GUN, None))
    obs["mechs"] = [_mech(obs, 7, 60, 0), _mech(obs, 8, 90, 30, aim=0.0), _mech(obs, 9, 100, -30, aim=0.0)]
    with switches(*B2C):
        assert _mode(respond(bot, obs)[0]) == "approach"
    reset(bot)
    _flat(bot, obs)
    with switches(*B2C, "MULTI_GUARD"):
        assert _mode(respond(bot, obs)[0]) != "approach"
    reset(bot)
    _flat(bot, obs)
    obs["mechs"] = obs["mechs"][:2]  # only one other near it: rush as before
    with switches(*B2C, "MULTI_GUARD"):
        assert _mode(respond(bot, obs)[0]) == "approach"
