"""Run bot.py with some features.py switches flipped, for arena matches (tools/play.py).

    python -u tools/run_bot.py                                   # the defaults in features.py
    python -u tools/run_bot.py --off LASER_GUARD --off NO_CHIP   # e.g. the scored best: DUEL + LIGHTNING + economy
    python -u tools/run_bot.py --on AMBUSH

Speaks mechfight/1 on stdin/stdout exactly like `python -u bot.py`; logs the switches used to stderr.
"""
import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import features  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--on", action="append", default=[], metavar="SWITCH")
    ap.add_argument("--off", action="append", default=[], metavar="SWITCH")
    a = ap.parse_args(argv)
    for s, v in [(s, True) for s in a.on] + [(s, False) for s in a.off]:
        if not isinstance(getattr(features, s, None), bool):
            ap.error(f"unknown switch {s!r}")
        setattr(features, s, v)
    import bot  # noqa: E402  (after the switches: they are read at decision time anyway)
    import mechfight as mf  # noqa: E402
    on = [s for s in features.V9_SWITCHES if getattr(features, s)]
    mf.log(f"run_bot: v9 switches on: {', '.join(on) or 'none'}; ECONOMY={features.ECONOMY} HEAL={features.HEAL}")
    mf.run(bot.Bot)


if __name__ == "__main__":
    main()
