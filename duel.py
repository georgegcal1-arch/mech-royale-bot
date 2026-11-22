"""v9 experiment DUEL: a 1v1 (exactly one enemy mech in sight or tracked, and it is our target).

Torso: the best weapon for the range. Lightning fires whenever the target is inside its range and cone
(we only hold one if it was in the starting kit: loot.py never takes it). The charged laser and the gun
work as in combat.engage. The mortar may take the aim only from a slow target (its 72 m/s shell misses
anything strafing), DUEL_MORTAR_M away.

Legs: circle the enemy. When its torso faces away from us, circle the way that brings us behind it (hits from outside its 180-degree front arc do 1.5x). It turns at 4 rad/s, so
this only pays while it is aimed at something else (more than DUEL_AWAY off us). When it faces us,
circle and reverse like the strafe. Either way a radial pull holds the range our weapons like best
(DUEL_RANGE_M). A wall or the zone edge ahead reverses the circle; both blocked -> v5's strafe.
"""
import math

import features

from perception import _wrap

DUEL_AWAY = 0.55           # its torso aimed more than this (rad) off us: it is busy elsewhere, go for its back
DUEL_RANGE_M = {"lightning": 30.0, "laser": 140.0, "gun": 60.0}  # preferred range by our best weapon
DUEL_CLOSE_M = 35.0       # v9 B2: the range held without lightning
DUEL_BAND_M = 20.0         # the radial pull reaches full strength this far off the preferred range
DUEL_RADIAL = 0.7          # ...where it is this strong against the circling (1.0)
MORTAR_SLOW_MPS = 6.5      # the mortar only against a target slower than this (a charging laser walks 5.85)
DUEL_MORTAR_M = (40.0, 235.0)


class DuelMixin:
    """Uses the movement strafe state (self.strafe_dir, self.strafe_until, _new_strafe, _lane_ok) and engage."""

    @staticmethod
    def duel_on(enemies, target):
        return target is not None and len(enemies) == 1 and enemies[0]["id"] == target["id"]

    @staticmethod
    def duel_range(me):
        held = {w["kind"] for w in me["weapons"] if w}
        if features.AGGRO_CLOSE:  # v9 B2: hold the fight close (lightning's, else the gun's short range)
            return DUEL_RANGE_M["lightning"] if "lightning" in held else DUEL_CLOSE_M
        for kind in ("lightning", "laser"):
            if kind in held:
                return DUEL_RANGE_M[kind]
        return DUEL_RANGE_M["gun"]

    def duel_move(self, obs, target):
        """Unit move circling the target toward its rear at our preferred range, or None if blocked."""
        me, t = obs["self"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        ex, ey = target["pos"][0], target["pos"][1]
        d = math.hypot(x - ex, y - ey)
        if d < 1e-6:
            return None
        phi = math.atan2(y - ey, x - ex)  # its bearing to us
        aim = target.get("aim")
        away = aim is not None and abs(_wrap(aim[0] - phi)) > DUEL_AWAY
        if t >= self.strafe_until:
            self._new_strafe(t)
        if away:
            # strafe_dir -1 moves us counter-clockwise around it (phi grows): toward its rear (aim + pi)
            self.strafe_dir = -1 if _wrap(aim[0] + math.pi - phi) > 0 else 1
        b = phi + math.pi  # our bearing to it
        pull = max(-1.0, min(1.0, (d - self.duel_range(me)) / DUEL_BAND_M)) * DUEL_RADIAL
        for _ in range(2):
            a = b + self.strafe_dir * math.pi / 2
            mx, my = math.cos(a) + pull * math.cos(b), math.sin(a) + pull * math.sin(b)
            n = math.hypot(mx, my)
            if self._lane_ok(obs, math.atan2(my, mx)):
                return [mx / n, my / n]
            self._new_strafe(t)
        return None

    def duel_engage(self, obs, target, legs_hurry):
        """(aim, fire) against the duel target: combat.engage with lightning, and the mortar only when slow."""
        me = obs["self"]
        speed = math.hypot(target["vel"][0], target["vel"][1])
        d = math.dist(me["pos"][:2], target["pos"][:2])
        mortar = speed < MORTAR_SLOW_MPS and DUEL_MORTAR_M[0] <= d <= DUEL_MORTAR_M[1]
        return self.engage(obs, target, legs_hurry, mortar=mortar, lightning=True)
