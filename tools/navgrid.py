"""Build the ground-level walkability grid for navigation (nav.py, features.NAV_ROUTE). Run it on the kit's map;
commit the output (the image can't read /arena at build time, and the kit is gitignored):

    python tools/navgrid.py                              # -> nav/ground.nav.gz
    python tools/navgrid.py --arena path/to/arena --out nav/ground.nav.gz

The map is the same every match (protocol.md section 11), so this is done once. Ground layer only (no roofs or
jet edges yet). Steps:
  1. Sample the terrain's 1 m vertex grid: blocked where a prism fills knee, chest or head height above the
     ground (prisms lower than step_up are walked over; bridges higher than a mech are walked under), or
     outside the map bounds (the edge is a wall).
  2. Widen every blocked sample by the mech radius (CLEARANCE_M): a free sample has room for our body.
  3. 2 m cells on every other vertex. Each cell stores an 8-bit mask of the directions (DIRS) it can be left
     in: the neighbour and the 1 m sample between them are free, a diagonal also needs both side cells, and
     no 1 m sub-step climbs more than the 50 degree slope rule allows (downhill is always fine: you drop).
Output: gzip of one JSON header line (version, the map's sha256, origin, cell size, nx, ny, DIRS), then
nx * ny mask bytes, row-major (row j = y). A mask of 0 means "not walkable".
"""
import argparse
import gzip
import hashlib
import json
import math
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from world import World  # noqa: E402

VERSION = 1
CELL = 2                       # metres per cell (= 2 terrain samples)
CLEARANCE_M = 2.6              # mech radius 2.5 + a little; a wall can sit up to ~0.7 m nearer (between 1 m samples):
                               # 3.2 would guarantee full room but closes real gaps (the 97/100 corner, round the stadium)
STEP_UP_M = 1.2                # protocol.md: ledges up to step_up are walked up
MAX_SLOPE = math.tan(math.radians(50.0))
BODY_Z = (STEP_UP_M + 0.1, 4.5, 8.5)  # knee, chest, head above the ground
EDGE_MARGIN_M = 3.0
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


def map_sha(arena):
    h = hashlib.sha256()
    for f in ("world.json", "terrain.i16"):
        with open(os.path.join(arena, "v2", f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def build(arena):
    t0 = time.time()
    w = World(arena)
    with open(os.path.join(arena, "v2", "world.json")) as f:
        bounds = json.load(f).get("bounds") or {}
    nx, ny, minx, miny, step = w.nx, w.ny, w.t_minx, w.t_miny, w.t_step
    assert step == 1.0, "the grid assumes 1 m terrain samples"
    hts = [v * w.t_scale for v in w.h]  # ground height per vertex
    bx0, bx1 = bounds.get("minX", minx) + EDGE_MARGIN_M, bounds.get("maxX", minx + nx) - EDGE_MARGIN_M
    by0, by1 = bounds.get("minY", miny) + EDGE_MARGIN_M, bounds.get("maxY", miny + ny) - EDGE_MARGIN_M

    # 1. raw blocked samples
    raw = bytearray(nx * ny)
    for j in range(ny):
        y = miny + j
        row = j * nx
        for i in range(nx):
            x = minx + i
            if not (bx0 <= x <= bx1 and by0 <= y <= by1):
                raw[row + i] = 1
                continue
            g = hts[row + i]
            if any(w.solid_at(x, y, g + dz) for dz in BODY_Z):
                raw[row + i] = 1
    t1 = time.time()

    # 2. widen by the clearance
    r = int(math.ceil(CLEARANCE_M))
    disc = [(di, dj) for di in range(-r, r + 1) for dj in range(-r, r + 1) if di * di + dj * dj <= CLEARANCE_M ** 2]
    free = bytearray(b"\x01" * (nx * ny))
    for k in range(nx * ny):
        if raw[k]:
            i, j = k % nx, k // nx
            for di, dj in disc:
                ii, jj = i + di, j + dj
                if 0 <= ii < nx and 0 <= jj < ny:
                    free[jj * nx + ii] = 0
    t2 = time.time()

    # 3. 2 m cells and their exit masks
    cnx, cny = (nx - 1) // CELL + 1, (ny - 1) // CELL + 1

    def vfree(i, j):
        return 0 <= i < nx and 0 <= j < ny and free[j * nx + i]

    def vh(i, j):
        return hts[j * nx + i]

    def climb_ok(a, b, horiz):
        return vh(*b) - vh(*a) <= MAX_SLOPE * horiz + 1e-6

    masks = bytearray(cnx * cny)
    for cj in range(cny):
        for ci in range(cnx):
            i, j = ci * CELL, cj * CELL
            if not vfree(i, j):
                continue
            m = 0
            for k, (dx, dy) in enumerate(DIRS):
                ni, nj = i + dx * CELL, j + dy * CELL
                mi, mj = i + dx, j + dy  # the 1 m sample between
                if not (vfree(ni, nj) and vfree(mi, mj)):
                    continue
                if dx and dy and not (vfree(i + dx * CELL, j) and vfree(i, j + dy * CELL)
                                      and vfree(i + dx, j) and vfree(i, j + dy)):
                    continue  # don't cut a corner
                horiz = math.hypot(dx, dy)
                if not (climb_ok((i, j), (mi, mj), horiz) and climb_ok((mi, mj), (ni, nj), horiz)):
                    continue
                m |= 1 << k
            masks[cj * cnx + ci] = m
    t3 = time.time()
    header = {"version": VERSION, "sha256": map_sha(arena), "origin": [minx, miny], "cell": float(CELL),
              "nx": cnx, "ny": cny, "dirs": DIRS, "clearance_m": CLEARANCE_M,
              "built_s": round(t3 - t0, 1)}
    stats = {"raw_s": round(t1 - t0, 1), "widen_s": round(t2 - t1, 1), "cells_s": round(t3 - t2, 1),
             "walkable": sum(1 for v in masks if v), "cells": cnx * cny}
    return header, masks, stats


def save(path, header, masks):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with gzip.open(path, "wb", compresslevel=9) as f:
        f.write((json.dumps(header, separators=(",", ":")) + "\n").encode())
        f.write(bytes(masks))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arena", default=None, help="the arena/ folder (default: the local kit's)")
    ap.add_argument("--out", default=os.path.join(REPO, "nav", "ground.nav.gz"))
    a = ap.parse_args(argv)
    arena = a.arena
    if arena is None:
        import arena as arena_mod  # tools/arena.py
        arena = arena_mod.kit_arena()
    header, masks, stats = build(arena)
    save(a.out, header, masks)
    print(f"{a.out}: {header['nx']} x {header['ny']} cells of {header['cell']:.0f} m, "
          f"{stats['walkable']} walkable ({100 * stats['walkable'] / stats['cells']:.0f}%), "
          f"{os.path.getsize(a.out) / 1024:.0f} KB; built in {header['built_s']} s {stats}")


if __name__ == "__main__":
    main()
