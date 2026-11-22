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
    return hidden, modes, worst


def test_replayed_laser_losses_hide_in_time():
    on = [_replay(c, True) for c in CASES]
    off = [_replay(c, False) for c in CASES]
    rows = [f"{c['match']} t={c['hit_t']}: on {'HIDDEN' if a[0] else 'hit'} ({','.join(sorted(set(m for m in a[1] if m.startswith('lc'))))}), "
            f"off {'HIDDEN' if b[0] else 'hit'}" for c, a, b in zip(CASES, on, off)]
    n_on, n_off = sum(a[0] for a in on), sum(b[0] for b in off)
    print("\n" + "\n".join(rows) + f"\nhidden at the hit: {n_on}/{len(CASES)} with LASER_COUNTER, {n_off} without")
    # Cover existed within ~40 m (live line checks) at only 5 of the 9 spots; at the four ZEFXJ hits there was
    # none in reach (none within 80 m, or 41-59 m from a laser 41-53 m away): no line-of-sight answer there.
    # Measured: 3 of 9 hidden with LASER_COUNTER (3 of the 5 with cover), 1 without.
    assert n_on > n_off, "\n".join(rows)
    assert n_on >= 3, "\n".join(rows)
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
    assert mode == "lc/close" and boost and goal == (m["pos"][0], m["pos"][1]), "lightning: close in its window"
    obs2, _ = _foe_scene(bot, GUN, LASER)
    obs2["t"] = obs["t"] + 0.2
    assert bot.laser_counter(obs2, obs2["mechs"], "walk") is None, "no lightning: the normal fight peeks and shoots"
    obs2["t"] = obs["t"] + LC.WINDOW_S + 0.1  # the window is over
    bot.lc = None
    out = bot.laser_counter(obs2, obs2["mechs"], "walk")
    assert out is None or out[2] in ("lc/cover", "lc/hide", "lc/hold"), "the window is over: back to cover"


def test_hides_from_a_charging_laser_to_a_confirmed_anchor():
    found = 0
    for c in CASES:  # the real spots of our lost duels
        o = next(p for p in c["obs"] if any(m["id"] == c["shooter"] and m.get("charging") for m in p["mechs"]))
        bot = new_bot()
        enemies = sorted((m for m in o["mechs"] if m["kind"] == "mech"), key=lambda m: math.dist(m["pos"], o["self"]["pos"]))
        out = bot.laser_counter(o, enemies, "walk")
        if out is None:
            continue
        goal, boost, mode = out
        assert mode == "lc/hide" and boost, mode
        foe = bot.laser_foe(o, enemies)
        fx, fy, fz = foe["pos"]
        g = bot.world.ground(*goal)
        assert bot.world.blocked((fx, fy, fz + LC.MUZZLE_M), (goal[0], goal[1], g + LC.CENTER_M))
        assert math.hypot(goal[0] - o["self"]["pos"][0], goal[1] - o["self"]["pos"][1]) <= LC.ANCHOR_SEARCH_M
        found += 1
    assert found >= 3, f"an anchor at only {found} of {len(CASES)} real spots (cover in reach at about 5)"


def test_lightning_up_close_is_the_normal_fight():
    bot = new_bot()
    obs, m = _foe_scene(bot, GUN, ARC, charging=True, dist=35.0)
    assert bot.laser_counter(obs, obs["mechs"], "walk") is None


def test_off_by_default_and_no_map_means_no_change():
    import features
    assert features.LASER_COUNTER is False
    bot = new_bot()
    bot.exposure = None
    obs, m = _foe_scene(bot, GUN, None, charging=True)
    assert bot.laser_counter(obs, obs["mechs"], "walk") is None or bot.lc["anchor"] is None
