# Experiment queue (v9)

Base: **v6 = 2.92** (`v9-base`, commit 51bf6cf: all four v9 switches off = v6 exactly).
A gain counts only if it beats 2.92 by **0.15+** (noise), i.e. a score of **3.07 or more**.

Each step turns on only its own switches in `features.py` (all other v9 switches off), runs the tests and
check.py, and is committed as `exp X: ...` and pushed to `main` for submission. Scores are logged in
`scores.txt` with the commit hash.

| Step | Switches on (features.py) | Commit | Score | Gain vs 2.92 | Counts? |
|------|---------------------------|--------|-------|--------------|---------|
| A | `DUEL` | | | | |
| B | `LEAD_AIM` | | | | |
| C | `LEARN_DODGE` | | | | |
| D | `SQUAD_AVOID` | | | | |
| E | every one of A-D that scored 3.07+ (skipped if none) | | | | |

At the end `main` is left on the best-scoring setup (v6 if nothing beats it by 0.15+).

Status: **A ready to submit**
