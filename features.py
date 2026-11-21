"""On/off switches for the v5 and v6 features. All on = v6; ECONOMY off = v5 exactly.

They are read at decision time (features.X), so a test or an experiment can flip them on the module.
  STRAFE           legs strafe perpendicular to a target in reach (movement.py); off = v2 zone walk.
  DODGE            step sideways off the path of an incoming gun/NPC bullet (movement.py).
  LASER_FETCH      walk to a visible laser pickup early in the match (movement.py / loot.py).
  CIRCULAR_AIM     predictions follow each mech's estimated turn rate (perception.py); off = straight line.
  LOWHP_TARGETING  target the lowest-HP clear mech, an attacker taking over unless the target is nearly
                   dead (targeting.py); off = v3's order (visible attacker, hit bearing, nearest).
  ECONOMY          v6: when quiet, shoot crates and walk to cubes nearby inside the next circle (economy.py),
                  and pick upgrades plating > armor > nanites (safety.py); off = v5 (armor, else choice 0).
"""
STRAFE = True
DODGE = True
LASER_FETCH = True
CIRCULAR_AIM = True
LOWHP_TARGETING = True
ECONOMY = True
