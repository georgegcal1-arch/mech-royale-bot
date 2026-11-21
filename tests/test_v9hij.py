"""v9 experiments H (features.LASER_GUARD), I (features.NO_CHIP) and J (features.AMBUSH). All three are off by
default; the golden replays force every V9 switch off, and test_v9's whole-match / all-on / Hypothesis tests
iterate V9_SWITCHES, so they cover H, I and J too."""
import math

import pytest

import ambush as A
import nochip as N
from conftest import DECISION_BUDGET_MS, OBS, base_obs, new_bot, reset, respond
from test_v9 import _label, _mech, _mode, _run_match, _sky, switches
from test_v9fg import ARC, GUN, LASER, _arm

BEST = ("DUEL", "LIGHTNING")  # the setup the queue stacks on (LOOKAHEAD joins if G wins)


class Box:
    """Flat ground at z0 with one tall box [x0, x1] x [y0, y1]: it blocks every line crossing it."""

    def __init__(self, z0, x0, y0, x1, y1):
        self.z0, self.box = z0, (x0, y0, x1, y1)

    def ground(self, x, y):
        return self.z0

    def under_canopy(self, x, y, z):
        return False

    def solid_at(self, x, y, z):
        x0, y0, x1, y1 = self.box
        return x0 <= x <= x1 and y0 <= y <= y1

    def blocked(self, a, b):
        """Liang-Barsky: does the segment a -> b (in plan) touch the box?"""
        x0, y0, x1, y1 = self.box
        dx, dy = b[0] - a[0], b[1] - a[1]
        lo, hi = 0.0, 1.0
        for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
            if abs(p) < 1e-12:
                if q < 0:
                    return False
                continue
            r = q / p
            if p < 0:
                lo = max(lo, r)
            else:
                hi = min(hi, r)
            if lo > hi:
                return False
        return True


def _charger(obs, mid, dx, dy, **kw):
    m = _mech(obs, mid, dx, dy, weapons=("gun", "laser"), **kw)
    m["charging"] = True
    return m


def _respond(bot, obs, *on, n=1):
    with switches(*on):
        for _ in range(n):
            action, _ = respond(bot, obs)
            obs["self"]["aim"] = list(action["aim"])
    return action


# --- H: laser guard -------------------------------------------------------------------------------
def test_guard_boosts_across_the_line_with_nowhere_to_hide(bot):
    obs = _arm(_sky(base_obs(100)), GUN, None)
    obs["mechs"] = [_charger(obs, 3, 0, -120)]  # south of us, aimed at us, in the open sky
    action = _respond(bot, obs, *BEST, "LASER_GUARD")
    assert _mode(action) == "guard" and action["boost"] is True, _label(action)
    assert abs(action["move"][1]) < 0.2, f"sideways across its north-pointing line: {action['move']}"


def test_guard_ignores_a_laser_charging_elsewhere_or_behind_cover(bot):
    obs = _arm(_sky(base_obs(100)), GUN, None)
    obs["mechs"] = [_charger(obs, 3, 0, -120, aim=0.0)]  # charging, but aimed east
    assert _mode(_respond(bot, obs, *BEST, "LASER_GUARD")) != "guard"
    reset(bot)
    obs = _arm(base_obs(100), GUN, None)
    x, y, z = obs["self"]["pos"]
    bot.world = Box(z, x - 20, y - 40, x + 20, y - 30)  # between it and us
    obs["mechs"] = [_charger(obs, 3, 0, -120)]
    assert bot.laser_chargers(obs, obs["mechs"]) == []
    assert _mode(_respond(bot, obs, *BEST, "LASER_GUARD")) != "guard"


def test_guard_takes_the_nearest_spot_it_cannot_see(bot):
    obs = _arm(base_obs(100), GUN, None)
    x, y, z = obs["self"]["pos"]
    bot.world = Box(z, x + 4, y - 3, x + 10, y + 3)  # a pillar just east of us; the charger is south-east
    m = _charger(obs, 3, 100, -100)
    obs["mechs"] = [m]
    action = _respond(bot, obs, *BEST, "LASER_GUARD")
    assert _mode(action) == "guard" and action["boost"] is True, _label(action)
    gx, gy = bot.guard[2]
    eye = (m["pos"][0], m["pos"][1], z + 7.0)
    assert bot.world.blocked(eye, (gx, gy, z + 4.5)), "the spot is out of its line"
    assert not bot.world.blocked((x, y, z), (gx, gy, z)), "and reachable in a straight line"
    assert math.hypot(gx - x, gy - y) <= 19.5 * 0.75, "and within what a boost covers in 0.75 s"


def test_guard_shorts_a_close_charger_with_lightning(bot):
    obs = _arm(_sky(base_obs(100)), LASER, ARC)
    obs["self"]["aim"] = [-math.pi / 2, 0.0]
    obs["mechs"] = [_charger(obs, 3, 0, -30)]
    action = _respond(bot, obs, *BEST, "LASER_GUARD", n=2)
    assert " short 3" in _label(action), _label(action)
    assert action["fire"] == [False, True], "lightning fires; no laser charge of our own"


def test_guard_without_energy_takes_cover_instead(bot):
    obs = _arm(_sky(base_obs(100)), LASER, ARC)
    obs["self"]["energy"] = 1
    obs["mechs"] = [_charger(obs, 3, 0, -30)]
    assert _mode(_respond(bot, obs, *BEST, "LASER_GUARD")) == "guard"


def test_guard_hears_a_charge_it_cannot_see(bot):
    obs = _arm(_sky(base_obs(100)), GUN, None)
    obs["sounds"] = [{"kind": "laser_charge", "bearing": -math.pi / 2, "dist": "mid", "age": 0.1}]
    assert len(bot.laser_chargers(obs, [])) == 1
    assert _mode(_respond(bot, obs, *BEST, "LASER_GUARD")) == "guard"
    reset(bot)
    obs["sounds"][0]["age"] = 2.0  # old news
    assert bot.laser_chargers(obs, []) == []


def test_guard_triggers_in_the_recorded_match():
    """Mech 10 charges its laser at the recorded mech four times (tools/damage_ledger.py: 4 laser hits)."""
    with switches(*BEST, "LASER_GUARD"):
        labels = _run_match(new_bot())
    assert sum(lab.startswith("guard") for lab in labels) >= 10


# --- I: no chip -----------------------------------------------------------------------------------
@pytest.mark.parametrize("dist, hp, fires", [(150.0, 1400, False), (150.0, 300, True), (80.0, 1400, True)])
def test_no_chip_gun_holds_at_range(bot, dist, hp, fires):
    obs = _arm(_sky(base_obs(100)), GUN, None)
    obs["self"]["aim"] = [0.0, 0.0]
    obs["mechs"] = [_mech(obs, 3, dist, 0, hp=hp)]
    action = _respond(bot, obs, *BEST, "NO_CHIP", n=3)
    assert action["fire"][0] is fires, _label(action)
    reset(bot)
    if not fires:  # NO_CHIP off: v6 fires
        assert _respond(bot, obs, *BEST, n=3)["fire"][0] is True


def test_no_chip_approaches_a_far_target(bot):
    obs = _arm(base_obs(100), GUN, None)
    x, y, z = obs["self"]["pos"]
    bot.world = Box(z, x + 500, y + 500, x + 501, y + 501)  # flat, open ground
    obs["mechs"] = [_mech(obs, 3, 150, 0)]
    action = _respond(bot, obs, *BEST, "NO_CHIP")
    assert _mode(action) == "approach", _label(action)
    assert action["move"][0] > 0.4, f"toward it: {action['move']}"
    assert not action["boost"]


def test_no_chip_leaves_the_laser_its_range(bot):
    obs = _arm(_sky(base_obs(100)), GUN, LASER)
    obs["mechs"] = [_mech(obs, 3, 150, 0)]
    assert _mode(_respond(bot, obs, *BEST, "NO_CHIP")) == "duel", "laser without lightning: the duel's 140 m"


def test_no_chip_approach_prefers_cover(bot):
    obs = _arm(base_obs(100), GUN, None)
    x, y, z = obs["self"]["pos"]
    m = _mech(obs, 3, 150, 0)
    # a box north-east of us hides the spots behind it from the target (east): the fan's left side
    bot.world = Box(z, x + 30, y + 6, x + 34, y + 40)
    goal = bot.approach_goal(obs, m)
    assert goal is not None
    assert bot.world.blocked((m["pos"][0], m["pos"][1], z + 7), (goal[0], goal[1], z + 6.3)), goal


def test_no_chip_recorded_match_fires_the_gun_only_close_or_at_a_weak_target():
    bot = new_bot()
    with switches(*BEST, "NO_CHIP"):
        for obs in OBS:
            action, _ = respond(bot, obs)
            gun = next((i for i, w in enumerate(obs["self"]["weapons"]) if w and w["kind"] == "gun"), None)
            if gun is not None and action["fire"][gun]:
                me = obs["self"]["pos"]
                near = min(math.dist(m["pos"], me) for m in obs["mechs"] if m["kind"] == "mech")
                weak = min(m["hp"] / m["max_hp"] for m in obs["mechs"] if m["kind"] == "mech")
                assert near <= N.NO_CHIP_M + 10 or weak < N.NO_CHIP_FINISH, (obs["t"], near)
    assert bot.fallbacks == 0


# --- J: ambush ------------------------------------------------------------------------------------
def _hunt_scene(bot, dist=120.0, closing=8.0):
    """We hold laser + lightning; one hunter south of us walks north at us; a box 15 m east of us."""
    obs = _arm(base_obs(100), LASER, ARC)
    x, y, z = obs["self"]["pos"]
    bot.world = Box(z, x + 12, y - 6, x + 18, y + 6)
    hunter = _mech(obs, 3, 30, -dist, vel=(0.0, closing, 0.0), aim=math.pi / 2)
    obs["mechs"] = [hunter]
    return obs


def test_ambush_waits_hidden_and_silent(bot):
    obs = _hunt_scene(bot)
    action = _respond(bot, obs, *BEST, "AMBUSH")
    assert _mode(action) == "ambush", _label(action)
    assert action["fire"] == [False, False] and not action["boost"]
    sx, sy = bot.ambush["spot"]
    h = obs["mechs"][0]["pos"]
    assert bot.world.blocked((h[0], h[1], h[2] + 8), (sx, sy, obs["self"]["pos"][2] + 4.5)), "hidden from it"
    # at the spot: stand still
    obs["self"]["pos"][0], obs["self"]["pos"][1] = sx, sy
    action = _respond(bot, obs, *BEST, "AMBUSH")
    assert _mode(action) == "ambush" and action["move"] == [0.0, 0.0]


def test_ambush_springs_with_lightning_inside_43_m(bot):
    obs = _hunt_scene(bot)
    _respond(bot, obs, *BEST, "AMBUSH")
    assert bot.ambush is not None
    x, y, z = obs["self"]["pos"]
    obs["mechs"] = [_mech(obs, 3, -25, 0, vel=(4.0, 0.0, 0.0))]  # it came round, west of us, in the open
    action = _respond(bot, obs, *BEST, "AMBUSH", n=3)
    assert bot.ambush is None and _mode(action) != "ambush", _label(action)
    assert action["fire"][1] is True, "lightning"


@pytest.mark.parametrize("change", ["damage", "second", "no_lightning"])
def test_ambush_aborts(bot, change):
    obs = _hunt_scene(bot)
    _respond(bot, obs, *BEST, "AMBUSH")
    assert bot.ambush is not None
    if change == "damage":
        obs["events"] = [{"type": "damaged", "amount": 150, "weapon": "laser", "bearing": 0.0, "by": 3}]
        obs["t"] += 0.1
    elif change == "second":
        obs["mechs"].append(_mech(obs, 4, -100, 60))
    else:
        obs["self"]["weapons"] = [dict(LASER), dict(GUN)]
    action = _respond(bot, obs, *BEST, "AMBUSH")
    assert bot.ambush is None and _mode(action) != "ambush", _label(action)
    assert bot.ambush_cool > obs["t"], "no new ambush right away"


def test_no_ambush_without_cover_a_hunter_or_lightning(bot):
    obs = _arm(_sky(base_obs(100)), LASER, ARC)  # the open sky: nowhere to hide
    obs["mechs"] = [_mech(obs, 3, 0, -120, vel=(0.0, 8.0, 0.0))]
    assert _mode(_respond(bot, obs, *BEST, "AMBUSH")) != "ambush"
    reset(bot)
    obs = _hunt_scene(bot, closing=-8.0)
    obs["mechs"][0]["aim"] = [0.0, 0.0]  # walking away, looking elsewhere: not hunting
    assert _mode(_respond(bot, obs, *BEST, "AMBUSH")) != "ambush"
    reset(bot)
    obs = _hunt_scene(bot)
    obs["self"]["weapons"] = [dict(GUN), dict(LASER)]
    assert _mode(_respond(bot, obs, *BEST, "AMBUSH")) != "ambush"


def test_ambush_gives_up_on_a_lost_hunter(bot):
    obs = _hunt_scene(bot)
    _respond(bot, obs, *BEST, "AMBUSH")
    obs["mechs"] = []
    obs["t"] += A.AMBUSH_LOST_S + 0.5
    _respond(bot, obs, *BEST, "AMBUSH")
    assert bot.ambush is None


# --- budget on the real map -----------------------------------------------------------------------
@pytest.mark.parametrize("i", [50, 150, 290])
def test_hij_planning_stays_in_budget_on_the_real_map(i):
    """Worst case, all on: two lasers charging at us (re-planned every decision) and a hunter closing."""
    bot = new_bot()
    obs = _arm(base_obs(i), LASER, ARC)
    obs["mechs"] = [_charger(obs, 3, 120, 40), _charger(obs, 4, -90, -100)]
    worst = 0.0
    with switches(*BEST, "LOOKAHEAD", "LASER_GUARD", "NO_CHIP", "AMBUSH"):
        for _ in range(5):
            bot.guard = None
            _, dt = respond(bot, obs)
            worst = max(worst, dt)
        reset(bot)
        obs["mechs"] = [_mech(obs, 3, 0, -150, vel=(0.0, 8.0, 0.0))]
        for _ in range(5):
            bot.ambush, bot.ambush_cool = None, 0.0
            _, dt = respond(bot, obs)
            worst = max(worst, dt)
    assert bot.fallbacks == 0
    assert worst * 1000 < DECISION_BUDGET_MS, f"{worst * 1000:.2f} ms"
