"""v9 experiments F (features.LIGHTNING) and G (features.LOOKAHEAD). With both off the bot is v6 exactly (the
golden replays force every V9 switch off; test_v9's all-on tests iterate V9_SWITCHES, so they cover F and G)."""
import math
import time

import pytest

import lookahead as L
from conftest import DECISION_BUDGET_MS, OBS, base_obs, new_bot, reset, respond
from test_economy import _crate, _place, _quiet_obs, _settle
from test_v9 import Wall, _label, _mech, _mode, _run_match, _sky, switches

GUN = {"kind": "gun", "cooldown": 0, "ammo": 12, "reload": 0,
       "stats": {"damage": 45, "cooldown": 0.24, "speed": 252, "range": 230, "mag": 12, "reload": 2.4}}
LASER = {"kind": "laser", "cooldown": 0, "charge": 0,
         "stats": {"damage": 315, "range": 280, "charge": 0.75, "cooldown": 1.65}}
ARC = {"kind": "lightning", "cooldown": 0, "stats": {"range": 45, "cone_deg": 30, "energy": 3, "cooldown": 0.25}}
MORTAR = {"kind": "mortar", "cooldown": 0, "stats": {"speed": 72.0, "gravity": 22.0}}


def _arm(obs, *weapons):
    obs["self"]["weapons"] = [dict(w) if w else None for w in weapons]
    obs["self"]["energy"] = 100
    return obs


def _pickup(obs, pid, kind, dx=2.0):
    x, y, z = obs["self"]["pos"]
    return {"id": pid, "kind": kind, "pos": [x + dx, y, z]}


# --- F: loot --------------------------------------------------------------------------------------
def _take(bot, obs):
    act = {}
    bot.take_loot(obs, act)
    return act


@pytest.mark.parametrize("held, near, take, drop", [
    ((GUN, None), ("mortar", "lightning", "laser"), "laser", None),       # empty slot: laser first
    ((GUN, None), ("mortar", "lightning"), "lightning", None),            # ...then lightning
    ((GUN, MORTAR), ("lightning",), "lightning", 1),                      # lightning replaces a mortar
    ((GUN, LASER), ("lightning",), "lightning", 0),                       # laser + lightning: drop the gun
    ((GUN, ARC), ("laser",), "laser", 0),                                 # ...either order
    ((LASER, ARC), ("mortar",), None, None),                              # complete: keep them
    ((GUN, LASER), ("mortar",), None, None),
])
def test_lightning_take_order(bot, held, near, take, drop):
    obs = _arm(base_obs(100), *held)
    kinds = {k: 9000 + i for i, k in enumerate(near)}
    obs["pickups"] = [_pickup(obs, pid, k) for k, pid in kinds.items()]
    with switches("LIGHTNING"):
        act = _take(bot, obs)
    assert act.get("take") == (kinds[take] if take else None)
    assert act.get("drop") == drop


def test_lightning_off_never_takes_lightning(bot):
    obs = _arm(base_obs(100), GUN, None)
    obs["pickups"] = [_pickup(obs, 9001, "lightning")]
    with switches():
        assert _take(bot, obs) == {}


def test_the_gun_is_never_taken_back(bot):
    obs = _arm(base_obs(100), LASER, None)
    obs["pickups"] = [_pickup(obs, 9001, "gun")]
    with switches("LIGHTNING"):
        assert _take(bot, obs) == {}


def test_early_fetch_walks_to_lightning(bot):
    obs = _arm(base_obs(10), GUN, LASER)
    obs["pickups"] = [_pickup(obs, 9001, "lightning", dx=40.0)]
    with switches("LIGHTNING"):
        assert bot.fetch_goal(obs, []) is not None
    with switches():
        assert bot.fetch_goal(obs, []) is None, "v6 only fetches lasers"


# --- F: torso -------------------------------------------------------------------------------------
def _fight(bot, dist, *weapons):
    obs = _arm(_sky(base_obs(100)), *weapons)
    obs["self"]["aim"] = [0.0, 0.0]
    m = _mech(obs, 3, dist, 0)
    obs["mechs"] = [m]
    with switches("LIGHTNING"):
        action, _ = respond(bot, obs)
    return action


def test_lightning_fires_up_close_and_the_laser_waits(bot):
    action = _fight(bot, 25.0, LASER, ARC)
    assert action["fire"] == [False, True], "no new laser charge inside lightning range; lightning fires"


def test_laser_beyond_lightning_range(bot):
    action = _fight(bot, 120.0, LASER, ARC)
    assert action["fire"] == [True, False], "the laser starts its charge; lightning is out of range"


def test_out_of_energy_the_laser_charges_up_close(bot):
    obs = _arm(_sky(base_obs(100)), LASER, ARC)
    obs["self"]["aim"] = [0.0, 0.0]
    obs["self"]["energy"] = 1
    obs["mechs"] = [_mech(obs, 3, 25.0, 0)]
    with switches("LIGHTNING"):
        action, _ = respond(bot, obs)
    assert action["fire"] == [True, False]


def test_gun_and_lightning_both_fire_up_close(bot):
    obs = _arm(_sky(base_obs(100)), GUN, ARC)
    obs["mechs"] = [_mech(obs, 3, 25.0, 0)]
    with switches("LIGHTNING"):
        for _ in range(3):
            action, _ = respond(bot, obs)
            obs["self"]["aim"] = list(action["aim"])
    assert action["fire"] == [True, True]


# --- F: crates without the gun --------------------------------------------------------------------
def test_gunless_crate_with_the_laser(bot):
    o = _arm(_quiet_obs(bot), LASER, ARC)
    o["crates"] = [_crate(_place(bot, o, 60.0))]
    with switches("LIGHTNING"):
        action = _settle(bot, o)
        assert action["fire"][0] is True, "charging at the crate"
        o["self"]["weapons"][0]["charge"] = 0.75  # full, on target
        action, _ = respond(bot, o)
    assert action["fire"][0] is False, "let go: fires the full charge"
    assert "crate" in _label(action)


def test_gunless_crate_with_lightning_up_close(bot):
    o = _arm(_quiet_obs(bot), LASER, ARC)
    o["crates"] = [_crate(_place(bot, o, 20.0))]
    with switches("LIGHTNING"):
        action = _settle(bot, o)
    assert action["fire"] == [False, True]


def test_gunless_crates_ignored_with_lightning_off(bot):
    o = _arm(_quiet_obs(bot), LASER, ARC)
    o["crates"] = [_crate(_place(bot, o, 20.0))]
    with switches():
        action = _settle(bot, o)
    assert action["fire"] == [False, False]


def test_whole_match_with_lightning():
    with switches("LIGHTNING"):
        _run_match(new_bot())


# --- G: lookahead ---------------------------------------------------------------------------------
def _bullet_at_us(obs, lead_s=0.4, pid=500):
    """A gun bullet from the east that will pass through our center in lead_s seconds."""
    x, y, z = obs["self"]["pos"]
    return {"id": pid, "kind": "gun", "pos": [x + 252.0 * lead_s, y, z + 4.5], "vel": [-252.0, 0.0, 0.0]}


def test_lookahead_steps_off_a_bullet_line(bot, monkeypatch):
    """With v5's dodge off, lookahead does the dodging; with it on, the dodge is already safe and stays."""
    obs = _sky(base_obs(100))
    obs["self"]["vel"] = [0.0, 0.0, 0.0]
    obs["projectiles"] = [_bullet_at_us(obs)]
    with switches("LOOKAHEAD"):
        threats = bot.look_threats(obs, [])
        stand = bot._expected_damage(obs, bot._sim(obs["self"], (0, 0), 0, 58.5, None), threats, lambda i: 0.7)
        assert stand == pytest.approx(45.0)
        action, _ = respond(bot, obs)
        assert _mode(action) == "dodge", "the v5 dodge already gets out of the way"
        monkeypatch.setattr(__import__("features"), "DODGE", False)
        reset(bot)
        action, _ = respond(bot, obs)
    assert _mode(action) == "look"
    mx, my = action["move"]
    assert abs(my) > 0.7, f"sideways off the east-west line: {action['move']}"


def test_lookahead_is_v6_without_threats(bot):
    obs = _sky(base_obs(100))
    with switches():
        v6, _ = respond(bot, obs)
    reset(bot)
    with switches("LOOKAHEAD"):
        look, _ = respond(bot, obs)
        assert bot.look_threats(obs, []) is None
    assert look["move"] == v6["move"] and _mode(look) == _mode(v6)


def test_lookahead_ignores_a_bullet_passing_wide(bot):
    obs = _sky(base_obs(100))
    b = _bullet_at_us(obs)
    b["pos"][1] += 60.0
    obs["projectiles"] = [b]
    with switches("LOOKAHEAD"):
        assert bot.look_threats(obs, []) is None


def test_lookahead_hides_from_a_gunner_behind_a_wall(bot):
    """A gunner aimed at us from the south and a wall 3 m north of us: moving north takes us out of its
    line of fire for the rest of the run, which beats any strafe."""
    obs = base_obs(100)
    x, y, z = obs["self"]["pos"]
    bot.world = Wall(z, y + 3.0)
    obs["mechs"] = [_mech(obs, 3, 0, -80)]
    with switches("LOOKAHEAD"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "look", _label(action)
    assert action["move"][1] > 0.7, action["move"]


def test_lookahead_shell_splash():
    bot = new_bot()
    obs = _sky(base_obs(100))
    x, y, z = obs["self"]["pos"]
    # a shell falling straight onto us in 0.5 s
    shell = {"id": 7, "kind": "mortar", "pos": [x, y, z + 11.0 * 0.5 + 0.5 * 22 * 0.25], "vel": [0.0, 0.0, -11.0]}
    obs["projectiles"] = [shell]
    threats = bot.look_threats(obs, [])
    stay = bot._expected_damage(obs, bot._sim(obs["self"], (0, 0), 0, 58.5, None), threats, lambda i: 0.7)
    run = bot._expected_damage(obs, bot._sim(obs["self"], (1, 0), 19.5, 58.5, None), threats, lambda i: 0.7)
    assert stay == pytest.approx(360.0) and run < 160.0


def test_lookahead_budget_with_many_threats_on_the_real_map(monkeypatch):
    """20 bullets and 4 engaging gunners around us on the real map, all switches on: under the 10 ms cap,
    and an artificially tiny budget still answers (the baseline is always scored)."""
    bot = new_bot()
    obs = base_obs(200)
    x, y, z = obs["self"]["pos"]
    obs["mechs"] = [_mech(obs, 3 + k, 60 * math.cos(k * 1.57 + 0.3), 60 * math.sin(k * 1.57 + 0.3),
                          weapons=("gun", "laser")) for k in range(4)]
    obs["mechs"][0]["charging"] = True
    obs["projectiles"] = [_bullet_at_us(obs, 0.1 + 0.03 * i, 600 + i) for i in range(20)]
    worst = 0.0
    with switches(*__import__("features").V9_SWITCHES):
        for _ in range(5):
            bot.squad = None
            _, dt = respond(bot, obs)
            worst = max(worst, dt)
        assert bot.fallbacks == 0
        assert worst * 1000 < DECISION_BUDGET_MS, f"{worst * 1000:.2f} ms"
        monkeypatch.setattr(L, "LOOK_BUDGET_S", 0.0)
        t0 = time.perf_counter()
        action, _ = respond(bot, obs)
        assert bot.fallbacks == 0 and (time.perf_counter() - t0) * 1000 < DECISION_BUDGET_MS


def test_whole_match_with_lookahead():
    with switches("LOOKAHEAD"):
        labels = _run_match(new_bot())
    assert any(lab.startswith("look ") for lab in labels)
