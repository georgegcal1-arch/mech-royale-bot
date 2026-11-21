"""Staying alive (v2): the safety net around every decision (fallback, validation, timing) and zone
awareness (hurry when near the edge, otherwise walk quietly to a spot inside the next circle)."""
import json
import math
import time

import features
import mechfight as mf
from perception import _finite

# --- zone tuning (v2) ---
EDGE_MARGIN_M = 25.0   # closer than this to the zone edge (now, or LOOKAHEAD_S from now) = hurry
LOOKAHEAD_S = 6.0      # also judge the edge where it will be this many seconds from now
SPOT_FRAC = 0.5        # target spot: this fraction of next_radius from next_center, on our side
ARRIVE_M = 3.0         # closer than this to the spot = stand still
SLOW_FRAC = 0.9        # slow walk = 90% of sound.step_speed: no footstep sounds
SPARE_S = 10.0         # walk at full speed if slow walking would reach the spot later than shrink_end - SPARE_S

UPGRADE = "armor"  # v5: this if offered, else choice 0
UPGRADE_PREFS = ("plating", "armor", "nanites")  # v6 (features.ECONOMY): first one offered, else choice 0

# --- safety net (v2) ---
SLOW_DECISION_MS = 10.0  # log any decision slower than this


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


def pick_upgrade(offer):
    """Index of the upgrade to take: the first of UPGRADE_PREFS offered (v5: UPGRADE), else 0."""
    ids = [c.get("id") for c in offer.get("choices") or []]
    for want in (UPGRADE_PREFS if features.ECONOMY else (UPGRADE,)):
        if want in ids:
            return ids.index(want)
    return 0


class SafetyMixin:
    """Uses self.decide, self.times, self.fallbacks, self.step_speed, self.walk_speed."""

    # ------------------------------------------------------------------ safety net
    def act(self, obs: mf.Observation) -> mf.Action:
        t0 = time.perf_counter()
        try:
            act = self.decide(obs)
            self._validate(act)
            self._scrub_drawing()
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
        json.dumps(act, allow_nan=False)  # anything else non-finite or unserializable

    @staticmethod
    def _scrub_drawing():
        """Drop debug items with non-finite coordinates (they would make the whole line invalid JSON)."""
        d = getattr(mf.draw, "_d", None)
        if not isinstance(d, dict):
            return
        for key, fields in (("lines", ("a", "b")), ("points", ("at",)), ("text", ("at",))):
            items = d.get(key)
            if items:
                d[key] = [it for it in items if all(_finite(*it[f]) for f in fields if f in it)]

    def safe(self, obs):
        """Move toward the zone's next center, no firing; answer a pending upgrade offer. Never raises."""
        act = {"tick": obs.get("tick"), "fire": [False, False]}
        try:
            if obs.get("offer"):
                act["pick"] = pick_upgrade(obs["offer"])
        except Exception:
            pass
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

    # ------------------------------------------------------------------ zone awareness (v2)
    def _walk_mps(self, me):
        """Our walk speed (after upgrades); the config default if missing or not positive."""
        v = (me.get("stats") or {}).get("walk_speed")
        return v if isinstance(v, (int, float)) and v > 0 else self.walk_speed

    @staticmethod
    def _edge(z, t, x, y):
        """How far (x, y) is inside the zone edge, now and LOOKAHEAD_S from now (the smaller)."""
        cx, cy, r = zone_at(z, t)
        fx, fy, fr = zone_at(z, t + LOOKAHEAD_S)
        return min(r - math.hypot(x - cx, y - cy), fr - math.hypot(x - fx, y - fy))

    def zone_move(self, obs):
        """(goal or None, speed 0..1, boost, mode) for the legs."""
        me, z, t = obs["self"], obs["zone"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        ncx, ncy = z["next_center"]
        nr = z["next_radius"]

        if not me["in_zone"] or self._edge(z, t, x, y) < EDGE_MARGIN_M:
            return (ncx, ncy), 1.0, not me["in_zone"], "zone!"

        # A spot inside the next circle on the line from its center to us (not the crowded center).
        d = math.hypot(x - ncx, y - ncy)
        keep = SPOT_FRAC * nr
        if d <= keep + ARRIVE_M:
            return None, 0.0, False, "hold"
        goal = (ncx + (x - ncx) / d * keep, ncy + (y - ncy) / d * keep)
        dist = d - keep
        slow_mps = SLOW_FRAC * self.step_speed
        walk_mps = self._walk_mps(me)
        time_left = z["shrink_end"] - t - SPARE_S
        if dist > slow_mps * max(time_left, 0.0):
            return goal, 1.0, False, "walk"
        return goal, min(1.0, slow_mps / walk_mps), False, "sneak"
