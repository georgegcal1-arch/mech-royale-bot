"""v9 experiment K, features.ROOFTOP: take a roof inside the next circle and fight from it with the laser.

A roof gives height (shots down over cover, fewer lines of fire from the street) and a laser hits anything
it can see. With 100 energy a mech jets about 60 m (protocol.md section 7: jump, then boost in the air:
jet_accel 34 against gravity 22, rise capped at 10 m/s, jet_drain 15/s, so ~1.5 energy per metre).

  Roofs (once, at start-up, from world.py's prisms): a prism ROOF_MIN_SIDE_M wide or more, whose bounding-box
    center is inside its footprint, ROOF_HEIGHT_M above the terrain there, solid just below its top and
    clear for a mech's height above (not covered by a taller block). The spot is that center, on the top.
  Pick (not on a roof): we hold a laser, not in the zone hurry, not within ROOF_COOL_S of giving up, and a
    roof within ROOF_PICK_M of us lies inside the next circle by ROOF_NEXT_MARGIN_M and we have the energy
    to climb it (ROOF_ENERGY_PER_M x height + ROOF_SPARE_ENERGY). Best: nearest, a little credit for height.
  Go: walk to it; within ROOF_JET_M of its footprint (or once airborne on the way up), jump and jet
    (boost) toward the spot until our feet are above the roof, then walk onto it. Give up (cool down) after
    ROOF_CLIMB_MAX_S, or out of energy below the roof.
  Hold: on the roof (feet within ROOF_ON_Z_M of the top, inside the footprint), the legs stay on it: the
    normal strafe/dodge/duel move only if ROOF_LANE_M along it is still roof, else stand. The torso fights as
    usual (the laser from up here). The laser guard's cover is skipped (its spots are on the ground).
  Down: only the zone (its hurry mode) ends the hold; then the normal zone walk takes us off the edge.
    Falling off ends it too (cool down).
"""
import math

ROOF_MIN_SIDE_M = 10.0
ROOF_HEIGHT_M = (6.0, 45.0)
ROOF_PICK_M = 60.0
ROOF_NEXT_MARGIN_M = 10.0
ROOF_ENERGY_PER_M = 1.6
ROOF_SPARE_ENERGY = 15.0
ROOF_HEIGHT_CREDIT = 0.5   # metres of distance one metre of height is worth when picking
ROOF_JET_M = 6.0           # start the jump + jet this close to the footprint
ROOF_CLIMB_MAX_S = 15.0
ROOF_ON_Z_M = 1.5
ROOF_LANE_M = 6.0
ROOF_COOL_S = 10.0
ROOF_FALL_M = 3.0          # feet this far below the top while holding: we fell off
ROOF_ARRIVE_M = 2.0


def find_roofs(world):
    """[(cx, cy, top, prism index)] of standable roofs (see the module docstring)."""
    out = []
    if world is None:
        return out
    for i, (minx, miny, maxx, maxy, base, top, e) in enumerate(world.prisms):
        if min(maxx - minx, maxy - miny) < ROOF_MIN_SIDE_M:
            continue
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        if not world._inside(cx, cy, e):
            continue
        h = top - world.ground(cx, cy)
        if not ROOF_HEIGHT_M[0] <= h <= ROOF_HEIGHT_M[1]:
            continue
        if (not world.solid_at(cx, cy, top - 0.5) or world.solid_at(cx, cy, top + 1.0)
                or world.solid_at(cx, cy, top + 8.5)):
            continue
        out.append((cx, cy, top, i))
    return out


class RooftopMixin:
    """Uses self.roofs (find_roofs), self.roof (state dict or None), self.roof_cool and self.world."""

    def _roof_footprint_dist(self, idx, x, y):
        """Horizontal distance from (x, y) to prism idx's bounding box (0 inside it)."""
        minx, miny, maxx, maxy = self.world.prisms[idx][:4]
        return math.hypot(max(minx - x, 0.0, x - maxx), max(miny - y, 0.0, y - maxy))

    def on_roof_point(self, idx, top, px, py):
        """Is (px, py) still on roof idx (its footprint at its top, nothing on it)?"""
        w = self.world
        e = w.prisms[idx][6]
        return w._inside(px, py, e) and w.solid_at(px, py, top - 0.5) and not w.solid_at(px, py, top + 1.0)

    def _pick_roof(self, obs):
        me, zn = obs["self"], obs["zone"]
        x, y, z = me["pos"]
        (ncx, ncy), nr = zn["next_center"], zn["next_radius"]
        energy = me.get("energy") or 0.0
        best = None
        for cx, cy, top, idx in self.roofs:
            d = math.hypot(cx - x, cy - y)
            if d > ROOF_PICK_M or math.hypot(cx - ncx, cy - ncy) > nr - ROOF_NEXT_MARGIN_M:
                continue
            climb = top - z
            if energy < ROOF_ENERGY_PER_M * max(climb, 0.0) + ROOF_SPARE_ENERGY:
                continue
            s = d - ROOF_HEIGHT_CREDIT * climb
            if best is None or s < best[0]:
                best = (s, cx, cy, top, idx)
        return None if best is None else best[1:]

    def rooftop_step(self, obs, mode):
        """None (no roof business: the normal flow), or {"phase": "go"|"climb"|"hold", "goal": (x, y) or None,
        "jet": bool}."""
        me, t = obs["self"], obs["t"]
        x, y, z = me["pos"]
        if self.world is None or not self.roofs:
            return None
        r = self.roof
        if r is None:
            held = {w["kind"] for w in me["weapons"] if w}
            if mode == "zone!" or t < self.roof_cool or "laser" not in held:
                return None
            pick = self._pick_roof(obs)
            if pick is None:
                return None
            cx, cy, top, idx = pick
            r = self.roof = {"spot": (cx, cy), "top": top, "idx": idx, "t0": t, "up": False}
        cx, cy = r["spot"]
        top, idx = r["top"], r["idx"]
        on = abs(z - top) <= ROOF_ON_Z_M and self.on_roof_point(idx, top, x, y)
        if mode == "zone!":  # the only way down
            self.roof = None
            self.roof_cool = t + ROOF_COOL_S
            return None
        if on:
            r["up"] = True
            goal = None if math.hypot(cx - x, cy - y) <= ROOF_ARRIVE_M else (cx, cy)
            return {"phase": "hold", "goal": goal, "jet": False}
        if r["up"] and z < top - ROOF_FALL_M:  # fell (or was knocked) off
            self.roof = None
            self.roof_cool = t + ROOF_COOL_S
            return None
        energy = me.get("energy") or 0.0
        if t - r["t0"] > ROOF_CLIMB_MAX_S or (z < top and energy < 3.0 and not me.get("grounded", True)):
            self.roof = None
            self.roof_cool = t + ROOF_COOL_S
            return None
        near = self._roof_footprint_dist(idx, x, y) <= ROOF_JET_M
        rising = z < top + 0.5 and (near or not me.get("grounded", True))
        return {"phase": "climb" if rising else "go", "goal": (cx, cy), "jet": rising}

    def roof_legs(self, obs, legs):
        """On the roof: keep the strafe/dodge/duel unit move `legs` if ROOF_LANE_M along it is still roof."""
        if legs is None or self.roof is None:
            return None
        x, y, _ = obs["self"]["pos"]
        n = math.hypot(legs[0], legs[1])
        if n < 1e-6:
            return None
        px, py = x + ROOF_LANE_M * legs[0] / n, y + ROOF_LANE_M * legs[1] / n
        return legs if self.on_roof_point(self.roof["idx"], self.roof["top"], px, py) else None
