"""v4 survival behavior: low-HP retreat to cover, quiet walking, NPC drone avoidance, zone first.

Scenarios start from real recorded observations (conftest.base_obs) on the real map."""
import copy
import math
import random
import time

from hypothesis import assume, given

import bot as B
from conftest import DECISION_BUDGET_MS, HELLO, OBS, base_obs, new_bot, reset, respond, yaw_diff
from strategies import game_observations

STEP = HELLO["config"]["sound"]["step_speed"]  # 5 m/s: faster than this on the ground makes footsteps
WALK = HELLO["config"]["mech"]["walk_speed"]
BOOST = HELLO["config"]["mech"]["boost_speed"]
PROP_BOT = new_bot()


def _mode(action):
    return ((action.get("debug") or {}).get("label") or "").split(" ")[0]


def _move_yaw(action):
    return math.atan2(action["move"][1], action["move"][0])


def _enemy(obs, mid, dx, dy, dz=0.0, kind="mech"):
    x, y, z = obs["self"]["pos"]
    m = {"id": mid, "kind": kind, "pos": [x + dx, y + dy, z + dz], "vel": [0, 0, 0], "aim": [0, 0],
         "hp": 1400, "max_hp": 1400}
    if kind == "mech":
        m.update(cubes=0, weapons=["gun"], charging=False)
    return m


def _low(obs, frac=0.3):
    obs["self"]["hp"] = frac * obs["self"]["max_hp"]
    return obs


def _settle(bot, obs, n=30):
    """Repeat the situation, letting the torso reach the commanded aim each time."""
    action = None
    for _ in range(n):
        action, _ = respond(bot, obs)
        obs = copy.deepcopy(obs)
        obs["self"]["aim"] = list(action["aim"])
        obs["events"] = []
    return action


# --- low-HP retreat ----------------------------------------------------------------------
def test_retreat_holds_fire_and_faces_the_threat(bot):
    obs = _low(base_obs(100))
    obs["mechs"] = [_enemy(obs, 5, 60, 0)]
    action = _settle(bot, obs)
    assert not any(action["fire"]), "firing while breaking off restarts the regen timer"
    assert yaw_diff(action["aim"][0], 0.0) < 0.2, "torso should keep facing the threat (front armor)"
    assert action["move"][0] <= 1e-9, "legs must not close in on the enemy"
    assert not action.get("jump"), "no jumping (head over cover) while breaking off"


def test_retreat_still_shoots_back_when_cornered(bot):
    obs = _low(base_obs(100))
    obs["self"]["pos"][2] += 300  # open sky: nothing in the way of the shot
    obs["mechs"] = [_enemy(obs, 5, 12, 0)]
    assert _settle(bot, obs)["fire"][0] is True


def test_retreat_hysteresis_and_resume(bot):
    obs = base_obs(100)
    obs["mechs"] = [_enemy(obs, 5, 60, 0)]
    respond(bot, _low(copy.deepcopy(obs), 0.45))
    assert bot.retreating
    respond(bot, _low(copy.deepcopy(obs), 0.7))
    assert bot.retreating, "regenerating from 45% to 70% should not end the retreat"
    respond(bot, _low(copy.deepcopy(obs), B.RESUME_FRAC))
    assert not bot.retreating


def test_hidden_low_hp_stands_still_and_quiet(bot):
    obs = _low(base_obs(100))
    action, _ = respond(bot, obs)
    assert _mode(action) == "hide"
    assert action["move"] == [0.0, 0.0] and not action.get("boost") and not any(action["fire"])


def test_retreat_never_overrides_the_zone(bot):
    obs = _low(base_obs(100))
    z = obs["zone"]
    obs["self"]["pos"] = [z["center"][0] + z["radius"] + 40, z["center"][1], 0.0]
    obs["self"]["in_zone"] = False
    obs["mechs"] = [_enemy(obs, 5, -60, 0)]  # the enemy is between us and the zone
    action, _ = respond(bot, obs)
    x, y = obs["self"]["pos"][:2]
    nx, ny = z["next_center"]
    assert yaw_diff(_move_yaw(action), math.atan2(ny - y, nx - x)) < math.radians(15)
    assert math.hypot(*action["move"]) > 0.95 and action.get("boost") is True


def _sightline_cases(bot, n=200, seed=1):
    """(obs, threats) from recorded positions with 1-2 enemies 40-120 m away in random directions."""
    rnd = random.Random(seed)
    for _ in range(n):
        obs = _low(base_obs(rnd.randrange(len(OBS))))
        obs["self"]["in_zone"] = True
        k = rnd.choice((1, 1, 2))
        obs["mechs"] = [_enemy(obs, 5 + i, *(lambda a, d: (d * math.cos(a), d * math.sin(a)))(
            rnd.uniform(-math.pi, math.pi), rnd.uniform(40, 120))) for i in range(k)]
        yield obs, obs["mechs"]


def test_cover_spots_are_hidden_inside_the_zone_and_not_toward_enemies(bot):
    found = 0
    for obs, threats in _sightline_cases(bot):
        reset(bot)
        spot = bot.find_cover(obs, threats)
        if spot is None:
            continue
        sx, sy, kind = spot
        x, y, z = obs["self"]["pos"]
        if (sx, sy) != (x, y):
            assert B.Bot._edge(obs["zone"], obs["t"], sx, sy) >= B.EDGE_MARGIN_M
            for m in threats:
                assert (sx - x) * (m["pos"][0] - x) + (sy - y) * (m["pos"][1] - y) <= 1e-6
        if kind == "cover":
            found += 1
            g = z if (sx, sy) == (x, y) else bot.world.ground(sx, sy)
            eyes = [bot._eye(m) for m in threats]
            assert bot._hidden(eyes, sx, sy, g, 4.5, 8.0)
    assert found >= 20, f"cover found in only {found} of 200 cases"


def test_walks_into_cover_and_stops(bot):
    """Follow the legs (straight-line kinematics) from exposed spots; each should end hidden and still."""
    reached = tried = 0
    for obs, threats in _sightline_cases(bot, n=120, seed=7):
        reset(bot)
        x, y, z = obs["self"]["pos"]
        eyes = [bot._eye(m) for m in threats]
        if bot._hidden(eyes, x, y, z, 4.5, 8.0):
            continue
        spot = bot.find_cover(obs, threats)
        if spot is None or spot[2] != "cover":
            continue
        tried += 1
        reset(bot)
        o = copy.deepcopy(obs)
        for _ in range(60):  # 6 s
            action, _ = respond(bot, o)
            v = BOOST if action.get("boost") else WALK
            o["self"]["pos"][0] += action["move"][0] * v * 0.1
            o["self"]["pos"][1] += action["move"][1] * v * 0.1
            o["self"]["pos"][2] = bot.world.ground(*o["self"]["pos"][:2])
            o["t"] += 0.1
            o["tick"] += 2
        px, py, pz = o["self"]["pos"]
        if bot._hidden(eyes, px, py, pz, 4.5, 8.0) and action["move"] == [0.0, 0.0]:
            reached += 1
    assert tried >= 10
    assert reached >= 0.9 * tried, f"reached cover in {reached} of {tried}"


def test_cover_search_is_fast():
    """Worst case: a forced search every decision, one enemy in the open, every recorded position."""
    b = new_bot()
    worst = 0.0
    for i, o in enumerate(OBS):
        obs = _low(base_obs(i))
        obs["mechs"] = [_enemy(obs, 5, 70, 20)]
        b.cover, b.retreating = None, True
        _, dt = respond(b, obs)
        worst = max(worst, dt)
    assert worst * 1000 < DECISION_BUDGET_MS, f"slowest decision {worst * 1000:.2f} ms"


# --- quiet -------------------------------------------------------------------------------
@given(game_observations)
def test_quiet_when_no_enemy_in_view(obs):
    assume(not any(m["kind"] == "mech" for m in obs["mechs"]))
    reset(PROP_BOT)
    action, _ = respond(PROP_BOT, obs)
    mode = _mode(action)
    if mode in ("zone!", "walk", "SAFE"):
        return  # the zone forces speed (or the safety net fell back)
    walk = obs["self"]["stats"].get("walk_speed")
    walk = walk if isinstance(walk, (int, float)) and walk > 0 else WALK
    assert math.hypot(*action["move"]) * walk <= STEP + 1e-6, f"mode {mode}"
    assert not action.get("boost")


def test_recorded_match_is_quiet_when_nothing_in_view():
    b = new_bot()
    checked = 0
    for o in OBS:
        action, _ = respond(b, o)
        if not any(m["kind"] == "mech" for m in o["mechs"]) and _mode(action) not in ("zone!", "walk"):
            assert math.hypot(*action["move"]) * WALK <= STEP + 1e-6, f"tick {o['tick']}"
            checked += 1
    assert checked > 50


# --- zone first --------------------------------------------------------------------------
@given(game_observations)
def test_outside_the_zone_always_runs_back(obs):
    me = obs["self"]
    nx, ny = obs["zone"]["next_center"]
    assume(not me["in_zone"] and math.hypot(nx - me["pos"][0], ny - me["pos"][1]) > 1.0)
    reset(PROP_BOT)
    action, _ = respond(PROP_BOT, obs)
    want = math.atan2(ny - me["pos"][1], nx - me["pos"][0])
    assert yaw_diff(_move_yaw(action), want) < math.radians(15)
    assert math.hypot(*action["move"]) > 0.95 and action.get("boost") is True


# --- NPC drones --------------------------------------------------------------------------
def _travel(bot, obs):
    quiet, _ = respond(bot, copy.deepcopy(obs))
    reset(bot)
    return _move_yaw(quiet)


def test_steers_around_npc_on_the_path(bot):
    obs = base_obs(100)
    travel = _travel(bot, obs)
    npc = _enemy(obs, 104, 60 * math.cos(travel), 60 * math.sin(travel), kind="npc")
    obs["mechs"] = [npc]
    action, _ = respond(bot, obs)
    a = _move_yaw(action)
    x, y, _ = obs["self"]["pos"]
    for d in (5, 10, 15, 20):
        p = (x + d * math.cos(a), y + d * math.sin(a))
        assert math.dist(p, npc["pos"][:2]) >= B.NPC_AVOID_M - 1e-6
    assert math.hypot(*action["move"]) * WALK <= STEP + 1e-6, "the detour is still quiet"


def test_steps_out_of_npc_range_when_standing_in_it(bot):
    obs = base_obs(100)
    x, y, _ = obs["self"]["pos"]
    npc = _enemy(obs, 104, 30, 0, kind="npc")
    move = bot.steer(obs, [0.0, 0.0], [npc], "hold")
    assert move[0] < -0.9, f"should step straight away from the drone, got {move}"


def test_fights_an_npc_that_attacked_instead_of_avoiding_it(bot):
    obs = base_obs(100)
    travel = _travel(bot, obs)
    npc = _enemy(obs, 104, 60 * math.cos(travel), 60 * math.sin(travel), kind="npc")
    obs["mechs"] = [npc]
    obs["events"] = [{"type": "damaged", "amount": 25, "weapon": "npc", "bearing": travel, "by": 104}]
    action, _ = respond(bot, obs)
    assert yaw_diff(_move_yaw(action), travel) < math.radians(1)
    assert yaw_diff(action["aim"][0], travel) < 0.1, "should be aiming at the drone"


def test_no_npc_detour_outside_the_zone(bot):
    obs = base_obs(100)
    z = obs["zone"]
    obs["self"]["pos"] = [z["center"][0] + z["radius"] + 40, z["center"][1], 0.0]
    obs["self"]["in_zone"] = False
    obs["t"] = 10.0
    x, y, _ = obs["self"]["pos"]
    nx, ny = z["next_center"]
    want = math.atan2(ny - y, nx - x)
    obs["mechs"] = [_enemy(obs, 104, 40 * math.cos(want), 40 * math.sin(want), kind="npc")]
    action, _ = respond(bot, obs)
    assert yaw_diff(_move_yaw(action), want) < math.radians(15)
