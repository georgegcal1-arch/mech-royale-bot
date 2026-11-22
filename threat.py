"""v9 F1, features.THREAT_TARGET: shoot the mech most dangerous to us right now, not the weakest.

Threat of a clear mech (higher = shoot first):
  hitting us in the last HIT_RECENT_S     +HIT_W, plus its recent damage / HIT_DMG_SCALE
  lightning held and us within 50 m       +LIGHTNING_W   (45 m cone that chains)
  a laser charging                        +CHARGING_W
  gun held and us within its range        +GUN_W
  behind us (outside our 180 deg front)   +REAR_W        (its hits do x1.5)
  torso aimed at us                       +AIMED_W
  close                                   up to +CLOSE_W, falling to 0 at CLOSE_M
Exception: a clear mech we kill in under ~1 s (HP <= our one-second burst, cubes counted) is finished first.
Sticky: the current target stays unless another's threat is STICKY_THREAT higher.
"""
import math

from perception import _hp, _wrap

HIT_W, HIT_DMG_SCALE = 3.0, 200.0
LIGHTNING_W, LIGHTNING_M = 3.0, 50.0
CHARGING_W = 3.0
GUN_W, GUN_M = 1.0, 230.0
REAR_W = 1.5
AIMED_W, AIMED_RAD = 1.0, 0.3
CLOSE_W, CLOSE_M = 2.0, 100.0
STICKY_THREAT = 0.75
HIT_RECENT_S = 2.0
BURST_DPS = {"gun": 45.0 / 0.24, "lightning": 60.0 / 0.25}  # protocol.md defaults
LASER_BURST = 315.0
LIGHTNING_REACH_M = 45.0


class ThreatMixin:
    """Uses self.hits, self.target_id."""

    def threat_of(self, obs, m, recent):
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        mx, my = m["pos"][0], m["pos"][1]
        d = math.hypot(mx - x, my - y)
        kinds = set(m.get("weapons") or ())
        s = 0.0
        if m["id"] in recent:
            s += HIT_W + recent[m["id"]] / HIT_DMG_SCALE
        if "lightning" in kinds and d <= LIGHTNING_M:
            s += LIGHTNING_W
        if m.get("charging"):
            s += CHARGING_W
        if "gun" in kinds and d <= GUN_M:
            s += GUN_W
        our = (me.get("aim") or [None])[0]
        if our is not None and abs(_wrap(math.atan2(my - y, mx - x) - our)) > math.pi / 2:
            s += REAR_W
        aim = m.get("aim")
        if aim is not None and abs(_wrap(aim[0] - math.atan2(y - my, x - mx))) <= AIMED_RAD:
            s += AIMED_W
        s += CLOSE_W * max(0.0, 1.0 - d / CLOSE_M)
        return s

    @staticmethod
    def burst_1s(me, d):
        """Damage we can deal in about one second at range d (cubes counted)."""
        out = 0.0
        for w in me["weapons"]:
            if not w:
                continue
            k = w["kind"]
            if k == "gun" and (w.get("ammo") or 0) > 0:
                out += BURST_DPS["gun"]
            elif k == "lightning" and d <= LIGHTNING_REACH_M:
                out += BURST_DPS["lightning"]
            elif k == "laser" and (w.get("cooldown") or 0) <= 0.0 and (w.get("charge") or 0) > 0.5:
                out += LASER_BURST
        return out * (1.0 + 0.04 * (me.get("cubes") or 0))

    def threat_target(self, obs, clear):
        """The clear mech to shoot ([(hp, dist, mech)] from clear_mechs) and why ('finish' or 'threat')."""
        t, me = obs["t"], obs["self"]
        recent = {}
        for ht, by, amount, _ in self.hits:
            if t - ht <= HIT_RECENT_S:
                recent[by] = recent.get(by, 0.0) + amount
        quick = [c for c in clear if c[0] <= self.burst_1s(me, c[1])]
        if quick:
            return min(quick, key=lambda c: (c[0], c[1]))[2], "finish"
        scored = [(self.threat_of(obs, c[2], recent), c[2]) for c in clear]
        best_s, best = max(scored, key=lambda sc: sc[0])
        cur = next((sc for sc in scored if sc[1]["id"] == self.target_id), None)
        if cur is not None and cur[0] >= best_s - STICKY_THREAT:
            best = cur[1]
        return best, "threat"
