"""Shared test setup: paths, the recorded stream, the schema, and a helper that answers an observation
exactly the way mechfight.run() does (clear the drawing, call act, set tick, attach the drawing)."""
import atexit
import copy
import gzip
import importlib.util
import json
import math
import os
import sys

import pytest
from hypothesis import HealthCheck, settings

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.environ.get("MECHFIGHT_KIT", os.path.join(REPO, "mechfight-kit"))
STREAM = os.path.join(KIT, "check", "stream.jsonl.gz")
SCHEMA = os.path.join(KIT, "mechfight.schema.json")

sys.path.insert(0, REPO)
import mechfight as mf  # noqa: E402
import bot as botmod  # noqa: E402

if not (os.path.exists(STREAM) and os.path.exists(SCHEMA)):
    pytest.exit(f"mechfight kit not found at {KIT} (set MECHFIGHT_KIT)", returncode=4)

# Hypothesis: "default" for a thorough run, "hook" for the quick run after every edit.
settings.register_profile("default", max_examples=300, deadline=None,
                          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
settings.register_profile("hook", max_examples=60, deadline=None,
                          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

DECISION_BUDGET_MS = 10.0


def load_stream():
    with gzip.open(STREAM, "rt") as f:
        msgs = [json.loads(line) for line in f if line.strip()]
    return msgs[0], [m for m in msgs if m.get("type") == "obs"]


HELLO, OBS = load_stream()


def load_check_module():
    """check.py's own schema validator, so tests judge actions exactly as the conformance check does."""
    spec = importlib.util.spec_from_file_location("mechfight_check", os.path.join(KIT, "check", "check.py"))
    mod = importlib.util.module_from_spec(spec)
    was, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # leave the kit folder untouched
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
    return mod


CHECK = load_check_module()
VALIDATOR = CHECK.Schema(SCHEMA)


def new_bot():
    mf.draw.caps = HELLO["config"]["debug"]
    b = botmod.Bot(copy.deepcopy(HELLO))
    atexit.unregister(b._report)  # its timing summary is for matches; tests report their own
    return b


def respond(bot, obs):
    """What mechfight.run() would send for `obs`: (action dict, decision seconds)."""
    mf.draw.clear()
    before = len(bot.times)
    action = dict(bot.act(obs) or {})
    action["tick"] = obs["tick"]
    d = mf.draw.take()
    if d and "debug" not in action:
        action["debug"] = d
    dt = bot.times[-1] if len(bot.times) > before else float("nan")
    return action, dt


def action_problems(action, obs):
    """Everything check.py would fail an answer for: schema, tick echo, JSON validity, line size."""
    out = [f"schema: {e}" for e in VALIDATOR.errors(action, "Action")]
    if action.get("tick") != obs["tick"]:
        out.append(f"tick {action.get('tick')!r} != {obs['tick']!r}")
    try:
        line = json.dumps(action, separators=(",", ":"), allow_nan=False)
        if len(line.encode()) > HELLO["config"]["bot"]["max_line_bytes"]:
            out.append("line too long")
    except (TypeError, ValueError) as e:
        out.append(f"not valid JSON: {e}")
    return out


def base_obs(i=0):
    """A real recorded observation with nothing in sight: the starting point for scenarios."""
    o = copy.deepcopy(OBS[i])
    for k in ("mechs", "projectiles", "crates", "cubes", "pickups", "sounds", "beams", "events", "drops"):
        o[k] = []
    o["offer"] = None
    return o


def yaw_diff(a, b):
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


@pytest.fixture
def bot():
    return new_bot()
