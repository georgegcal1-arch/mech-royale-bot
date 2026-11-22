# Experiment queue (v9)

Read this first after a /clear: it holds the rules, the order, where the code lives and what to do next.

## Rules (since exp HI scored 3.52)
- **Stacked:** each experiment (or bundle of switches) is turned on *on top of the current best setup*, which keeps all winners on.
  Only the experiment under test changes between two submissions.
- **Bar:** the current best score. A step counts as a winner only if it beats the bar by **0.15+**
  (noise). Now: best = **3.52** (DUEL + LIGHTNING + LASER_GUARD + NO_CHIP), so a step must score **3.67 or more**.
  (HI was +0.06 over 3.46, under the 0.15 rule; kept as the new best by the user's call.)
- **Winner:** it stays on, and its score becomes the new bar. **Loser:** it is turned off again before
  the next step, so the next step stacks on the unchanged best.
- **End:** `main` is left on the best-scoring setup.

## Per-step procedure
1. In `features.py`, set the switches to the current best plus the step's switch(es). Other v9 switches stay off.
2. Run `python -m pytest tests` (with `MECHFIGHT_KIT=C:/Users/georg/buildathon/mech-royale-bot/mechfight-kit`),
   `python tests/replay.py --no-timing --v6` (must equal `tests/golden/v6_replay.csv`) and
   `python mechfight-kit/check/check.py -- python -u bot.py` (kit at the repo root, not in the worktree).
3. Commit `exp X: <setup>` on branch `worktree-v3`, run `git push origin HEAD:main`, and confirm GitHub's top
   commit with `gh api repos/georgegcal1-arch/mech-royale-bot/commits/main`. Then report "ready to submit X".
4. When the score comes in: log it in `scores.txt` with the commit hash, apply winner/loser, update this
   file, and prepare the next step right away.

## Order: big bundles first, fine-tune later (since G)
| Step | Experiment (switches) | Setup submitted | Commit | Score | vs bar | Result |
|------|-----------------------|-----------------|--------|-------|--------|--------|
| A | duel (`DUEL`) | DUEL | 3b7c5ac | **3.21** | +0.29 vs 2.92 | **winner: kept on** |
| F | lightning (`LIGHTNING`) | DUEL + LIGHTNING | 21dbb8f | **3.46** | +0.25 vs 3.21 | **winner: kept on** |
| C | learn_dodge (`LEARN_DODGE`) | DUEL + LIGHTNING + LEARN_DODGE | 6dc2b3e | **3.46** | +0.00 vs 3.46 | loser: turned off |
| G | lookahead (`LOOKAHEAD`) | DUEL + LIGHTNING + LOOKAHEAD | 5ea055b | **3.23** | -0.23 vs 3.46 | loser: turned off |
| HI | laser discipline (`LASER_GUARD` + `NO_CHIP`) | DUEL + LIGHTNING + LASER_GUARD + NO_CHIP | 663cf6f | **3.52** | +0.06 vs 3.46 | **kept as best (user's call; under +0.15)** |
| N | npc_aware (`NPC_AWARE`) | HI + NPC_AWARE | 535f7fc | **3.52** | +0.00 vs 3.52 | no effect yet: **kept on** (user's call: harmless, should matter once navigation lets us roam) |
| S | stuck escape (`STUCK_ESCAPE`, navigation step 1) | N + STUCK_ESCAPE | (exp S commit) | | needs 3.67 | arena MAZAE 1st / 5 kills, 0 s stuck, but the escape never triggered; merged on the navsim proof (user's call) |
| J | ambush (`AMBUSH`) | best + AMBUSH | | | | |
| K | rooftop (`ROOFTOP`, to build on a side branch) | best + ROOFTOP | | | | |

"Best" always includes v6's economy (crates, cubes, upgrades: `ECONOMY`, on since v6).

**K rooftop (spec):** if a reachable roof lies inside the next zone circle within ~60 m, jet onto it early
and fight from there with the laser; come down only when the zone forces it.

**Parked for later (fine-tuning):** B lead_aim (`LEAD_AIM`), D squad_avoid (`SQUAD_AVOID`), the upgrade order,
heal variants (`HEAL` thresholds), lookahead variants (`LOOKAHEAD` 3.23 as is).

E (all winners combined) is no longer a separate step: with stacking, the setup after the last step
already is the combination of the winners.

## Where the code lives
- `v9-base` (51bf6cf): DUEL, LEAD_AIM, LEARN_DODGE, SQUAD_AVOID, all off = v6 exactly.
- Branch `v9-fg` (f9a8ced, pushed to origin): F `LIGHTNING` and G `LOOKAHEAD`, built off v9-base, both off.
  Plan: merge it into `worktree-v3` (main) only when the queue reaches F. A dry run showed the merge is
  conflict-free. **Status: merged for step F** (both switches still exist; G stays off until its step).
- Branch `v9-hij` (c0a679a, pushed to origin): H `LASER_GUARD` (guard.py), I `NO_CHIP` (nochip.py), J `AMBUSH`
  (ambush.py), all off, plus `tools/damage_ledger.py`; built off worktree-v3 at 51da0da. Merge it into
  `worktree-v3` when the queue reaches H (right after G's score is logged). **Status: merged for step HI**
  (75296ab; AMBUSH stays off until J).
- Why H, I, J (from `tools/damage_ledger.py` on the recorded match): 1260 of 1665 damage taken was 4 laser
  hits at 55-150 m; our gun chip shots at 100-160 m (6 of 10 hits) kept resetting the regen clock.
- All v9 switches are listed in `features.V9_SWITCHES`. `conftest.v9_off` and `replay.py --v6` turn them all
  off for the v6 golden comparisons, so those stay valid whatever the defaults are.

- Branch `v9-n` (pushed): N `NPC_AWARE` (npc.py) plus the arena tools (`tools/play.py`, `record.py`, `analyze.py`).
  Arena: baseline VGYDJ 14th (a drone did 838 dmg); with N MNUCG 1st, AGTRR 3rd, but no drone came near us, so
  N went untested there. **Status: merged for step N.**

Status: **S ready to submit** (DUEL + LIGHTNING + LASER_GUARD + NO_CHIP + NPC_AWARE + STUCK_ESCAPE). Next: navigation step 2
(walkability grid + A*, ground layer, `NAV_ROUTE`) on branch `v9-nav`, arena-tested before it is queued.
