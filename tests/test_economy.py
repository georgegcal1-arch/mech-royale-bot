"""v6 economy (features.ECONOMY): crates, cubes and upgrade preferences, and v5 exactly with it off."""
import math

import pytest

import features
from conftest import DECISION_BUDGET_MS, HELLO, OBS, REPO, action_problems, base_obs, new_bot, respond, v9_off, yaw_diff
from replay import replay
from safety import pick_upgrade

GOLDEN = f"{REPO}/tests/golden/v5_replay.csv"


def _offer(*ids):
    return {"id": 1, "expires": 15, "choices": [{"id": i, "name": i, "desc": ""} for i in ids]}


def _move_yaw(action):
    return math.atan2(action["move"][1], action["move"][0])


@pytest.fixture(scope="module")
def shared_bot():
    return new_bot()


def _quiet_obs(bot):
    """A recorded observation with nothing in sight, a big zone centered on us, a full gun."""
    o = base_obs(100)
    x, y, _ = o["self"]["pos"]
    o["zone"] = {"center": [x, y], "radius": 2000, "next_center": [x, y], "next_radius": 1500,
                 "shrink_start": 1000, "shrink_end": 1100, "dps": 20}
    o["t"] = 50.0
    return o


def _place(bot, o, dist):
    """(x, y, z) of a spot `dist` m away, on the ground, with a clear gun line to a crate there."""
    me = o["self"]
    origin = bot._origin(me)
    for k in range(36):
        a = 2 * math.pi * k / 36
        px, py = me["pos"][0] + dist * math.cos(a), me["pos"][1] + dist * math.sin(a)
        pz = bot.world.ground(px, py)
        if bot.clear(origin, (px, py, pz + 1.2)) and not bot.world.solid_at(px, py, pz + 0.5):
            return px, py, pz
    pytest.skip(f"no clear ground {dist} m around the recorded position")


def _crate(pos, cid=1007):
    return {"id": cid, "pos": list(pos), "hp": 300}


def _settle(bot, o, n=3):
    """Answer o n times, turning our torso to where the bot aims (as the server would, given time)."""
    action = None
    for _ in range(n):
        action, _ = respond(bot, o)
        assert action_problems(action, o) == []
        o["self"]["aim"] = list(action["aim"])
    return action


# --- upgrades ------------------------------------------------------------------------------
@pytest.mark.parametrize("ids, want", [
    (("gun_rate", "plating", "armor"), 1),
    (("armor", "nanites", "sensors"), 0),
    (("sensors", "nanites", "armor"), 2),
    (("sensors", "nanites", "gyros"), 1),
    (("sensors", "gun_rate", "gyros"), 0),
    (("nanites", "plating"), 1),
])
def test_upgrade_preference(ids, want):
    assert pick_upgrade(_offer(*ids)) == want


def test_upgrade_economy_off_is_v5(monkeypatch):
    monkeypatch.setattr(features, "ECONOMY", False)
    assert pick_upgrade(_offer("gun_rate", "plating", "armor")) == 2
    assert pick_upgrade(_offer("plating", "nanites", "sensors")) == 0


def test_offer_answered_in_the_decision_that_sees_it(shared_bot):
    o = _quiet_obs(shared_bot)
    o["offer"] = _offer("sensors", "nanites", "plating")
    action, _ = respond(shared_bot, o)
    assert action["pick"] == 2


# --- crates --------------------------------------------------------------------------------
def test_shoots_a_clear_crate_inside_the_next_circle():
    bot = new_bot()
    o = _quiet_obs(bot)
    pos = _place(bot, o, 50.0)
    o["crates"] = [_crate(pos)]
    action = _settle(bot, o)
    want = math.atan2(pos[1] - o["self"]["pos"][1], pos[0] - o["self"]["pos"][0])
    assert yaw_diff(action["aim"][0], want) < math.radians(3)
    assert action["fire"][0] is True
    assert "crate 1007" in action["debug"]["label"]


def _no_crate_shot(o, bot):
    action = _settle(bot, o)
    assert not any(action["fire"]), action
    assert "crate" not in action["debug"]["label"]


def test_ignores_crate_outside_the_next_circle():
    bot = new_bot()
    o = _quiet_obs(bot)
    pos = _place(bot, o, 50.0)
    o["crates"] = [_crate(pos)]
    o["zone"]["next_center"] = [2 * pos[0] - o["self"]["pos"][0], 2 * pos[1] - o["self"]["pos"][1]]
    o["zone"]["next_radius"] = 55.0  # its edge passes 5 m inside the crate's side: crate outside (margin)
    _no_crate_shot(o, bot)


def test_ignores_crate_too_far():
    bot = new_bot()
    o = _quiet_obs(bot)
    o["crates"] = [_crate(_place(bot, o, 95.0))]
    _no_crate_shot(o, bot)


def test_ignores_crate_with_an_enemy_mech_in_sight():
    bot = new_bot()
    o = _quiet_obs(bot)
    o["crates"] = [_crate(_place(bot, o, 50.0))]
    x, y, z = o["self"]["pos"]
    o["mechs"] = [{"id": 5, "kind": "mech", "pos": [x + 2000, y, z], "vel": [0, 0, 0], "aim": [0, 0],
                   "hp": 1400, "max_hp": 1400, "cubes": 0, "weapons": ["gun"], "charging": False}]
    action = _settle(bot, o)
    assert "crate" not in action["debug"]["label"]


@pytest.mark.parametrize("dist, quiet", [("near", False), ("mid", False), ("far", True)])
def test_heard_gunshot_nearby_stops_crate_shooting(dist, quiet):
    bot = new_bot()
    o = _quiet_obs(bot)
    o["crates"] = [_crate(_place(bot, o, 50.0))]
    o["sounds"] = [{"kind": "gun", "bearing": 0.0, "dist": dist, "age": 0.1}]
    action = _settle(bot, o)
    assert ("crate" in action["debug"]["label"]) is quiet


def test_ignores_crate_near_an_npc_drone():
    bot = new_bot()
    o = _quiet_obs(bot)
    pos = _place(bot, o, 50.0)
    o["crates"] = [_crate(pos)]
    o["mechs"] = [{"id": 101, "kind": "npc", "pos": [pos[0] + 20, pos[1], pos[2]], "vel": [0, 0, 0], "aim": [0, 0]}]
    _no_crate_shot(o, bot)


def test_ignores_crate_on_a_roof():
    bot = new_bot()
    o = _quiet_obs(bot)
    x, y, z = _place(bot, o, 50.0)
    o["crates"] = [_crate((x, y, z + 12.0))]
    _no_crate_shot(o, bot)


def test_crate_volley_is_not_broken_by_idle_reload():
    bot = new_bot()
    o = _quiet_obs(bot)
    o["crates"] = [_crate(_place(bot, o, 50.0))]
    o["self"]["weapons"][0]["ammo"] = 5  # partly used: v5 would reload when idle
    assert not _settle(bot, o).get("reload")
    o["self"]["weapons"][0]["ammo"] = 0
    assert _settle(bot, o).get("reload") is True


def test_economy_off_never_shoots_crates(monkeypatch):
    monkeypatch.setattr(features, "ECONOMY", False)
    bot = new_bot()
    o = _quiet_obs(bot)
    o["crates"] = [_crate(_place(bot, o, 50.0))]
    _no_crate_shot(o, bot)


# --- cubes ---------------------------------------------------------------------------------
def _cube(pos, cid=5012):
    return {"id": cid, "pos": list(pos)}


def test_walks_to_a_cube_inside_the_next_circle():
    bot = new_bot()
    o = _quiet_obs(bot)
    pos = _place(bot, o, 40.0)
    o["cubes"] = [_cube(pos)]
    action = _settle(bot, o, 1)
    want = math.atan2(pos[1] - o["self"]["pos"][1], pos[0] - o["self"]["pos"][0])
    assert action["debug"]["label"].startswith("cube ")
    assert yaw_diff(_move_yaw(action), want) < math.radians(2)
    assert math.hypot(*action["move"]) == pytest.approx(1.0)


def test_ignores_cube_outside_the_next_circle_or_near_a_drone():
    bot = new_bot()
    o = _quiet_obs(bot)
    pos = _place(bot, o, 40.0)
    o["cubes"] = [_cube(pos)]
    o["zone"]["next_center"] = [2 * pos[0] - o["self"]["pos"][0], 2 * pos[1] - o["self"]["pos"][1]]
    o["zone"]["next_radius"] = 45.0
    assert not _settle(bot, o, 1)["debug"]["label"].startswith("cube ")
    o = _quiet_obs(bot)
    o["cubes"] = [_cube(pos)]
    o["mechs"] = [{"id": 101, "kind": "npc", "pos": [pos[0], pos[1] + 30, pos[2]], "vel": [0, 0, 0], "aim": [0, 0]}]
    assert not _settle(bot, o, 1)["debug"]["label"].startswith("cube ")


# --- timing and v5 equality ----------------------------------------------------------------
def test_many_crates_and_cubes_stay_fast():
    """36 crates (every one in a match) and 30 cubes, all eligible-looking, around us."""
    bot = new_bot()
    o = _quiet_obs(bot)
    x, y, _ = o["self"]["pos"]
    o["crates"], o["cubes"] = [], []
    for k in range(36):
        a, r = 2 * math.pi * k / 36, 20 + 1.6 * k
        px, py = x + r * math.cos(a), y + r * math.sin(a)
        o["crates"].append(_crate((px, py, bot.world.ground(px, py)), 1000 + k))
    for k in range(30):
        a, r = 2 * math.pi * k / 30 + 0.1, 30 + 1.5 * k
        px, py = x + r * math.cos(a), y + r * math.sin(a)
        o["cubes"].append(_cube((px, py, bot.world.ground(px, py)), 5000 + k))
    worst = 0.0
    for _ in range(50):
        _, dt = respond(bot, o)
        worst = max(worst, dt)
    assert worst * 1000 < DECISION_BUDGET_MS, f"{worst * 1000:.2f} ms"


def test_economy_off_replay_matches_v5_exactly(monkeypatch, tmp_path):
    monkeypatch.setattr(features, "ECONOMY", False)
    monkeypatch.setattr(features, "HEAL", False)  # v7's heal is on top of v6
    v9_off(monkeypatch)
    out = tmp_path / "off.csv"
    replay(str(out), timing=False)
    with open(GOLDEN, "rb") as f:
        assert out.read_bytes() == f.read(), "with ECONOMY and HEAL off the replay must equal v5's (tests/golden/v5_replay.csv)"
