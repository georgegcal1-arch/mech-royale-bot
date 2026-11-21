"""Static map geometry for line-of-shot checks (protocol.md section 11).

Shots are stopped by prisms (buildings, stadium, bridges) and terrain; trees never stop them, so they
are ignored here. `World.blocked(a, b)` says whether the straight segment a -> b hits anything solid.
"""
import json
import math
import os
import sys
from array import array


class World:
    def __init__(self, map_dir):
        with open(os.path.join(map_dir, "v2", "world.json")) as f:
            w = json.load(f)

        # Each prism: (minx, miny, maxx, maxy, base, top, edges) with edges a flat tuple x0,y0,x1,y1,...
        # over the ring and all holes. Even-odd crossing over all of them = inside ring, outside holes.
        self.prisms = []
        for p in w["prisms"]:
            loops = [p["ring"]] + list(p.get("holes") or [])
            edges = []
            xs, ys = p["ring"][0::2], p["ring"][1::2]
            for loop in loops:
                n = len(loop) // 2
                for i in range(n):
                    j = (i + 1) % n
                    edges += (loop[2 * i], loop[2 * i + 1], loop[2 * j], loop[2 * j + 1])
            self.prisms.append((min(xs), min(ys), max(xs), max(ys), p["base"], p["top"], tuple(edges)))

        t = w["terrain"]
        self.t_minx, self.t_miny, self.t_step = t["minX"], t["minY"], t["step"]
        self.nx, self.ny, self.t_scale = t["nx"], t["ny"], t.get("scale", 0.01)
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

    def blocked(self, a, b):
        """True if the segment a -> b (3D points) passes through a prism or under the terrain."""
        ax, ay, az = a
        dx, dy, dz = b[0] - ax, b[1] - ay, b[2] - az
        lox, hix = min(ax, b[0]), max(ax, b[0])
        loy, hiy = min(ay, b[1]), max(ay, b[1])
        loz, hiz = min(az, b[2]), max(az, b[2])
        for minx, miny, maxx, maxy, base, top, edges in self.prisms:
            if maxx < lox or minx > hix or maxy < loy or miny > hiy or top < loz or base > hiz:
                continue
            if self._prism_blocks(ax, ay, az, dx, dy, dz, base, top, edges):
                return True
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        return self._terrain_blocks(ax, ay, az, dx, dy, dz, length)
