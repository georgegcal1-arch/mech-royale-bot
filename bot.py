"""Mech Royale bot v6: the cube economy.

v6 = v5 plus (features.ECONOMY; off = v5 exactly):
  1. Quiet moments (no target, no hit to face, no enemy mech in sight, no mech-like sound within ~160 m
     in the last HEARD_S): the gun shoots the nearest crate within ECON_MAX_M that has a clear line,
     lies inside the next zone circle and isn't on a roof; the legs walk to such a cube (after a laser
     fetch, before strafing). Nothing near a visible NPC drone (NPC_AVOID_M).
  2. Upgrades: plating, else armor, else nanites, else choice 0 (answered on the first decision that
     sees the offer, well inside its 5 s timeout).

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

Layout (behavior identical to the single-file v5):
  bot.py        this coordinator: Bot.__init__ (config, state) and Bot.decide (one decision)
  safety.py     safety net (Bot.act, validation, fallback, timing) and zone awareness (v2)
  perception.py damage memory, motion model (circular), sizes, predictions, line-of-shot
  targeting.py  target choice (lowest HP, attacker, v3 fallbacks)
  combat.py     the torso: lead, gun/laser/mortar, reload when idle
  movement.py   the legs: dodge, strafe, priority order
  loot.py       taking weapons in reach, the early laser fetch
  economy.py    v6: crates and cubes when quiet
  features.py   on/off switches for the v5/v6 features (all on = v6, ECONOMY off = v5)
"""
import atexit
import gc
import math
import random

import mechfight as mf
from economy import CRATE_HEIGHT, CRATE_RADIUS, ECON_MAX_M, EconomyMixin  # noqa: F401
from combat import AIM_HEIGHTS, AIM_TOL, MORTAR_MIN_M, MORTAR_RANGE_ERR_M, SHOT_OFFSET_M, CombatMixin, aimed  # noqa: F401
from loot import (EARLY_DEFAULT_S, FETCH_ARRIVE_M, FETCH_MAX_M, FETCH_SAFE_M, TAKE_KINDS, TAKE_Z_M,  # noqa: F401
                  LootMixin)
from movement import DODGE_HORIZON_S, DODGE_M, STRAFE_LOOK_M, STRAFE_MAX_S, STRAFE_MIN_S, MovementMixin  # noqa: F401
from perception import (ATTACKER_WINDOW_S, LATENCY_S, MOTION_DT_MAX, MOTION_MIN_SPEED, OMEGA_ALPHA,  # noqa: F401
                        OMEGA_MAX, PerceptionMixin, _finite, _hp, _hp_frac, _wrap)
from safety import (ARRIVE_M, EDGE_MARGIN_M, LOOKAHEAD_S, SLOW_DECISION_MS, SLOW_FRAC, SPARE_S,  # noqa: F401
                    SPOT_FRAC, UPGRADE, UPGRADE_PREFS, SafetyMixin, pick_upgrade, zone_at)
from targeting import FACE_S, FINISH_FRAC, HIT_RECENT_S, MAX_CANDIDATES, STICKY_HP, TargetingMixin  # noqa: F401


class Bot(SafetyMixin, PerceptionMixin, TargetingMixin, CombatMixin, MovementMixin, LootMixin, EconomyMixin):
    name = "mech-royale-bot v6"

    def __init__(self, hello: mf.Hello):
        cfg = hello["config"]
        self.reach = cfg["loot"]["pickup_radius"]
        self.step_speed = cfg["sound"]["step_speed"]
        self.walk_speed = cfg["mech"].get("walk_speed", 11.7)
        self.gravity = cfg["mech"].get("gravity", 22.0)
        self.size_per_cube = cfg.get("cubes", {}).get("size_per_cube", 0.05)
        self.npc_radius = cfg.get("npc", {}).get("radius", 1.8)
        self.npc_height = cfg.get("npc", {}).get("height", 3.5)
        self.crate_radius = cfg.get("crates", {}).get("radius", CRATE_RADIUS)
        self.crate_height = cfg.get("crates", {}).get("height", CRATE_HEIGHT)
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

    def decide(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        act: mf.Action = {"tick": obs["tick"]}
        if obs["offer"]:
            act["pick"] = pick_upgrade(obs["offer"])
        self.take_loot(obs, act)

        self.note_damage(obs)
        self.note_motion(obs)
        enemies = sorted((m for m in obs["mechs"] if m["kind"] == "mech"), key=lambda m: math.dist(m["pos"], me["pos"]))
        target, face, why = self.choose_target(obs, enemies)
        quiet = self.quiet(obs, enemies, target, face)  # always False with features.ECONOMY off

        move, yaw, boost, goal, legs, mode = self.legs(obs, enemies, target, self.cube_goal(obs) if quiet else None)
        act.update(move=move, aim=[yaw, 0.0], boost=boost, jump=obs["tick"] % 40 == 0, fire=[False, False])

        # torso
        if target is not None:
            aim, fire = self.engage(obs, target, mode == "zone!")
            act.update(aim=aim, fire=fire)
        elif face is not None:
            act["aim"] = [face, 0.0]
        crate = self.crate_shot(obs) if quiet else None
        if crate is not None:
            c, aim, fire = crate
            act.update(aim=aim, fire=fire)
            why = f"crate {c['id']}"
            mf.draw.point(list(c["pos"]), "#ffd040", 6)
            gun = next((w for w in me["weapons"] if w and w["kind"] == "gun"), None)
            if gun and gun.get("ammo", 0) <= 0 and (gun.get("reload") or 0) <= 0:
                act["reload"] = True  # only on an empty mag: don't break off the volley
        else:
            self.reload_if_idle(act, me["weapons"], enemies, target)

        if goal is not None and legs is None:
            mf.draw.point([goal[0], goal[1], z], "#40c0ff", 6)
        tag = f"{why} {target['id']}" if target is not None else why
        mf.draw.label(f"{mode} · hp {me['hp']:.0f} · {len(enemies)} in sight · {tag}")
        return act


if __name__ == "__main__":
    mf.run(Bot)
