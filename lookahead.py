"""v9 experiment LOOKAHEAD: under threat, simulate a short future for a few candidate moves and take the one
with the least expected damage.

Runs after legs() has picked the v6 move (the baseline), only outside zone hurry and only under threat: a
visible enemy gun bullet, NPC bullet or mortar shell that could reach us within LOOK_H, or an engaging
enemy mech (squad.engagers: aimed at us in range, or hit us recently). Candidates: the baseline, standing
still, and 8 compass directions at walk speed. Our legs are simulated in LOOK_DT steps, accelerating at
ground_accel toward the commanded velocity and stopping at walls. Expected damage counts:
  - bullets in flight (gun, NPC): a pass within our radius + LOOK_PAD_M while inside our height;
  - mortar shells: direct hit, or splash falling to splash_edge at splash_radius, where they land;
  - predicted shots: each engaging gunner fires every gun cooldown at our position then plus k x our
    velocity x flight time (k: LEARN_DODGE's learned lead, else LOOK_K_PRIOR), hitting with probability
    exp(-miss^2 / 2 sigma^2); a charging laser lands LOOK_LASER_P x its damage; no line of sight, no shots;
  - the zone: its dps for every step outside.
The best candidate replaces the baseline only if it saves LOOK_MIN_GAIN. Evaluation stops LOOK_BUDGET_S
after the decision started (the baseline is scored first), so the decision stays inside its 10 ms cap.
"""
import math
import time

from safety import zone_at

LOOK_H = 0.8            # seconds simulated
LOOK_DT = 0.1
LOOK_PAD_M = 0.3        # bullet hit margin around our cylinder
LOOK_SIGMA_M = 1.5      # spread of a predicted shot around its aim point
LOOK_K_PRIOR = 0.7      # lead fraction before LEARN_DODGE knows better (measured on the house bot)
LOOK_LASER_P = 0.8
LOOK_MIN_GAIN = 15.0    # HP of expected damage a candidate must save to replace the baseline
LOOK_BUDGET_S = 0.008   # from the decision's start
LOOK_MAX_PROJ = 20
LOOK_MAX_ENG = 4
LOOK_FIRST_SHOT_S = 0.1  # an engaging gunner's next shot


class LookaheadMixin:
    """Uses self.wcfg (hello weapons config), self.decision_t0, self.engagers (squad.py), self.predict,
    self.lead_k (learn_dodge.py), self.world and self._size."""

    def _sim(self, me, move, speed, accel, walls):
        """Our (x, y) at t = 0, LOOK_DT, ..., LOOK_H under a constant move command."""
        x, y, z = me["pos"]
        v = me.get("vel") or [0, 0, 0]
        vx, vy = v[0], v[1]
        tx, ty = move[0] * speed, move[1] * speed
        pts = [(x, y)]
        h = (me.get("stats") or {}).get("height", 9.0)
        for _ in range(round(LOOK_H / LOOK_DT)):
            dx, dy = tx - vx, ty - vy
            dv = math.hypot(dx, dy)
            lim = accel * LOOK_DT
            if dv > lim:
                dx, dy = dx * lim / dv, dy * lim / dv
            vx, vy = vx + dx, vy + dy
            nx, ny = x + vx * LOOK_DT, y + vy * LOOK_DT
            if walls is not None and (walls.solid_at(nx, ny, z + 1.0) or walls.solid_at(nx, ny, z + h - 1.0)):
                vx = vy = 0.0
            else:
                x, y = nx, ny
            pts.append((x, y))
        return pts

    @staticmethod
    def _at(pts, t):
        """Our (x, y, vx, vy) at time t (linear between steps, straight on past the end)."""
        n = len(pts) - 1
        k = min(int(t / LOOK_DT), n - 1)
        (ax, ay), (bx, by) = pts[k], pts[k + 1]
        vx, vy = (bx - ax) / LOOK_DT, (by - ay) / LOOK_DT
        s = t - k * LOOK_DT
        return ax + vx * s, ay + vy * s, vx, vy

    def look_threats(self, obs, enemies):
        """(bullets, shells, gunners, lasers) worth simulating, or None if nothing threatens us."""
        me = obs["self"]
        x, y, z = me["pos"]
        reach = 20.0 + 2 * LOOK_PAD_M + (me.get("stats") or {}).get("radius", 2.5)
        bullets, shells = [], []
        for p in obs.get("projectiles") or []:
            kind = p.get("kind")
            (px, py, pz), (vx, vy, vz) = p["pos"], p["vel"]
            if kind in ("gun", "npc"):
                vv = vx * vx + vy * vy
                s = 0.0 if vv < 1e-9 else max(0.0, min(LOOK_H, -((px - x) * vx + (py - y) * vy) / vv))
                d = math.hypot(px + vx * s - x, py + vy * s - y)
                if d <= reach:
                    bullets.append((d, p))
            elif kind == "mortar":
                shells.append(p)
        bullets = [p for _, p in sorted(bullets, key=lambda dp: dp[0])[:LOOK_MAX_PROJ]]
        eng = self.engagers(obs, enemies)[:LOOK_MAX_ENG]
        gunners = [m for m in eng if "gun" in (m.get("weapons") or ())]
        lasers = [m for m in eng if m.get("charging")]
        if not (bullets or shells or gunners or lasers):
            return None
        return bullets, shells, gunners, lasers

    def _expected_damage(self, obs, pts, threats, k_of):
        me = obs["self"]
        z = me["pos"][2]
        st = me.get("stats") or {}
        r, h = st.get("radius", 2.5) + LOOK_PAD_M, st.get("height", 9.0)
        bullets, shells, gunners, lasers = threats
        n = len(pts) - 1
        dmg = 0.0
        # bullets in flight: closest approach per step, our motion linear within the step
        for p in bullets:
            (bx, by, bz), (bvx, bvy, bvz) = p["pos"], p["vel"]
            hit = self.wcfg.get("gun", {}).get("damage", 45.0) if p.get("kind") == "gun" else self.npc_damage
            for k in range(n):
                t0 = k * LOOK_DT
                (ax, ay), (cx, cy) = pts[k], pts[k + 1]
                ux, uy = (cx - ax) / LOOK_DT, (cy - ay) / LOOK_DT
                dx, dy = bx + bvx * t0 - ax, by + bvy * t0 - ay
                wx, wy = bvx - ux, bvy - uy
                ww = wx * wx + wy * wy
                s = 0.0 if ww < 1e-9 else max(0.0, min(LOOK_DT, -(dx * wx + dy * wy) / ww))
                if math.hypot(dx + wx * s, dy + wy * s) <= r and z - LOOK_PAD_M <= bz + bvz * (t0 + s) <= z + h + LOOK_PAD_M:
                    dmg += hit
                    break
        # mortar shells: where and when they land
        ms = self.wcfg.get("mortar", {})
        g = ms.get("gravity", 22.0)
        for p in shells:
            (sx, sy, sz), (svx, svy, svz) = p["pos"], p["vel"]
            disc = svz * svz + 2 * g * (sz - z)
            if disc < 0:
                continue
            ti = (svz + math.sqrt(disc)) / g
            if not 0 <= ti <= LOOK_H:
                continue
            qx, qy, _, _ = self._at(pts, ti)
            d = max(0.0, math.hypot(sx + svx * ti - qx, sy + svy * ti - qy) - r)
            rad = ms.get("splash_radius", 7.0)
            if d <= 0:
                dmg += ms.get("damage", 200.0) + ms.get("splash", 160.0)
            elif d <= rad:
                dmg += ms.get("splash", 160.0) * (1 - (1 - ms.get("splash_edge", 0.3)) * d / rad)
        # predicted shots: line of sight judged at the end of the run
        ex, ey = pts[-1]
        gs = self.wcfg.get("gun", {})
        speed, cool, gdmg = gs.get("speed", 252.0), gs.get("cooldown", 0.24), gs.get("damage", 45.0)
        two_s2 = 2 * LOOK_SIGMA_M * LOOK_SIGMA_M
        for m in gunners:
            _, mh = self._size(m)
            mz = m["pos"][2] + 0.78 * mh
            mx, my, _ = self.predict(m, LOOK_H, 0.0)
            if not self.clear((mx, my, mz), (ex, ey, z + 0.5 * h)):
                continue
            k = k_of(m["id"])
            tau = LOOK_FIRST_SHOT_S
            while tau < LOOK_H:
                sx, sy, _ = self.predict(m, tau, 0.0)
                px, py, vx, vy = self._at(pts, tau)
                tf = math.hypot(px - sx, py - sy) / speed
                ax, ay = px + k * vx * tf, py + k * vy * tf
                qx, qy, _, _ = self._at(pts, tau + tf)
                miss2 = (qx - ax) ** 2 + (qy - ay) ** 2
                dmg += gdmg * math.exp(-miss2 / two_s2)
                tau += cool
        ldmg = self.wcfg.get("laser", {}).get("damage", 315.0)
        for m in lasers:
            _, mh = self._size(m)
            if self.clear((m["pos"][0], m["pos"][1], m["pos"][2] + 0.78 * mh), (ex, ey, z + 0.5 * h)):
                dmg += LOOK_LASER_P * ldmg
        # the zone
        zn, t = obs["zone"], obs["t"]
        for k in range(1, n + 1):
            cx, cy, cr = zone_at(zn, t + k * LOOK_DT)
            if math.hypot(pts[k][0] - cx, pts[k][1] - cy) > cr:
                dmg += (zn.get("dps") or 0.0) * LOOK_DT
        return dmg

    def lookahead(self, obs, enemies, base_move, base_boost):
        """A unit move that beats the baseline's expected damage by LOOK_MIN_GAIN, or None."""
        threats = self.look_threats(obs, enemies)
        if threats is None:
            return None
        me = obs["self"]
        st = me.get("stats") or {}
        walk, boost_v = self._walk_mps(me), st.get("boost_speed", 19.5)
        accel = st.get("ground_accel", 58.5)
        deadline = (getattr(self, "decision_t0", None) or time.perf_counter()) + LOOK_BUDGET_S
        k_cache = {}

        def k_of(mid):
            if mid not in k_cache:
                k = self.lead_k(mid)
                k_cache[mid] = LOOK_K_PRIOR if k is None else k
            return k_cache[mid]

        bl = math.hypot(base_move[0], base_move[1])
        base_speed = (boost_v if base_boost else walk) * min(bl, 1.0)
        base_dir = (base_move[0] / bl, base_move[1] / bl) if bl > 1e-9 else (0.0, 0.0)
        cands = [(base_dir, base_speed), ((0.0, 0.0), 0.0)]
        cands += [((math.cos(a), math.sin(a)), walk) for a in (i * math.pi / 4 for i in range(8))]
        base_cost, best = None, None
        for (d, sp) in cands:
            if base_cost is not None and time.perf_counter() > deadline:
                break
            cost = self._expected_damage(obs, self._sim(me, d, sp, accel, self.world), threats, k_of)
            if base_cost is None:
                base_cost = cost
            elif best is None or cost < best[0]:
                best = (cost, d)
        if best is None or base_cost - best[0] < LOOK_MIN_GAIN:
            return None
        return [best[1][0], best[1][1]]
