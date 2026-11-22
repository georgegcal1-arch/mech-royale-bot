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
import scorecard as SC  # noqa: E402
import scorecard_deep as SD  # noqa: E402

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

    # what the bot was doing (labels recorded by tools/play.py)
    labels = [s.get("label") or "" for s in msgs if s.get("type") == "sent" and "label" in s]
    if any(labels):
        modes = collections.Counter(lab.split(" ")[0] for lab in labels if lab)
        p("  modes: " + ", ".join(f"{k} {v}" for k, v in modes.most_common(12)))
        tags = {"npc self-defense": " npc ", "npc farm": " farm ", "npc avoid": "/npc ", "laser short": " short ",
                "crate": " crate ", "unstick jet": "unstick/jet ", "unstick over": "unstick/over ",
                "unstick slide": "unstick/slide ", "route": " route "}
        p("  triggers: " + ", ".join(f"{k} {sum(t in lab + ' ' for lab in labels)}" for k, t in tags.items()))

    movement_report(obs, msgs, p)

    # answer times
    rx = {m["tick"]: m["_rx"] for m in obs if "_rx" in m}
    lat = [(s["at"] - rx[s["action"]["tick"]]) * 1000 for s in msgs
           if s.get("type") == "sent" and s.get("action", {}).get("tick") in rx]
    if lat:
        lat.sort()
        p(f"  answers: {len(lat)} for {len(obs)} obs; obs->action ms p50 {lat[len(lat) // 2]:.1f}, "
          f"p99 {lat[int(.99 * (len(lat) - 1))]:.1f}, max {lat[-1]:.1f}; over 50 ms: {sum(x > 50 for x in lat)}")
    return me


STUCK_MIN_S, STUCK_MAX_DISP_M, CRAWL_MPS = 1.5, 2.0, 3.0


def movement_report(obs, msgs, p):
    """Time stuck (legs asking to move, < 2 m in 1.5 s+), time crawling (a full-speed command, < 3 m/s, not
    charging a laser), and zone damage with its stretches."""
    moves = {s["action"]["tick"]: s["action"].get("move") or [0, 0] for s in msgs if s.get("type") == "sent"}
    if not moves:
        return
    stuck, i = [], 0
    while i < len(obs):
        if math.hypot(*moves.get(obs[i]["tick"], [0, 0])) < 0.5:
            i += 1
            continue
        p0, j = obs[i]["self"]["pos"], i
        while (j + 1 < len(obs) and math.hypot(*moves.get(obs[j + 1]["tick"], [0, 0])) >= 0.5
               and math.dist(obs[j + 1]["self"]["pos"][:2], p0[:2]) < STUCK_MAX_DISP_M):
            j += 1
        if obs[j]["t"] - obs[i]["t"] >= STUCK_MIN_S:
            stuck.append((obs[i]["t"], obs[j]["t"] - obs[i]["t"], [round(v) for v in p0]))
        i = j + 1
    crawl = 0.0
    for a, b in zip(obs, obs[1:]):
        if math.hypot(*moves.get(a["tick"], [0, 0])) < 0.9:
            continue
        lw = next((w for w in a["self"]["weapons"] if w and w["kind"] == "laser"), None)
        if lw and (lw.get("charge") or 0) > 0:
            continue
        dt = b["t"] - a["t"]
        if dt > 0 and math.dist(a["self"]["pos"][:2], b["self"]["pos"][:2]) / dt < CRAWL_MPS:
            crawl += min(dt, 0.5)
    alive = max(obs[-1]["t"], 1e-6)
    zone = [(o["t"], e["amount"]) for o in obs for e in o["events"] if e["type"] == "damaged" and e.get("weapon") == "zone"]
    p(f"  movement: stuck {sum(s[1] for s in stuck):.1f} s in {len(stuck)} stretches "
      f"({100 * sum(s[1] for s in stuck) / alive:.0f}% of time alive), crawling {crawl:.1f} s ({100 * crawl / alive:.0f}%)")
    for t, d, pos in stuck:
        p(f"    stuck t={t:.1f} for {d:.1f} s at {pos}")
    if zone:
        p(f"  zone damage: {sum(a for _, a in zone):.0f} from t={zone[0][0]:.1f} to {zone[-1][0]:.1f} s")
    else:
        p("  zone damage: none")


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
    """(header, [(t seconds, kind, fields)]) from a spectator recording (mechreplay/1: frame and event `t`
    are ticks at header.tick_hz; events are keyed by `e`)."""
    header = next((m for m in msgs if isinstance(m, dict) and m.get("type") == "header"), None)
    hz = float((header or {}).get("tick_hz") or 20.0)
    evs, t = [], 0.0
    for m in msgs:
        if not isinstance(m, dict) or m.get("type") in ("header", "channel"):
            continue
        if isinstance(m.get("t"), (int, float)):
            t = m["t"] / hz
        for ev in m.get("ev") or []:
            et = ev.get("t") if isinstance(ev, dict) else None
            evs.append((et / hz if isinstance(et, (int, float)) else t, _kind(ev), _fields(ev)))
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
    is_mech = lambda i: isinstance(i, int) and 0 <= i < 100  # protocol.md: mechs 0-15, NPCs 100+
    npc_deaths = [(t, f.get("id"), f.get("by")) for t, k, f in evs if k == "death" and not is_mech(f.get("id"))]
    deaths = sorted(((f.get("place", 99), t, f.get("id"), f.get("by")) for t, k, f in evs
                     if k == "death" and is_mech(f.get("id"))), key=lambda d: (d[0], -d[1]))
    end = next((f for _, k, f in evs if k == "end" and isinstance(f.get("places"), list)), None)
    if end:  # places indexed by mech id; survivors have no death event
        dead = {d[2] for d in deaths}
        ranked = sorted(enumerate(end["places"]), key=lambda x: x[1])
        alive = [label(i) for i in range(len(end["places"])) if i not in dead]
        p("final places: " + ", ".join(f"{label(i)}={pl}" for i, pl in ranked) + f"  (survivors: {alive})")
    kills = collections.Counter(by for _, _, _, by in deaths if by is not None)
    if deaths:
        p("death order (place: mech, killed by, at):")
        for place, t, i, by in sorted(deaths, key=lambda d: -d[0]):
            mark = "  <- us" if i == me else ""
            p(f"  {place:>2}: {label(i)}, by {label(by) if by is not None else 'zone/?'}, t={t:.1f}{mark}")
        p("kills: " + ", ".join(f"{label(i)} {n}" for i, n in kills.most_common()))
    if npc_deaths:
        p("drones killed: " + ", ".join(f"{i} by {label(by)} at {t:.1f}" for t, i, by in sorted(npc_deaths)))
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
            p("attackers hitting us per minute (mechs + drones): "
              + ", ".join(f"min {m}: {len(s)} ({sum(map(is_mech, s))} mechs)" for m, s in sorted(per_min.items())))
    elif me is None:
        p("(our mech id unknown: pass --me ID or --name NAME for the hits on us)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("recording", nargs="+", help="a -play.jsonl or -live.jsonl recording (with --table: several)")
    ap.add_argument("--table", action="store_true", help="one scorecard table row per play recording")
    ap.add_argument("--live", default=None, help="the spectator recording (default: the -live.jsonl beside a -play "
                                                     "one; 'none' to skip it)")
    ap.add_argument("--me", type=int, default=None)
    ap.add_argument("--name", default=None)
    a = ap.parse_args(argv)
    p = print
    if a.table:
        rows = []
        for path in a.recording:
            c = SC.scorecard(load(path))
            if c is not None:
                rows.append((os.path.basename(path).replace("-play.jsonl", ""), SC.setup_of(path), c))
        SC.table(rows, p)
        p("")
        SD.table([(n, s, SD.deep(load(path))) for (n, s, _), path in zip(rows, [x for x in a.recording
                  if SC.scorecard(load(x)) is not None])], p)
        return
    a.recording = a.recording[0]
    msgs = load(a.recording)
    me = a.me
    live_path = a.live
    if any(m.get("type") in ("hello", "obs") for m in msgs if isinstance(m, dict)):
        got = play_report(msgs, p)
        me = me if me is not None else got
        c = SC.scorecard(msgs)
        if c is not None:
            p("")
            SC.report(c, p)
            SD.report(SD.deep(msgs), p)
        if live_path is None and a.recording.endswith("-play.jsonl"):
            cand = a.recording[: -len("-play.jsonl")] + "-live.jsonl"
            live_path = cand if os.path.exists(cand) else None
        live = load(live_path) if live_path and live_path.lower() != "none" else None
    else:
        live = msgs
    if live:
        live_report(live, me, p, a.name)
    elif live_path is None or live_path.lower() != "none":
        p("\n(no spectator recording: run tools/record.py alongside for the whole-match view)")


if __name__ == "__main__":
    main()
