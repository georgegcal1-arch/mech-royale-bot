"""v9 experiment, features.LASER_COUNTER: beat laser duels by breaking line of sight on the laser's own clock.

The rules (protocol.md section 7): a laser charges up to 0.75 s, visibly (`charging`, its aim, a laser_charge
sound), tracks at full turn rate while charging, then fires an instant beam along its aim; cooldown 1.65 s; a
full charge held 2 s more fires on its own. Buildings, bridges and terrain stop the beam (trees don't); only
lightning interrupts it (short, 2 s). Strafing can't beat a 4 rad/s torso. In our three lost laser duels
(arena MUWWU, VNETQ, ZEFXJ) every laser hit came 0.6-0.8 s after we first saw the charge. So:

  Foe: the nearest visible enemy mech holding a laser within LASER_RANGE_M with a clear line to our centre or top.
  Its clock: when we see its beam (`beams`, by it) or its laser hits us, it can't fire for WINDOW_S (cooldown +
    minimum charge, less a reaction margin): the window. Charging = a beam within 0.15-2.75 s.
  Anchor: the best cell of the precomputed exposure map (tools/exposure.py -> nav/exposure.nav.gz) within
    ANCHOR_SEARCH_M that is hidden from the foe (both neighbouring direction and range bins, so the lookup is
    conservative), walkable, and confirmed with live line checks from the foe's real muzzle; re-chosen every
    ANCHOR_REPLAN_S or when the foe moves. Scored by travel and closeness to the foe: the next cover on the way in.
  Priority is CLOSING (bracket B2, close aggression, won 4.50): cover is only for the moments its laser is ready.
  Legs (in zone hurry nothing changes):
    foe within DONE_M (LIGHTNING_M with lightning) -> the normal close fight (B2's; lightning shorts it)
    in the window         -> close: boost straight toward the foe (with or without lightning)
    foe charging          -> hide: boost to an anchor within ANCHOR_SEARCH_M (reachable in the ~0.7 s warning);
                             none in reach: keep closing (B2)
    ready, not charging   -> nothing: B2's direct approach (replays: detours to cover on the way in ended 86 m
                             from the shooter at its beam, against 77 m without them)
  The foe stays minded for STICKY_S after it loses its line to us (we are behind the cover), and the anchor is
  kept while live checks still find it hidden (re-chosen when it isn't, or the foe moved ANCHOR_FOE_MOVE_M).
  Replay history (tests/test_laser_counter.py): hiding first (45 m search, hold cover while it is ready) hid 3/9
  of the beams vs 1/9 but stood back; that is the opposite of the winner, so it was cut back to this.
  The torso is untouched: it fires whenever it has a clear line (so it shoots while closing, not from cover).
"""
import gzip
import hashlib
import json
import math
import os
import struct

from perception import LATENCY_S

EXPOSURE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nav", "exposure.nav.gz")
LASER_RANGE_M = 280.0
LIGHTNING_M = 43.0
DONE_M = 40.0            # without lightning: B2's close range, from here the normal close fight
FOES_MAX = 2             # line-check at most this many nearest lasers per decision (time bound)
COOLDOWN_S, MIN_CHARGE_S, REACT_S = 1.65, 0.15, 0.3
WINDOW_S = COOLDOWN_S + MIN_CHARGE_S - REACT_S   # 1.5 s after a beam: it can't fire
ANCHOR_SEARCH_M = 12.0   # cover reachable inside the charge's ~0.7 s warning (boost from a standstill ~10 m)
ANCHOR_M = 8.0           # stay this close to the anchor while the foe could charge (0.7 s at boost ~ 10 m)
ANCHOR_REPLAN_S = 0.5
ANCHOR_FOE_MOVE_M = 10.0
CLOSE_W = 0.5            # metres of travel one metre closer to the foe is worth (cover on the way in)
MAX_CONFIRM = 4          # live line checks (2 lines each) per anchor plan (8 pushed the all-on worst case past 10 ms)
LC_STOP_M = 1.5          # this close to the anchor: stand
LC_SLOW_M = 4.0          # ...this close: no boost (no overshoot past the cover)
STICKY_S = 3.0           # keep minding a laser this long after it lost its line to us (we are in cover)
ANCHOR_KEEP_S = 3.0      # keep a still-hidden anchor this long past its re-plan time (no flip-flopping)
MUZZLE_M, CENTER_M, TOP_M = 7.0, 4.5, 7.6


class ExposureMap:
    def __init__(self, header, data):
        self.ox, self.oy = header["origin"]
        self.cell = float(header["cell"])
        self.nx, self.ny = header["nx"], header["ny"]
        self.dirs = header["dirs"]
        self.ranges = header["ranges"]
        self.bits = struct.unpack(f"<{self.nx * self.ny}Q", data)

    @classmethod
    def load(cls, map_dir, path=EXPOSURE_FILE):
        """The map for this arena, or None (no file, or built for another map)."""
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
        if h.hexdigest() != header.get("sha256") or len(data) - nl - 1 != 8 * header["nx"] * header["ny"]:
            return None
        return cls(header, data[nl + 1:])

    def cell_of(self, x, y):
        return int(round((x - self.ox) / self.cell)), int(round((y - self.oy) / self.cell))

    def center(self, c):
        return self.ox + c[0] * self.cell, self.oy + c[1] * self.cell

    def value(self, c):
        i, j = c
        return self.bits[j * self.nx + i] if 0 <= i < self.nx and 0 <= j < self.ny else 0

    def hidden(self, c, sx, sy):
        """Is cell c hidden from a shooter at (sx, sy)? Conservative: the two nearest direction bins and the two
        range bins around its distance must all say hidden."""
        v = self.value(c)
        if not v:
            return False
        x, y = self.center(c)
        d = math.hypot(sx - x, sy - y)
        a = (math.atan2(sy - y, sx - x) / (2 * math.pi) * self.dirs) % self.dirs
        k0 = int(a) % self.dirs
        k1 = (k0 + 1) % self.dirs
        rs = self.ranges
        r1 = next((r for r, v_ in enumerate(rs) if v_ >= d), len(rs) - 1)
        r0 = max(r1 - 1, 0) if d > rs[0] else r1
        nr = len(rs)
        for k in (k0, k1):
            for r in (r0, r1):
                if not (v >> (k * nr + r)) & 1:
                    return False
        return True


class LaserCounterMixin:
    """Uses self.exposure (ExposureMap or None), self.laser_fired {mech id: t}, self.lc (anchor state or None),
    self.navgrid (walkability), self.world and the perception helpers."""

    def note_lasers(self, obs):
        """Remember when each enemy laser last fired: its beam in view, or its laser hitting us."""
        t = obs["t"]
        for b in obs.get("beams") or []:
            if b.get("kind") == "laser" and isinstance(b.get("by"), int):
                self.laser_fired[b["by"]] = t - (b.get("age") or 0.0)
        for e in obs.get("events") or []:
            if e.get("type") == "damaged" and e.get("weapon") == "laser" and isinstance(e.get("by"), int):
                self.laser_fired[e["by"]] = t

    def laser_foe(self, obs, enemies):
        """The nearest visible enemy laser with a clear line to us within LASER_RANGE_M, or None."""
        me = obs["self"]
        x, y, z = me["pos"]
        us, top = (x, y, z + CENTER_M), (x, y, z + TOP_M)
        checked = 0
        for m in enemies:  # nearest first
            if "laser" not in (m.get("weapons") or ()):
                continue
            mx, my, mz = m["pos"]
            if math.hypot(mx - x, my - y) > LASER_RANGE_M or checked >= FOES_MAX:
                break
            checked += 1
            eye = (mx, my, mz + MUZZLE_M)
            if self.clear(eye, us) or self.clear(eye, top):  # it can hit what it can see of us
                return m
        return None

    def _anchor(self, obs, foe, closing):
        """(x, y) of the best hidden cell near us from the foe, or None."""
        ex, w = self.exposure, self.world
        if ex is None or w is None:
            return None
        me = obs["self"]
        x, y, z = me["pos"]
        fx, fy, fz = foe["pos"]
        eye = (fx, fy, fz + MUZZLE_M)
        c0 = ex.cell_of(x, y)
        r = int(math.ceil(ANCHOR_SEARCH_M / ex.cell))
        cands = []
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                c = (c0[0] + di, c0[1] + dj)
                cx, cy = ex.center(c)
                travel = math.hypot(cx - x, cy - y)
                if travel > ANCHOR_SEARCH_M or not ex.hidden(c, fx, fy):
                    continue
                if self.navgrid is not None and not self.navgrid.ok(self.navgrid.cell_of(cx, cy)):
                    continue
                cost = travel + (CLOSE_W * math.hypot(fx - cx, fy - cy) if closing else 0.0)
                cands.append((cost, cx, cy))
        cands.sort()
        for cost, cx, cy in cands[:MAX_CONFIRM]:
            g = w.ground(cx, cy)
            if w.blocked(eye, (cx, cy, g + CENTER_M)) and w.blocked(eye, (cx, cy, g + TOP_M)):
                return cx, cy
        return None

    def _still_hidden(self, foe, p):
        fx, fy, fz = foe["pos"]
        eye = (fx, fy, fz + MUZZLE_M)
        g = self.world.ground(*p)
        return self.world.blocked(eye, (p[0], p[1], g + CENTER_M)) and self.world.blocked(eye, (p[0], p[1], g + TOP_M))

    def laser_counter(self, obs, enemies, mode):
        """None (no change), or (goal (x, y), boost, mode) for the legs."""
        if mode == "zone!":
            return None
        me, t = obs["self"], obs["t"]
        lc = self.lc
        foe = self.laser_foe(obs, enemies)
        if foe is not None:
            seen_t = t
        elif lc is not None and t - lc["seen"] <= STICKY_S:
            # out of its line (behind our cover, most likely): keep minding it while it is still tracked
            foe = next((m for m in enemies if m["id"] == lc["foe"]), None)
            seen_t = lc["seen"]
        if foe is None:
            self.lc = None
            return None
        x, y, _ = me["pos"]
        fx, fy, _ = foe["pos"]
        d = math.hypot(fx - x, fy - y)
        held = {w_["kind"] for w_ in me["weapons"] if w_}
        lightning = "lightning" in held
        if d <= (LIGHTNING_M if lightning else DONE_M):
            return None  # close enough: the normal close fight (B2's; lightning shorts it)
        in_window = t - self.laser_fired.get(foe["id"], -1e9) < WINDOW_S and not foe.get("charging")
        if in_window:
            return (fx, fy), True, "lc/close"  # it can't fire: close the gap now
        if not foe.get("charging"):
            return None  # ready: B2's direct approach (detours to cover cost more distance than they save)
        keep = (lc is not None and lc["foe"] == foe["id"] and lc["anchor"] is not None
                and t < lc["until"] + ANCHOR_KEEP_S and self._still_hidden(foe, lc["anchor"]))
        if not keep and (lc is None or lc["foe"] != foe["id"] or t >= lc["until"] or lc["anchor"] is None
                         or math.hypot(lc["fpos"][0] - fx, lc["fpos"][1] - fy) > ANCHOR_FOE_MOVE_M
                         or not self._still_hidden(foe, lc["anchor"])):
            anchor = self._anchor(obs, foe, True)  # the next cover on the way in
            lc = self.lc = {"foe": foe["id"], "until": t + ANCHOR_REPLAN_S, "anchor": anchor, "closing": True,
                            "fpos": (fx, fy), "seen": seen_t}
        lc["seen"] = seen_t
        anchor = lc["anchor"]
        if anchor is None:
            return None  # no cover within reach of the charge: keep closing (B2)
        return anchor, True, "lc/hide"  # charging: duck behind the cover within reach; its beam opens the window
