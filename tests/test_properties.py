"""Property tests: for any schema-valid observation the bot's answer must be sound.

  1. never crashes           act() never raises (the safety net catches everything)
  2. valid action            check.py's own schema check, the tick echo, valid JSON, line size
  3. under 10 ms             every decision, measured inside act()
  4. picks an upgrade        an offer with choices gets a valid `pick` index
  5. fires only at a target  `fire` is set only while something to shoot (mech, NPC, or v6: crate) is in sight

The game-range bot loads the real map, so cover checks (world.py) are part of every decision.
"""
import json
import math

from hypothesis import given, note

from conftest import DECISION_BUDGET_MS, VALIDATOR, action_problems, base_obs, new_bot, reset, respond
from strategies import game_observations, wild_observations, with_offer

BOT = new_bot()  # one bot for the whole module, with the map (it keeps no per-match state that matters here)

# Extreme-number observations run without the map: world.py walks a line in 2 m steps against the
# terrain, so an astronomically long line (coordinates ~1e300) never finishes. That cost is pinned by
# test_far_target_decision_is_fast below; this bot tests the decision code's own number handling.
WILD_BOT = new_bot()
WILD_BOT.world = None


def _respond(obs, bot=BOT):
    """Each example is an independent situation: start from a fresh bot (its map is kept)."""
    reset(bot)
    note(f"obs = {json.dumps(obs)[:2000]}")
    action, dt = respond(bot, obs)
    note(f"action = {json.dumps(action)}  ({dt * 1000:.3f} ms)")
    return action, dt


def _target_in_sight(obs):
    """Any visible mech or NPC drone that isn't us, or (v6 economy) a crate."""
    return any(m["id"] != obs["self"]["id"] for m in obs["mechs"]) or bool(obs.get("crates"))


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
    action, _ = _respond(obs, WILD_BOT)
    assert action_problems(action, obs) == []


# --- 3: decision time --------------------------------------------------------------------
@given(game_observations)
def test_decision_under_10_ms(obs):
    _, dt = _respond(obs)
    assert dt * 1000 < DECISION_BUDGET_MS


def test_far_target_decision_is_fast():
    """A schema-valid (if off-map) enemy 100 km away in open sky: the line-of-shot check must not
    scale with distance. Inside the map lines are at most ~1.3 km, so matches are not affected."""
    obs = base_obs(100)
    x, y, _ = obs["self"]["pos"]
    obs["self"]["pos"] = [x, y, 400.0]
    obs["mechs"] = [{"id": 5, "kind": "mech", "pos": [x + 100_000, y, 400.0], "vel": [0, 0, 0], "aim": [0, 0]}]
    reset(BOT)
    _, dt = respond(BOT, obs)
    assert dt * 1000 < DECISION_BUDGET_MS, f"{dt * 1000:.1f} ms for a target 100 km away"


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
        assert _target_in_sight(obs), "fired with nothing in sight"
