"""v9 experiment SQUAD_AVOID: two or more enemy mechs engaging us.

Engaging = hit us in the last HIT_RECENT_S, or torso aimed within SQUAD_AIM_TOL of us with us inside its
longest weapon's range. Legs: when two or more of them have a line of fire on us, walk (boosting) to a
nearby spot where fewer do, ideally one: SQUAD_DIRS directions times SQUAD_STEPS seconds of walking, in
the zone with room to stand, fewest lines first, then the shortest walk. Re-planned every SQUAD_REPLAN_S;
a plan stops at SQUAD_BUDGET_S (line checks over the terrain are the cost) with the best spot so far.
Target: the most isolated clear enemy (farthest from its nearest fellow), sticky, a nearly dead
current target finished first.
"""
import math
import time

from perception import _hp, _hp_frac, _wrap
from safety import EDGE_MARGIN_M
from targeting import FINISH_FRAC, HIT_RECENT_S

SQUAD_AIM_TOL = 0.3        # rad: an enemy torso aimed this close to us is engaging us
SQUAD_MAX = 4              # at most this many engagers (nearest first) are considered
SQUAD_DIRS = 12           # tried in a spread-out order, so a plan cut short has still looked all round
SQUAD_ORDER = (0, 6, 3, 9, 1, 7, 4, 10, 2, 8, 5, 11)
SQUAD_BUDGET_S = 0.004
SQUAD_STEPS = (1.0, 2.0)   # seconds of walking to each candidate spot (the nearer ring is tried first)
SQUAD_REPLAN_S = 0.5
SQUAD_ARRIVE_M = 2.0
ISO_STICKY_M = 20.0        # switch target only to one this much more isolated
WEAPON_RANGE = {"gun": 230.0, "laser": 280.0, "mortar": 235.0, "lightning": 45.0}


class SquadMixin:
    """Uses self.engaging (this decision's engagers), self.squad (replan time, goal or None), self.hits,
    self.world, self._edge (safety.py), self._walk_mps and self._size."""

    def engagers(self, obs, enemies):
        """Enemy mechs engaging us, nearest first (at most SQUAD_MAX)."""
        t = obs["t"]
        x, y = obs["self"]["pos"][0], obs["self"]["pos"][1]
        recent = {h[1] for h in self.hits if t - h[0] <= HIT_RECENT_S}
        out = []
        for m in enemies:
            if len(out) >= SQUAD_MAX:
                break
            if m["id"] in recent:
                out.append(m)
                continue
            aim = m.get("aim")
            if aim is None:
                continue
            mx, my = m["pos"][0], m["pos"][1]
            reach = max((WEAPON_RANGE.get(k, 0.0) for k in m.get("weapons") or ()), default=WEAPON_RANGE["gun"])
            if (math.hypot(x - mx, y - my) <= reach
                    and abs(_wrap(aim[0] - math.atan2(y - my, x - mx))) <= SQUAD_AIM_TOL):
                out.append(m)
        return out

    def _lines(self, engagers, p, stop=99):
        """How many engagers have a clear line from their muzzle to point p (counting stops at `stop`)."""
        n = 0
        for m in engagers:
            _, h = self._size(m)
            if self.clear((m["pos"][0], m["pos"][1], m["pos"][2] + 0.78 * h), p):
                n += 1
                if n >= stop:
                    break
        return n

    def squad_goal(self, obs):
        """(x, y) to walk to while two or more engagers have a line on us, or None."""
        eng = self.engaging
        if len(eng) < 2:
            self.squad = None
            return None
        t = obs["t"]
        if self.squad is not None and t < self.squad[0]:
            g = self.squad[1]
            return None if g is None or math.dist(g, obs["self"]["pos"][:2]) <= SQUAD_ARRIVE_M else g
        me = obs["self"]
        x, y, z = me["pos"]
        h = (me.get("stats") or {}).get("height", 9.0)
        now = self._lines(eng, (x, y, z + 0.5 * h))
        goal = None
        if now >= 2:
            walk = self._walk_mps(me)
            best = None
            deadline = time.perf_counter() + SQUAD_BUDGET_S
            for secs in SQUAD_STEPS:
                for k in SQUAD_ORDER:
                    if time.perf_counter() > deadline:
                        break
                    a = 2 * math.pi * k / SQUAD_DIRS
                    px, py = x + walk * secs * math.cos(a), y + walk * secs * math.sin(a)
                    if self._edge(obs["zone"], t, px, py) < EDGE_MARGIN_M:
                        continue
                    pz = z
                    if self.world is not None:
                        pz = self.world.ground(px, py)
                        if self.world.solid_at(px, py, pz + 1.0) or self.world.solid_at(px, py, pz + h - 1.0):
                            continue
                    n = self._lines(eng, (px, py, pz + 0.5 * h), now if best is None else best[0])
                    if n < (now if best is None else best[0]):
                        best = (n, (px, py))
                        if n == 0:
                            break
                if best is not None and best[0] <= 1:
                    break  # the nearer ring already has a spot with at most one line: don't walk further
            if best is not None:
                goal = best[1]
        self.squad = (t + SQUAD_REPLAN_S, goal)
        return goal

    def isolated_target(self, clear, enemies):
        """The clear mech farthest from its nearest fellow enemy (sticky; a nearly dead target kept)."""
        def iso(m):
            return min((math.dist(m["pos"], o["pos"]) for o in enemies if o["id"] != m["id"]), default=1e9)
        scored = [(iso(c[2]), -_hp(c[2]), c[2]) for c in clear]
        best_iso, _, best = max(scored, key=lambda s: (s[0], s[1]))
        cur = next((s for s in scored if s[2]["id"] == self.target_id), None)
        if cur is not None and (_hp_frac(cur[2]) < FINISH_FRAC or cur[0] >= best_iso - ISO_STICKY_M):
            return cur[2]
        return best
