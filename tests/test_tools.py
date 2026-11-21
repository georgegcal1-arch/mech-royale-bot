"""tools/damage_ledger.py on the recorded stream: the totals tools/damage_ledger.py prints for check.py's match."""
import io
import os
import sys

from conftest import REPO, STREAM

sys.path.insert(0, os.path.join(REPO, "tools"))
import damage_ledger as L  # noqa: E402


def test_ledger_totals_on_the_recorded_match():
    rows, info = L.ledger(L.messages(STREAM))
    taken = [r for r in rows if r[1] == "taken"]
    dealt = [r for r in rows if r[1] == "dealt"]
    assert sum(r[3] for r in taken) == 1665.0 and len(taken) == 13
    assert sum(r[3] for r in taken if r[2] == "laser") == 1260.0
    assert sum(r[3] for r in dealt) == 472.5 and len(dealt) == 10
    assert {r[4] for r in rows} == {10}
    assert info["end"]["place"] == 13 and info["end"]["kills"] == 0
    assert all(r[5] is None or 50 <= r[5] <= 160 for r in rows)


def test_ledger_report_prints_both_sides():
    rows, info = L.ledger(L.messages(STREAM))
    out = io.StringIO()
    L.report(rows, info, timeline=False, out=out)
    text = out.getvalue()
    assert "taken: 1665.0 in 13 hits" in text and "dealt: 472.5 in 10 hits" in text
    assert "100-160 m" in text
