"""Property tests: for any schema-valid observation the bot's answer must be sound.

  1. never crashes         act() never raises (the safety net catches everything)
  2. valid action          check.py's own schema check, the tick echo, valid JSON, line size
  3. under 10 ms           every decision, measured inside act()
  4. picks an upgrade      an offer with choices gets a valid `pick` index
  5. fires only at a target  `fire` is set only while an enemy mech is in sight
"""
import json

from hypothesis import given, note

from conftest import DECISION_BUDGET_MS, VALIDATOR, action_problems, new_bot, respond
from strategies import game_observations, wild_observations, with_offer

BOT = new_bot()  # one bot for the whole module (it keeps no per-match state that matters here)


def _respond(obs):
    note(f"obs = {json.dumps(obs)[:2000]}")
    action, dt = respond(BOT, obs)
    note(f"action = {json.dumps(action)}  ({dt * 1000:.3f} ms)")
    return action, dt


def _enemy_in_sight(obs):
    return any(m["kind"] == "mech" and m["id"] != obs["self"]["id"] for m in obs["mechs"])


# --- sanity: the generators really produce schema-valid observations -------------------------
@given(game_observations)
def test_generated_game_observations_are_schema_valid(obs):
    assert VALIDATOR.errors(obs, "Observation") == []


@given(wild_observations)
def test_generated_wild_observations_are_schema_valid(obs):
    assert VALIDATOR.errors(obs, "Observation") == []


# --- 1 + 2: never crashes, always a valid action ---------------------------------------------
@given(game_observations)
def test_never_crashes_and_action_is_valid(obs):
    action, _ = _respond(obs)
    assert action_problems(action, obs) == []


@given(wild_observations)
def test_never_crashes_and_action_is_valid_on_extreme_numbers(obs):
    action, _ = _respond(obs)
    assert action_problems(action, obs) == []


# --- 3: decision time --------------------------------------------------------------------
@given(game_observations)
def test_decision_under_10_ms(obs):
    _, dt = _respond(obs)
    assert dt * 1000 < DECISION_BUDGET_MS


# --- 4: upgrades -------------------------------------------------------------------------
@given(with_offer(game_observations))
def test_picks_an_upgrade_when_offered(obs):
    action, _ = _respond(obs)
    n = len(obs["offer"]["choices"])
    assert "pick" in action, "offer ignored: the 5 s timeout would apply choice 0"
    assert action["pick"] == int(action["pick"]) and 0 <= action["pick"] < n


# --- 5: firing ---------------------------------------------------------------------------
@given(game_observations)
def test_fires_only_with_a_target(obs):
    action, _ = _respond(obs)
    if any(action.get("fire", [])):
        assert _enemy_in_sight(obs), "fired with no enemy mech in sight"
