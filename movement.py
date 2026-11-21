"""The legs (v5): zone hurry > dodge > (v7) hide > (v9) squad > laser fetch > (v6) cube > (v9) duel > strafe
> v2 zone walk."""
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

    def _strafe_s(self):
        """(min, max) seconds per strafe leg: v5's, or (features.LEARN_DODGE) what the shooters' lead calls for."""
        return self.strafe_window((STRAFE_MIN_S, STRAFE_MAX_S)) if features.LEARN_DODGE else (STRAFE_MIN_S, STRAFE_MAX_S)

    def _new_strafe(self, t):
        self.strafe_dir = -self.strafe_dir
        self.strafe_until = t + self.rng.uniform(*self._strafe_s())

    def strafe_move(self, obs, target):
        """Unit move perpendicular to the target, or None if both sides are blocked."""
        me, t = obs["self"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        if features.LEARN_DODGE:  # a leg planned before we learned the shooters lead may be too long
            self.strafe_until = min(self.strafe_until, t + self._strafe_s()[1])
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
        if features.LEARN_DODGE:  # v9: against or with our sideways motion, by how its shooter leads
            s = self.dodge_side(p, nx, ny, mv, s)
        return [nx * s, ny * s]

    def legs(self, obs, enemies, target, cube=None, hide=None):
        """(move, yaw, boost, goal, legs, mode): zone hurry > dodge > (v7) hide > (v9) squad > laser fetch > cube
        > (v9 I) approach > (v9) duel > strafe > zone walk. `legs` is the strafe/dodge unit move (None when walking to `goal` or standing). `cube`
        is the (x, y) of a cube to collect (economy.py), or None. `hide` is (goal or None, boost) from
        heal.py while disengaging from threats, else None."""
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        goal, speed, boost, mode = self.zone_move(obs)
        legs = None
        if mode != "zone!":
            dodge = self.dodge_move(obs) if features.DODGE else None
            fetch = None if dodge or hide or not features.LASER_FETCH else self.fetch_goal(obs, enemies)
            squad = self.squad_goal(obs) if features.SQUAD_AVOID and not dodge and not hide else None  # v9
            if dodge:
                legs, mode = dodge, "dodge"
                if target is not None:  # keep strafing the way we dodged
                    b = math.atan2(target["pos"][1] - y, target["pos"][0] - x)
                    self.strafe_dir = 1 if dodge[0] * -math.sin(b) + dodge[1] * math.cos(b) >= 0 else -1
            elif hide:
                goal, speed, boost, mode = hide[0], 1.0, hide[1], "hide" if hide[0] else "cover"
            elif squad:
                goal, speed, boost, mode = squad, 1.0, True, "squad"
            elif fetch:
                goal, speed, boost, mode = fetch, 1.0, False, "fetch"
                if math.hypot(fetch[0] - x, fetch[1] - y) <= FETCH_ARRIVE_M:
                    goal = None
            elif cube:
                goal, speed, boost, mode = cube, 1.0, False, "cube"
            elif target is not None and math.dist(target["pos"], me["pos"]) <= self._reach(me):
                s = None
                ap = self.approach_goal(obs, target) if features.NO_CHIP else None  # v9 I (nochip.py)
                if ap is not None:
                    goal, speed, boost, mode = ap, 1.0, False, "approach"
                elif features.DUEL and self.duel_on(enemies, target):  # v9 (duel.py)
                    s, how = self.duel_move(obs, target), "duel"
                if ap is None and s is None and features.STRAFE:
                    s, how = self.strafe_move(obs, target), "strafe"
                if s:
                    legs, mode = s, how
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
