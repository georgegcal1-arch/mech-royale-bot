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
LOOKAHEAD = True  # exp G under test, on top of DUEL + LIGHTNING (QUEUE.md)
V9_SWITCHES = ("DUEL", "LEAD_AIM", "LEARN_DODGE", "SQUAD_AVOID", "LIGHTNING", "LOOKAHEAD")

# HEAL tuning
HEAL_ENTER_FRAC = 0.30  # disengage below this fraction of max HP...
HEAL_EXIT_FRAC = 0.70   # ...until back at this fraction
HEAL_FIGHT_M = 40.0     # a mech or NPC drone this close: fight back instead
HEAL_FINAL_R = 70.0     # zone radius at or below this (the last phases): never disengage
