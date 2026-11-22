"""v9 experiment, features.STUCK_ESCAPE: notice when the legs push into a wall, and get past it.

In arena matches MNUCG and AGTRR the zone walk pressed straight into a building (building 97, 21 m tall) or the
stadium wall for up to 34 s at a time, about a third of the time alive, and both zone-damage stretches came
from that. Escalation, as protocol.md section 7 allows (walls stop you; jump and jet until your feet clear the
edge, then move over it):

  Detect: for STUCK_WINDOW_S the legs asked for at least STUCK_MIN_MOVE of full speed (not a fight's strafe,
    not standing, not already climbing), we moved less than STUCK_DISP_M, and the map has a wall (or a terrain
    step) within STUCK_PROBE_M ahead.
  1. Jet over: the solid thing STUCK_PROBE_M ahead along the move has its top within climb range (energy at
     STUCK_ENERGY_PER_M per metre + STUCK_SPARE_ENERGY): jump, then boost (jet) while pushing the same way,
     until our feet are STUCK_CLEAR_M above that top and we have moved on, or the energy runs low.
  2. Else slide: the open direction (STUCK_LANE_M clear) that turns least from the goal (STUCK_SLIDE_DEVS),
     trying first the side where the wall ends sooner (probed every STUCK_SIDE_STEP_M up to STUCK_SIDE_MAX_M);
     nothing open: back off. Boost if the energy allows; stop once the way to the goal is open again
     (and we moved 2 m), or after STUCK_SLIDE_MAX_S.
  Once a jet's feet clear the top, it stops jetting and moves over the edge (unstick/over) until we have moved
  on or landed on the roof.
  3. A second stuck at the same spot within STUCK_REPEAT_S tries the other way (the other side, or a slide
     after a jet that didn't clear). Later: hand over to a route (step 2, the walkability grid).
"""
import collections
import math

STUCK_WINDOW_S = 0.3   # quick, because a wall must also be ahead on the map (a reversing strafe isn't stuck)
STUCK_MIN_MOVE = 0.5
STUCK_DISP_M = 1.0
# modes whose legs aren't walking to a goal (a fight's circling, a dodge, a hold, K's own climb): never "stuck"
STUCK_FREE_MODES = ("strafe", "duel", "dodge", "look", "roof", "climb", "ambush", "guard", "strafe/npc", "duel/npc")
STUCK_PROBE_M = (2.8, 3.5, 4.5, 6.0)
STUCK_ENERGY_PER_M = 1.6
STUCK_SPARE_ENERGY = 8.0
STUCK_MAX_CLIMB_M = 55.0
STUCK_CLEAR_M = 1.0
STUCK_JET_MAX_S = 6.0
STUCK_SIDE_STEP_M = 4.0
STUCK_SIDE_MAX_M = 48.0
STUCK_SLIDE_MAX_S = 4.0
STUCK_SLIDE_DEVS = tuple(math.radians(d) for d in (30, 60, 90, 120, 150))  # turns from the goal, least first
STUCK_LANE_M = (3.0, 6.0)
STUCK_LANE_HALF_W = 2.6  # our radius 2.5, plus a little
STUCK_SLIDE_BOOST_ENERGY = 30.0
STUCK_REPEAT_S = 6.0
STUCK_REPEAT_M = 6.0
STUCK_HISTORY = 12


class StuckMixin:
    """Uses self.stuck_hist (deque), self.escape (dict or None), self.last_stuck, self.world."""

    def _wall_ahead(self, x, y, z, h, a):
        """(distance to the first solid thing along yaw a within STUCK_PROBE_M, the tallest top along the
        whole probe), or None. The tallest: at a corner the thing behind the first wall may stand higher."""
        w = self.world
        first, tallest = None, None
        for d in STUCK_PROBE_M:
            px, py = x + d * math.cos(a), y + d * math.sin(a)
            tops = [top for base, top in w.tops_at(px, py) if base <= z + h - 0.5 and top >= z + 0.5]
            g = w.ground(px, py)
            if g > z + 1.2:  # a terrain step
                tops.append(g)
            if tops:
                first = d if first is None else first
                tallest = max(tops + ([tallest] if tallest is not None else []))
        return None if first is None else (first, tallest)

    def _side_free_at(self, x, y, z, h, a, side):
        """Metres along the wall (perpendicular `side` of yaw a) to where the way a opens, or None."""
        n = a + side * math.pi / 2
        k = STUCK_SIDE_STEP_M
        while k <= STUCK_SIDE_MAX_M:
            sx, sy = x + k * math.cos(n), y + k * math.sin(n)
            if self.world.solid_at(sx, sy, z + 1.0) or self.world.solid_at(sx, sy, z + h - 1.0):
                return None  # a wall across the slide too
            if self._wall_ahead(sx, sy, z, h, a) is None:
                return k
            k += STUCK_SIDE_STEP_M
        return None

    def _plan_escape(self, obs, a, repeat):
        me, t = obs["self"], obs["t"]
        x, y, z = me["pos"]
        h = (me.get("stats") or {}).get("height", 9.0)
        energy = me.get("energy") or 0.0
        wall = self._wall_ahead(x, y, z, h, a)
        climb = None if wall is None else wall[1] - z + STUCK_CLEAR_M
        can_jet = (climb is not None and climb <= STUCK_MAX_CLIMB_M
                   and energy >= STUCK_ENERGY_PER_M * climb + STUCK_SPARE_ENERGY)
        if can_jet and not (repeat and repeat.get("kind") == "jet"):
            return {"kind": "jet", "a": a, "top": wall[1], "t0": t, "x0": x, "y0": y}
        # slide: the side whose wall ends sooner first (else the strafe side), the least turn from the goal that is open
        ends = {s: self._side_free_at(x, y, z, h, a, s) for s in (1, -1)}
        order = sorted((1, -1), key=lambda s: (ends[s] is None, ends[s] or 0.0, s != self.strafe_dir))
        if repeat and repeat.get("kind") == "slide":  # the other side this time
            order = [-repeat["side"], repeat["side"]]
        for dev in STUCK_SLIDE_DEVS:
            for s in order:
                b = a + s * dev
                if self._lane_free(x, y, z, h, b):
                    return {"kind": "slide", "a": a, "b": b, "side": s, "t0": t, "x0": x, "y0": y}
        return {"kind": "slide", "a": a, "b": a + math.pi, "side": order[0], "t0": t, "x0": x, "y0": y}  # back off

    def _lane_free(self, x, y, z, h, b):
        """Open ground STUCK_LANE_M along yaw b, wide enough for us (the line and STUCK_LANE_HALF_W to each side:
        no prism at knee or head height, no tall terrain step)?"""
        w = self.world
        cb, sb = math.cos(b), math.sin(b)
        for d in STUCK_LANE_M:
            for off in (0.0, STUCK_LANE_HALF_W, -STUCK_LANE_HALF_W):
                px, py = x + d * cb - off * sb, y + d * sb + off * cb
                if w.solid_at(px, py, z + 1.5) or w.solid_at(px, py, z + h - 1.0) or w.ground(px, py) > z + 1.2:
                    return False
        return True

    def stuck_step(self, obs, move, mode):
        """None (carry on), or (move, boost, jump, mode) for an escape in progress."""
        me, t = obs["self"], obs["t"]
        x, y, z = me["pos"]
        if self.world is None:
            return None
        hist = self.stuck_hist
        n = math.hypot(move[0], move[1])
        hist.append((t, x, y, n >= STUCK_MIN_MOVE and mode.split(" ")[0] not in STUCK_FREE_MODES, math.atan2(move[1], move[0])))
        while len(hist) > STUCK_HISTORY:
            hist.popleft()
        e = self.escape
        if e is not None:
            out = self._continue_escape(obs, e)
            if out is not None:
                return out
            self.escape = None
            hist.clear()
            return None
        recent = [s for s in hist if t - s[0] <= STUCK_WINDOW_S + 1e-6]
        h = (me.get("stats") or {}).get("height", 9.0)
        if (len(recent) >= 3 and recent[-1][0] - recent[0][0] >= STUCK_WINDOW_S - 0.05 and all(s[3] for s in recent)
                and max(math.hypot(s[1] - x, s[2] - y) for s in recent) < STUCK_DISP_M
                and self._wall_ahead(x, y, z, h, recent[-1][4]) is not None):
            a = recent[-1][4]
            last = self.last_stuck
            repeat = (last if last is not None and t - last["t0"] <= STUCK_REPEAT_S
                      and math.hypot(last["x0"] - x, last["y0"] - y) <= STUCK_REPEAT_M else None)
            self.escape = self._plan_escape(obs, a, repeat)
            self.last_stuck = self.escape
            hist.clear()
            return self._continue_escape(obs, self.escape)
        return None

    def _continue_escape(self, obs, e):
        me, t = obs["self"], obs["t"]
        x, y, z = me["pos"]
        energy = me.get("energy") or 0.0
        grounded = me.get("grounded", True)
        a = e["a"]
        if e["kind"] == "jet":
            moved = math.hypot(x - e["x0"], y - e["y0"])
            if t - e["t0"] > STUCK_JET_MAX_S or (energy < 3.0 and not grounded and z < e["top"]):
                return None
            if z >= e["top"] + STUCK_CLEAR_M or e.get("over"):
                # feet clear the edge: stop jetting and move over it (protocol.md section 7 "Ledges")
                e["over"] = True
                if moved >= 4.0 or (grounded and z >= e["top"] - 0.5):
                    return None  # over it (on the roof or past): the normal legs take it from here
                return [math.cos(a), math.sin(a)], False, False, "unstick/over"
            return [math.cos(a), math.sin(a)], True, grounded, "unstick/jet"
        # slide along the open direction until the way we want to go opens up (or time runs out)
        b = e["b"]
        gone = math.hypot(x - e["x0"], y - e["y0"])
        h = (me.get("stats") or {}).get("height", 9.0)
        if t - e["t0"] > STUCK_SLIDE_MAX_S or (gone >= 2.0 and self._wall_ahead(x, y, z, h, a) is None):
            return None
        return [math.cos(b), math.sin(b)], energy >= STUCK_SLIDE_BOOST_ENERGY, False, "unstick/slide"


def new_history():
    return collections.deque()
