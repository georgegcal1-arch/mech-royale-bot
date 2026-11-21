"""Tests for bot v3. Standard library only.

    MAP_DIR=/kit/arena KIT_DIR=/kit python3 -m unittest discover -s tests -v

MAP_DIR: the folder holding v2/world.json (tests that need it are skipped without it).
KIT_DIR: the mechfight kit (for the recorded stream in check/stream.jsonl.gz).
"""
import copy
import gzip
import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bot as B  # noqa: E402
import mechfight as mf  # noqa: E402

MAP_DIR = os.environ.get("MAP_DIR", "/arena")
KIT_DIR = os.environ.get("KIT_DIR", "/kit")
HAVE_MAP = os.path.exists(os.path.join(MAP_DIR, "v2", "world.json"))
STREAM = os.path.join(KIT_DIR, "check", "stream.jsonl.gz")

CONFIG = {
    "loot": {"pickup_radius": 4},
    "sound": {"step_speed": 5},
    "mech": {"walk_speed": 11.7, "gravity": 22},
    "cubes": {"size_per_cube": 0.05},
    "npc": {},
}
GUN_STATS = {"damage": 45, "cooldown": 0.24, "speed": 252, "range": 230, "spread_deg": 0.6, "mag": 12, "reload": 2.4}


def hello(map_dir="/nonexistent"):
    return {"type": "hello", "config": copy.deepcopy(CONFIG), "map": {"dir": map_dir}}


def gun(ammo=12, reload=0.0):
    return {"kind": "gun", "cooldown": 0, "ammo": ammo, "reload": reload, "stats": dict(GUN_STATS)}


def obs(pos=(0.0, 0.0, 200.0), aim=(0.0, 0.0), mechs=(), events=(), pickups=(), offer=None, weapons=None, t=10.0):
    return {
        "type": "obs", "tick": int(t * 20), "t": t, "alive": 16,
        "self": {"id": 0, "pos": list(pos), "vel": [0, 0, 0], "aim": list(aim), "hp": 1400, "max_hp": 1400,
                 "in_zone": True, "cubes": 0, "weapons": weapons or [gun(), None],
                 "stats": {"walk_speed": 11.7, "muzzle_height": 7, "height": 9}},
        "mechs": list(mechs), "projectiles": [], "pickups": list(pickups), "events": list(events),
        "sounds": [], "beams": [], "crates": [], "cubes": [], "drops": [], "offer": offer,
        # big zone centered on us: legs hold still, no zone hurry
        "zone": {"center": [0, 0], "radius": 2000, "next_center": [0, 0], "next_radius": 1500,
                 "shrink_start": 1000, "shrink_end": 1100, "dps": 20},
    }


def mech(id, pos, vel=(0.0, 0.0, 0.0), kind="mech"):
    return {"id": id, "kind": kind, "pos": list(pos), "vel": list(vel), "aim": [0, 0], "hp": 1400, "cubes": 0}


def settle(bot, o, n=40):
    """Feed the same situation n times, letting the torso reach the commanded aim each time."""
    act = None
    for _ in range(n):
        act = bot.act(o)
        o = copy.deepcopy(o)
        o["self"]["aim"] = list(act["aim"])
        o["events"] = []
    return act


class Ballistics(unittest.TestCase):
    def test_lead_meets_crossing_target(self):
        b = B.Bot(hello())
        tgt = mech(5, (120, 0, 200), vel=(0, 11.7, 0))
        act = settle(b, obs(mechs=[tgt]))
        yaw, pitch = act["aim"]
        self.assertGreater(yaw, 0.01, "should lead toward +y")
        # fly the bullet and the target; the bullet must pass through the target's center
        o = (0.0, 0.0, 207.0)
        d = (math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch))
        best = min(
            math.dist([o[k] + d[k] * (3 + 252 * (t - B.LATENCY_S)) for k in range(3)], (120, 11.7 * t, 204.5))
            for t in (B.LATENCY_S + i / 10000 for i in range(10000)))
        self.assertLess(best, 0.3)

    def test_stationary_target_aims_straight(self):
        b = B.Bot(hello())
        act = settle(b, obs(mechs=[mech(5, (0, 80, 200))]))
        self.assertAlmostEqual(act["aim"][0], math.pi / 2, places=6)
        self.assertEqual(act["fire"], [True, False])

    def test_mortar_lob_lands_on_target(self):
        b = B.Bot(hello())
        ms = {"speed": 72, "gravity": 22}
        sol = b.mortar_solution((0, 0, 209.5), mech(5, (150, 40, 200)), ms)
        yaw, pitch, d, q, ok = sol
        self.assertTrue(ok)
        tf = d / (72 * math.cos(pitch))
        land = (72 * math.cos(pitch) * math.cos(yaw) * tf, 72 * math.cos(pitch) * math.sin(yaw) * tf,
                209.5 + 72 * math.sin(pitch) * tf - 11 * tf * tf)
        self.assertLess(math.dist(land, (150, 40, 201)), 0.5)
        self.assertIsNone(b.mortar_solution((0, 0, 209.5), mech(5, (400, 0, 200)), ms), "out of range")


class Firing(unittest.TestCase):
    def test_no_fire_until_torso_is_on_target(self):
        b = B.Bot(hello())
        act = b.act(obs(aim=(math.pi, 0), mechs=[mech(5, (100, 0, 200))]))
        self.assertEqual(act["fire"], [False, False])
        self.assertEqual(settle(b, obs(mechs=[mech(5, (100, 0, 200))]))["fire"], [True, False])

    def test_no_fire_out_of_range(self):
        b = B.Bot(hello())
        self.assertEqual(settle(b, obs(mechs=[mech(5, (260, 0, 200))]))["fire"], [False, False])

    def test_no_fire_while_reloading(self):
        b = B.Bot(hello())
        o = obs(mechs=[mech(5, (60, 0, 200))], weapons=[gun(ammo=0, reload=1.5), None])
        self.assertEqual(settle(b, o)["fire"], [False, False])

    @unittest.skipUnless(HAVE_MAP, "needs MAP_DIR")
    def test_no_fire_through_a_building(self):
        b = B.Bot(hello(MAP_DIR))
        w = b.world
        # a tall building and a point inside it
        for minx, miny, maxx, maxy, base, top, edges in sorted(w.prisms, key=lambda p: p[4] - p[5]):
            cx, cy, zm = (minx + maxx) / 2, (miny + maxy) / 2, (base + top) / 2
            if top - base > 15 and w.blocked((cx, cy, zm - 1), (cx, cy, zm + 1)):
                break
        me, them = (cx - 70, cy, zm - 7), (cx + 70, cy, zm - 4.5)
        self.assertTrue(w.blocked((me[0], me[1], me[2] + 7), (them[0], them[1], them[2] + 4.5)))
        act = settle(b, obs(pos=me, mechs=[mech(5, them)]))
        self.assertEqual(act["fire"], [False, False])
        # same shot high above the roofs is clear
        act = settle(b, obs(pos=(me[0], me[1], 300), mechs=[mech(5, (them[0], them[1], 300))]))
        self.assertEqual(act["fire"], [True, False])


class Facing(unittest.TestCase):
    def test_turns_to_unseen_attacker(self):
        b = B.Bot(hello())
        hit = {"type": "damaged", "amount": 67.5, "weapon": "gun", "bearing": 2.4, "by": 7, "rear": True}
        act = b.act(obs(events=[hit]))
        self.assertAlmostEqual(act["aim"][0], 2.4)
        self.assertEqual(act["fire"], [False, False])
        # still facing it a moment later, forgotten after FACE_S
        self.assertAlmostEqual(b.act(obs(t=11.0))["aim"][0], 2.4)
        self.assertNotAlmostEqual(b.act(obs(t=10.0 + B.FACE_S + 0.5))["aim"][0], 2.4)

    def test_attacker_beats_nearest(self):
        b = B.Bot(hello())
        hit = {"type": "damaged", "amount": 45, "weapon": "gun", "bearing": -1.57, "by": 7}
        act = b.act(obs(events=[hit], mechs=[mech(3, (20, 0, 200)), mech(7, (0, -90, 200))]))
        self.assertAlmostEqual(act["aim"][0], -math.pi / 2, places=2)

    def test_zone_damage_is_not_an_attacker(self):
        b = B.Bot(hello())
        act = b.act(obs(events=[{"type": "damaged", "amount": 20, "weapon": "zone", "bearing": None, "by": None}]))
        self.assertEqual(b.hits, [])
        self.assertEqual(act["fire"], [False, False])


class Housekeeping(unittest.TestCase):
    def test_reload_partial_mag_when_nothing_in_view(self):
        b = B.Bot(hello())
        self.assertTrue(b.act(obs(weapons=[gun(ammo=0), None])).get("reload"))
        self.assertTrue(b.act(obs(weapons=[gun(ammo=5), None])).get("reload"), "top off a partly used mag")
        self.assertFalse(b.act(obs(weapons=[gun(ammo=12), None])).get("reload"), "mag already full")
        self.assertFalse(b.act(obs(weapons=[gun(ammo=5), None], mechs=[mech(5, (300, 0, 200))])).get("reload"))
        npc = mech(104, (30, 0, 200), kind="npc")
        hit = {"type": "damaged", "amount": 25, "weapon": "npc", "bearing": 0.0, "by": 104}
        self.assertFalse(b.act(obs(weapons=[gun(ammo=5), None], mechs=[npc], events=[hit])).get("reload"),
                         "not while fighting an NPC")
        self.assertFalse(b.act(obs(weapons=[gun(ammo=0, reload=2.0), None])).get("reload"), "already reloading")

    def test_takes_laser_or_mortar_within_4m(self):
        b = B.Bot(hello())
        def take(*pickups, weapons=None):
            return b.act(obs(pickups=[{"id": 8000 + i, "kind": k, "pos": p} for i, (k, p) in enumerate(pickups)],
                             weapons=weapons)).get("take")
        self.assertEqual(take(("mortar", [3, 0, 200])), 8000)
        self.assertEqual(take(("mortar", [1, 0, 200]), ("laser", [3.9, 0, 200])), 8001, "laser preferred")
        import features
        was, features.LIGHTNING = features.LIGHTNING, False  # v9 features.LIGHTNING takes it (test_v9fg.py)
        try:
            self.assertIsNone(take(("lightning", [1, 0, 200])))
        finally:
            features.LIGHTNING = was
        self.assertIsNone(take(("laser", [4.5, 0, 200])), "too far")
        self.assertIsNone(take(("laser", [1, 0, 205])), "too high")
        laser = {"kind": "laser", "cooldown": 0, "charge": 0, "stats": {"range": 280, "charge": 0.75}}
        self.assertIsNone(take(("mortar", [1, 0, 200]), weapons=[gun(), laser]), "no empty slot")

    def test_picks_armor(self):
        b = B.Bot(hello())
        ch = lambda *ids: {"id": 1, "expires": 15, "choices": [{"id": i, "name": i, "desc": ""} for i in ids]}
        self.assertEqual(b.act(obs(offer=ch("gun_rate", "sensors", "armor")))["pick"], 2)
        # v6 (features.ECONOMY): plating > armor > nanites; v5 took choice 0 here
        self.assertEqual(b.act(obs(offer=ch("gun_rate", "sensors", "plating")))["pick"], 2)
        self.assertEqual(b.act(obs(offer=ch("gun_rate", "sensors", "gyros")))["pick"], 0)
        import features
        was, features.ECONOMY = features.ECONOMY, False
        try:
            self.assertEqual(b.act(obs(offer=ch("gun_rate", "sensors", "plating")))["pick"], 0)
        finally:
            features.ECONOMY = was


class Laser(unittest.TestCase):
    def laser(self, charge, cooldown=0.0):
        return {"kind": "laser", "cooldown": cooldown, "charge": charge, "stats": {"range": 280, "charge": 0.75}}

    def test_charge_then_release_on_target(self):
        b = B.Bot(hello())
        tgt = [mech(5, (100, 0, 200))]
        self.assertEqual(b.act(obs(mechs=tgt, weapons=[gun(), self.laser(0)]))["fire"][1], True, "start charging")
        self.assertEqual(b.act(obs(mechs=tgt, weapons=[gun(), self.laser(0.4)]))["fire"][1], True, "keep charging")
        self.assertEqual(b.act(obs(mechs=tgt, weapons=[gun(), self.laser(0.75)]))["fire"][1], False, "release")
        off = obs(aim=(1.0, 0), mechs=tgt, weapons=[gun(), self.laser(0.75)])
        self.assertEqual(b.act(off)["fire"][1], True, "hold until on target")
        self.assertEqual(b.act(obs(mechs=tgt, weapons=[gun(), self.laser(0, cooldown=1)]))["fire"][1], False)


class SafetyNet(unittest.TestCase):
    def test_broken_observation_falls_back(self):
        b = B.Bot(hello())
        o = obs(mechs=[mech(5, (100, 0, 200))])
        del o["self"]["weapons"]
        act = b.act(o)
        self.assertEqual(act["fire"], [False, False])
        self.assertEqual(b.fallbacks, 1)
        mf.draw.clear()

    def test_bad_hello_map_still_plays(self):
        b = B.Bot(hello("/nonexistent"))
        self.assertIsNone(b.world)
        self.assertEqual(settle(b, obs(mechs=[mech(5, (50, 0, 200))]))["fire"], [True, False])


@unittest.skipUnless(HAVE_MAP and os.path.exists(STREAM), "needs MAP_DIR and KIT_DIR")
class RecordedMatch(unittest.TestCase):
    def test_whole_stream_without_fallbacks(self):
        with gzip.open(STREAM, "rt") as f:
            lines = [json.loads(l) for l in f]
        h = lines[0]
        h["map"]["dir"] = MAP_DIR
        b = B.Bot(h)
        self.assertIsNotNone(b.world)
        fired = 0
        for o in lines[1:]:
            if o.get("type") != "obs":
                continue
            act = b.act(o)
            mf.draw.clear()
            json.dumps(act, allow_nan=False)
            fired += any(act["fire"])
        self.assertEqual(b.fallbacks, 0)
        self.assertGreater(fired, 0)
        ms = sorted(b.times)[int(0.99 * len(b.times))] * 1000
        print(f"\n  recorded match: {len(b.times)} decisions, p99 {ms:.2f} ms, max {max(b.times) * 1000:.2f} ms, fired on {fired}")
        self.assertLess(ms, 20)


class WorldGeometry(unittest.TestCase):
    @unittest.skipUnless(HAVE_MAP, "needs MAP_DIR")
    def test_terrain_and_sky(self):
        from world import World
        w = World(MAP_DIR)
        g = w.ground(0, 0)
        self.assertTrue(-50 < g < 50)
        self.assertFalse(w.blocked((0, 0, 400), (100, 100, 400)))
        self.assertTrue(w.blocked((0, 0, g - 5), (60, 0, w.ground(60, 0) - 5)), "underground")


if __name__ == "__main__":
    unittest.main()
