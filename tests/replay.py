"""Replay check.py's recorded hello + 300 observations through the bot's decision code and write
one CSV row per decision.

    python tests/replay.py                 # -> tests/out/decisions.csv
    python tests/replay.py --out my.csv
    python tests/replay.py --no-timing     # blank ms column: byte-comparable across runs

Columns: tick, t, hp, nearest_enemy (id@metres, blank if none in sight), move (x y), aim (yaw pitch,
radians), fire (slot0 slot1), reason (the bot's mode: zone!/walk/sneak/hold/SAFE, plus what it did),
ms (decision time; blank with --no-timing).
"""
import argparse
import csv
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import OBS, REPO, new_bot, respond  # noqa: E402

COLUMNS = ["tick", "t", "hp", "nearest_enemy", "move", "aim", "fire", "reason", "ms"]


def nearest_enemy(obs):
    me = obs["self"]
    best = None
    for m in obs["mechs"]:
        if m["kind"] == "mech" and m["id"] != me["id"]:
            d = math.dist(m["pos"], me["pos"])
            if best is None or d < best[1]:
                best = (m["id"], d)
    return best


def reason(action, obs, fell_back, enemy):
    label = (action.get("debug") or {}).get("label", "")
    parts = ["SAFE (decision failed)" if fell_back else (label.split(" ")[0] or "?")]
    if any(action.get("fire", [])):
        parts.append(f"firing at {enemy[0]}" if enemy else "firing (no target)")
    if "take" in action:
        parts.append(f"take {action['take']}")
    if "pick" in action:
        parts.append(f"pick {action['pick']}")
    if action.get("boost"):
        parts.append("boost")
    if action.get("jump"):
        parts.append("jump")
    return "; ".join(parts)


def replay(out_path, timing=True):
    bot = new_bot()
    rows = []
    for obs in OBS:
        fb = bot.fallbacks
        action, dt = respond(bot, obs)
        enemy = nearest_enemy(obs)
        rows.append({
            "tick": obs["tick"],
            "t": obs["t"],
            "hp": round(obs["self"]["hp"]),
            "nearest_enemy": f"{enemy[0]}@{enemy[1]:.0f}" if enemy else "",
            "move": " ".join(f"{v:.3f}" for v in action.get("move", [0, 0])),
            "aim": " ".join(f"{v:.3f}" for v in action.get("aim", [])),
            "fire": " ".join(str(bool(v)).lower() for v in action.get("fire", [False, False])),
            "reason": reason(action, obs, bot.fallbacks > fb, enemy),
            "ms": f"{dt * 1000:.3f}" if timing else "",
        })
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return rows, bot


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(REPO, "tests", "out", "decisions.csv"))
    ap.add_argument("--no-timing", action="store_true", help="leave the ms column blank (for byte-for-byte comparisons)")
    a = ap.parse_args()
    rows, bot = replay(a.out, timing=not a.no_timing)
    worst = max((float(r["ms"]) for r in rows if r["ms"]), default=float("nan"))
    firing = sum(1 for r in rows if "firing" in r["reason"])
    print(f"wrote {len(rows)} decisions to {a.out}: worst {worst:.3f} ms, fallbacks {bot.fallbacks}, firing in {firing}")


if __name__ == "__main__":
    main()
