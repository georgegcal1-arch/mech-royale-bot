"""Mech Royale bot v2.

The kit's example bot with two changes:
  1. Safety net: any error (or a non-finite number) in the decision falls back to a safe action
     (move toward the zone, no firing). Each decision does a small, bounded amount of work and its
     time is measured (worst case logged to stderr at exit).
  2. Zone awareness: outside the zone or near its edge, hurry toward next_center; otherwise walk
     slowly (below the footstep-sound speed) to a spot inside the next circle, on our side of it.
Combat, pickups and upgrade picks are unchanged from the example bot.
"""
import atexit
import gc
import math
import time

import mechfight as mf

# --- zone tuning ---
EDGE_MARGIN_M = 25.0   # closer than this to the zone edge (now, or LOOKAHEAD_S from now) = hurry
LOOKAHEAD_S = 6.0      # also judge the edge where it will be this many seconds from now
SPOT_FRAC = 0.5        # target spot: this fraction of next_radius from next_center, on our side
ARRIVE_M = 3.0         # closer than this to the spot = stand still
SLOW_FRAC = 0.9        # slow walk = 90% of sound.step_speed: no footstep sounds
SPARE_S = 10.0         # walk at full speed if slow walking would reach the spot later than shrink_end - SPARE_S

# --- safety net ---
SLOW_DECISION_MS = 10.0  # log any decision slower than this


def _finite(*vals):
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def zone_at(z, t):
    """(cx, cy, r) of the zone circle at match time t, moving linearly from the current circle to the
    next one during [shrink_start, shrink_end]. If the server already reports the moving circle, this
    errs inward (we hurry a little earlier), never outward."""
    s0, s1 = z["shrink_start"], z["shrink_end"]
    if t <= s0:
        f = 0.0
    elif t >= s1 or s1 <= s0:
        f = 1.0
    else:
        f = (t - s0) / (s1 - s0)
    (cx, cy), (nx, ny) = z["center"], z["next_center"]
    return cx + (nx - cx) * f, cy + (ny - cy) * f, z["radius"] + (z["next_radius"] - z["radius"]) * f


class Bot:
    name = "mech-royale-bot v2"

    def __init__(self, hello: mf.Hello):
        cfg = hello["config"]
        self.reach = cfg["loot"]["pickup_radius"]
        self.step_speed = cfg["sound"]["step_speed"]
        self.walk_speed = cfg["mech"].get("walk_speed", 11.7)
        self.times = []  # seconds per decision
        self.fallbacks = 0
        gc.freeze()  # keep start-up objects out of the collector so collections stay short
        atexit.register(self._report)

    # ------------------------------------------------------------------ safety net
    def act(self, obs: mf.Observation) -> mf.Action:
        t0 = time.perf_counter()
        try:
            act = self.decide(obs)
            self._validate(act)
        except Exception as e:
            self.fallbacks += 1
            if self.fallbacks <= 5 or self.fallbacks % 100 == 0:
                mf.log(f"decision failed at tick {obs.get('tick')} ({self.fallbacks} so far): {e!r}")
            mf.draw.clear()  # drop anything a half-finished decision drew
            act = self.safe(obs)
        dt = time.perf_counter() - t0
        self.times.append(dt)
        if dt * 1000 > SLOW_DECISION_MS:
            mf.log(f"slow decision at tick {obs.get('tick')}: {dt * 1000:.1f} ms")
        return act

    @staticmethod
    def _validate(act):
        """Raise if the action holds anything that would not serialize to valid JSON numbers."""
        for k in ("move", "aim"):
            v = act.get(k)
            if v is not None and (len(v) != 2 or not _finite(*v)):
                raise ValueError(f"bad {k}: {v!r}")

    def safe(self, obs):
        """Move toward the zone's next center, no firing. Never raises."""
        act = {"tick": obs.get("tick"), "fire": [False, False]}
        try:
            x, y = obs["self"]["pos"][:2]
            gx, gy = obs["zone"]["next_center"]
            yaw = math.atan2(gy - y, gx - x)
            if _finite(yaw):
                act.update(move=[math.cos(yaw), math.sin(yaw)], aim=[yaw, 0.0],
                           boost=not obs["self"].get("in_zone", True))
            mf.draw.label("SAFE MODE")
        except Exception:
            pass
        return act

    def _report(self):
        if not self.times:
            return
        ts = sorted(self.times)
        p = lambda q: ts[min(len(ts) - 1, int(q * len(ts)))] * 1000
        mf.log(f"decisions: {len(ts)}  ms p50 {p(0.5):.2f}  p99 {p(0.99):.2f}  max {ts[-1] * 1000:.2f}  fallbacks: {self.fallbacks}")

    # ------------------------------------------------------------------ zone awareness
    def zone_move(self, obs):
        """(goal or None, speed 0..1, boost, mode) for the legs."""
        me, z, t = obs["self"], obs["zone"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        ncx, ncy = z["next_center"]
        nr = z["next_radius"]

        cx, cy, r = zone_at(z, t)
        fx, fy, fr = zone_at(z, t + LOOKAHEAD_S)
        edge = min(r - math.hypot(x - cx, y - cy), fr - math.hypot(x - fx, y - fy))
        if not me["in_zone"] or edge < EDGE_MARGIN_M:
            return (ncx, ncy), 1.0, not me["in_zone"], "zone!"

        # A spot inside the next circle on the line from its center to us (not the crowded center).
        d = math.hypot(x - ncx, y - ncy)
        keep = SPOT_FRAC * nr
        if d <= keep + ARRIVE_M:
            return None, 0.0, False, "hold"
        goal = (ncx + (x - ncx) / d * keep, ncy + (y - ncy) / d * keep)
        dist = d - keep
        slow_mps = SLOW_FRAC * self.step_speed
        walk_mps = me["stats"].get("walk_speed", self.walk_speed) or self.walk_speed
        time_left = z["shrink_end"] - t - SPARE_S
        if dist > slow_mps * max(time_left, 0.0):
            return goal, 1.0, False, "walk"
        return goal, min(1.0, slow_mps / walk_mps), False, "sneak"

    # ------------------------------------------------------------------ the decision
    def decide(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        act: mf.Action = {"tick": obs["tick"]}
        if obs["offer"]:
            act["pick"] = 0
        near = [p for p in obs["pickups"] if math.dist(p["pos"][:2], (x, y)) < self.reach]
        if me["weapons"][1] is None and near and near[0]["kind"] != "gun":
            act["take"] = near[0]["id"]
        enemies = sorted((m for m in obs["mechs"] if m["kind"] == "mech"), key=lambda m: math.dist(m["pos"], me["pos"]))

        goal, speed, boost, mode = self.zone_move(obs)
        nc = obs["zone"]["next_center"]
        if goal is None:
            move = [0.0, 0.0]
            yaw = math.atan2(nc[1] - y, nc[0] - x)
        else:
            yaw = math.atan2(goal[1] - y, goal[0] - x)
            move = [math.cos(yaw) * speed, math.sin(yaw) * speed]
        act.update(move=move, aim=[yaw, 0.0], boost=boost, jump=obs["tick"] % 40 == 0)

        if enemies:
            t = enemies[0]["pos"]
            aim = [math.atan2(t[1] - y, t[0] - x), math.atan2(t[2] - z - 2.5, math.dist(t[:2], (x, y)))]
            act.update(aim=aim, fire=[True, True])
            mf.draw.line([x, y, z + 7], [t[0], t[1], t[2] + 4.5], "#ff5040")
        if goal is not None:
            mf.draw.point([goal[0], goal[1], z], "#40c0ff", 6)
        mf.draw.label(f"{mode} · hp {me['hp']:.0f} · {len(enemies)} in sight")
        return act


if __name__ == "__main__":
    mf.run(Bot)
