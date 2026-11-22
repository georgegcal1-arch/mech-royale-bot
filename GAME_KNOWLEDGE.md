# Mech Royale: game knowledge

Everything we know about the game, with its source. "§N" always means protocol.md §N; this file's own parts are "section N". Read this before designing a behaviour. `CLAUDE.md`
holds the short version; `QUEUE.md` and `scores.txt` hold the experiments.

**Sources and trust order:**
1. Live `hello.config` (the bot must read it every match).
2. `mechfight-kit/protocol.md` (cited as §N).
3. Our measurements (cited with the tool or test).
4. The track page and the browser game (user-reported, not in the kit).

The browser game differs in places (its zone runs 240 s). For practice, trust protocol.md and
`hello.config`. Numbers marked *(config)* are from the `hello.config` recorded in
`mechfight-kit/check/stream.jsonl.gz`, which equals protocol.md's defaults.

## 1. Scoring and practice opponents

- Place is the order of death: the last mech alive is 1st and the first to die is 16th *(§4)*.
- Mechs that die on the same tick share a place *(§4)*.
- At the 8:00 time limit, survivors are ranked by HP *(§4, §9)*.
- Each match scores `16 - place`. The score is the mean over the matches played. Surviving matters more than
  kills, though kills give cubes *(§4)*.
- Each submission is built, checked with `check.py`, and played in **48 practice matches** against built-in
  bots *(§1 "Submitting")*.
- **Practice opponents:** 15 house bots, 4 easy, 4 medium, 4 hard and 3 expert *(track page)*.
- **They act as one squad:** they share vision, never fight each other, and hunt us weakest first. One comes
  early, more as we thin them out, and all of them late *(track page)*.
- **So score ≈ kills before death.** Every kill pushes our death one place later. Not being found early and
  winning the first 1v1s is what pays.

## 2. Match lifecycle and timing

- Lockstep: the server sends all 16 observations at once, waits for every answer or the 50 ms budget, then
  simulates 2 ticks (20 ticks/s, one decision per 0.1 s) *(§2; config `match.tick_hz` 20, `decide_every` 2)*.
- `ready` must come within 20 s, which covers container start *(§2; config `bot.hello_budget_ms`)*.
- Every action echoes its observation's `tick`; any other tick is dropped. When observations queue up,
  answer only the newest *(§2)*.
- **A late or missing answer is skipped.** The mech keeps `move`/`boost`/`aim` and drops
  `fire`/`jump`/`reload`/`pick`/`take`/`drop` for `coast_s` 1 s, then idles: it stands still with its aim
  held *(§2)*.
- A crashed process idles for the rest of the match *(§2)*.
- After death there are no more observations, only `end` with `place` and `kills` *(§2)*.
- Our own target is < 10 ms per decision, against the 50 ms budget. `check.py` fails a pass when more than
  2% of answers are late, and retries up to 3 passes *(§1)*.

## 3. Runtime sandbox and submission

- **Sandbox:** Docker as uid 65534 with no capabilities *(§3)*.
  - No network.
  - Read-only filesystem; only `/tmp` is writable (64 MB).
  - 1 CPU and 512 MB RAM, no swap.
  - At most 128 processes and threads.
  - The map is mounted read-only at `/arena`.
- **I/O:** JSON lines of at most 64 KiB on stdin/stdout, flushed every line. Logs go to stderr only;
  anything else on stdout fails the check *(§1, §3)*.
- **Submission:** a public GitHub repo with the `Dockerfile` at its top level, **≤ 20 MB at the submitted
  commit**. It is built on linux/amd64 with the network on and must build within 10 min. The scorer runs
  `check.py` in the match sandbox before playing *(§1)*.

## 4. Coordinates and dimensions

- x = east, y = north, z = up, in metres, seconds and radians *(§5)*.
- Yaw counts counter-clockwise from east (π/2 = north). Pitch is up-positive *(§5)*.
- A mech is a cylinder of radius 2.5 m and height 9 m; `pos` is its feet *(§5)*.
  - Centre at +4.5, eyes at +8, gun muzzle at +7, mortar tube at +9.5.
  - Shots leave 3 m out along the aim.
  - Cubes scale all of these (section 11 below).
- NPC drones are radius 1.8 m, height 3.5 m *(§5)*.

## 5. Movement and energy

- **Speed:** walk 11.7 m/s, boost 19.5 m/s. Acceleration is 58.5 m/s² on the ground and 12 in the air, so
  reversals take time *(§7 Movement; config)*.
- **Turning:** the torso turns at 4 rad/s and pitches at 3 rad/s, between −0.7 and 1.3 rad *(config)*.
- **Climbing:** ledges up to 1.2 m are walked up; anything taller is a wall. Slopes over 50° can't be walked
  *(§7 Movement)*.
- **Jets:** jump at 11 m/s, then boost in the air. That jets at `jet_accel` 34 against gravity 22, rising at
  most 10 m/s and draining 15 energy/s. Boosting on the ground drains 17/s *(config)*.
- **Climb range:** 100 energy climbs about 60 m, enough for almost every campus roof; the 70 m towers are out
  of reach *(§7 Movement)*. Our rooftop code budgets about 1.6 energy per metre (`rooftop.py`).
- **Energy regen:** 18/s after 0.6 s without spending *(config)*.
- **Roof edges:** coming down past a roof you still overhang, you land on its edge. Keep moving toward the roof
  and you step on; otherwise you slide off. Eaves and bridge undersides stop a jet *(§7 Movement)*.
- **Other movement rules:** charging a laser halves your speed. The map edge is a wall. Overlapping mechs are
  pushed apart *(§7)*.

## 6. Health, regen and armor

- 1400 HP *(§7 Health)*.
- **Regen is 45 HP/s after 5 s without dealing OR taking damage**, and never outside the zone *(§7 Health)*.
  - Zone damage counts as taking damage, so the timer restarts when you step back inside.
  - Our own long-range chip shots block our regen too (section 14 below).
- Armor upgrades reduce weapon damage, not zone damage *(§7 Health)*.
- **Rear hits:** a hit from outside the torso's 180° front arc does ×1.5. Facing the attacker protects you;
  `damaged.rear` reports it *(§7 Rear armor)*.

## 7. Weapons

Every shot leaves along the *actual* torso aim; there is no lock-on. You start with the gun only, in
slot 0 *(§7 Weapons)*.

| Weapon | Numbers | Notes |
|---|---|---|
| gun | 45 dmg, 252 m/s, 230 m, 0.24 s cooldown, mag 12, reload 2.4 s, spread 0.6° | Straight flight; lead moving targets. |
| mortar | 72 m/s at gravity 22 (max ≈ 235 m), 200 direct + 160 splash (7 m, 30% at the edge), 2.6 s cooldown | Knock-back 16 m/s plus 9 m/s up, −45 energy. Own splash hurts us at 50%. |
| laser | Hold to charge up to 0.75 s, 315 dmg (35% uncharged, 0.15 s minimum), 280 m hitscan, 1.65 s cooldown | Fires by itself after holding full charge 2 s more. **Cook-off:** +60% vs a mortar carrier. |
| lightning | 60 dmg, 45 m, 30° cone, chains to 2 more within 14 m (×0.7 per jump), 3 energy per shot, 0.25 s cooldown | Needs line of sight. **Short:** a laser holder it hits loses its charge and can't charge for 2 s. |

- **Weapon triangle:** laser beats mortar, mortar beats lightning, lightning beats laser; the gun is neutral
  *(§7)*.
- **Charging lasers are visible:** while a laser charges, everyone who sees the shooter sees `charging: true`
  and its aim. The `laser_charge` sound carries 140 m. The beam goes where it aims when it lets go
  *(§7, §8)*.
- **Cover:** bullets, shells and lasers hit buildings, bridges and terrain. **Trees don't stop shots**, only
  sight *(§7)*.
- **Cube scaling:** damage is multiplied by `1 + 0.04 × min(cubes, 10)` *(§7, §10)*.

## 8. NPC drones, crates, loot and supply drops

- **NPC drones:** 12 per match, 350 HP, 2 cubes each. They don't take zone damage *(§7 NPC drones)*.
  - They wander within 30 m of home.
  - They shoot (25 dmg, 90 m/s, 1 s cooldown) at whoever attacks them, or at mechs within 45 m with line of
    sight.
  - They chase until the target is 90 m away or they are 60 m from home.
- **Crates:** 36 per match, about a quarter on rooftops, 300 HP. Each drops 1 cube, and a weapon with
  chance 0.5 *(§7 Crates)*.
- **Weapon loot** *(§7 Weapon loot)*:
  - Sources: 40 floor spawns at match start (a quarter on roofs; mortar, laser and lightning 1:1:1), crates,
    and dead mechs (all weapons except the gun).
  - Taking is explicit: send `take` within 4 m. When both slots are full, `drop` a slot to swap.
  - You can't hold two of the same kind. A new weapon can't fire for `max(1 s, its cooldown)`.
  - You can't drop a laser while it charges. Loot never despawns.
- **Supply drops:** called at 70, 140 and 210 s, landing 25 s later inside the next circle *(§7 Supply
  drops)*.
  - Every mech sees them from the call.
  - Stand alone in the 8 m ring for 4 s to get +3 cubes, full HP and full energy.

## 9. Vision and sound

- **Vision:** 200 m inside a 120° wedge centred on the torso aim, plus 45 m all round (proximity sensors).
  Buildings block it *(§8 Vision)*.
  - Canopy fades sight (dense crowns hide); tree trunks block it.
  - Aiming is looking, so scanning costs aim time.
- **Tracking:** a seen mech or drone stays in `mechs` at its real position for 5 s after leaving view
  *(§8 Vision)*.
- **Sound ranges (m)** *(§8 Sound; config)*:
  - Long: death 600, drop 600, mortar 420, explosion 420, laser 380, gun 320.
  - Medium: crate 220, npc_gun 200, lightning 160, laser_charge 140.
  - Short: jet 90, boost 90, reload 60, step 40.
- **Footsteps:** mechs on the ground faster than 5 m/s make a `step` every 0.6 s. Walking slowly or standing
  still is silent *(§8 Sound)*.
- **What a sound tells us:** its bearing (σ 7° noise), a distance class (near < 60 m, mid < 160 m, far) and
  its age. It doesn't say who made it, but its kind tells the weapon *(§8 Sound)*.

## 10. The zone

The zone holds, then shrinks linearly to the next circle; the next circle is always inside the current
one *(§9)*. It starts at radius 560 around (41.3, −72.8). Outside it we lose `dps` HP/s, with no armor and
no regen.

| Phase | Hold, then shrink (s) | To radius (m) | dps | Done at |
|---|---|---|---|---|
| 1 | 60 / 50 | 380 | 20 | 1:50 |
| 2 | 45 / 40 | 240 | 35 | 3:15 |
| 3 | 35 / 35 | 140 | 60 | 4:25 |
| 4 | 30 / 30 | 70 | 100 | 5:25 |
| 5 | 25 / 25 | 25 | 160 | 6:15 |
| 6 | 15 / 25 | 0 | 260 | 6:55 |

*(§9; config `zone.phases`.)* It closes fully by about 6:55, consistent with the track page's "about 7
minutes" for practice. Matches end when one mech is left or at 8:00 *(§9; config `match.duration_s` 480)*.

## 11. Cubes and upgrades

- **Pickup:** within 3.5 m horizontally and 4 m vertically. Each cube gives +4% max HP (healing the
  difference) and +4% damage, up to 10 cubes *(§10)*.
- **Size:** each cube also makes the mech 5% bigger with no cap (radius, height, eye, muzzle), so it is
  easier to hit but sees and shoots from higher up *(§10)*.
- Killed mechs drop all their cubes plus 2 *(§10)*.
- **Upgrade offers:** one at cube counts 1, 3, 6, 10, 15, 21, 28, 36 and 45. Answer with `pick`, or choice 0
  is applied after 5 s. Offers queue one at a time *(§10; config)*.
- **The upgrades:** plating, armor, servos, capacitor, reactor, jets, nanites, gyros, sensors; gun
  damage/rate/velocity/mag; mortar splash/reload/velocity; laser charge/damage/pierce; lightning
  chain/range/efficiency. Their effects are in `hello.config.upgrades` *(config)*. Weapon upgrades belong to
  the mech and persist across drops *(§10)*.
- Our order since v6: plating > armor > nanites (`safety.py`).

## 12. The map

- **The map is the same every match.** Spawns, crates, NPCs, loot, the zone and drops are random *(§11)*.
  So anything derived from the map alone can be computed offline (section 16 below).
- **`v2/world.json`** (300 KB) holds `prisms` and `trees` *(§11)*.
  - `prisms`: building, stadium and bridge, each a footprint ring with holes, a `base` and a `top`. You can
    walk and shoot under bridges.
  - `trees`: they block sight only.
  - Scenery such as cars, water and barriers blocks nothing.
- **`v2/terrain.i16`** (1.5 MB): little-endian int16 centimetres on a 1070 × 724 grid at 1 m, sampled
  bilinearly *(§11)*.
- **Measured:** 259 prisms. 102 standable roofs (`rooftop.find_roofs`: ≥ 10 m wide, 6–45 m up, clear above),
  heights mostly 8–25 m (median about 16 m).

## 13. Other venues

- **play.joinbuilders.org** runs the same mechfight/1 engine. Private matches fill empty seats with bots
  *(browser game / track page)*. Its zone runs 240 s, so its timings don't transfer.
- **The kit has no local simulator.** `check.py` replays 300 recorded observations that don't react to the
  bot, so it checks the protocol, not play *(§1)*. Practice submissions remain the real measure.
- **Our arena tools** (`tools/play.py`, `record.py`, `analyze.py`) play and record private matches there in
  real time *(measured: match VGYDJ, 2026-11-21)*.
  - **Lobby:** a seat sends `{"type":"ready","name":…}`; the host clicks Start.
  - **Feed:** the spectator feed is `mechreplay/1`. A header gives the frame fields and mech names. Frame and
    event `t` are ticks at 20 Hz. Events are keyed by `e` (`hit`, `death {id,by,place}`, `fire`, `charge`,
    `laser`, `zap`, `short`, `cookoff`, `concuss`, `weapon`, `cube`, `upgrade`, `drop`,
    `end {places by id}`).
  - **Latency:** our answers left 2 ms (median) after each observation arrived; 1 of 356 was over 50 ms.
- **The private-room config is the quick game.** The match lasts 240 s and the zone closes at 208 s (phases
  start shrinking at 30, 77.5, 115, 147.5, 175 and 195 s). Supply drops are called at 35, 70 and 105 s.
  The bot reads all of this from `hello.config`, but timings and scores don't transfer to practice.
- **House bots in a private room:** 15 seats with names `red`, `expert`, `crimson`, `skirmish` and `astra`
  (`house:probe-red-best`, `house:expert`, `house:probe-red-honest`, `house:probe-astra-skirmish`,
  `house:probe-astra-finalist`, three of each). **They killed each other** (in VGYDJ every death but ours
  was mech on mech), unlike the practice squad on the track page. Treat arena results as a sanity check,
  not a score estimate.
- **Drones are a real threat early.** In VGYDJ an NPC drone did 838 of the 1400 damage that killed us
  (28+ hits at 10–25 m, mostly from behind), while the bot, which targets mechs first, never shot it.

## 14. Measured in the kit's recorded match

From `python tools/damage_ledger.py mechfight-kit/check/stream.jsonl.gz`. The match is mech 3's view of
t = 0–36.8 s, and that mech isn't our bot.

- **Taken:** 1665 in 13 hits, all from one mech (id 10, gun + laser).
  - **76% of it laser:** 4 × 315 = 1260, every laser hit at 45–160 m (56, 100, 149 and 151 m).
  - The rest was gun: 9 × 45 = 405.
  - **None from behind.**
- **Dealt:** 472.5 in 10 gun hits, 6 of them at 100–160 m. The recorded mech died 13th with 0 kills.
- **Regen confirmed:** HP rose at 18.8 s (545 → 556), exactly 5 s after the last hit it *dealt* (13.5 s).
  From 20 s it sat at 196 HP for about 10 s, because its own chip shots at 24.7 and 25.0 s kept restarting
  the timer.
- **House bots lead their gun shots:** learn_dodge measured a lead fraction k ≈ 0.73 from 5 of mech 10's
  bullets. 0 means aimed at where we were, 1 means fully led (`learn_dodge.py`, `tests/test_v9.py`).
- **What follows:** lasers at mid range are the main threat (hence H `LASER_GUARD`). Gun chip shots at range
  trade badly and block regen (hence I `NO_CHIP`). Lightning wins inside 45 m (hence J `AMBUSH`).

## 15. Experiment results so far

Full history: `scores.txt`. Queue and rules: `QUEUE.md`. The bar to beat is the current best + 0.15 (noise).

| Setup | Score | Lesson |
|---|---|---|
| v1 kit example | 1.15 | |
| v2 safety net + zone | 1.10 | Not dying to the zone alone doesn't score. |
| v3 gun duels | 1.81 | Fighting back well is the first big gain. |
| v4 survival (retreat, quiet) | 1.63 | Hiding without fighting loses. |
| v5 kills (lowest-HP target, strafe, dodge, laser fetch) | 2.60 | Kills are the score. |
| v6 economy (crates, cubes, upgrades) | 2.92 | |
| v7 / v7b heal (disengage to regen) | 2.67 / 2.81 | Disengaging costs more than it saves. |
| A: DUEL | 3.21 | 1v1 weapon choice and circling to the rear help. |
| F: + LIGHTNING | **3.46** | Current best. |
| C: + LEARN_DODGE | 3.46 | No gain. Most damage is laser, which it doesn't model. |
| G: + LOOKAHEAD | 3.23 | Worse. |
| HI: + LASER_GUARD + NO_CHIP | pending | Commit 663cf6f. |

Next: J `AMBUSH`, then K `ROOFTOP` (branch `v9-k`). Parked: B `LEAD_AIM`, D `SQUAD_AVOID`, the upgrade
order, heal and lookahead variants.

## 16. Later experiment (not built): offline map analysis for J and K

The map never changes (section 12 above; protocol.md §11), so the expensive geometry J (ambush) and K (rooftop) do live could be
precomputed from `world.json` + `terrain.i16`.

**Delivery:**
- Generate the tables with a script in `tools/`, run on the kit's map, and commit the output. The image can't
  read `/arena` at build time, and the kit is gitignored.
- At start-up, load the tables and compare a stored sha256 of `world.json` with `/arena`'s. On a mismatch,
  fall back to the live line checks we have now.

**What to precompute:**
- **Cover spots.** A 4 m grid of standable ground cells (not in a prism, slope walkable): about 268 × 181 ≈
  48.5k cells.
  - Per cell, a hidden-from bitmask: for each of 16 bearing sectors and 3 ranges (50, 100, 150 m), whether
    buildings or terrain block a mech-height line.
  - That is 48 bits per cell, about 290 KB raw and less gzipped.
  - It turns H's cover search and J's spot search (30–50 live `world.blocked` lines per plan, the costliest
    part of a decision) into a few lookups.
- **Building corners near likely paths.**
  - Corners: the convex footprint vertices of ground-level buildings, with their outward bisector and the
    ground height there.
  - Which to keep: corners beside open corridors (cells with long free sightlines along one axis), where an
    approaching mech comes into view inside 45 m. Those are the ambush angles for J.
  - Paths depend on the random zone, so keep corridors map-wide and filter by the next circle at run time.
  - A few thousand corners × ~16 B ≈ 30–50 KB.
- **Rooftops reachable by jet.** Per roof (102):
  - footprint, top, foot height, climb height and energy needed (about 1.6 per metre);
  - approach points: open ground ≤ 6 m from the wall, with no eave or bridge overhead to stop the jet;
  - standable area and distance to the edge for the spots on top;
  - a laser-value score: open ground cells visible from the roof within 280 m.

  Under 20 KB. K could then pick by value, not just distance, and approach from a known-good side.
- **Exposure map (optional).** Per cell, the open area visible within 200 m, so the bot can pick
  low-exposure waiting spots (J, the zone walk).
- **File size and limits:** about 0.3–0.5 MB in all.
  - Repo: 20 MB allowed; tracked files are about 0.4 MB now.
  - Load time: well within the 20 s `ready` budget.
  - Memory: a few MB of the 512 MB.
  - It must load from the image (read-only), not `/tmp`.
- **Tests:** random samples of every table must agree with live `world.blocked` and `rooftop.find_roofs` on
  the real map, and decisions must get faster, not slower.
