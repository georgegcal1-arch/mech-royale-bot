"""Loot (v3): take a laser or mortar within reach; v5's early walk to a visible laser pickup."""
import math

from safety import EDGE_MARGIN_M

TAKE_KINDS = ("laser", "mortar")  # pickups worth taking, preferred first
TAKE_Z_M = 4.0          # vertical pickup reach (fixed by the protocol)
FETCH_MAX_M = 150.0     # walk to a visible laser pickup at most this far away
FETCH_SAFE_M = 60.0     # ...only with no enemy mech this close
FETCH_ARRIVE_M = 1.0
EARLY_DEFAULT_S = 110.0  # end of the first zone phase if the config doesn't say


class LootMixin:
    """Uses self.reach, self.early_s and self._edge (safety.py)."""

    def take_loot(self, obs, act):
        """A laser or mortar within reach into the empty slot; a laser replaces a mortar. Sets act take/drop."""
        me = obs["self"]
        x, y, z = me["pos"]
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
