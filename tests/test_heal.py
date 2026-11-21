"""v7 heal (features.HEAL): disengage at low HP so regen can start, and v6 exactly with it off."""
import math

import features
import heal
from conftest import DECISION_BUDGET_MS, OBS, REPO, base_obs, new_bot, reset, respond
from replay import replay

GOLDEN = f"{REPO}/tests/golden/v6_replay.csv"
MAX_HP = 1400.0


def _obs(hp_frac, i=100):
    o = base_obs(i)
    o["self"]["hp"] = hp_frac * MAX_HP
    o["self"]["max_hp"] = MAX_HP
    o["self"]["weapons"][0]["ammo"] = 12
    return o


def _clear_dir(bot, obs, dist):
    """A bearing from us along which a mech `dist` away is in plain sight (no building or terrain)."""
    x, y, z = obs["self"]["pos"]
    for k in range(72):
        a = 2 * math.pi * k / 72
        ex, ey = x + dist * math.cos(a), y + dist * math.sin(a)
        ez = bot.world.ground(ex, ey)
        if not bot.world.blocked((x, y, z + 7.0), (ex, ey, ez + 4.5)) and abs(ez - z) < 5:
            return a, [ex, ey, ez]
    raise AssertionError("no clear direction around this spot")


def _mech(mid, pos):
    return {"id": mid, "kind": "mech", "pos": list(pos), "vel": [0, 0, 0], "aim": [0, 0], "hp": MAX_HP,
            "max_hp": MAX_HP, "cubes": 0, "weapons": ["gun"], "charging": False}


def _run(bot, obs, n=8):
    """n decisions, the torso following the bot's aim (as the server turns it); the actions."""
    out = []
    for _ in range(n):
        action, _ = respond(bot, obs)
        out.append(action)
        obs["self"]["aim"] = list(action["aim"])
        obs["tick"] += 2
        obs["t"] = round(obs["t"] + 0.1, 3)
    return out


def _label(action):
    return (action.get("debug") or {}).get("label", "")


def test_low_hp_holds_fire_and_moves_away_from_a_far_enemy(bot):
    obs = _obs(0.3)
    a, pos = _clear_dir(bot, obs, 120)
    obs["mechs"] = [_mech(5, pos)]
    acts = _run(bot, obs)
    assert all(_label(x).startswith("heal/") for x in acts)
    assert not any(any(x["fire"]) for x in acts), "fired while healing (resets the regen timer)"
    mx, my = acts[0]["move"]
    assert mx * math.cos(a) + my * math.sin(a) <= 1e-9, "walked toward the enemy while healing"
    assert abs((acts[-1]["aim"][0] - a + math.pi) % (2 * math.pi) - math.pi) < 0.2, "not facing the threat"


def test_same_situation_at_full_hp_fires(bot):  # control for the test above
    obs = _obs(1.0)
    _, pos = _clear_dir(bot, obs, 120)
    obs["mechs"] = [_mech(5, pos)]
    assert any(any(x["fire"]) for x in _run(bot, obs))


def test_fights_back_within_fight_range(bot):
    obs = _obs(0.3)
    _, pos = _clear_dir(bot, obs, 0.8 * features.HEAL_FIGHT_M)
    obs["mechs"] = [_mech(5, pos)]
    acts = _run(bot, obs)
    assert not any(_label(x).startswith("heal/") for x in acts)
    assert any(any(x["fire"]) for x in acts), "an enemy within HEAL_FIGHT_M: fight back"
    assert bot.healing, "still healing once it is gone"


def test_no_heal_in_the_final_zone(bot):
    obs = _obs(0.3)
    x, y, _ = obs["self"]["pos"]
    obs["zone"].update(center=[x, y], next_center=[x, y], radius=features.HEAL_FINAL_R, next_radius=25.0,
                       shrink_start=obs["t"] + 30, shrink_end=obs["t"] + 55)
    _, pos = _clear_dir(bot, obs, 50)
    obs["mechs"] = [_mech(5, pos)]
    acts = _run(bot, obs)
    assert not any(_label(x).startswith("heal/") for x in acts) and not bot.healing
    assert any(any(x["fire"]) for x in acts)


def test_hysteresis_45_to_85(bot):
    obs = _obs(features.HEAL_ENTER_FRAC + 0.05)
    respond(bot, obs)
    assert not bot.healing, "above the enter threshold: no heal yet"
    obs["self"]["hp"] = (features.HEAL_ENTER_FRAC - 0.05) * MAX_HP
    respond(bot, obs)
    assert bot.healing
    obs["self"]["hp"] = 0.7 * MAX_HP
    action, _ = respond(bot, obs)
    assert bot.healing and _label(action).startswith("heal/"), "between the thresholds: keep healing"
    obs["self"]["hp"] = features.HEAL_EXIT_FRAC * MAX_HP
    action, _ = respond(bot, obs)
    assert not bot.healing and not _label(action).startswith("heal/")


def test_zone_hurry_beats_hiding(bot):
    obs = _obs(0.3)
    z = obs["zone"]
    cx, cy = z["center"]
    d = z["radius"] + 60
    obs["self"]["pos"] = [cx + d, cy, 0.0]
    obs["self"]["in_zone"] = False
    obs["mechs"] = [_mech(5, [cx + d + 100, cy, 0.0])]
    action, _ = respond(bot, obs)
    assert _label(action).startswith("heal/zone!")
    assert action["boost"] is True and not any(action["fire"])
    nx, ny = z["next_center"]
    want = math.atan2(ny - cy, nx - cx - d)
    assert abs((math.atan2(action["move"][1], action["move"][0]) - want + math.pi) % (2 * math.pi) - math.pi) < 0.3


def test_prefers_a_spot_out_of_the_threats_sight(bot, monkeypatch):
    """With buildings (stubbed) hiding every spot north of us from the threat, the hide spot is north,
    although straight away from the threat (west) would gain more distance."""
    obs = _obs(0.3)
    nx, ny = obs["zone"]["next_center"]
    z = bot.world.ground(nx, ny)
    obs["self"]["pos"] = [nx, ny, z]
    obs["mechs"] = [_mech(5, [nx + 120, ny, z])]
    monkeypatch.setattr(bot.world, "solid_at", lambda *a: False)
    monkeypatch.setattr(bot.world, "under_canopy", lambda *a: False)
    monkeypatch.setattr(bot.world, "blocked", lambda a, b: a[0] > nx + 100 and b[1] > ny + 5)
    action, _ = respond(bot, obs)
    assert _label(action).startswith("heal/hide")
    assert action["move"][1] > 0.3, f"move {action['move']} doesn't head for the hidden side"
    monkeypatch.setattr(bot.world, "blocked", lambda a, b: False)
    reset(bot)
    action, _ = respond(bot, obs)
    assert action["move"][0] < -0.5, "with no cover anywhere, walk away from the threat"


def test_stays_put_when_already_hidden(bot, monkeypatch):
    obs = _obs(0.3)
    nx, ny = obs["zone"]["next_center"]
    z = bot.world.ground(nx, ny)
    obs["self"]["pos"] = [nx, ny, z]
    obs["mechs"] = [_mech(5, [nx + 120, ny, z])]
    monkeypatch.setattr(bot.world, "blocked", lambda a, b: a[0] > nx + 100)  # hidden everywhere
    action, _ = respond(bot, obs)
    assert _label(action).startswith("heal/cover") and action["move"] == [0.0, 0.0]


def test_unseen_attacker_counts_as_a_threat(bot):
    obs = _obs(0.3)
    obs["events"] = [{"type": "damaged", "amount": 45, "weapon": "gun", "bearing": 0.5, "by": 7, "rear": False}]
    action, _ = respond(bot, obs)
    assert _label(action).startswith("heal/") and "1 threats" in _label(action)
    assert abs(action["aim"][0] - 0.5) < 0.05 and not any(action["fire"])


def test_canopy_under_a_dense_crown():
    w = new_bot().world
    assert w.crowns, "no dense crowns loaded from world.json"
    cx, cy, r, lo, hi = max(w.crowns, key=lambda c: c[2])
    assert w.under_canopy(cx, cy, 0.5 * (lo + hi))
    assert not w.under_canopy(cx, cy, hi + 1.0)
    assert not w.under_canopy(cx + r + 1.0, cy, 0.5 * (lo + hi))


def test_worst_case_hide_plan_is_fast(bot):
    """A cold re-plan (3 threats, every candidate checked) at spots all over the recorded match."""
    worst = 0.0
    for i in range(0, len(OBS), 10):
        obs = _obs(0.3, i)
        x, y, z = obs["self"]["pos"]
        obs["mechs"] = [_mech(5 + k, [x + d * math.cos(a), y + d * math.sin(a), z])
                        for k, (d, a) in enumerate(((60, 0.3), (110, 2.4), (150, -1.9)))]
        reset(bot)
        _, dt = respond(bot, obs)
        worst = max(worst, dt)
    assert worst * 1000 < DECISION_BUDGET_MS, f"{worst * 1000:.2f} ms"
    assert bot.fallbacks == 0


def test_hide_plan_checks_are_bounded():
    assert heal.HIDE_MAX_CHECKS <= 48 and heal.HIDE_THREATS <= 3 and heal.HIDE_SIGHT_M <= 250


def test_far_threats_are_not_line_checked(bot, monkeypatch):
    """A threat beyond sight range counts as hidden without a (length-costly) line check."""
    obs = _obs(0.3)
    x, y, z = obs["self"]["pos"]
    obs["mechs"] = [_mech(5, [x + 1000, y, z])]
    calls = []
    real = bot.world.blocked
    monkeypatch.setattr(bot.world, "blocked", lambda a, b: calls.append(math.dist(a, b)) or real(a, b))
    action, _ = respond(bot, obs)
    assert _label(action).startswith("heal/cover"), "out of its sight already: stay"
    assert all(d < 100 for d in calls), f"a long line was checked: {max(calls):.0f} m"


def test_heal_off_replay_matches_v6_exactly(monkeypatch, tmp_path):
    monkeypatch.setattr(features, "HEAL", False)
    out = tmp_path / "off.csv"
    replay(str(out), timing=False)
    with open(GOLDEN, "rb") as f:
        assert out.read_bytes() == f.read(), "with HEAL off the replay must equal v6's (tests/golden/v6_replay.csv)"
