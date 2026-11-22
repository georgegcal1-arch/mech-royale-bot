"""v9 experiment L, features.LOOT_SWEEP: go and collect cubes and better weapons nearby, death-drop piles first.

The scorecard (tools/scorecard.py) on the arena matches: of the cubes that came within 30 m we picked up 8 of 25
(PGBKZ) and 0 of 4 (MUWWU); better weapons 1 of 4 (MUWWU). Economy only walks to cubes when nothing is seen or
heard. So, with no enemy mech within SWEEP_SAFE_M:

  Targets: cubes and the weapons loot.py would take (laser > lightning > mortar into an empty slot; laser or
    lightning over a mortar, or over the gun to complete laser + lightning) within SWEEP_M, on the ground (not
    on a roof), not within NPC_AGGRO_M of a drone, and inside the next circle by SWEEP_NEXT_MARGIN_M. A weapon
    may lie outside it if we can walk there and back inside before the shrink ends (minus SPARE_S) and it is
    clear of the current edge (arena ZEFXJ: a laser on the next circle's edge was skipped, and a gun-only bot
    then died to a laser). A laser or lightning counts WANT_BONUS_M closer while we hold neither.
  Piles first: an item within PILE_M of where we killed a mech in the last SWEEP_AFTER_KILL_S (killed mechs drop
    all their cubes + 2 and their weapons, protocol.md section 10) counts PILE_BONUS_M closer; right after a kill,
    with the pile not in view yet, walk to the kill spot itself if it is within SWEEP_KILL_SPOT_M.
  Walk: the target becomes the legs' cube goal (so NAV_ROUTE routes it); cubes are picked up by walking within
    reach, weapons by loot.py's take. The torso keeps fighting anything at range.
  Abort: hits from 2+ mechs within SWEEP_MULTI_S, or a laser charging at us (guard.py's chargers): no sweep for
    SWEEP_ABORT_S.
"""
import math

from safety import EDGE_MARGIN_M, SPARE_S

SWEEP_M = 40.0
SWEEP_SAFE_M = 40.0
SWEEP_NEXT_MARGIN_M = 10.0
SWEEP_ROOF_M = 2.0          # an item this high above the ground under it is on a roof: skip
NPC_AGGRO_M = 45.0
PILE_M = 12.0
PILE_BONUS_M = 25.0
SWEEP_AFTER_KILL_S = 12.0
SWEEP_KILL_SPOT_M = 60.0
SWEEP_KILL_ARRIVE_M = 4.0
SWEEP_MULTI_S = 3.0
SWEEP_ABORT_S = 4.0
TAKE_ORDER = ("laser", "lightning", "mortar")
WANT_BONUS_M = 25.0         # a laser or lightning when we hold neither counts this much closer


def would_take(kind, weapons):
    """loot.py's _take_loot_lightning rules: would we take a pickup of this kind holding `weapons`?"""
    slot = {w["kind"] for w in weapons if w}
    if kind not in TAKE_ORDER or kind in slot:
        return False
    if None in weapons:
        return True
    if kind == "mortar":
        return False
    other = "lightning" if kind == "laser" else "laser"
    return "mortar" in slot or (other in slot and "gun" in slot)


class LootSweepMixin:
    """Uses self.last_pos {mech id: (x, y, z)}, self.kill_spots [(t, x, y)], self.sweep_cool, self.hits, self.world
    and guard.py's laser_chargers."""

    def note_kills(self, obs):
        """Remember where each mech was last seen, and where the ones we killed fell."""
        t = obs["t"]
        for m in obs["mechs"]:
            if m.get("kind") == "mech":
                self.last_pos[m["id"]] = tuple(m["pos"])
        for e in obs.get("events") or []:
            if e.get("type") == "kill" and e.get("target") in self.last_pos:
                x, y, _ = self.last_pos[e["target"]]
                self.kill_spots.append((t, x, y))
        self.kill_spots = [k for k in self.kill_spots if t - k[0] <= SWEEP_AFTER_KILL_S]

    def _sweepable(self, obs, p, max_d=SWEEP_M, weapon=False):
        me, zn, t = obs["self"], obs["zone"], obs["t"]
        x, y, z = me["pos"]
        d = math.hypot(p[0] - x, p[1] - y)
        if d > max_d:
            return False
        (ncx, ncy), nr = zn["next_center"], zn["next_radius"]
        out = math.hypot(p[0] - ncx, p[1] - ncy) - (nr - SWEEP_NEXT_MARGIN_M)
        if out > 0:
            # a cube outside the next circle isn't worth it; a weapon is if we can grab it and still be back
            # inside before the shrink ends (heal.py's rule), and it isn't at the current edge
            if not weapon or self._edge(zn, t, p[0], p[1]) < EDGE_MARGIN_M:
                return False
            if (d + out) / self._walk_mps(me) > zn["shrink_end"] - t - SPARE_S:
                return False
        if self.world is not None and p[2] > self.world.ground(p[0], p[1]) + SWEEP_ROOF_M:
            return False
        for m in obs["mechs"]:
            if m.get("kind") == "npc" and math.hypot(p[0] - m["pos"][0], p[1] - m["pos"][1]) <= NPC_AGGRO_M:
                return False
        return True

    def sweep_goal(self, obs, enemies):
        """(x, y) to walk to for loot now, or None."""
        me, t = obs["self"], obs["t"]
        x, y, _ = me["pos"]
        if t < self.sweep_cool:
            return None
        if any(math.dist(m["pos"][:2], (x, y)) < SWEEP_SAFE_M for m in enemies):
            return None
        attackers = {by for ht, by, _, _ in self.hits if t - ht <= SWEEP_MULTI_S and isinstance(by, int) and 0 <= by < 100}
        if len(attackers) >= 2 or self.laser_chargers(obs, enemies):
            self.sweep_cool = t + SWEEP_ABORT_S
            return None
        held = {w["kind"] for w in me["weapons"] if w}
        items = [(q["pos"], None) for q in obs.get("cubes") or []]
        items += [(q["pos"], q["kind"]) for q in obs.get("pickups") or [] if would_take(q["kind"], me["weapons"])]
        best = None
        for p, kind in items:
            if not self._sweepable(obs, p, weapon=kind is not None):
                continue
            d = math.hypot(p[0] - x, p[1] - y)
            if any(math.hypot(p[0] - kx, p[1] - ky) <= PILE_M for _, kx, ky in self.kill_spots):
                d -= PILE_BONUS_M
            if kind in ("laser", "lightning") and not held & {"laser", "lightning"}:
                d -= WANT_BONUS_M
            if best is None or d < best[0]:
                best = (d, (p[0], p[1]))
        if best is not None:
            return best[1]
        for _, kx, ky in reversed(self.kill_spots):  # the pile isn't in view yet: go where it fell
            d = math.hypot(kx - x, ky - y)
            if SWEEP_KILL_ARRIVE_M < d and self._sweepable(obs, (kx, ky, me["pos"][2]), SWEEP_KILL_SPOT_M):
                return kx, ky
        return None
