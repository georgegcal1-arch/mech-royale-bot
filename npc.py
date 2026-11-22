"""v9 experiment N, features.NPC_AWARE: deal with NPC drones (protocol.md section 7 "NPC drones").

Drones (12 per match, 350 HP, 2 cubes) shoot 25-damage bullets once a second at whoever attacks them, or at a
mech within aggro_range (45 m) with line of sight while they are near home, and chase until it is 90 m away.
In arena match VGYDJ one followed us at 15-25 m for 27 s and did 838 of the 1400 damage that killed us, while
the mechs-first targeting never shot it. So:

  Self-defense: a drone within NPC_DEFEND_M that hit us in the last NPC_HIT_S, with a clear line, becomes the
    target (lightning if held, else the gun), unless a mech is within NPC_MECH_CLOSE_M (that one matters more).
  Avoid: while fighting a mech (the strafe / duel / approach legs), a move that would take us into a drone's
    aggro range (+ NPC_AVOID_PAD_M) within NPC_AVOID_LOOK_S is turned (+-45, 90, 135, 180 degrees, smallest
    first, lane permitting) to one that doesn't.
  Farm: in quiet moments (economy's rule: no mech seen or heard), at NPC_FARM_HP of our HP or more, shoot the
    nearest drone within NPC_FARM_M with a clear line (2 cubes each, like a crate that shoots back); without a
    gun, walk to NPC_FARM_CLOSE_M first so lightning reaches it.
"""
import math

from perception import LATENCY_S, _hp_frac

NPC_DEFEND_M = 50.0
NPC_HIT_S = 2.5            # drones fire every 1 s
NPC_MECH_CLOSE_M = 40.0
NPC_AVOID_PAD_M = 5.0
NPC_AVOID_LOOK_S = 1.0
NPC_AVOID_TURNS = (math.pi / 4, -math.pi / 4, math.pi / 2, -math.pi / 2, 3 * math.pi / 4, -3 * math.pi / 4, math.pi)
NPC_AVOID_MODES = ("strafe", "duel", "approach", "look")
NPC_FARM_M = 60.0
NPC_FARM_HP = 0.5
NPC_FARM_CLOSE_M = 35.0


class NpcMixin:
    """Uses self.hits, the perception helpers (_origin, _size, predict, clear) and _lane_ok."""

    def _npcs(self, obs):
        return [m for m in obs["mechs"] if m.get("kind") == "npc"]

    def _clear_to(self, obs, m):
        origin = self._origin(obs["self"])
        _, height = self._size(m)
        q = self.predict(m, LATENCY_S, 0.5 * height)
        return self.clear(origin, q), math.dist(origin, q)

    def npc_defense(self, obs, enemies):
        """The drone to shoot back at, or None."""
        me, t = obs["self"], obs["t"]
        if enemies and math.dist(enemies[0]["pos"], me["pos"]) < NPC_MECH_CLOSE_M:
            return None
        hitters = {by for ht, by, _, _ in self.hits if t - ht <= NPC_HIT_S}
        best = None
        for m in self._npcs(obs):
            if m["id"] not in hitters:
                continue
            ok, d = self._clear_to(obs, m)
            if ok and d <= NPC_DEFEND_M and (best is None or d < best[0]):
                best = (d, m)
        return best and best[1]

    def npc_avoid(self, obs, move, aggro_m):
        """`move` (unit [x, y]), or a turned one that stays out of drones' aggro range."""
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        n = math.hypot(move[0], move[1])
        npcs = [m for m in self._npcs(obs)]
        if n < 1e-6 or not npcs:
            return move
        reach = self._walk_mps(me) * NPC_AVOID_LOOK_S
        r = aggro_m + NPC_AVOID_PAD_M

        def bad(a):
            px, py = x + reach * math.cos(a), y + reach * math.sin(a)
            for m in npcs:
                mx, my = m["pos"][0], m["pos"][1]
                d_now, d_next = math.hypot(x - mx, y - my), math.hypot(px - mx, py - my)
                if d_next < r and d_next < d_now:
                    return True
            return False

        a0 = math.atan2(move[1], move[0])
        if not bad(a0):
            return move
        for k in NPC_AVOID_TURNS:
            a = a0 + k
            if not bad(a) and self._lane_ok(obs, a):
                return [n * math.cos(a), n * math.sin(a)]
        return move

    def npc_farm(self, obs):
        """(drone, walk-to goal or None) to farm now, or None."""
        me = obs["self"]
        if _hp_frac(me) < NPC_FARM_HP:
            return None
        best = None
        for m in self._npcs(obs):
            ok, d = self._clear_to(obs, m)
            if ok and d <= NPC_FARM_M and (best is None or d < best[0]):
                best = (d, m)
        if best is None:
            return None
        d, m = best
        held = {w["kind"] for w in me["weapons"] if w}
        goal = None
        if "gun" not in held and d > NPC_FARM_CLOSE_M:
            goal = (m["pos"][0], m["pos"][1])
        return m, goal
