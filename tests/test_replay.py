"""The recorded match (check.py's 300 observations), end to end through the decision code (tests/replay.py)."""
import csv

from conftest import DECISION_BUDGET_MS, OBS, action_problems, new_bot, respond
from replay import COLUMNS, replay


def test_replay_writes_one_row_per_observation(tmp_path):
    out = tmp_path / "decisions.csv"
    rows, bot = replay(str(out))
    with open(out, newline="") as f:
        read = list(csv.DictReader(f))
    assert len(read) == len(OBS) == 300
    assert list(read[0]) == COLUMNS
    assert [int(r["tick"]) for r in read] == [o["tick"] for o in OBS]
    assert bot.fallbacks == 0, "the safety net fired on real observations"


def test_replay_every_answer_valid_and_fast():
    bot = new_bot()
    worst = 0.0
    for obs in OBS:
        action, dt = respond(bot, obs)
        assert action_problems(action, obs) == [], f"tick {obs['tick']}"
        worst = max(worst, dt)
    assert worst * 1000 < DECISION_BUDGET_MS, f"slowest decision {worst * 1000:.2f} ms"
