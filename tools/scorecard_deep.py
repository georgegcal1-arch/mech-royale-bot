"""The deeper scorecard sections for tools/analyze.py (from a play recording: obs + tools/play.py's `sent` lines).

  multi-enemy  time and damage while 2+ mechs hit us within MULTI_S, and what we did meanwhile: rushed
               (closing on them faster than MOVE_MPS), retreated (opening faster than MOVE_MPS) or held
  targeting    seconds we fired at one mech while a DIFFERENT mech was hitting us (in the last HIT_S) that was
               closer, behind us, or holding lightning within LIGHTNING_M; split by where its hits came from
  kills        time to kill per mech we killed (our first hit -> our kill); damage on mechs that got away
  LC           laser charge episodes seen (and how many aimed at us), our response in the episode + RESP_S
               (hid: lc/hide or guard; closed: lc/close; none), laser damage taken within AFTER_S of a seen charge
  death        the DEATH_S seconds before death: each hit on us (attacker, range, weapon, rear, our HP after)
"""
import collections
import math
import re

MULTI_S = 3.0
MOVE_MPS = 1.5
HIT_S = 2.0
LIGHTNING_M = 50.0
RESP_S = 1.0
AFTER_S = 2.0
DEATH_S = 10.0
AIMED_RAD = 0.35
TARGET_RE = re.compile(r"\b(weakest|attacker|isolated|nearest|threat|finish) (\d+)\b")


def is_mech(i):
    return isinstance(i, int) and 0 <= i < 100


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def _target_of(o, s):
    """The mech we were shooting at: the label's target, else the mech nearest our aim ray."""
    m = TARGET_RE.search(s.get("label") or "")
    if m:
        return int(m.group(2))
    yaw = (s["action"].get("aim") or [None])[0]
    if yaw is None:
        return None
    x, y = o["self"]["pos"][0], o["self"]["pos"][1]
    best = None
    for q in o["mechs"]:
        if q.get("kind") != "mech":
            continue
        err = abs(_wrap(math.atan2(q["pos"][1] - y, q["pos"][0] - x) - yaw))
        if err < 0.3 and (best is None or err < best[0]):
            best = (err, q["id"])
    return best[1] if best else None


def deep(msgs):
    obs = [m for m in msgs if m.get("type") == "obs"]
    if not obs:
        return None
    end = next((m for m in msgs if m.get("type") == "end"), {}) or {}
    sent = {s["action"]["tick"]: s for s in msgs if s.get("type") == "sent"}
    d = {}

    hits_on_us = []  # (t, by, amount, weapon, rear, range, hp after)
    for o in obs:
        pos = {m["id"]: m["pos"] for m in o["mechs"]}
        x, y = o["self"]["pos"][0], o["self"]["pos"][1]
        for e in o["events"]:
            if e["type"] == "damaged" and e.get("weapon") != "zone":
                p = pos.get(e.get("by"))
                hits_on_us.append((o["t"], e.get("by"), e["amount"], e.get("weapon"), bool(e.get("rear")),
                                   None if p is None else math.hypot(p[0] - x, p[1] - y), o["self"].get("hp")))
    mech_hits = [h for h in hits_on_us if is_mech(h[1])]

    def attackers(t, window):
        return {h[1] for h in mech_hits if t - window <= h[0] <= t}

    # -- multi-enemy
    multi_s, multi_dmg, did, modes = 0.0, 0.0, collections.Counter(), collections.Counter()
    for a, b in zip(obs, obs[1:]):
        att = attackers(a["t"], MULTI_S)
        if len(att) < 2:
            continue
        dt = b["t"] - a["t"]
        multi_s += dt
        multi_dmg += sum(h[2] for h in mech_hits if a["t"] < h[0] <= b["t"])
        pa = {m["id"]: m["pos"] for m in a["mechs"]}
        pb = {m["id"]: m["pos"] for m in b["mechs"]}
        ids = [i for i in att if i in pa and i in pb]
        if ids and dt > 0:
            da = sum(math.dist(a["self"]["pos"][:2], pa[i][:2]) for i in ids) / len(ids)
            db = sum(math.dist(b["self"]["pos"][:2], pb[i][:2]) for i in ids) / len(ids)
            v = (db - da) / dt
            did["rushed" if v < -MOVE_MPS else "retreated" if v > MOVE_MPS else "held"] += dt
        s = sent.get(a["tick"])
        if s:
            modes[(s.get("label") or "?").split(" ")[0]] += 1
    d.update(multi_s=multi_s, multi_dmg=multi_dmg, multi_did=dict(did), multi_modes=dict(modes.most_common(4)))

    # -- targeting
    wrong_s, wrong_front, wrong_rear = 0.0, 0.0, 0.0
    for a, b in zip(obs, obs[1:]):
        s = sent.get(a["tick"])
        if not s or not any(s["action"].get("fire") or ()):
            continue
        tgt = _target_of(a, s)
        if tgt is None:
            continue
        seen = {m["id"]: m for m in a["mechs"]}
        x, y = a["self"]["pos"][0], a["self"]["pos"][1]
        dist = lambda i: math.hypot(seen[i]["pos"][0] - x, seen[i]["pos"][1] - y) if i in seen else None
        dt_ = dist(tgt)
        bad = None
        for i in attackers(a["t"], HIT_S) - {tgt}:
            di = dist(i)
            last = max((h for h in mech_hits if h[1] == i and h[0] <= a["t"]), key=lambda h: h[0])
            lightning = i in seen and "lightning" in (seen[i].get("weapons") or ()) and di is not None and di <= LIGHTNING_M
            closer = di is not None and dt_ is not None and di < dt_
            if closer or last[4] or lightning:
                bad = last
                break
        if bad is not None:
            dt = b["t"] - a["t"]
            wrong_s += dt
            if bad[4]:
                wrong_rear += dt
            else:
                wrong_front += dt
    d.update(wrong_s=wrong_s, wrong_front_s=wrong_front, wrong_rear_s=wrong_rear)

    # -- kills
    dealt, first = collections.Counter(), {}
    for o in obs:
        for e in o["events"]:
            if e["type"] == "hit" and is_mech(e.get("target")):
                dealt[e["target"]] += e["amount"]
                first.setdefault(e["target"], o["t"])
    killed = {e["target"]: o["t"] for o in obs for e in o["events"] if e["type"] == "kill"}
    d["ttk"] = {i: killed[i] - first[i] for i in killed if i in first}
    d["wasted"] = {i: v for i, v in dealt.items() if i not in killed}
    d["wasted_dmg"] = sum(d["wasted"].values())

    # -- LC: laser charge episodes
    episodes, open_ = [], {}
    for o in obs:
        now = set()
        x, y = o["self"]["pos"][0], o["self"]["pos"][1]
        for m in o["mechs"]:
            if m.get("kind") == "mech" and m.get("charging"):
                now.add(m["id"])
                if m["id"] not in open_:
                    aim = m.get("aim")
                    at_us = aim is not None and abs(_wrap(aim[0] - math.atan2(y - m["pos"][1], x - m["pos"][0]))) <= AIMED_RAD
                    open_[m["id"]] = {"id": m["id"], "t0": o["t"], "t1": o["t"], "at_us": at_us}
                    episodes.append(open_[m["id"]])
                open_[m["id"]]["t1"] = o["t"]
        for i in list(open_):
            if i not in now:
                del open_[i]
    resp = collections.Counter()
    lc_dmg = 0.0
    for ep in episodes:
        labels = [sent[o["tick"]].get("label") or "" for o in obs
                  if ep["t0"] <= o["t"] <= ep["t1"] + RESP_S and o["tick"] in sent]
        if any(l.startswith("lc/close") for l in labels):
            ep["resp"] = "closed"
        elif any(l.startswith(("lc/hide", "guard")) for l in labels):
            ep["resp"] = "hid"
        else:
            ep["resp"] = "none"
        resp[ep["resp"]] += 1
        ep["dmg"] = sum(h[2] for h in mech_hits
                        if h[1] == ep["id"] and h[3] == "laser" and ep["t0"] <= h[0] <= ep["t1"] + AFTER_S)
        lc_dmg += ep["dmg"]
    d.update(lc_charges=len(episodes), lc_at_us=sum(1 for e in episodes if e["at_us"]), lc_resp=dict(resp),
             lc_dmg=lc_dmg, lc_triggers=sum(1 for s in sent.values() if (s.get("label") or "").startswith("lc/")))

    # -- death timeline
    died = (end.get("place") or 1) > 1 and obs[-1]["self"].get("hp", 1) is not None
    t_end = obs[-1]["t"]
    d["died"] = died
    d["death_tl"] = [h for h in hits_on_us if t_end - h[0] <= DEATH_S] if died else []
    d["t_end"] = t_end
    return d


def report(d, p):
    p("== scorecard (deep) ==")
    did = ", ".join(f"{k} {v:.1f} s" for k, v in sorted(d["multi_did"].items(), key=lambda kv: -kv[1])) or "-"
    p(f"  multi-enemy: {d['multi_s']:.1f} s with 2+ mechs hitting us, {d['multi_dmg']:.0f} dmg taken; we {did};"
      f" modes {d['multi_modes']}")
    p(f"  targeting:   {d['wrong_s']:.1f} s firing at one mech while another (closer / behind / lightning) hit us"
      f" (its hits from the front {d['wrong_front_s']:.1f} s, from behind {d['wrong_rear_s']:.1f} s)")
    ttk = ", ".join(f"{i}: {v:.1f} s" for i, v in sorted(d["ttk"].items())) or "-"
    waste = ", ".join(f"{i}: {v:.0f}" for i, v in sorted(d["wasted"].items(), key=lambda kv: -kv[1])) or "-"
    p(f"  kills:       time to kill {ttk}; damage on mechs that got away {d['wasted_dmg']:.0f} ({waste})")
    p(f"  LC:          {d['lc_charges']} laser charges seen ({d['lc_at_us']} aimed at us); response {d['lc_resp'] or '-'};"
      f" laser dmg after a seen charge {d['lc_dmg']:.0f}; lc/ decisions {d['lc_triggers']}")
    if d["died"]:
        p(f"  death:       the last {DEATH_S:.0f} s (t-rel, attacker, range, weapon, rear, our hp after):")
        for t, by, amt, w, rear, rng, hp in d["death_tl"]:
            r = f"{rng:.0f} m" if rng is not None else "?"
            p(f"    {t - d['t_end']:+5.1f} s  {by!s:>4}  {r:>5}  {w or '?':<9} {amt:4.0f}{' REAR' if rear else '     '}  hp {hp:.0f}")


def table(rows, p):
    """One markdown row per match: [(name, setup, deep)]."""
    p("| match | setup | 2+ mechs s/dmg | rushed/held/retreated s | wrong target s (rear) | TTKs | wasted dmg | "
      "laser charges (at us) | hid/closed/none | laser dmg after charge |")
    p("|" + "---|" * 10)
    for name, setup, d in rows:
        md = d["multi_did"]
        r = d["lc_resp"]
        ttk = " ".join(f"{v:.1f}" for v in d["ttk"].values()) or "-"
        p(f"| {name} | {setup} | {d['multi_s']:.1f}/{d['multi_dmg']:.0f} | {md.get('rushed', 0):.1f}/{md.get('held', 0):.1f}/"
          f"{md.get('retreated', 0):.1f} | {d['wrong_s']:.1f} ({d['wrong_rear_s']:.1f}) | {ttk} | {d['wasted_dmg']:.0f} | "
          f"{d['lc_charges']} ({d['lc_at_us']}) | {r.get('hid', 0)}/{r.get('closed', 0)}/{r.get('none', 0)} | {d['lc_dmg']:.0f} |")
