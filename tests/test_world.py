"""world.py: the grid-accelerated, clipped line test must agree with a brute-force reference."""
import math
import random
import time

from conftest import ARENA
from world import World

W = World(ARENA)


def brute_blocked(w, a, b):
    """Every prism, no grid, no clipping (v3's algorithm)."""
    ax, ay, az = a
    dx, dy, dz = b[0] - ax, b[1] - ay, b[2] - az
    for minx, miny, maxx, maxy, base, top, edges in w.prisms:
        if w._prism_blocks(ax, ay, az, dx, dy, dz, base, top, edges):
            return True
    return w._terrain_blocks(ax, ay, az, dx, dy, dz, math.sqrt(dx * dx + dy * dy + dz * dz))


def test_grid_matches_brute_force():
    rnd = random.Random(3)
    agree = blocked = 0
    for _ in range(3000):
        x, y = rnd.uniform(W.t_minx + 5, W.t_maxx - 5), rnd.uniform(W.t_miny + 5, W.t_maxy - 5)
        a = (x, y, W.ground(x, y) + rnd.uniform(1, 40))
        ang, d = rnd.uniform(-math.pi, math.pi), rnd.uniform(1, 250)
        bx = min(max(x + d * math.cos(ang), W.t_minx), W.t_maxx)
        by = min(max(y + d * math.sin(ang), W.t_miny), W.t_maxy)
        b = (bx, by, W.ground(bx, by) + rnd.uniform(1, 40))
        want = brute_blocked(W, a, b)
        assert W.blocked(a, b) == want, (a, b)
        blocked += want
        agree += 1
    assert 300 < blocked < 2700, "the sample should mix blocked and clear lines"


def test_solid_at():
    rnd = random.Random(5)
    inside = 0
    for minx, miny, maxx, maxy, base, top, edges in W.prisms:
        for _ in range(5):
            x, y = rnd.uniform(minx, maxx), rnd.uniform(miny, maxy)
            want = W._inside(x, y, edges)
            zm = (base + top) / 2
            if want:
                inside += 1
                assert W.solid_at(x, y, zm)
                assert not W.solid_at(x, y, top + 1) or any(  # another prism may be stacked on top
                    p[4] <= top + 1 <= p[5] and W._inside(x, y, p[6]) for p in W.prisms)
    assert inside > 200


def test_lines_off_the_map_are_clear_and_cheap():
    t0 = time.perf_counter()
    assert not W.blocked((0, 0, 400), (100_000, 0, 400))
    assert not W.blocked((5000, 5000, 0), (6000, 6000, 0))
    assert (time.perf_counter() - t0) * 1000 < 10
