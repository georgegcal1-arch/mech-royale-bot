"""LASER_COUNTER (laser_counter.py) and its exposure map (tools/exposure.py -> nav/exposure.nav.gz).

The key test replays the 9 laser hits we saw coming in our three lost laser duels (arena MUWWU, VNETQ, ZEFXJ:
tests/fixtures/laser_losses.json.gz, each from 1 s before the charge was first seen to the hit). A fresh bot
drives tests/navsim.py from the recorded start (the enemies are as recorded); at the moment the beam landed in
the real match we check, with live line checks, whether our body is now hidden from the shooter's muzzle.
With LASER_COUNTER off the same replay is the control."""
import copy
import gzip
import json
import math
import os
import random

import pytest

import laser_counter as LC
from conftest import ARENA, DECISION_BUDGET_MS, HELLO, REPO, base_obs, new_bot, respond
from navsim import NavSim
from test_v9 import _label, _mech, _mode, switches
from test_v9fg import ARC, GUN, LASER, _arm

BEST = ("DUEL", "LIGHTNING", "LASER_GUARD", "NO_CHIP", "NPC_AWARE", "STUCK_ESCAPE", "NAV_ROUTE", "LOOT_SWEEP",
        "UPGRADES_OFFENSE")
MECH = HELLO["config"]["mech"]
with gzip.open(os.path.join(REPO, "tests", "fixtures", "laser_losses.json.gz"), "rt", encoding="utf-8") as f:
    CASES = json.load(f)
BOT = new_bot()
EX = BOT.exposure


def test_exposure_map_loads_and_matches_live_lines():
    assert EX is not None, "nav/exposure.nav.gz missing or for another map: python tools/exposure.py"
    assert LC.ExposureMap.load(None) is None
    w = BOT.world
    rng = random.Random(3)
    cells = [(i, j) for i in range(EX.nx) for j in range(EX.ny) if EX.value((i, j))]
    checked = 0
    for c in rng.sample(cells, 120):
        x, y = EX.center(c)
        g = w.ground(x, y)
        v = EX.value(c)
        for _ in range(3):
            k, r = rng.randrange(EX.dirs), rng.randrange(len(EX.ranges))
            a = 2 * math.pi * k / EX.dirs
            sx, sy = x + EX.ranges[r] * math.cos(a), y + EX.ranges[r] * math.sin(a)
            eye = (sx, sy, w.ground(sx, sy) + LC.MUZZLE_M)
            hid = w.blocked(eye, (x, y, g + LC.CENTER_M)) and w.blocked(eye, (x, y, g + LC.TOP_M))
            assert bool((v >> (k * len(EX.ranges) + r)) & 1) == hid, (c, k, r)
            checked += 1
    assert checked == 360


def _replay(case, on):
    """(hidden at the hit?, modes seen, worst decision s)"""
    bot = new_bot()
    obs = [copy.deepcopy(o) for o in case["obs"]]
    sim = NavSim(bot.world, MECH, obs[0]["self"]["pos"], energy=obs[0]["self"].get("energy") or 100.0)
    sim.vx, sim.vy = obs[0]["self"]["vel"][0], obs[0]["self"]["vel"][1]
    modes, worst = [], 0.0
    with switches(*BEST, *(("LASER_COUNTER",) if on else ())):
        for o in obs[:-1]:
            sim.into(o["self"])
            o["events"] = [e for e in o["events"] if not (e["type"] == "damaged" and e.get("weapon") == "laser")]
            action, dt = respond(bot, o)
            worst = max(worst, dt)
            modes.append(_mode(action))
            sim.step(action)
    hit = obs[-1]
    shooter = next(m for m in hit["mechs"] if m["id"] == case["shooter"])
    sx, sy, sz = shooter["pos"]
    eye = (sx, sy, sz + LC.MUZZLE_M)
    w = bot.world
    hidden = w.blocked(eye, (sim.x, sim.y, sim.z + LC.CENTER_M)) and w.blocked(eye, (sim.x, sim.y, sim.z + LC.TOP_M))
    return hidden, modes, worst, math.hypot(sx - sim.x, sy - sim.y)


def test_replayed_laser_losses_close_in_without_more_exposure():
    """Re-aimed at the winner (B2, close aggression): LASER_COUNTER closes in during the laser's cooldown window and
    uses cover only while it is ready. So at the moment each recorded beam landed we should be closer to the
    shooter than without it, and not less often hidden. (The shooters are as recorded: they don't react to us.)
    The hiding-first design measured 3/9 hidden vs 1/9 off, but stood back; this one trades that for distance."""
    on = [_replay(c, True) for c in CASES]
    off = [_replay(c, False) for c in CASES]
    rows = [f"{c['match']} t={c['hit_t']}: on {'HIDDEN' if a[0] else 'hit'} at {a[3]:.0f} m "
            f"({','.join(sorted(set(m for m in a[1] if m.startswith('lc'))))}), off {'HIDDEN' if b[0] else 'hit'} at {b[3]:.0f} m"
            for c, a, b in zip(CASES, on, off)]
    n_on, n_off = sum(a[0] for a in on), sum(b[0] for b in off)
    d_on, d_off = sum(a[3] for a in on) / len(on), sum(b[3] for b in off) / len(off)
    print("\n" + "\n".join(rows) + f"\nhidden at the hit: {n_on}/{len(CASES)} on, {n_off} off; "
          f"mean distance to the shooter at the hit: {d_on:.0f} m on, {d_off:.0f} m off")
    # Measured (closing design): 67 m on vs 77 m off on average, closer in every case with a window; hidden 0/9 on
    # vs 1/9 off (VNETQ t=56.4: without LASER_COUNTER we happened to stand behind cover; with it we were closing).
    assert d_on < d_off - 5.0, "\n".join(rows)
    assert n_on >= n_off - 1, "\n".join(rows)
    assert max(a[2] for a in on) * 1000 < DECISION_BUDGET_MS


def _foe_scene(bot, *weapons, charging=False, dist=120.0):
    obs = _arm(base_obs(150), *weapons)
    m = _mech(obs, 7, dist, 0, weapons=("gun", "laser"))
    m["charging"] = charging
    obs["mechs"] = [m]
    return obs, m


def test_window_after_its_beam():
    bot = new_bot()
    obs, m = _foe_scene(bot, GUN, ARC)
    if bot.laser_foe(obs, obs["mechs"]) is None:
        pytest.skip("no clear line on this spot of the real map")
    obs["beams"] = [{"by": 7, "kind": "laser", "path": [m["pos"], obs["self"]["pos"]], "age": 0.1}]
    bot.note_lasers(obs)
    goal, boost, mode = bot.laser_counter(obs, obs["mechs"], "walk")
    assert mode == "lc/close" and boost and goal == (m["pos"][0], m["pos"][1]), "close in its window"
    obs2, _ = _foe_scene(bot, GUN, LASER)
    obs2["t"] = obs["t"] + 0.2
    assert bot.laser_counter(obs2, obs2["mechs"], "walk")[2] == "lc/close", "without lightning too (B2 won)"
    obs2["t"] = obs["t"] + LC.WINDOW_S + 0.1  # the window is over, not charging: B2's own approach
    assert bot.laser_counter(obs2, obs2["mechs"], "walk") is None


def _cover_pair(bot):
    """A real-map spot B with cover 8 m away: cell A is hidden from a shooter 100 m off in some direction, its
    neighbour B two cells over is in the open from that shooter (live checks). (B, shooter position)."""
    ex, w = bot.exposure, bot.world
    r100 = ex.ranges.index(100.0)
    for j in range(40, ex.ny - 40, 3):
        for i in range(40, ex.nx - 40, 3):
            v = ex.value((i, j))
            if not v:
                continue
            for k in range(ex.dirs):
                if not (v >> (k * len(ex.ranges) + r100)) & 1:
                    continue
                ax, ay = ex.center((i, j))
                a = 2 * math.pi * k / ex.dirs
                sx, sy = ax + 100 * math.cos(a), ay + 100 * math.sin(a)
                for di, dj in ((2, 0), (-2, 0), (0, 2), (0, -2)):
                    b = (i + di, j + dj)
                    if not ex.value(b) or ex.hidden(b, sx, sy):
                        continue
                    bx, by = ex.center(b)
                    gb, gs = w.ground(bx, by), w.ground(sx, sy)
                    if not w.blocked((sx, sy, gs + LC.MUZZLE_M), (bx, by, gb + LC.CENTER_M)):
                        return (bx, by, gb), (sx, sy, gs)
    return None


def test_hides_from_a_charging_laser_behind_cover_in_reach():
    bot = new_bot()
    pair = _cover_pair(bot)
    assert pair is not None, "no spot with cover 8 m away on the map?"
    (bx, by, bz), (sx, sy, sz) = pair
    obs = _arm(base_obs(150), GUN, None)
    obs["self"]["pos"] = [bx, by, bz]
    m = _mech(obs, 7, sx - bx, sy - by, weapons=("gun", "laser"))
    m["pos"][2] = sz
    m["charging"] = True
    obs["mechs"] = [m]
    goal, boost, mode = bot.laser_counter(obs, obs["mechs"], "walk")
    assert mode == "lc/hide" and boost
    g = bot.world.ground(*goal)
    assert bot.world.blocked((sx, sy, sz + LC.MUZZLE_M), (goal[0], goal[1], g + LC.CENTER_M)), "behind the cover"
    assert math.hypot(goal[0] - bx, goal[1] - by) <= LC.ANCHOR_SEARCH_M, "reachable in the ~0.7 s warning"
    m["charging"] = False
    bot.lc = None
    assert bot.laser_counter(obs, obs["mechs"], "walk") is None, "ready but not charging: B2's approach"


def test_lightning_up_close_is_the_normal_fight():
    bot = new_bot()
    obs, m = _foe_scene(bot, GUN, ARC, charging=True, dist=35.0)
    assert bot.laser_counter(obs, obs["mechs"], "walk") is None


def test_no_map_means_no_change():
    # LASER_COUNTER is on since step LC (QUEUE.md); without the exposure map it must change nothing
    bot = new_bot()
    bot.exposure = None
    obs, m = _foe_scene(bot, GUN, None, charging=True)
    assert bot.laser_counter(obs, obs["mechs"], "walk") is None or bot.lc["anchor"] is None
