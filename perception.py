"""What we know about the world: damage taken, each mech's motion (v5: circular), sizes, predictions,
the muzzle, and line-of-shot checks."""
import math

import features

LATENCY_S = 0.05        # the shot leaves somewhere in the 0.1 s after this observation: aim for the middle
ATTACKER_WINDOW_S = 4.0  # damage older than this is forgotten
OMEGA_MAX = 2.5         # rad/s cap on an estimated turn rate
OMEGA_ALPHA = 0.5       # smoothing of the turn-rate estimate
MOTION_DT_MAX = 0.25    # observations further apart than this don't give a turn rate
MOTION_MIN_SPEED = 2.0  # slower than this, the heading is noise


def _finite(*vals):
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def _hp(m):
    hp = m.get("hp")
    return hp if isinstance(hp, (int, float)) else (m.get("max_hp") or 1400.0)


def _hp_frac(m):
    return _hp(m) / (m.get("max_hp") or 1400.0)


class PerceptionMixin:
    """Uses self.hits, self.motion, self.gravity, self.world, self.npc_radius/height, self.size_per_cube."""

    def note_damage(self, obs):
        t = obs["t"]
        for e in obs.get("events") or []:
            if e.get("type") == "damaged" and e.get("by") is not None and e.get("bearing") is not None:
                self.hits.append((t, e["by"], e.get("amount", 0), e["bearing"]))
        self.hits = [h for h in self.hits if t - h[0] <= ATTACKER_WINDOW_S]

    def _origin(self, me):
        """Our gun muzzle, moved on by one decision's latency."""
        x, y, z = me["pos"]
        mv = me.get("vel") or [0, 0, 0]
        mh = (me.get("stats") or {}).get("muzzle_height", 7.0)
        return x + mv[0] * LATENCY_S, y + mv[1] * LATENCY_S, z + mv[2] * LATENCY_S + mh

    # ------------------------------------------------------------------ motion model (v5: circular)
    def note_motion(self, obs):
        """Update each visible mech's turn-rate estimate from its velocity heading."""
        t = obs["t"]
        motion = {}
        for m in obs["mechs"]:
            if m["kind"] != "mech":
                continue
            vx, vy = m["vel"][0], m["vel"][1]
            if math.hypot(vx, vy) < MOTION_MIN_SPEED:
                motion[m["id"]] = (t, None, 0.0)
                continue
            h = math.atan2(vy, vx)
            prev = self.motion.get(m["id"])
            w = 0.0
            if prev is not None and prev[1] is not None:
                dt = t - prev[0]
                if 0 < dt <= MOTION_DT_MAX:
                    w = prev[2] + OMEGA_ALPHA * (_wrap(h - prev[1]) / dt - prev[2])
                    w = max(-OMEGA_MAX, min(OMEGA_MAX, w))
                elif dt == 0:
                    w = prev[2]  # same observation again
            motion[m["id"]] = (t, h, w)
        self.motion = motion

    # ------------------------------------------------------------------ geometry
    def _size(self, m):
        """(radius, height) of a mech or NPC."""
        if m.get("kind") == "npc":
            return self.npc_radius, self.npc_height
        s = 1 + self.size_per_cube * (m.get("cubes") or 0)
        return 2.5 * s, 9.0 * s

    def predict(self, m, dt, z_off):
        """Where m's aim point (z_off above its feet) will be dt seconds from now: along an arc at its
        estimated turn rate (a straight line if it isn't turning, or with features.CIRCULAR_AIM off)."""
        (px, py, pz), (vx, vy, vz) = m["pos"], m["vel"]
        w = self.motion.get(m.get("id"), (0, None, 0.0))[2] if features.CIRCULAR_AIM and m.get("kind") == "mech" else 0.0
        if abs(w) > 1e-3:
            s, c = math.sin(w * dt), math.cos(w * dt)
            qx, qy = px + (vx * s - vy * (1 - c)) / w, py + (vy * s + vx * (1 - c)) / w
        else:
            qx, qy = px + vx * dt, py + vy * dt
        qz = pz + vz * dt
        if abs(vz) > 1.0:  # airborne (or on a steep slope: the ground clamp keeps it honest)
            qz -= 0.5 * self.gravity * dt * dt
            if self.world is not None:
                qz = max(qz, self.world.ground(qx, qy))
        return qx, qy, qz + z_off

    def clear(self, a, b):
        return self.world is None or not self.world.blocked(a, b)

    @staticmethod
    def angles(origin, q):
        dx, dy, dz = q[0] - origin[0], q[1] - origin[1], q[2] - origin[2]
        return math.atan2(dy, dx), math.atan2(dz, math.hypot(dx, dy))
