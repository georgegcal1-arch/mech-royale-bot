"""v9 STUCK_ESCAPE on the real map, at the three spots where arena matches MNUCG and AGTRR got stuck: the bot must
get past within 3 s (tests/navsim.py moves it); without the switch it stays stuck (the control)."""
import copy
import math

import pytest

import stuck as S
from conftest import HELLO, base_obs, new_bot, respond
from navsim import NavSim
from test_v9 import _label, _mode, switches

MECH = HELLO["config"]["mech"]
# (name, position, next circle center, its radius, edge margin (None = a plain walk, far inside), seconds allowed)
# The corner at (-28, 51): the move points into building 100 (top 24.8 m) behind a sliver of building 97, a
# 27 m climb. At the jet's ~9-10 m/s rise (protocol.md section 7: max_rise_speed 10) that alone takes ~3 s, so
# that spot gets 4 s; the other two must be past within the 3 s asked for.
SPOTS = [
    ("corner of buildings 97/100 (MNUCG t=118)", (-28.0, 51.0), (92.0, -1.0), 140.0, None, 4.0),
    ("building 97 north (MNUCG t=49)", (-35.0, 79.0), (92.0, -1.0), 140.0, None, 3.0),
    ("stadium wall (AGTRR t=90, zone hurry)", (-183.0, 104.0), (34.0, 64.0), 240.0, 20.0, 3.0),
]
ON = ("STUCK_ESCAPE",)


def _obs(bot, xy, nc, nr, edge_in):
    """A zone walk toward the next circle from xy (feet on the ground there), nothing else around."""
    obs = base_obs(100)
    x, y = xy
    z = bot.world.ground(x, y)
    me = obs["self"]
    me.update(pos=[x, y, z], vel=[0.0, 0.0, 0.0], energy=100.0, grounded=True, in_zone=True, hp=me["max_hp"])
    d = math.hypot(x - nc[0], y - nc[1])
    r = d + edge_in if edge_in is not None else d + 150.0  # zone hurry (edge within the margin) or a plain walk
    t = obs["t"]
    obs["zone"].update(center=list(nc), radius=r, next_center=list(nc), next_radius=nr,
                       shrink_start=t + 5.0, shrink_end=t + 15.0)
    return obs


def _over_wall(bot, sim, wall_top):
    """Feet at or above the wall's top, over a prism that tall (on, or just above, the roof)."""
    return sim.z >= wall_top - 0.2 and any(top >= wall_top - 0.5 for _, top in bot.world.tops_at(sim.x, sim.y))


def _drive(xy, nc, nr, edge_in, on, seconds=3.0, energy=100.0):
    """(got past, first label, the wall's top, trace) after `seconds` of decisions. Past = 5 m of progress
    toward the goal, or over the wall (feet above its top, above its footprint)."""
    bot = new_bot()
    obs = _obs(bot, xy, nc, nr, edge_in)
    obs["self"]["energy"] = energy
    sim = NavSim(bot.world, MECH, obs["self"]["pos"], energy=energy)
    x0, y0, _ = obs["self"]["pos"]
    a = math.atan2(nc[1] - y0, nc[0] - x0)
    wall = bot._wall_ahead(x0, y0, obs["self"]["pos"][2], 9.0, a) or bot._wall_ahead(x0, y0, obs["self"]["pos"][2], 9.0, a + 0.4) \
        or bot._wall_ahead(x0, y0, obs["self"]["pos"][2], 9.0, a - 0.4)
    trace, first = [], None
    with switches("DUEL", "LIGHTNING", "LASER_GUARD", "NO_CHIP", "NPC_AWARE", *(ON if on else ())):
        for k in range(int(round(seconds / 0.1))):
            sim.into(obs["self"])
            action, _ = respond(bot, obs)
            first = first or _label(action)
            trace.append((round(obs["t"], 1), _mode(action), [round(v, 1) for v in obs["self"]["pos"]], round(sim.energy)))
            sim.step(action)
            obs["tick"] += 2
            obs["t"] = round(obs["t"] + 0.1, 3)
            progress = (sim.x - x0) * math.cos(a) + (sim.y - y0) * math.sin(a)
            if progress >= 5.0 or (wall is not None and _over_wall(bot, sim, wall[1])):
                return True, first, wall, trace
    return False, first, wall, trace


@pytest.mark.parametrize("name, xy, nc, nr, edge_in, limit", SPOTS, ids=[s[0] for s in SPOTS])
def test_gets_past_the_real_stuck_spot_in_time(name, xy, nc, nr, edge_in, limit):
    ok, first, wall, trace = _drive(xy, nc, nr, edge_in, on=True, seconds=limit)
    assert wall is not None, f"{name}: no wall ahead (the spot moved?)"
    assert first.split(" ")[0] in ("walk", "sneak", "zone!", "hold"), first
    assert ok, f"{name}: still stuck after {limit} s: {trace[-6:]}"
    assert any(m.startswith("unstick/") for _, m, _, _ in trace), "it was the escape that did it"


@pytest.mark.parametrize("name, xy, nc, nr, edge_in, limit", SPOTS, ids=[s[0] for s in SPOTS])
def test_without_the_switch_it_stays_stuck(name, xy, nc, nr, edge_in, limit):
    ok, _, _, trace = _drive(xy, nc, nr, edge_in, on=False, seconds=limit)
    assert not ok, f"{name}: the control got past on its own: {trace[-3:]}"


def test_no_escape_while_moving_or_standing_or_fighting():
    bot = new_bot()
    obs = _obs(bot, (-28.0, 51.0), (92.0, -1.0), 140.0, None)
    for k in range(10):  # walking freely: the position changes
        obs["self"]["pos"][0] -= 1.2
        obs["t"] += 0.1
        assert bot.stuck_step(obs, [-1.0, 0.0], "walk") is None
    for k in range(10):  # standing (no move asked)
        obs["t"] += 0.1
        assert bot.stuck_step(obs, [0.0, 0.0], "hold") is None
    for k in range(10):  # pressed, but circling a mech: the duel's business
        obs["t"] += 0.1
        assert bot.stuck_step(obs, [1.0, 0.0], "duel") is None
    for k in range(4):  # pressed while walking: not yet (0.5 s)
        obs["t"] += 0.1
        assert bot.stuck_step(obs, [1.0, 0.0], "walk") is None


def test_low_energy_slides_along_the_least_turned_open_way():
    bot = new_bot()
    obs = _obs(bot, (-28.0, 51.0), (92.0, -1.0), 140.0, None)
    obs["self"]["energy"] = 10.0  # not enough to climb 20 m
    a = math.atan2(-1.0 - 51.0, 92.0 + 28.0)
    out = None
    for k in range(8):
        obs["t"] += 0.1
        out = bot.stuck_step(obs, [math.cos(a), math.sin(a)], "walk") or out
    assert out is not None and out[3] == "unstick/slide" and bot.escape["kind"] == "slide"
    x, y, z = obs["self"]["pos"]
    b = bot.escape["b"]
    dev = abs((b - a + math.pi) % (2 * math.pi) - math.pi)
    assert bot._lane_free(x, y, z, 9.0, b), "the slide goes where it's open"
    for d in S.STUCK_SLIDE_DEVS:
        if d < dev - 1e-6:
            assert not any(bot._lane_free(x, y, z, 9.0, a + s * d) for s in (1, -1)), "a smaller turn was open"


def test_low_energy_slide_travels_along_the_wall():
    """At (-35, 79) building 97 is 104 m long, so without the energy to jet there is no quick way round; the
    slide must at least travel along it (the route of navigation step 2 then takes over), not press in place."""
    _, _, _, trace = _drive((-35.0, 79.0), (92.0, -1.0), 140.0, None, on=True, seconds=2.5, energy=10.0)
    slides = [r for r in trace if r[1] == "unstick/slide"]
    assert slides, "it slid"
    (x0, y0, _), (x1, y1, _) = slides[0][2], slides[-1][2]
    assert math.hypot(x1 - x0, y1 - y0) >= 4.0, trace[-4:]


def test_a_repeat_at_the_same_spot_tries_the_other_way():
    bot = new_bot()
    obs = _obs(bot, (-28.0, 51.0), (92.0, -1.0), 140.0, None)
    a = math.atan2(-1.0 - 51.0, 92.0 + 28.0)
    for k in range(6):
        obs["t"] += 0.1
        bot.stuck_step(obs, [math.cos(a), math.sin(a)], "walk")
    assert bot.escape["kind"] == "jet"
    bot.escape = None  # pretend the jet gave up where it started
    bot.stuck_hist.clear()
    for k in range(6):
        obs["t"] += 0.1
        bot.stuck_step(obs, [math.cos(a), math.sin(a)], "walk")
    assert bot.escape["kind"] == "slide", "the jet didn't work here: slide instead"


def test_decision_cost_is_small():
    import time
    bot = new_bot()
    obs = _obs(bot, (-28.0, 51.0), (92.0, -1.0), 140.0, None)
    a = math.atan2(-1.0 - 51.0, 92.0 + 28.0)
    worst = 0.0
    for k in range(40):
        obs["t"] += 0.1
        t0 = time.perf_counter()
        bot.stuck_step(obs, [math.cos(a), math.sin(a)], "walk")
        worst = max(worst, time.perf_counter() - t0)
        bot.escape = None if k % 7 == 6 else bot.escape
    assert worst * 1000 < 3.0, f"{worst * 1000:.2f} ms"
