"""v9 experiment L, features.LOOT_SWEEP: go and collect cubes and better weapons nearby, death-drop piles first.

The scorecard (tools/scorecard.py) on the arena matches: of the cubes that came within 30 m we picked up 8 of 25
(PGBKZ) and 0 of 4 (MUWWU); better weapons 1 of 4 (MUWWU). Economy only walks to cubes when nothing is seen or
heard. So, with no enemy mech within SWEEP_SAFE_M:

  Targets: cubes and the weapons loot.py would take (laser > lightning > mortar into an empty slot; laser or
    lightning over a mortar, or over the gun to complete laser + lightning) within SWEEP_M, on the ground (not
    on a roof), inside the next circle by SWEEP_NEXT_MARGIN_M, not within NPC_AGGRO_M of a drone.
  Piles first: an item within PILE_M of where we killed a mech in the last SWEEP_AFTER_KILL_S (killed mechs drop
    all their cubes + 2 and their weapons, protocol.md section 10) counts PILE_BONUS_M closer; right after a kill,
    with the pile not in view yet, walk to the kill spot itself if it is within SWEEP_KILL_SPOT_M.
  Walk: the target becomes the legs' cube goal (so NAV_ROUTE routes it); cubes are picked up by walking within
    reach, weapons by loot.py's take. The torso keeps fighting anything at range.
  Abort: hits from 2+ mechs within SWEEP_MULTI_S, or a laser charging at us (guard.py's chargers): no sweep for
    SWEEP_ABORT_S.
"""
import math

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

    def _sweepable(self, obs, p, max_d=SWEEP_M):
        me, zn = obs["self"], obs["zone"]
        x, y, z = me["pos"]
        if math.hypot(p[0] - x, p[1] - y) > max_d:
            return False
        (ncx, ncy), nr = zn["next_center"], zn["next_radius"]
        if math.hypot(p[0] - ncx, p[1] - ncy) > nr - SWEEP_NEXT_MARGIN_M:
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
        items = [q["pos"] for q in obs.get("cubes") or []]
        items += [q["pos"] for q in obs.get("pickups") or [] if would_take(q["kind"], me["weapons"])]
        best = None
        for p in items:
            if not self._sweepable(obs, p):
                continue
            d = math.hypot(p[0] - x, p[1] - y)
            if any(math.hypot(p[0] - kx, p[1] - ky) <= PILE_M for _, kx, ky in self.kill_spots):
                d -= PILE_BONUS_M
            if best is None or d < best[0]:
                best = (d, (p[0], p[1]))
        if best is not None:
            return best[1]
        for _, kx, ky in reversed(self.kill_spots):  # the pile isn't in view yet: go where it fell
            d = math.hypot(kx - x, ky - y)
            if SWEEP_KILL_ARRIVE_M < d and self._sweepable(obs, (kx, ky, me["pos"][2]), SWEEP_KILL_SPOT_M):
                return kx, ky
        return None
