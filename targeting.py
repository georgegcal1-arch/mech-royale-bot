"""Who to shoot (v5): mechs first, the lowest-HP clear mech (sticky), an attacker taking over unless the
current target is nearly dead; then v3's fallbacks."""
import math

import features
from perception import LATENCY_S, _hp, _hp_frac

FACE_S = 2.0            # keep facing an unseen attacker's bearing this long after its last hit
MAX_CANDIDATES = 4      # clear-shot checks for at most this many nearest mechs per decision
STICKY_HP = 100.0       # switch to another clear mech only if it has at least this much less HP
HIT_RECENT_S = 2.0      # a mech that damaged us this recently is "hitting us"
FINISH_FRAC = 0.3       # ...but a current target below this fraction of its HP is finished first


class TargetingMixin:
    """Uses self.hits, self.target_id and the perception/combat methods."""

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
        unless a mech is hitting us and the current target isn't nearly dead; then v3's fallbacks.
        With features.LOWHP_TARGETING off, only v3's order."""
        t = obs["t"]
        seen = {m["id"]: m for m in obs["mechs"]}
        dmg, last = {}, {}
        for ht, by, amount, bearing in self.hits:
            dmg[by] = dmg.get(by, 0) + amount
            last[by] = (ht, bearing)
        is_mech = lambda i: seen[i]["kind"] == "mech" if i in seen else (isinstance(i, (int, float)) and 0 <= i < 100)

        if features.LOWHP_TARGETING:
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
