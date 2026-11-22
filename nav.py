"""Navigation step 2, features.NAV_ROUTE: routes over the precomputed ground grid (tools/navgrid.py ->
nav/ground.nav.gz) instead of straight lines into buildings.

  Grid: 2 m cells; each stores the 8 directions it can be left in (room for our body, no corner cutting, the
    50 degree slope rule). Loaded once at start-up, and only if its sha256 matches the map in hello.map.dir
    (a different map: no routes, the straight lines and STUCK_ESCAPE as before).
  Goal: a walking goal (the zone spot first; cubes, crates, the laser fetch and I's approach too) further than
    NAV_DIRECT_M, or without a straight grid walk to it, gets a route. Its cell is snapped to the nearest
    walkable one within NAV_SNAP_CELLS.
  Plan: weighted A* (octile heuristic x NAV_WEIGHT) with a time budget of NAV_BUDGET_MS per decision, resumed on
    the next decision (meanwhile we keep walking straight), given up after NAV_MAX_EXPANSIONS. Re-planned when
    the goal moves NAV_REGOAL_M, when we stray NAV_OFF_PATH_M from the path, or when STUCK_ESCAPE fires.
  Follow: walk to the farthest path cell within NAV_LOOK_CELLS ahead that a straight grid walk reaches.
"""
import gzip
import hashlib
import heapq
import json
import math
import os
import time

NAV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nav", "ground.nav.gz")
NAV_DIRECT_M = 12.0
NAV_SNAP_CELLS = 8
NAV_WEIGHT = 1.5
NAV_BUDGET_MS = 2.0
NAV_MAX_EXPANSIONS = 60000
NAV_REGOAL_M = 12.0
NAV_OFF_PATH_M = 8.0
NAV_LOOK_CELLS = 25
NAV_RETRY_S = 3.0
NAV_MODES = ("walk", "sneak", "zone!", "cube", "fetch", "approach")
SQRT2 = math.sqrt(2.0)


class NavGrid:
    def __init__(self, header, masks):
        self.ox, self.oy = header["origin"]
        self.cell = float(header["cell"])
        self.nx, self.ny = header["nx"], header["ny"]
        self.dirs = [tuple(d) for d in header["dirs"]]
        self.masks = masks
        self.steps = [(dx, dy, dy * self.nx + dx, SQRT2 if dx and dy else 1.0) for dx, dy in self.dirs]
        self.bit = {d: k for k, d in enumerate(self.dirs)}

    @classmethod
    def load(cls, map_dir, path=NAV_FILE):
        """The grid for this map, or None (no file, or built for another map)."""
        if not map_dir or not os.path.exists(path):
            return None
        with gzip.open(path, "rb") as f:
            data = f.read()
        nl = data.index(b"\n")
        header = json.loads(data[:nl])
        h = hashlib.sha256()
        for name in ("world.json", "terrain.i16"):
            with open(os.path.join(map_dir, "v2", name), "rb") as fh:
                h.update(fh.read())
        if h.hexdigest() != header.get("sha256"):
            return None
        masks = data[nl + 1:]
        if len(masks) != header["nx"] * header["ny"]:
            return None
        return cls(header, masks)

    def cell_of(self, x, y):
        return int(round((x - self.ox) / self.cell)), int(round((y - self.oy) / self.cell))

    def center(self, c):
        return self.ox + c[0] * self.cell, self.oy + c[1] * self.cell

    def ok(self, c):
        return 0 <= c[0] < self.nx and 0 <= c[1] < self.ny and self.masks[c[1] * self.nx + c[0]] != 0

    def snap(self, c, rmax=NAV_SNAP_CELLS):
        """The nearest walkable cell to c within rmax cells (ring by ring), or None."""
        if self.ok(c):
            return c
        for r in range(1, rmax + 1):
            best = None
            for dx in range(-r, r + 1):
                for dy in (-r, r) if abs(dx) != r else range(-r, r + 1):
                    q = (c[0] + dx, c[1] + dy)
                    if self.ok(q):
                        d = dx * dx + dy * dy
                        if best is None or d < best[0]:
                            best = (d, q)
            if best:
                return best[1]
        return None

    def line_ok(self, a, b):
        """Can we walk the straight line of cells from a to b (each step allowed by its cell's mask)?"""
        (x0, y0), (x1, y1) = a, b
        dx, dy = x1 - x0, y1 - y0
        n = max(abs(dx), abs(dy))
        if n == 0:
            return self.ok(a)
        px, py = x0, y0
        for k in range(1, n + 1):
            cx, cy = x0 + round(dx * k / n), y0 + round(dy * k / n)
            sx, sy = cx - px, cy - py
            if not (0 <= px < self.nx and 0 <= py < self.ny):
                return False
            m = self.masks[py * self.nx + px]
            bit = self.bit.get((sx, sy))
            if bit is None or not (m >> bit) & 1:
                return False
            px, py = cx, cy
        return True


class AStar:
    """A resumable weighted A* from start to goal (cells) on a NavGrid."""

    def __init__(self, grid, start, goal):
        self.g, self.start, self.goal = grid, start, goal
        nx = grid.nx
        self.gi = goal[1] * nx + goal[0]
        si = start[1] * nx + start[0]
        self.cost = {si: 0.0}
        self.parent = {si: -1}
        self.open = [(self._h(start[0], start[1]), 0.0, si)]
        self.expanded = 0
        self.path = None
        self.failed = False

    def _h(self, x, y):
        dx, dy = abs(x - self.goal[0]), abs(y - self.goal[1])
        return NAV_WEIGHT * (max(dx, dy) + (SQRT2 - 1.0) * min(dx, dy))

    def run(self, deadline):
        """Expand until done or past `deadline` (perf_counter). True when finished (path or failed)."""
        grid, masks, nx = self.g, self.g.masks, self.g.nx
        cost, parent, open_, steps, gi = self.cost, self.parent, self.open, self.g.steps, self.gi
        push, pop = heapq.heappush, heapq.heappop
        n = 0
        while open_:
            f, gc, ci = pop(open_)
            if gc > cost.get(ci, 1e18):
                continue
            if ci == gi:
                self.path = self._trace(ci)
                return True
            self.expanded += 1
            m = masks[ci]
            cx, cy = ci % nx, ci // nx
            for k, (dx, dy, di, w) in enumerate(steps):
                if not (m >> k) & 1:
                    continue
                ni = ci + di
                ng = gc + w
                if ng < cost.get(ni, 1e18):
                    cost[ni] = ng
                    parent[ni] = ci
                    push(open_, (ng + self._h(cx + dx, cy + dy), ng, ni))
            n += 1
            if self.expanded >= NAV_MAX_EXPANSIONS:
                self.failed = True
                return True
            if n & 63 == 0 and time.perf_counter() > deadline:
                return False
        self.failed = True
        return True

    def _trace(self, ci):
        nx, out = self.g.nx, []
        while ci != -1:
            out.append((ci % nx, ci // nx))
            ci = self.parent[ci]
        out.reverse()
        return out


class NavMixin:
    """Uses self.navgrid (NavGrid or None) and self.nav (state dict or None)."""

    def nav_reset(self):
        self.nav = None

    def nav_waypoint(self, obs, goal):
        """Where to walk now on the way to `goal` (x, y): a route waypoint, or `goal` itself (no grid, a straight
        walk works, or the route is still being planned)."""
        g = self.navgrid
        if g is None:
            return goal
        me, t = obs["self"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        here = g.snap(g.cell_of(x, y), 3)
        if here is None:
            return goal
        gc = g.snap(g.cell_of(goal[0], goal[1]))
        if gc is None:
            return goal
        if math.hypot(goal[0] - x, goal[1] - y) <= NAV_DIRECT_M and g.line_ok(here, gc):
            return goal
        nav = self.nav
        if nav is None or math.hypot(nav["goal"][0] - goal[0], nav["goal"][1] - goal[1]) > NAV_REGOAL_M:
            if g.line_ok(here, gc):  # a straight walk all the way: no search needed
                self.nav = {"goal": goal, "path": None, "search": None, "straight": True, "retry": 0.0}
                return goal
            nav = self.nav = {"goal": goal, "path": None, "search": AStar(g, here, gc), "straight": False,
                              "retry": 0.0, "i": 0}
        if nav.get("straight"):
            return goal
        if nav["path"] is None:
            s = nav["search"]
            if s is None:
                if t < nav["retry"]:
                    return goal
                s = nav["search"] = AStar(g, here, gc)
            t0 = self.decision_t0 if self.decision_t0 is not None else time.perf_counter()
            if s.run(max(time.perf_counter(), t0) + NAV_BUDGET_MS / 1000.0):
                if s.failed:
                    nav["search"], nav["retry"] = None, t + NAV_RETRY_S
                    return goal
                nav["path"], nav["search"], nav["i"] = s.path, None, 0
            else:
                return goal  # still planning: walk straight meanwhile (STUCK_ESCAPE is the safety net)
        path = nav["path"]
        # where are we on it (search a window ahead of the last index)
        i0 = nav["i"]
        best = None
        for k in range(i0, min(len(path), i0 + NAV_LOOK_CELLS * 2)):
            cx, cy = g.center(path[k])
            d = math.hypot(cx - x, cy - y)
            if best is None or d < best[0]:
                best = (d, k)
        if best is None or best[0] > NAV_OFF_PATH_M:
            self.nav = None  # strayed: re-plan next decision
            return goal
        nav["i"] = best[1]
        far = best[1]
        for k in range(min(len(path) - 1, best[1] + NAV_LOOK_CELLS), best[1], -1):
            if g.line_ok(here, path[k]):
                far = k
                break
        if far >= len(path) - 1:
            return goal
        return g.center(path[far])
