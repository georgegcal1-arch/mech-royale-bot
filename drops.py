"""v9 DROP_CAPTURE and EARLY_ARM (features.py), both off by default; branch v9-drops, on top of LC.

DROP_CAPTURE (protocol.md section 7, *Supply drops*; practice hello.config: called at 70 / 140 / 210 s, lands
`fall_s` 25 s later on open ground inside the next circle, ring `radius` 8 m, `capture_s` 4 s alone in the ring
-> `cubes` 3 power cubes, `repair` 1 = full HP, and full energy; two mechs in the ring freeze the clock, a new
sole holder restarts it; the landing does no damage):
  Route (nav.py) to the nearest drop within DROP_MAX_M that lies DROP_EDGE_M inside the next circle, when we
  can get there before it lands (DROP_SPEED) or nobody else is near it (no visible enemy within DROP_CLEAR_M
  of it, no other holder). Boost the approach while far. In the ring: circle DROP_ORBIT_M around its centre
  (a moving target, still inside the 8 m ring) until it is ours; the torso fights as usual.
  Abort (and leave drops alone for DROP_ABORT_S) when 2+ mechs engage us (squad.engagers) or a visible enemy
  laser that is ready (charging, or not in the WINDOW_S after its last beam) has a line to us.

EARLY_ARM: in the first ARM_UNTIL_S of the match, holding neither a laser nor lightning, route (boosting) to
  the nearest visible laser or lightning pickup within ARM_MAX_M, reachable (TAKE_Z_M), inside the zone, and
  not nearer to a visible enemy than to us. A walk started in time may finish until ARM_KEEP_S.

Both take the legs after legs() (zone hurry and dodges keep theirs); the laser counter, the laser guard and
stuck escape still come after them."""
import math

import features
from laser_counter import WINDOW_S
from loot import TAKE_Z_M
from safety import EDGE_MARGIN_M

DROP_MAX_M = 200.0     # consider drops at most this far away
DROP_SPEED = 13.0      # m/s for the arrival estimate (walk 11.7, boost 19.5 while energy lasts)
DROP_LATE_S = 2.0      # arriving up to this long after it lands still counts as "before it lands"
DROP_CLEAR_M = 40.0    # no visible enemy this close to the drop = uncontested
DROP_EDGE_M = 15.0     # the drop must be this far inside the next circle
DROP_ORBIT_M = 3.0     # in the ring, circle this far from its centre
DROP_IN_M = 2.5        # "in the ring" = within radius - this
DROP_BOOST_M = 30.0    # boost the approach while farther than this (energy over DROP_BOOST_E)
DROP_BOOST_E = 25.0
DROP_ABORT_S = 4.0     # after an abort, no drop for this long
ARM_UNTIL_S = 20.0     # EARLY_ARM starts walks only this early in the match
ARM_KEEP_S = 35.0      # ...and a walk started in time may finish until then
ARM_MAX_M = 120.0


class DropsMixin:
    """Uses self.drop_state ({"id", "abort_until"}), self.arm_id, self.engagers (squad.py), self.laser_foe /
    self.laser_fired / self.note_lasers (laser_counter.py), self._edge (safety.py), self.strafe_dir."""

    # --- EARLY_ARM ---------------------------------------------------------------------------------
    def arm_goal(self, obs, enemies):
        """(x, y) of the laser / lightning pickup to fetch now, or None."""
        me, t = obs["self"], obs["t"]
        if t > ARM_KEEP_S or any(w and w["kind"] in ("laser", "lightning") for w in me["weapons"]):
            self.arm_id = None
            return None
        x, y, z = me["pos"]
        best = None
        for p in obs["pickups"]:
            if p["kind"] not in ("laser", "lightning") or abs(p["pos"][2] - z) > TAKE_Z_M:
                continue
            if t > ARM_UNTIL_S and p["id"] != self.arm_id:
                continue  # late: only finish the walk already started
            px, py = p["pos"][0], p["pos"][1]
            d = math.hypot(px - x, py - y)
            if d > ARM_MAX_M or self._edge(obs["zone"], t, px, py) < EDGE_MARGIN_M:
                continue
            if any(math.hypot(m["pos"][0] - px, m["pos"][1] - py) < d for m in enemies):
                continue  # someone gets there first
            if best is None or d < best[0]:
                best = (d, p["id"], px, py)
        self.arm_id = None if best is None else best[1]
        return None if best is None else (best[2], best[3])

    # --- DROP_CAPTURE ------------------------------------------------------------------------------
    def _drop_danger(self, obs, enemies):
        """Why to abort now ("2 engaging" / "laser"), or None."""
        if len(self.engagers(obs, enemies)) >= 2:
            return "engaged"
        foe = self.laser_foe(obs, enemies)
        if foe is not None and (foe.get("charging") or obs["t"] - self.laser_fired.get(foe["id"], -1e9) >= WINDOW_S):
            return "laser"
        return None

    def drop_goal(self, obs, enemies):
        """The drop dict to go for now, or None (aborts and cooldowns included)."""
        drops = obs.get("drops") or []
        st, t = self.drop_state, obs["t"]
        if not drops or t < st["abort_until"]:
            return None
        self.note_lasers(obs)  # this decision's beams (LASER_COUNTER notes them only later in decide; idempotent)
        if self._drop_danger(obs, enemies):
            st["abort_until"], st["id"] = t + DROP_ABORT_S, None
            return None
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        z = obs["zone"]
        ncx, ncy = z["next_center"]
        best = None
        for d in drops:
            if d.get("holder") not in (None, me["id"]):
                continue  # someone else is capturing it
            px, py = d["pos"][0], d["pos"][1]
            dist = math.hypot(px - x, py - y)
            if dist > DROP_MAX_M or math.hypot(px - ncx, py - ncy) > z["next_radius"] - DROP_EDGE_M:
                continue
            before = dist / DROP_SPEED <= (d.get("lands_in") or 0.0) + DROP_LATE_S
            alone = not any(math.hypot(m["pos"][0] - px, m["pos"][1] - py) <= DROP_CLEAR_M for m in enemies)
            if not (before or alone or d["id"] == st["id"] and dist <= d["radius"]):
                continue
            if best is None or dist < best[0]:
                best = (dist, d)
        st["id"] = None if best is None else best[1]["id"]
        return None if best is None else best[1]

    def drop_legs(self, obs, d):
        """(goal or None, legs or None, boost, mode) toward / inside drop d."""
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        px, py = d["pos"][0], d["pos"][1]
        rx, ry = x - px, y - py
        r = math.hypot(rx, ry)
        if r > d["radius"] - DROP_IN_M:
            far = r > DROP_BOOST_M and (me.get("energy") or 0) > DROP_BOOST_E
            return (px, py), None, far, "drop"
        if r < 0.5:
            rx, ry, r = 1.0, 0.0, 1.0
        s = self.strafe_dir
        tx, ty = -ry / r * s, rx / r * s                       # around the centre...
        k = (DROP_ORBIT_M - r) / DROP_ORBIT_M                  # ...pulled onto the orbit
        mx, my = tx + k * rx / r, ty + k * ry / r
        n = math.hypot(mx, my) or 1.0
        return None, [mx / n, my / n], False, "drop/hold" if (d.get("lands_in") or 0) <= 0 else "drop/wait"

    def drops_step(self, obs, enemies):
        """(goal, legs, boost, mode) for EARLY_ARM / DROP_CAPTURE, or None."""
        if features.EARLY_ARM:
            g = self.arm_goal(obs, enemies)
            if g is not None:
                me = obs["self"]
                return g, None, (me.get("energy") or 0) > DROP_BOOST_E and bool(me.get("grounded", True)), "arm"
        if features.DROP_CAPTURE:
            d = self.drop_goal(obs, enemies)
            if d is not None:
                return self.drop_legs(obs, d)
        return None
