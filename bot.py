"""Mech Royale bot v9: four experiments behind switches, all off by default (= v6 exactly).

v9 (features.DUEL / LEAD_AIM / LEARN_DODGE / SQUAD_AVOID; QUEUE.md turns them on one at a time):
  DUEL         1v1: weapon by range, legs circle toward the enemy's rear at our weapon's range (duel.py).
  LEAD_AIM     per-mech motion model picked by back-testing its track, pooled prior (lead.py).
  LEARN_DODGE  learn the shooters' lead fraction from their bullets; strafe/dodge to beat it (learn_dodge.py).
  SQUAD_AVOID  2+ mechs engaging: walk where at most one has a line; target the most isolated (squad.py).
  LIGHTNING    take lightning; laser + lightning (gun dropped) once both are ours; lightning owns close fights.
  LOOKAHEAD    under threat, simulate 0.8 s of 10 candidate moves; least expected damage wins (lookahead.py).
  LASER_GUARD  H: a laser charging at us: short it with lightning up close, else boost out of its line (guard.py).
  NO_CHIP      I: no gun shots beyond 100 m at a healthy mech; close in via cover instead (nochip.py).
  AMBUSH       J: one hunter closing: wait hidden and silent, spring with lightning inside 43 m (ambush.py).
  NPC_AWARE    N: shoot back at a drone hitting us (unless a mech is within 40 m), keep the fight's legs out of
               drones' aggro range, and farm drones for cubes when quiet (npc.py).
  STUCK_ESCAPE pushing into a wall for 0.3 s without moving: jet over it if its top is within climb range,
               else slide along it toward its nearer end (stuck.py); the last word on the legs.
  NAV_ROUTE    walking goals (the zone spot, cubes, crates, the laser fetch, I's approach) follow A* routes on the
               precomputed ground grid (nav.py, nav/ground.nav.gz from tools/navgrid.py).
  LOOT_SWEEP   L: no enemy within 40 m: walk (routed) to cubes and better weapons within 40 m, death piles first;
               abort on 2+ attackers or a laser charging at us (loot_sweep.py).
  Legs: zone hurry > dodge > hide > squad > laser fetch > cube > (I) approach > duel > strafe > zone walk;
  then LOOKAHEAD may replace the result, then (J) the ambush, then (H) the laser guard's cover (none of them
  in zone hurry).

v7: heal (disengage to regen).

v7 = v6 plus (features.HEAL; off = v6 exactly):
  Regen only starts after 5 s without dealing or taking damage. Below HEAL_ENTER_FRAC of max HP the bot
  stops firing (crates too), faces the nearest threat and walks to a hide spot nearby: out of the threats'
  sight behind buildings or under dense canopy, away from them, inside the next circle. It stays
  disengaged until HEAL_EXIT_FRAC, but fights back (v6, that decision) against anything within
  HEAL_FIGHT_M; zone hurry and dodging still come first; never once the zone radius is HEAL_FINAL_R or less.

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
  heal.py       v7: disengage at low HP so regen can start (state, threats, hide spot)
  duel.py, lead.py, learn_dodge.py, squad.py, lookahead.py, guard.py, nochip.py, ambush.py, npc.py, stuck.py, nav.py, loot_sweep.py
                v9 experiments (LIGHTNING: loot/combat/economy)
  features.py   on/off switches for the v5/v6/v7 features and the v9 experiments (defaults = v6)
"""
import atexit
import gc
import math
import random

import features
import mechfight as mf
from economy import CRATE_HEIGHT, CRATE_RADIUS, ECON_MAX_M, EconomyMixin  # noqa: F401
from heal import HealMixin
from combat import AIM_HEIGHTS, AIM_TOL, MORTAR_MIN_M, MORTAR_RANGE_ERR_M, SHOT_OFFSET_M, CombatMixin, aimed  # noqa: F401
from loot import (EARLY_DEFAULT_S, FETCH_ARRIVE_M, FETCH_MAX_M, FETCH_SAFE_M, TAKE_KINDS, TAKE_Z_M,  # noqa: F401
                  LootMixin)
from movement import DODGE_HORIZON_S, DODGE_M, STRAFE_LOOK_M, STRAFE_MAX_S, STRAFE_MIN_S, MovementMixin  # noqa: F401
from perception import (ATTACKER_WINDOW_S, LATENCY_S, MOTION_DT_MAX, MOTION_MIN_SPEED, OMEGA_ALPHA,  # noqa: F401
                        OMEGA_MAX, PerceptionMixin, _finite, _hp, _hp_frac, _wrap)
from safety import (ARRIVE_M, EDGE_MARGIN_M, LOOKAHEAD_S, SLOW_DECISION_MS, SLOW_FRAC, SPARE_S,  # noqa: F401
                    SPOT_FRAC, UPGRADE, UPGRADE_PREFS, SafetyMixin, pick_upgrade, zone_at)
from duel import DuelMixin
from lead import LeadMixin
from learn_dodge import LearnDodgeMixin
from lookahead import LookaheadMixin
from squad import SquadMixin
from guard import LaserGuardMixin
from nochip import NoChipMixin
from ambush import AmbushMixin
from npc import NPC_AVOID_MODES, NpcMixin
from stuck import StuckMixin, new_history
from nav import NavGrid, NavMixin
from loot_sweep import LootSweepMixin
from threat import ThreatMixin
from laser_counter import LC_SLOW_M, LC_STOP_M, ExposureMap, LaserCounterMixin
from targeting import FACE_S, FINISH_FRAC, HIT_RECENT_S, MAX_CANDIDATES, STICKY_HP, TargetingMixin  # noqa: F401


class Bot(SafetyMixin, PerceptionMixin, TargetingMixin, CombatMixin, MovementMixin, LootMixin, EconomyMixin,
          HealMixin, DuelMixin, LeadMixin, LearnDodgeMixin, SquadMixin, LookaheadMixin,
          LaserGuardMixin, NoChipMixin, AmbushMixin, NpcMixin, StuckMixin, NavMixin, LootSweepMixin,
          LaserCounterMixin, ThreatMixin):
    name = "mech-royale-bot v9"

    def __init__(self, hello: mf.Hello):
        cfg = hello["config"]
        self.reach = cfg["loot"]["pickup_radius"]
        self.step_speed = cfg["sound"]["step_speed"]
        self.walk_speed = cfg["mech"].get("walk_speed", 11.7)
        self.gravity = cfg["mech"].get("gravity", 22.0)
        self.size_per_cube = cfg.get("cubes", {}).get("size_per_cube", 0.05)
        self.npc_radius = cfg.get("npc", {}).get("radius", 1.8)
        self.npc_height = cfg.get("npc", {}).get("height", 3.5)
        self.npc_damage = cfg.get("npc", {}).get("damage", 25.0)  # v9 lookahead
        self.wcfg = cfg.get("weapons", {})                       # v9 lookahead
        self.decision_t0 = None
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
        self.healing = False  # v7: disengaged until HP is back up (heal.py)
        self.hide = None      # v7: (re-plan time, threat key, hide goal or None)
        self.tracks, self.lead_err, self.lead_pool, self.lead_state = {}, {}, [[], 0], {}  # v9 LEAD_AIM
        self.bullets_seen, self.k_pool, self.k_by = {}, [0.0, 0], {}  # v9 LEARN_DODGE
        self.engaging, self.squad = [], None  # v9 SQUAD_AVOID
        self.guard = None  # v9 LASER_GUARD: (re-plan time, chargers key, cover goal)
        self.approach = None  # v9 NO_CHIP: (re-plan time, target id, goal)
        self.ambush, self.ambush_cool = None, 0.0  # v9 AMBUSH
        self.stuck_hist, self.escape, self.last_stuck = new_history(), None, None  # v9 STUCK_ESCAPE
        self.nav, self.navgrid, self.nav_tick = None, None, None  # v9 NAV_ROUTE: the ground grid (nav.py)
        self.last_pos, self.kill_spots, self.sweep_cool = {}, [], 0.0  # v9 LOOT_SWEEP
        self.exposure, self.laser_fired, self.lc = None, {}, None  # v9 LASER_COUNTER
        if self.world is not None:
            try:
                self.exposure = ExposureMap.load(hello["map"]["dir"])
            except Exception as e:
                mf.log(f"exposure map not loaded: {e!r}")
        if self.world is not None:
            try:
                self.navgrid = NavGrid.load(hello["map"]["dir"])
            except Exception as e:
                mf.log(f"nav grid not loaded: {e!r}")
            if self.navgrid is None:
                mf.log("nav grid missing or for another map: straight-line legs")
        self.npc_aggro_m = float(cfg.get("npc", {}).get("aggro_range", 45.0))  # v9 NPC_AWARE
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
        if features.LEARN_DODGE:
            self.note_bullets(obs)
        enemies = sorted((m for m in obs["mechs"] if m["kind"] == "mech"), key=lambda m: math.dist(m["pos"], me["pos"]))
        if features.SQUAD_AVOID:
            self.engaging = self.engagers(obs, enemies)
        target, face, why = self.choose_target(obs, enemies)
        if features.NPC_AWARE:  # v9 N (npc.py): shoot back at a drone that is hitting us
            npc = self.npc_defense(obs, enemies)
            if npc is not None:
                target, face, why = npc, None, "npc"
        guard = self.laser_guard(obs, enemies) if features.LASER_GUARD else None  # v9 H (guard.py)
        if guard is not None and guard[0] == "short":
            target, face, why = guard[1], None, "short"

        if self.heal_state(obs):  # v7: always False with features.HEAL off
            threats = self.threats(obs)
            hide = self.hide_spot(obs, threats) if threats else None
            move, yaw, boost, goal, legs, mode = self.legs(obs, enemies, None, None, hide)
            act.update(move=move, aim=[yaw, 0.0], boost=boost, jump=obs["tick"] % 40 == 0, fire=[False, False])
            aim = self.heal_aim(obs, threats, face)
            if aim is not None:
                act["aim"] = aim
            self.reload_if_idle(act, me["weapons"], [], None)  # no shots now: top up the magazine
            if goal is not None and legs is None:
                mf.draw.point([goal[0], goal[1], z], "#40ff80", 6)
            mf.draw.label(f"heal/{mode} · hp {me['hp']:.0f} · {len(enemies)} in sight · {len(threats)} threats")
            return act

        quiet = self.quiet(obs, enemies, target, face)  # always False with features.ECONOMY off

        farm = self.npc_farm(obs) if features.NPC_AWARE and quiet else None  # v9 N: drones for cubes
        cube = (farm[1] if farm is not None else self.cube_goal(obs)) if quiet else None
        sweep = None
        if features.LOOT_SWEEP:  # v9 L (loot_sweep.py): cubes / better weapons nearby, death piles first
            self.note_kills(obs)
            sweep = self.sweep_goal(obs, enemies)
            if sweep is not None:
                cube = sweep
        move, yaw, boost, goal, legs, mode = self.legs(obs, enemies, target, cube)
        if sweep is not None and mode == "cube":
            mode = "loot"
        if (features.NPC_AWARE and target is not None and target.get("kind") == "mech"
                and mode in NPC_AVOID_MODES):  # v9 N: fighting a mech, stay out of drones' aggro range
            turned = self.npc_avoid(obs, move, self.npc_aggro_m)
            if turned is not move:
                move, mode = turned, mode + "/npc"
                legs = turned if legs is not None else None
                yaw = math.atan2(turned[1], turned[0])
        covering = guard is not None and guard[0] == "cover" and guard[1] is not None  # v9 H takes the legs
        if features.LOOKAHEAD and mode != "zone!" and not covering:  # v9 (lookahead.py): least expected damage under threat
            look = self.lookahead(obs, enemies, move, boost)
            if look is not None:
                move, boost, legs, mode = look, False, look, "look"
                yaw = math.atan2(look[1], look[0])
        amb = None
        if (features.AMBUSH or features.AGGRO_HOLD) and mode != "zone!" and guard is None:  # v9 J / B3 (ambush.py)
            amb = self.ambush_step(obs, enemies, mode)
            if amb is not None:
                goal, legs, boost, mode = amb["goal"], None, False, "ambush"
                if goal is None:
                    move = [0.0, 0.0]
                else:
                    yaw = math.atan2(goal[1] - y, goal[0] - x)
                    move = [math.cos(yaw), math.sin(yaw)]
        lcm = None
        if features.LASER_COUNTER:  # v9 (laser_counter.py): hide on the laser's clock, peek / close in its window
            self.note_lasers(obs)
            lcm = self.laser_counter(obs, enemies, mode)
            if lcm is not None:
                goal, boost, mode = lcm
                if features.NAV_ROUTE:
                    goal = self.nav_waypoint(obs, goal)
                legs = None
                yaw = math.atan2(goal[1] - y, goal[0] - x)
                gd = math.hypot(goal[0] - x, goal[1] - y)
                if mode != "lc/close" and gd <= LC_STOP_M:  # at the anchor: stand (a boosting overshoot exposes us)
                    move, boost = [0.0, 0.0], False
                else:
                    move = [math.cos(yaw), math.sin(yaw)]
                    if mode != "lc/close" and gd <= LC_SLOW_M:
                        boost = False
        if covering and mode != "zone!" and lcm is None:  # v9 H: boost out of the charging laser's line
            goal, legs, boost, mode = guard[1], None, True, "guard"
            yaw = math.atan2(goal[1] - y, goal[0] - x)
            move = [math.cos(yaw), math.sin(yaw)]
        esc = self.stuck_step(obs, move, mode) if features.STUCK_ESCAPE else None  # v9 (stuck.py): past a wall
        if esc is not None:
            self.nav_reset()  # v9 NAV_ROUTE: re-plan from wherever the escape takes us
            move, boost, esc_jump, mode = esc
            legs, goal = None, None
            yaw = math.atan2(move[1], move[0])
        act.update(move=move, aim=[yaw, 0.0], boost=boost, jump=obs["tick"] % 40 == 0, fire=[False, False])
        if mode == "guard" or mode.startswith("lc/"):
            # sprinting to cover: no hop, and boost only on the ground (boost in the air is a jet, protocol.md
            # section 7: it lifts us into the open sky instead of behind the wall)
            act["jump"] = False
            act["boost"] = bool(act["boost"] and me.get("grounded", True))
        if esc is not None:
            act["jump"] = esc_jump

        # torso
        if amb is not None:  # v9 J: on the hunter, no shots, the gun topped up
            act["aim"] = self.ambush_aim(obs, amb["look"])
            why = f"ambush {self.ambush['hunter']}"
            target, quiet = None, False
        elif target is not None and why == "short":  # v9 H: lightning shorts the charging laser
            aim, fire = self.engage(obs, target, mode == "zone!", lightning=True)
            act.update(aim=aim, fire=fire)
        elif target is not None:
            if features.DUEL and self.duel_on(enemies, target):  # v9 (duel.py)
                aim, fire = self.duel_engage(obs, target, mode == "zone!")
            else:
                aim, fire = self.engage(obs, target, mode == "zone!", lightning=features.LIGHTNING)
            act.update(aim=aim, fire=fire)
        elif face is not None:
            act["aim"] = [face, 0.0]
        crate = self.crate_shot(obs) if quiet and farm is None else None
        if farm is not None:  # v9 N: the drone first (it would shoot us while we shoot a crate)
            aim, fire = self.engage(obs, farm[0], False, mortar=False, lightning=True)
            act.update(aim=aim, fire=fire)
            why = f"farm {farm[0]['id']}"
        elif crate is not None:
            c, aim, fire = crate
            act.update(aim=aim, fire=fire)
            why = f"crate {c['id']}"
            mf.draw.point(list(c["pos"]), "#ffd040", 6)
            gun = next((w for w in me["weapons"] if w and w["kind"] == "gun"), None)
            if gun and gun.get("ammo", 0) <= 0 and (gun.get("reload") or 0) <= 0:
                act["reload"] = True  # only on an empty mag: don't break off the volley
        else:
            self.reload_if_idle(act, me["weapons"], [] if amb is not None else enemies, target)

        if self.heal_quiet(obs, enemies):  # v9 B4 (heal.py): no shots, so regen's timer runs
            charging = {i for i, w in enumerate(me["weapons"]) if w and w["kind"] == "laser" and (w.get("charge") or 0) > 0}
            act["fire"] = [bool(f) and i in charging for i, f in enumerate(act.get("fire") or [False, False])]
            why = f"quiet {why}".strip()  # (a laser already charging keeps its trigger: letting go would fire it)
        if goal is not None and legs is None:
            mf.draw.point([goal[0], goal[1], z], "#40c0ff", 6)
        tag = f"{why} {target['id']}" if target is not None else why
        routed = " · route" if self.nav_tick == obs["tick"] else ""  # v9 NAV_ROUTE: for tools/analyze.py
        mf.draw.label(f"{mode} · hp {me['hp']:.0f} · {len(enemies)} in sight · {tag}{routed}")
        return act


if __name__ == "__main__":
    mf.run(Bot)
