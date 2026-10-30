"""End-to-end test of the server with two fake players. Run: python server/test_server.py"""
import asyncio
import json
import secrets
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inzoi_mp_server as mp  # noqa: E402


class Fake:
    def __init__(self, port, name, key=None, password="", **extra):
        self.port, self.name, self.password, self.extra = port, name, password, extra
        self.key = key or secrets.token_urlsafe(24)
        self.skip_others = False

    async def connect(self):
        self.r, self.w = await asyncio.open_connection("127.0.0.1", self.port, limit=mp.MAX_LINE)
        await self.send({"t": "hello", "protocol": mp.PROTOCOL, "key": self.key, "name": self.name, "password": self.password,
                        **self.extra})
        return await self.expect("welcome", "error")

    async def send(self, msg):
        self.w.write((json.dumps(msg) + "\n").encode())
        await self.w.drain()

    async def expect(self, *types, timeout=3):
        while True:
            line = await asyncio.wait_for(self.r.readline(), timeout)
            assert line, f"{self.name}: connection closed while waiting for {types}"
            msg = json.loads(line)
            if msg["t"] in types:
                return msg
            assert msg["t"] == "ping" or self.skip_others, f"{self.name}: expected {types}, got {msg}"


async def run():
    with tempfile.TemporaryDirectory() as tmp:
        server = mp.Server(mp.Store(Path(tmp)), password="pw")
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        port = srv.sockets[0].getsockname()[1]

        bad = Fake(port, "Mallory", password="nope")
        err = await bad.connect()
        assert err["error"] == "wrong server password" and err["code"] == "wrong_password" and err["fatal"]

        a = Fake(port, "Ana", password="pw")
        wa = await a.connect()
        assert wa["t"] == "welcome" and wa["players"] == [] and wa["leader"] == wa["id"]

        b = Fake(port, "Ben", password="pw", version="0.8.1")
        wb = await b.connect()
        assert [p["name"] for p in wb["players"]] == ["Ana"] and wb["server_version"] == mp.VERSION
        joined = (await a.expect("joined"))["player"]
        assert joined["name"] == "Ben" and joined["version"] == "0.8.1"

        await a.send({"t": "profile", "profile": {"first_name": "Ana", "gender": "Female"}})
        assert (await b.expect("profile"))["profile"]["first_name"] == "Ana"

        await a.send({"t": "state", "state": {"x": 1, "y": 2, "z": 3, "yaw": 90, "anim": "Idle"}})
        st = await b.expect("state")
        assert st["id"] == wa["id"] and st["state"]["yaw"] == 90

        await b.send({"t": "world", "world": {"hour": 9}})           # not the leader: ignored
        await a.send({"t": "world", "world": {"hour": 8, "minute": 30}})
        assert (await b.expect("world"))["world"]["hour"] == 8

        await b.send({"t": "chat", "text": "hi!"})
        assert (await a.expect("chat"))["text"] == "hi!"
        assert (await b.expect("chat"))["name"] == "Ben"              # chat echoes to the sender too

        await b.send({"t": "event", "to": wa["id"], "kind": "visit_request", "data": {"msg": "can I come over?"}})
        ev = await a.expect("event")
        assert ev["from"] == wb["id"] and ev["kind"] == "visit_request"

        house = {"name": "Ana's place", "walls": [[0, 0, 100, 0]] * 2000, "objects": [{"table_id": "Sofa_01", "x": 1}] * 3000}
        await a.send({"t": "house_put", "house": house})
        assert (await b.expect("house_updated"))["meta"]["owner"] == wa["id"]
        assert (await a.expect("house_updated"))["meta"]["name"] == "Ana's place"   # uploader is told too
        await b.send({"t": "house_get", "owner": wa["id"]})
        h = await b.expect("house")
        assert h["house"] == house

        # A malformed message gets an error (not fatal), and the connection stays open.
        await b.send({"t": "admin_claim", "code": {"not": "a string"}, "ref": 6})
        await b.expect("admin")
        r, w = await asyncio.open_connection("127.0.0.1", port)      # an exception in a handler (bad int)
        w.write(b'{"t": "announce_restart", "in": "soon"}\n{"t": "info"}\n')
        await w.drain()
        e = json.loads(await r.readline())
        assert e["t"] == "error" and e["code"] == "bad_request" and not e.get("fatal"), e
        assert json.loads(await r.readline())["t"] == "info"          # still connected
        w.close()

        # Ana reconnects with the same key (a Wi-Fi blip): same id, old session kicked, and Ben is NOT told she left:
        # only "rejoined", her Zoi stays.
        a2 = Fake(port, "Ana", key=a.key, password="pw")
        wa2 = await a2.connect()
        assert wa2["id"] == wa["id"] and len(wa2["houses"]) == 1 and wa2["leader"] == wa["id"]
        assert (await a.expect("kicked"))["t"] == "kicked"
        rj = await b.expect("rejoined")
        assert rj["player"]["id"] == wa["id"] and rj["player"]["profile"]["first_name"] == "Ana"
        assert rj["player"]["state"]["yaw"] == 90                       # her last state carries over
        await asyncio.sleep(0.2)                                         # the old session's hang-up is handled
        await b.send({"t": "who"})
        assert sorted(p["name"] for p in (await b.expect("who"))["players"]) == ["Ana", "Ben"]   # no left/joined between

        # Leader hand-off when the leader disconnects.
        a2.w.close()
        assert (await b.expect("left"))["id"] == wa["id"]
        assert (await b.expect("leader"))["id"] == wb["id"]

        b.w.close()
        srv.close()
        await srv.wait_closed()
    await hardening()
    await mods_shared()
    await mods_hosted()
    await shared_world()
    await servers_page()
    print("all server tests passed")


async def hardening():
    """A password with accents, an oversized line before hello, a guest can't take the hosted clock, garbage collection
    of world files nothing uses, a damaged server.json falls back to its backup, admin code guessing is slowed."""
    with tempfile.TemporaryDirectory() as tmp:
        server = mp.Server(mp.Store(Path(tmp)), password="cora\u00e7\u00e3o", hosted=True)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        port = srv.sockets[0].getsockname()[1]
        pw = "cora\u00e7\u00e3o"                                        # "coracao" with cedilla and tilde
        err = await Fake(port, "Mallory", password="coracao").connect()
        assert err["t"] == "error" and err["code"] == "wrong_password" and err["fatal"]
        err = await Fake(port, "Mallory", password="\u00e7\u00e3\u00e9").connect()
        assert err["code"] == "wrong_password"
        err = await Fake(port, "Old", password=pw, protocol=99).connect()
        assert err["code"] == "version_mismatch"
        host = Fake(port, "Host", password=pw, host=True)
        assert (await host.connect())["t"] == "welcome"
        host.skip_others = True
        guest = Fake(port, "Guest", password=pw)
        wg = await guest.connect()
        assert wg["t"] == "welcome" and wg["leader"] == wg["host"]
        await guest.send({"t": "take_leader"})
        assert (await guest.expect("leader"))["id"] == wg["host"]        # the host keeps the clock
        assert server.leader.id == wg["host"]

        # An oversized line before hello: refused and hung up (after hello up to MAX_LINE is fine: see the house test).
        r, w = await asyncio.open_connection("127.0.0.1", port, limit=mp.MAX_LINE)
        w.write(b'{"t": "hello", "pad": "' + b"x" * (200 * 1024) + b'"}\n')
        await w.drain()
        e = json.loads(await asyncio.wait_for(r.readline(), 3))
        assert e["code"] == "too_large" and e["fatal"], e
        try:
            assert await asyncio.wait_for(r.read(), 3) == b""
        except ConnectionResetError:                                      # hung up with our bytes unread: a reset
            pass
        w.close()

        # Admin code guessing: 5 tries a minute.
        for i in range(6):
            await guest.send({"t": "admin_claim", "code": f"guess{i}", "ref": i})
            m = await answer(guest, i)
        assert m["ok"] is False and m["code"] == "rate_limited"

        # Garbage collection: a world file no version uses goes (once old), mod files and kept versions stay.
        st = server.store
        mod, w1, w2, orphan = b"MOD", b"WORLD1", b"WORLD2", b"ORPHAN"
        st.set_mods("pzz", "Zed", [{"id": "M", "files": [{"p": "m.pak", "h": sha(mod), "s": 3}]}])
        for data in (mod, w1, w2, orphan):
            st.blob(sha(data)).write_bytes(data)
        st.set_world_save({"version": 1, "files": [{"p": "w.usvx", "h": sha(w1), "s": 6}]})
        st.set_world_save({"version": 2, "files": [{"p": "w.usvx", "h": sha(w2), "s": 6}]})
        part = st.blob("a" * 64).with_name("a" * 64 + ".p1.part")
        part.write_bytes(b"half")
        old = mp.now() - 2 * 86400
        for f in (st.root / "blobs").iterdir():
            mp.os.utime(f, (old, old))
        st.gc_blobs()
        assert [st.blob(sha(d)).exists() for d in (mod, w1, w2, orphan)] == [True, True, True, False]
        assert not part.exists()

        # server.json damaged: its backup is used (the password survives), loudly.
        st.save_settings()
        (st.root / "server.json").write_text("{broken", "utf-8")
        assert mp.Store(st.root).settings["password"] == pw
        for x in (host, guest):
            x.w.close()
        srv.close()
        await srv.wait_closed()


async def answer(player, ref):
    while True:
        m = await player.expect("admin")
        if m.get("ref") == ref:
            return m


async def ask(port, msg, reply):
    return await mp.request(f"127.0.0.1:{port}", msg, reply)


async def servers_page():
    """What the servers page needs: info before joining, the admin console (code, settings, kick, ban), the public list
    kept by a directory, and going back to an older world version."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as tmp2:
        pi = mp.Server(mp.Store(Path(tmp)), name="Nox", directory=True, password="pw")
        srv = await asyncio.start_server(pi.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        port = pi.port = srv.sockets[0].getsockname()[1]
        info = await ask(port, {"t": "info"}, "info")
        assert info["name"] == "Nox" and info["password"] is True and info["players"] == 0 and info["directory"]
        a, b = Fake(port, "Ana", password="pw"), Fake(port, "Ben", password="pw")
        wa = await a.connect()
        a.skip_others = True                                              # the admin: only answers matter
        assert wa["admin"] is False and wa["server"]["id"] == info["id"]
        wb = await b.connect()
        b.skip_others = True
        pak = b"MODDATA"
        await b.send({"t": "mods", "mods": [{"id": "Top", "name": "Top", "files": [{"p": "t.pak", "h": sha(pak), "s": 7}]}]})
        await a.expect("modset")
        info = await ask(port, {"t": "info"}, "info")
        assert info["players"] == 2 and sorted(info["names"]) == ["Ana", "Ben"]
        assert info["mods"]["list"][0]["fp"] == mp.mod_fp({"files": [{"p": "t.pak", "h": sha(pak), "s": 7}]})

        # Admin: not until the admin code; then settings, say, kick, ban, unban.
        await a.send({"t": "admin", "op": "status", "ref": 1})
        assert "not an admin" in (await answer(a, 1))["error"]
        await a.send({"t": "admin_claim", "code": "nope", "ref": 2})
        assert (await answer(a, 2))["ok"] is False
        await a.send({"t": "admin_claim", "code": pi.store.settings["admin_code"], "ref": 3})
        assert (await answer(a, 3))["ok"] is True
        await a.send({"t": "admin", "op": "status", "ref": 4})
        st = await answer(a, 4)
        assert st["ref"] == 4 and {p["name"] for p in st["players"]} == {"Ana", "Ben"} and st["admins"][0]["name"] == "Ana"
        await a.send({"t": "admin", "op": "set", "ref": 5, "name": "Nox's world", "motd": "hi all", "max_players": 8})
        assert sorted((await answer(a, 5))["changed"]) == ["max_players", "motd", "name"]
        assert (await b.expect("server_info"))["server"]["name"] == "Nox's world"
        await a.send({"t": "admin", "op": "say", "ref": 6, "text": "restart soon"})
        assert (await b.expect("chat"))["name"] == "Server"
        await a.send({"t": "admin", "op": "kick", "ref": 7, "id": wb["id"], "reason": "test"})
        assert "kicked by Ana" in (await b.expect("kicked"))["reason"]
        assert (await answer(a, 7))["ok"] is True
        b2 = Fake(port, "Ben", key=b.key, password="pw")
        assert (await b2.connect())["t"] == "welcome"                 # kicked, not banned: can come back
        await a.send({"t": "admin", "op": "ban", "ref": 8, "id": wb["id"], "reason": "griefing"})
        assert (await answer(a, 8))["ok"] is True
        assert (await b2.expect("kicked"))["reason"].startswith("banned")
        b3 = Fake(port, "Ben", key=b.key, password="pw")
        assert "banned" in (await b3.connect())["error"]
        await a.send({"t": "admin", "op": "unban", "ref": 9, "id": wb["id"]})
        await answer(a, 9)
        b4 = Fake(port, "Ben", key=b.key, password="pw")
        b4.skip_others = True
        assert (await b4.connect())["t"] == "welcome"
        await a.send({"t": "admin", "op": "restart", "ref": 10})
        assert "restart that program" in (await answer(a, 10))["error"]   # not a stand-alone server here
        assert (await ask(port, {"t": "make_admin", "who": "Ben"}, "ok"))["ok"] is True
        assert wb["id"] in pi.store.settings["admins"]

        # The public list: a friend's server goes public, the directory checks it and lists it.
        friend = mp.Server(mp.Store(Path(tmp2)), name="Ana's place")
        fsrv = await asyncio.start_server(friend.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        friend.port = fsrv.sockets[0].getsockname()[1]
        friend.store.settings.update(public=True, directories=[f"127.0.0.1:{port}"])
        task = asyncio.ensure_future(friend.announcer())
        for _ in range(50):
            if friend.public_status:
                break
            await asyncio.sleep(0.1)
        status = next(iter(friend.public_status.values()))
        assert status["ok"] and status["reachable"], status
        listed = (await ask(port, {"t": "dir_list"}, "dir_list"))["servers"]
        assert [s["name"] for s in listed] == ["Ana's place"] and listed[0]["address"].endswith(f":{friend.port}")
        assert "secret" not in listed[0]
        fake = {**friend.listing(), "secret": "x" * 32}                     # someone else claiming that id
        assert (await ask(port, fake, "dir_announce"))["ok"] is False
        await ask(port, {"t": "dir_remove", "id": friend.store.settings["id"], "secret": "x" * 32}, "dir_remove")
        assert len((await ask(port, {"t": "dir_list"}, "dir_list"))["servers"]) == 1
        await friend.unlist()                                               # going private
        assert (await ask(port, {"t": "dir_list"}, "dir_list"))["servers"] == []
        task.cancel()

        # World history: two versions, then back to the first (as version 3).
        for n, data in ((1, b"WORLD-ONE"), (2, b"WORLD-TWO")):
            pi.store.blob(sha(data)).write_bytes(data)
            pi.store.set_world_save({"version": n, "by": "Ana", "time": n, "name": "w", "size": len(data),
                                     "files": [{"p": "world.usvx", "h": sha(data), "s": len(data)}]})
        await a.send({"t": "admin", "op": "world_restore", "ref": 11, "version": 1})
        assert (await answer(a, 11))["version"] == 3
        ws = await b4.expect("world_save")
        assert ws["version"] == 3 and ws["files"][0]["h"] == sha(b"WORLD-ONE")
        assert [m["version"] for m in pi.store.world_history()] == [3, 2, 1]
        for x in (a, b4):
            x.w.close()
        for s in (srv, fsrv):
            s.close()
            await s.wait_closed()


async def shared_world():
    """The world save: offered, uploaded, committed as version 1; a stale upload is refused; others can download it;
    a restart announcement (only from the server's own machine) reaches everyone."""
    with tempfile.TemporaryDirectory() as tmp:
        server = mp.Server(mp.Store(Path(tmp)))
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        port = srv.sockets[0].getsockname()[1]
        a, b = Fake(port, "Ana"), Fake(port, "Ben")
        wa = await a.connect()
        assert wa["world_save"] is None
        await b.connect()
        await a.expect("joined")
        save, pic = b"IZMS-world-data" * 1000, b"PNG"
        files = [{"p": "world.usvx", "h": sha(save), "s": len(save)}, {"p": "pictures/Portrait/1.png", "h": sha(pic), "s": 3}]
        await a.send({"t": "world_save_offer", "files": files})
        assert sorted((await a.expect("world_save_offer"))["need"]) == sorted([sha(save), sha(pic)])
        fa = await files_conn(port, a)
        assert (await upload(fa, save, step=len(save)))["t"] == "blob_ok"
        await a.send({"t": "world_save_put", "files": files, "base": 0, "name": "Sawyer"})
        assert (await a.expect("world_save_put"))["ok"] is False                     # a file is still missing
        assert (await upload(fa, pic))["t"] == "blob_ok"
        await a.send({"t": "world_save_put", "files": files, "base": 0, "name": "Sawyer"})
        put = await a.expect("world_save_put")
        assert put["ok"] and put["version"] == 1
        ws = await b.expect("world_save")
        assert ws["version"] == 1 and ws["by"] == "Ana" and ws["name"] == "Sawyer"
        fb = await files_conn(port, b)
        assert await download(fb, sha(save)) == save
        await b.send({"t": "world_save_offer", "files": files})
        await b.expect("world_save_offer")
        await b.send({"t": "world_save_put", "files": files, "base": 0})               # based on an old version
        stale = await b.expect("world_save_put")
        assert stale["ok"] is False and stale["version"] == 1
        r, w = await asyncio.open_connection("127.0.0.1", port)                        # the restart announcement
        w.write(b'{"t": "announce_restart", "in": 30}\n')
        await w.drain()
        assert json.loads(await r.readline())["t"] == "ok"
        rs = await a.expect("restart_soon")
        assert rs["in"] == 30 and rs["keeper"] == wa["id"]
        for x in (a, b, fa, fb):
            x.w.close()
        w.close()
        srv.close()
        await srv.wait_closed()


def sha(data: bytes) -> str:
    return mp.hashlib.sha256(data).hexdigest()


async def files_conn(port, player):
    f = Fake(port, player.name, key=player.key)
    f.r, f.w = await asyncio.open_connection("127.0.0.1", port, limit=mp.MAX_LINE)
    await f.send({"t": "hello", "protocol": mp.PROTOCOL, "key": player.key, "name": player.name, "password": "",
                  "files": True})
    await f.expect("files_ok")
    return f


async def upload(f, data, step=3):
    h = sha(data)
    for o in range(0, len(data), step):                              # tiny pieces, to exercise the offsets
        await f.send({"t": "blob_put", "h": h, "o": o, "d": mp.base64.b64encode(data[o:o + step]).decode(),
                      "end": o + step >= len(data)})
    return await f.expect("blob_ok", "blob_bad")


async def download(f, h):
    out, o = b"", 0
    while True:
        await f.send({"t": "blob_get", "h": h, "o": o})
        m = await f.expect("blob")
        if m.get("error"):
            return m["error"]
        out += mp.base64.b64decode(m["d"])
        o = len(out)
        if m["end"]:
            return out


async def mods_shared():
    """A Pi-style server: everyone's mods are pooled, files go up and come down intact, only shared files are served."""
    with tempfile.TemporaryDirectory() as tmp:
        server = mp.Server(mp.Store(Path(tmp)))
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        port = srv.sockets[0].getsockname()[1]
        a, b = Fake(port, "Ana"), Fake(port, "Ben")
        await a.connect()
        await b.connect()
        await a.expect("joined")
        pak, cfg = b"PAK-DATA-1234567", b'{"ok":1}'
        mod = {"id": "Top_A1", "name": "Top", "version": "1", "path": "88849/1_2/Top_A1",
               "files": [{"p": "a.pak", "h": sha(pak), "s": len(pak)}, {"p": "m.json", "h": sha(cfg), "s": len(cfg)}]}
        await a.send({"t": "mods", "mods": [mod, {"id": "broken", "files": [{"p": "x", "h": "nothex", "s": 1}]}]})
        ms = await b.expect("modset")
        assert ms["source"] == "everyone" and [m["id"] for m in ms["mods"]] == ["Top_A1"] and ms["mods"][0]["by"] == ["Ana"]
        assert ms["mods"][0]["size"] == len(pak) + len(cfg)
        assert (await a.expect("modset"))["mods"][0]["id"] == "Top_A1"   # the sender gets it too
        fa, fb = await files_conn(port, a), await files_conn(port, b)
        await fa.send({"t": "blob_check", "h": [sha(pak), sha(cfg)]})
        assert len((await fa.expect("blob_missing"))["h"]) == 2
        assert await download(fb, sha(pak)) == "not uploaded yet"
        assert (await upload(fb, pak, step=len(pak)))["t"] == "blob_bad"   # Ben can't upload files of Ana's mods
        assert (await upload(fa, pak))["t"] == "blob_ok"
        assert (await upload(fa, cfg))["t"] == "blob_ok"
        assert await download(fb, sha(pak)) == pak and await download(fb, sha(cfg)) == cfg
        assert await download(fb, sha(b"secret")) == "not shared"
        await fb.send({"t": "chat", "text": "hi"})                         # player messages don't go on file connections
        assert "belong" in (await fb.expect("error"))["error"]
        await b.send({"t": "mods", "mods": [{**mod, "id": "Hair_B", "name": "Hair", "path": "InGame/Hair_B"}]})
        ms = await a.expect("modset")
        assert [m["id"] for m in ms["mods"]] == ["Hair_B", "Top_A1"]       # pooled, by name
        for x in (a, b, fa, fb):
            x.w.close()
        srv.close()
        await srv.wait_closed()


async def mods_hosted():
    """Host and play: the host's mods are what everyone gets, whatever the others have; the host leads the clock."""
    with tempfile.TemporaryDirectory() as tmp:
        server = mp.Server(mp.Store(Path(tmp)), hosted=True)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        port = srv.sockets[0].getsockname()[1]
        guest, host = Fake(port, "Guest"), Fake(port, "Host")
        wg = await guest.connect()
        assert wg["leader"] == wg["id"] and wg["modset"]["source"] == "host" and wg["modset"]["mods"] == []
        host.r, host.w = await asyncio.open_connection("127.0.0.1", port, limit=mp.MAX_LINE)
        await host.send({"t": "hello", "protocol": mp.PROTOCOL, "key": host.key, "name": "Host", "password": "",
                         "host": True})
        wh = await host.expect("welcome")
        assert wh["host"] == wh["id"] and wh["leader"] == wh["id"]
        assert (await guest.expect("joined"))["player"]["leader"] is True
        assert (await guest.expect("leader"))["id"] == wh["id"]           # the host takes over the clock
        assert (await guest.expect("modset"))["host"] == wh["id"]
        data = b"x" * 10
        await guest.send({"t": "mods", "mods": [{"id": "GuestMod", "files": [{"p": "g", "h": sha(data), "s": 10}]}]})
        await host.send({"t": "mods", "mods": [{"id": "HostMod", "files": [{"p": "h", "h": sha(data), "s": 10}]}]})
        ms = await guest.expect("modset")
        assert [m["id"] for m in ms["mods"]] == ["HostMod"] and ms["mods"][0]["by"] == ["Host"]
        for x in (guest, host):
            x.w.close()
        srv.close()
        await srv.wait_closed()


if __name__ == "__main__":
    asyncio.run(run())
