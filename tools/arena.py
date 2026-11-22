"""Shared helpers for the private-match arena on play.joinbuilders.org (tools/record.py, tools/play.py).

What the site's own client does (read from its /assets/play-*.js and game-*.js):
  GET  /mm/code/join?c=CODE&sid=SID            -> {"code": ..., "room": N} or {"error": unknown|busy|slow, "retry_s": s}
  WSS  /r/N/play?token=&sid=SID&name=NAME&code=CODE   a seat: lobby, hello, obs, end, results (mechfight/1)
  WSS  /r/N/live?token=&code=CODE&sid=SID              the spectator feed: newline-separated lines per message
Close codes: 4000 seat opened elsewhere, 4404 code ended, 4429 too many tries, 4029 (live) back off 10-20 s.
"""
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("ARENA_BASE", "https://play.joinbuilders.org")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDINGS = os.path.join(REPO, "recordings")
USER_AGENT = "mechfight-arena-tools/1"  # generic: no personal details in requests
JOIN_TRIES = 6


class ArenaError(Exception):
    pass


def norm_code(code):
    """As the site does: letters only, upper case, at most 8."""
    c = re.sub(r"[^A-Z]", "", code.upper())[:8]
    if len(c) < 4:
        raise ArenaError(f"a match code is 4-8 letters, got {code!r}")
    return c


def safe_name(name):
    """As the site does: [A-Za-z0-9 _-], single spaces, at most 16 characters."""
    n = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 _-]", "", name)).strip()[:16].strip()
    return n or "bot"


def random_sid():
    return secrets.token_hex(16)  # the site accepts 16-64 hex characters


def join_room(code, sid, base=BASE, tries=JOIN_TRIES):
    """The room number for a match code (retrying 'busy' / 'slow' after the server's retry_s)."""
    url = f"{base}/mm/code/join?c={urllib.parse.quote(code)}&sid={urllib.parse.quote(sid)}"
    last = None
    for _ in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-store"})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            last = f"join request failed: {e}"
            time.sleep(2.0)
            continue
        if isinstance(data.get("room"), int):
            return data["room"]
        err = data.get("error")
        last = f"join refused: {data}"
        if err in ("busy", "slow"):
            time.sleep(min(float(data.get("retry_s") or 3.0), 30.0))
            continue
        raise ArenaError(f"match {code}: {err or data}")
    raise ArenaError(last or "join failed")


def ws_url(room, kind, base=BASE, **params):
    """wss://host/r/N/<kind>?<params> (kind: play or live)."""
    u = urllib.parse.urlsplit(base)
    scheme = "wss" if u.scheme == "https" else "ws"
    q = urllib.parse.urlencode({k: ("" if v is None else v) for k, v in params.items()})
    return f"{scheme}://{u.netloc}/r/{room}/{kind}?{q}"


def recording_path(code, kind, stamp=None, out_dir=RECORDINGS):
    """recordings/<CODE>-<UTC yyyymmdd-hhmmss>-<kind>.jsonl (kind: live, play, bot.log...)."""
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    os.makedirs(out_dir, exist_ok=True)
    ext = "" if "." in kind else ".jsonl"
    return os.path.join(out_dir, f"{code}-{stamp}-{kind}{ext}")


def kit_arena():
    """The local kit's arena/ (MECHFIGHT_KIT, the worktree's kit, or the main checkout's)."""
    cands = [os.environ.get("MECHFIGHT_KIT", ""), os.path.join(REPO, "mechfight-kit")]
    here = REPO
    for _ in range(4):  # a worktree under .claude/worktrees/: the main checkout is a few levels up
        here = os.path.dirname(here)
        cands.append(os.path.join(here, "mechfight-kit"))
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "arena", "v2", "world.json")):
            return os.path.join(c, "arena")
    return None
