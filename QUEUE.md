# Experiment queue (v9)

Read this first after a /clear: it holds the rules, the order, where the code lives and what to do next.

## Rules (since exp A scored 3.21)
- **Stacked:** each experiment is turned on *on top of the current best setup*, which keeps all winners on.
  Only the experiment under test changes between two submissions.
- **Bar:** the current best score. A step counts as a winner only if it beats the bar by **0.15+**
  (noise). Now: best = **3.21** (DUEL), so a step must score **3.36 or more**.
- **Winner:** it stays on, and its score becomes the new bar. **Loser:** it is turned off again before
  the next step, so the next step stacks on the unchanged best.
- **End:** `main` is left on the best-scoring setup.

## Per-step procedure
1. In `features.py`, set the switches to the current best plus the step's switch. Other v9 switches stay off.
2. Run `python -m pytest tests` (with `MECHFIGHT_KIT=C:/Users/georg/buildathon/mech-royale-bot/mechfight-kit`),
   `python tests/replay.py --no-timing --v6` (must equal `tests/golden/v6_replay.csv`) and
   `python mechfight-kit/check/check.py -- python -u bot.py` (kit at the repo root, not in the worktree).
3. Commit `exp X: <setup>` on branch `worktree-v3`, run `git push origin HEAD:main`, and confirm GitHub's top
   commit with `gh api repos/georgegcal1-arch/mech-royale-bot/commits/main`. Then report "ready to submit X".
4. When the score comes in: log it in `scores.txt` with the commit hash, apply winner/loser, update this
   file, and prepare the next step right away.

## Order (re-ranked by expected payoff after A)
| Step | Experiment (switch) | Setup submitted | Commit | Score | vs bar | Result |
|------|---------------------|-----------------|--------|-------|--------|--------|
| A | duel (`DUEL`) | DUEL | 3b7c5ac | **3.21** | +0.29 vs 2.92 | **winner: kept on** |
| F | lightning (`LIGHTNING`) | DUEL + LIGHTNING | (exp F commit) | | needs 3.36 | |
| C | learn_dodge (`LEARN_DODGE`) | best + LEARN_DODGE | | | | |
| G | lookahead (`LOOKAHEAD`) | best + LOOKAHEAD | | | | |
| B | lead_aim (`LEAD_AIM`) | best + LEAD_AIM | | | | |
| D | squad_avoid (`SQUAD_AVOID`) | best + SQUAD_AVOID | | | | |

E (all winners combined) is no longer a separate step: with stacking, the setup after the last step
already is the combination of the winners.

## Where the code lives
- `v9-base` (51bf6cf): DUEL, LEAD_AIM, LEARN_DODGE, SQUAD_AVOID, all off = v6 exactly.
- Branch `v9-fg` (f9a8ced, pushed to origin): F `LIGHTNING` and G `LOOKAHEAD`, built off v9-base, both off.
  Plan: merge it into `worktree-v3` (main) only when the queue reaches F. A dry run showed the merge is
  conflict-free. **Status: merged for step F** (both switches still exist; G stays off until its step).
- All v9 switches are listed in `features.V9_SWITCHES`. `conftest.v9_off` and `replay.py --v6` turn them all
  off for the v6 golden comparisons, so those stay valid whatever the defaults are.

Status: **F ready to submit** (DUEL + LIGHTNING).
