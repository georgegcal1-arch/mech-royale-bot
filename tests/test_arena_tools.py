"""tools/play.py, tools/record.py and tools/analyze.py against a fake play.joinbuilders.org room on localhost:
the lobby handshake, the mechfight/1 bridge to a real bot process, the recordings, and the analysis."""
import asyncio
import contextlib
import copy
import io
import json
import os
import sys

import pytest

websockets = pytest.importorskip("websockets")
from websockets.asyncio.server import serve  # noqa: E402

from conftest import HELLO, OBS, REPO  # noqa: E402

sys.path.insert(0, os.path.join(REPO, "tools"))
import analyze  # noqa: E402
import arena  # noqa: E402
import play  # noqa: E402
import record  # noqa: E402

N_OBS = 60
BOT = [sys.executable, "-u", os.path.join(REPO, "tools", "run_bot.py"), "--off", "LASER_GUARD", "--off", "NO_CHIP"]


def test_helpers():
    assert arena.norm_code(" abc-de ") == "ABCDE"
    with pytest.raises(arena.ArenaError):
        arena.norm_code("ab")
    assert arena.safe_name("mech!royale  bot <3 and more") == "mechroyale bot 3"
    assert len(arena.random_sid()) == 32
    assert arena.ws_url(7, "play", "https://play.joinbuilders.org", token="", sid="ab", name="x y", code="ABCDE") == \
        "wss://play.joinbuilders.org/r/7/play?token=&sid=ab&name=x+y&code=ABCDE"
    assert arena.kit_arena() is not None


def _fake_room(seen):
    """A room: lobby -> expects our ready -> hello + N_OBS obs (each answered) -> end -> results."""
    async def handler(ws):
        seen["path"] = ws.request.path
        await ws.send(json.dumps({"type": "lobby", "phase": "waiting", "you": {"ready": False, "seated": True}}))
        msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
        seen["first"] = msg
        hello = copy.deepcopy(HELLO)
        hello["map"]["dir"] = "/arena"  # what a real server sends: play.py must point it at the local kit
        await ws.send(json.dumps({"type": "lobby", "phase": "running", "you": {"ready": True, "seated": True}}))
        await ws.send(json.dumps(hello))
        acts = []
        for o in OBS[:N_OBS]:
            await ws.send(json.dumps(o))
            while True:
                a = json.loads(await asyncio.wait_for(ws.recv(), 10))
                acts.append(a)
                if a.get("tick") == o["tick"]:
                    break
        seen["acts"] = acts
        await ws.send(json.dumps({"type": "end", "place": 9, "kills": 1}))
        await ws.send(json.dumps({"type": "results", "places": []}))
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.recv(), 2)
    return handler


def test_play_bridges_a_match_to_the_bot(tmp_path, capsys):
    seen = {}

    async def main():
        async with serve(_fake_room(seen), "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            url = arena.ws_url(1, "play", f"http://127.0.0.1:{port}", token="", sid="ab" * 8, name="mrb", code="ABCDE")
            b = play.Bridge("ABCDE", "mrb", arena.kit_arena(), BOT, str(tmp_path), stamp="t")
            return await asyncio.wait_for(b.run(url), 120), b

    rc, b = asyncio.run(main())
    assert rc == 0
    assert seen["path"].startswith("/r/1/play?")
    assert seen["first"] == {"type": "ready", "name": "mrb"}, "the lobby ready, sent once before hello"
    acts = seen["acts"]
    assert not any(a.get("type") == "ready" for a in acts), "the bot's own ready is not forwarded"
    assert not any("debug" in a for a in acts), "debug drawings stay local"
    assert {a["tick"] for a in acts} >= {o["tick"] for o in OBS[:N_OBS]} - {OBS[N_OBS - 1]["tick"]} or len(acts) >= N_OBS - 2
    assert all(isinstance(a.get("move"), list) and len(a.get("fire", [])) == 2 for a in acts)
    out = capsys.readouterr().out
    assert "JOINED as 'mrb'" in out and "END: place 9, kills 1" in out
    rec = [json.loads(l) for l in open(tmp_path / "ABCDE-t-play.jsonl", encoding="utf-8")]
    kinds = [m["type"] for m in rec]
    assert kinds.count("obs") == N_OBS and kinds.count("sent") == len(acts) and "results" in kinds
    hello = next(m for m in rec if m["type"] == "hello")
    assert hello["map"]["dir"] == "/arena", "the recording keeps what the server sent"
    log = open(tmp_path / "ABCDE-t-bot.log", encoding="utf-8").read()
    assert "map not loaded" not in log, "the bot got the local map"
    assert "v9 switches on: DUEL, LIGHTNING" in log and "LASER_GUARD" not in log.split("v9 switches on:")[1].split("\n")[0]

    # analyze.py on it: the play part (no spectator recording beside it)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        analyze.main([str(tmp_path / "ABCDE-t-play.jsonl")])
    text = buf.getvalue()
    assert "== our match (mech 3" in text and "place 9, kills 1" in text and "answers:" in text


def test_record_saves_every_line_and_reconnects(tmp_path):
    header = {"type": "header", "fields": {"m": ["id", "x", "y", "z", "aim_yaw", "aim_pitch", "hp"]}}
    calls = []

    async def handler(ws):
        calls.append(ws.request.path)
        if len(calls) == 1:
            await ws.send(json.dumps(header) + "\n" + json.dumps({"t": 0.1, "m": [[1, 0, 0, 0, 0, 0, 1400]]}))
            await ws.close(4029, "slow down")
            return
        await ws.send("\n".join(json.dumps({"t": 0.1 * k, "ev": []}) for k in range(2, 6)))
        await ws.send(json.dumps({"type": "end"}))
        await asyncio.sleep(0.5)

    async def main():
        async with serve(handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            url = arena.ws_url(4, "live", f"http://127.0.0.1:{port}", token="", code="ABCDE", sid="watch")
            r = record.Recorder("ABCDE", str(tmp_path / "live.jsonl"), backoff=(0.01, 0.02), idle_s=5)
            return await asyncio.wait_for(r.run(url), 30), r

    rc, r = asyncio.run(main())
    assert rc == 0 and len(calls) == 2 and calls[0].startswith("/r/4/live?") and "sid=watch" in calls[0]
    lines = open(tmp_path / "live.jsonl", encoding="utf-8").read().splitlines()
    assert len(lines) == 7 and json.loads(lines[0])["type"] == "header" and json.loads(lines[-1])["type"] == "end"
    assert r.header_shown


def test_analyze_live_events(capsys):
    msgs = [{"type": "header", "players": [{"id": 3, "name": "us"}, {"id": 5, "name": "Expert 1"}]},
            {"t": 10.0, "ev": [{"type": "hit", "src": 5, "dst": 3, "w": "laser", "amt": 315, "rear": False}]},
            {"t": 70.0, "ev": [{"type": "hit", "src": 6, "dst": 3, "w": "gun", "amt": 45, "rear": True},
                               {"type": "hit", "src": 3, "dst": 6, "w": "gun", "amt": 45}]},
            {"t": 75.0, "ev": [{"type": "death", "id": 6, "place": 16, "by": 3}]},
            {"t": 90.0, "ev": [{"type": "death", "id": 3, "place": 15, "by": 5}]}]
    analyze.live_report(msgs, 3, print)
    out = capsys.readouterr().out
    assert "16: 6, by 3 us" in out and "15: 3 us, by 5 Expert 1" in out and "<- us" in out
    kills = next(line for line in out.splitlines() if line.startswith("kills: "))
    assert sorted(kills[len("kills: "):].split(", ")) == ["3 us 1", "5 Expert 1 1"]
    assert "hits on us: 2, 360 dmg, rear 1" in out and "mechs hitting us per minute: 0:1, 1:1" in out
