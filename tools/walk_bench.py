"""A test bench for the live sync: a stand-in player walks a known path (back and forth between two points at a set
speed, 10 positions a second with the sender's clock, like the real program) while this game's copy of its Zoi is
measured against where it really is at every moment.

    python tools/walk_bench.py --password <server password> --look <entity> --from X,Y,Z --to X,Y,Z
        [--speed 200] [--seconds 40] [--pause 2]

Prints the distance between their Zoi here and the true position (while walking, while standing) and the delay that
explains it best.
"""
import argparse
import asyncio
import json
import math
import re
import secrets
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
from game import Game  # noqa: E402
from profile import read_profile  # noqa: E402


def path_at(t, a, b, speed, pause):
    """Where the walker is t s in: a -> b, pause, b -> a, pause, ... ; and whether it's walking."""
    length = math.dist(a[:2], b[:2])
    leg = length / speed
    cycle = 2 * (leg + pause)
    u = t % cycle
    if u < leg:
        k, walking, yaw = u / leg, True, math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    elif u < leg + pause:
        k, walking, yaw = 1.0, False, math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    elif u < 2 * leg + pause:
        k, walking, yaw = 1 - (u - leg - pause) / leg, True, math.degrees(math.atan2(a[1] - b[1], a[0] - b[0]))
    else:
        k, walking, yaw = 0.0, False, math.degrees(math.atan2(a[1] - b[1], a[0] - b[0]))
    return [a[i] + (b[i] - a[i]) * k for i in range(3)], walking, yaw


async def main(o):
    game = Game()
    a = [float(v) for v in o.frm.split(",")]
    b = [float(v) for v in o.to.split(",")]
    me = game.possessed()
    city = game.state_of(me)["city"]
    profile = await asyncio.to_thread(read_profile, game.call, o.look)
    r, w = await asyncio.open_connection("127.0.0.1", o.port, limit=64 * 1024 * 1024)

    async def send(m):
        w.write((json.dumps(m) + "\n").encode())
        await w.drain()
    version = re.search(r'^VERSION = "(.+)"', (ROOT / "client" / "companion.py").read_text("utf-8"), re.M).group(1)
    await send({"t": "hello", "protocol": 1, "key": secrets.token_urlsafe(24), "name": "Bancada", "password": o.password,
                "version": version})
    welcome = json.loads(await r.readline())
    my_id = welcome["id"]
    await send({"t": "profile", "profile": profile})

    async def drain():
        while await r.readline():
            pass
    asyncio.create_task(drain())
    start, n = time.time(), 0
    truth = []                    # (t, x, y, walking)
    samples = []                  # (t, x, y)
    zoi = None
    log_path = Path(__import__("os").environ["APPDATA"]) / "inzoi-mp" / "companion.log"

    async def stream():
        nonlocal n
        while time.time() - start < o.seconds + 8:
            t = time.time() - start
            pos, walking, yaw = path_at(max(0.0, t - 6), a, b, o.speed, o.pause)   # (6 s standing first: spawn)
            n += 1
            truth.append((time.time(), pos[0], pos[1], walking))
            await send({"t": "state", "state": {"n": n, "x": round(pos[0], 1), "y": round(pos[1], 1),
                                                "z": round(pos[2], 1), "yaw": round(yaw, 1), "city": city, "site": -1,
                                                "act": None, "anim": None, "outfit": None, "ts": round(time.time(), 3)}})
            await asyncio.sleep(0.1)
    task = asyncio.create_task(stream())
    while time.time() - start < o.seconds + 8:
        if zoi is None and time.time() - start > 3:
            m = re.findall(rf"spawned {my_id} as (\d+)", log_path.read_text("utf-8", errors="replace"))
            zoi = int(m[-1]) if m else None
        if zoi and time.time() - start > 7:
            try:
                s = await asyncio.to_thread(game.state_of, zoi)
                samples.append((time.time(), s["x"], s["y"]))
            except Exception:                            # noqa: BLE001
                pass
        await asyncio.sleep(0.1)
    await task
    await send({"t": "bye"})
    w.close()
    if not samples:
        print("their Zoi never showed up here", zoi)
        return

    def where(t):
        best = None
        for e in truth:
            if e[0] <= t:
                best = e
            else:
                break
        return best
    walk_e, stand_e = [], []
    for t, x, y in samples:
        e = where(t)
        if e:
            (walk_e if e[3] else stand_e).append(math.dist((x, y), (e[1], e[2])))
    for name, xs in (("walking", walk_e), ("standing", stand_e)):
        if xs:
            print(f"distance {name}: median {statistics.median(xs):.0f} cm, p95 {sorted(xs)[int(len(xs) * 0.95)]:.0f} cm,"
                  f" max {max(xs):.0f} cm ({len(xs)} samples)")
    best = None
    for d in [i * 0.05 for i in range(0, 41)]:
        es = [math.dist((x, y), (e[1], e[2])) for t, x, y in samples if (e := where(t - d))]
        if es and (not best or statistics.median(es) < best[1]):
            best = (d, statistics.median(es))
    print(f"their Zoi matches where they were {best[0]:.2f} s earlier (median {best[1]:.0f} cm off then)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", default="")
    ap.add_argument("--port", type=int, default=24242)
    ap.add_argument("--look", type=int, required=True)
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--speed", type=float, default=200)
    ap.add_argument("--pause", type=float, default=2)
    ap.add_argument("--seconds", type=float, default=40)
    asyncio.run(main(ap.parse_args()))
