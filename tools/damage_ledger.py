"""Damage ledger for a mechfight observation stream: every hit taken and dealt, with weapon, range and time.

    python tools/damage_ledger.py mechfight-kit/check/stream.jsonl.gz
    python tools/damage_ledger.py my_match.jsonl            # .jsonl or .jsonl.gz, one message per line
    python tools/damage_ledger.py - < my_match.jsonl        # stdin
    python tools/damage_ledger.py STREAM --summary          # totals only, no per-hit timeline

A stream is what a bot reads on stdin (hello, obs..., end) or just the obs lines. From each obs it reads
`events`: `damaged` (taken) and `hit` (dealt), plus `kill`, `effect` and the final `end`. The range is
the horizontal distance to the other mech when it is in that observation's `mechs` (seen or tracked),
else "?" (an unseen attacker, or the zone). Standard library only.
"""
import argparse
import collections
import gzip
import json
import math
import sys

BANDS = ((45.0, "0-45"), (100.0, "45-100"), (160.0, "100-160"), (230.0, "160-230"), (math.inf, "230+"))


def band(d):
    if d is None:
        return "?"
    return next(name for top, name in BANDS if d < top)


def messages(path):
    if path == "-":
        f = sys.stdin
    elif path.endswith(".gz"):
        f = gzip.open(path, "rt", encoding="utf-8")
    else:
        f = open(path, encoding="utf-8")
    with f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def ledger(msgs):
    """(rows, info): rows = [(t, 'taken'|'dealt', weapon, amount, other id, range m or None, rear, hp after)]."""
    rows, info = [], {"self": None, "first_t": None, "last_t": None, "kills": [], "effects": [], "end": None,
                      "last_hp": None, "last_alive": None}
    for m in msgs:
        kind = m.get("type")
        if kind == "end":
            info["end"] = m
            continue
        if kind != "obs":
            continue
        me = m["self"]
        t = m.get("t", 0.0)
        info["self"] = me.get("id")
        info["first_t"] = t if info["first_t"] is None else info["first_t"]
        info["last_t"], info["last_hp"], info["last_alive"] = t, me.get("hp"), m.get("alive")
        pos = {o["id"]: o["pos"] for o in m.get("mechs") or []}

        def rng(other):
            p = pos.get(other)
            return None if p is None else math.hypot(p[0] - me["pos"][0], p[1] - me["pos"][1])

        for e in m.get("events") or []:
            et = e.get("type")
            if et == "damaged":
                rows.append((t, "taken", e.get("weapon") or "?", e.get("amount", 0.0), e.get("by"),
                             rng(e.get("by")), bool(e.get("rear")), me.get("hp")))
            elif et == "hit":
                rows.append((t, "dealt", e.get("weapon") or "?", e.get("amount", 0.0), e.get("target"),
                             rng(e.get("target")), bool(e.get("rear")), me.get("hp")))
            elif et == "kill":
                info["kills"].append((t, e.get("target")))
            elif et == "effect":
                info["effects"].append((t, e.get("effect"), e.get("by")))
    return rows, info


def report(rows, info, timeline=True, out=sys.stdout):
    p = lambda s="": print(s, file=out)
    end = info["end"] or {}
    p(f"mech {info['self']}: t {info['first_t']} -> {info['last_t']} s, last hp {info['last_hp']}, "
      f"alive {info['last_alive']}; end: place {end.get('place', '?')}, kills {end.get('kills', len(info['kills']))}")
    if timeline:
        p()
        p(f"{'t':>7} {'side':5} {'weapon':9} {'amount':>7} {'other':>6} {'range':>6} {'rear':4} {'hp':>7}")
        for t, side, w, a, other, d, rear, hp in rows:
            ds = "?" if d is None else f"{d:.0f}"
            p(f"{t:7.1f} {side:5} {w:9} {a:7.1f} {str(other):>6} {ds:>6} {'rear' if rear else '':4} {hp:7.1f}")
    for side in ("taken", "dealt"):
        sel = [r for r in rows if r[1] == side]
        p()
        p(f"{side}: {sum(r[3] for r in sel):.1f} in {len(sel)} hits")
        by_w = collections.defaultdict(lambda: [0.0, 0])
        by_b = collections.defaultdict(lambda: [0.0, 0])
        by_o = collections.defaultdict(lambda: [0.0, 0])
        for r in sel:
            for d, k in ((by_w, r[2]), (by_b, band(r[5])), (by_o, r[4])):
                d[k][0] += r[3]
                d[k][1] += 1
        order = [name for _, name in BANDS] + ["?"]
        p("  by weapon: " + ", ".join(f"{k} {v[0]:.0f} ({v[1]})" for k, v in sorted(by_w.items(), key=lambda kv: -kv[1][0])))
        p("  by range:  " + ", ".join(f"{k} m {by_b[k][0]:.0f} ({by_b[k][1]})" for k in order if k in by_b))
        p(f"  by {'attacker' if side == 'taken' else 'target'}: "
          + ", ".join(f"{k} {v[0]:.0f} ({v[1]})" for k, v in sorted(by_o.items(), key=lambda kv: -kv[1][0])))
        if sel:
            p(f"  rear: {sum(r[3] for r in sel if r[6]):.0f} ({sum(1 for r in sel if r[6])} hits); "
              f"first {sel[0][0]:.1f} s, last {sel[-1][0]:.1f} s")
    if info["kills"]:
        p()
        p("kills: " + ", ".join(f"{target} at {t:.1f} s" for t, target in info["kills"]))
    if info["effects"]:
        p("effects on us: " + ", ".join(f"{e} by {by} at {t:.1f} s" for t, e, by in info["effects"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stream", help=".jsonl / .jsonl.gz stream, or - for stdin")
    ap.add_argument("--summary", action="store_true", help="totals only, no per-hit timeline")
    a = ap.parse_args(argv)
    rows, info = ledger(messages(a.stream))
    report(rows, info, timeline=not a.summary)


if __name__ == "__main__":
    main()
