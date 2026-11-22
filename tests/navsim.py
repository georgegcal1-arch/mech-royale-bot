"""A small kinematic stand-in for the server's movement (protocol.md section 7 "Movement"), for navigation tests.

Not the server's physics: a vertical cylinder (radius, height from hello.config.mech) on the real map
(world.py). Velocity approaches the commanded one at ground_accel / air_accel; walls stop the part of a move that
goes into them (we slide along axis by axis); ledges up to step_up are walked up; jump if grounded; boost on
the ground runs at boost_speed (boost_drain/s), in the air jets up at jet_accel against gravity, rising at most
max_rise_speed (jet_drain/s); energy regenerates at energy_regen/s after energy_regen_delay s without spending.
Two 0.05 s ticks per decision, as in a match.
"""
import math

TICK_S = 0.05


class NavSim:
    def __init__(self, world, mech_cfg, pos, energy=100.0):
        self.w, self.c = world, mech_cfg
        self.x, self.y, self.z = pos
        self.vx = self.vy = self.vz = 0.0
        self.energy = energy
        self.idle_s = 10.0  # since energy was last spent
        self.grounded = True
        self.z = max(self.z, self.support(self.x, self.y, self.z))

    # ---------------------------------------------------------------- geometry
    def support(self, x, y, z):
        """The highest surface under (x, y) that a mech with feet at z stands on (terrain or a prism top)."""
        s = self.w.ground(x, y)
        for base, top in self.w.tops_at(x, y):
            if top <= z + self.c["step_up"] and top > s:
                s = top
        return s

    def blocked(self, x, y, z):
        r, h, step = self.c["radius"], self.c["height"], self.c["step_up"]
        for k in range(8):
            a = k * math.pi / 4
            px, py = x + r * math.cos(a), y + r * math.sin(a)
            for hz in (z + step + 0.1, z + 0.5 * h, z + h - 0.1):
                if self.w.solid_at(px, py, hz):
                    return True
            if self.w.ground(px, py) > z + step:
                return True
        return False

    # ---------------------------------------------------------------- one decision
    def step(self, action, n_ticks=2):
        c = self.c
        mv = action.get("move") or [0.0, 0.0]
        n = math.hypot(mv[0], mv[1])
        if n > 1.0:
            mv = [mv[0] / n, mv[1] / n]
        boost, jump = bool(action.get("boost")), bool(action.get("jump"))
        for k in range(n_ticks):
            spent = False
            if jump and k == 0 and self.grounded:
                self.vz = c["jump_speed"]
                self.grounded = False
            speed = c["boost_speed"] if (boost and self.grounded and self.energy > 0) else c["walk_speed"]
            tx, ty = mv[0] * speed, mv[1] * speed
            acc = (c["ground_accel"] if self.grounded else c["air_accel"]) * TICK_S
            dx, dy = tx - self.vx, ty - self.vy
            d = math.hypot(dx, dy)
            if d > acc:
                dx, dy = dx / d * acc, dy / d * acc
            self.vx += dx
            self.vy += dy
            if boost and self.energy > 0:
                if self.grounded:
                    if n > 0.1:
                        self.energy -= c["boost_drain"] * TICK_S
                        spent = True
                else:
                    self.vz = min(self.vz + c["jet_accel"] * TICK_S, c["max_rise_speed"])
                    self.energy -= c["jet_drain"] * TICK_S
                    spent = True
            self.vz -= c["gravity"] * TICK_S
            # horizontal, sliding along walls axis by axis
            nx, ny = self.x + self.vx * TICK_S, self.y + self.vy * TICK_S
            if not self.blocked(nx, ny, self.z):
                self.x, self.y = nx, ny
            elif not self.blocked(nx, self.y, self.z):
                self.x, self.vy = nx, 0.0
            elif not self.blocked(self.x, ny, self.z):
                self.y, self.vx = ny, 0.0
            else:
                self.vx = self.vy = 0.0
            # vertical
            s = self.support(self.x, self.y, self.z)
            nz = self.z + self.vz * TICK_S
            if nz <= s:
                self.z, self.vz, self.grounded = s, 0.0, True
            else:
                self.z, self.grounded = nz, False
            self.energy = max(self.energy, 0.0)
            self.idle_s = 0.0 if spent else self.idle_s + TICK_S
            if self.idle_s >= c["energy_regen_delay"]:
                self.energy = min(100.0, self.energy + c["energy_regen"] * TICK_S)

    def into(self, me):
        """Write the state into an observation's `self`."""
        me["pos"] = [self.x, self.y, self.z]
        me["vel"] = [self.vx, self.vy, self.vz]
        me["energy"] = self.energy
        me["grounded"] = self.grounded
