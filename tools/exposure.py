"""Build the exposure map for LASER_COUNTER (laser_counter.py): which ground spots a shooter can't see. Run it on
the kit's map after tools/navgrid.py; commit the output (the map never changes, protocol.md section 11):

    python tools/exposure.py                    # -> nav/exposure.nav.gz

For every CELL_M cell whose centre is walkable on the nav grid: for each of DIRS bearings (every 22.5 degrees)
and each of RANGES_M, is a mech standing in the cell hidden from a shooter standing that far away in that
direction? A shooter's muzzle is MUZZLE_M above the ground under it; we are hidden when buildings or terrain
block the line to our centre (4.5 m) and the one to our top (TOP_M) (trees don't stop lasers, protocol.md
section 7, so canopy is ignored). One bit per (direction, range), so DIRS * len(RANGES_M) = 64 bits per cell.
Cells that aren't walkable are all-zero (never offered as a hiding spot).
Output: gzip of one JSON header line (version, sha256 of the map, origin, cell, nx, ny, dirs, ranges), then
nx * ny little-endian uint64s, row-major (row j = y); bit (k * len(RANGES_M) + r) = hidden from direction k at
range r.
"""
import argparse
import gzip
import json
import math
import os
import struct
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from world import World  # noqa: E402
from nav import NavGrid  # noqa: E402
import navgrid  # noqa: E402

VERSION = 1
CELL_M = 4.0
DIRS = 16
RANGES_M = (50.0, 100.0, 150.0, 250.0)
MUZZLE_M = 7.0
CENTER_M = 4.5
TOP_M = 7.6


def build(arena):
    t0 = time.time()
    w = World(arena)
    grid = NavGrid.load(arena)
    assert grid is not None, "build nav/ground.nav.gz first (tools/navgrid.py)"
    ox, oy = w.t_minx, w.t_miny
    nx = int((w.t_maxx - ox) // CELL_M) + 1
    ny = int((w.t_maxy - oy) // CELL_M) + 1
    dirs = [(math.cos(2 * math.pi * k / DIRS), math.sin(2 * math.pi * k / DIRS)) for k in range(DIRS)]
    bits = [0] * (nx * ny)
    cells = hidden = 0
    for j in range(ny):
        y = oy + j * CELL_M
        for i in range(nx):
            x = ox + i * CELL_M
            if not grid.ok(grid.cell_of(x, y)):
                continue
            cells += 1
            g = w.ground(x, y)
            center, top = (x, y, g + CENTER_M), (x, y, g + TOP_M)
            v = 0
            for k, (cx, cy) in enumerate(dirs):
                for r, rng in enumerate(RANGES_M):
                    sx, sy = x + rng * cx, y + rng * cy
                    eye = (sx, sy, w.ground(sx, sy) + MUZZLE_M)
                    if w.blocked(eye, center) and w.blocked(eye, top):
                        v |= 1 << (k * len(RANGES_M) + r)
            bits[j * nx + i] = v
            hidden += bin(v).count("1")
    header = {"version": VERSION, "sha256": navgrid.map_sha(arena), "origin": [ox, oy], "cell": CELL_M, "nx": nx,
              "ny": ny, "dirs": DIRS, "ranges": list(RANGES_M), "muzzle_m": MUZZLE_M, "built_s": round(time.time() - t0, 1)}
    stats = {"cells": cells, "hidden_pct": round(100 * hidden / max(cells * DIRS * len(RANGES_M), 1), 1)}
    return header, bits, stats


def save(path, header, bits):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with gzip.open(path, "wb", compresslevel=9) as f:
        f.write((json.dumps(header, separators=(",", ":")) + "\n").encode())
        f.write(struct.pack(f"<{len(bits)}Q", *bits))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arena", default=None, help="the arena/ folder (default: the local kit's)")
    ap.add_argument("--out", default=os.path.join(REPO, "nav", "exposure.nav.gz"))
    a = ap.parse_args(argv)
    arena = a.arena
    if arena is None:
        import arena as arena_mod
        arena = arena_mod.kit_arena()
    header, bits, stats = build(arena)
    save(a.out, header, bits)
    print(f"{a.out}: {header['nx']} x {header['ny']} cells of {CELL_M:.0f} m, {stats['cells']} walkable, "
          f"hidden {stats['hidden_pct']}% of (cell, direction, range); {os.path.getsize(a.out) / 1024:.0f} KB; "
          f"built in {header['built_s']} s")


if __name__ == "__main__":
    main()
