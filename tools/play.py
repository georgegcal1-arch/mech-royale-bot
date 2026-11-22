"""Play our bot in a private match on play.joinbuilders.org (real time, one match at a time).

    python tools/play.py CODE
    python tools/play.py CODE --name mech-royale-bot -- python -u tools/run_bot.py --off LASER_GUARD --off NO_CHIP

Joins the match's lobby as a player and marks the seat ready (the host clicks Start). On `hello` it starts the
bot (default `python -u tools/run_bot.py`) and bridges mechfight/1: server hello/obs/end -> the bot's stdin,
the bot's action lines -> the server. The bot's own `ready` line is not forwarded (the lobby already has
ours; the site's client sends none after hello), nor are its debug drawings.

Writes recordings/<CODE>-<stamp>-play.jsonl (every server message with its receive time `_rx`, plus each
sent action as {"type": "sent", ...}; damage_ledger.py and analyze.py read it) and ...-bot.log (the bot's
stderr). Prints a latency summary at the end. Needs `websockets` (tools/requirements.txt).
"""
import argparse
import asyncio
import json
import os
import sys
import time

import websockets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arena  # noqa: E402

READY_EVERY_S = 2.0
AFTER_END_S = 10.0
RECONNECTS = 5
FATAL = {4000: "this seat was opened elsewhere", 4404: "the match code has ended or is unknown",
         4429: "too many tries from this network: wait a minute"}


def say(*a):
    print("[play]", *a, flush=True)


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


class Bridge:
    def __init__(self, code, name, map_dir, bot_cmd, out_dir, stamp=None):
        self.code, self.name, self.map_dir, self.bot_cmd = code, name, map_dir, bot_cmd
        # line-buffered: the recording can be read while the match runs
        self.rec = open(arena.recording_path(code, "play", stamp, out_dir), "w", encoding="utf-8", buffering=1)
        self.log_path = arena.recording_path(code, "bot.log", stamp, out_dir)
        self.proc = None
        self.ws = None
        self.ready_at = -1e9
        self.lobby_seen = None
        self.ended_at = None
        self.results = None
        self.obs_rx = {}         # tick -> perf time the obs arrived
        self.lat_ms = []         # obs arrival -> action sent
        self.gaps_ms = []        # between consecutive obs
        self.last_obs = None
        self.obs_n = self.sent_n = self.ready_dropped = 0

    def record(self, msg):
        self.rec.write(json.dumps(msg, separators=(",", ":")) + "\n")

    async def start_bot(self):
        log = open(self.log_path, "w", encoding="utf-8")
        self.proc = await asyncio.create_subprocess_exec(
            *self.bot_cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
            cwd=arena.REPO)
        asyncio.get_running_loop().create_task(self.pump_bot())
        say(f"bot started: {' '.join(self.bot_cmd)} (stderr -> {self.log_path})")

    async def to_bot(self, msg):
        if self.proc is None or self.proc.stdin is None or self.proc.stdin.is_closing():
            return
        try:
            self.proc.stdin.write((json.dumps(msg, separators=(",", ":")) + "\n").encode())
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def pump_bot(self):
        """The bot's stdout -> the server (actions only)."""
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                return
            try:
                act = json.loads(line)
            except ValueError:
                say(f"bot wrote a non-JSON line: {line[:80]!r}")
                continue
            if act.get("type") == "ready":
                self.ready_dropped += 1
                continue
            if "tick" not in act:
                continue
            act.pop("debug", None)
            t0 = self.obs_rx.pop(act["tick"], None)
            if self.ws is not None:
                try:
                    await self.ws.send(json.dumps(act, separators=(",", ":")))
                except websockets.ConnectionClosed:
                    continue
                self.sent_n += 1
                if t0 is not None:
                    self.lat_ms.append((time.perf_counter() - t0) * 1000)
            self.record({"type": "sent", "at": time.time(), "action": act})

    async def on_message(self, msg):
        kind = msg.get("type")
        now = time.perf_counter()
        if kind == "lobby":
            you = msg.get("you") or {}
            if not you.get("ready") and now - self.ready_at >= READY_EVERY_S and self.proc is None:
                await self.ws.send(json.dumps({"type": "ready", "name": self.name}))
                self.ready_at = now
            phase = msg.get("phase")
            if self.lobby_seen != phase:
                self.lobby_seen = phase
                others = [k for k in msg if k not in ("type", "you")]
                say(f"lobby phase={phase} you={json.dumps(you)[:200]} (fields: {', '.join(others)})")
                if phase != "running":
                    say(f"JOINED as {self.name!r}, seat ready: waiting for the host to click Start")
        elif kind == "hello":
            if self.map_dir:
                msg.setdefault("map", {})["dir"] = self.map_dir
            else:
                say("WARNING: no local kit arena/ found: the bot will play without its map")
            if self.proc is None:
                await self.start_bot()
            z = (msg.get("config") or {}).get("zone") or {}
            dur = sum(p.get("hold_s", 0) + p.get("shrink_s", 0) for p in z.get("phases") or [])
            say(f"hello: slot {msg.get('slot')}, seed {msg.get('seed')}, zone closes at {dur:.0f} s, "
                f"match {((msg.get('config') or {}).get('match') or {}).get('duration_s')} s")
            await self.to_bot(msg)
        elif kind == "obs":
            self.obs_n += 1
            self.obs_rx[msg.get("tick")] = now
            if len(self.obs_rx) > 200:  # answers the bot skipped (queued observations)
                for k in sorted(self.obs_rx)[:100]:
                    del self.obs_rx[k]
            if self.last_obs is not None:
                self.gaps_ms.append((now - self.last_obs) * 1000)
            self.last_obs = now
            if self.obs_n % 300 == 0:
                me = msg.get("self") or {}
                say(f"t={msg.get('t')} hp={me.get('hp')} alive={msg.get('alive')} sent={self.sent_n}")
            await self.to_bot(msg)
        elif kind == "end":
            self.ended_at = now
            say(f"END: place {msg.get('place')}, kills {msg.get('kills')}")
            await self.to_bot(msg)
        elif kind == "results":
            self.results = msg
            say("results received")

    async def session(self, url):
        async with websockets.connect(url, max_size=2 ** 23, ping_interval=20, ping_timeout=20,
                                      user_agent_header=arena.USER_AGENT, open_timeout=15) as ws:
            self.ws = ws
            while True:
                timeout = None if self.ended_at is None else max(0.1, AFTER_END_S - (time.perf_counter() - self.ended_at))
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout)
                except asyncio.TimeoutError:
                    return
                for line in str(raw).split("\n"):
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    msg["_rx"] = time.time()
                    self.record(msg)
                    msg.pop("_rx")
                    await self.on_message(msg)
                if self.results is not None:
                    return

    async def run(self, url):
        tries = 0
        try:
            while True:
                try:
                    await self.session(url)
                    return 0
                except websockets.ConnectionClosed as e:
                    code = e.rcvd.code if e.rcvd else None
                    if code in FATAL:
                        say(f"closed {code}: {FATAL[code]}")
                        return 2
                    if self.ended_at is not None or tries >= RECONNECTS:
                        say(f"closed ({code}), not reconnecting")
                        return 0 if self.ended_at is not None else 3
                    tries += 1
                    say(f"dropped ({code}); reconnecting ({tries}/{RECONNECTS})")
                    await asyncio.sleep(1.5)
                except OSError as e:
                    if tries >= RECONNECTS:
                        raise
                    tries += 1
                    say(f"connection error {e!r}; retrying ({tries}/{RECONNECTS})")
                    await asyncio.sleep(2.0)
        finally:
            self.ws = None
            if self.proc is not None:
                if self.proc.stdin and not self.proc.stdin.is_closing():
                    self.proc.stdin.close()
                try:
                    await asyncio.wait_for(self.proc.wait(), 5)
                except asyncio.TimeoutError:
                    self.proc.kill()
            self.rec.close()
            self.summary()

    def summary(self):
        lat, gaps = self.lat_ms, self.gaps_ms
        say(f"observations {self.obs_n}, actions sent {self.sent_n}, bot ready lines dropped {self.ready_dropped}")
        if lat:
            say(f"obs -> action ms: p50 {pct(lat, .5):.1f} p90 {pct(lat, .9):.1f} p99 {pct(lat, .99):.1f} "
                f"max {max(lat):.1f}; over 50 ms: {sum(x > 50 for x in lat)}")
        if gaps:
            say(f"gap between obs ms: p50 {pct(gaps, .5):.1f} p99 {pct(gaps, .99):.1f} max {max(gaps):.1f}")
        say(f"recording: {self.rec.name}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    bot_cmd = None
    if "--" in argv:
        i = argv.index("--")
        argv, bot_cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("code")
    ap.add_argument("--name", default="mech-royale-bot")
    ap.add_argument("--map-dir", default=None, help="the arena/ folder for hello.map.dir (default: the local kit's)")
    ap.add_argument("--base", default=arena.BASE)
    ap.add_argument("--room", type=int, default=None, help="skip the code lookup (tests)")
    ap.add_argument("--out-dir", default=arena.RECORDINGS)
    ap.add_argument("--stamp", default=None)
    a = ap.parse_args(argv)
    code = arena.norm_code(a.code)
    name = arena.safe_name(a.name)
    bot_cmd = bot_cmd or [sys.executable, "-u", os.path.join(arena.REPO, "tools", "run_bot.py")]
    sid = arena.random_sid()
    room = a.room if a.room is not None else arena.join_room(code, sid, a.base)
    url = arena.ws_url(room, "play", a.base, token="", sid=sid, name=name, code=code)
    say(f"match {code}: room {room}")
    b = Bridge(code, name, a.map_dir or arena.kit_arena(), bot_cmd, a.out_dir, a.stamp)
    return asyncio.run(b.run(url))


if __name__ == "__main__":
    sys.exit(main())
