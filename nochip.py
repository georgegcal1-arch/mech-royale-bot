"""v9 experiment I, features.NO_CHIP: no gun chip shots at range; close in via cover instead.

At 100-160 m a gun duel is a slow trade (45 per hit) that mostly hands the other side's laser time. Our
shots also restart our own regen clock (5 s without dealing or taking damage, protocol.md section 7). So:

  Torso (combat.engage): the gun holds fire at a mech further than NO_CHIP_M unless it is below
    NO_CHIP_FINISH of its max HP (a kill is worth the shots). The laser, lightning and mortar are untouched.
  Legs (movement.legs, in place of the duel/strafe at range): while the target mech is further than
    NO_CHIP_M and not nearly dead, walk toward it through the spots it can't see. Candidates fan out
    APPROACH_FAN_RAD around the bearing to it at APPROACH_RADII; each scores the distance gained, COVER_W if
    buildings hide its torso from the target's eyes, CANOPY_W under dense crowns, minus travel. Not while we
    hold a laser and no lightning gun: then the range is ours (the duel holds 140 m for the laser).
"""
import math

import features
from perception import _hp_frac
from safety import EDGE_MARGIN_M

NO_CHIP_M = 100.0
NO_CHIP_FINISH = 0.25
APPROACH_RADII = (12.0, 24.0)
APPROACH_FAN_RAD = (-1.0, -0.5, 0.0, 0.5, 1.0)  # around the bearing to the target
APPROACH_REPLAN_S = 0.5
APPROACH_MAX_CLIMB_M = 6.0
EYE_M = 7.0
TORSO_FRAC = 0.7
GAIN_W, COVER_W, CANOPY_W, TRAVEL_W = 0.08, 1.5, 0.5, 0.01


class NoChipMixin:
    """Uses self.approach (re-plan time, target id, goal), self.world and _edge."""

    @staticmethod
    def chip_hold(m, d):
        """Should the gun hold fire at m, d metres away? (Always False with features.NO_CHIP off.)"""
        return features.NO_CHIP and m.get("kind") == "mech" and d > NO_CHIP_M and _hp_frac(m) >= NO_CHIP_FINISH

    def approach_goal(self, obs, target):
        """(x, y) to walk to on the way to a far target, or None (in range, nearly dead, or the laser's range)."""
        me, t = obs["self"], obs["t"]
        x, y, z = me["pos"]
        tx, ty, tz = target["pos"]
        d0 = math.hypot(tx - x, ty - y)
        if target.get("kind") != "mech" or d0 <= NO_CHIP_M or _hp_frac(target) < NO_CHIP_FINISH:
            self.approach = None
            return None
        held = {w["kind"] for w in me["weapons"] if w}
        if "laser" in held and "lightning" not in held:
            return None
        if self.approach is not None and t < self.approach[0] and self.approach[1] == target["id"]:
            return self.approach[2]
        zn, w = obs["zone"], self.world
        h = (me.get("stats") or {}).get("height", 9.0)
        eye = (tx, ty, tz + EYE_M)
        b = math.atan2(ty - y, tx - x)
        best = None
        for r in APPROACH_RADII:
            for f in APPROACH_FAN_RAD:
                a = b + f
                px, py = x + r * math.cos(a), y + r * math.sin(a)
                if self._edge(zn, t, px, py) < EDGE_MARGIN_M:
                    continue
                s = GAIN_W * (d0 - math.hypot(tx - px, ty - py)) - TRAVEL_W * r
                if w is not None:
                    pz = w.ground(px, py)
                    if (abs(pz - z) > APPROACH_MAX_CLIMB_M or w.solid_at(px, py, pz + 1.0)
                            or w.solid_at(px, py, pz + h - 1.0) or w.blocked((x, y, z + 2.0), (px, py, pz + 2.0))):
                        continue
                    if w.blocked(eye, (px, py, pz + TORSO_FRAC * h)):
                        s += COVER_W
                    if w.under_canopy(px, py, pz + TORSO_FRAC * h):
                        s += CANOPY_W
                if best is None or s > best[0]:
                    best = (s, (px, py))
        goal = best[1] if best else None
        self.approach = (t + APPROACH_REPLAN_S, target["id"], goal)
        return goal
