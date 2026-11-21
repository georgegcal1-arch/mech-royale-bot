# mech-royale-bot

## Key facts

Defaults from `mechfight-kit/protocol.md` and the recorded `hello.config`; the bot must read live values from `hello.config`.

**Scoring:** place = order of death (last alive = 1st; same tick = shared place; at the 8:00 time limit survivors rank by HP). Each match scores `16 - place`; a submission's score is the mean over 48 practice matches. Surviving beats kills. History in `scores.txt`.

**Timing and sandbox:**
- 20 ticks/s, one decision every 2 ticks (10/s). Answer each `obs` within **50 ms**, echoing its `tick`; `ready` within 20 s.
- No answer: `move`/`aim`/`boost` coast for 1 s, then idle. Queued observations: answer only the newest. Lines ≤ 64 KiB.
- Our target is **< 10 ms per decision**. check.py fails a pass when > 2% of answers are late (3 tries).
- Container: no network, 1 CPU, 512 MB, read-only disk (write only `/tmp`, 64 MB), uid 65534, map at `/arena`. Logs to stderr only.
- Submission build: linux/amd64, ≤ 10 min, repo ≤ 20 MB, `Dockerfile` at the top level.

**Weapons** (shots leave along the *actual* torso aim; hits from behind the 180° front arc do x1.5):
- gun: 45 dmg, 252 m/s, 230 m range, 0.24 s cooldown, mag 12, reload 2.4 s.
- mortar: 72 m/s at gravity 22 (max range ~235 m), 200 direct + 160 splash (7 m), 2.6 s cooldown, knock-back.
- laser: hold to charge 0.75 s, release fires 315 dmg (35% at no charge), 280 m, 1.65 s cooldown, +60% vs mortar carriers.
- lightning: 60 dmg, 45 m, 30° cone, chains to 2 more within 14 m, 3 energy per shot, 0.25 s cooldown.
- Pickup reach: 4 m horizontal and 4 m vertical. Cubes: +4% HP and damage each (up to 10).

**Zone:** starts at 560 m radius. Phases (hold s / shrink s → radius, dps outside): 60/50 → 380 m, 20 · 45/40 → 240, 35 · 35/35 → 140, 60 · 30/30 → 70, 100 · 25/25 → 25, 160 · 15/25 → 0, 260. Closed by 6:55. No regen outside it.
**Sensing:** vision 200 m in a 120° cone plus 45 m all round; seen mechs tracked for 5 s. Moving > 5 m/s on the ground makes footstep sounds. Mechs walk 11.7 m/s, boost 19.5 m/s, 1400 HP; regen 45 HP/s after 5 s without taking OR dealing damage (protocol.md §7: long-range chip shots block it too).

**Files:** `bot.py` (thin coordinator: `Bot.__init__` + `decide`; `Bot` mixes in the modules below) · `safety.py` (`Bot.act` safety net, fallback, zone awareness) · `perception.py` (damage memory, motion model, prediction, line-of-shot) · `targeting.py` · `combat.py` (torso, weapons, reload) · `movement.py` (legs: dodge, strafe, priority) · `loot.py` (take, early laser fetch) · `economy.py` (v6: crates and cubes when quiet) · `heal.py` (v7: disengage below 30% HP to regen, hide spot) · v9 experiments, each behind a switch: `duel.py` (`DUEL`: 1v1 weapon by range, circle toward the rear), `lead.py` (`LEAD_AIM`: per-mech motion model by back-test), `learn_dodge.py` (`LEARN_DODGE`: learn shooters' lead fraction, strafe/dodge against it), `squad.py` (`SQUAD_AVOID`: 2+ engaging -> spot with ≤1 line of fire, most isolated target), `LIGHTNING` (loot.py/combat.py/economy.py: take lightning, laser + lightning with the gun dropped once both are ours, lightning owns fights ≤ 43 m, gunless crates by laser/lightning), `lookahead.py` (`LOOKAHEAD`: under threat, 0.8 s simulation of 10 candidate moves, least expected damage, 8 ms budget from `decision_t0`), `guard.py` (`LASER_GUARD`, H: a laser charging at us with line of sight -> short it with lightning ≤ 43 m, else boost to the nearest spot it can't see within 0.75 s), `nochip.py` (`NO_CHIP`, I: no gun shots beyond 100 m at a mech above 25% HP; legs close in via cover), `ambush.py` (`AMBUSH`, J: lightning held + one hunter closing from 50-180 m -> wait hidden and silent within 40 m, spring inside 43 m) · `features.py` (on/off switches for the v5/v6/v7 features + heal thresholds + the v9 experiments (`V9_SWITCHES`); defaults = v6 (`HEAL` and the v9 switches off), `HEAL` on = v7b, `ECONOMY` off too = v5) · `QUEUE.md` (the experiment queue: which switches each submission turns on, commit, score) · `world.py` (map geometry, line-of-shot checks, dense canopy) · `mechfight.py` (kit wrapper, don't edit) · `tools/damage_ledger.py` (damage taken/dealt by weapon, range and time for any obs stream) · `Dockerfile` · `scores.txt` · `tests/` (pytest + Hypothesis suite, v3's unittest `test_bot.py`, v5's `test_kills.py`, v6's `test_economy.py`, v7's `test_heal.py`, `test_world.py`, `replay.py` (`--off SWITCH`; `golden/v5_replay.csv` / `golden/v6_replay.csv` = v5's / v6's `--no-timing` replay), `test_v9.py` (DUEL/LEAD_AIM/LEARN_DODGE/SQUAD_AVOID), `test_v9fg.py` (LIGHTNING/LOOKAHEAD), `test_v9hij.py` (LASER_GUARD/NO_CHIP/AMBUSH, `Box` fake world), `test_tools.py`; `conftest.v9_off` pins legacy tests to v6, `hook.py` = PostToolUse hook). `mechfight-kit/` is local only (gitignored); the map is `mechfight-kit/arena/v2/`.

**Commands** (from the repo root):
```sh
python -m pip install -r tests/requirements.txt
python -m pytest tests                                  # full suite (~1 min); HYPOTHESIS_PROFILE=hook for the quick run
python tests/replay.py                                  # -> tests/out/decisions.csv (--no-timing: byte-comparable)
python tests/replay.py --no-timing                      # defaults (HEAL off): must equal tests/golden/v6_replay.csv
python tests/replay.py --no-timing --off ECONOMY         # must equal tests/golden/v5_replay.csv
python tests/replay.py --no-timing --v6                  # v9 switches off whatever their defaults: must equal v6_replay.csv
python tests/replay.py --on DUEL --on LEAD_AIM --on LEARN_DODGE --on SQUAD_AVOID --on LIGHTNING --on LOOKAHEAD   # timing, all experiments on
python mechfight-kit/check/check.py -- python -u bot.py
python tools/damage_ledger.py mechfight-kit/check/stream.jsonl.gz   # --summary for totals only
docker build -t my-bot .
python mechfight-kit/check/check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "${PWD}/mechfight-kit/arena:/arena:ro" my-bot
```
On Windows check.py turns `--map-dir /arena` into `C:\arena`, so the bot runs without its map locally ("map not loaded" on stderr). The Linux scorer passes `/arena` unchanged.
