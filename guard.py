"""v9 experiment H, features.LASER_GUARD: get out of a charging laser's line, or short it.

A laser charges for up to 0.75 s (everyone who sees the shooter sees `charging: true` and its aim, and
`laser_charge` carries 140 m) and then fires a 315-damage hitscan beam along its aim. So, each decision:

  Chargers: a seen or tracked enemy mech with `charging`, aimed at us (its torso within GUARD_AIM_RAD of
    us, or its aim line within GUARD_AIM_M of us) and with a clear line from its muzzle to us; or a
    laser_charge heard in the last GUARD_SOUND_AGE_S that no visible charger explains, placed along its
    bearing at SOUND_DIST_M for its distance class (if that point has a line to us).
  Short it: a charger within GUARD_LIGHTNING_M with a clear line, while we hold a lightning gun with the
    energy for a shot and a cooldown it can beat: it becomes the target (lightning makes it lose the charge
    and blocks a new one for short_s, 2 s). The legs carry on as usual.
  Else break the line: if no charger can see our torso center and top, nothing to do. Otherwise the nearest
    spot (GUARD_DIRS x radii up to what a boost covers in GUARD_REACH_S) on the ground, not in a building,
    reachable in a straight line and inside the zone, that none of them can see; boost to it. No such spot:
    boost sideways across the nearest charger's line (it fires where it aims when it lets go).
"""
import math

from perception import LATENCY_S, _wrap
from safety import EDGE_MARGIN_M

GUARD_AIM_RAD = 0.35       # its torso this close (rad) to the bearing to us: charging at us
GUARD_AIM_M = 10.0         # ...or its aim line passes this close to us
GUARD_SOUND_AGE_S = 0.5    # a laser_charge heard this recently
SOUND_DIST_M = {"near": 40.0, "mid": 100.0, "far": 140.0}  # where a heard charger is placed
SOUND_MATCH_RAD = 0.35     # a heard charge this close in bearing to a seen charging mech is that mech
GUARD_LIGHTNING_M = 43.0   # short a charger this close (lightning range 45 m, minus a margin)
GUARD_REACH_S = 0.75       # the cover spot must be reachable in this long (a full charge)
GUARD_DIRS = 12
GUARD_MIN_R = 4.0
GUARD_STEPS = 3            # radii from GUARD_MIN_R to the reach
GUARD_MAX_CHECKS = 32      # line-of-sight checks per plan (time bound)
GUARD_REPLAN_S = 0.3       # keep a plan this long while the same chargers threaten
GUARD_SIDE_M = 12.0        # no hidden spot: boost this far sideways
GUARD_MAX_CLIMB_M = 4.0    # spots this much higher or lower than us are not reachable in time
MUZZLE_M = 7.0
TOP_FRAC = 0.85            # we are hidden when the line to our center and the one to our top are blocked


class LaserGuardMixin:
    """Uses self.guard (re-plan time, key, goal), self.world, self.strafe_dir, _edge and _lane_ok."""

    def laser_chargers(self, obs, enemies):
        """[(eye (x, y, z), mech or None)] of lasers charging at us that can see us."""
        me = obs["self"]
        x, y, z = me["pos"]
        h = (me.get("stats") or {}).get("height", 9.0)
        us = (x, y, z + 0.5 * h)
        out, charging = [], []
        for m in enemies:
            if not m.get("charging"):
                continue
            mx, my, mz = m["pos"]
            b = math.atan2(y - my, x - mx)
            charging.append(b)
            d = math.hypot(x - mx, y - my)
            aim = m.get("aim") or [b, 0.0]
            err = abs(_wrap(aim[0] - b))
            if err > GUARD_AIM_RAD and not (err < math.pi / 2 and math.sin(err) * d <= GUARD_AIM_M):
                continue
            eye = (mx, my, mz + MUZZLE_M)
            if self.clear(eye, us):
                out.append((eye, m))
        for s in obs.get("sounds") or []:
            if s.get("kind") != "laser_charge" or (s.get("age") or 0.0) > GUARD_SOUND_AGE_S:
                continue
            b = s.get("bearing")
            if not isinstance(b, (int, float)) or not math.isfinite(b):
                continue
            if any(abs(_wrap(b + math.pi - cb)) <= SOUND_MATCH_RAD for cb in charging):
                continue  # a charging mech we see (judged above)
            r = SOUND_DIST_M.get(s.get("dist"), SOUND_DIST_M["mid"])
            px, py = x + r * math.cos(b), y + r * math.sin(b)
            pz = self.world.ground(px, py) if self.world is not None else z
            eye = (px, py, pz + MUZZLE_M)
            if self.clear(eye, us):
                out.append((eye, None))
        return out

    def _short_target(self, obs, chargers):
        """The nearest seen charger our lightning can hit now, or None."""
        me = obs["self"]
        arc = next((w for w in me["weapons"] if w and w["kind"] == "lightning"), None)
        if arc is None:
            return None
        st = arc.get("stats") or {}
        if (me.get("energy") or 0) < st.get("energy", 3.0) or (arc.get("cooldown") or 0) > st.get("cooldown", 0.25):
            return None
        origin = self._origin(me)
        best = None
        for _, m in chargers:
            if m is None:
                continue
            _, height = self._size(m)
            q = self.predict(m, LATENCY_S, 0.5 * height)
            d = math.dist(origin, q)
            if d <= min(GUARD_LIGHTNING_M, st.get("range", 45.0) - 2.0) and self.clear(origin, q):
                if best is None or d < best[0]:
                    best = (d, m)
        return best and best[1]

    def _seen_by(self, eyes, px, py, pz, h):
        return any(self.clear(e, (px, py, pz + 0.5 * h)) or self.clear(e, (px, py, pz + TOP_FRAC * h)) for e in eyes)

    def _cover_goal(self, obs, eyes):
        """(x, y) of the nearest spot reachable in GUARD_REACH_S that no eye sees, else a sideways spot."""
        me, zn, t = obs["self"], obs["zone"], obs["t"]
        x, y, z = me["pos"]
        st = me.get("stats") or {}
        h = st.get("height", 9.0)
        w = self.world
        reach = max(GUARD_MIN_R, st.get("boost_speed", 19.5) * GUARD_REACH_S * 0.9)
        radii = [GUARD_MIN_R + (reach - GUARD_MIN_R) * k / max(GUARD_STEPS - 1, 1) for k in range(GUARD_STEPS)]
        checks = 0
        if w is not None:
            for r in radii:
                for k in range(GUARD_DIRS):
                    a = 2 * math.pi * k / GUARD_DIRS
                    px, py = x + r * math.cos(a), y + r * math.sin(a)
                    if self._edge(zn, t, px, py) < EDGE_MARGIN_M:
                        continue
                    pz = w.ground(px, py)
                    if (abs(pz - z) > GUARD_MAX_CLIMB_M or w.solid_at(px, py, pz + 1.0)
                            or w.solid_at(px, py, pz + h - 1.0) or w.blocked((x, y, z + 2.0), (px, py, pz + 2.0))):
                        continue
                    checks += 2 * len(eyes)
                    if not self._seen_by(eyes, px, py, pz, h):
                        return px, py
                    if checks >= GUARD_MAX_CHECKS:
                        break
                if checks >= GUARD_MAX_CHECKS:
                    break
        # nowhere to hide in time: across the nearest charger's line
        ex, ey, _ = min(eyes, key=lambda e: math.hypot(e[0] - x, e[1] - y))
        b = math.atan2(y - ey, x - ex)
        for s in (self.strafe_dir, -self.strafe_dir):
            a = b + s * math.pi / 2
            if self._lane_ok(obs, a):
                self.strafe_dir = s
                return x + GUARD_SIDE_M * math.cos(a), y + GUARD_SIDE_M * math.sin(a)
        return None

    def laser_guard(self, obs, enemies):
        """None, ("short", charger mech) or ("cover", (x, y) goal or None)."""
        chargers = self.laser_chargers(obs, enemies)
        if not chargers:
            self.guard = None
            return None
        m = self._short_target(obs, chargers)
        if m is not None:
            return "short", m
        t = obs["t"]
        key = tuple(sorted(str(c[1]["id"]) if c[1] is not None else "sound" for c in chargers))
        if self.guard is not None and t < self.guard[0] and self.guard[1] == key:
            return "cover", self.guard[2]
        goal = self._cover_goal(obs, [e for e, _ in chargers])
        self.guard = (t + GUARD_REPLAN_S, key, goal)
        return "cover", goal
