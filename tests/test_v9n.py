"""v9 experiment N (features.NPC_AWARE): self-defense against drones, staying out of their aggro range while
fighting a mech, and farming them when quiet. Off by default; test_v9's whole-match / all-on / Hypothesis
tests iterate V9_SWITCHES, so they cover N too."""
import math

import pytest

import npc as N
from conftest import base_obs, new_bot, reset, respond
from test_v9 import _label, _mech, _mode, _sky, switches
from test_v9fg import ARC, GUN, _arm

BEST = ("DUEL", "LIGHTNING")


def _drone(obs, dx, dy, nid=110, hp=350):
    x, y, z = obs["self"]["pos"]
    return {"id": nid, "kind": "npc", "pos": [x + dx, y + dy, z], "vel": [0.0, 0.0, 0.0],
            "aim": [math.atan2(-dy, -dx), 0.0], "hp": hp, "max_hp": 350}


def _hit_by(obs, by, dx, dy):
    obs["events"] = [{"type": "damaged", "amount": 25, "weapon": "npc", "bearing": math.atan2(dy, dx), "by": by}]


def _run(bot, obs, *on, n=3):
    with switches(*on):
        for _ in range(n):
            action, _ = respond(bot, obs)
            obs["self"]["aim"] = list(action["aim"])
            obs["events"] = obs["events"]  # keep the hit fresh (same t)
    return action


@pytest.fixture
def bot():
    return new_bot()


# --- self-defense ---------------------------------------------------------------------------------
def test_shoots_back_at_a_drone_hitting_us_instead_of_a_far_mech(bot):
    obs = _arm(_sky(base_obs(100)), GUN, None)
    obs["mechs"] = [_mech(obs, 15, 70, 0), _drone(obs, 0, -20)]
    _hit_by(obs, 110, 0, -20)
    action = _run(bot, obs, *BEST, "NPC_AWARE")
    assert " npc 110" in _label(action), _label(action)
    assert action["fire"][0] is True, "the gun on the drone"
    yaw = action["aim"][0]
    assert abs((yaw + math.pi / 2 + math.pi) % (2 * math.pi) - math.pi) < 0.2, "aimed south, at the drone"
    reset(bot)
    assert " npc " not in _label(_run(bot, obs, *BEST)), "NPC_AWARE off: v6's mechs first"


def test_lightning_on_a_close_drone(bot):
    obs = _arm(_sky(base_obs(100)), GUN, ARC)
    obs["mechs"] = [_mech(obs, 15, 90, 0), _drone(obs, 0, -20)]
    _hit_by(obs, 110, 0, -20)
    action = _run(bot, obs, *BEST, "NPC_AWARE")
    assert " npc 110" in _label(action) and action["fire"][1] is True, (_label(action), action["fire"])


@pytest.mark.parametrize("case", ["mech_close", "not_hitting", "too_far"])
def test_no_self_defense(bot, case):
    obs = _arm(_sky(base_obs(100)), GUN, None)
    mech_d = 30 if case == "mech_close" else 70
    drone_d = 80 if case == "too_far" else 20
    obs["mechs"] = [_mech(obs, 15, mech_d, 0), _drone(obs, 0, -drone_d)]
    if case != "not_hitting":
        _hit_by(obs, 110, 0, -drone_d)
    assert " npc " not in _label(_run(bot, obs, *BEST, "NPC_AWARE"))


# --- avoid ----------------------------------------------------------------------------------------
def test_avoid_turns_a_move_out_of_aggro_range(bot):
    obs = _sky(base_obs(100))
    obs["mechs"] = [_drone(obs, 0, 55)]  # 55 m north: a step north takes us inside 45 + 5 m
    turned = bot.npc_avoid(obs, [0.0, 1.0], 45.0)
    assert turned != [0.0, 1.0]
    x, y, _ = obs["self"]["pos"]
    reach = bot._walk_mps(obs["self"]) * N.NPC_AVOID_LOOK_S
    px, py = x + reach * turned[0], y + reach * turned[1]
    d0, d1 = 55.0, math.hypot(px - x, py - (y + 55))
    assert d1 >= 50.0 or d1 >= d0, "out of range, or at least not closer"
    assert bot.npc_avoid(obs, [0.0, -1.0], 45.0) == [0.0, -1.0], "moving away: unchanged"


def _into_aggro(bot, obs, move, r=50.0):
    x, y, _ = obs["self"]["pos"]
    drone = next(m for m in obs["mechs"] if m["kind"] == "npc")
    reach = bot._walk_mps(obs["self"]) * N.NPC_AVOID_LOOK_S
    px, py = x + reach * move[0], y + reach * move[1]
    d0 = math.hypot(x - drone["pos"][0], y - drone["pos"][1])
    d1 = math.hypot(px - drone["pos"][0], py - drone["pos"][1])
    return d1 < r and d1 < d0


@pytest.mark.parametrize("on", [True, False])
def test_avoid_in_a_mech_fight(on):
    """A mech east at 80 m (the duel circles it: north/south legs), a drone 52 m north: with N no fight
    move steps into its aggro range; without N some do."""
    bad, fight = 0, 0
    for seed in range(6):
        bot = new_bot()
        bot.strafe_dir = 1 if seed % 2 else -1
        obs = _arm(_sky(base_obs(100)), GUN, None)
        obs["mechs"] = [_mech(obs, 15, 80, 0), _drone(obs, 0, 52)]
        with switches(*BEST, *(("NPC_AWARE",) if on else ())):
            for k in range(6):
                obs["t"] += 0.5 * k  # new strafe/duel legs now and then
                action, _ = respond(bot, obs)
                if _mode(action).split("/")[0] in ("duel", "strafe"):
                    fight += 1
                    bad += _into_aggro(bot, obs, action["move"])
    assert fight > 0
    if on:
        assert bad == 0, f"{bad} of {fight} fight moves stepped into the drone's range"
    else:
        assert bad > 0, "control: without N the circling walks into it"


# --- farm -----------------------------------------------------------------------------------------
def test_farms_a_drone_when_quiet(bot):
    obs = _arm(_sky(base_obs(100)), GUN, None)
    obs["mechs"] = [_drone(obs, 40, 0)]
    obs["self"]["aim"] = [0.0, 0.0]
    action = _run(bot, obs, *BEST, "NPC_AWARE")
    assert "farm 110" in _label(action) and action["fire"][0] is True, _label(action)
    reset(bot)
    assert "farm" not in _label(_run(bot, obs, *BEST)), "NPC_AWARE off: no farming"


@pytest.mark.parametrize("case", ["low_hp", "far", "mech_seen"])
def test_no_farm(bot, case):
    obs = _arm(_sky(base_obs(100)), GUN, None)
    obs["mechs"] = [_drone(obs, 90 if case == "far" else 40, 0)]
    if case == "low_hp":
        obs["self"]["hp"] = 0.4 * obs["self"]["max_hp"]
    if case == "mech_seen":
        obs["mechs"].append(_mech(obs, 15, -150, 0))
    assert "farm" not in _label(_run(bot, obs, *BEST, "NPC_AWARE"))


def test_farm_without_a_gun_walks_into_lightning_range(bot):
    from test_v9fg import LASER
    obs = _arm(_sky(base_obs(100)), LASER, ARC)
    obs["mechs"] = [_drone(obs, 55, 0)]
    farm = bot.npc_farm(obs)
    assert farm is not None and farm[1] is not None, "walk closer first"
    obs["mechs"] = [_drone(obs, 30, 0)]
    assert bot.npc_farm(obs)[1] is None
