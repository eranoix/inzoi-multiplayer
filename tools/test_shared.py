"""The server's shared state (server/inzoi_mp_server.py: on_set) with real connections:
- a "set" gets the next version and reaches everyone, the sender too (its confirmation)
- a change made on an older version ("base") is refused with the current one
- a newcomer gets the whole state in its welcome; a server restarted on the same data still has it
- the latest skeleton frames of each player reach a newcomer at once; a player who leaves takes theirs along
and the companion's side of it (client/shared.py):
- share(wait=True) is True only once the server's broadcast of that very set came back: False when refused, not
  connected, swallowed on the way; a set the server got whose answer went down with the connection is confirmed by
  the next welcome, one that went down with it is reported lost at once
- a partner's version that the server ordered before my set still on its way is held back (superseded)
- the versions already applied belong to one server and one life of its shared state: switching servers (or the
  server's data starting over, or its versions going back) never ignores its changes, and coming back to a server
  doesn't apply its old state again; a config from before keeps nothing it can't vouch for
- the shared family: brought into a save that doesn't match it a few times, each after a longer wait, then left
  alone until it changes; another family living on the head's lot in this save counts as done (never retried)
- a family request I asked for: the partner's "it didn't work in my save" stays, even when it comes while my own
  save is still being changed (client/couple.py couple_perform)

    python tools/test_shared.py
"""
import asyncio
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

DATA = Path(tempfile.mkdtemp())
os.environ["APPDATA"] = str(DATA / "appdata")            # (before any client module: never the real config)
os.environ["INZOI_MP_DOCUMENTS"] = str(DATA / "docs")    # (couple.py imports saves.py: never the game's folders)
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "client")]
import couple  # noqa: E402
import inzoi_mp_server as mp  # noqa: E402
import shared  # noqa: E402


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


class Tele:
    def rec(self, k, **d):
        pass


class Net(shared.SharedState):
    """A companion's shared-state half on a real connection (what companion.py does with these messages); a house
    version is "applied" the way housesync.py does it (seen versions; my own only marked)."""

    def __init__(self, name, key=None, cfg=None):
        self.name, self.key = name, key or secrets.token_urlsafe(24)
        self.cfg, self.tele = cfg if cfg is not None else {}, Tele()
        self.writer = self.me_id = None
        self.gate = asyncio.Event()
        self.gate.set()
        self.houses, self.held = [], []                    # house versions applied; (v, superseded) as they came
        self.shared_init()

    def save_cfg(self):
        pass

    async def send(self, msg):
        if self.writer:
            self.writer.write((json.dumps(msg) + "\n").encode())
            await self.writer.drain()

    async def connect(self, port):
        r, w = await asyncio.open_connection("127.0.0.1", port, limit=mp.MAX_LINE)
        self.writer = w
        await self.send({"t": "hello", "protocol": mp.PROTOCOL, "key": self.key, "name": self.name, "version": "t"})
        m = json.loads(await r.readline())
        self.me_id, self.cfg["server_id"] = m["id"], (m.get("server") or {}).get("id")
        self.on_shared_snapshot(m.get("shared"), m.get("seq") or 0, m.get("epoch"))
        self.reader = asyncio.create_task(self.read(r, w))
        return self

    async def read(self, r, w):
        try:
            while line := await r.readline():
                await self.gate.wait()
                m = json.loads(line)
                t = m.get("t")
                if t == "shared":
                    self.on_shared_msg(m)
                elif t == "shared_all":
                    self.on_shared_snapshot(m.get("shared"), m.get("seq") or 0, m.get("epoch"))
                elif t == "set_refused":
                    self.on_shared_refused(m)
                elif t == "error" and m.get("ref"):
                    self.on_shared_error(m)
                elif t == "ping":
                    await self.send({"t": "pong"})
        except (OSError, ConnectionError):
            pass
        if self.writer is w:
            self.writer = self.me_id = None

    async def drop(self):
        w, self.writer, self.me_id = self.writer, None, None
        self.reader.cancel()
        if w:
            w.close()
        await asyncio.sleep(0.05)

    def on_shared_house_live(self, key, item, snapshot):
        if not item:
            return
        v = int(item["v"])
        self.held.append((v, self.superseded(key, item)))
        if item.get("by") == self.me_id:
            self.mark_seen(key, v)
            return
        if self.seen(key) >= v:
            return
        self.houses.append((key, v, item.get("by")))
        self.mark_seen(key, v)

    def on_shared_house_exact(self, *a): pass
    def on_shared_object(self, *a): pass


async def until(check, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if check():
            return True
        await asyncio.sleep(0.02)
    return False


async def server_at(folder=None):
    server = mp.Server(mp.Store(folder or Path(tempfile.mkdtemp())))
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
    return server, srv, srv.sockets[0].getsockname()[1]


async def confirmations():
    """#24: share() confirms on the server's broadcast of that very set, not on bytes written."""
    server, srv, port = await server_at()
    ana, ben = await Net("Ana").connect(port), await Net("Ben").connect(port)
    key = "house:1038:live"
    assert await ana.share(key, {"n": 1}, wait=True)
    assert ana.shared[key]["by"] == ana.me_id and ana.shared[key]["value"] == {"n": 1}, "True before the confirmation"
    assert await ana.share(key, {"n": 2})                  # (no wait: True once sent...)
    assert ana.superseded(key)                             # (...and on its way until its broadcast comes back)
    assert await until(lambda: not ana.superseded(key) and ana.shared[key]["value"] == {"n": 2})
    assert await until(lambda: ben.shared.get(key, {}).get("v") == ana.shared[key]["v"])
    assert not await ben.share(key, {"n": 9}, base=1, wait=True), "a change made on an older version was confirmed"
    assert await ben.share_ex(key, {"n": 9}, base=1) == "refused" and ben.shared[key]["value"] == {"n": 2}
    assert await ben.share_ex("Bad Key!", 1) == "rejected"
    assert not await Net("Off").share(key, 1, wait=True) and await Net("Off").share_ex(key, 1) == "offline"
    print("share(wait=True): True on the server's broadcast of it; False when refused, rejected or not connected: ok")

    shared.CONFIRM_WITHIN = 0.3
    real_send = ana.send

    async def void(msg):                                   # (written, but the link died: it never arrives)
        pass
    ana.send = void
    t0 = time.time()
    assert not await ana.share(key, {"n": 3}, wait=True) and time.time() - t0 < 3
    assert await ana.share_ex(key, {"n": 3}) == "lost" and not ana.shared_out and not ana.superseded(key)
    ana.send = real_send
    shared.CONFIRM_WITHIN = 10.0
    print("a set swallowed on the way: not confirmed (False) after the wait: ok")

    # the server got it, but its answer went down with the connection: the next welcome confirms it
    ana.gate.clear()
    seq = server.seq
    task = asyncio.create_task(ana.share(key, {"n": 4}, wait=True))
    assert await until(lambda: server.seq == seq + 1)
    await ana.drop()
    ana.gate.set()
    t0 = time.time()
    await ana.connect(port)
    assert await asyncio.wait_for(task, 3) is True and time.time() - t0 < 3
    # it went down with the connection (never arrived): the next welcome says so at once (not after 10 s)
    ana.send = void
    task = asyncio.create_task(ana.share(key, {"n": 5}, wait=True))
    await asyncio.sleep(0.1)
    ana.send = real_send
    await ana.drop()
    t0 = time.time()
    await ana.connect(port)
    assert await asyncio.wait_for(task, 3) is False and time.time() - t0 < 3 and not ana.shared_out
    print("after a reconnect: a set the server has is confirmed, one it never got is lost, both at once: ok")

    # a partner's version ordered before my set still on its way: held back (superseded); my own then counts
    ana.held.clear()
    ana.gate.clear()
    assert await ben.share(key, {"n": 6}, wait=True)
    assert await ana.share(key, {"n": 7})
    ana.gate.set()
    assert await until(lambda: not ana.shared_out)
    v_ben, v_ana = ana.shared[key]["v"] - 1, ana.shared[key]["v"]
    assert ana.held == [(v_ben, True), (v_ana, False)], ana.held
    assert ana.shared[key]["value"] == {"n": 7}
    print("a partner's version ordered before my set on its way is held back for mine: ok")
    for c in (ana, ben):
        await c.drop()
    srv.close()


async def scopes():
    """#4: the seen versions are per server and per life of its shared state."""
    (a, srv_a, pa), (b, srv_b, pb) = await server_at(), await server_at()
    key = "house:1038:live"
    ana_a, ana_b = await Net("Ana", "A" * 24).connect(pa), await Net("Ana", "A" * 24).connect(pb)
    ben = await Net("Ben", "B" * 24).connect(pa)
    for i in range(59):
        assert await ana_a.share("family", {"n": i}, wait=True)
    assert await ana_a.share(key, {"objects": "A's"}, wait=True)
    assert await until(lambda: ben.houses == [(key, 60, ana_a.me_id)]), ben.houses
    await ben.drop()
    await ben.connect(pb)                                   # another server: its v1 is new here
    assert await ana_b.share(key, {"objects": "B's"}, wait=True)
    assert await until(lambda: len(ben.houses) == 2), "server B's v1 was ignored (A's v60 was seen)"
    assert ben.houses[1] == (key, 1, ana_b.me_id)
    await ben.drop()
    await ben.connect(pa)                                   # back on A: its house (v60) was applied already
    await asyncio.sleep(0.1)
    assert len(ben.houses) == 2, f"A's old house was applied again: {ben.houses}"
    restarted = await Net("Ben", "B" * 24, cfg=json.loads(json.dumps(ben.cfg))).connect(pa)
    assert not restarted.houses, "a restart applied it again"
    print("seen versions per server: B's v1 applied after A's v60, A's not again when back (nor after a restart): ok")

    item = dict(a.shared[key])
    ben.on_shared_snapshot({key: item}, 60, "0123456789abcdef")         # (A's data started over: a new epoch)
    assert len(ben.houses) == 3, "a new epoch kept the old seen versions"
    ben.on_shared_snapshot({key: dict(item, v=5)}, 5, "0123456789abcdef")    # (same epoch, but its versions went back)
    assert ben.houses[-1][1] == 5, "versions that went back were ignored"
    ben.mark_seen("obj:1038:x", 9)
    assert ben.seen("obj:1038:x") == 9 and "obj:1038:x" not in ben.cfg["shared_seen"]
    ben.on_shared_snapshot({}, 9, "fedcba9876543210")
    assert ben.seen("obj:1038:x") == 0, "object versions survived a new epoch"
    old = Net("Old", cfg={"server_id": ben.cfg["server_id"], "shared_seen": {key: 99}})
    old.me_id = "pold"
    old.on_shared_snapshot({key: dict(item, v=3)}, 3, None)
    assert old.houses == [(key, 3, item["by"])] and old.cfg["shared_seen_scope"], "a config from before was trusted"
    print("seen versions dropped when the server's data starts over or its versions go back; old configs: ok")
    for c in (ana_a, ana_b, ben, restarted):
        await c.drop()
    srv_a.close()
    srv_b.close()


class FamilyPC(shared.SharedState):
    """Ben's program, as far as the shared family goes (the game's answers are stood in for by each case)."""

    def __init__(self):
        self.cfg, self.tele, self.remotes, self.puppet_ids = {}, Tele(), {}, {}
        self.me_id, self.writer, self.world, self.my_entity, self.leader = "pben", object(), "play", 20, None
        self.shared_init()

    def save_cfg(self):
        pass

    def mp_active(self):
        return True

    async def g(self, fn, *a):
        return await asyncio.to_thread(fn, *a)

    def on_shared_house_live(self, *a): pass
    def on_shared_house_exact(self, *a): pass
    def on_shared_object(self, *a): pass


class Clock:
    now = 1000.0

    @classmethod
    def time(cls):
        return cls.now


async def family_reconcile():
    """The shared family says Ana and Ben live together; Ben's save doesn't: his program makes it so, into the head's
    home (Ana is the head), with a longer wait each time and a few tries at most; once his save matches, nothing more
    is done (#34: another family on Ana's lot in Ben's save counts as matching - never retried every 2 minutes)."""
    ben = FamilyPC()
    ben.remotes["pana"] = types.SimpleNamespace(pid="pana", name="Ana", hidden=False, profile=None)
    ben.puppet_ids["pana"] = 88
    calls, checks, state = [], [], {"together": False, "answer": {"ok": True}}
    ben.family_together = lambda rr, fam: checks.append(1) or state["together"]
    ben.family_apply = lambda action, pid, home=None: calls.append((action, pid, home)) or state["answer"]
    shared.time = Clock

    def family(v, home=1038):
        ben.on_shared_snapshot({"family": {"key": "family", "v": v, "by": "pana", "by_name": "Ana",
                                           "value": {"together": True, "head": "pana", "home_site": home,
                                                     "members": ["pana", "pben"]}}}, v)

    async def at(dt):
        Clock.now += dt
        await ben.reconcile_family()

    family(7)
    await at(0)
    assert calls == [("movein", "pana", 1038)], calls
    await at(60)
    assert len(calls) == 1, "tried again within 2 minutes"
    for n, dt in enumerate((120, 240, 480, 960), 2):        # (60 s of the first wait went by above)
        await at(dt - (60 if n == 2 else 0) - 2)
        assert len(calls) == n - 1, f"try {n} came too soon"
        await at(3)
        assert len(calls) == n, f"try {n} didn't come"
    for dt in (1801, 1801, 100000):
        await at(dt)
    assert len(calls) == 5, f"never stops trying: {len(calls)}"
    print("a save that won't match: tried again after 2, 4, 8, 16 min, then left alone: ok")

    family(8)                                               # (the family changed: worth trying again, at once)
    await at(1)
    assert len(calls) == 6
    state["together"], n = True, len(checks)
    await at(500)
    await at(60)
    await at(60)
    assert len(calls) == 6 and len(checks) == n + 1, "a matching save is checked every 5 s (pages the game)"
    print("a new version of the family is tried at once; a matching save isn't re-checked all the time: ok")

    state["together"], state["answer"] = False, {"error": "the game asks", "code": "asked_game"}
    family(9)
    await at(1)
    await at(shared.FAMILY_RETRY + 1)
    assert len(calls) == 7, "the game's question was asked again after 2 minutes"
    await at(shared.FAMILY_RETRY_MAX - shared.FAMILY_RETRY)
    assert len(calls) == 8
    print("the game asked the player on screen: not asked again for half an hour: ok")

    # the real check: on Ana's lot in Ben's save lives another family (not hers): family_apply put Ana's Zoi in Ben's
    # household instead - that's as together as it gets, so it isn't tried again
    del ben.family_together
    zoi = {"eid": None}

    def apply(action, pid, home=None):
        calls.append((action, pid, home))
        zoi["eid"] = 88
        return {"ok": True}
    ben.family_apply, ben.family_zoi = apply, lambda rr: zoi["eid"]
    ben.my_home_site, ben.household_at = lambda: 2000, lambda site: 5 if int(site) == 1038 else None
    ben.my_household = lambda: {"household_id": 1}
    ben.has_member = lambda hh, rr: False
    family(10)
    await at(1)
    assert len(calls) == 9 and zoi["eid"] == 88
    for dt in (121, 241, 3600):
        await at(dt)
    assert len(calls) == 9, "strangers on the head's lot: retried forever"
    ben.has_member = lambda hh, rr: True                    # (but if that household is hers: we should move in)
    family(11)
    await at(1)
    assert len(calls) == 10, "the household on the head's lot is hers, yet nothing was done"
    print("another family on the head's lot counts as together (no endless retries); hers doesn't: ok")
    shared.time = time


async def family_result_race():
    """I asked Ben to move in, he said yes and his save refused it; his "it didn't work" comes while my own save is
    still being changed (my home being looked up to share the family): it stays failed, it's not overwritten."""
    entered, release = threading.Event(), threading.Event()
    shared_calls = []

    class Asker(couple.Couple):
        me_id, leader, remotes, chat = "pa", None, {}, []

        async def g(self, fn, *a, **kw):
            return await asyncio.to_thread(fn, *a, **kw)

        def family_apply(self, action, pid, home=None):
            return {"ok": True}

        def my_home_site(self):
            entered.set()
            release.wait(5)
            return 1038

        async def share_family(self, home_site, members):
            shared_calls.append((home_site, sorted(members)))

        def system_line(self, text):
            self.chat.append(text)

    ana = Asker()
    ana.sent = {"r1": {"id": "r1", "to": "Ben", "to_id": "pb", "action": "movein", "at": time.time(),
                       "state": "accepted", "message": ""}}
    perform = asyncio.create_task(ana.couple_perform(ana.sent["r1"], initiator=True))
    for _ in range(250):                                     # (my save is changed; then the home is looked up)
        if entered.is_set() or perform.done():
            break
        await asyncio.sleep(0.02)
    ana.on_couple_event({"t": "event", "from": "pb", "data": {"type": "result", "id": "r1", "ok": False,
                                                              "message": "the game asks on screen"}})
    release.set()
    await asyncio.wait_for(perform, 5)
    for _ in range(250):
        if shared_calls:
            break
        await asyncio.sleep(0.02)
    s = ana.sent["r1"]
    assert s["state"] == "failed" and s["message"] == "the game asks on screen", s
    assert any("didn't work in their save" in c for c in ana.chat), ana.chat
    assert shared_calls == [(1038, ["pa", "pb"])], shared_calls
    print("a family request: the partner's 'it didn't work' that comes while my save is being changed stays: ok")


async def main_all():
    await main()
    await confirmations()
    await scopes()
    await family_reconcile()
    await family_result_race()
    print("shared state tests passed")


if __name__ == "__main__":
    asyncio.run(main_all())
