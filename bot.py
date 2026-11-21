"""Mech Royale bot v5: more kills per match.

v5 = v3's strategy (v4's retreat, quiet walk and NPC avoidance dropped) plus:
  1. Target choice: mechs before NPC drones. Among visible mechs with a clear shot in reach, the lowest
     HP (sticky: a new one must be STICKY_HP lower). A mech hitting us (last HIT_RECENT_S) takes the
     target over, unless the current target is below FINISH_FRAC of its HP: finish that kill first.
  2. Strafe while fighting a target in reach: legs move perpendicular to it at full speed, reversing
     every STRAFE_MIN_S..STRAFE_MAX_S (random, seeded), and early before a wall or the zone edge.
  3. Dodge: a visible gun/NPC bullet that will pass within DODGE_M of us in the next DODGE_HORIZON_S
     -> step sideways, perpendicular to its path, on the side we are already off its line.
  4. Early laser fetch: before the first zone phase ends, with no laser held, a visible laser pickup
     (within FETCH_MAX_M, inside the zone) and no enemy mech within FETCH_SAFE_M -> walk to it. A mortar
     in slot 1 is swapped out for it.
  5. Circular aim: each mech's turn rate is estimated from its velocity heading between observations;
     predictions (gun lead, laser, mortar) follow that arc.
  Legs: zone hurry > dodge > laser fetch > strafe > v2 zone walk.
Kept from v4 (fixes, not strategy): the safety net answers an upgrade offer; non-finite numbers in the
action or a debug drawing fall back instead of sending invalid JSON; world.py's grid and clipping.

v3, on top of v2 (safety net + zone awareness):
  1. Face the attacker: damage events are remembered for a few seconds; if the attacker can't be seen,
     the torso turns to the hit's bearing (hits from outside the front arc do 1.5x).
  2. Lead: the gun aims where the target will be when the bullet (weapon speed, no drop) gets there,
     allowing for one decision of latency and gravity on airborne targets.
  3. Clear shot only: a weapon fires only when the torso's actual aim is on the solution and the line
     (or the mortar arc) misses every building and the terrain (world.py). Reload a partly used
     magazine when no enemy mech is in view and nothing is engaged.
  4. Loot: take a laser (preferred) or mortar lying within 4 m. The laser charges on a clear target and
     is released when fully charged and on target; the mortar is lobbed on a low arc when its arc is
     clear.
  5. Upgrades: "armor" when it is offered, otherwise choice 0.
"""
import atexit
import gc
import json
import math
import random
import time

import mechfight as mf

# --- zone tuning (v2) ---
EDGE_MARGIN_M = 25.0   # closer than this to the zone edge (now, or LOOKAHEAD_S from now) = hurry
LOOKAHEAD_S = 6.0      # also judge the edge where it will be this many seconds from now
SPOT_FRAC = 0.5        # target spot: this fraction of next_radius from next_center, on our side
ARRIVE_M = 3.0         # closer than this to the spot = stand still
SLOW_FRAC = 0.9        # slow walk = 90% of sound.step_speed: no footstep sounds
SPARE_S = 10.0         # walk at full speed if slow walking would reach the spot later than shrink_end - SPARE_S

# --- combat tuning (v3) ---
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

# --- kills tuning (v5) ---
MAX_CANDIDATES = 4      # clear-shot checks for at most this many nearest mechs per decision
STICKY_HP = 100.0       # switch to another clear mech only if it has at least this much less HP
HIT_RECENT_S = 2.0      # a mech that damaged us this recently is "hitting us"
FINISH_FRAC = 0.3       # ...but a current target below this fraction of its HP is finished first
STRAFE_MIN_S, STRAFE_MAX_S = 1.0, 2.0  # strafe direction lasts a random time in this range
STRAFE_LOOK_M = 6.0     # reverse early if this far along the strafe is a wall or near the zone edge
DODGE_M = 4.0           # dodge a bullet that will pass closer than this to our center
DODGE_HORIZON_S = 1.0   # ...within this many seconds
FETCH_MAX_M = 150.0     # walk to a visible laser pickup at most this far away
FETCH_SAFE_M = 60.0     # ...only with no enemy mech this close
FETCH_ARRIVE_M = 1.0
EARLY_DEFAULT_S = 110.0  # end of the first zone phase if the config doesn't say
OMEGA_MAX = 2.5         # rad/s cap on an estimated turn rate
OMEGA_ALPHA = 0.5       # smoothing of the turn-rate estimate
MOTION_DT_MAX = 0.25    # observations further apart than this don't give a turn rate
MOTION_MIN_SPEED = 2.0  # slower than this, the heading is noise

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


def _hp(m):
    hp = m.get("hp")
    return hp if isinstance(hp, (int, float)) else (m.get("max_hp") or 1400.0)


def _hp_frac(m):
    return _hp(m) / (m.get("max_hp") or 1400.0)


class Bot:
    name = "mech-royale-bot v5"

    def __init__(self, hello: mf.Hello):
        cfg = hello["config"]
        self.reach = cfg["loot"]["pickup_radius"]
        self.step_speed = cfg["sound"]["step_speed"]
        self.walk_speed = cfg["mech"].get("walk_speed", 11.7)
        self.gravity = cfg["mech"].get("gravity", 22.0)
        self.size_per_cube = cfg.get("cubes", {}).get("size_per_cube", 0.05)
        self.npc_radius = cfg.get("npc", {}).get("radius", 1.8)
        self.npc_height = cfg.get("npc", {}).get("height", 3.5)
        self.early_s = EARLY_DEFAULT_S
        try:
            ph = cfg["zone"]["phases"][0]
            self.early_s = float(ph["hold_s"]) + float(ph["shrink_s"])
        except Exception:
            pass
        self.rng = random.Random(hello.get("seed", 0))
        self.hits = []  # (t, attacker id, amount, bearing) of recent damage
        self.target_id = None
        self.motion = {}  # mech id -> (t, velocity heading or None, turn rate)
        self.strafe_dir = self.rng.choice((-1, 1))
        self.strafe_until = 0.0
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

    # ------------------------------------------------------------------ safety net
    def act(self, obs: mf.Observation) -> mf.Action:
        t0 = time.perf_counter()
        try:
            act = self.decide(obs)
            self._validate(act)
            self._scrub_drawing()
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
        json.dumps(act, allow_nan=False)  # anything else non-finite or unserializable

    @staticmethod
    def _scrub_drawing():
        """Drop debug items with non-finite coordinates (they would make the whole line invalid JSON)."""
        d = getattr(mf.draw, "_d", None)
        if not isinstance(d, dict):
            return
        for key, fields in (("lines", ("a", "b")), ("points", ("at",)), ("text", ("at",))):
            items = d.get(key)
            if items:
                d[key] = [it for it in items if all(_finite(*it[f]) for f in fields if f in it)]

    def safe(self, obs):
        """Move toward the zone's next center, no firing; answer a pending upgrade offer. Never raises."""
        act = {"tick": obs.get("tick"), "fire": [False, False]}
        try:
            if obs.get("offer"):
                act["pick"] = pick_upgrade(obs["offer"])
        except Exception:
            pass
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
    def _walk_mps(self, me):
        """Our walk speed (after upgrades); the config default if missing or not positive."""
        v = (me.get("stats") or {}).get("walk_speed")
        return v if isinstance(v, (int, float)) and v > 0 else self.walk_speed

    @staticmethod
    def _edge(z, t, x, y):
        """How far (x, y) is inside the zone edge, now and LOOKAHEAD_S from now (the smaller)."""
        cx, cy, r = zone_at(z, t)
        fx, fy, fr = zone_at(z, t + LOOKAHEAD_S)
        return min(r - math.hypot(x - cx, y - cy), fr - math.hypot(x - fx, y - fy))

    def zone_move(self, obs):
        """(goal or None, speed 0..1, boost, mode) for the legs."""
        me, z, t = obs["self"], obs["zone"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        ncx, ncy = z["next_center"]
        nr = z["next_radius"]

        if not me["in_zone"] or self._edge(z, t, x, y) < EDGE_MARGIN_M:
            return (ncx, ncy), 1.0, not me["in_zone"], "zone!"

        # A spot inside the next circle on the line from its center to us (not the crowded center).
        d = math.hypot(x - ncx, y - ncy)
        keep = SPOT_FRAC * nr
        if d <= keep + ARRIVE_M:
            return None, 0.0, False, "hold"
        goal = (ncx + (x - ncx) / d * keep, ncy + (y - ncy) / d * keep)
        dist = d - keep
        slow_mps = SLOW_FRAC * self.step_speed
        walk_mps = self._walk_mps(me)
        time_left = z["shrink_end"] - t - SPARE_S
        if dist > slow_mps * max(time_left, 0.0):
            return goal, 1.0, False, "walk"
        return goal, min(1.0, slow_mps / walk_mps), False, "sneak"

    # ------------------------------------------------------------------ attackers and targets
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

    def _reach(self, me):
        """Longest range among our direct-fire weapons (gun, laser)."""
        r = 0.0
        for w in me["weapons"]:
            if w and w["kind"] in ("gun", "laser"):
                r = max(r, (w.get("stats") or {}).get("range", 230.0 if w["kind"] == "gun" else 280.0))
        return r

    def clear_mechs(self, obs, enemies):
        """[(hp, dist, mech)] for the nearest MAX_CANDIDATES mechs we have a clear shot at, in reach."""
        me = obs["self"]
        origin = self._origin(me)
        gun = next((w for w in me["weapons"] if w and w["kind"] == "gun"), None)
        laser = next((w for w in me["weapons"] if w and w["kind"] == "laser"), None)
        out = []
        for m in enemies[:MAX_CANDIDATES]:
            _, height = self._size(m)
            ok, d = False, math.dist(origin, m["pos"])
            if gun:
                gs = gun.get("stats") or {}
                ok = self.gun_solution(origin, m, gs.get("speed", 252.0), height, gs.get("range", 230.0))[4]
            if not ok and laser:
                q = self.predict(m, LATENCY_S, 0.5 * height)
                ok = math.dist(origin, q) <= (laser.get("stats") or {}).get("range", 280.0) and self.clear(origin, q)
            if ok:
                out.append((_hp(m), d, m))
        return out

    def choose_target(self, obs, enemies):
        """(target or None, bearing to face or None, why). Mechs first: the lowest-HP clear mech (sticky),
        unless a mech is hitting us and the current target isn't nearly dead; then v3's fallbacks."""
        t = obs["t"]
        seen = {m["id"]: m for m in obs["mechs"]}
        dmg, last = {}, {}
        for ht, by, amount, bearing in self.hits:
            dmg[by] = dmg.get(by, 0) + amount
            last[by] = (ht, bearing)
        is_mech = lambda i: seen[i]["kind"] == "mech" if i in seen else (isinstance(i, (int, float)) and 0 <= i < 100)
        hitting = sorted((i for i in dmg if is_mech(i) and t - last[i][0] <= HIT_RECENT_S), key=lambda i: -dmg[i])

        target, why = None, ""
        clear = self.clear_mechs(obs, enemies)
        if clear:
            hp, _, best = min(clear, key=lambda c: (c[0], c[1]))
            cur = next((c for c in clear if c[2]["id"] == self.target_id), None)
            if cur is not None and hp > cur[0] - STICKY_HP:
                best = cur[2]
            target, why = best, "weakest"
        if hitting and (target is None or (hitting[0] != target["id"] and _hp_frac(target) >= FINISH_FRAC)):
            a = hitting[0]
            if a in seen:
                target, why = seen[a], "attacker"
            else:
                self.target_id = None
                return None, last[a][1], "face hit"
        if target is not None:
            self.target_id = target["id"]
            return target, None, why

        # nothing clear: v3's order (a visible attacker, mechs first; an unseen hit's bearing; the nearest)
        ranked = sorted(dmg, key=lambda i: (not is_mech(i), -dmg[i]))
        for by in ranked:
            if by in seen:
                self.target_id = by
                return seen[by], None, "attacker"
        self.target_id = None
        if ranked:
            ht, bearing = last[ranked[0]]
            if t - ht <= FACE_S:
                return None, bearing, "face hit"
        if enemies:
            self.target_id = enemies[0]["id"]
            return enemies[0], None, "nearest"
        return None, None, ""

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

    # ------------------------------------------------------------------ ballistics
    def _size(self, m):
        """(radius, height) of a mech or NPC."""
        if m.get("kind") == "npc":
            return self.npc_radius, self.npc_height
        s = 1 + self.size_per_cube * (m.get("cubes") or 0)
        return 2.5 * s, 9.0 * s

    def predict(self, m, dt, z_off):
        """Where m's aim point (z_off above its feet) will be dt seconds from now: along an arc at its
        estimated turn rate (a straight line if it isn't turning)."""
        (px, py, pz), (vx, vy, vz) = m["pos"], m["vel"]
        w = self.motion.get(m.get("id"), (0, None, 0.0))[2] if m.get("kind") == "mech" else 0.0
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

    def gun_solution(self, origin, m, speed, height, rng):
        """(yaw, pitch, dist, point, clear) for the first aim height with a clear line, else the center.
        Out of range counts as not clear (and skips the cover check)."""
        first = None
        for frac in AIM_HEIGHTS:
            q, d, _ = self.lead(origin, m, frac * height, speed)
            yaw, pitch = self.angles(origin, q)
            if d > rng:
                return yaw, pitch, d, q, False
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

    # ------------------------------------------------------------------ the fight (torso)
    def engage(self, obs, m, legs_hurry):
        """(aim, fire) against target m."""
        me = obs["self"]
        x, y, z = me["pos"]
        st = me["stats"]
        aim_now = me["aim"]
        radius, height = self._size(m)
        origin = self._origin(me)
        fire = [False, False]
        slots = {w["kind"]: (i, w) for i, w in enumerate(me["weapons"]) if w}

        # gun: lead the target
        aim = None
        gun = slots.get("gun")
        if gun:
            i, w = gun
            gs = w.get("stats", {})
            rng = gs.get("range", 230.0)
            yaw, pitch, d, q, ok = self.gun_solution(origin, m, gs.get("speed", 252.0), height, rng)
            aim = [yaw, pitch]
            mf.draw.line([x, y, origin[2]], list(q), "#ff5040" if ok else "#806060")
            if (ok and d <= rng and w.get("ammo", 0) > 0 and (w.get("reload") or 0) <= 0
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

    # ------------------------------------------------------------------ the legs (v5)
    def _lane_ok(self, obs, a):
        """Is STRAFE_LOOK_M along yaw a open ground (no wall) and clear of the zone edge?"""
        me = obs["self"]
        x, y, z = me["pos"]
        px, py = x + STRAFE_LOOK_M * math.cos(a), y + STRAFE_LOOK_M * math.sin(a)
        if self._edge(obs["zone"], obs["t"], px, py) < EDGE_MARGIN_M:
            return False
        if self.world is not None:
            h = (me.get("stats") or {}).get("height", 9.0)
            if self.world.solid_at(px, py, z + 1.0) or self.world.solid_at(px, py, z + h - 1.0):
                return False
        return True

    def _new_strafe(self, t):
        self.strafe_dir = -self.strafe_dir
        self.strafe_until = t + self.rng.uniform(STRAFE_MIN_S, STRAFE_MAX_S)

    def strafe_move(self, obs, target):
        """Unit move perpendicular to the target, or None if both sides are blocked."""
        me, t = obs["self"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        if t >= self.strafe_until:
            self._new_strafe(t)
        b = math.atan2(target["pos"][1] - y, target["pos"][0] - x)
        for _ in range(2):
            a = b + self.strafe_dir * math.pi / 2
            if self._lane_ok(obs, a):
                return [math.cos(a), math.sin(a)]
            self._new_strafe(t)
        return None

    def dodge_move(self, obs):
        """Unit move sideways off the path of the most urgent bullet that would pass within DODGE_M, or None."""
        me = obs["self"]
        x, y, z = me["pos"]
        cz = z + 0.5 * (me.get("stats") or {}).get("height", 9.0)
        mv = me.get("vel") or [0, 0, 0]
        best = None
        for p in obs.get("projectiles") or []:
            if p.get("kind") not in ("gun", "npc"):
                continue
            rx, ry, rz = p["pos"][0] - x, p["pos"][1] - y, p["pos"][2] - cz
            vx, vy, vz = p["vel"][0] - mv[0], p["vel"][1] - mv[1], p["vel"][2] - mv[2]
            vv = vx * vx + vy * vy + vz * vz
            if vv < 1e-9:
                continue
            tc = -(rx * vx + ry * vy + rz * vz) / vv
            if not 0 < tc <= DODGE_HORIZON_S:
                continue
            ox, oy, oz = rx + vx * tc, ry + vy * tc, rz + vz * tc  # the bullet, relative to us, when closest
            if math.sqrt(ox * ox + oy * oy + oz * oz) >= DODGE_M:
                continue
            if best is None or tc < best[0]:
                best = (tc, p, ox, oy)
        if best is None:
            return None
        _, p, ox, oy = best
        bx, by = p["vel"][0], p["vel"][1]
        hb = math.hypot(bx, by)
        if hb < 1e-6:
            return None  # falling straight down: no sideways
        nx, ny = -by / hb, bx / hb
        side = -(nx * ox + ny * oy)  # where we are relative to the bullet's closest point, across its path
        s = (1 if side > 0 else -1) if abs(side) > 1e-6 else self.strafe_dir
        return [nx * s, ny * s]

    def fetch_goal(self, obs, enemies):
        """(x, y) of a laser pickup worth walking to early in the match, or None."""
        me, t = obs["self"], obs["t"]
        if t >= self.early_s or any(w and w["kind"] == "laser" for w in me["weapons"]):
            return None
        if any(math.dist(m["pos"], me["pos"]) < FETCH_SAFE_M for m in enemies):
            return None
        x, y, z = me["pos"]
        best = None
        for p in obs["pickups"]:
            if p["kind"] != "laser" or abs(p["pos"][2] - z) > TAKE_Z_M:
                continue
            d = math.hypot(p["pos"][0] - x, p["pos"][1] - y)
            if d <= FETCH_MAX_M and self._edge(obs["zone"], t, p["pos"][0], p["pos"][1]) >= EDGE_MARGIN_M:
                if best is None or d < best[0]:
                    best = (d, p["pos"][0], p["pos"][1])
        return None if best is None else (best[1], best[2])

    # ------------------------------------------------------------------ the decision
    def decide(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        act: mf.Action = {"tick": obs["tick"]}
        if obs["offer"]:
            act["pick"] = pick_upgrade(obs["offer"])

        # loot: a laser or mortar within reach into the empty slot; a laser replaces a mortar
        weapons = me["weapons"]
        held = {w["kind"] for w in weapons if w}
        near = [p for p in obs["pickups"] if p["kind"] in TAKE_KINDS and p["kind"] not in held
                and math.dist(p["pos"][:2], (x, y)) <= self.reach and abs(p["pos"][2] - z) <= TAKE_Z_M]
        near.sort(key=lambda p: (TAKE_KINDS.index(p["kind"]), math.dist(p["pos"][:2], (x, y))))
        if near and None in weapons:
            act["take"] = near[0]["id"]
        elif near and near[0]["kind"] == "laser" and "mortar" in held:
            act["take"] = near[0]["id"]
            act["drop"] = next(i for i, w in enumerate(weapons) if w and w["kind"] == "mortar")

        self.note_damage(obs)
        self.note_motion(obs)
        enemies = sorted((m for m in obs["mechs"] if m["kind"] == "mech"), key=lambda m: math.dist(m["pos"], me["pos"]))
        target, face, why = self.choose_target(obs, enemies)

        # legs: zone hurry > dodge > laser fetch > strafe > zone walk
        goal, speed, boost, mode = self.zone_move(obs)
        legs = None
        if mode != "zone!":
            dodge = self.dodge_move(obs)
            fetch = None if dodge else self.fetch_goal(obs, enemies)
            if dodge:
                legs, mode = dodge, "dodge"
                if target is not None:  # keep strafing the way we dodged
                    b = math.atan2(target["pos"][1] - y, target["pos"][0] - x)
                    self.strafe_dir = 1 if dodge[0] * -math.sin(b) + dodge[1] * math.cos(b) >= 0 else -1
            elif fetch:
                goal, speed, boost, mode = fetch, 1.0, False, "fetch"
                if math.hypot(fetch[0] - x, fetch[1] - y) <= FETCH_ARRIVE_M:
                    goal = None
            elif target is not None and math.dist(target["pos"], me["pos"]) <= self._reach(me):
                s = self.strafe_move(obs, target)
                if s:
                    legs, mode = s, "strafe"
        nc = obs["zone"]["next_center"]
        if legs is not None:
            move, boost = legs, False
            yaw = math.atan2(legs[1], legs[0])
        elif goal is None:
            move = [0.0, 0.0]
            yaw = math.atan2(nc[1] - y, nc[0] - x)
        else:
            yaw = math.atan2(goal[1] - y, goal[0] - x)
            move = [math.cos(yaw) * speed, math.sin(yaw) * speed]
        act.update(move=move, aim=[yaw, 0.0], boost=boost, jump=obs["tick"] % 40 == 0, fire=[False, False])

        # torso
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

        if goal is not None and legs is None:
            mf.draw.point([goal[0], goal[1], z], "#40c0ff", 6)
        tag = f"{why} {target['id']}" if target is not None else why
        mf.draw.label(f"{mode} · hp {me['hp']:.0f} · {len(enemies)} in sight · {tag}")
        return act


if __name__ == "__main__":
    mf.run(Bot)
