"""The legs (v5): zone hurry > dodge > laser fetch > strafe > v2 zone walk."""
import math

import features
from loot import FETCH_ARRIVE_M
from safety import EDGE_MARGIN_M

STRAFE_MIN_S, STRAFE_MAX_S = 1.0, 2.0  # strafe direction lasts a random time in this range
STRAFE_LOOK_M = 6.0     # reverse early if this far along the strafe is a wall or near the zone edge
DODGE_M = 4.0           # dodge a bullet that will pass closer than this to our center
DODGE_HORIZON_S = 1.0   # ...within this many seconds


class MovementMixin:
    """Uses self.rng, self.strafe_dir, self.strafe_until, self.world, and zone_move/_edge (safety.py),
    fetch_goal (loot.py), _reach (targeting.py)."""

    def _lane_ok(self, obs, a):
        """Is STRAFE_LOOK_M along yaw a open ground (no wall) and clear of the zone edge?"""
        me = obs["self"]
        x, y, z = me["pos"]
        px, py = x + STRAFE_LOOK_M * math.cos(a), y + STRAFE_LOOK_M * math.sin(a)
        if self._edge(obs["zone"], obs["t"], px, py) < EDGE_MARGIN_M:
            return False
        if self.world is not None:
            h = (me.get("stats") or {}).get("height", 9.0)
            if self.world.solid_at(px, py, z + 1.0) or self.world.solid_at(px, py, z + h - 1.0):
                return False
        return True

    def _new_strafe(self, t):
        self.strafe_dir = -self.strafe_dir
        self.strafe_until = t + self.rng.uniform(STRAFE_MIN_S, STRAFE_MAX_S)

    def strafe_move(self, obs, target):
        """Unit move perpendicular to the target, or None if both sides are blocked."""
        me, t = obs["self"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        if t >= self.strafe_until:
            self._new_strafe(t)
        b = math.atan2(target["pos"][1] - y, target["pos"][0] - x)
        for _ in range(2):
            a = b + self.strafe_dir * math.pi / 2
            if self._lane_ok(obs, a):
                return [math.cos(a), math.sin(a)]
            self._new_strafe(t)
        return None

    def dodge_move(self, obs):
        """Unit move sideways off the path of the most urgent bullet that would pass within DODGE_M, or None."""
        me = obs["self"]
        x, y, z = me["pos"]
        cz = z + 0.5 * (me.get("stats") or {}).get("height", 9.0)
        mv = me.get("vel") or [0, 0, 0]
        best = None
        for p in obs.get("projectiles") or []:
            if p.get("kind") not in ("gun", "npc"):
                continue
            rx, ry, rz = p["pos"][0] - x, p["pos"][1] - y, p["pos"][2] - cz
            vx, vy, vz = p["vel"][0] - mv[0], p["vel"][1] - mv[1], p["vel"][2] - mv[2]
            vv = vx * vx + vy * vy + vz * vz
            if vv < 1e-9:
                continue
            tc = -(rx * vx + ry * vy + rz * vz) / vv
            if not 0 < tc <= DODGE_HORIZON_S:
                continue
            ox, oy, oz = rx + vx * tc, ry + vy * tc, rz + vz * tc  # the bullet, relative to us, when closest
            if math.sqrt(ox * ox + oy * oy + oz * oz) >= DODGE_M:
                continue
            if best is None or tc < best[0]:
                best = (tc, p, ox, oy)
        if best is None:
            return None
        _, p, ox, oy = best
        bx, by = p["vel"][0], p["vel"][1]
        hb = math.hypot(bx, by)
        if hb < 1e-6:
            return None  # falling straight down: no sideways
        nx, ny = -by / hb, bx / hb
        side = -(nx * ox + ny * oy)  # where we are relative to the bullet's closest point, across its path
        s = (1 if side > 0 else -1) if abs(side) > 1e-6 else self.strafe_dir
        return [nx * s, ny * s]

    def legs(self, obs, enemies, target):
        """(move, yaw, boost, goal, legs, mode): zone hurry > dodge > laser fetch > strafe > zone walk.
        `legs` is the strafe/dodge unit move (None when walking to `goal` or standing)."""
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        goal, speed, boost, mode = self.zone_move(obs)
        legs = None
        if mode != "zone!":
            dodge = self.dodge_move(obs) if features.DODGE else None
            fetch = None if dodge or not features.LASER_FETCH else self.fetch_goal(obs, enemies)
            if dodge:
                legs, mode = dodge, "dodge"
                if target is not None:  # keep strafing the way we dodged
                    b = math.atan2(target["pos"][1] - y, target["pos"][0] - x)
                    self.strafe_dir = 1 if dodge[0] * -math.sin(b) + dodge[1] * math.cos(b) >= 0 else -1
            elif fetch:
                goal, speed, boost, mode = fetch, 1.0, False, "fetch"
                if math.hypot(fetch[0] - x, fetch[1] - y) <= FETCH_ARRIVE_M:
                    goal = None
            elif features.STRAFE and target is not None and math.dist(target["pos"], me["pos"]) <= self._reach(me):
                s = self.strafe_move(obs, target)
                if s:
                    legs, mode = s, "strafe"
        nc = obs["zone"]["next_center"]
        if legs is not None:
            move, boost = legs, False
            yaw = math.atan2(legs[1], legs[0])
        elif goal is None:
            move = [0.0, 0.0]
            yaw = math.atan2(nc[1] - y, nc[0] - x)
        else:
            yaw = math.atan2(goal[1] - y, goal[0] - x)
            move = [math.cos(yaw) * speed, math.sin(yaw) * speed]
        return move, yaw, boost, goal, legs, mode
