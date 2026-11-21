"""Economy (v6): in quiet moments, shoot nearby crates and walk to cubes (each cube: +4% HP and damage,
and upgrade offers at 1/3/6/10 cubes). Upgrade preferences live in safety.pick_upgrade.

Quiet = no target, no hit bearing to face, no enemy mech in sight (or tracked), and no sound a mech
could have made heard within ~160 m in the last HEARD_S. A crate or cube is only worth it within
ECON_MAX_M, inside the next zone circle, on the ground (not a roof), and away from visible NPC drones.
v9 features.LIGHTNING: with the gun dropped (laser + lightning), crates are shot with lightning up close
or a fully charged laser (315 one-shots a 300 HP crate).
"""
import math

import features
from combat import aimed
from perception import _finite, _wrap

ECON_MAX_M = 80.0       # shoot crates / walk to cubes at most this far away (horizontal)
ECON_MARGIN_M = 10.0    # ...and at least this far inside the next zone circle
NPC_AVOID_M = 45.0      # ...and further than this from any visible NPC drone (their aggro range)
ROOF_M = 3.0            # higher than this above the terrain = on a roof (can't walk to its cube)
CRATE_CANDIDATES = 3    # clear-line checks for at most this many nearest crates per decision
HEARD_S = 3.0           # a sound this recent...
HEARD_DISTS = ("near", "mid")  # ...this close (< 160 m) means someone is around
NOT_MECH_SOUNDS = ("npc_gun", "drop")  # sounds that are not a mech nearby
CRATE_RADIUS, CRATE_HEIGHT = 1.4, 2.4  # defaults; the config's crates section overrides


class EconomyMixin:
    """Uses self.world, self.crate_radius/height and the perception methods (_origin, clear, angles)."""

    def quiet(self, obs, enemies, target, face):
        """Is now a good time for crates and cubes?"""
        if not features.ECONOMY or target is not None or face is not None or enemies:
            return False
        for s in obs.get("sounds") or []:
            if (s.get("kind") not in NOT_MECH_SOUNDS and s.get("dist") in HEARD_DISTS
                    and (s.get("age") or 0.0) <= HEARD_S):
                return False
        return True

    def _eligible(self, obs, p):
        """Horizontal distance to loot position p if it is worth going for, else None."""
        if not _finite(*p[:3]):
            return None
        x, y, z = obs["self"]["pos"]
        d = math.hypot(p[0] - x, p[1] - y)
        if d > ECON_MAX_M:
            return None
        zn = obs["zone"]
        (ncx, ncy), nr = zn["next_center"], zn["next_radius"]
        if math.hypot(p[0] - ncx, p[1] - ncy) > nr - ECON_MARGIN_M:
            return None
        for m in obs["mechs"]:
            if m["kind"] == "npc" and math.hypot(p[0] - m["pos"][0], p[1] - m["pos"][1]) <= NPC_AVOID_M:
                return None
        if self.world is not None:
            if p[2] > self.world.ground(p[0], p[1]) + ROOF_M:
                return None
        elif abs(p[2] - z) > 4.0:
            return None
        return d

    def crate_shot(self, obs):
        """(crate, aim, fire) for the nearest eligible crate with a clear gun line, or None."""
        me = obs["self"]
        gun = next(((i, w) for i, w in enumerate(me["weapons"]) if w and w["kind"] == "gun"), None)
        if gun is None and not features.LIGHTNING:
            return None
        crates = []
        for c in obs.get("crates") or []:
            d = self._eligible(obs, c["pos"])
            if d is not None:
                crates.append((d, c))
        if not crates:
            return None
        crates.sort(key=lambda dc: dc[0])
        if gun is None:
            return self._crate_shot_gunless(obs, crates)
        i, w = gun
        rng = (w.get("stats") or {}).get("range", 230.0)
        origin = self._origin(me)
        for _, c in crates[:CRATE_CANDIDATES]:
            q = (c["pos"][0], c["pos"][1], c["pos"][2] + 0.5 * self.crate_height)
            d = math.dist(origin, q)
            if d > rng or not self.clear(origin, q):
                continue
            yaw, pitch = self.angles(origin, q)
            fire = [False, False]
            fire[i] = (w.get("ammo", 0) > 0 and (w.get("reload") or 0) <= 0
                       and aimed(me["aim"], yaw, pitch, d, self.crate_radius, self.crate_height))
            return c, [yaw, pitch], fire
        return None

    def _crate_shot_gunless(self, obs, crates):
        """v9: (crate, aim, fire) with lightning (in range, energy, cone) or the laser (charge, release when
        full and on target), or None."""
        me = obs["self"]
        slots = {w["kind"]: (i, w) for i, w in enumerate(me["weapons"]) if w}
        origin = self._origin(me)
        for _, c in crates[:CRATE_CANDIDATES]:
            q = (c["pos"][0], c["pos"][1], c["pos"][2] + 0.5 * self.crate_height)
            d = math.dist(origin, q)
            yaw, pitch = self.angles(origin, q)
            fire = [False, False]
            if "lightning" in slots:
                i, w = slots["lightning"]
                ls = w.get("stats") or {}
                if (d <= ls.get("range", 45.0) - 2.0 and (me.get("energy") or 0) >= ls.get("energy", 3.0)
                        and self.clear(origin, q)):
                    fire[i] = ((w.get("cooldown") or 0) <= 0
                               and abs(_wrap(me["aim"][0] - yaw)) <= 0.4 * math.radians(ls.get("cone_deg", 30.0)))
                    return c, [yaw, pitch], fire
            if "laser" in slots:
                i, w = slots["laser"]
                ls = w.get("stats") or {}
                if d > ls.get("range", 280.0) or not self.clear(origin, q):
                    continue
                charge = w.get("charge") or 0.0
                if charge > 0:
                    full = charge >= ls.get("charge", 0.75)
                    fire[i] = not (full and aimed(me["aim"], yaw, pitch, d, self.crate_radius, self.crate_height))
                elif (w.get("cooldown") or 0) <= 0:
                    fire[i] = True
                return c, [yaw, pitch], fire
        return None

    def cube_goal(self, obs):
        """(x, y) of the nearest eligible cube, or None."""
        best = None
        for c in obs.get("cubes") or []:
            d = self._eligible(obs, c["pos"])
            if d is not None and (best is None or d < best[0]):
                best = (d, c["pos"][0], c["pos"][1])
        return None if best is None else (best[1], best[2])
