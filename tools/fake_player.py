"""Rehearsal: a fake second player. Connects to the server as its own player, shares the profile of one Zoi
(its 'look') and streams the live state of another Zoi in this same game (its 'moves'): position, facing, what it's
doing, what it's wearing - exactly what a real friend's companion would send.

    python tools/fake_player.py --look 18513 --moves 21784 [--server nox.local] [--name "Fake David"]
        [--social Co_Friendship_G04_Start]   after 6 s, pretend the friend greets you (your Zoi) with that social
        [--chat "hi!"]                       say something in chat after 4 s
        [--share-house <library folder id>]   share that saved lot (Documents/inZOI/Creations/MySites/<id>)
        [--still]                            stand where 'moves' is now and turn 90 degrees every 6 s (facing test)
"""
import argparse
import asyncio
import json
import re
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
from game import Game, GameError, describe_act  # noqa: E402
from houses import pack  # noqa: E402
from profile import read_profile  # noqa: E402

KEY_FILE = Path(__file__).with_name(".fake_player_key")


async def main(a):
    game = Game()
    key = KEY_FILE.read_text().strip() if KEY_FILE.exists() else secrets.token_urlsafe(24)
    KEY_FILE.write_text(key)
    profile = await asyncio.to_thread(read_profile, game.call, a.look)
    reader, writer = await asyncio.open_connection(a.server, a.port, limit=32 * 1024 * 1024)

    async def send(m):
        writer.write((json.dumps(m) + "\n").encode())
        await writer.drain()

    version = re.search(r'^VERSION = "(.+)"', (ROOT / "client" / "companion.py").read_text("utf-8"), re.M).group(1)
    await send({"t": "hello", "protocol": 1, "key": key, "name": a.name, "password": a.password, "version": version})
    welcome = json.loads(await reader.readline())
    me = welcome.get("id")
    others = [p["id"] for p in welcome.get("players", [])]
    print("fake player joined as", me, welcome.get("error", ""), "- others:", others, flush=True)
    await send({"t": "profile", "profile": profile})
    if a.share_house:
        house = pack(a.share_house)
        house.update(name=f"{a.name}'s test house", by=a.name)
        await send({"t": "house_put", "house": house})
        print("shared house", a.share_house, flush=True)
    print(f"shared look of {profile['first_name']} {profile['last_name']}; streaming entity {a.moves}", flush=True)

    async def drain():
        while line := await reader.readline():
            m = json.loads(line)
            if m.get("t") == "joined":
                others.append(m["player"]["id"])
            elif m.get("t") == "chat":
                print(f"[chat] {m['name']}: {m['text']}", flush=True)

    asyncio.create_task(drain())
    start, n, act, anim, outfit, cache = time.time(), 0, None, None, None, {}
    spot = await asyncio.to_thread(game.state_of, a.moves) if a.still else None
    while True:
        try:
            if spot:
                turned = spot["yaw"] + 90 * int((time.time() - start) // 6)
                st = {**spot, "yaw": round((turned + 180) % 360 - 180, 1)}
            else:
                st = await asyncio.to_thread(game.state_of, a.moves)
            if a.offset:
                st = {**st, "x": st["x"] + a.offset * 100}        # (game units are cm)
            n += 1
            if n % 2 == 0 and not spot:
                act_raw = await asyncio.to_thread(game.activity, a.moves)
                act = await asyncio.to_thread(describe_act, game, act_raw, a.moves, lambda _: None, cache)
                anim = act_raw["anim"]["id"] if act_raw.get("anim") else None
            if n % 10 == 1:
                outfit = await asyncio.to_thread(game.outfit, a.moves)
            age = time.time() - start
            if a.social and others and 6 < age < 16:
                act = {"bag": a.social, "player": others[0]}        # "my Zoi is greeting yours"
            if a.chat and 4 < age and not getattr(main, "said", False):
                main.said = True
                await send({"t": "chat", "text": a.chat})
            await send({"t": "state", "state": {"n": n, **st, "act": act, "anim": anim, "outfit": outfit}})
        except GameError as e:
            print("game:", e, flush=True)
        if a.seconds and time.time() - start > a.seconds:
            writer.close()
            print("fake player left", flush=True)
            return
        await asyncio.sleep(0.2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--look", type=int, required=True, help="entity whose profile this fake player shares")
    ap.add_argument("--moves", type=int, required=True, help="entity whose state it streams")
    ap.add_argument("--server", default="nox.local")
    ap.add_argument("--port", type=int, default=24242)
    ap.add_argument("--name", default="Fake Friend")
    ap.add_argument("--password", default="")
    ap.add_argument("--social", help="interaction bag to 'greet' the other player with, 6-16 s in")
    ap.add_argument("--chat", help="chat line to send 4 s in")
    ap.add_argument("--share-house", help="library folder id of a saved lot to share")
    ap.add_argument("--still", action="store_true", help="stand still and turn 90 degrees every 6 s")
    ap.add_argument("--offset", type=float, default=0, help="metres to the side of 'moves' (stream your own Zoi)")
    ap.add_argument("--seconds", type=float, default=0, help="leave after this many seconds (0: never)")
    asyncio.run(main(ap.parse_args()))
