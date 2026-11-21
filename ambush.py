"""v9 experiment J, features.AMBUSH: let a hunter walk into our lightning.

Lightning does 60 per 0.25 s (240/s, plus chains) inside 45 m, and a short blocks the hunter's laser. At
100+ m we trade badly with laser carriers. So when one hunter comes for us, we wait where it can't see us.

  Start: we hold a lightning gun with AMBUSH_MIN_ENERGY, exactly one enemy mech is known, AMBUSH_HUNT_M away,
    and it is hunting us: closing at AMBUSH_CLOSING_MPS, aimed at us, or it hit us in the last HIT_S. Not
    in the zone hurry, not within AMBUSH_COOL_S of the last ambush.
  Spot: our own spot, or one of AMBUSH_DIRS x AMBUSH_RADII within AMBUSH_SPOT_M: on the ground, not in a
    building, reachable in a straight line, inside the zone (and the next circle if it shrinks before we'd
    be done), and hidden from the hunter's eyes (center and top). Best: closest to the hunter's straight path
    to us (it heads where it last saw us), then the shortest walk, dense canopy as a bonus. None: no ambush.
  Hold: walk there (no boost: the boost sound carries 90 m), then stand still (silent), torso on the hunter,
    no shots (no chip, and regen keeps counting), the gun topped up.
  Spring: any enemy mech within AMBUSH_SPRING_M ends the ambush; the normal fight (duel, lightning) takes it.
  Abort: AMBUSH_ABORT_DMG taken within AMBUSH_ABORT_WINDOW_S, a second enemy mech, the zone hurry or edge,
    the lightning gun gone, the hunter unknown for AMBUSH_LOST_S, or AMBUSH_MAX_S holding.
"""
import math

from perception import _wrap
from safety import EDGE_MARGIN_M

AMBUSH_HUNT_M = (50.0, 180.0)
AMBUSH_CLOSING_MPS = 2.0
AMBUSH_AIM_RAD = 0.35
HIT_S = 3.0
AMBUSH_MIN_ENERGY = 30.0
AMBUSH_SPOT_M = 40.0
AMBUSH_DIRS = 12
AMBUSH_RADII = (10.0, 20.0, 30.0, 40.0)
AMBUSH_MAX_CHECKS = 50        # line-of-sight checks per plan (2 per candidate)
AMBUSH_MAX_CLIMB_M = 4.0
AMBUSH_SPRING_M = 43.0
AMBUSH_MAX_S = 20.0
AMBUSH_LOST_S = 6.0
AMBUSH_COOL_S = 10.0
AMBUSH_RETRY_S = 2.0          # no spot found: try again after this long
AMBUSH_ABORT_DMG = 100.0
AMBUSH_ABORT_WINDOW_S = 2.0
AMBUSH_NEXT_MARGIN_M = 10.0
AMBUSH_ARRIVE_M = 2.0
EYE_M = 8.0
TOP_FRAC = 0.85
PATH_W, TRAVEL_W, CANOPY_W = 1.0, 0.3, 5.0


class AmbushMixin:
    """Uses self.ambush (state dict or None), self.ambush_cool, self.hits, self.world and _edge."""

    def _end_ambush(self, t, cool):
        self.ambush = None
        self.ambush_cool = t + cool

    def _hunting(self, obs, m):
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        mx, my = m["pos"][0], m["pos"][1]
        d = math.hypot(x - mx, y - my)
        if not AMBUSH_HUNT_M[0] <= d <= AMBUSH_HUNT_M[1]:
            return False
        ux, uy = (x - mx) / d, (y - my) / d
        closing = m["vel"][0] * ux + m["vel"][1] * uy - ((me.get("vel") or [0, 0])[0] * ux + (me.get("vel") or [0, 0])[1] * uy)
        aim = m.get("aim")
        aimed = aim is not None and abs(_wrap(aim[0] - math.atan2(uy, ux))) <= AMBUSH_AIM_RAD
        hit = any(by == m["id"] and obs["t"] - ht <= HIT_S for ht, by, _, _ in self.hits)
        return closing >= AMBUSH_CLOSING_MPS or aimed or hit

    def _seen_from(self, eye, px, py, pz, h):
        w = self.world
        return not (w.blocked(eye, (px, py, pz + 0.5 * h)) and w.blocked(eye, (px, py, pz + TOP_FRAC * h)))

    def _plan_ambush(self, obs, m):
        """(x, y) of a hidden spot near the hunter's path to us, our own (x, y) if already hidden, or None."""
        me, zn, t = obs["self"], obs["zone"], obs["t"]
        x, y, z = me["pos"]
        w = self.world
        if w is None:
            return None
        h = (me.get("stats") or {}).get("height", 9.0)
        hx, hy, hz = m["pos"]
        eye = (hx, hy, hz + EYE_M)
        if not self._seen_from(eye, x, y, z, h):
            return x, y
        # the next circle matters if it shrinks before we would be done waiting
        ncx, ncy = zn["next_center"]
        nr = zn["next_radius"] - AMBUSH_NEXT_MARGIN_M
        next_counts = zn["shrink_start"] - t < AMBUSH_MAX_S + 10.0
        sx, sy = x - hx, y - hy
        ss = max(sx * sx + sy * sy, 1e-9)
        cands = []
        for r in AMBUSH_RADII:
            if r > AMBUSH_SPOT_M:
                continue
            for k in range(AMBUSH_DIRS):
                a = 2 * math.pi * k / AMBUSH_DIRS
                px, py = x + r * math.cos(a), y + r * math.sin(a)
                if self._edge(zn, t, px, py) < EDGE_MARGIN_M or (next_counts and math.hypot(px - ncx, py - ncy) > nr):
                    continue
                f = max(0.0, min(1.0, ((px - hx) * sx + (py - hy) * sy) / ss))
                dpath = math.hypot(px - (hx + f * sx), py - (hy + f * sy))
                cands.append((PATH_W * dpath + TRAVEL_W * r, px, py))
        cands.sort()
        checks = 0
        best = None
        for cost, px, py in cands:
            if checks >= AMBUSH_MAX_CHECKS or (best is not None and cost - CANOPY_W >= best[0]):
                break  # out of checks, or nothing later can beat the best (sorted by cost)
            pz = w.ground(px, py)
            if (abs(pz - z) > AMBUSH_MAX_CLIMB_M or w.solid_at(px, py, pz + 1.0) or w.solid_at(px, py, pz + h - 1.0)
                    or w.blocked((x, y, z + 2.0), (px, py, pz + 2.0))):
                continue
            checks += 2
            if self._seen_from(eye, px, py, pz, h):
                continue
            if w.under_canopy(px, py, pz + 0.7 * h):
                cost -= CANOPY_W
            if best is None or cost < best[0]:
                best = (cost, px, py)
        return None if best is None else (best[1], best[2])

    def ambush_step(self, obs, enemies, mode):
        """None (no ambush: the normal flow), or {"goal": (x, y) or None (stand still), "look": (x, y, z)}."""
        me, t = obs["self"], obs["t"]
        x, y, z = me["pos"]
        arc = next((w for w in me["weapons"] if w and w["kind"] == "lightning"), None)
        a = self.ambush
        if a is not None:
            hunter = next((m for m in enemies if m["id"] == a["hunter"]), None)
            dmg = sum(amt for ht, _, amt, _ in self.hits if ht >= a["t0"] and t - ht <= AMBUSH_ABORT_WINDOW_S)
            if any(math.dist(m["pos"], me["pos"]) <= AMBUSH_SPRING_M for m in enemies):
                self._end_ambush(t, AMBUSH_RETRY_S)  # spring: the normal fight takes it
                return None
            if (mode == "zone!" or arc is None or len(enemies) >= 2 or dmg >= AMBUSH_ABORT_DMG
                    or t - a["t0"] > AMBUSH_MAX_S or self._edge(obs["zone"], t, *a["spot"]) < EDGE_MARGIN_M):
                self._end_ambush(t, AMBUSH_COOL_S)
                return None
            if hunter is not None:
                a["seen"], a["look"] = t, tuple(hunter["pos"])
            elif t - a["seen"] > AMBUSH_LOST_S:
                self._end_ambush(t, AMBUSH_COOL_S)
                return None
        else:
            if mode == "zone!" or t < self.ambush_cool or arc is None or len(enemies) != 1:
                return None
            if (me.get("energy") or 0) < AMBUSH_MIN_ENERGY or not self._hunting(obs, enemies[0]):
                return None
            hunter = enemies[0]
            spot = self._plan_ambush(obs, hunter)
            if spot is None:
                self.ambush_cool = t + AMBUSH_RETRY_S
                return None
            a = self.ambush = {"hunter": hunter["id"], "spot": spot, "t0": t, "seen": t, "look": tuple(hunter["pos"])}
        sx, sy = a["spot"]
        goal = None if math.hypot(sx - x, sy - y) <= AMBUSH_ARRIVE_M else (sx, sy)
        return {"goal": goal, "look": a["look"]}

    def ambush_aim(self, obs, look):
        """[yaw, pitch] at the hunter's last known torso."""
        origin = self._origin(obs["self"])
        return list(self.angles(origin, (look[0], look[1], look[2] + 4.5)))
