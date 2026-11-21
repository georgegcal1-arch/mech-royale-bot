"""On/off switches for the v5, v6 and v7 features. All on = v7; HEAL off = v6 exactly; HEAL and ECONOMY
off = v5 exactly.

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
                   radius is HEAL_FINAL_R or less (heal.py); off = v6.
"""
STRAFE = True
DODGE = True
LASER_FETCH = True
CIRCULAR_AIM = True
LOWHP_TARGETING = True
ECONOMY = True
HEAL = True

# HEAL tuning
HEAL_ENTER_FRAC = 0.30  # disengage below this fraction of max HP...
HEAL_EXIT_FRAC = 0.70   # ...until back at this fraction
HEAL_FIGHT_M = 40.0     # a mech or NPC drone this close: fight back instead
HEAL_FINAL_R = 70.0     # zone radius at or below this (the last phases): never disengage
