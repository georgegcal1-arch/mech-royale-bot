"""Navigation step 2 (features.NAV_ROUTE): the precomputed ground grid (tools/navgrid.py -> nav/ground.nav.gz) and
A* routes. From the three arena stuck spots the bot must walk round the buildings to the zone spot by route alone
(STUCK_ESCAPE off); without routes it stays stuck (the control)."""
import gzip
import json
import math
import random
import time

import pytest
from hypothesis import assume, given

import nav as N
from conftest import ARENA, DECISION_BUDGET_MS, new_bot, reset, respond
from strategies import game_observations
from navsim import NavSim
from test_stuck import MECH, SPOTS, _obs
from test_v9 import _mode, switches

BASE = ("DUEL", "LIGHTNING", "LASER_GUARD", "NO_CHIP", "NPC_AWARE")
BOT = new_bot()
G = BOT.navgrid


def test_grid_loads_for_this_map_only(tmp_path):
    assert G is not None, "nav/ground.nav.gz missing or built for another map: run python tools/navgrid.py"
    assert G.cell == 2.0 and G.nx * G.ny == len(G.masks)
    with gzip.open(N.NAV_FILE, "rb") as f:
        data = f.read()
    nl = data.index(b"\n")
    header = json.loads(data[:nl])
    header["sha256"] = "0" * 64  # as if built for another map
    other = tmp_path / "other.nav.gz"
    with gzip.open(other, "wb") as f:
        f.write((json.dumps(header) + "\n").encode() + data[nl + 1:])
    assert N.NavGrid.load(ARENA, str(other)) is None
    assert N.NavGrid.load(None) is None


ROOM_M = 1.4  # what the grid guarantees in the worst case: the nearest blocked 1 m sample can be a diagonal 2.83 m
# away (just outside CLEARANCE_M 2.6) with the wall face up to a diagonal step (1.41 m) in front of it. So the body
# (2.5 m) may graze a wall at a route's tightest spots: the server slides it along (as navsim does), and
# STUCK_ESCAPE is the safety net. A full 2.5 m guarantee (clearance 3.2) closes real gaps the routes need; if
# grazing shows up in arena matches, sample at 0.5 m instead (about 4x the build time).


def test_walkable_cells_have_room_for_the_body():
    rng = random.Random(7)
    w, checked = BOT.world, 0
    cells = [(i, j) for i in range(G.nx) for j in range(G.ny) if G.masks[j * G.nx + i]]
    for c in rng.sample(cells, 1500):
        x, y = G.center(c)
        for k in range(8):
            a = k * math.pi / 4
            px, py = x + ROOM_M * math.cos(a), y + ROOM_M * math.sin(a)
            g = w.ground(px, py)  # each point against the ground under it (slopes: a low prism is a step there)
            for dz in (1.4, 4.5, 8.5):
                assert not w.solid_at(px, py, g + dz), (c, (x, y), k, dz)
        checked += 1
    assert checked == 1500


@pytest.mark.parametrize("name, xy, nc, nr, edge_in, limit", SPOTS, ids=[s[0] for s in SPOTS])
def test_astar_route_is_valid(name, xy, nc, nr, edge_in, limit):
    start = G.snap(G.cell_of(*xy), 3)
    goal = G.snap(G.cell_of(*nc))
    s = N.AStar(G, start, goal)
    t0 = time.perf_counter()
    while not s.run(time.perf_counter() + 0.002):
        pass
    assert s.path and not s.failed, f"{name}: no route ({s.expanded} expansions)"
    assert s.path[0] == start and s.path[-1] == goal
    for a, b in zip(s.path, s.path[1:]):
        d = (b[0] - a[0], b[1] - a[1])
        assert (G.masks[a[1] * G.nx + a[0]] >> G.bit[d]) & 1, f"step {a}->{b} not allowed"
    assert time.perf_counter() - t0 < 1.0, "the whole plan, all budget slices together"


def _walk(xy, nc, nr, edge_in, nav, seconds):
    """(reached the zone spot, worst decision s, modes seen, end position) walking from xy."""
    bot = new_bot()
    obs = _obs(bot, xy, nc, nr, edge_in)
    sim = NavSim(bot.world, MECH, obs["self"]["pos"])
    worst, modes = 0.0, set()
    with switches(*BASE, *(("NAV_ROUTE",) if nav else ())):
        for k in range(int(seconds / 0.1)):
            sim.into(obs["self"])
            action, dt = respond(bot, obs)
            worst = max(worst, dt)
            modes.add(_mode(action))
            if _mode(action) == "hold":
                return True, worst, modes, (sim.x, sim.y)
            sim.step(action)
            obs["tick"] += 2
            obs["t"] = round(obs["t"] + 0.1, 3)
    return False, worst, modes, (sim.x, sim.y)


@pytest.mark.parametrize("name, xy, nc, nr, edge_in, limit", SPOTS, ids=[s[0] for s in SPOTS])
def test_routes_round_the_real_stuck_spots(name, xy, nc, nr, edge_in, limit):
    ok, worst, modes, end = _walk(xy, nc, nr, edge_in, nav=True, seconds=45.0)
    assert ok, f"{name}: never reached the zone spot (ended at {end})"
    assert not any(m.startswith("unstick") for m in modes), "routes alone (STUCK_ESCAPE is off here)"
    assert worst * 1000 < DECISION_BUDGET_MS, f"worst decision {worst * 1000:.2f} ms"


@pytest.mark.parametrize("name, xy, nc, nr, edge_in, limit", SPOTS, ids=[s[0] for s in SPOTS])
def test_without_routes_it_stays_stuck(name, xy, nc, nr, edge_in, limit):
    ok, _, _, end = _walk(xy, nc, nr, edge_in, nav=False, seconds=15.0)
    assert not ok and math.hypot(end[0] - xy[0], end[1] - xy[1]) < 5.0, f"{name}: the control moved to {end}"


PROP_BOT = new_bot()


@given(game_observations)
def test_outside_the_zone_with_routes_still_runs_back_boosting(obs):
    """v4's rule with routes on: full speed and boost back to the zone (the direction is the route's)."""
    me = obs["self"]
    nx, ny = obs["zone"]["next_center"]
    assume(not me["in_zone"] and math.hypot(nx - me["pos"][0], ny - me["pos"][1]) > 1.0)
    reset(PROP_BOT)
    with switches(*BASE, "STUCK_ESCAPE", "NAV_ROUTE"):
        action, _ = respond(PROP_BOT, obs)
    assert math.hypot(*action["move"]) > 0.95 and action.get("boost") is True
    assert PROP_BOT.fallbacks == 0


def test_routes_are_deterministic():
    """Planning is budgeted by expansions (time is only a safety cap), so the same match replays the same."""
    def run():
        bot = new_bot()
        obs = _obs(bot, SPOTS[2][1], SPOTS[2][2], SPOTS[2][3], SPOTS[2][4])
        out = []
        for _ in range(20):
            bot.decision_t0 = time.perf_counter()
            out.append(bot.nav_waypoint(obs, (154.0, 64.0)))
            obs["t"] += 0.1
        return out
    assert run() == run()


def test_no_grid_means_straight_lines():
    bot = new_bot()
    bot.navgrid = None
    obs = _obs(bot, SPOTS[0][1], SPOTS[0][2], SPOTS[0][3], None)
    assert bot.nav_waypoint(obs, (92.0, -1.0)) == (92.0, -1.0)


def test_a_plan_is_spread_over_decisions_within_the_budget():
    bot = new_bot()
    obs = _obs(bot, SPOTS[2][1], SPOTS[2][2], SPOTS[2][3], SPOTS[2][4])
    goal = (34.0 + 120.0, 64.0)  # far round the stadium
    slices = 0
    for _ in range(200):
        bot.decision_t0 = time.perf_counter()
        t0 = time.perf_counter()
        bot.nav_waypoint(obs, goal)
        dt = time.perf_counter() - t0
        assert dt * 1000 < 3.5, f"{dt * 1000:.2f} ms in one decision ({N.NAV_EXPANSIONS_PER_DECISION} expansions)"
        slices += 1
        if bot.nav and (bot.nav.get("path") or bot.nav.get("straight") or bot.nav.get("search") is None):
            break
    assert bot.nav and (bot.nav.get("path") or bot.nav.get("straight")), "planned within 200 decisions"
