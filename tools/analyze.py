"""Analyze an arena match: our place, kills, damage taken/dealt by weapon, range and rear, and who killed us.

    python tools/analyze.py recordings/ABCDE-20261121-230000-play.jsonl         # + the -live.jsonl beside it
    python tools/analyze.py PLAY.jsonl --live LIVE.jsonl
    python tools/analyze.py LIVE.jsonl --me 7                                    # a spectator recording only

From the play recording (tools/play.py: exact mechfight/1 obs, as our bot saw them):
  place and kills (`end`); damage taken and dealt by weapon, range band and rear (tools/damage_ledger.py);
  how we died: every attacker in our last DEATH_WINDOW_S with weapon, hits, range and rear, our HP into death,
  and the threats in sight; our answer times (obs receive -> action sent).
From the live recording (tools/record.py: the spectator feed), whole-match context:
  the death order with who killed whom, everyone's kills, hits on us by attacker and weapon, and how many
  mechs were hitting us in each minute (house bots hunt as a squad). It reads `ev` events (hit
  {src,dst,w,amt,rear}, death {id,place,by}) and tolerates fields it doesn't know.
"""
import argparse
import collections
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import damage_ledger as L  # noqa: E402

DEATH_WINDOW_S = 10.0
EVENT_KIND_KEYS = ("type", "e", "k", "kind", "ev", "t")


def load(path):
    return list(L.messages(path))


# ---------------------------------------------------------------- the play recording (mechfight/1)
def play_report(msgs, p):
    hello = next((m for m in msgs if m.get("type") == "hello"), None)
    obs = [m for m in msgs if m.get("type") == "obs"]
    if not obs:
        p("play recording: no observations")
        return None
    rows, info = L.ledger(msgs)
    me = obs[0]["self"]["id"]
    cfg = (hello or {}).get("config") or {}
    phases = (cfg.get("zone") or {}).get("phases") or []
    p(f"== our match (mech {me}, slot {(hello or {}).get('slot')}) ==")
    p(f"config: match {(cfg.get('match') or {}).get('duration_s')} s, zone closes at "
      f"{sum(x.get('hold_s', 0) + x.get('shrink_s', 0) for x in phases):.0f} s")
    L.report(rows, info, timeline=False, out=_Out(p))

    # how we died
    end = info.get("end") or {}
    last_t = obs[-1]["t"]
    died = obs[-1]["self"].get("hp", 1) <= 60 or end.get("place", 1) > 1
    p("")
    p(f"== how it ended (last obs t={last_t:.1f} s, hp {obs[-1]['self'].get('hp'):.0f}, alive {obs[-1].get('alive')}) ==")
    window = [r for r in rows if r[1] == "taken" and r[0] >= last_t - DEATH_WINDOW_S]
    by = collections.defaultdict(lambda: {"amt": 0.0, "n": 0, "w": collections.Counter(), "rng": [], "rear": 0})
    for t, _, w, a, other, d, rear, _ in window:
        e = by[other]
        e["amt"] += a
        e["n"] += 1
        e["w"][w] += 1
        e["rear"] += rear
        if d is not None:
            e["rng"].append(d)
    if window:
        for other, e in sorted(by.items(), key=lambda kv: -kv[1]["amt"]):
            rng = f"{min(e['rng']):.0f}-{max(e['rng']):.0f} m" if e["rng"] else "range ?"
            p(f"  attacker {other}: {e['amt']:.0f} dmg in {e['n']} hits ({', '.join(f'{k} x{v}' for k, v in e['w'].items())}), "
              f"{rng}, rear {e['rear']}")
        killer = window[-1]
        p(f"  last hit: {killer[2]} {killer[3]:.0f} from {killer[4]} at "
          f"{'?' if killer[5] is None else f'{killer[5]:.0f} m'}{' (rear)' if killer[6] else ''}, t={killer[0]:.1f} s")
    else:
        p(f"  no damage in the last {DEATH_WINDOW_S:.0f} s" + ("" if died else " (survived?)"))
    trace = []
    for back in (DEATH_WINDOW_S, 5.0, 2.0, 0.0):
        o = min(obs, key=lambda o: abs(o["t"] - (last_t - back)))
        trace.append(f"-{back:.0f}s {o['self'].get('hp', 0):.0f}")
    p("  our hp: " + ", ".join(trace))
    o = obs[-1]
    seen = [(m["id"], round(math.dist(m["pos"][:2], o["self"]["pos"][:2])), m.get("weapons"), m.get("hp"))
            for m in o.get("mechs") or [] if m.get("kind") == "mech"]
    p(f"  in sight at the end: {seen or 'nobody'}")
    p(f"  weapons at the end: {[w and w.get('kind') for w in o['self'].get('weapons') or []]}, "
      f"cubes {o['self'].get('cubes')}, energy {o['self'].get('energy')}")

    # answer times
    rx = {m["tick"]: m["_rx"] for m in obs if "_rx" in m}
    lat = [(s["at"] - rx[s["action"]["tick"]]) * 1000 for s in msgs
           if s.get("type") == "sent" and s.get("action", {}).get("tick") in rx]
    if lat:
        lat.sort()
        p(f"  answers: {len(lat)} for {len(obs)} obs; obs->action ms p50 {lat[len(lat) // 2]:.1f}, "
          f"p99 {lat[int(.99 * (len(lat) - 1))]:.1f}, max {lat[-1]:.1f}; over 50 ms: {sum(x > 50 for x in lat)}")
    return me


class _Out:
    """A file-like wrapper so damage_ledger.report prints through p()."""

    def __init__(self, p):
        self.p, self.buf = p, ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.p(line)

    def flush(self):
        pass


# ---------------------------------------------------------------- the live recording (spectator feed)
def _kind(ev):
    if isinstance(ev, dict):
        for k in EVENT_KIND_KEYS:
            v = ev.get(k)
            if isinstance(v, str):
                return v
    if isinstance(ev, (list, tuple)) and ev and isinstance(ev[0], str):
        return ev[0]
    return None


def _fields(ev):
    if isinstance(ev, dict):
        return ev
    if isinstance(ev, (list, tuple)):
        for x in ev[1:]:
            if isinstance(x, dict):
                return x
    return {}


def _names(header):
    """{id: name} if the header lists players."""
    out = {}

    def walk(v):
        if isinstance(v, dict):
            if "id" in v and isinstance(v.get("name"), str):
                out[v["id"]] = v["name"]
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(header or {})
    return out


def live_events(msgs):
    """(header, [(t, kind, fields)]) from a spectator recording."""
    header = next((m for m in msgs if isinstance(m, dict) and m.get("type") == "header"), None)
    evs, t = [], 0.0
    for m in msgs:
        if not isinstance(m, dict):
            continue
        for k in ("t", "time", "tick"):
            if isinstance(m.get(k), (int, float)):
                t = m[k] / 20.0 if k == "tick" else m[k]
                break
        for ev in m.get("ev") or []:
            evs.append((t, _kind(ev), _fields(ev)))
    return header, evs


def live_report(msgs, me, p, name=None):
    header, evs = live_events(msgs)
    names = _names(header)
    if me is None and name is not None:
        me = next((i for i, n in names.items() if n == name), None)
    label = lambda i: f"{i} {names[i]}" if i in names else str(i)
    p("")
    p(f"== whole match (spectator feed: {len(msgs)} lines, {len(evs)} events, "
      f"kinds {dict(collections.Counter(k for _, k, _ in evs))}) ==")
    deaths = sorted(((f.get("place", 99), t, f.get("id"), f.get("by")) for t, k, f in evs if k == "death"),
                    key=lambda d: (d[0], -d[1]))
    kills = collections.Counter(by for _, _, _, by in deaths if by is not None)
    if deaths:
        p("death order (place: mech, killed by, at):")
        for place, t, i, by in sorted(deaths, key=lambda d: -d[0]):
            mark = "  <- us" if i == me else ""
            p(f"  {place:>2}: {label(i)}, by {label(by) if by is not None else 'zone/?'}, t={t:.1f}{mark}")
        p("kills: " + ", ".join(f"{label(i)} {n}" for i, n in kills.most_common()))
    hits = [(t, f) for t, k, f in evs if k == "hit"]
    if me is not None and hits:
        on_us = [(t, f) for t, f in hits if f.get("dst") == me]
        ours = [(t, f) for t, f in hits if f.get("src") == me]
        agg = collections.defaultdict(lambda: collections.Counter())
        for _, f in on_us:
            agg[f.get("src")][f.get("w")] += f.get("amt", 0) or 0
        p(f"hits on us: {len(on_us)}, {sum((f.get('amt') or 0) for _, f in on_us):.0f} dmg, rear "
          f"{sum(1 for _, f in on_us if f.get('rear'))}")
        for src, ws in sorted(agg.items(), key=lambda kv: -sum(kv[1].values())):
            p(f"  from {label(src)}: " + ", ".join(f"{w} {a:.0f}" for w, a in ws.items()))
        p(f"our hits: {len(ours)}, {sum((f.get('amt') or 0) for _, f in ours):.0f} dmg on "
          f"{sorted({label(f.get('dst')) for _, f in ours})}")
        per_min = collections.defaultdict(set)
        for t, f in on_us:
            per_min[int(t // 60)].add(f.get("src"))
        if per_min:
            p("mechs hitting us per minute: " + ", ".join(f"{m}:{len(s)}" for m, s in sorted(per_min.items())))
    elif me is None:
        p("(our mech id unknown: pass --me ID or --name NAME for the hits on us)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("recording", help="a -play.jsonl or -live.jsonl recording")
    ap.add_argument("--live", default=None, help="the spectator recording (default: the -live.jsonl beside a -play one)")
    ap.add_argument("--me", type=int, default=None)
    ap.add_argument("--name", default=None)
    a = ap.parse_args(argv)
    p = print
    msgs = load(a.recording)
    me = a.me
    live_path = a.live
    if any(m.get("type") in ("hello", "obs") for m in msgs if isinstance(m, dict)):
        got = play_report(msgs, p)
        me = me if me is not None else got
        if live_path is None and a.recording.endswith("-play.jsonl"):
            cand = a.recording[: -len("-play.jsonl")] + "-live.jsonl"
            live_path = cand if os.path.exists(cand) else None
        live = load(live_path) if live_path else None
    else:
        live = msgs
    if live:
        live_report(live, me, p, a.name)
    elif live_path is None:
        p("\n(no spectator recording: run tools/record.py alongside for the whole-match view)")


if __name__ == "__main__":
    main()
