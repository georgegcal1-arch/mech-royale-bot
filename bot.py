"""Mech Royale bot v3: win 1-on-1 gun fights.

On top of v2 (safety net + zone awareness, both unchanged):
  1. Face the attacker: damage events are remembered for a few seconds. The visible attacker that did
     the most damage becomes the target; if it can't be seen, the torso turns to the hit's bearing
     (hits from outside the front arc do 1.5x).
  2. Lead: the gun aims where the target will be when the bullet (weapon speed, no drop) gets there,
     allowing for one decision of latency and gravity on airborne targets.
  3. Clear shot only: a weapon fires only when the torso's actual aim is on the solution and the line
     (or the mortar arc) misses every building and the terrain (world.py). Reload when the magazine
     is empty and no enemy mech is in view.
  4. Loot: take a laser (preferred) or mortar lying within 4 m into the empty slot. The laser charges
     on a clear target and is released when fully charged and on target; the mortar is lobbed on a
     low arc when its arc is clear.
  5. Upgrades: "armor" when it is offered, otherwise choice 0.
"""
import atexit
import gc
import math
import time

import mechfight as mf

# --- zone tuning (v2) ---
EDGE_MARGIN_M = 25.0   # closer than this to the zone edge (now, or LOOKAHEAD_S from now) = hurry
LOOKAHEAD_S = 6.0      # also judge the edge where it will be this many seconds from now
SPOT_FRAC = 0.5        # target spot: this fraction of next_radius from next_center, on our side
ARRIVE_M = 3.0         # closer than this to the spot = stand still
SLOW_FRAC = 0.9        # slow walk = 90% of sound.step_speed: no footstep sounds
SPARE_S = 10.0         # walk at full speed if slow walking would reach the spot later than shrink_end - SPARE_S

# --- combat tuning ---
LATENCY_S = 0.05        # the shot leaves somewhere in the 0.1 s after this observation: aim for the middle
ATTACKER_WINDOW_S = 4.0  # damage older than this is forgotten
FACE_S = 2.0            # keep facing an unseen attacker's bearing this long after its last hit
AIM_TOL = 0.7           # fire when the aim error at the target is within this fraction of its radius / half height
AIM_HEIGHTS = (0.5, 0.8)  # try the target's center, then near its top (peeking over cover)
TAKE_KINDS = ("laser", "mortar")  # pickups worth taking, preferred first
TAKE_Z_M = 4.0          # vertical pickup reach (fixed by the protocol)
SHOT_OFFSET_M = 3.0     # shots spawn radius + 0.5 out along the aim
MORTAR_MIN_M = 25.0     # never lob closer than this (own splash, knock-back)
MORTAR_RANGE_ERR_M = 3.0  # fire the mortar when the pitch error moves the impact less than this
UPGRADE = "armor"

# --- safety net (v2) ---
SLOW_DECISION_MS = 10.0  # log any decision slower than this


def _finite(*vals):
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def zone_at(z, t):
    """(cx, cy, r) of the zone circle at match time t, moving linearly from the current circle to the
    next one during [shrink_start, shrink_end]. If the server already reports the moving circle, this
    errs inward (we hurry a little earlier), never outward."""
    s0, s1 = z["shrink_start"], z["shrink_end"]
    if t <= s0:
        f = 0.0
    elif t >= s1 or s1 <= s0:
        f = 1.0
    else:
        f = (t - s0) / (s1 - s0)
    (cx, cy), (nx, ny) = z["center"], z["next_center"]
    return cx + (nx - cx) * f, cy + (ny - cy) * f, z["radius"] + (z["next_radius"] - z["radius"]) * f


def pick_upgrade(offer):
    """Index of the upgrade to take: UPGRADE if offered, else 0."""
    for i, c in enumerate(offer.get("choices") or []):
        if c.get("id") == UPGRADE:
            return i
    return 0


def aimed(aim, yaw, pitch, dist, radius, height):
    """Does the actual torso aim put the shot on a target of this size at this distance?"""
    return (abs(_wrap(aim[0] - yaw)) * dist <= AIM_TOL * radius
            and abs(aim[1] - pitch) * dist <= AIM_TOL * height / 2)


class Bot:
    name = "mech-royale-bot v3"

    def __init__(self, hello: mf.Hello):
        cfg = hello["config"]
        self.reach = cfg["loot"]["pickup_radius"]
        self.step_speed = cfg["sound"]["step_speed"]
        self.walk_speed = cfg["mech"].get("walk_speed", 11.7)
        self.gravity = cfg["mech"].get("gravity", 22.0)
        self.size_per_cube = cfg.get("cubes", {}).get("size_per_cube", 0.05)
        self.npc_radius = cfg.get("npc", {}).get("radius", 1.8)
        self.npc_height = cfg.get("npc", {}).get("height", 3.5)
        self.hits = []  # (t, attacker id, amount, bearing) of recent damage
        self.world = None
        try:
            from world import World
            self.world = World(hello["map"]["dir"])
        except Exception as e:  # no map: still fight, just without the cover check
            mf.log(f"map not loaded, shots will not check cover: {e!r}")
        self.times = []  # seconds per decision
        self.fallbacks = 0
        gc.freeze()  # keep start-up objects out of the collector so collections stay short
        atexit.register(self._report)

    # ------------------------------------------------------------------ safety net (v2)
    def act(self, obs: mf.Observation) -> mf.Action:
        t0 = time.perf_counter()
        try:
            act = self.decide(obs)
            self._validate(act)
        except Exception as e:
            self.fallbacks += 1
            if self.fallbacks <= 5 or self.fallbacks % 100 == 0:
                mf.log(f"decision failed at tick {obs.get('tick')} ({self.fallbacks} so far): {e!r}")
            mf.draw.clear()  # drop anything a half-finished decision drew
            act = self.safe(obs)
        dt = time.perf_counter() - t0
        self.times.append(dt)
        if dt * 1000 > SLOW_DECISION_MS:
            mf.log(f"slow decision at tick {obs.get('tick')}: {dt * 1000:.1f} ms")
        return act

    @staticmethod
    def _validate(act):
        """Raise if the action holds anything that would not serialize to valid JSON numbers."""
        for k in ("move", "aim"):
            v = act.get(k)
            if v is not None and (len(v) != 2 or not _finite(*v)):
                raise ValueError(f"bad {k}: {v!r}")

    def safe(self, obs):
        """Move toward the zone's next center, no firing. Never raises."""
        act = {"tick": obs.get("tick"), "fire": [False, False]}
        try:
            x, y = obs["self"]["pos"][:2]
            gx, gy = obs["zone"]["next_center"]
            yaw = math.atan2(gy - y, gx - x)
            if _finite(yaw):
                act.update(move=[math.cos(yaw), math.sin(yaw)], aim=[yaw, 0.0],
                           boost=not obs["self"].get("in_zone", True))
            mf.draw.label("SAFE MODE")
        except Exception:
            pass
        return act

    def _report(self):
        if not self.times:
            return
        ts = sorted(self.times)
        p = lambda q: ts[min(len(ts) - 1, int(q * len(ts)))] * 1000
        mf.log(f"decisions: {len(ts)}  ms p50 {p(0.5):.2f}  p99 {p(0.99):.2f}  max {ts[-1] * 1000:.2f}  fallbacks: {self.fallbacks}")

    # ------------------------------------------------------------------ zone awareness (v2)
    def zone_move(self, obs):
        """(goal or None, speed 0..1, boost, mode) for the legs."""
        me, z, t = obs["self"], obs["zone"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        ncx, ncy = z["next_center"]
        nr = z["next_radius"]

        cx, cy, r = zone_at(z, t)
        fx, fy, fr = zone_at(z, t + LOOKAHEAD_S)
        edge = min(r - math.hypot(x - cx, y - cy), fr - math.hypot(x - fx, y - fy))
        if not me["in_zone"] or edge < EDGE_MARGIN_M:
            return (ncx, ncy), 1.0, not me["in_zone"], "zone!"

        # A spot inside the next circle on the line from its center to us (not the crowded center).
        d = math.hypot(x - ncx, y - ncy)
        keep = SPOT_FRAC * nr
        if d <= keep + ARRIVE_M:
            return None, 0.0, False, "hold"
        goal = (ncx + (x - ncx) / d * keep, ncy + (y - ncy) / d * keep)
        dist = d - keep
        slow_mps = SLOW_FRAC * self.step_speed
        walk_mps = me["stats"].get("walk_speed", self.walk_speed) or self.walk_speed
        time_left = z["shrink_end"] - t - SPARE_S
        if dist > slow_mps * max(time_left, 0.0):
            return goal, 1.0, False, "walk"
        return goal, min(1.0, slow_mps / walk_mps), False, "sneak"

    # ------------------------------------------------------------------ attackers
    def note_damage(self, obs):
        t = obs["t"]
        for e in obs.get("events") or []:
            if e.get("type") == "damaged" and e.get("by") is not None and e.get("bearing") is not None:
                self.hits.append((t, e["by"], e.get("amount", 0), e["bearing"]))
        self.hits = [h for h in self.hits if t - h[0] <= ATTACKER_WINDOW_S]

    def choose_target(self, obs, enemies):
        """(target or None, bearing to face or None, why)."""
        seen = {m["id"]: m for m in obs["mechs"]}
        dmg, last = {}, {}
        for ht, by, amount, bearing in self.hits:
            dmg[by] = dmg.get(by, 0) + amount
            last[by] = (ht, bearing)
        ranked = sorted(dmg, key=lambda k: -dmg[k])
        for by in ranked:
            if by in seen:
                return seen[by], None, "attacker"
        if ranked:
            ht, bearing = last[ranked[0]]
            if obs["t"] - ht <= FACE_S:
                return None, bearing, "face hit"
        if enemies:
            return enemies[0], None, "nearest"
        return None, None, ""

    # ------------------------------------------------------------------ ballistics
    def _size(self, m):
        """(radius, height) of a mech or NPC."""
        if m.get("kind") == "npc":
            return self.npc_radius, self.npc_height
        s = 1 + self.size_per_cube * (m.get("cubes") or 0)
        return 2.5 * s, 9.0 * s

    def predict(self, m, dt, z_off):
        """Where m's aim point (z_off above its feet) will be dt seconds from now."""
        (px, py, pz), (vx, vy, vz) = m["pos"], m["vel"]
        qx, qy, qz = px + vx * dt, py + vy * dt, pz + vz * dt
        if abs(vz) > 1.0:  # airborne (or on a steep slope: the ground clamp keeps it honest)
            qz -= 0.5 * self.gravity * dt * dt
            if self.world is not None:
                qz = max(qz, self.world.ground(qx, qy))
        return qx, qy, qz + z_off

    def lead(self, origin, m, z_off, speed):
        """(point, distance, flight time) for a straight shot from origin at speed."""
        tf = 0.0
        for _ in range(3):
            q = self.predict(m, tf + LATENCY_S, z_off)
            d = math.dist(origin, q)
            tf = max(d - SHOT_OFFSET_M, 0.0) / speed
        return q, d, tf

    def clear(self, a, b):
        return self.world is None or not self.world.blocked(a, b)

    @staticmethod
    def angles(origin, q):
        dx, dy, dz = q[0] - origin[0], q[1] - origin[1], q[2] - origin[2]
        return math.atan2(dy, dx), math.atan2(dz, math.hypot(dx, dy))

    def gun_solution(self, origin, m, speed, height):
        """(yaw, pitch, dist, point, clear) for the first aim height with a clear line, else the center."""
        first = None
        for frac in AIM_HEIGHTS:
            q, d, _ = self.lead(origin, m, frac * height, speed)
            yaw, pitch = self.angles(origin, q)
            ok = self.clear(origin, q)
            if ok:
                return yaw, pitch, d, q, True
            first = first or (yaw, pitch, d, q, False)
        return first

    def mortar_solution(self, tube, m, stats):
        """(yaw, pitch, horizontal dist, point, clear) for a low-arc lob at m's feet, or None if out of reach."""
        v, g = stats.get("speed", 72.0), stats.get("gravity", 22.0)
        tf = 0.0
        for _ in range(3):
            q = self.predict(m, tf + LATENCY_S, 1.0)
            dx, dy, h = q[0] - tube[0], q[1] - tube[1], q[2] - tube[2]
            d = math.hypot(dx, dy)
            disc = v ** 4 - g * (g * d * d + 2 * h * v * v)
            if d < 1.0 or disc < 0:
                return None
            pitch = math.atan((v * v - math.sqrt(disc)) / (g * d))
            tf = d / (v * math.cos(pitch))
        yaw = math.atan2(dy, dx)
        ok = True
        if self.world is not None:  # check the arc in 8 straight pieces
            cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
            pts = [(tube[0] + v * cp * cy * s, tube[1] + v * cp * sy * s, tube[2] + v * sp * s - 0.5 * g * s * s)
                   for s in (tf * k / 8 for k in range(9))]
            ok = not any(self.world.blocked(pts[k], pts[k + 1]) for k in range(8))
        return yaw, pitch, d, q, ok

    # ------------------------------------------------------------------ the fight
    def engage(self, obs, m, legs_hurry):
        """(aim, fire) against target m."""
        me = obs["self"]
        x, y, z = me["pos"]
        st = me["stats"]
        aim_now = me["aim"]
        radius, height = self._size(m)
        mv = me.get("vel") or [0, 0, 0]
        origin = (x + mv[0] * LATENCY_S, y + mv[1] * LATENCY_S, z + mv[2] * LATENCY_S + st.get("muzzle_height", 7.0))
        fire = [False, False]
        slots = {w["kind"]: (i, w) for i, w in enumerate(me["weapons"]) if w}

        # gun: lead the target
        aim = None
        gun = slots.get("gun")
        if gun:
            i, w = gun
            gs = w.get("stats", {})
            yaw, pitch, d, q, ok = self.gun_solution(origin, m, gs.get("speed", 252.0), height)
            aim = [yaw, pitch]
            mf.draw.line([x, y, origin[2]], list(q), "#ff5040" if ok else "#806060")
            if (ok and d <= gs.get("range", 230.0) and w.get("ammo", 0) > 0 and (w.get("reload") or 0) <= 0
                    and aimed(aim_now, yaw, pitch, d, radius, height)):
                fire[i] = True

        # laser: hitscan, no lead. Charge on a clear target, let go when full and on target.
        laser = slots.get("laser")
        if laser:
            i, w = laser
            ls = w.get("stats", {})
            q = self.predict(m, LATENCY_S, 0.5 * height)
            d = math.dist(origin, q)
            yaw, pitch = self.angles(origin, q)
            ok = d <= ls.get("range", 280.0) and self.clear(origin, q)
            charge = w.get("charge") or 0.0
            if charge > 0:
                full = charge >= ls.get("charge", 0.75)
                if full and ok:
                    aim = [yaw, pitch]
                    # let go (fire=False) only when on target; holding on fires by itself after hold_s
                    fire[i] = not aimed(aim_now, yaw, pitch, d, radius, height)
                else:
                    fire[i] = True
            elif ok and w.get("cooldown", 0) <= 0 and not legs_hurry:  # charging halves our speed
                fire[i] = True

        # mortar: lob when ready and the arc is clear; it takes the aim until it fires
        mortar = slots.get("mortar")
        if mortar:
            i, w = mortar
            ms = w.get("stats", {})
            tube = (x, y, z + st.get("height", 9.0) + 0.5)
            sol = self.mortar_solution(tube, m, ms) if w.get("cooldown", 0) <= 0 else None
            if sol and sol[4] and sol[2] >= MORTAR_MIN_M:
                yaw, pitch, d, q, _ = sol
                aim = [yaw, pitch]
                v, g = ms.get("speed", 72.0), ms.get("gravity", 22.0)
                drange = max(abs(2 * v * v * math.cos(2 * pitch) / g), 1.0)  # impact metres per radian of pitch
                if (abs(_wrap(aim_now[0] - yaw)) * d <= AIM_TOL * radius + 2.0
                        and abs(aim_now[1] - pitch) * drange <= MORTAR_RANGE_ERR_M):
                    fire[i] = True
                mf.draw.point(list(q), "#ffa030", 6)

        if aim is None:  # no gun (never dropped, but be safe): just look at it
            aim = list(self.angles(origin, self.predict(m, LATENCY_S, 0.5 * height)))
        return aim, fire

    # ------------------------------------------------------------------ the decision
    def decide(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        act: mf.Action = {"tick": obs["tick"]}
        if obs["offer"]:
            act["pick"] = pick_upgrade(obs["offer"])

        # loot: a laser or mortar within reach, into the empty slot
        weapons = me["weapons"]
        held = {w["kind"] for w in weapons if w}
        if None in weapons:
            near = [p for p in obs["pickups"] if p["kind"] in TAKE_KINDS and p["kind"] not in held
                    and math.dist(p["pos"][:2], (x, y)) <= self.reach and abs(p["pos"][2] - z) <= TAKE_Z_M]
            if near:
                near.sort(key=lambda p: (TAKE_KINDS.index(p["kind"]), math.dist(p["pos"][:2], (x, y))))
                act["take"] = near[0]["id"]

        self.note_damage(obs)
        enemies = sorted((m for m in obs["mechs"] if m["kind"] == "mech"), key=lambda m: math.dist(m["pos"], me["pos"]))
        target, face, why = self.choose_target(obs, enemies)

        goal, speed, boost, mode = self.zone_move(obs)
        nc = obs["zone"]["next_center"]
        if goal is None:
            move = [0.0, 0.0]
            yaw = math.atan2(nc[1] - y, nc[0] - x)
        else:
            yaw = math.atan2(goal[1] - y, goal[0] - x)
            move = [math.cos(yaw) * speed, math.sin(yaw) * speed]
        act.update(move=move, aim=[yaw, 0.0], boost=boost, jump=obs["tick"] % 40 == 0, fire=[False, False])

        if target is not None:
            aim, fire = self.engage(obs, target, mode == "zone!")
            act.update(aim=aim, fire=fire)
        elif face is not None:
            act["aim"] = [face, 0.0]

        # Reload a partly used magazine whenever no enemy is in view (and nothing, e.g. an NPC, is engaged).
        gun = next((w for w in weapons if w and w["kind"] == "gun"), None)
        if gun and not enemies and target is None and (gun.get("reload") or 0) <= 0:
            ammo, mag = gun.get("ammo"), (gun.get("stats") or {}).get("mag")
            if ammo is not None and (ammo < mag if mag is not None else ammo == 0):
                act["reload"] = True

        if goal is not None:
            mf.draw.point([goal[0], goal[1], z], "#40c0ff", 6)
        tag = f"{why} {target['id']}" if target is not None else why
        mf.draw.label(f"{mode} · hp {me['hp']:.0f} · {len(enemies)} in sight · {tag}")
        return act


if __name__ == "__main__":
    mf.run(Bot)
