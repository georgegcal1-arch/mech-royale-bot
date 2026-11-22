"""On/off switches for the v5, v6 and v7 features. The defaults = v6 exactly (HEAL off: v7 scored 2.67 and v7b 2.81 against
v6's 2.92). All on = v7b; HEAL and ECONOMY off = v5 exactly.

They are read at decision time (features.X), so a test or an experiment can flip them on the module.
  STRAFE           legs strafe perpendicular to a target in reach (movement.py); off = v2 zone walk.
  DODGE            step sideways off the path of an incoming gun/NPC bullet (movement.py).
  LASER_FETCH      walk to a visible laser pickup early in the match (movement.py / loot.py).
  CIRCULAR_AIM     predictions follow each mech's estimated turn rate (perception.py); off = straight line.
  LOWHP_TARGETING  target the lowest-HP clear mech, an attacker taking over unless the target is nearly
                   dead (targeting.py); off = v3's order (visible attacker, hit bearing, nearest).
  ECONOMY          v6: when quiet, shoot crates and walk to cubes nearby inside the next circle (economy.py),
                  and pick upgrades plating > armor > nanites (safety.py); off = v5 (armor, else choice 0).
  HEAL             v7: below HEAL_ENTER_FRAC of max HP, stop firing and hide from known enemies inside the
                   next circle until HEAL_EXIT_FRAC, fighting back within HEAL_FIGHT_M; never once the zone
                   radius is HEAL_FINAL_R or less (heal.py); off = v6. Off by default.

v9 experiments (all off by default = v6 exactly; QUEUE.md turns them on one at a time):
  DUEL         exactly one enemy mech, our target, in reach: weapon by range (lightning <= its range, the
               mortar only on a slow target) and the legs circle it toward its rear while holding a range
               (duel.py); off = v6's strafe and engage.
  LEAD_AIM     predictions pick, per mech, the motion model (straight, circular, mean velocity, standing)
               that best back-tests on its recent track, pooled over all mechs as a prior (lead.py);
               off = CIRCULAR_AIM's arc.
  LEARN_DODGE  learn how far enemy shooters lead us (0 = at us, 1 = fully led) from their bullets, pooled
               (house bots share code): leaders -> short strafe legs and dodge against our motion; direct
               aimers -> long strafe legs and dodge with it (learn_dodge.py); off = v5's dodge and strafe.
  SQUAD_AVOID  two or more enemy mechs engaging us: walk to where at most one has a line of fire, and
               target the most isolated one (squad.py); off = v6.
  LIGHTNING    take lightning guns (laser > lightning > mortar; drop the gun only once laser and lightning
               are both ours), fire lightning inside its range without starting laser charges there, and
               shoot crates with the laser or lightning when the gun is gone (loot.py, combat.py, economy.py).
  LOOKAHEAD    under threat, simulate ~0.8 s of 10 candidate moves against bullets/shells in flight and
               predicted enemy shots; take the least expected damage if it saves LOOK_MIN_GAIN (lookahead.py).
  LASER_GUARD  H: an enemy with line of sight charging a laser at us (its `charging` flag aimed at us, or a
               fresh laser_charge sound): within lightning range with a lightning gun ready, hit it (a short
               stops its laser 2 s); else boost to the nearest spot it can't see, reachable in 0.75 s (guard.py).
  NO_CHIP      I: the gun holds fire at a mech beyond NO_CHIP_M unless it is below NO_CHIP_FINISH of its HP
               (chip shots block our regen and trade at range), and the legs close in via cover instead of
               strafing at range (nochip.py; not while a laser without lightning wants the range).
  AMBUSH       J: holding lightning, one hunter closing from 50-180 m, the zone not pressing: walk to a spot
               within 40 m that it can't see, near its path to us, stand still and silent with fire held,
               and spring (the normal lightning fight) when it comes within 43 m; abort on damage, a second
               enemy, the zone, or a timeout (ambush.py).
  NPC_AWARE    N: a drone within 50 m that hit us in the last 2.5 s becomes the target (lightning if held) unless
               a mech is within 40 m; while fighting a mech the legs turn away from drones' aggro range; when
               quiet (economy's rule) and at half HP or more, farm drones within 60 m (npc.py).
  STUCK_ESCAPE the legs ask for 0.5+ of full speed for 0.5 s (not a fight's circling) but we moved < 1 m:
               jump + jet over the wall ahead if its top is within climb range (1.6 energy/m), else slide along
               it toward the side where it ends sooner; a repeat at the same spot tries the other way (stuck.py).
  NAV_ROUTE    walking goals (zone spot, cubes, crates, laser fetch, I's approach) follow weighted-A* routes on
               the precomputed 2 m ground grid (nav.py; nav/ground.nav.gz from tools/navgrid.py), planned within
               2 ms per decision; no grid for this map = straight lines as before.
  LOOT_SWEEP   L: with no enemy mech within 40 m, walk (routed) to cubes and the weapons loot.py would take
               within 40 m, death-drop piles first (and to a fresh kill spot before the pile is in view); abort
               for 4 s on hits from 2+ mechs or a laser charging at us (loot_sweep.py).
  Brackets (each submitted alone on top of the best):
  UPGRADES_OFFENSE  B1: upgrade offers take gun_damage > gun_rate > laser_damage > lightning_range >
               lightning_efficiency > lightning_chain > laser_charge first, then v6's plating > armor > nanites.
  AGGRO_CLOSE  B2: approach any mech target to under 40 m (routed, cover-scored, whatever we hold, weak ones too)
               and hold the fight at 30 m with lightning, else 35 m (nochip.py, duel.py).
  AGGRO_HOLD   B3: a hunter closing in (up to 2 mechs in sight, any weapons): take the best nearby spot it can't
               see and let it come, fighting within 43 m (ambush.py without its lightning requirement).
  HEAL_QUIET   B4: below 80% HP with no mech engaging us (no hit in 3 s, none aimed at us in reach, none within
               45 m), hold all fire so regen's 5 s timer runs; the legs carry on (loot, zone) (heal.py).
  AGGRO_RUSH   B2b: closer still than B2: approach the target (the nearest hunter once seen) to under 32 m,
               boosting while it is over 60 m away (with 35+ energy), hold 25 m with lightning / 30 m without, and
               prefer lightning pickups (take lightning > laser > mortar; loot_sweep counts lightning closer).
  AGGRO_CLOSER B2c, on top of B2b: closer still: approach to under 27 m, hold 20 m with lightning / 25 m without,
               and boost the approach at any range whenever energy > 25 (duel.py, nochip.py).
  LASER_COUNTER  a visible enemy laser with a line to us: keep a cover anchor from the exposure map
               (nav/exposure.nav.gz, tools/exposure.py) within 8 m, boost to it when the laser charges, and in the
               1.5 s after its beam peek and shoot (or, with lightning, close in); with lightning the anchor is the
               next cover closer to it (laser_counter.py).
  NO_MORTAR    B5: never carry a mortar (an enemy laser does +60% to a mortar carrier, protocol.md cook-off):
               never take one, drop one we hold, and loot_sweep never walks to one (loot.py, loot_sweep.py).
"""
STRAFE = True
DODGE = True
LASER_FETCH = True
CIRCULAR_AIM = True
LOWHP_TARGETING = True
ECONOMY = True
HEAL = False  # v7/v7b scored below v6: kept for experiments, off = v6 exactly

# v9 experiments: all off = v6 exactly
DUEL = True  # winner: exp A 3.21 (QUEUE.md)
LEAD_AIM = False
LEARN_DODGE = False  # exp C 3.46: no gain over 3.46 (QUEUE.md)
SQUAD_AVOID = False
LIGHTNING = True  # winner: exp F 3.46 (QUEUE.md)
LOOKAHEAD = False  # exp G 3.23: below the 3.46 bar (QUEUE.md)
LASER_GUARD = True  # kept: exp HI 3.52 (laser discipline, with NO_CHIP; QUEUE.md)
NO_CHIP = True  # kept: exp HI 3.52 (QUEUE.md)
AMBUSH = False  # exp J (next, on top of whatever wins)
NPC_AWARE = True  # kept: exp N 3.52 (no effect yet; on by the user's call, QUEUE.md)
STUCK_ESCAPE = True  # kept: exp S 3.56 (QUEUE.md); the safety net for routing
NAV_ROUTE = True  # kept: exp R (scored with L: 3.92, QUEUE.md)
LOOT_SWEEP = True  # winner: exp L 3.92 (with R, QUEUE.md)
UPGRADES_OFFENSE = False  # bracket B1 3.81: below 3.92, off (QUEUE.md)
AGGRO_CLOSE = True  # winner: bracket B2 4.50 (QUEUE.md)
AGGRO_HOLD = False  # bracket B3: skipped (the opposite of the winner B2)
HEAL_QUIET = True  # bracket B4: 4.54, kept on (QUEUE.md)
NO_MORTAR = True  # kept: bracket B5 4.06 (QUEUE.md)
AGGRO_RUSH = True  # winner: bracket B2b 4.69 (QUEUE.md)
LASER_COUNTER = True  # step LC under test, on top of L + B5 + B2 + B4 + B2b (QUEUE.md)
AGGRO_CLOSER = False  # bracket B2c (branch v9-b2c): after LC's score
V9_SWITCHES = ("DUEL", "LEAD_AIM", "LEARN_DODGE", "SQUAD_AVOID", "LIGHTNING", "LOOKAHEAD",
               "LASER_GUARD", "NO_CHIP", "AMBUSH", "NPC_AWARE", "STUCK_ESCAPE", "NAV_ROUTE", "LOOT_SWEEP",
               "UPGRADES_OFFENSE", "AGGRO_CLOSE", "AGGRO_HOLD", "HEAL_QUIET",
               "NO_MORTAR", "AGGRO_RUSH", "LASER_COUNTER", "AGGRO_CLOSER")

# HEAL tuning
HEAL_ENTER_FRAC = 0.30  # disengage below this fraction of max HP...
HEAL_EXIT_FRAC = 0.70   # ...until back at this fraction
HEAL_FIGHT_M = 40.0     # a mech or NPC drone this close: fight back instead
HEAL_FINAL_R = 70.0     # zone radius at or below this (the last phases): never disengage
