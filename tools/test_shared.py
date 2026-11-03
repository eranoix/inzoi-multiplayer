"""The server's shared state (server/inzoi_mp_server.py: on_set) with real connections:
- a "set" gets the next version and reaches everyone, the sender too (its confirmation)
- a change made on an older version ("base") is refused with the current one
- a newcomer gets the whole state in its welcome; a server restarted on the same data still has it
- the latest skeleton frames of each player reach a newcomer at once; a player who leaves takes theirs along

    python tools/test_shared.py
"""
import asyncio
import json
import secrets
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import inzoi_mp_server as mp  # noqa: E402

DATA = Path(tempfile.mkdtemp())


async def start():
    server = mp.Server(mp.Store(DATA))
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
    return server, srv, srv.sockets[0].getsockname()[1]


class C:
    def __init__(self, name):
        self.name, self.key, self.msgs = name, secrets.token_urlsafe(24), []

    async def connect(self, port):
        self.r, self.w = await asyncio.open_connection("127.0.0.1", port, limit=mp.MAX_LINE)
        await self.send({"t": "hello", "protocol": mp.PROTOCOL, "key": self.key, "name": self.name, "version": "t"})
        self.welcome = json.loads(await self.r.readline())
        self.task = asyncio.create_task(self.read())
        return self

    async def read(self):
        while line := await self.r.readline():
            self.msgs.append(json.loads(line))

    async def send(self, m):
        self.w.write((json.dumps(m) + "\n").encode())
        await self.w.drain()

    async def wait(self, check, timeout=5):
        for _ in range(int(timeout / 0.02)):
            hit = next((m for m in self.msgs if check(m)), None)
            if hit:
                return hit
            await asyncio.sleep(0.02)
        raise AssertionError(f"{self.name}: nothing matched; got {[m.get('t') for m in self.msgs]}")


async def main():
    server, srv, port = await start()
    ana, ben = await (C("Ana")).connect(port), await (C("Ben")).connect(port)
    assert ana.welcome["shared"] == {} and ana.welcome["seq"] == 0

    await ana.send({"t": "set", "key": "house:1038:live", "value": {"objects": 3}, "ref": "r1"})
    a = await ana.wait(lambda m: m.get("t") == "shared" and m["key"] == "house:1038:live")
    b = await ben.wait(lambda m: m.get("t") == "shared" and m["key"] == "house:1038:live")
    assert a["v"] == b["v"] == 1 and a["ref"] == "r1" and b["item"]["value"] == {"objects": 3}
    assert b["item"]["by"] == ana.welcome["id"] and b["item"]["by_name"] == "Ana"
    print("a change gets a version and reaches everyone, the sender too: ok")

    await ben.send({"t": "set", "key": "house:1038:live", "value": {"objects": 4}, "base": 1})
    await ben.wait(lambda m: m.get("t") == "shared" and m.get("v") == 2)
    await ana.send({"t": "set", "key": "house:1038:live", "value": {"objects": 9}, "base": 1, "ref": "stale"})
    refused = await ana.wait(lambda m: m.get("t") == "set_refused")
    assert refused["ref"] == "stale" and refused["current"]["v"] == 2 and refused["current"]["value"] == {"objects": 4}
    assert server.shared["house:1038:live"]["value"] == {"objects": 4}
    print("a change made on an older version is refused, with the current one: ok")

    await ana.send({"t": "set", "key": "Bad Key!", "value": 1, "ref": "x"})
    assert (await ana.wait(lambda m: m.get("t") == "error" and m.get("ref") == "x"))["code"] == "bad_key"
    await ana.send({"t": "set", "key": "family", "value": {"together": True}})
    await ben.wait(lambda m: m.get("t") == "shared" and m["key"] == "family")
    cat = await (C("Cat")).connect(port)
    assert set(cat.welcome["shared"]) == {"house:1038:live", "family"} and cat.welcome["seq"] == 3
    print("a newcomer gets the whole shared state in its welcome: ok")

    # skeleton frames: the latest of each player reaches a newcomer at once
    await ana.send({"t": "event", "kind": "pose", "data": {"k": "1|0,0,0,0|1|0|1,2,3,4,5,6,7"}})
    await ana.send({"t": "event", "kind": "pose", "data": {"d": "1|5|0,0,0,0|"}})
    await ben.wait(lambda m: m.get("kind") == "pose" and "d" in m["data"])
    dan = await (C("Dan")).connect(port)
    k = await dan.wait(lambda m: m.get("kind") == "pose" and "k" in m["data"])
    d = await dan.wait(lambda m: m.get("kind") == "pose" and "d" in m["data"])
    assert k["from"] == ana.welcome["id"] and d["data"]["d"].startswith("1|5|")
    await ana.send({"t": "bye"})
    ana.task.cancel()
    ana.w.close()
    await asyncio.sleep(0.5)
    assert ana.welcome["id"] not in server.last_pose
    print("the latest skeleton frames reach a newcomer; a player who leaves takes theirs along: ok")

    # removing a key, and the state surviving a restart
    await ben.send({"t": "set", "key": "family", "value": None})
    await cat.wait(lambda m: m.get("t") == "shared" and m["key"] == "family" and m["item"] is None)
    for c in (ben, cat, dan):
        c.task.cancel()
        c.w.close()
    srv.close()
    server2, srv2, port2 = await start()
    assert set(server2.shared) == {"house:1038:live"} and server2.seq == 4
    eve = await (C("Eve")).connect(port2)
    assert eve.welcome["shared"]["house:1038:live"]["value"] == {"objects": 4}
    eve.task.cancel()
    srv2.close()
    print("a removed key is gone; the shared state survives a server restart: ok")
    await family_reconcile()
    print("shared state tests passed")


async def family_reconcile():
    """The shared family says Ana and Ben live together; Ben's save doesn't: his program makes it so, into the head's
    home (Ana is the head), at most every 2 minutes; once his save matches, nothing more is done."""
    import os
    os.environ["APPDATA"] = str(DATA / "appdata")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
    import companion
    ben = companion.Companion({"key": "B" * 20, "name": "Ben", "server": "127.0.0.1", "port": 1, "password": ""})
    ben.save_cfg = lambda: None
    ben.me_id, ben.writer, ben.world, ben.my_entity = "pben", object(), "play", 20
    ben.mp_active = lambda: True
    r = companion.Remote("pana", "Ana") if hasattr(companion, "Remote") else None
    ben.remotes["pana"] = r
    r.hidden = False
    ben.puppet_ids["pana"] = 88
    calls, state = [], {"together": False}
    ben.family_together = lambda rr, fam: state["together"]
    ben.family_apply = lambda action, pid, home=None: calls.append((action, pid, home)) or {"ok": True}
    ben.on_shared_snapshot({"family": {"key": "family", "v": 7, "by": "pana", "by_name": "Ana",
                                       "value": {"together": True, "head": "pana", "home_site": 1038,
                                                 "members": ["pana", "pben"]}}}, 7)
    await ben.reconcile_family()
    assert calls == [("movein", "pana", 1038)], calls
    await ben.reconcile_family()
    assert len(calls) == 1, "tried again within 2 minutes"
    ben.family_tries.clear()
    state["together"] = True
    await ben.reconcile_family()
    assert len(calls) == 1
    print("the shared family is brought into a save that doesn't match it (once, into the head's home): ok")


if __name__ == "__main__":
    asyncio.run(main())
