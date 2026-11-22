"""Loot (v3): take a laser or mortar within reach; v5's early walk to a visible laser pickup.
v9 features.LIGHTNING: lightning too (laser > lightning > mortar); a laser or lightning replaces a mortar, and
the gun is dropped only to hold laser and lightning together; the early fetch also walks to a lightning gun."""
import math

import features
from safety import EDGE_MARGIN_M

TAKE_KINDS = ("laser", "mortar")  # pickups worth taking, preferred first
TAKE_KINDS_LIGHTNING = ("laser", "lightning", "mortar")  # v9 features.LIGHTNING
TAKE_KINDS_RUSH = ("lightning", "laser", "mortar")       # v9 B2b (features.AGGRO_RUSH): close fights want lightning
TAKE_Z_M = 4.0          # vertical pickup reach (fixed by the protocol)
FETCH_MAX_M = 150.0     # walk to a visible laser pickup at most this far away
FETCH_SAFE_M = 60.0     # ...only with no enemy mech this close
FETCH_ARRIVE_M = 1.0
EARLY_DEFAULT_S = 110.0  # end of the first zone phase if the config doesn't say


class LootMixin:
    """Uses self.reach, self.early_s and self._edge (safety.py)."""

    def take_loot(self, obs, act):
        """A laser or mortar within reach into the empty slot; a laser replaces a mortar. Sets act take/drop.
        v9 B5 (features.NO_MORTAR): never a mortar (an enemy laser does +60% to a mortar carrier, protocol.md
        section 7 "cook-off"), and one we hold is dropped when nothing else is being taken."""
        if features.LIGHTNING:
            self._take_loot_lightning(obs, act)
        else:
            self._take_loot_v3(obs, act)
        if features.NO_MORTAR and "take" not in act:
            mortar = next((i for i, w in enumerate(obs["self"]["weapons"]) if w and w["kind"] == "mortar"), None)
            if mortar is not None:
                act["drop"] = mortar

    def _take_loot_v3(self, obs, act):
        me = obs["self"]
        x, y, z = me["pos"]
        weapons = me["weapons"]
        held = {w["kind"] for w in weapons if w}
        near = [p for p in obs["pickups"] if p["kind"] in TAKE_KINDS and p["kind"] not in held
                and not (features.NO_MORTAR and p["kind"] == "mortar")
                and math.dist(p["pos"][:2], (x, y)) <= self.reach and abs(p["pos"][2] - z) <= TAKE_Z_M]
        near.sort(key=lambda p: (TAKE_KINDS.index(p["kind"]), math.dist(p["pos"][:2], (x, y))))
        if near and None in weapons:
            act["take"] = near[0]["id"]
        elif near and near[0]["kind"] == "laser" and "mortar" in held:
            act["take"] = near[0]["id"]
            act["drop"] = next(i for i, w in enumerate(weapons) if w and w["kind"] == "mortar")

    def _take_loot_lightning(self, obs, act):
        """v9: laser > lightning > mortar into an empty slot; with both slots full, a laser or lightning
        replaces a mortar, or replaces the gun when it completes laser + lightning. The gun is never taken."""
        me = obs["self"]
        x, y, z = me["pos"]
        weapons = me["weapons"]
        slot = {w["kind"]: i for i, w in enumerate(weapons) if w}
        near = [p for p in obs["pickups"] if p["kind"] in TAKE_KINDS_LIGHTNING and p["kind"] not in slot
                and not (features.NO_MORTAR and p["kind"] == "mortar")
                and math.dist(p["pos"][:2], (x, y)) <= self.reach and abs(p["pos"][2] - z) <= TAKE_Z_M]
        if not near:
            return
        order = TAKE_KINDS_RUSH if features.AGGRO_RUSH else TAKE_KINDS_LIGHTNING  # v9 B2b: lightning first
        near.sort(key=lambda p: (order.index(p["kind"]), math.dist(p["pos"][:2], (x, y))))
        if None in weapons:
            act["take"] = near[0]["id"]
            return
        for p in near:
            if p["kind"] == "mortar":
                continue
            other = "lightning" if p["kind"] == "laser" else "laser"
            if "mortar" in slot:
                act["take"], act["drop"] = p["id"], slot["mortar"]
                return
            if other in slot and "gun" in slot:
                act["take"], act["drop"] = p["id"], slot["gun"]
                return

    def fetch_goal(self, obs, enemies):
        """(x, y) of a laser pickup worth walking to early in the match, or None. v9 features.LIGHTNING: a
        laser or lightning pickup, whichever we don't hold yet."""
        me, t = obs["self"], obs["t"]
        if features.LIGHTNING:
            want = {"laser", "lightning"} - {w["kind"] for w in me["weapons"] if w}
            if t >= self.early_s or not want:
                return None
        elif t >= self.early_s or any(w and w["kind"] == "laser" for w in me["weapons"]):
            return None
        if any(math.dist(m["pos"], me["pos"]) < FETCH_SAFE_M for m in enemies):
            return None
        x, y, z = me["pos"]
        best = None
        for p in obs["pickups"]:
            if (p["kind"] not in want if features.LIGHTNING else p["kind"] != "laser") or abs(p["pos"][2] - z) > TAKE_Z_M:
                continue
            d = math.hypot(p["pos"][0] - x, p["pos"][1] - y)
            if d <= FETCH_MAX_M and self._edge(obs["zone"], t, p["pos"][0], p["pos"][1]) >= EDGE_MARGIN_M:
                if best is None or d < best[0]:
                    best = (d, p["pos"][0], p["pos"][1])
        return None if best is None else (best[1], best[2])
