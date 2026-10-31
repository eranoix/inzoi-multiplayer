"""Measure the live sync with a real partner: how far behind and how far away their Zoi in this game is from where
they really are. Joins the local server as an observer ("Observador", no Zoi of its own), records the partner's
states as they arrive, and at the same time reads where their Zoi is in this game.

    python tools/measure_sync.py --password <server password> [--seconds 60] [--name Chey]

Prints: states per second and the gaps between them, the age of each state on arrival (needs the two PCs' clocks to
agree), the distance between their Zoi here and their newest position (now, while walking and while standing), and
the delay that best explains it (their Zoi here vs. their path some time earlier).
"""
import argparse
import asyncio
import json
import math
import secrets
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
from game import Game  # noqa: E402


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


async def main(a):
    game = Game()
    members = game.call("household.get_members").get("members") or []
    r, w = await asyncio.open_connection("127.0.0.1", a.port, limit=32 * 1024 * 1024)
    w.write((json.dumps({"t": "hello", "protocol": 1, "key": secrets.token_urlsafe(24), "name": "Observador",
                         "password": a.password, "version": "0.0.0"}) + "\n").encode())
    await w.drain()
    welcome = json.loads(await r.readline())
    target = next((p for p in welcome.get("players", []) if p.get("name") == a.name), None)
    if not target:
        print("partner not on the server:", [p.get("name") for p in welcome.get("players", [])])
        return
    prof = (target.get("profile") or {})
    zoi = a.zoi or next((m["entity_id"] for m in members if m.get("first_name") == prof.get("first_name")
                         and m.get("last_name") == prof.get("last_name")), None)
    print(f"watching {a.name} ({prof.get('first_name')} {prof.get('last_name')}), their Zoi here: {zoi}")
    states = []          # (arrival, ts, x, y, act)
    samples = []         # (t, x, y)

    async def listen():
        while True:
            line = await r.readline()
            if not line:
                return
            m = json.loads(line)
            if m.get("t") == "state" and m.get("id") == target["id"]:
                st = m["state"]
                if "x" in st:
                    states.append((time.time(), st.get("ts"), st["x"], st["y"], (st.get("act") or {}).get("bag")))

    task = asyncio.create_task(listen())
    end = time.time() + a.seconds
    while time.time() < end:
        try:
            s = await asyncio.to_thread(game.state_of, zoi)
            samples.append((time.time(), s["x"], s["y"]))
        except Exception:                                # noqa: BLE001
            pass
        await asyncio.sleep(0.1)
    task.cancel()
    w.write(b'{"t":"bye"}\n')
    await w.drain()
    w.close()

    if len(states) < 5 or len(samples) < 5:
        print("not enough data", len(states), len(samples))
        return
    arr = [s[0] for s in states]
    gaps = [b - a for a, b in zip(arr, arr[1:])]
    print(f"states: {len(states) / a.seconds:.1f}/s, gap median {statistics.median(gaps) * 1000:.0f} ms, "
          f"p95 {sorted(gaps)[int(len(gaps) * 0.95)] * 1000:.0f} ms, max {max(gaps) * 1000:.0f} ms")
    ages = [s[0] - s[1] for s in states if s[1]]
    if ages:
        print(f"age on arrival (clock-dependent): median {statistics.median(ages) * 1000:.0f} ms")
    # their path, by arrival time; where they were at time t (newest state at or before t)
    def where(t):
        best = None
        for s in states:
            if s[0] <= t:
                best = s
            else:
                break
        return (best[2], best[3]) if best else None

    def speed_at(t):
        prev = [s for s in states if t - 0.6 <= s[0] <= t]
        if len(prev) < 2:
            return 0
        return dist((prev[0][2], prev[0][3]), (prev[-1][2], prev[-1][3])) / max(0.01, prev[-1][0] - prev[0][0])
    moving, still, alle = [], [], []
    for t, x, y in samples:
        p = where(t)
        if not p:
            continue
        e = dist((x, y), p)
        alle.append(e)
        (moving if speed_at(t) > 40 else still).append(e)
    for name, xs in (("all", alle), ("walking", moving), ("standing", still)):
        if xs:
            print(f"distance {name}: median {statistics.median(xs):.0f} cm, p95 {sorted(xs)[int(len(xs) * 0.95)]:.0f} cm, "
                  f"max {max(xs):.0f} cm  ({len(xs)} samples)")
    best = None
    for d in [i * 0.1 for i in range(0, 31)]:
        es = [dist((x, y), p) for t, x, y in samples if (p := where(t - d))]
        if es:
            m = statistics.median(es)
            if not best or m < best[1]:
                best = (d, m)
    print(f"their Zoi here matches where they were {best[0]:.1f} s earlier (median {best[1]:.0f} cm off then)")
    spd = [v for v in (speed_at(s[0]) for s in states) if v > 40]
    if spd:
        print(f"their speed while moving: median {statistics.median(spd):.0f} cm/s, p90 {sorted(spd)[int(len(spd) * 0.9)]:.0f} cm/s")
    acts = [s[4] for s in states if s[4]]
    print("actions seen:", sorted(set(acts))[:12])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", default="")
    ap.add_argument("--port", type=int, default=24242)
    ap.add_argument("--name", default="Chey")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--zoi", type=int, help="their Zoi's entity here (default: the family member with their name)")
    asyncio.run(main(ap.parse_args()))
