"""v5: target choice, strafing, dodging, the early laser fetch, circular aim; and the zone still comes first.

Scenarios start from real recorded observations (conftest.base_obs) on the real map; where the map
would get in the way of the point being tested, the scene is lifted into open sky (z + 300)."""
import copy
import math

from hypothesis import assume, given

import bot as B
import features
from conftest import HELLO, base_obs, new_bot, reset, respond, v9_off, yaw_diff
from strategies import game_observations

PROP_BOT = new_bot()
SKY = 300.0


def _mode(action):
    return ((action.get("debug") or {}).get("label") or "").split(" ")[0]


def _label(action):
    return (action.get("debug") or {}).get("label") or ""


def _move_yaw(action):
    return math.atan2(action["move"][1], action["move"][0])


def _sky(obs):
    obs["self"]["pos"][2] += SKY
    return obs


def _mech(obs, mid, dx, dy, dz=0.0, hp=1400, vel=(0.0, 0.0, 0.0), kind="mech"):
    x, y, z = obs["self"]["pos"]
    m = {"id": mid, "kind": kind, "pos": [x + dx, y + dy, z + dz], "vel": list(vel), "aim": [0, 0],
         "hp": hp, "max_hp": 1400}
    if kind == "mech":
        m.update(cubes=0, weapons=["gun"], charging=False)
    return m


def _hit(by, bearing=0.0, amount=45.0):
    return {"type": "damaged", "amount": amount, "weapon": "gun", "bearing": bearing, "by": by}


def _target_id(action):
    lab = _label(action).split("·")[-1].split()
    return int(lab[-1]) if lab and lab[-1].isdigit() else None


# --- 1. target choice --------------------------------------------------------------------
def test_lowest_hp_clear_mech_beats_a_nearer_one(bot):
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, 30, 0), _mech(obs, 7, 0, 120, hp=300)]
    action, _ = respond(bot, obs)
    assert _target_id(action) == 7 and "weakest" in _label(action)
    assert yaw_diff(action["aim"][0], math.pi / 2) < 0.05


def test_low_hp_mech_out_of_range_is_not_chosen(bot):
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, 30, 0), _mech(obs, 7, 0, 260, hp=100)]
    action, _ = respond(bot, obs)
    assert _target_id(action) == 3


def test_low_hp_mech_behind_a_building_is_skipped(bot):
    w = bot.world
    for minx, miny, maxx, maxy, base, top, edges in sorted(w.prisms, key=lambda p: p[4] - p[5]):
        cx, cy, zm = (minx + maxx) / 2, (miny + maxy) / 2, (base + top) / 2
        if top - base > 20 and w.blocked((cx, cy, zm - 1), (cx, cy, zm + 1)):
            break
    obs = base_obs(100)
    obs["self"]["pos"] = [cx - 70, cy, zm - 7]
    obs["mechs"] = [_mech(obs, 3, 0, 0, dz=SKY), _mech(obs, 7, 140, 0, hp=100)]  # 3 floats in open sky above us
    obs["mechs"][0]["pos"] = [cx - 70, cy + 40, zm - 7]
    assert w.blocked((cx - 70, cy, zm), (cx + 70, cy, zm - 2.5)), "the building hides mech 7"
    if not bot.clear((cx - 70, cy, zm), (cx - 70, cy + 40, zm - 2.5)):
        return  # mech 3's line is blocked too on this map spot: nothing to compare
    action, _ = respond(bot, obs)
    assert _target_id(action) == 3


def test_mechs_beat_an_attacking_npc(bot, monkeypatch):
    # v5's rule; v9 NPC_AWARE turns it around (a drone hitting us within 50 m first: tests/test_v9n.py)
    monkeypatch.setattr(features, "NPC_AWARE", False)
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 104, 20, 0, kind="npc", hp=50), _mech(obs, 3, 0, 80)]
    obs["events"] = [_hit(104)]
    action, _ = respond(bot, obs)
    assert _target_id(action) == 3


def test_attacker_takes_over_unless_target_nearly_dead(bot):
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, 60, 0, hp=600), _mech(obs, 7, 0, 90)]
    obs["events"] = [_hit(7, bearing=math.pi / 2)]
    action, _ = respond(bot, obs)
    assert _target_id(action) == 7 and "attacker" in _label(action)

    reset(bot)
    obs["mechs"][0]["hp"] = 0.25 * 1400  # under FINISH_FRAC: finish it first
    action, _ = respond(bot, obs)
    assert _target_id(action) == 3


def test_unseen_attacker_is_faced_unless_finishing(bot):
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, 60, 0, hp=900)]
    obs["events"] = [_hit(7, bearing=2.5)]
    action, _ = respond(bot, obs)
    assert yaw_diff(action["aim"][0], 2.5) < 1e-6 and not any(action["fire"])


def test_target_is_sticky(bot):
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, 60, 0, hp=700), _mech(obs, 7, 0, 60, hp=800)]
    assert _target_id(respond(bot, obs)[0]) == 3
    obs["mechs"][0]["hp"] = 760  # now 40 HP more than... still within STICKY_HP of 7's 800
    obs["mechs"][1]["hp"] = 720
    assert _target_id(respond(bot, obs)[0]) == 3, "a 40 HP difference shouldn't flip the target"
    obs["mechs"][1]["hp"] = 500
    assert _target_id(respond(bot, obs)[0]) == 7


# --- 2. strafe ---------------------------------------------------------------------------
def _strafe_run(bot, obs, steps):
    out = []
    o = copy.deepcopy(obs)
    for _ in range(steps):
        action, _ = respond(bot, o)
        out.append(action)
        o["t"] += 0.1
        o["tick"] += 2
    return out


def test_strafe_is_perpendicular_and_full_speed(bot, monkeypatch):
    v9_off(monkeypatch)  # v5's strafe: DUEL circles instead, LEARN_DODGE retimes it
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, 80, 0)]
    action, _ = respond(bot, obs)
    assert _mode(action) == "strafe"
    mx, my = action["move"]
    assert abs(mx) < 1e-9 and abs(abs(my) - 1) < 1e-9


def test_strafe_reverses_every_1_to_2_s_and_is_seeded(bot, monkeypatch):
    v9_off(monkeypatch)  # v5's strafe: DUEL circles instead, LEARN_DODGE retimes it
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, 80, 0)]
    run = _strafe_run(bot, obs, 120)  # 12 s
    sides = [1 if a["move"][1] > 0 else -1 for a in run]
    flips = [i for i in range(1, len(sides)) if sides[i] != sides[i - 1]]
    gaps = [(b - a) / 10 for a, b in zip(flips, flips[1:])]
    assert len(flips) >= 6
    assert all(0.95 <= g <= 2.05 for g in gaps), gaps
    again = _strafe_run(new_bot(), obs, 120)
    assert [a["move"] for a in again] == [a["move"] for a in run], "same seed, same pattern"


def test_strafe_reverses_before_the_zone_edge(bot, monkeypatch):
    v9_off(monkeypatch)  # v5's strafe: DUEL circles instead, LEARN_DODGE retimes it
    obs = _sky(base_obs(100))
    z = obs["zone"]
    x, y, _ = obs["self"]["pos"]
    # an enemy straight east; the zone edge just north of us (but not close enough to hurry)
    z["center"] = [x, y - (500 - (B.EDGE_MARGIN_M + 2))]  # edge 27 m north of us
    z["radius"] = z["next_radius"] = 500
    z["next_center"] = list(z["center"])
    z["shrink_start"] = z["shrink_end"] = 1e6
    obs["mechs"] = [_mech(obs, 3, 80, 0)]
    for _ in range(30):
        action, _ = respond(bot, obs)
        assert _mode(action) == "strafe" and action["move"][1] < 0, "strafed north toward the edge"
        obs["t"] += 0.1
        obs["tick"] += 2


def test_strafe_reverses_before_a_wall(bot):
    w = bot.world
    for minx, miny, maxx, maxy, base, top, edges in sorted(w.prisms, key=lambda p: (p[2] - p[0]) * (p[3] - p[1])):
        if top - base < 8 or maxx - minx < 20 or maxy - miny < 20:
            continue
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        g = w.ground(cx, cy)
        if not (base <= g + 1 <= top and w.solid_at(cx, cy, g + 1)):
            continue
        # walk south from the center until just outside the building
        sy = cy
        while w.solid_at(cx, sy, g + 1) and sy > miny - 5:
            sy -= 0.5
        if w.solid_at(cx, sy - 3, w.ground(cx, sy - 3) + 1):
            continue
        break
    obs = base_obs(100)
    gz = w.ground(cx, sy - 3)
    obs["self"]["pos"] = [cx, sy - 3, gz]
    obs["self"]["in_zone"] = True
    obs["mechs"] = [_mech(obs, 3, 80, 0)]  # east: strafing is north (into the wall) or south
    for _ in range(30):
        action, _ = respond(bot, obs)
        if _mode(action) == "strafe":
            assert action["move"][1] < 0, "strafed into the wall north of us"
        obs["t"] += 0.1
        obs["tick"] += 2


def test_zone_hurry_overrides_strafe(bot):
    obs = base_obs(100)
    z = obs["zone"]
    obs["self"]["pos"] = [z["center"][0] + z["radius"] + 40, z["center"][1], 0.0]
    obs["self"]["in_zone"] = False
    obs["mechs"] = [_mech(obs, 5, -60, 0)]
    action, _ = respond(bot, obs)
    x, y = obs["self"]["pos"][:2]
    nx, ny = z["next_center"]
    assert _mode(action) == "zone!"
    assert yaw_diff(_move_yaw(action), math.atan2(ny - y, nx - x)) < math.radians(15)


# --- 3. dodge ----------------------------------------------------------------------------
def _bullet(obs, dx, dy, vx, vy, dz=4.5, kind="gun"):
    x, y, z = obs["self"]["pos"]
    return {"id": 280, "kind": kind, "pos": [x + dx, y + dy, z + dz], "vel": [vx, vy, 0.0]}


def test_dodges_a_bullet_aimed_at_us(bot):
    obs = _sky(base_obs(100))
    obs["projectiles"] = [_bullet(obs, 100, 1.0, -252, 0)]  # from the east, 1 m north of our center line
    action, _ = respond(bot, obs)
    assert _mode(action) == "dodge"
    mx, my = action["move"]
    assert abs(mx) < 1e-9 and my < -0.99, "should step south, away from the side the bullet passes"


def test_no_dodge_for_a_bullet_passing_wide_or_flying_away(bot):
    obs = _sky(base_obs(100))
    obs["projectiles"] = [_bullet(obs, 100, 10.0, -252, 0)]
    assert _mode(respond(bot, obs)[0]) != "dodge"
    obs["projectiles"] = [_bullet(obs, 20, 0.0, 252, 0)]
    assert _mode(respond(bot, obs)[0]) != "dodge"
    obs["projectiles"] = [_bullet(obs, 400, 0.0, -252, 0)]  # 1.6 s away: beyond the horizon
    assert _mode(respond(bot, obs)[0]) != "dodge"


def test_no_dodge_outside_the_zone(bot):
    obs = base_obs(100)
    z = obs["zone"]
    obs["self"]["pos"] = [z["center"][0] + z["radius"] + 40, z["center"][1], 0.0]
    obs["self"]["in_zone"] = False
    obs["projectiles"] = [_bullet(obs, 0, 100, 0, -252)]
    assert _mode(respond(bot, obs)[0]) == "zone!"


# --- 4. laser fetch ----------------------------------------------------------------------
def _laser_at(obs, dx, dy, dz=0.0, pid=8013):
    x, y, z = obs["self"]["pos"]
    return {"id": pid, "kind": "laser", "pos": [x + dx, y + dy, z + dz]}


def _early(obs):
    obs["t"] = 20.0
    return obs


def test_walks_to_a_visible_laser_early(bot):
    obs = _early(base_obs(100))
    obs["pickups"] = [_laser_at(obs, 0, 50)]
    action, _ = respond(bot, obs)
    assert _mode(action) == "fetch"
    assert yaw_diff(_move_yaw(action), math.pi / 2) < 1e-6 and math.hypot(*action["move"]) > 0.99


def test_no_fetch_when_unsafe_late_unreachable_or_already_armed(bot):
    def mode_with(**kw):
        reset(bot)
        obs = _early(base_obs(100))
        obs["pickups"] = [_laser_at(obs, 0, 50, kw.get("dz", 0.0))]
        if "enemy_at" in kw:
            obs["mechs"] = [_mech(obs, 5, kw["enemy_at"], 0)]
        if "t" in kw:
            obs["t"] = kw["t"]
        if "weapons" in kw:
            obs["self"]["weapons"][1] = kw["weapons"]
        return _mode(respond(bot, obs)[0])
    assert mode_with() == "fetch"
    assert mode_with(enemy_at=40) != "fetch"
    assert mode_with(t=bot.early_s + 1) != "fetch"
    assert mode_with(dz=12) != "fetch", "on a roof"
    laser = {"kind": "laser", "cooldown": 0, "charge": 0, "stats": {"range": 280, "charge": 0.75}}
    assert mode_with(weapons=laser) != "fetch"


def test_early_phase_comes_from_the_config():
    ph = HELLO["config"]["zone"]["phases"][0]
    assert new_bot().early_s == ph["hold_s"] + ph["shrink_s"]


def test_swaps_a_mortar_for_a_laser_in_reach(bot):
    obs = base_obs(100)
    obs["self"]["weapons"][1] = {"kind": "mortar", "cooldown": 0, "stats": {"speed": 72, "gravity": 22}}
    obs["pickups"] = [_laser_at(obs, 2, 0)]
    action, _ = respond(bot, obs)
    assert action.get("take") == 8013 and action.get("drop") == 1


# --- 5. circular aim ---------------------------------------------------------------------
def test_circular_lead_hits_a_turning_target(bot):
    obs = _sky(base_obs(100))
    x, y, z = obs["self"]["pos"]
    R, w, v = 30.0, 0.39, 11.7  # a 30 m circle at walk speed (v = w * R)
    ccx, ccy = x + 120, y

    def state(t):
        a = w * t
        return [ccx + R * math.cos(a), ccy + R * math.sin(a), z], [-v * math.sin(a), v * math.cos(a), 0.0]

    o = copy.deepcopy(obs)
    t0 = o["t"]
    for k in range(8):  # observations 0.1 s apart so the bot learns the turn rate
        pos, vel = state(k * 0.1)
        o["t"], o["tick"] = t0 + k * 0.1, obs["tick"] + 2 * k
        o["mechs"] = [{"id": 3, "kind": "mech", "pos": pos, "vel": vel, "aim": [0, 0], "hp": 1400, "max_hp": 1400,
                       "cubes": 0}]
        action, _ = respond(bot, o)
    assert abs(bot.motion[3][2] - w) < 0.02, f"turn rate {bot.motion[3][2]:.3f}, true {w}"
    yaw, pitch = action["aim"]
    org = bot._origin(o["self"])
    d = (math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch))
    t_now = 0.7
    best = min(
        math.dist([org[i] + d[i] * (3 + 252 * (s - B.LATENCY_S)) for i in range(3)],
                  [*state(t_now + s)[0][:2], z + 4.5])
        for s in (B.LATENCY_S + i / 5000 for i in range(5000)))
    assert best < 0.5, f"bullet misses the circling target by {best:.2f} m"


def test_straight_target_has_no_turn_rate(bot):
    obs = _sky(base_obs(100))
    for k in range(5):
        obs["t"] += 0.1
        obs["tick"] += 2
        obs["mechs"] = [_mech(obs, 3, 100, 10 + 1.17 * k, vel=(0, 11.7, 0))]
        respond(bot, obs)
    assert abs(bot.motion[3][2]) < 1e-9


# --- zone first (from v4) ----------------------------------------------------------------
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


def test_fighting_never_overrides_the_zone(bot):
    obs = base_obs(100)
    z = obs["zone"]
    obs["self"]["pos"] = [z["center"][0] + z["radius"] + 40, z["center"][1], 0.0]
    obs["self"]["in_zone"] = False
    obs["mechs"] = [_mech(obs, 5, -60, 0, hp=100)]  # a nearly dead enemy between us and the zone
    obs["projectiles"] = [_bullet(obs, 0, 100, 0, -252)]
    obs["pickups"] = [_laser_at(obs, 0, 50)]
    action, _ = respond(bot, obs)
    x, y = obs["self"]["pos"][:2]
    nx, ny = z["next_center"]
    assert yaw_diff(_move_yaw(action), math.atan2(ny - y, nx - x)) < math.radians(15)
    assert math.hypot(*action["move"]) > 0.95 and action.get("boost") is True
