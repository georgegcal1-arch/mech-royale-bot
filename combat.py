"""The torso (v3): lead, clear shots only, laser charge/release, mortar lobs, and reloading when idle."""
import math

import mechfight as mf
from perception import LATENCY_S, _wrap

AIM_TOL = 0.7           # fire when the aim error at the target is within this fraction of its radius / half height
AIM_HEIGHTS = (0.5, 0.8)  # try the target's center, then near its top (peeking over cover)
SHOT_OFFSET_M = 3.0     # shots spawn radius + 0.5 out along the aim
MORTAR_MIN_M = 25.0     # never lob closer than this (own splash, knock-back)
MORTAR_RANGE_ERR_M = 3.0  # fire the mortar when the pitch error moves the impact less than this


def aimed(aim, yaw, pitch, dist, radius, height):
    """Does the actual torso aim put the shot on a target of this size at this distance?"""
    return (abs(_wrap(aim[0] - yaw)) * dist <= AIM_TOL * radius
            and abs(aim[1] - pitch) * dist <= AIM_TOL * height / 2)


class CombatMixin:
    """Uses the perception methods (predict, _origin, _size, clear, angles) and self.world."""

    # ------------------------------------------------------------------ ballistics
    def lead(self, origin, m, z_off, speed):
        """(point, distance, flight time) for a straight shot from origin at speed."""
        tf = 0.0
        for _ in range(3):
            q = self.predict(m, tf + LATENCY_S, z_off)
            d = math.dist(origin, q)
            tf = max(d - SHOT_OFFSET_M, 0.0) / speed
        return q, d, tf

    def gun_solution(self, origin, m, speed, height, rng):
        """(yaw, pitch, dist, point, clear) for the first aim height with a clear line, else the center.
        Out of range counts as not clear (and skips the cover check)."""
        first = None
        for frac in AIM_HEIGHTS:
            q, d, _ = self.lead(origin, m, frac * height, speed)
            yaw, pitch = self.angles(origin, q)
            if d > rng:
                return yaw, pitch, d, q, False
            ok = self.clear(origin, q)
            if ok:
                return yaw, pitch, d, q, True
            first = first or (yaw, pitch, d, q, False)
        return first

    def mortar_solution(self, tube, m, stats):
        """(yaw, pitch, horizontal dist, point, clear) for a low-arc lob at m's feet, or None if out of reach."""
        v, g = stats.get("speed", 72.0), stats.get("gravity", 22.0)
        tf = 0.0
        for _ in range(3):
            q = self.predict(m, tf + LATENCY_S, 1.0)
            dx, dy, h = q[0] - tube[0], q[1] - tube[1], q[2] - tube[2]
            d = math.hypot(dx, dy)
            disc = v ** 4 - g * (g * d * d + 2 * h * v * v)
            if d < 1.0 or disc < 0:
                return None
            pitch = math.atan((v * v - math.sqrt(disc)) / (g * d))
            tf = d / (v * math.cos(pitch))
        yaw = math.atan2(dy, dx)
        ok = True
        if self.world is not None:  # check the arc in 8 straight pieces
            cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
            pts = [(tube[0] + v * cp * cy * s, tube[1] + v * cp * sy * s, tube[2] + v * sp * s - 0.5 * g * s * s)
                   for s in (tf * k / 8 for k in range(9))]
            ok = not any(self.world.blocked(pts[k], pts[k + 1]) for k in range(8))
        return yaw, pitch, d, q, ok

    # ------------------------------------------------------------------ the fight (torso)
    def engage(self, obs, m, legs_hurry):
        """(aim, fire) against target m."""
        me = obs["self"]
        x, y, z = me["pos"]
        st = me["stats"]
        aim_now = me["aim"]
        radius, height = self._size(m)
        origin = self._origin(me)
        fire = [False, False]
        slots = {w["kind"]: (i, w) for i, w in enumerate(me["weapons"]) if w}

        # gun: lead the target
        aim = None
        gun = slots.get("gun")
        if gun:
            i, w = gun
            gs = w.get("stats", {})
            rng = gs.get("range", 230.0)
            yaw, pitch, d, q, ok = self.gun_solution(origin, m, gs.get("speed", 252.0), height, rng)
            aim = [yaw, pitch]
            mf.draw.line([x, y, origin[2]], list(q), "#ff5040" if ok else "#806060")
            if (ok and d <= rng and w.get("ammo", 0) > 0 and (w.get("reload") or 0) <= 0
                    and aimed(aim_now, yaw, pitch, d, radius, height)):
                fire[i] = True

        # laser: hitscan, no lead. Charge on a clear target, let go when full and on target.
        laser = slots.get("laser")
        if laser:
            i, w = laser
            ls = w.get("stats", {})
            q = self.predict(m, LATENCY_S, 0.5 * height)
            d = math.dist(origin, q)
            yaw, pitch = self.angles(origin, q)
            ok = d <= ls.get("range", 280.0) and self.clear(origin, q)
            charge = w.get("charge") or 0.0
            if charge > 0:
                full = charge >= ls.get("charge", 0.75)
                if full and ok:
                    aim = [yaw, pitch]
                    # let go (fire=False) only when on target; holding on fires by itself after hold_s
                    fire[i] = not aimed(aim_now, yaw, pitch, d, radius, height)
                else:
                    fire[i] = True
            elif ok and w.get("cooldown", 0) <= 0 and not legs_hurry:  # charging halves our speed
                fire[i] = True

        # mortar: lob when ready and the arc is clear; it takes the aim until it fires
        mortar = slots.get("mortar")
        if mortar:
            i, w = mortar
            ms = w.get("stats", {})
            tube = (x, y, z + st.get("height", 9.0) + 0.5)
            sol = self.mortar_solution(tube, m, ms) if w.get("cooldown", 0) <= 0 else None
            if sol and sol[4] and sol[2] >= MORTAR_MIN_M:
                yaw, pitch, d, q, _ = sol
                aim = [yaw, pitch]
                v, g = ms.get("speed", 72.0), ms.get("gravity", 22.0)
                drange = max(abs(2 * v * v * math.cos(2 * pitch) / g), 1.0)  # impact metres per radian of pitch
                if (abs(_wrap(aim_now[0] - yaw)) * d <= AIM_TOL * radius + 2.0
                        and abs(aim_now[1] - pitch) * drange <= MORTAR_RANGE_ERR_M):
                    fire[i] = True
                mf.draw.point(list(q), "#ffa030", 6)

        if aim is None:  # no gun (never dropped, but be safe): just look at it
            aim = list(self.angles(origin, self.predict(m, LATENCY_S, 0.5 * height)))
        return aim, fire

    def reload_if_idle(self, act, weapons, enemies, target):
        """Reload a partly used magazine whenever no enemy is in view (and nothing, e.g. an NPC, is engaged)."""
        gun = next((w for w in weapons if w and w["kind"] == "gun"), None)
        if gun and not enemies and target is None and (gun.get("reload") or 0) <= 0:
            ammo, mag = gun.get("ammo"), (gun.get("stats") or {}).get("mag")
            if ammo is not None and (ammo < mag if mag is not None else ammo == 0):
                act["reload"] = True
