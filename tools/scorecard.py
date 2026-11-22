"""The match scorecard for tools/analyze.py: navigation, looting, fighting and defense from a play recording.

All from what the bot saw (mechfight/1 obs) and did (tools/play.py's `sent` lines):
  navigation  stuck (moving asked, < 2 m in 1.5 s+), crawling (full speed asked, < 3 m/s), zone damage
  looting     cubes that came within LOOT_NEAR_M vs cubes picked up (`pickup` events, + supply-drop captures);
              better weapons within LOOT_NEAR_M (ones loot.py's take rules would take, given what we held then)
              vs weapons taken (`took`); cubes held at each minute
  fighting    shots per weapon (gun: ammo spent; laser / lightning / mortar: a cooldown restarting), hits per
              weapon (`hit` events; lightning counts once per shot that hit, chains aside), hit rate;
              engagements (mech-on-us or us-on-mech damage, split by ENGAGE_GAP_S of quiet): dealt vs taken,
              kills and time to kill (first hit on that mech -> `kill`), and fights lost with why (the weapon,
              attackers, rear share and range of the damage, our weapons and HP going in)
  defense     damage from behind (`rear`); damage while 2+ mechs hit us within MULTI_S; laser damage taken,
              and how much of it came from a shooter we had seen charging in the CHARGE_SEEN_S before
              ("in the open": a dodgeable beam we didn't get out of)
"""
import collections
import math
import os
import statistics

LOOT_NEAR_M = 30.0
ENGAGE_GAP_S = 8.0
MULTI_S = 3.0
CHARGE_SEEN_S = 1.5
TAKE_ORDER = ("laser", "lightning", "mortar")  # loot.py with features.LIGHTNING


def is_mech(i):
    return isinstance(i, int) and 0 <= i < 100


def _would_take(kind, weapons):
    """loot.py's _take_loot_lightning: would we take a pickup of this kind holding `weapons`?"""
    slot = {w["kind"] for w in weapons if w}
    if kind not in TAKE_ORDER or kind in slot:
        return False
    if None in weapons:
        return True
    if kind == "mortar":
        return False
    other = "lightning" if kind == "laser" else "laser"
    return "mortar" in slot or (other in slot and "gun" in slot)


def _shots(a, b):
    """Counter of shots per weapon kind between two consecutive observations."""
    out = collections.Counter()
    wa = {w["kind"]: w for w in a["self"]["weapons"] if w}
    for w in b["self"]["weapons"]:
        if not w or w["kind"] not in wa:
            continue
        p, k = wa[w["kind"]], w["kind"]
        if k == "gun":
            if (w.get("ammo") or 0) < (p.get("ammo") or 0):
                out[k] += (p.get("ammo") or 0) - (w.get("ammo") or 0)
        elif (w.get("cooldown") or 0) > (p.get("cooldown") or 0) + 0.02:
            out[k] += 1
    return out


def scorecard(msgs):
    obs = [m for m in msgs if m.get("type") == "obs"]
    if not obs:
        return None
    end = next((m for m in msgs if m.get("type") == "end"), {}) or {}
    sent = {s["action"]["tick"]: s for s in msgs if s.get("type") == "sent"}
    me = obs[0]["self"]["id"]
    t_end = obs[-1]["t"]
    c = {"place": end.get("place"), "kills": end.get("kills"), "alive_s": t_end}

    # -- navigation
    moves = {k: (s["action"].get("move") or [0, 0]) for k, s in sent.items()}
    stuck, i = 0.0, 0
    while i < len(obs):
        if math.hypot(*moves.get(obs[i]["tick"], [0, 0])) < 0.5:
            i += 1
            continue
        p0, j = obs[i]["self"]["pos"], i
        while (j + 1 < len(obs) and math.hypot(*moves.get(obs[j + 1]["tick"], [0, 0])) >= 0.5
               and math.dist(obs[j + 1]["self"]["pos"][:2], p0[:2]) < 2.0):
            j += 1
        if obs[j]["t"] - obs[i]["t"] >= 1.5:
            stuck += obs[j]["t"] - obs[i]["t"]
        i = j + 1
    crawl = 0.0
    for a, b in zip(obs, obs[1:]):
        if math.hypot(*moves.get(a["tick"], [0, 0])) < 0.9:
            continue
        lw = next((w for w in a["self"]["weapons"] if w and w["kind"] == "laser"), None)
        if lw and (lw.get("charge") or 0) > 0:
            continue
        dt = b["t"] - a["t"]
        if dt > 0 and math.dist(a["self"]["pos"][:2], b["self"]["pos"][:2]) / dt < 3.0:
            crawl += min(dt, 0.5)
    c.update(stuck_s=stuck, crawl_pct=100 * crawl / max(t_end, 1e-6),
             zone_dmg=sum(e["amount"] for o in obs for e in o["events"] if e["type"] == "damaged" and e.get("weapon") == "zone"))

    # -- looting
    cubes_near, better_near = set(), set()
    for o in obs:
        x, y = o["self"]["pos"][0], o["self"]["pos"][1]
        for q in o.get("cubes") or []:
            if math.hypot(q["pos"][0] - x, q["pos"][1] - y) <= LOOT_NEAR_M:
                cubes_near.add(q["id"])
        for q in o.get("pickups") or []:
            if (math.hypot(q["pos"][0] - x, q["pos"][1] - y) <= LOOT_NEAR_M
                    and _would_take(q["kind"], o["self"]["weapons"])):
                better_near.add(q["id"])
    picked = sum(1 for o in obs for e in o["events"] if e["type"] == "pickup")
    drops = sum(1 for o in obs for e in o["events"] if e["type"] == "drop")
    took = [e["kind"] for o in obs for e in o["events"] if e["type"] == "took"]
    held = []
    for minute in range(1, int(t_end // 60) + 1):
        o = min(obs, key=lambda o: abs(o["t"] - 60 * minute))
        held.append(o["self"].get("cubes") or 0)
    c.update(cubes_near=len(cubes_near), cubes_picked=picked, drops_captured=drops, better_near=len(better_near),
             weapons_taken=len(took), took=took, cubes_by_min=held, cubes_end=obs[-1]["self"].get("cubes") or 0)

    # -- fighting: shots and hits
    shots, hits = collections.Counter(), collections.Counter()
    for a, b in zip(obs, obs[1:]):
        shots += _shots(a, b)
    for o in obs:
        kinds_hit = collections.Counter(e.get("weapon") for e in o["events"] if e["type"] == "hit")
        for k, n in kinds_hit.items():
            hits[k] += 1 if k == "lightning" else n
    c["shots"], c["hits"] = dict(shots), dict(hits)
    c["hit_rate"] = {k: hits.get(k, 0) / shots[k] for k in shots if shots[k]}

    # -- engagements
    evs = []  # (t, kind 'dealt'|'taken', amount, other, weapon, rear, range or None)
    for o in obs:
        pos = {m["id"]: m["pos"] for m in o["mechs"]}
        x, y = o["self"]["pos"][0], o["self"]["pos"][1]
        for e in o["events"]:
            if e["type"] == "hit" and is_mech(e.get("target")):
                p = pos.get(e["target"])
                evs.append((o["t"], "dealt", e["amount"], e["target"], e.get("weapon"), bool(e.get("rear")),
                            None if p is None else math.hypot(p[0] - x, p[1] - y)))
            elif e["type"] == "damaged" and is_mech(e.get("by")):
                p = pos.get(e["by"])
                evs.append((o["t"], "taken", e["amount"], e["by"], e.get("weapon"), bool(e.get("rear")),
                            None if p is None else math.hypot(p[0] - x, p[1] - y)))
    kills = [(o["t"], e["target"]) for o in obs for e in o["events"] if e["type"] == "kill"]
    died = (end.get("place") or 1) > 1
    fights = []
    for ev in evs:
        if not fights or ev[0] - fights[-1]["t1"] > ENGAGE_GAP_S:
            fights.append({"t0": ev[0], "t1": ev[0], "evs": []})
        fights[-1]["t1"] = ev[0]
        fights[-1]["evs"].append(ev)
    ttk = []
    for f in fights:
        f["dealt"] = sum(e[2] for e in f["evs"] if e[1] == "dealt")
        f["taken"] = sum(e[2] for e in f["evs"] if e[1] == "taken")
        f["foes"] = sorted({e[3] for e in f["evs"]})
        f["kills"] = [k for k in kills if f["t0"] - 1 <= k[0] <= f["t1"] + 2]
        for kt, target in f["kills"]:
            first = next((e[0] for e in f["evs"] if e[1] == "dealt" and e[3] == target), None)
            if first is not None:
                ttk.append(kt - first)
        o0 = min(obs, key=lambda o: abs(o["t"] - f["t0"]))
        f["hp0"] = o0["self"].get("hp")
        f["weapons"] = [w["kind"] for w in o0["self"]["weapons"] if w]
        f["died"] = died and f is fights[-1] and t_end - f["t1"] <= 3.0
        f["lost"] = f["died"] or (not f["kills"] and f["taken"] > 1.5 * max(f["dealt"], 1.0) and f["taken"] >= 300)
        if f["lost"]:
            tk = [e for e in f["evs"] if e[1] == "taken"]
            by_w = collections.Counter()
            for e in tk:
                by_w[e[4]] += e[2]
            rngs = [e[6] for e in tk if e[6] is not None]
            f["why"] = {"weapon": by_w.most_common(1)[0][0] if by_w else "?",
                        "attackers": len({e[3] for e in tk}),
                        "rear_pct": 100 * sum(e[2] for e in tk if e[5]) / max(f["taken"], 1.0),
                        "range_m": statistics.median(rngs) if rngs else None}
    c["fights"] = fights
    c["fights_won"] = sum(1 for f in fights if f["kills"])
    c["fights_lost"] = sum(1 for f in fights if f["lost"])
    c["ttk_median_s"] = statistics.median(ttk) if ttk else None
    c["dealt_mechs"] = sum(e[2] for e in evs if e[1] == "dealt")
    c["taken_mechs"] = sum(e[2] for e in evs if e[1] == "taken")

    # -- defense
    taken_all = [(o["t"], e) for o in obs for e in o["events"] if e["type"] == "damaged" and e.get("weapon") != "zone"]
    total = sum(e["amount"] for _, e in taken_all) or 1.0
    rear = sum(e["amount"] for _, e in taken_all if e.get("rear"))
    mech_hits = [(t, e["by"]) for t, e in taken_all if is_mech(e.get("by"))]
    multi = 0.0
    for t, e in taken_all:
        if is_mech(e.get("by")) and len({b for ht, b in mech_hits if abs(ht - t) <= MULTI_S}) >= 2:
            multi += e["amount"]
    seen_charging = collections.defaultdict(list)  # mech id -> times we saw it charging
    for o in obs:
        for m in o["mechs"]:
            if m.get("charging"):
                seen_charging[m["id"]].append(o["t"])
    laser = [(t, e) for t, e in taken_all if e.get("weapon") == "laser"]
    laser_seen = sum(e["amount"] for t, e in laser
                     if any(t - CHARGE_SEEN_S <= ct <= t for ct in seen_charging.get(e.get("by"), ())))
    c.update(taken_total=total if taken_all else 0.0, rear_dmg=rear, rear_pct=100 * rear / total,
             multi_dmg=multi, multi_pct=100 * multi / total, laser_dmg=sum(e["amount"] for _, e in laser),
             laser_hits=len(laser), laser_seen_charging_dmg=laser_seen)
    return c


def setup_of(play_path):
    """The bot's switches from the -bot.log beside a play recording (tools/run_bot.py's first line)."""
    log = play_path.replace("-play.jsonl", "-bot.log")
    if os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("run_bot: v9 switches on:"):
                    return line.split(":", 2)[2].split(";")[0].strip()
    return "?"


def report(c, p):
    """The scorecard sections for one match."""
    rate = lambda k: f"{100 * c['hit_rate'][k]:.0f}% ({c['hits'].get(k, 0)}/{c['shots'][k]})" if k in c["hit_rate"] else "-"
    p("== scorecard ==")
    p(f"  navigation: stuck {c['stuck_s']:.1f} s, crawling {c['crawl_pct']:.0f}%, zone damage {c['zone_dmg']:.0f}")
    p(f"  looting:    cubes within {LOOT_NEAR_M:.0f} m {c['cubes_near']}, picked up {c['cubes_picked']}"
      f" (+{c['drops_captured']} supply drops); better weapons within {LOOT_NEAR_M:.0f} m {c['better_near']}, "
      f"taken {c['weapons_taken']} {c['took']}; cubes by minute {c['cubes_by_min']}, at the end {c['cubes_end']}")
    p(f"  fighting:   hit rate gun {rate('gun')}, laser {rate('laser')}, lightning {rate('lightning')}"
      + (f", mortar {rate('mortar')}" if 'mortar' in c['shots'] else ""))
    p(f"              vs mechs: dealt {c['dealt_mechs']:.0f}, taken {c['taken_mechs']:.0f}; {len(c['fights'])} fights, "
      f"{c['fights_won']} won (a kill), {c['fights_lost']} lost; time to kill median "
      + (f"{c['ttk_median_s']:.1f} s" if c["ttk_median_s"] is not None else "-"))
    for f in c["fights"]:
        tag = "WON" if f["kills"] else ("LOST" if f["lost"] else "even")
        why = ""
        if f["lost"]:
            w = f["why"]
            rng = f"{w['range_m']:.0f} m" if w["range_m"] is not None else "?"
            why = (f" | why: {w['weapon']}, {w['attackers']} attacker(s), rear {w['rear_pct']:.0f}%, median range {rng},"
                   f" we held {f['weapons']} at hp {f['hp0']:.0f}{' -> died' if f['died'] else ''}")
        p(f"    t={f['t0']:.0f}-{f['t1']:.0f} s vs {f['foes']}: dealt {f['dealt']:.0f}, taken {f['taken']:.0f} [{tag}]{why}")
    p(f"  defense:    from behind {c['rear_dmg']:.0f} ({c['rear_pct']:.0f}%), while 2+ mechs hit us {c['multi_dmg']:.0f} "
      f"({c['multi_pct']:.0f}%), laser {c['laser_dmg']:.0f} in {c['laser_hits']} hits, of which {c['laser_seen_charging_dmg']:.0f} "
      f"from a shooter seen charging in the {CHARGE_SEEN_S:.1f} s before")


def table(rows, p):
    """One markdown table over several matches: [(name, setup, scorecard)]."""
    def hr(c, k):
        return f"{100 * c['hit_rate'][k]:.0f}%" if k in c["hit_rate"] else "-"
    p("| match | setup | place | kills | alive s | stuck s | crawl | zone dmg | cubes near/picked | better wpn near/taken | "
      "cubes end | hit gun/laser/lightning | dealt/taken (mechs) | fights won/lost | TTK | rear | 2+ mechs | laser (seen charging) |")
    p("|" + "---|" * 18)
    for name, setup, c in rows:
        ttk = f"{c['ttk_median_s']:.1f} s" if c["ttk_median_s"] is not None else "-"
        p(f"| {name} | {setup} | {c['place']} | {c['kills']} | {c['alive_s']:.0f} | {c['stuck_s']:.1f} | {c['crawl_pct']:.0f}% | "
          f"{c['zone_dmg']:.0f} | {c['cubes_near']}/{c['cubes_picked']} | {c['better_near']}/{c['weapons_taken']} | {c['cubes_end']} | "
          f"{hr(c, 'gun')}/{hr(c, 'laser')}/{hr(c, 'lightning')} | {c['dealt_mechs']:.0f}/{c['taken_mechs']:.0f} | "
          f"{c['fights_won']}/{c['fights_lost']} | {ttk} | {c['rear_pct']:.0f}% | {c['multi_pct']:.0f}% | "
          f"{c['laser_dmg']:.0f} ({c['laser_seen_charging_dmg']:.0f}) |")
