"""Static map geometry for line-of-shot and line-of-sight checks (protocol.md section 11).

Shots and sight are stopped by prisms (buildings, stadium, bridges) and terrain. Trees never stop
shots (they only fade sight), so they are ignored by `blocked`; `World.under_canopy` (v7 heal) says
whether a point sits inside a dense crown, i.e. is hidden from sight. `World.blocked(a, b)` says whether the straight
segment a -> b hits anything solid; `World.solid_at(x, y, z)` whether a point is inside a prism.
Prisms are bucketed in a CELL_M grid so a line only tests the prisms near it, and lines are clipped to
the terrain grid (nothing is out there), so the cost is bounded by the map size, not the line length.
"""
import json
import math
import os
import sys
from array import array

CELL_M = 32.0
CANOPY_MIN_R = 4.0  # crowns this wide (or more) put >= ~4 m of foliage on any line to a point under them:
                    # transmittance exp(-0.35 * 4) < canopyBlockT 0.25, so they hide us from sight


class World:
    def __init__(self, map_dir):
        with open(os.path.join(map_dir, "v2", "world.json")) as f:
            w = json.load(f)

        # Each prism: (minx, miny, maxx, maxy, base, top, edges) with edges a flat tuple x0,y0,x1,y1,...
        # over the ring and all holes. Even-odd crossing over all of them = inside ring, outside holes.
        self.prisms = []
        self.cells = {}
        for p in w["prisms"]:
            loops = [p["ring"]] + list(p.get("holes") or [])
            edges = []
            xs, ys = p["ring"][0::2], p["ring"][1::2]
            for loop in loops:
                n = len(loop) // 2
                for i in range(n):
                    j = (i + 1) % n
                    edges += (loop[2 * i], loop[2 * i + 1], loop[2 * j], loop[2 * j + 1])
            idx = len(self.prisms)
            self.prisms.append((min(xs), min(ys), max(xs), max(ys), p["base"], p["top"], tuple(edges)))
            for ci in range(math.floor(min(xs) / CELL_M), math.floor(max(xs) / CELL_M) + 1):
                for cj in range(math.floor(min(ys) / CELL_M), math.floor(max(ys) / CELL_M) + 1):
                    self.cells.setdefault((ci, cj), []).append(idx)

        # v7 heal: dense tree crowns (sight only, never shots). Each: (cx, cy, r, crown bottom, crown top).
        self.crowns = []
        self.crown_cells = {}
        trees = w.get("trees") or {}
        f = trees.get("fields") or []
        if all(k in f for k in ("z", "height", "crownR", "crownBase", "crownX", "crownY")):
            data, n = trees.get("data") or [], len(f)
            col = {k: f.index(k) for k in f}
            for i in range(0, len(data) - n + 1, n):
                r = data[i + col["crownR"]]
                if r < CANOPY_MIN_R:
                    continue
                tz, cx, cy = data[i + col["z"]], data[i + col["crownX"]], data[i + col["crownY"]]
                idx = len(self.crowns)
                self.crowns.append((cx, cy, r, tz + data[i + col["crownBase"]], tz + data[i + col["height"]]))
                for ci in range(math.floor((cx - r) / CELL_M), math.floor((cx + r) / CELL_M) + 1):
                    for cj in range(math.floor((cy - r) / CELL_M), math.floor((cy + r) / CELL_M) + 1):
                        self.crown_cells.setdefault((ci, cj), []).append(idx)

        t = w["terrain"]
        self.t_minx, self.t_miny, self.t_step = t["minX"], t["minY"], t["step"]
        self.nx, self.ny, self.t_scale = t["nx"], t["ny"], t.get("scale", 0.01)
        self.t_maxx = self.t_minx + (self.nx - 1) * self.t_step
        self.t_maxy = self.t_miny + (self.ny - 1) * self.t_step
        h = array("h")
        with open(os.path.join(map_dir, "v2", t.get("file", "terrain.i16")), "rb") as f:
            h.frombytes(f.read())
        if sys.byteorder == "big":
            h.byteswap()
        if len(h) != self.nx * self.ny:
            raise ValueError(f"terrain has {len(h)} samples, expected {self.nx * self.ny}")
        self.h = h

    # ------------------------------------------------------------------ terrain
    def ground(self, x, y):
        """Terrain height at (x, y), bilinear; clamped to the grid."""
        fx = min(max((x - self.t_minx) / self.t_step, 0.0), self.nx - 1.001)
        fy = min(max((y - self.t_miny) / self.t_step, 0.0), self.ny - 1.001)
        i, j = int(fx), int(fy)
        u, v = fx - i, fy - j
        h, k = self.h, j * self.nx + i
        a = h[k] + (h[k + 1] - h[k]) * u
        b = h[k + self.nx] + (h[k + self.nx + 1] - h[k + self.nx]) * u
        return (a + (b - a) * v) * self.t_scale

    def _terrain_blocks(self, ax, ay, az, dx, dy, dz, length, step=2.0, tol=0.3):
        n = int(length / step)
        for k in range(1, n):
            s = k / n
            if az + dz * s < self.ground(ax + dx * s, ay + dy * s) - tol:
                return True
        return False

    # ------------------------------------------------------------------ prisms
    @staticmethod
    def _inside(x, y, e):
        inside = False
        for k in range(0, len(e), 4):
            x0, y0, x1, y1 = e[k], e[k + 1], e[k + 2], e[k + 3]
            if (y0 > y) != (y1 > y) and x0 + (y - y0) * (x1 - x0) / (y1 - y0) > x:
                inside = not inside
        return inside

    @staticmethod
    def _prism_blocks(ax, ay, az, dx, dy, dz, base, top, e):
        inside = False  # is point a inside the footprint? (ray toward +x)
        cuts = []
        for k in range(0, len(e), 4):
            x0, y0, x1, y1 = e[k], e[k + 1], e[k + 2], e[k + 3]
            if (y0 > ay) != (y1 > ay) and x0 + (ay - y0) * (x1 - x0) / (y1 - y0) > ax:
                inside = not inside
            ex, ey = x1 - x0, y1 - y0
            den = dx * ey - dy * ex
            if den == 0.0:
                continue
            wx, wy = x0 - ax, y0 - ay
            s = (wx * ey - wy * ex) / den
            u = (wx * dy - wy * dx) / den
            if 0.0 <= s <= 1.0 and 0.0 <= u < 1.0:
                cuts.append(s)
        cuts.sort()
        cuts.append(1.0)
        s0 = 0.0
        for s1 in cuts:
            if inside and s1 > s0:
                z0, z1 = az + dz * s0, az + dz * s1
                if max(z0, z1) >= base and min(z0, z1) <= top:
                    return True
            inside = not inside
            s0 = s1
        return False

    def _near(self, ax, ay, bx, by):
        """Indices of the prisms in the grid cells the 2D segment a -> b passes through."""
        n = int(max(abs(bx - ax), abs(by - ay)) / (0.5 * CELL_M)) + 1
        out, seen, cells = [], set(), self.cells
        pi = pj = None
        keys = []
        for k in range(n + 1):
            s = k / n
            i = math.floor((ax + (bx - ax) * s) / CELL_M)
            j = math.floor((ay + (by - ay) * s) / CELL_M)
            if i == pi and j == pj:
                continue
            if pi is not None and i != pi and j != pj:  # a diagonal step may cut through either side cell
                keys += ((i, pj), (pi, j))
            keys.append((i, j))
            pi, pj = i, j
        for key in keys:
            for idx in cells.get(key, ()):
                if idx not in seen:
                    seen.add(idx)
                    out.append(idx)
        return out

    def _clip(self, ax, ay, dx, dy):
        """[s0, s1] of the segment a + s*d (s in 0..1) inside the terrain grid, or None."""
        s0, s1 = 0.0, 1.0
        for p, q in ((-dx, ax - self.t_minx), (dx, self.t_maxx - ax), (-dy, ay - self.t_miny), (dy, self.t_maxy - ay)):
            if p == 0.0:
                if q < 0.0:
                    return None
            else:
                r = q / p
                if p < 0.0:
                    s0 = max(s0, r)
                else:
                    s1 = min(s1, r)
                if s0 > s1:
                    return None
        return s0, s1

    def blocked(self, a, b):
        """True if the segment a -> b (3D points) passes through a prism or under the terrain."""
        ax, ay, az = a
        dx, dy, dz = b[0] - ax, b[1] - ay, b[2] - az
        clip = self._clip(ax, ay, dx, dy)
        if clip is None:
            return False
        s0, s1 = clip
        ax, ay, az, dx, dy, dz = ax + dx * s0, ay + dy * s0, az + dz * s0, dx * (s1 - s0), dy * (s1 - s0), dz * (s1 - s0)
        bx, by, bz = ax + dx, ay + dy, az + dz
        lox, hix, loy, hiy = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
        loz, hiz = min(az, bz), max(az, bz)
        prisms = self.prisms
        for idx in self._near(ax, ay, bx, by):
            minx, miny, maxx, maxy, base, top, edges = prisms[idx]
            if maxx < lox or minx > hix or maxy < loy or miny > hiy or top < loz or base > hiz:
                continue
            if self._prism_blocks(ax, ay, az, dx, dy, dz, base, top, edges):
                return True
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        return self._terrain_blocks(ax, ay, az, dx, dy, dz, length)

    def under_canopy(self, x, y, z):
        """True if (x, y, z) is inside a dense tree crown (1 m in from its edge)."""
        for idx in self.crown_cells.get((math.floor(x / CELL_M), math.floor(y / CELL_M)), ()):
            cx, cy, r, lo, hi = self.crowns[idx]
            if lo <= z <= hi and math.hypot(x - cx, y - cy) <= r - 1.0:
                return True
        return False

    def tops_at(self, x, y):
        """[(base, top)] of every prism whose footprint holds (x, y) (v9 STUCK_ESCAPE: what to jet over)."""
        out = []
        for idx in self.cells.get((math.floor(x / CELL_M), math.floor(y / CELL_M)), ()):
            minx, miny, maxx, maxy, base, top, edges = self.prisms[idx]
            if minx <= x <= maxx and miny <= y <= maxy and self._inside(x, y, edges):
                out.append((base, top))
        return out

    def solid_at(self, x, y, z):
        """True if (x, y, z) is inside a prism."""
        for idx in self.cells.get((math.floor(x / CELL_M), math.floor(y / CELL_M)), ()):
            minx, miny, maxx, maxy, base, top, edges = self.prisms[idx]
            if minx <= x <= maxx and miny <= y <= maxy and base <= z <= top and self._inside(x, y, edges):
                return True
        return False
