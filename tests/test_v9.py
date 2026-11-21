"""v9 experiments (features.DUEL, LEAD_AIM, LEARN_DODGE, SQUAD_AVOID): each one's behaviour in built
scenarios, the whole recorded match with each on (and all on), the decision time with all on, and v6
exactly with all off (the golden replays in test_features / test_heal / test_economy force them off)."""
import contextlib
import math

import pytest
from hypothesis import given

import features
from conftest import DECISION_BUDGET_MS, OBS, action_problems, base_obs, new_bot, reset, respond
from learn_dodge import DIRECT_STRAFE_S, LEADER_STRAFE_S
from strategies import game_observations

SKY = 300.0
ALL = features.V9_SWITCHES


@contextlib.contextmanager
def switches(*on):
    """Exactly these v9 switches on (the rest off), restored afterwards."""
    was = {s: getattr(features, s) for s in ALL}
    try:
        for s in ALL:
            setattr(features, s, s in on)
        yield
    finally:
        for s, v in was.items():
            setattr(features, s, v)


def _label(action):
    return (action.get("debug") or {}).get("label") or ""


def _mode(action):
    return _label(action).split(" ")[0]


def _sky(obs):
    obs["self"]["pos"][2] += SKY
    return obs


def _mech(obs, mid, dx, dy, hp=1400, vel=(0.0, 0.0, 0.0), aim=None, weapons=("gun",)):
    x, y, z = obs["self"]["pos"]
    if aim is None:  # aimed at us
        aim = math.atan2(-dy, -dx)
    return {"id": mid, "kind": "mech", "pos": [x + dx, y + dy, z], "vel": list(vel), "aim": [aim, 0.0],
            "hp": hp, "max_hp": 1400, "cubes": 0, "weapons": list(weapons), "charging": False}


def _run_match(bot):
    labels = []
    for obs in OBS:
        action, _ = respond(bot, obs)
        assert action_problems(action, obs) == [], f"tick {obs['tick']}"
        labels.append(_label(action))
    assert bot.fallbacks == 0
    return labels


# --- all of them -------------------------------------------------------------------------------
@pytest.mark.parametrize("on", [(s,) for s in ALL] + [ALL], ids=list(ALL) + ["ALL"])
def test_whole_match_valid_and_fast_with_switch_on(on):
    with switches(*on):
        bot = new_bot()
        _run_match(bot)
    ts = sorted(bot.times)
    assert ts[int(0.99 * len(ts))] * 1000 < DECISION_BUDGET_MS, f"p99 {ts[int(0.99 * len(ts))] * 1000:.2f} ms"


PROP_BOT = new_bot()


@given(game_observations)
def test_all_on_never_falls_back_on_any_observation(obs):
    with switches(*ALL):
        reset(PROP_BOT)
        action, dt = respond(PROP_BOT, obs)
    assert action_problems(action, obs) == []
    assert PROP_BOT.fallbacks == 0, "a v9 module raised"
    assert dt * 1000 < DECISION_BUDGET_MS


# --- DUEL --------------------------------------------------------------------------------------
def test_duel_replaces_the_strafe_in_the_recorded_1v1():
    with switches("DUEL"):
        labels = _run_match(new_bot())
    assert any(lab.startswith("duel ") for lab in labels)
    assert not any(lab.startswith("strafe ") for lab in labels)


@pytest.mark.parametrize("enemy_aim, north", [(math.pi / 2, False), (-math.pi / 2, True)])
def test_duel_circles_toward_the_enemy_rear(bot, enemy_aim, north):
    """Enemy 60 m east of us (our preferred gun range: no radial pull), its torso busy north (or south):
    we circle the way that takes us behind it (south of it, or north)."""
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, 60, 0, aim=enemy_aim)]
    with switches("DUEL"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "duel"
    my = action["move"][1]
    assert (my > 0.9) if north else (my < -0.9), action["move"]


@pytest.mark.parametrize("dist, closer", [(200.0, True), (20.0, False)])
def test_duel_holds_its_range(bot, dist, closer):
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, dist, 0)]  # facing us
    with switches("DUEL"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "duel"
    mx = action["move"][0]  # + = toward it
    assert (mx > 0.3) if closer else (mx < -0.3), action["move"]
    assert abs(math.hypot(*action["move"]) - 1) < 1e-9


def test_duel_fires_a_held_lightning_gun(bot):
    obs = _sky(base_obs(100))
    me = obs["self"]
    me["aim"] = [0.0, 0.0]
    me["energy"] = 100
    me["weapons"][1] = {"kind": "lightning", "cooldown": 0,
                        "stats": {"range": 45, "cone_deg": 30, "energy": 3, "cooldown": 0.25}}
    obs["mechs"] = [_mech(obs, 3, 20, 0)]
    with switches():
        action, _ = respond(bot, obs)
    assert action["fire"][1] is False, "v6 never fires lightning"
    reset(bot)
    with switches("DUEL"):
        action, _ = respond(bot, obs)
    assert action["fire"][1] is True


def test_duel_keeps_the_mortar_off_a_fast_target(bot):
    obs = _sky(base_obs(100))
    me = obs["self"]
    me["weapons"][1] = {"kind": "mortar", "cooldown": 0, "stats": {"speed": 72.0, "gravity": 22.0}}
    fast = _mech(obs, 3, 100, 0, vel=(0.0, 11.7, 0.0))
    obs["mechs"] = [fast]
    bot.note_motion(obs)
    aim_v6, _ = bot.engage(obs, fast, False)
    aim_duel, fire = bot.duel_engage(obs, fast, False)
    assert aim_v6[1] > 0.1, "v6: the mortar takes the aim (a lob)"
    assert aim_duel[1] < 0.05 and fire[1] is False, "duel: the gun keeps the aim, no lob at a strafing target"
    slow = _mech(obs, 3, 100, 0)
    assert bot.duel_engage(obs, slow, False)[0][1] > 0.1, "a standing target still gets the mortar"


def test_no_duel_with_two_enemies(bot):
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, 60, 0), _mech(obs, 4, -150, 0)]
    with switches("DUEL"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "strafe"


# --- LEAD_AIM ----------------------------------------------------------------------------------
def _track(bot, vel_at, steps=80, dt=0.1):
    """Feed a mech moving by vel_at(t) through note_tracks; return its chosen model."""
    x = y = 0.0
    for i in range(steps):
        t = i * dt
        vx, vy = vel_at(t)
        bot.note_tracks({"t": t, "mechs": [{"id": 7, "kind": "mech", "pos": [x, y, 0.0], "vel": [vx, vy, 0.0]}]})
        x, y = x + vx * dt, y + vy * dt
    return bot.lead_state[7][0]


def test_lead_picks_mean_velocity_for_a_zig_zag(bot):
    zig = lambda t: (3.0, 11.0 if int(t / 0.5) % 2 == 0 else -11.0)
    assert _track(bot, zig) == "mean"


def test_lead_picks_straight_for_a_straight_walk(bot):
    assert _track(bot, lambda t: (8.0, 8.0)) in ("straight", "circular")


def test_lead_picks_the_arc_for_a_turning_walk(bot):
    assert _track(bot, lambda t: (11.7 * math.cos(1.0 * t), 11.7 * math.sin(1.0 * t))) == "circular"


def test_lead_aim_moves_the_prediction(bot):
    zig = lambda t: (3.0, 11.0 if int(t / 0.5) % 2 == 0 else -11.0)
    _track(bot, zig)
    m = {"id": 7, "kind": "mech", "pos": [0.0, 0.0, 0.0], "vel": [3.0, 11.0, 0.0]}
    with switches("LEAD_AIM"):
        q = bot.predict(m, 0.5, 0.0)
    with switches():
        q6 = bot.predict(m, 0.5, 0.0)
    assert abs(q6[1] - 5.5) < 0.5, "v6 leads along its velocity"
    assert abs(q[1]) < 2.0, "the mean velocity of a zig-zag is ~0 sideways"


def test_lead_pool_learns_in_the_recorded_match():
    with switches("LEAD_AIM"):
        bot = new_bot()
        _run_match(bot)
    assert bot.lead_pool[1] > 50


# --- LEARN_DODGE -------------------------------------------------------------------------------
def _bullets(bot, k, n=5):
    """We walk north at 11.7 m/s; a shooter 150 m east fires n bullets leading us by fraction k."""
    obs = _sky(base_obs(100))
    me = obs["self"]
    me["vel"] = [0.0, 11.7, 0.0]
    x, y, z = me["pos"]
    shooter = _mech(obs, 5, 150, 0)
    mx, my, mz = shooter["pos"][0], shooter["pos"][1], shooter["pos"][2] + 7.0
    s = 0.1  # the bullets left the muzzle 0.1 s ago
    px, py = x, y - 11.7 * s  # us when it fired
    tf = math.hypot(px - mx, py - my) / 252.0
    ax, ay, az = px, py + 11.7 * tf * k, z + 4.5
    d = math.dist((ax, ay, az), (mx, my, mz))
    v = [(ax - mx) / d * 252.0, (ay - my) / d * 252.0, (az - mz) / d * 252.0]
    obs["mechs"] = [shooter]
    for i in range(n):
        obs["projectiles"] = [{"id": 900 + i, "kind": "gun", "pos": [mx + v[0] * s, my + v[1] * s, mz + v[2] * s],
                               "vel": v}]
        bot.note_bullets(obs)
    return obs


@pytest.mark.parametrize("k", [0.0, 1.0, 0.6])
def test_learns_the_lead_fraction(bot, k):
    _bullets(bot, k)
    assert bot.lead_k() == pytest.approx(k, abs=0.05)
    assert bot.lead_k(5) == pytest.approx(k, abs=0.05), "its own samples too"


def test_unknown_lead_keeps_v5_strafe_and_dodge(bot):
    assert bot.lead_k() is None
    assert bot.strafe_window((1.0, 2.0)) == (1.0, 2.0)
    assert bot.dodge_side({"id": 1}, 0.0, 1.0, [0.0, 11.7, 0.0], -1) == -1


@pytest.mark.parametrize("k, window, side", [(1.0, LEADER_STRAFE_S, -1), (0.0, DIRECT_STRAFE_S, 1)])
def test_dodge_follows_the_lead(bot, k, window, side):
    """Leaders: short strafe legs, dodge against our motion; direct aimers: long legs, dodge with it."""
    _bullets(bot, k)
    assert bot.strafe_window((1.0, 2.0)) == window
    # a bullet flying west; across its path (0, 1) we move north (+)
    assert bot.dodge_side({"id": 900}, 0.0, 1.0, [0.0, 11.7, 0.0], -side) == side


def test_learned_strafe_legs_in_the_scene(bot):
    _bullets(bot, 1.0)
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, 80, 0)]
    sides = []
    with switches("LEARN_DODGE"):
        for _ in range(40):
            action, _ = respond(bot, obs)
            sides.append(1 if action["move"][1] > 0 else -1)
            obs["t"] += 0.1
            obs["tick"] += 2
    flips = sum(1 for a, b in zip(sides, sides[1:]) if a != b)
    assert flips >= 5, f"leaders: reverse every 0.35-0.7 s, got {flips} flips in 4 s"


def test_learns_the_house_bot_lead_in_the_recorded_match():
    with switches("LEARN_DODGE"):
        bot = new_bot()
        _run_match(bot)
    assert bot.k_pool[1] >= 3 and bot.lead_k() is not None


def test_bullet_not_from_a_visible_mech_is_ignored(bot):
    obs = _bullets(bot, 1.0, n=0)
    obs["projectiles"] = [{"id": 1, "kind": "gun", "pos": [0.0, 0.0, 500.0], "vel": [252.0, 0.0, 0.0]}]
    bot.note_bullets(obs)
    assert bot.k_pool[1] == 0


# --- SQUAD_AVOID -------------------------------------------------------------------------------
class Wall:
    """Flat open ground at z0 with an invisible wall along y = y_wall: lines across it are blocked."""

    def __init__(self, z0, y_wall):
        self.z0, self.y_wall = z0, y_wall

    def ground(self, x, y):
        return self.z0

    def solid_at(self, x, y, z):
        return False

    def under_canopy(self, x, y, z):
        return False

    def blocked(self, a, b):
        return (a[1] < self.y_wall) != (b[1] < self.y_wall)


def _squad_scene(bot):
    obs = base_obs(100)
    x, y, z = obs["self"]["pos"]
    bot.world = Wall(z, y + 8.0)  # a wall 8 m north of us; both enemies are south
    obs["mechs"] = [_mech(obs, 3, 60, -100), _mech(obs, 4, -60, -100)]
    return obs, y


def test_engagers_are_aimed_or_hitting(bot):
    obs = _sky(base_obs(100))
    a, b, c = _mech(obs, 3, 100, 0), _mech(obs, 4, -100, 0, aim=math.pi), _mech(obs, 5, 0, 300)
    assert [m["id"] for m in bot.engagers(obs, [a, b, c])] == [3], "4 aims away, 5 is out of gun range"
    bot.hits = [(obs["t"], 4, 45, 0.0)]
    assert [m["id"] for m in bot.engagers(obs, [a, b, c])] == [3, 4]


def test_squad_walks_behind_the_wall(bot):
    obs, y = _squad_scene(bot)
    with switches("SQUAD_AVOID"):
        action, _ = respond(bot, obs)
    assert _mode(action) == "squad" and action.get("boost") is True
    goal = bot.squad[1]
    assert goal[1] > y + 8.0, "the spot is behind the wall, out of both lines"
    assert action["move"][1] > 0.5


def test_squad_is_off_with_one_engager_or_when_already_covered(bot):
    obs, y = _squad_scene(bot)
    obs["mechs"][1]["aim"] = [0.0, 0.0]  # 4 looks away
    with switches("SQUAD_AVOID"):
        action, _ = respond(bot, obs)
    assert _mode(action) != "squad"
    reset(bot)
    obs, y = _squad_scene(bot)
    bot.world.y_wall = y - 8.0  # we are already behind it
    with switches("SQUAD_AVOID"):
        action, _ = respond(bot, obs)
    assert _mode(action) != "squad"


def test_squad_targets_the_most_isolated(bot):
    obs = _sky(base_obs(100))
    obs["mechs"] = [_mech(obs, 3, 100, 0, hp=300), _mech(obs, 4, 100, 10), _mech(obs, 5, -100, 50)]
    with switches("SQUAD_AVOID"):
        action, _ = respond(bot, obs)
    assert "isolated 5" in _label(action), _label(action)
    reset(bot)
    with switches():
        action, _ = respond(bot, obs)
    assert "weakest 3" in _label(action), "v6: the lowest HP"


@pytest.mark.parametrize("i, dist", [(50, 60.0), (50, 180.0), (200, 180.0), (290, 60.0)])
def test_squad_planning_stays_in_budget_on_the_real_map(i, dist):
    """Four engagers around us on the real map (all on): the worst decision is the re-plan."""
    bot = new_bot()
    obs = base_obs(i)
    x, y, z = obs["self"]["pos"]
    obs["mechs"] = [_mech(obs, 3 + k, dist * math.cos(k * math.pi / 2 + 0.3), dist * math.sin(k * math.pi / 2 + 0.3),
                          weapons=("gun", "laser")) for k in range(4)]
    worst = 0.0
    with switches(*ALL):
        for _ in range(5):
            bot.squad = None
            _, dt = respond(bot, obs)
            worst = max(worst, dt)
    assert bot.fallbacks == 0
    assert worst * 1000 < DECISION_BUDGET_MS, f"{worst * 1000:.2f} ms"
