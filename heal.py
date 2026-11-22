"""Heal (v7, features.HEAL): disengage at low HP so regen can start.

Regen (protocol.md, HP): regen_per_s (45) HP/s after regen_delay (5 s) without dealing OR taking damage,
and never outside the zone. So healing means no shots at all (crates included) and getting out of sight.

  - On below HEAL_ENTER_FRAC of max HP, off at HEAL_EXIT_FRAC (hysteresis), and off for good once the zone
    radius is HEAL_FINAL_R or less (the last phases: just fight).
  - Suspended for a decision while a mech or NPC drone is within HEAL_FIGHT_M: fight back as v6 does.
  - Legs: zone hurry > dodge > hide spot > the normal legs. The torso faces the nearest threat (hits on the
    front arc do 1x, not 1.5x) without firing, and reloads a partly used magazine.
  - Hide spot: stay put if buildings already hide us from every threat. Otherwise our own spot or one of
    HIDE_DIRS x HIDE_RADII around us, inside the next circle (or, while we are outside it, no further out
    than we can walk back from before it closes), not in a building, reachable in a straight line. Scored by buildings
    blocking the line from each threat (COVER_W each), dense canopy overhead (CANOPY_W), distance gained
    from the nearest threat and travel cost. Re-planned every HIDE_REPLAN_S or when the threats change.
"""
import math

import features
from perception import _finite, _hp
from safety import EDGE_MARGIN_M, SPARE_S, zone_at

HIDE_DIRS = 12            # candidate directions around us
HIDE_RADII = (12.0, 28.0)  # ...at these distances
HIDE_THREATS = 3          # cover is judged against at most this many nearest threats
HIDE_MAX_CHECKS = 40      # line-of-sight checks per re-plan (time bound)
HIDE_SIGHT_M = 210.0      # a threat further than this can't see us (vision range 200 m)
HIDE_REPLAN_S = 0.5       # keep a hide spot this long unless the threats change
HIDE_ARRIVE_M = 3.0       # closer than this to the spot = stand still
HIDE_BOOST_M = 120.0      # boost to the spot while a threat is this close
HIDE_NEXT_MARGIN_M = 10.0  # hide spots stay this far inside the next zone circle
NPC_THREAT_M = 60.0       # NPC drones further than this are left alone (aggro range 45 m)
HIT_THREAT_M = 80.0       # an unseen attacker is placed this far along its hit bearing
EYE_M = 7.0               # a threat looks from this high above its feet
TORSO_FRAC = 0.7          # ...at this fraction of our height
COVER_W, CANOPY_W, GAIN_W, TRAVEL_W = 3.0, 1.0, 0.02, 0.01


HEAL_QUIET_FRAC = 0.80    # v9 B4 (features.HEAL_QUIET): quiet below this fraction of max HP...
HEAL_QUIET_HIT_S = 3.0    # ...unless a mech hit us this recently
HEAL_QUIET_NEAR_M = 45.0  # ...or one is this close
HEAL_QUIET_ENEMY = 100    # ids below this are mechs (protocol.md)


class HealMixin:
    """Uses self.healing, self.hide, self.hits, self.world, _edge (safety.py) and the perception methods."""

    def heal_quiet(self, obs, enemies):
        """v9 B4: hold fire so regen's 5 s timer runs (protocol.md section 7: regen needs 5 s without dealing or
        taking damage)? Below HEAL_QUIET_FRAC of max HP, with no mech engaging us (none hit us in HEAL_QUIET_HIT_S,
        none aimed at us in its weapon's reach (squad.py's engagers), none within HEAL_QUIET_NEAR_M)."""
        if not features.HEAL_QUIET:
            return False
        me, t = obs["self"], obs["t"]
        if _hp(me) >= HEAL_QUIET_FRAC * (me.get("max_hp") or 1400.0):
            return False
        if any(t - ht <= HEAL_QUIET_HIT_S and isinstance(by, int) and 0 <= by < HEAL_QUIET_ENEMY
               for ht, by, _, _ in self.hits):
            return False
        if any(math.dist(m["pos"], me["pos"]) <= HEAL_QUIET_NEAR_M for m in enemies):
            return False
        return not self.engagers(obs, enemies)

    def heal_state(self, obs):
        """Should this decision disengage? Updates self.healing (always False with features.HEAL off)."""
        if not features.HEAL:
            return False
        me = obs["self"]
        max_hp = me.get("max_hp") or 1400.0
        hp = _hp(me)
        if zone_at(obs["zone"], obs["t"])[2] <= features.HEAL_FINAL_R:
            self.healing = False
        elif hp < features.HEAL_ENTER_FRAC * max_hp:
            self.healing = True
        elif hp >= features.HEAL_EXIT_FRAC * max_hp:
            self.healing = False
        if not self.healing:
            self.hide = None
            return False
        for m in obs["mechs"]:
            if m.get("kind") in ("mech", "npc") and math.dist(m["pos"], me["pos"]) <= features.HEAL_FIGHT_M:
                return False  # too close to run from: fight back
        return True

    def threats(self, obs):
        """[(dist, key, (x, y, eye z), mech or None)]: the HIDE_THREATS nearest known mechs, NPC drones within
        NPC_THREAT_M, and unseen recent attackers (placed along their hit bearing)."""
        me = obs["self"]
        x, y, z = me["pos"]
        out, seen = [], set()
        for m in obs["mechs"]:
            seen.add(m["id"])
            d = math.dist(m["pos"], me["pos"])
            if m["kind"] == "mech" or (m["kind"] == "npc" and d <= NPC_THREAT_M):
                px, py, pz = m["pos"]
                out.append((d, m["id"], (px, py, pz + EYE_M), m))
        last = {}
        for ht, by, _, bearing in self.hits:
            if by not in seen and _finite(bearing):
                last[by] = bearing
        for by, b in last.items():
            out.append((HIT_THREAT_M, by, (x + HIT_THREAT_M * math.cos(b), y + HIT_THREAT_M * math.sin(b), z + EYE_M), None))
        out.sort(key=lambda th: th[0])
        return out[:HIDE_THREATS]

    def hide_spot(self, obs, threats):
        """(goal or None, boost): where to wait out the regen delay. None goal = stay here."""
        me, t = obs["self"], obs["t"]
        x, y, z = me["pos"]
        key = tuple(sorted(str(th[1]) for th in threats))
        if self.hide is not None and t < self.hide[0] and self.hide[1] == key:
            goal = self.hide[2]
        else:
            goal = self._plan_hide(obs, threats)
            self.hide = (t + HIDE_REPLAN_S, key, goal)
        if goal is None or math.hypot(goal[0] - x, goal[1] - y) <= HIDE_ARRIVE_M:
            return None, False
        return goal, threats[0][0] <= HIDE_BOOST_M

    def _plan_hide(self, obs, threats):
        me, zn, t = obs["self"], obs["zone"], obs["t"]
        x, y, z = me["pos"]
        if not _finite(x, y, z):
            return None
        h = (me.get("stats") or {}).get("height", 9.0)
        (ncx, ncy), nr = zn["next_center"], zn["next_radius"]
        d0 = math.hypot(x - ncx, y - ncy)
        # Inside the next circle (with a margin); or, while we are outside it, no further out than we can
        # still walk back from before it closes (shrink_end - SPARE_S), and never further out than now
        # plus our longest hide step.
        inner = nr - HIDE_NEXT_MARGIN_M
        spare = max(zn["shrink_end"] - t - SPARE_S, 0.0) * self._walk_mps(me)
        limit = inner if d0 <= inner else min(max(d0, inner + spare), d0 + HIDE_RADII[-1])
        w = self.world
        near0 = min(math.hypot(x - p[0], y - p[1]) for _, _, p, _ in threats)

        scored = []
        cands = [(x, y)] + [(x + r * math.cos(a), y + r * math.sin(a))
                            for r in HIDE_RADII for a in (2 * math.pi * k / HIDE_DIRS for k in range(HIDE_DIRS))]
        for i, (px, py) in enumerate(cands):
            travel = math.hypot(px - x, py - y)
            if i and (math.hypot(px - ncx, py - ncy) > limit or self._edge(zn, t, px, py) < EDGE_MARGIN_M):
                continue
            pz = z
            if w is not None:
                pz = w.ground(px, py)
                if i and (w.solid_at(px, py, pz + 1.0) or w.solid_at(px, py, pz + h - 1.0)
                          or w.blocked((x, y, z + 2.0), (px, py, pz + 2.0))):
                    continue
            near = min(math.hypot(px - p[0], py - p[1]) for _, _, p, _ in threats)
            s = GAIN_W * (near - near0) - TRAVEL_W * travel
            if w is not None and w.under_canopy(px, py, pz + TORSO_FRAC * h):
                s += CANOPY_W
            scored.append([s, -i, px, py, pz])

        if w is not None:
            here = scored[0]  # our own spot (index 0 always passes): already hidden from all = stay put
            hidden = sum(1 for _, _, p, _ in threats if self._hidden(p, (x, y, z + TORSO_FRAC * h)))
            if hidden == len(threats):
                return None
            here[0] += COVER_W * hidden
            scored.sort(reverse=True)  # the best by distance get the cover checks first
            checks = len(threats)
            for c in scored:
                if c is here:
                    continue
                for _, _, p, _ in threats:
                    if checks >= HIDE_MAX_CHECKS:
                        break
                    checks += 1
                    if self._hidden(p, (c[2], c[3], c[4] + TORSO_FRAC * h)):
                        c[0] += COVER_W
        best = max(scored)
        return None if best[1] == 0 else (best[2], best[3])

    def _hidden(self, eye, q):
        """Can't a threat looking from `eye` see point q? Beyond HIDE_SIGHT_M it can't (and the line, which
        costs with its length, isn't checked); otherwise buildings or terrain must block the line."""
        return math.dist(eye, q) > HIDE_SIGHT_M or self.world.blocked(eye, q)

    def heal_aim(self, obs, threats, face):
        """[yaw, pitch] toward the nearest threat (or an unseen hit's bearing), or None."""
        if threats:
            _, _, p, m = threats[0]
            origin = self._origin(obs["self"])
            if m is not None:
                _, height = self._size(m)
                return list(self.angles(origin, self.predict(m, 0.0, 0.5 * height)))
            return [math.atan2(p[1] - origin[1], p[0] - origin[0]), 0.0]
        if face is not None:
            return [face, 0.0]
        return None
