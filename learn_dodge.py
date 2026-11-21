"""v9 experiment LEARN_DODGE: learn how enemy shooters lead us, and dodge accordingly.

Each new enemy gun bullet is traced back to the visible mech whose muzzle lies on its path (the shooter).
Its direction then tells how far the shooter led us: k = 0 means it aimed where we were, k = 1 means it
aimed where our velocity would take us by the time the bullet arrived. House bots share code, so k is
pooled over all shooters; a shooter with enough samples of its own uses those.

Dodging (movement.py, with features.LEARN_DODGE):
  leaders (k >= LEAD_K): strafe legs of LEADER_STRAFE_S, and a bullet on course is dodged by reversing our
    sideways motion (they aimed where we were heading);
  direct aimers (k < LEAD_K): strafe legs of DIRECT_STRAFE_S, and a bullet on course is dodged by keeping
    our sideways motion (they aimed where we were);
  unknown (too few samples): v5's strafe and dodge.
"""
import math

from perception import _wrap

LEAD_K = 0.5
LEADER_STRAFE_S = (0.35, 0.7)  # accel 58.5 m/s^2: a full reversal takes ~0.4 s
DIRECT_STRAFE_S = (2.0, 3.0)
TRACE_MAX_S = 0.6        # a bullet first seen at most this long after it left the muzzle
TRACE_MISS_M = 5.0       # ...whose back-traced path passes this close to a mech's muzzle
MIN_LATERAL_M = 1.5      # our motion must have moved the lead point at least this much sideways
AIMED_AT_US_M = 12.0     # the bullet's line passes this close to our direct or led point
K_CLAMP = (-1.0, 2.0)
K_WINDOW = 20            # running mean over about this many samples
MIN_POOL = 3             # samples before the pooled k is used
MIN_OWN = 4              # samples before a shooter's own k is used
SEEN_KEEP_S = 3.0


def _mean_update(acc, k):
    acc[1] += 1
    acc[0] += (k - acc[0]) / min(acc[1], K_WINDOW)


class LearnDodgeMixin:
    """Uses self.bullets_seen {bullet id: (t, shooter or None)}, self.k_pool [mean, n] and
    self.k_by {shooter: [mean, n]}."""

    def note_bullets(self, obs):
        """Attribute and measure every gun bullet seen for the first time."""
        t = obs["t"]
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        mv = me.get("vel") or [0, 0, 0]
        seen = self.bullets_seen
        for bid in [b for b, (bt, _) in seen.items() if t - bt > SEEN_KEEP_S]:
            del seen[bid]
        shooters = [m for m in obs["mechs"] if m.get("kind") == "mech"]
        for p in obs.get("projectiles") or []:
            if p.get("kind") != "gun" or p.get("id") in seen:
                continue
            seen[p.get("id")] = (t, None)
            if not shooters:
                continue
            (bx, by, bz), (vx, vy, vz) = p["pos"], p["vel"]
            vv = vx * vx + vy * vy + vz * vz
            vh = math.hypot(vx, vy)
            if vv < 1.0 or vh < 1.0:
                continue
            best = None
            for m in shooters:
                mh = 7.0 * (1 + self.size_per_cube * (m.get("cubes") or 0))
                mx, my, mz = m["pos"][0], m["pos"][1], m["pos"][2] + mh
                s = ((bx - mx) * vx + (by - my) * vy + (bz - mz) * vz) / vv
                if not 0 <= s <= TRACE_MAX_S:
                    continue
                miss = math.dist((bx - vx * s, by - vy * s, bz - vz * s), (mx, my, mz))
                if miss <= TRACE_MISS_M and (best is None or miss < best[0]):
                    best = (miss, s, m)
            if best is None:
                continue
            _, s, m = best
            seen[p.get("id")] = (t, m["id"])
            mx, my = m["pos"][0], m["pos"][1]
            dx, dy = x - mv[0] * s - mx, y - mv[1] * s - my  # us when it fired, from the shooter
            dist = math.hypot(dx, dy)
            if dist < 1.0:
                continue
            tf = dist / vh
            lx, ly = dx + mv[0] * tf, dy + mv[1] * tf  # where our velocity was taking us
            a_d, a_l, a_s = math.atan2(dy, dx), math.atan2(ly, lx), math.atan2(vy, vx)
            den = _wrap(a_l - a_d)
            off_d, off_l = abs(_wrap(a_s - a_d)) * dist, abs(_wrap(a_s - a_l)) * dist
            if abs(den) * dist < MIN_LATERAL_M or min(off_d, off_l) > AIMED_AT_US_M:
                continue
            k = max(K_CLAMP[0], min(K_CLAMP[1], _wrap(a_s - a_d) / den))
            _mean_update(self.k_pool, k)
            _mean_update(self.k_by.setdefault(m["id"], [0.0, 0]), k)

    def lead_k(self, shooter=None):
        """The learned lead fraction for this shooter (its own, else pooled), or None if unknown."""
        own = self.k_by.get(shooter)
        if own is not None and own[1] >= MIN_OWN:
            return own[0]
        return self.k_pool[0] if self.k_pool[1] >= MIN_POOL else None

    def strafe_window(self, default):
        """(min, max) seconds per strafe leg for the pooled lead fraction."""
        k = self.lead_k()
        if k is None:
            return default
        return LEADER_STRAFE_S if k >= LEAD_K else DIRECT_STRAFE_S

    def dodge_side(self, p, nx, ny, mv, default):
        """+1/-1 along (nx, ny) (across bullet p's path) to dodge it given its shooter's lead, or default."""
        shooter = self.bullets_seen.get(p.get("id"), (0, None))[1]
        k = self.lead_k(shooter)
        lateral = mv[0] * nx + mv[1] * ny
        if k is None or abs(lateral) < 2.0:
            return default
        keep = 1 if lateral > 0 else -1
        return -keep if k >= LEAD_K else keep
