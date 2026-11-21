"""Scenario tests: hand-built situations from a real recorded observation, each with the behavior a
sensible bot should show.

Tests marked `strategy_gap` describe behavior the current strategy does not have yet. They are
expected to fail (strict xfail): the suite stays green while they fail, and turns red the moment one
starts passing, as a reminder to remove its marker."""
import math

import pytest

from conftest import HELLO, base_obs, respond, yaw_diff


def strategy_gap(reason):
    return pytest.mark.xfail(strict=True, reason=f"strategy gap: {reason}")

REACH = HELLO["config"]["loot"]["pickup_radius"]  # 4 m horizontally
VREACH = 4.0                                      # fixed vertical reach (protocol.md section 6, `take`)


def _move_yaw(action):
    mx, my = action["move"]
    return math.atan2(my, mx)


def _enemy(obs, mid, dx, dy, hp=1400):
    x, y, z = obs["self"]["pos"]
    return {"id": mid, "kind": "mech", "pos": [x + dx, y + dy, z], "vel": [0, 0, 0], "aim": [0, 0],
            "hp": hp, "max_hp": 1400, "cubes": 0, "weapons": ["gun"], "charging": False}


# --- hit from behind ---------------------------------------------------------------------
def test_hit_from_behind_turns_to_face_attacker(bot):
    """Shot in the back by an unseen attacker: turn the torso toward the damage bearing
    (rear hits take x1.5 and the attacker is likely there)."""
    obs = base_obs(100)
    quiet, _ = respond(bot, obs)
    travel = _move_yaw(quiet)
    behind = (travel + math.pi + math.pi) % (2 * math.pi) - math.pi  # exactly behind the way we walk
    obs["self"]["aim"] = [travel, 0.0]
    obs["events"] = [{"type": "damaged", "amount": 67.5, "weapon": "gun", "bearing": behind, "by": 5, "rear": True}]
    action, _ = respond(bot, obs)
    assert yaw_diff(action["aim"][0], behind) < math.radians(45), (
        f"aim {math.degrees(action['aim'][0]):.0f} deg, attacker at {math.degrees(behind):.0f} deg")


# --- safety net ----------------------------------------------------------------------------
def test_safe_fallback_still_answers_upgrade_offer(bot, monkeypatch):
    """If the decision code fails while an offer is pending, the fallback should still pick
    (otherwise the offer waits for its 5 s timeout)."""
    obs = base_obs(100)
    obs["offer"] = {"id": 1, "expires": obs["t"] + 5, "choices": [
        {"id": "armor", "name": "Reactive armor", "desc": "-8% damage taken"}]}
    monkeypatch.setattr(type(bot), "decide", lambda self, o: 1 / 0)
    action, _ = respond(bot, obs)
    assert bot.fallbacks == 1 and not any(action.get("fire", []))
    assert action.get("pick") == 0


# --- low HP ------------------------------------------------------------------------------
def test_low_hp_backs_away_from_enemy_ahead(bot):
    """At ~10% HP with a full-HP enemy 60 m straight ahead, don't keep walking into it."""
    obs = base_obs(100)
    quiet, _ = respond(bot, obs)
    travel = _move_yaw(quiet)
    obs["self"]["hp"] = 140
    obs["mechs"] = [_enemy(obs, 5, 60 * math.cos(travel), 60 * math.sin(travel))]
    action, _ = respond(bot, obs)
    mx, my = action["move"]
    toward = mx * math.cos(travel) + my * math.sin(travel)
    assert toward <= 0, f"still moving toward the enemy (component {toward:.2f})"


# --- outside the zone --------------------------------------------------------------------
def _outside(obs, extra_m=60):
    z = obs["zone"]
    cx, cy = z["center"]
    d = z["radius"] + extra_m
    obs["self"]["pos"] = [cx + d, cy, 0.0]
    obs["self"]["in_zone"] = False
    obs["t"] = 10.0  # before the first shrink
    return obs


def _assert_heads_to_zone(obs, action):
    x, y = obs["self"]["pos"][:2]
    nx, ny = obs["zone"]["next_center"]
    want = math.atan2(ny - y, nx - x)
    assert yaw_diff(_move_yaw(action), want) < math.radians(15)
    assert math.hypot(*action["move"]) > 0.95, "outside the zone at less than full speed"
    assert action.get("boost") is True, "outside the zone without boosting"


def test_outside_zone_heads_back_at_full_speed(bot):
    obs = _outside(base_obs(100))
    action, _ = respond(bot, obs)
    _assert_heads_to_zone(obs, action)


def test_outside_zone_keeps_heading_back_with_enemy_in_sight(bot):
    obs = _outside(base_obs(100))
    obs["mechs"] = [_enemy(obs, 5, 0, 50)]  # enemy off to the side
    action, _ = respond(bot, obs)
    _assert_heads_to_zone(obs, action)


def test_inside_but_near_edge_hurries_inward(bot):
    obs = base_obs(100)
    z = obs["zone"]
    cx, cy = z["center"]
    obs["self"]["pos"] = [cx + z["radius"] - 10, cy, 0.0]
    obs["self"]["in_zone"] = True
    obs["t"] = 10.0
    action, _ = respond(bot, obs)
    x, y = obs["self"]["pos"][:2]
    nx, ny = z["next_center"]
    assert yaw_diff(_move_yaw(action), math.atan2(ny - y, nx - x)) < math.radians(15)
    assert math.hypot(*action["move"]) > 0.95


# --- pickup within 4 m -------------------------------------------------------------------
def _with_pickup(obs, dist, dz=0.0, kind="laser", pid=8013):
    x, y, z = obs["self"]["pos"]
    obs["pickups"] = [{"id": pid, "kind": kind, "pos": [x + dist, y, z + dz]}]
    return obs


def test_takes_pickup_within_reach(bot):
    obs = _with_pickup(base_obs(100), 3.0)
    assert obs["self"]["weapons"][1] is None
    action, _ = respond(bot, obs)
    assert action.get("take") == 8013


def test_takes_pickup_just_inside_reach(bot):
    obs = _with_pickup(base_obs(100), REACH - 0.1)
    action, _ = respond(bot, obs)
    assert action.get("take") == 8013


def test_ignores_pickup_out_of_reach(bot):
    obs = _with_pickup(base_obs(100), REACH + 1.0)
    action, _ = respond(bot, obs)
    assert "take" not in action


def test_ignores_pickup_out_of_vertical_reach(bot):
    """A weapon on a roof 8 m above us is within 4 m horizontally but not vertically: `take` fails."""
    obs = _with_pickup(base_obs(100), 2.0, dz=8.0)
    action, _ = respond(bot, obs)
    assert "take" not in action, "sent `take` for a pickup 8 m above (server answers take_failed: range)"


def test_takes_nearby_pickup_listed_after_a_far_one(bot):
    obs = _with_pickup(base_obs(100), 3.0, pid=8013)
    x, y, z = obs["self"]["pos"]
    obs["pickups"].insert(0, {"id": 8099, "kind": "mortar", "pos": [x + 40, y, z]})
    action, _ = respond(bot, obs)
    assert action.get("take") == 8013


def test_no_take_when_both_slots_full(bot):
    obs = _with_pickup(base_obs(100), 2.0)
    gun = obs["self"]["weapons"][0]
    obs["self"]["weapons"][1] = {"kind": "mortar", "cooldown": 0, "stats": {}}
    assert gun["kind"] == "gun"
    action, _ = respond(bot, obs)
    assert "take" not in action or "drop" in action, "`take` with both slots full needs `drop`"
