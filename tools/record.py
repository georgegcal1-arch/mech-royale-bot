"""Record a private match on play.joinbuilders.org from its spectator feed.

    python tools/record.py CODE                  # -> recordings/<CODE>-<stamp>-live.jsonl

Looks the room up with sid=watch, opens wss://.../r/N/live?code=CODE and saves every line exactly as received
(one websocket message may hold several lines). The first `header` line documents the frame fields; its
summary is printed once. Reconnects with back-off (10-20 s after a 4029, a few seconds after a drop);
stops when the server closes the match (4404 / a normal close after frames), after IDLE_S without a line
once frames have come, or on Ctrl-C. Needs `websockets` (tools/requirements.txt).
"""
import argparse
import asyncio
import json
import os
import random
import sys
import time

import websockets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arena  # noqa: E402

IDLE_S = 30.0
RECONNECTS = 8
END_TYPES = {"end", "results", "over", "finished", "gameover"}


def say(*a):
    print("[record]", *a, flush=True)


def header_summary(h):
    """A short description of a header line: its keys, and any field lists."""
    out = [f"header keys: {', '.join(h.keys())}"]
    for k, v in h.items():
        if k == "type":
            continue
        s = json.dumps(v, separators=(",", ":"))
        out.append(f"  {k}: {s[:600]}{'...' if len(s) > 600 else ''}")
    return "\n".join(out)


class Recorder:
    def __init__(self, code, path, backoff=(10.0, 20.0), idle_s=IDLE_S):
        self.code, self.path, self.backoff, self.idle_s = code, path, backoff, idle_s
        self.f = open(path, "w", encoding="utf-8")
        self.lines = self.frames = 0
        self.header_shown = False
        self.ended = False
        self.types = {}

    def take(self, raw):
        for line in str(raw).split("\n"):
            if not line.strip():
                continue
            self.f.write(line.rstrip("\r") + "\n")
            self.lines += 1
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            kind = msg.get("type") if isinstance(msg, dict) else None
            self.types[kind] = self.types.get(kind, 0) + 1
            if kind == "header" and not self.header_shown:
                self.header_shown = True
                say(header_summary(msg))
            elif kind in END_TYPES:
                self.ended = True
            else:
                self.frames += 1
            if self.lines % 2000 == 0:
                self.f.flush()
                say(f"{self.lines} lines")

    async def session(self, url):
        async with websockets.connect(url, max_size=2 ** 24, ping_interval=20, ping_timeout=20,
                                      user_agent_header=arena.USER_AGENT, open_timeout=15) as ws:
            say("connected")
            while not self.ended:
                timeout = self.idle_s if self.frames else None
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout)
                except asyncio.TimeoutError:
                    say(f"no line for {self.idle_s:.0f} s: done")
                    self.ended = True
                    return
                self.take(raw)

    async def run(self, url):
        tries = 0
        try:
            while not self.ended:
                try:
                    await self.session(url)
                except websockets.ConnectionClosed as e:
                    code = e.rcvd.code if e.rcvd else None
                    if code == 4404 or (code == 1000 and self.frames):
                        say(f"closed ({code}): match over")
                        return 0
                    if tries >= RECONNECTS:
                        say(f"closed ({code}), giving up")
                        return 3
                    tries += 1
                    wait = random.uniform(*self.backoff) if code == 4029 else min(2.0 * tries, 10.0)
                    say(f"closed ({code}); reconnecting in {wait:.0f} s ({tries}/{RECONNECTS})")
                    await asyncio.sleep(wait)
                except OSError as e:
                    if tries >= RECONNECTS:
                        raise
                    tries += 1
                    say(f"connection error {e!r}; retrying ({tries}/{RECONNECTS})")
                    await asyncio.sleep(3.0)
            return 0
        finally:
            self.f.close()
            say(f"{self.lines} lines ({self.frames} frames; types {self.types}) -> {self.path}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("code")
    ap.add_argument("--base", default=arena.BASE)
    ap.add_argument("--room", type=int, default=None, help="skip the code lookup (tests)")
    ap.add_argument("--out-dir", default=arena.RECORDINGS)
    ap.add_argument("--stamp", default=None)
    ap.add_argument("--idle-s", type=float, default=IDLE_S)
    a = ap.parse_args(argv)
    code = arena.norm_code(a.code)
    room = a.room if a.room is not None else arena.join_room(code, "watch", a.base)
    url = arena.ws_url(room, "live", a.base, token="", code=code, sid="watch")
    path = arena.recording_path(code, "live", a.stamp, a.out_dir)
    say(f"match {code}: room {room} -> {path}")
    r = Recorder(code, path, idle_s=a.idle_s)
    try:
        return asyncio.run(r.run(url))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
