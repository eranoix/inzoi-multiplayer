"""Regression tests for the server's robustness fixes (server/inzoi_mp_server.py): one slow or dead connection never
holds up anyone else; hostile or broken input can't poison the server; disk failures and crashes leave memory, disk and
players in step; the server always stops promptly and cleanly.

Every test starts its own server in this process on port 0 with a fresh temp data folder (never the real one, never
port 24242/24243), and runs in its own event loop.

    python tools/test_server_robust.py              # all
    python tools/test_server_robust.py stalled      # the tests whose name contains "stalled"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Safety first: nothing here may ever see the real %APPDATA% (no client module is imported, but just in case).
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="robust-appdata-")

SERVER_DIR = Path(os.environ.get("ROBUST_SERVER_DIR") or Path(__file__).resolve().parents[1] / "server")
sys.path.insert(0, str(SERVER_DIR))
import inzoi_mp_server as mp  # noqa: E402

# Safety: the user's own server and app run on 24242/24243 on this machine. Every question a server under test asks
# another server (a directory's reachability check, an announcement, an unlisting) goes through mp.request: one aimed
# at those ports (an address without a port means 24242) never leaves, and fails the test that caused it (run_one).
LIVE_PORTS = {24242, 24243}
LIVE_CONTACTS: list = []
_real_request = mp.request


async def _guarded_request(address, *args, **kw):
    try:
        port = mp.split_address(str(address))[1]
    except Exception:                                # noqa: BLE001 - whatever it is, it isn't sent anywhere
        port = 24242
    if port in LIVE_PORTS:
        LIVE_CONTACTS.append(str(address))
        raise ConnectionRefusedError(f"the tests never contact {address}")
    return await _real_request(address, *args, **kw)


mp.request = _guarded_request


def free_port() -> int:
    """A loopback port nothing listens on (bound and let go: the kernel hands out ephemeral ports, never 24242)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert port not in LIVE_PORTS
    return port


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Patched:
    """Module constants (or attributes of other objects) changed for one test, put back after."""

    def __init__(self, target=mp, **values):
        self.target, self.values, self.saved = target, values, {}

    MISSING = object()

    def __enter__(self):
        for k, v in self.values.items():
            # (a class's own attribute as stored: getattr would unwrap a staticmethod, and putting that back breaks it)
            raw = vars(self.target).get(k) if isinstance(self.target, type) else None
            self.saved[k] = raw if raw is not None else getattr(self.target, k, self.MISSING)
            setattr(self.target, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is self.MISSING:                    # (an older server without it: ROBUST_SERVER_DIR)
                delattr(self.target, k)
            else:
                setattr(self.target, k, v)


class Errors(logging.Handler):
    """ERROR records (the server's and asyncio's) and loop exceptions: a test fails on any it didn't expect."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage()[:300])


def tmpdir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"robust-{prefix}-"))


async def serve(data: Path | None = None, **kw):
    server = mp.Server(mp.Store(data or tmpdir("data")), **kw)
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
    server.port = srv.sockets[0].getsockname()[1]
    return server, srv


async def unserve(server, srv):
    srv.close()
    if hasattr(server, "shutdown"):
        await server.shutdown()
    else:                                            # (an older server: ROBUST_SERVER_DIR)
        for c in list(server.conns):
            c.writer.transport.abort()
    try:
        await asyncio.wait_for(srv.wait_closed(), 3)
    except asyncio.TimeoutError:
        pass


class Peer:
    """A fake companion connection: records every message it reads (once reading), answers nothing by itself."""

    def __init__(self, name: str = "P", key: str | None = None):
        self.name, self.key = name, key or secrets.token_urlsafe(18)
        self.msgs: list[dict] = []
        self.task = None
        self.eof = False
        self.welcome = None

    async def open(self, port: int, rcvbuf: int | None = None):
        if rcvbuf:                                   # a peer that will stop reading: keep its buffers tiny
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
            s.setblocking(False)
            await asyncio.get_running_loop().sock_connect(s, ("127.0.0.1", port))
            self.r, self.w = await asyncio.open_connection(sock=s, limit=mp.MAX_LINE)
        else:
            self.r, self.w = await asyncio.open_connection("127.0.0.1", port, limit=mp.MAX_LINE)
        return self

    async def send(self, m: dict):
        self.w.write((json.dumps(m) + "\n").encode())
        await self.w.drain()

    async def raw(self, data: bytes):
        self.w.write(data)
        await self.w.drain()

    async def line(self, timeout: float = 5) -> dict:
        return json.loads(await asyncio.wait_for(self.r.readline(), timeout))

    async def hello(self, **extra) -> dict:
        await self.send({"t": "hello", "protocol": mp.PROTOCOL, "key": self.key, "name": self.name, "version": "t",
                         **extra})
        self.welcome = await self.line()
        return self.welcome

    async def join(self, port: int, read: bool = True, rcvbuf: int | None = None, **extra) -> dict:
        await self.open(port, rcvbuf)
        w = await self.hello(**extra)
        assert w.get("t") == "welcome", w
        if read:
            self.read()
        return w

    def read(self):
        self.task = asyncio.get_running_loop().create_task(self._read())

    async def _read(self):
        try:
            while True:
                line = await self.r.readline()
                if not line:
                    break
                self.msgs.append(json.loads(line))
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            self.eof = True

    def find(self, pred):
        return next((m for m in self.msgs if pred(m)), None)

    async def wait(self, pred, timeout: float = 3, what: str = ""):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            hit = self.find(pred)
            if hit is not None:
                return hit
            await asyncio.sleep(0.01)
        raise AssertionError(f"{self.name}: no {what or 'matching message'} within {timeout} s; "
                             f"got {[m.get('t') for m in self.msgs][-20:]}")

    async def none(self, pred, seconds: float, what: str = ""):
        await asyncio.sleep(seconds)
        hit = self.find(pred)
        assert hit is None, f"{self.name}: unexpected {what}: {str(hit)[:200]}"

    def drop(self):
        if self.task:
            self.task.cancel()
        try:
            self.w.close()
        except Exception:                            # noqa: BLE001
            pass


def conn_of(server, name: str):
    return next((c for c in list(server.conns) if c.name == name), None)


async def until(check, timeout: float, what: str):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"not within {timeout} s: {what}")


async def stall(server, sender: Peer, victim: str, size: int = 1_000_000, limit: float = 30):
    """Sends big messages until the server is visibly backed up on `victim` (its writes wait for the peer, or it was
    cut off for not reading)."""
    blob = "x" * size
    end = time.monotonic() + limit
    while time.monotonic() < end:
        c = conn_of(server, victim)
        if c is None or getattr(c, "aborted", False) or getattr(c, "out", None) or \
                c.writer.transport.get_write_buffer_size() > 65536:
            return
        await sender.send({"t": "event", "kind": "blob", "data": {"x": blob}})
        await asyncio.sleep(0.005)
    raise AssertionError(f"{victim} never backed up")


# ====================================================================== one slow peer never holds anyone up
@test
async def stalled_reader_freezes_nobody():
    """#0 / #11: a player who stops reading (PC asleep, frozen app) doesn't stop the server reading the others, the
    keepalive keeps going, and the stalled one is cut off after SEND_TIMEOUT."""
    with Patched(SEND_TIMEOUT=1.5, PING_EVERY=0.3, TIMEOUT=600, LINGER=0.3):
        server, srv = await serve()
        ka = asyncio.get_running_loop().create_task(server.keepalive())
        try:
            zed = Peer("Zed")
            await zed.join(server.port, read=False, rcvbuf=2048)     # reads its welcome, then never again
            ana, ben = Peer("Ana"), Peer("Ben")
            await ana.join(server.port)
            await ben.join(server.port)
            await stall(server, ana, "Zed")
            assert conn_of(server, "Zed") is not None
            t0 = time.monotonic()
            await ana.send({"t": "ping", "echo": "after-stall"})
            await ana.wait(lambda m: m.get("t") == "pong" and m.get("echo") == "after-stall", 2, "pong")
            await ana.send({"t": "set", "key": "family", "value": {"n": 1}, "ref": "s1"})
            await ana.wait(lambda m: m.get("t") == "shared" and m.get("ref") == "s1", 2, "set confirmation")
            await ana.send({"t": "chat", "text": "still here"})
            await ben.wait(lambda m: m.get("t") == "chat" and m.get("text") == "still here", 2, "chat")
            assert time.monotonic() - t0 < 3, "the others waited on the stalled player"
            pings = sum(1 for m in ben.msgs if m.get("t") == "ping")
            await asyncio.sleep(1.0)
            assert sum(1 for m in ben.msgs if m.get("t") == "ping") > pings, "the keepalive stopped"
            # Whatever the system still buffers for it (Windows takes tens of MB on loopback), a player who never
            # reads is cut off: by SEND_TIMEOUT once nothing more is taken, or by MAX_BACKLOG.
            blob = "x" * 1_000_000
            end = time.monotonic() + 60
            while conn_of(server, "Zed") is not None and time.monotonic() < end:
                await ana.send({"t": "event", "kind": "blob", "data": {"x": blob}})
                await asyncio.sleep(0.01)
            assert conn_of(server, "Zed") is None, "the stalled player is never cut off"
            await ben.wait(lambda m: m.get("t") == "left" and m.get("name") == "Zed", 3, "Zed left")
            assert not ben.eof and conn_of(server, "Ben") is not None, "the healthy player was cut off too"
        finally:
            ka.cancel()
            await unserve(server, srv)


@test
async def stalled_pose_stream_stays_bounded():
    """Skeleton frames to a peer that's behind replace each other (latest wins): its queue never grows past one frame
    per sender and kind, and a fast player still gets them all."""
    with Patched(SEND_TIMEOUT=30, TIMEOUT=600):
        server, srv = await serve()
        try:
            zed = Peer("Zed")
            await zed.join(server.port, read=False, rcvbuf=2048)
            ana, ben = Peer("Ana"), Peer("Ben")
            await ana.join(server.port)
            await ben.join(server.port)
            await stall(server, ana, "Zed")
            zc = conn_of(server, "Zed")
            frame = "1|5|" + "9" * 200_000
            for i in range(40):
                await ana.send({"t": "event", "kind": "pose", "data": {"d": f"{i}|{frame}"}})
                await ana.send({"t": "state", "state": {"n": i}})
            await ben.wait(lambda m: m.get("t") == "state" and m["state"]["n"] == 39, 5, "the last state")
            assert zc.aborted or sum(1 for e in zc.out if e[0] and e[1] and e[1][0] == "pose") <= 2, \
                "pose frames piled up for the stalled player"
            assert zc.aborted or sum(1 for e in zc.out if e[0] and e[1] and e[1][0] == "state") <= 1
            got = sum(1 for m in ben.msgs if m.get("kind") == "pose")
            assert got >= 20, f"the fast player missed most frames ({got} of 40)"
        finally:
            await unserve(server, srv)


@test
async def close_never_waits_and_shutdown_is_bounded():
    """#0 / #12: closing a stuck connection returns at once and the socket is cut within CLOSE_GRACE; shutdown with a
    stuck player finishes in about a second, and the goodbye reaches the healthy player."""
    with Patched(SEND_TIMEOUT=60, TIMEOUT=600, CLOSE_GRACE=1.0, MAX_BACKLOG=256 * 1024 * 1024):
        server, srv = await serve()
        zed = Peer("Zed")
        await zed.join(server.port, read=False, rcvbuf=2048)
        ana = Peer("Ana")
        await ana.join(server.port)
        await stall(server, ana, "Zed")
        zc = conn_of(server, "Zed")
        assert zc is not None and not zc.aborted
        t0 = time.monotonic()
        await zc.close()
        assert time.monotonic() - t0 < 0.1, "close() waited for the peer"
        await until(lambda: zc.aborted, mp.CLOSE_GRACE + 1.5, "the closed connection is cut")
        yan = Peer("Yan")
        await yan.join(server.port, read=False, rcvbuf=2048)
        await stall(server, ana, "Yan")
        srv.close()
        t0 = time.monotonic()
        await asyncio.wait_for(server.shutdown("bye now"), 4)
        assert time.monotonic() - t0 < 2.5, "shutdown waited for the stuck player"
        await ana.wait(lambda m: m.get("t") == "chat" and m.get("text") == "bye now", 2, "the goodbye")
        assert all(c.aborted for c in server.conns) or not server.conns
        await asyncio.wait_for(srv.wait_closed(), 4)


@test
async def rejoin_while_old_connection_stuck():
    """#2: a reconnect after a blip gets its welcome at once even though the old connection is stuck mid-write; the
    others see a silent "rejoined"; the old connection is cut."""
    with Patched(SEND_TIMEOUT=60, TIMEOUT=600, CLOSE_GRACE=1.0, MAX_BACKLOG=256 * 1024 * 1024):
        server, srv = await serve()
        try:
            ben_old = Peer("Ben")
            await ben_old.join(server.port, read=False, rcvbuf=2048)
            ana = Peer("Ana")
            await ana.join(server.port)
            await stall(server, ana, "Ben")
            old = server.clients[ben_old.welcome["id"]]
            ben = Peer("Ben", key=ben_old.key)
            t0 = time.monotonic()
            w = await ben.join(server.port)
            assert w["id"] == ben_old.welcome["id"] and time.monotonic() - t0 < 1.0, "the welcome waited"
            await ana.wait(lambda m: m.get("t") == "rejoined" and m["player"]["id"] == w["id"], 2, "rejoined")
            assert not ana.find(lambda m: m.get("t") in ("left", "joined") and
                                (m.get("id") == w["id"] or (m.get("player") or {}).get("id") == w["id"]))
            await until(lambda: old.aborted, mp.CLOSE_GRACE + 1.5, "the old connection is cut")
            await ben.send({"t": "ping", "echo": 1})
            await ben.wait(lambda m: m.get("t") == "pong", 2, "pong on the new connection")
        finally:
            await unserve(server, srv)


# ====================================================================== input that must not poison the server
@test
async def lone_surrogates_and_bad_utf8_are_refused():
    """#1: a lone UTF-16 surrogate (escaped) or CESU-8 bytes anywhere are refused at the door (bad_json); nothing is
    stored, everyone can still join; real emoji and accents go through unchanged; saved files are ASCII."""
    server, srv = await serve()
    try:
        p = Peer("Mal")
        await p.open(server.port)
        await p.raw(b'{"t":"hello","protocol":1,"key":"' + b"m" * 20 + b'","name":"x\\ud800"}\n')
        assert (await p.line())["code"] == "bad_json"
        assert (await p.hello())["t"] == "welcome"                # the connection is fine
        p.read()
        await p.raw(b'{"t":"set","key":"family","value":"\\udc00x","ref":"bad"}\n')
        await p.raw(b'{"t":"profile","profile":{"first_name":"\\ud83d"}}\n')
        await p.raw(b'{"t":"chat","text":"\xed\xa0\x80"}\n')       # CESU-8 surrogate bytes
        await p.raw(b'{"t":"ping","echo":"\\ud800","ref":"\\udfff"}\n')
        await until(lambda: sum(1 for m in p.msgs if m.get("code") == "bad_json") >= 4, 3, "four bad_json")
        assert "family" not in server.shared
        text = "a\u00e7a\u00ed \U0001F642 \ud55c"                   # accents, an emoji (a surrogate pair), Korean
        await p.send({"t": "chat", "text": text})
        await p.send({"t": "set", "key": "family", "value": {"name": text}, "ref": "ok"})
        await p.wait(lambda m: m.get("t") == "chat" and m.get("text") == text, 2, "the chat round trip")
        await p.wait(lambda m: m.get("t") == "shared" and m.get("ref") == "ok", 2, "the set")
        assert server.shared["family"]["value"]["name"] == text
        b = Peer("Ben")
        assert (await b.join(server.port))["shared"]["family"]["value"]["name"] == text
        for f in [server.store.players_file, *(server.store.root / "shared").glob("*.json")]:
            assert max(f.read_bytes(), default=0) < 128, f"{f.name} isn't ASCII"
        await p.raw(b'{"t":"' + b"q" * 50000 + b'"}\n')               # an error must not echo a huge "t" back
        reply = await p.wait(lambda m: m.get("code") == "unknown_type", 2, "unknown_type")
        assert len(json.dumps(reply)) < 400
    finally:
        await unserve(server, srv)


@test
async def deep_nesting_is_refused_and_never_bricks_startup():
    """#3: deeply nested JSON is answered with bad_json (before and after hello) and never stored; a damaged or
    pathological file in the data folder never stops the server from starting."""
    errors = Errors()
    logging.getLogger("asyncio").addHandler(errors)
    server, srv = await serve()
    try:
        p = Peer("Ana")
        await p.open(server.port)
        await p.raw(b"[" * 30000 + b"]" * 30000 + b"\n")             # past json.loads' own limit
        assert (await p.line())["code"] == "bad_json"
        await p.send({"t": "info"})
        assert (await p.line())["t"] == "info"                      # still connected
        assert (await p.hello())["t"] == "welcome"
        p.read()
        deep = lambda n: "[" * n + "]" * n                          # noqa: E731
        await p.raw(('{"t":"profile","profile":' + deep(200) + "}\n").encode())
        await p.raw(('{"t":"set","key":"house:1:live","value":' + deep(15500) + ',"ref":"d"}\n').encode())
        await p.raw(('{"t":"state","state":' + deep(mp.MAX_DEPTH + 5) + "}\n").encode())
        await until(lambda: sum(1 for m in p.msgs if m.get("code") == "bad_json") >= 3, 3, "three bad_json")
        assert "house:1:live" not in server.shared
        await p.send({"t": "set", "key": "house:1:live", "value": json.loads(deep(50)), "ref": "fine"})
        await p.wait(lambda m: m.get("ref") == "fine" and m.get("t") == "shared", 2, "a 50-deep value")
        b = Peer("Ben")
        assert (await b.join(server.port))["t"] == "welcome"
    finally:
        await unserve(server, srv)
        logging.getLogger("asyncio").removeHandler(errors)
    assert not errors.records, errors.records

    data = tmpdir("deepfile")
    shared = data / "shared"
    shared.mkdir(parents=True)
    for i, n in enumerate((20000, 16000)):                          # past loads' limit; past dumps' limit
        key = f"house:{i}:live"
        name = mp.hashlib.sha1(key.encode()).hexdigest()[:20] + ".json"
        (shared / name).write_text('{"key":"' + key + '","v":1,"value":' + deep(n) + "}", "utf-8")
    (data / "players.json").write_text(deep(25000), "utf-8")
    server, srv = await serve(data)
    try:
        assert not any(k.startswith("house:") for k in server.shared)
        assert any(f.name.startswith("players.json.corrupt-") for f in data.iterdir())
        assert (await Peer("Cat").join(server.port))["t"] == "welcome"
    finally:
        await unserve(server, srv)


def companion_loads(line: bytes):
    """json.loads as a companion runs it (Python 3.11+: no integer past 4300 digits), whatever runs the tests."""
    def to_int(s: str) -> int:
        if len(s.lstrip("-")) > 4300:
            raise ValueError(f"a {len(s)}-digit integer: past Python 3.11's limit")
        return int(s)
    return json.loads(line, parse_int=to_int)


@test
async def integers_a_companion_cant_read_are_refused():
    """Review round 2: a JSON integer longer than 4300 digits (what Python 3.11+, every companion, reads at most) is
    refused at the door (bad_json) and never stored, also on a server interpreter without that limit (CPython before
    3.9.14 / 3.10.7; simulated with sys.set_int_max_str_digits(0)). There it was saved and sent in every welcome, which
    no companion could read: nobody could join, after restarts too. One already in a saved shared value is left out
    at startup. 4300 digits (negative too) still go through."""
    get_limit = getattr(sys, "get_int_max_str_digits", None)
    set_limit = getattr(sys, "set_int_max_str_digits", None)
    saved = get_limit() if get_limit else None
    big, ok = "1" * 4301, "1" * 4300
    try:
        for limit in [None] + ([0] if set_limit else []):           # this interpreter as it is; then without a limit
            if limit is not None:
                set_limit(limit)
            server, srv = await serve()
            try:
                p = Peer("Mal")
                await p.join(server.port)
                await p.raw(('{"t":"set","key":"family","value":' + big + ',"ref":"big"}\n').encode())
                await p.raw(('{"t":"set","key":"house:1:live","value":{"n":[-' + big + ']}}\n').encode())
                await p.raw(('{"t":"profile","profile":{"age":' + big + "}}\n").encode())
                await p.raw(('{"t":"state","state":{"x":' + "9" * 20000 + "}}\n").encode())
                await until(lambda: sum(1 for m in p.msgs if m.get("code") == "bad_json") >= 4, 3, "four bad_json")
                assert not p.find(lambda m: m.get("ref") == "big"), "the long integer was taken"
                assert "family" not in server.shared and "house:1:live" not in server.shared, list(server.shared)
                assert not server.store.players[conn_of(server, "Mal").id].get("profile")
                assert not any(big in f.read_text("utf-8") for f in server.store.root.rglob("*.json"))
                await p.raw(('{"t":"set","key":"family","value":[' + ok + ",-" + ok + ",1." + big + '],"ref":"ok"}\n')
                            .encode())                               # (a long float is no integer: fine)
                await p.wait(lambda m: m.get("t") == "shared" and m.get("ref") == "ok", 2, "4300 digits taken")
                r, w = await asyncio.open_connection("127.0.0.1", server.port, limit=mp.MAX_LINE)
                w.write((json.dumps({"t": "hello", "protocol": 1, "key": "b" * 20, "name": "Ben"}) + "\n").encode())
                welcome = companion_loads(await asyncio.wait_for(r.readline(), 5))
                assert welcome["t"] == "welcome", welcome
                value = welcome["shared"]["family"]["value"]
                assert value[:2] == [int(ok), -int(ok)] and 1.1 < value[2] < 1.2, f"limit {limit}: {str(value)[:80]}"
                w.close()
            finally:
                await unserve(server, srv)

            # One a server without the limit saved earlier: left out at startup (also where loading it fails).
            data = tmpdir("bigint")
            (data / "shared").mkdir(parents=True)
            for key, value in (("family", big), ("house:2:live", ok)):
                name = mp.hashlib.sha1(key.encode()).hexdigest()[:20] + ".json"
                (data / "shared" / name).write_text('{"key":"' + key + '","v":1,"value":' + value + "}", "utf-8")
            server, srv = await serve(data)
            try:
                assert "family" not in server.shared and "house:2:live" in server.shared, list(server.shared)
                r, w = await asyncio.open_connection("127.0.0.1", server.port, limit=mp.MAX_LINE)
                w.write((json.dumps({"t": "hello", "protocol": 1, "key": "c" * 20, "name": "Cat"}) + "\n").encode())
                welcome = companion_loads(await asyncio.wait_for(r.readline(), 5))
                assert welcome["t"] == "welcome" and "family" not in welcome["shared"], str(welcome)[:200]
                w.close()
            finally:
                await unserve(server, srv)
    finally:
        if set_limit:
            set_limit(saved)


@test
async def house_get_owner_is_never_a_path():
    """#13: house_get's owner must be a player id: ../ or an absolute path reads nothing and renames nothing."""
    outside = tmpdir("outside")
    decoy = outside / "settings.json"
    decoy.write_text("{broken", "utf-8")
    data = tmpdir("houses")
    server, srv = await serve(data)
    try:
        p = Peer("Ana")
        await p.join(server.port)
        rel = os.path.relpath(outside / "settings", data / "houses")
        for owner in ("../players", rel, str(outside / "settings"), "p1234567g", "", "..\\server"):
            await p.send({"t": "house_get", "owner": owner})
            m = await p.wait(lambda m, o=owner[:40]: m.get("t") == "house" and m.get("owner") == o, 2, "house")
            assert m["meta"] is None and m["house"] is None, m
        assert decoy.exists() and [f.name for f in outside.iterdir()] == ["settings.json"]
        try:
            server.store.house_path("../x")
            raise AssertionError("house_path took a path")
        except ValueError:
            pass
    finally:
        await unserve(server, srv)


@test
async def bad_fields_answered_without_tracebacks():
    """A malformed field is a warning and a bad_request answer (with the message's ref), never an ERROR traceback in
    the log; the connection stays."""
    errors = Errors()
    mp.log.addHandler(errors)
    server, srv = await serve()
    try:
        p = Peer("Ana")
        await p.join(server.port)
        await p.send({"t": "set", "key": "family", "value": 1, "base": "abc", "ref": 5})
        m = await p.wait(lambda m: m.get("ref") == 5, 2, "an answer")
        assert m["t"] == "error" and m["code"] == "bad_request", m
        await p.send({"t": "house_put", "house": [1, 2], "ref": 6})
        assert (await p.wait(lambda m: m.get("ref") == 6, 2))["code"] == "bad_request"
        await p.send({"t": "blob_get", "h": "x", "o": "zz", "ref": 7})
        await p.send({"t": "set", "key": "family", "value": 1, "ref": "r" * 500})   # an overlong ref isn't echoed
        m = await p.wait(lambda m: m.get("t") == "shared" and m.get("key") == "family", 2)
        assert m["ref"] is None
    finally:
        await unserve(server, srv)
        mp.log.removeHandler(errors)
    assert not errors.records, errors.records


@test
async def long_digit_strings_are_never_converted():
    """Review round 3: the numbers the server converts itself (a set's base, blob_get's o, a dir_announce's port and
    counts) never go through int() as a long digit string. On an interpreter without Python's int-digit limit
    (CPython before 3.9.14 / 3.10.7, a Raspberry Pi's distro 3.9; simulated with sys.set_int_max_str_digits(0)) that
    takes quadratic time: one 1.4 MB set held up the whole server for 7 s (a 32 MB one: about 50 minutes). base and
    o must be JSON integers (bad_request otherwise: "000...0" used to be a valid base 0 or offset 0); a
    dir_announce's numbers may still be short strings, a long one counts as invalid (port: "bad port"; a count: 0)."""
    get_limit = getattr(sys, "get_int_max_str_digits", None)
    set_limit = getattr(sys, "set_int_max_str_digits", None)
    saved = get_limit() if get_limit else None
    long9, long0 = "9" * 100_000, "0" * 100_000
    errors = Errors()
    mp.log.addHandler(errors)
    try:
        for limit in [None] + ([0] if set_limit else []):           # this interpreter as it is; then without a limit
            if limit is not None:
                set_limit(limit)
            server, srv = await serve(directory=True)
            dead = free_port()                                       # (never 24242: the user's own server runs there)
            guarded = mp.request

            async def probe(address, *args, **kw):                   # the directory's checks stay on this test's port
                if address != f"127.0.0.1:{dead}":
                    raise ConnectionRefusedError(f"a check left the test's ports: {address}")
                return await guarded(address, *args, **kw)
            try:
                with Patched(request=probe):
                    mal = Peer("Mal")
                    await mal.join(server.port)
                    t0 = time.monotonic()
                    await mal.raw(('{"t":"set","key":"family","value":1,"base":"' + "9" * 1_500_000
                                   + '","ref":"huge"}\n').encode())
                    m = await mal.wait(lambda m: m.get("ref") == "huge", 5, "an answer to a 1.5M-digit base")
                    assert m["t"] == "error" and m["code"] == "bad_request", (limit, str(m)[:200])
                    assert time.monotonic() - t0 < 3, f"limit {limit}: {time.monotonic() - t0:.1f} s for one set"
                    for i, base in enumerate((long9, long0, "-" + long9, "3", 1.0, True, [1], {"v": 1})):
                        await mal.send({"t": "set", "key": "family", "value": {"n": i}, "base": base, "ref": f"b{i}"})
                        m = await mal.wait(lambda m, r=f"b{i}": m.get("ref") == r, 2, f"an answer to base {i}")
                        assert m["t"] == "error" and m["code"] == "bad_request", (limit, i, str(m)[:200])
                    assert "family" not in server.shared, server.shared["family"]
                    await mal.send({"t": "set", "key": "family", "value": {"n": "ok"}, "base": 0, "ref": "ok"})
                    m = await mal.wait(lambda m: m.get("ref") == "ok", 2, "an answer to base 0")
                    assert m["t"] == "shared" and server.shared["family"]["value"] == {"n": "ok"}, m
                    await mal.send({"t": "set", "key": "family", "value": {"n": 2}, "base": 0, "ref": "stale"})
                    assert (await mal.wait(lambda m: m.get("ref") == "stale", 2))["t"] == "set_refused"

                    f = Peer("Mal", key=mal.key)                     # blob_get: on a file connection
                    await f.open(server.port)
                    assert (await f.hello(files=True))["t"] == "files_ok"
                    f.read()
                    h = "a" * 64
                    for i, o in enumerate((long9, long0, "5", -1, 1.5, True)):
                        await f.send({"t": "blob_get", "h": h, "o": o, "ref": f"o{i}"})
                        m = await f.wait(lambda m, r=f"o{i}": m.get("ref") == r, 2, f"an answer to offset {i}")
                        assert m["t"] == "error" and m["code"] == "bad_request", (limit, i, str(m)[:200])
                    for o in (0, None, 512):                         # (a missing o is 0, as before)
                        await f.send({"t": "blob_get", "h": h, **({} if o is None else {"o": o})})
                    await until(lambda: sum(1 for m in f.msgs if m.get("t") == "blob") == 3, 2, "three blob answers")
                    assert all(m.get("error") == "not shared" for m in f.msgs if m.get("t") == "blob"), f.msgs[-3:]

                    def announce(sid, **kw):                         # before any hello: lines up to 64 KB
                        return {"t": "dir_announce", "id": sid, "secret": "s" * 20, "port": dead, "name": sid, **kw}
                    a = await guarded(f"127.0.0.1:{server.port}", announce("aaaaaaaa", port="0" * 60_000 + str(dead)),
                                      "dir_announce")
                    assert a["ok"] is False and "port" in a["error"], a
                    a = await guarded(f"127.0.0.1:{server.port}", announce("bbbbbbbb", port=str(dead), players="3",
                                                                           max_players=8, protocol=" 1 ",
                                                                           mods_count="9" * 30_000),
                                      "dir_announce", timeout=10)
                    assert a["ok"], a
                    e = server.directory["bbbbbbbb"]
                    assert (e["port"], e["players"], e["max_players"], e["protocol"], e["mods_count"]) == \
                        (dead, 3, 8, 1, 0), {k: str(e[k])[:20] for k in ("port", "players", "mods_count")}
                    a = await guarded(f"127.0.0.1:{server.port}", announce("bbbbbbbb", players="0" * 60_000 + "7"),
                                      "dir_announce", timeout=10)
                    assert a["ok"] and server.directory["bbbbbbbb"]["players"] == 0, (a, server.directory["bbbbbbbb"])
            finally:
                await unserve(server, srv)
    finally:
        if set_limit:
            set_limit(saved)
        mp.log.removeHandler(errors)
    assert not errors.records, errors.records


@test
async def a_closed_connection_is_not_heard_any_more():
    """Review round 3: a line the server reads after it closed the connection (an admin's kick or ban, a reconnect
    that replaced it) isn't handled. One already waiting used to be: a banned player's set landed in the shared state
    (saved, and sent to everyone after their "left"), a skeleton frame re-created their last_pose entry, which nothing
    removed any more, and was relayed after "left". Here the lines are put in the connection's reader at the very
    moment the server closes it (as if they had just arrived), so the order doesn't depend on the sockets. Each case
    puts a different line first: the server used to handle exactly one line after the close."""
    cases = (("kick", ("set", "pose", "chat")), ("ban", ("pose", "set", "chat")), ("reconnect", ("chat", "pose", "set")))
    for how, order in cases:
        server, srv = await serve()
        try:
            ana, ben, cat = Peer("Ana"), Peer("Ben"), Peer("Cat")
            for p in (ana, ben, cat):
                await p.join(server.port)
            ben_id = ben.welcome["id"]
            server.store.settings["admins"].append(ana.welcome["id"])
            await ben.send({"t": "event", "kind": "pose", "data": {"k": "before"}})
            await cat.wait(lambda m: m.get("t") == "event" and m.get("data") == {"k": "before"}, 2, "Ben's frame")
            assert server.last_pose.get(ben_id) == {"k": "before"}, server.last_pose
            bc = server.clients[ben_id]
            lines = {"set": {"t": "set", "key": "family", "value": {"by": f"Ben, after the {how}"}, "ref": "late"},
                     "pose": {"t": "event", "kind": "pose", "data": {"k": "after"}},
                     "chat": {"t": "chat", "text": f"after the {how}"}}
            real_shut = bc.shut

            def shut_with_lines_waiting():
                bc.shut = real_shut                          # (once: handle() closes it again at the end)
                for kind in order:
                    bc.reader.feed_data((json.dumps(lines[kind]) + "\n").encode())
                real_shut()
            bc.shut = shut_with_lines_waiting
            if how == "reconnect":
                ben2 = Peer("Ben", key=ben.key)
                await ben2.join(server.port)
            else:
                await ana.send({"t": "admin", "op": how, "id": ben_id, "ref": 1})
                answer = await ana.wait(lambda m: m.get("t") == "admin" and m.get("ref") == 1, 3, "the admin answer")
                assert answer["ok"], answer
            assert bc.closed
            await until(lambda: bc not in server.conns, 3, "the closed connection's handler ended")
            await asyncio.sleep(0.3)                         # (anything relayed would be at Cat's by now)
            assert "family" not in server.shared, (how, server.shared["family"])
            relayed = [m for m in cat.msgs if m.get("data") == {"k": "after"} or "after the" in str(m.get("text"))
                       or m.get("key") == "family"]
            assert not relayed, (how, relayed)
            if how == "reconnect":                           # still on, through the new connection
                assert server.clients.get(ben_id) is not bc and server.last_pose.get(ben_id) == {"k": "before"}
                await ben2.send({"t": "set", "key": "family", "value": {"by": "Ben again"}, "ref": "new"})
                await ben2.wait(lambda m: m.get("t") == "shared" and m.get("ref") == "new", 2, "the new set")
            else:
                assert ben_id not in server.clients and ben_id not in server.last_pose, (how, server.last_pose)
                await cat.wait(lambda m: m.get("t") == "left" and m.get("id") == ben_id, 2, "Ben left")
            # And a frame from a connection that's no longer its player's is never kept (drop() clears last_pose
            # only for the player's current connection): only relayed, as before.
            await server.on_event(bc, {"t": "event", "kind": "pose", "data": {"k": "ghost"}})
            assert server.last_pose.get(ben_id) == ({"k": "before"} if how == "reconnect" else None), server.last_pose
        finally:
            await unserve(server, srv)


# ====================================================================== disk: transactional writes, crash recovery
@test
async def failed_shared_save_changes_nothing():
    """#18: a set that can't be saved gets storage_failed (with its ref) and changes nothing: no version used, no
    broadcast; the next set gets the next version, and no version is handed out twice across a restart."""
    errors = Errors()
    mp.log.addHandler(errors)
    data = tmpdir("txn")
    server, srv = await serve(data)
    try:
        a, b = Peer("Ana"), Peer("Ben")
        await a.join(server.port)
        await b.join(server.port)
        await a.send({"t": "set", "key": "family", "value": {"n": 1}, "ref": "s1"})
        await b.wait(lambda m: m.get("ref") == "s1", 2)
        seq = server.seq
        real = mp.Store._save

        def failing(path, d):
            if path.parent.name == "shared" and path.name != "_seq.json":
                raise OSError(28, "No space left on device")
            return real(path, d)
        with Patched(mp.Store, _save=staticmethod(failing)):
            await a.send({"t": "set", "key": "family", "value": {"n": 2}, "ref": "s2"})
            m = await a.wait(lambda m: m.get("ref") == "s2", 2, "an answer")
            assert m["t"] == "error" and m["code"] == "storage_failed" and m["key"] == "family", m
            assert server.seq == seq and server.shared["family"]["value"] == {"n": 1}
            await b.none(lambda m: m.get("ref") == "s2", 0.3, "broadcast of a set that wasn't saved")
        await a.send({"t": "set", "key": "family", "value": {"n": 3}, "ref": "s3"})
        m = await b.wait(lambda m: m.get("ref") == "s3", 2)
        assert m["v"] == seq + 1, m
        handed_out = m["v"]
    finally:
        await unserve(server, srv)
        mp.log.removeHandler(errors)
    assert any("couldn't save shared" in r for r in errors.records)
    server, srv = await serve(data)
    try:
        c = Peer("Cat")
        w = await c.join(server.port)
        assert w["shared"]["family"]["value"] == {"n": 3}
        await c.send({"t": "set", "key": "family", "value": {"n": 4}, "ref": "s4"})
        assert (await c.wait(lambda m: m.get("ref") == "s4", 2))["v"] > handed_out
    finally:
        await unserve(server, srv)


@test
async def failed_players_save_never_refuses_a_join():
    """#18: players.json can't be written (disk full, a lock): hellos and profiles still work; it's saved later."""
    errors = Errors()
    mp.log.addHandler(errors)
    server, srv = await serve()
    try:
        real = mp.Store._save

        def failing(path, d):
            if path.name == "players.json":
                raise PermissionError(13, "locked")
            return real(path, d)
        with Patched(mp.Store, _save=staticmethod(failing)):
            a, b = Peer("Ana"), Peer("Ben")
            wa = await a.join(server.port)
            await b.join(server.port)
            await a.send({"t": "profile", "profile": {"first_name": "Ana"}})
            await b.wait(lambda m: m.get("t") == "profile", 2, "the profile")
            assert server.store.players_dirty
        server.tick()                                               # the keepalive's periodic save
        saved = json.loads(server.store.players_file.read_text("utf-8"))
        assert saved[wa["id"]]["profile"] == {"first_name": "Ana"} and not server.store.players_dirty
    finally:
        await unserve(server, srv)
        mp.log.removeHandler(errors)


@test
async def failed_settings_save_is_undone():
    """An admin change that can't be saved is undone in memory too (no password that silently reverts at the next
    start), and the admin is told."""
    errors = Errors()
    mp.log.addHandler(errors)
    server, srv = await serve()
    try:
        a = Peer("Ana")
        await a.join(server.port)
        await a.send({"t": "admin_claim", "code": server.store.settings["admin_code"], "ref": 1})
        assert (await a.wait(lambda m: m.get("t") == "admin" and m.get("ref") == 1, 2))["ok"] is True
        real = mp.Store._save

        def failing(path, d):
            if path.name == "server.json":
                raise OSError(30, "Read-only file system")
            return real(path, d)
        with Patched(mp.Store, _save=staticmethod(failing)):
            await a.send({"t": "admin", "op": "set", "ref": 2, "password": "secret", "motd": "hi"})
            m = await a.wait(lambda m: m.get("t") == "admin" and m.get("ref") == 2, 2, "the answer")
            assert m["ok"] is False and "couldn't save" in m["error"], m
            assert server.store.settings["password"] == "" and server.store.settings["motd"] == ""
        assert json.loads(server.store.settings_file.read_text("utf-8"))["password"] == ""
    finally:
        await unserve(server, srv)
        mp.log.removeHandler(errors)


@test
async def crash_leftovers_are_recovered():
    """#19: files of which only the .bak or the .tmp is left (a crash mid-save) are found at start, used, and written
    back; a save that fails at the swap leaves the old file in place; a damaged file survives several restarts."""
    data = tmpdir("bak")
    server, srv = await serve(data)
    a = Peer("Ana")
    wa = await a.join(server.port)
    await a.send({"t": "set", "key": "family", "value": {"n": 1}, "ref": 1})
    await a.wait(lambda m: m.get("ref") == 1, 2)
    await a.send({"t": "set", "key": "obj:1:lamp", "value": {"on": True}, "ref": 2})
    await a.wait(lambda m: m.get("ref") == 2, 2)
    await a.send({"t": "house_put", "house": {"name": "Casa"}})
    await a.wait(lambda m: m.get("t") == "house_updated", 2)
    await a.send({"t": "mods", "mods": [{"id": "M", "files": [{"p": "m.pak", "h": "a" * 64, "s": 3}]}]})
    await asyncio.sleep(0.2)
    await unserve(server, srv)
    st = server.store
    shared = data / "shared"
    fam = shared / (mp.hashlib.sha1(b"family").hexdigest()[:20] + ".json")
    lamp = shared / (mp.hashlib.sha1(b"obj:1:lamp").hexdigest()[:20] + ".json")
    house = data / "houses" / f"{wa['id']}.json"
    mods = data / "mods" / f"{wa['id']}.json"
    os.replace(fam, fam.with_name(fam.name + ".bak"))               # the old code's crash window: only .bak left
    os.replace(house, house.with_name(house.name + ".tmp"))         # only the fsynced .tmp left
    os.replace(mods, mods.with_name(mods.name + ".bak"))
    os.replace(st.players_file, st.players_file.with_name("players.json.bak"))
    st2 = mp.Store(data)
    seq, items = st2.load_shared()
    assert set(items) == {"family", "obj:1:lamp"}, items
    assert [h["owner"] for h in st2.list_houses()] == [wa["id"]]
    assert wa["id"] in st2.all_mods() and wa["id"] in st2.players
    assert fam.exists() and house.exists() and mods.exists() and st2.players_file.exists(), "not written back"

    real = os.replace

    def broken(src, dst):                                           # the swap itself fails (a lock, a full disk)
        if str(src).endswith(".json.tmp") and not str(src).endswith(".bak.tmp"):
            raise PermissionError(5, "Access is denied")
        return real(src, dst)
    with Patched(os, replace=broken):
        try:
            st2.save_shared(seq + 1, {"key": "obj:1:lamp", "value": {"on": False}, "v": seq + 1}, "obj:1:lamp")
            raise AssertionError("the failing save didn't raise")
        except PermissionError:
            pass
    assert json.loads(lamp.read_text("utf-8"))["value"] == {"on": True}, "the live file went away"
    assert not lamp.with_name(lamp.name + ".tmp").exists()
    fam.write_text("{damaged", "utf-8")
    for _ in range(3):
        assert mp.Store(data).load_shared()[1]["family"]["value"] == {"n": 1}


# ====================================================================== players who are reconnecting
@test
async def events_to_a_reconnecting_player_are_delivered():
    """#15: an event sent to a player during their linger is held and delivered when they're back; if they don't come
    back, the sender gets not_online with its ref; the hold is bounded."""
    with Patched(LINGER=1.0):
        server, srv = await serve()
        try:
            a, b = Peer("Ana"), Peer("Ben")
            await a.join(server.port)
            wb = await b.join(server.port)
            b.drop()
            await until(lambda: server.clients[wb["id"]].closed, 2, "Ben's connection noticed gone")
            await a.send({"t": "event", "kind": "couple", "to": wb["id"], "ref": "x1",
                          "data": {"type": "request", "id": "r1", "action": "hug"}})
            await a.none(lambda m: m.get("t") == "error", 0.3, "error for a held event")
            b2 = Peer("Ben", key=b.key)
            await b2.join(server.port)
            ev = await b2.wait(lambda m: m.get("t") == "event" and m.get("kind") == "couple", 2, "the held event")
            assert ev["data"]["id"] == "r1" and ev["from"] == a.welcome["id"]

            b2.drop()
            await until(lambda: server.clients[wb["id"]].closed, 2, "Ben gone again")
            await a.send({"t": "event", "kind": "couple", "to": wb["id"], "ref": "x2",
                          "data": {"type": "request", "id": "r2"}})
            err = await a.wait(lambda m: m.get("t") == "error" and m.get("ref") == "x2", mp.LINGER + 2, "not_online")
            assert err["code"] == "not_online" and err["kind"] == "couple" and err["id"] == "r2", err
            await a.wait(lambda m: m.get("t") == "left" and m.get("id") == wb["id"], 2, "left")

            b3 = Peer("Ben", key=b.key)
            await b3.join(server.port)
            b3.drop()
            await until(lambda: server.clients[wb["id"]].closed, 2, "Ben gone a third time")
            for i in range(mp.PENDING_MAX + 5):
                await a.send({"t": "event", "kind": "couple", "to": wb["id"], "ref": f"q{i}", "data": {"id": i}})
            await until(lambda: sum(1 for m in a.msgs if m.get("lingering")) >= 5, 2, "the overflow refused")
        finally:
            await unserve(server, srv)


# ====================================================================== abuse limits
def as_stranger(ports: set | None = None):
    """Connections look like they come from another machine (all of them, or those from these local ports)."""
    real = mp.Client.local
    return Patched(mp.Client, local=lambda self: real(self) and ports is not None and self.addr[1] not in ports)


@test
async def hello_is_rate_limited():
    """#16: wrong passwords from another machine are limited (10 a minute); new player records too (5 per 10
    minutes per address), and a full server or a refused hello creates no record."""
    with as_stranger():
        server, srv = await serve(password="pw")
        try:
            for i in range(mp.HELLO_FAILS):
                p = Peer("Mal", key="m" * 20)
                await p.open(server.port)
                assert (await p.hello(password=f"guess{i}"))["code"] == "wrong_password"
            p = Peer("Mal", key="m" * 20)
            await p.open(server.port)
            err = await p.hello(password="pw")
            assert err["code"] == "rate_limited" and err["fatal"], err
            # It passes in a minute, so no companion may take it as permanent: theirs (0.9.37's too) stop reconnecting
            # for good on a fatal error whose text says "password" or "version mismatch" (companion.py's connect_block).
            assert "password" not in err["error"] and "version mismatch" not in err["error"], err
            assert not server.store.players
        finally:
            await unserve(server, srv)
        server, srv = await serve(max_players=10)
        try:
            peers = []
            for i in range(mp.NEW_PLAYERS_PER_IP):
                p = Peer(f"P{i}")
                await p.join(server.port)
                peers.append(p)
            extra = Peer("Extra")
            await extra.open(server.port)
            err = await extra.hello()
            assert err["code"] == "rate_limited" and "password" not in err["error"], err
            assert len(server.store.players) == mp.NEW_PLAYERS_PER_IP
            peers[0].drop()
            back = Peer("P0", key=peers[0].key)                      # a known player is always welcome
            assert (await back.join(server.port))["t"] == "welcome"
        finally:
            await unserve(server, srv)
    server, srv = await serve(max_players=1)
    try:
        await Peer("Ana").join(server.port)
        full = Peer("Ben")
        await full.open(server.port)
        assert (await full.hello())["code"] == "full" and len(server.store.players) == 1
    finally:
        await unserve(server, srv)


@test
async def timeouts_count_silence_not_line_length():
    """#17 / #31: after hello a player is dropped only after TIMEOUT of silence (a long line still arriving is
    talking); before hello a connection must say hello within HELLO_WITHIN, pings or not."""
    with Patched(TIMEOUT=1.0, HELLO_WITHIN=1.0):
        server, srv = await serve()
        try:
            a, b = Peer("Ana"), Peer("Ben")
            await a.join(server.port, read=False)
            await b.join(server.port)

            async def chatty():                                     # Ben talks now and then, like a companion
                while True:
                    await b.send({"t": "pong"})
                    await asyncio.sleep(0.3)
            keep = asyncio.get_running_loop().create_task(chatty())
            line = json.dumps({"t": "chat", "text": "slowly " + "z" * 300}).encode() + b"\n"
            for i in range(0, len(line), 25):                       # ~3.4 s for one line
                await a.raw(line[i:i + 25])
                await asyncio.sleep(0.25)
            await b.wait(lambda m: m.get("t") == "chat" and m["text"].startswith("slowly"), 2, "the slow chat")
            keep.cancel()
            a.read()
            await until(lambda: a.eof, 4, "a silent player is dropped")
            s = Peer("Stranger")
            await s.open(server.port)
            t0 = time.monotonic()
            s.read()
            while not s.eof and time.monotonic() - t0 < 4:
                await s.send({"t": "ping"})
                await asyncio.sleep(0.2)
            assert s.eof and time.monotonic() - t0 < 2.5, "pings kept a connection that never said hello"
        finally:
            await unserve(server, srv)


@test
async def strangers_cannot_hold_every_slot():
    """#31: connections from other machines that haven't joined are capped (MAX_PRE_HELLO); this machine can always
    connect and join."""
    with Patched(MAX_PRE_HELLO=3, MAX_PRE_HELLO_PER_IP=3):
        server, srv = await serve()
        strangers: set = set()
        try:
            with as_stranger(strangers):
                held = []
                for _ in range(4):
                    s = socket.socket()
                    s.bind(("127.0.0.1", 0))
                    strangers.add(s.getsockname()[1])
                    s.setblocking(False)
                    await asyncio.get_running_loop().sock_connect(s, ("127.0.0.1", server.port))
                    held.append(s)
                    await asyncio.sleep(0.1)
                r, w = await asyncio.open_connection(sock=held[3])
                m = json.loads(await asyncio.wait_for(r.readline(), 3))
                assert m["code"] == "busy" and m["fatal"], m
                assert (await Peer("Host").join(server.port))["t"] == "welcome"   # (a local port: not a stranger)
                w.close()
                for s in held[:3]:
                    s.close()
        finally:
            await unserve(server, srv)


@test
async def directory_is_bounded():
    """#29: a directory refuses bad ports, lists a stranger's server where it announced from (it never checks a third
    party's address), caps listings per address and in all, and answers dir_list with at most DIR_LIST_MAX. The
    listings point at a port nothing listens on, and every check the directory makes stays on this test's ports."""
    server, srv = await serve(directory=True)
    dead = free_port()                               # (never 24242: the user's own server runs there)
    probed = []
    guarded = mp.request

    async def probe(address, *args, **kw):           # the directory's own checks: only ever to this test's ports
        probed.append(address)
        if address not in (f"127.0.0.1:{dead}", f"127.0.0.1:{server.port}"):
            raise ConnectionRefusedError(f"a check left the test's ports: {address}")
        return await guarded(address, *args, **kw)
    try:
        with Patched(request=probe):
            def announce(sid, **kw):
                return {"t": "dir_announce", "id": sid, "secret": "s" * 20, "port": dead, "name": sid, **kw}
            a = await guarded(f"127.0.0.1:{server.port}", announce("aaaaaaaa", port=99999), "dir_announce")
            assert a["ok"] is False and "port" in a["error"]
            with as_stranger():
                a = await guarded(f"127.0.0.1:{server.port}", announce("bbbbbbbb", address=f"10.255.255.1:{dead}"),
                                  "dir_announce", timeout=10)
                assert a["ok"] and a["address"] == f"127.0.0.1:{dead}" and a["reachable"] is False, a
                assert probed == [f"127.0.0.1:{dead}"], probed       # (checked where it announced from, not 10.255...)
                for i in range(mp.MAX_LISTED_PER_IP - 1):
                    a = await guarded(f"127.0.0.1:{server.port}", announce(f"c{i}cccccc"), "dir_announce", timeout=10)
                    assert a["ok"], a
                a = await guarded(f"127.0.0.1:{server.port}", announce("dddddddd"), "dir_announce", timeout=10)
                assert a["ok"] is False, a
                assert set(probed) == {f"127.0.0.1:{dead}"}, probed
            with Patched(MAX_LISTED=len(server.directory)):
                a = await guarded(f"127.0.0.1:{server.port}", announce("eeeeeeee"), "dir_announce")
                assert a["ok"] is False and "full" in a["error"], a
            for i in range(300):
                server.directory[f"{i:08x}"] = {"id": f"{i:08x}", "secret": "x" * 16, "address": f"h{i}:1", "local": False,
                                                "port": 1, "seen": mp.now(), "heard": getattr(mp, "mono", mp.now)(),
                                                "checked": getattr(mp, "mono", mp.now)(), "reachable": True,
                                                "error": "", "ip": "1.2.3.4", "name": str(i)}
            lst = await guarded(f"127.0.0.1:{server.port}", {"t": "dir_list"}, "dir_list")
            assert len(lst["servers"]) == mp.DIR_LIST_MAX and not {"ip", "heard"} & set(lst["servers"][0])
    finally:
        await unserve(server, srv)


@test
async def announcer_survives_nonsense_and_request_has_one_deadline():
    """#28: a directory answering something that isn't an object doesn't kill the announcer; start()'s tasks still
    stop (and free the UDP port) when cancelled; request() gives up after its timeout even on a peer that keeps
    sending bytes."""
    async def nonsense(r, w):
        try:
            while await r.readline():
                w.write(b"[]\n")
                await w.drain()
        except (ConnectionError, OSError):
            pass

    async def dribble(r, w):
        try:
            for _ in range(100):
                w.write(b"x")
                await w.drain()
                await asyncio.sleep(0.1)
        except (ConnectionError, OSError):
            pass
    fake = await asyncio.start_server(nonsense, "127.0.0.1", 0)
    slow = await asyncio.start_server(dribble, "127.0.0.1", 0)
    fport, sport = fake.sockets[0].getsockname()[1], slow.sockets[0].getsockname()[1]
    try:
        t0 = time.monotonic()
        try:
            await mp.request(f"127.0.0.1:{sport}", {"t": "info"}, "info", timeout=1)
            raise AssertionError("no timeout")
        except asyncio.TimeoutError:
            assert time.monotonic() - t0 < 2
        try:
            await mp.request(f"127.0.0.1:{fport}", {"t": "info"}, "info", timeout=3)
            raise AssertionError("a non-object answer was taken")
        except ValueError:
            pass
        with Patched(ANNOUNCE_EVERY=0.2):
            args = argparse.Namespace(host="127.0.0.1", port=0, password="", motd=None, max_players=None,
                                      data=str(tmpdir("ann")), hosted=False, name="Casa", public=True,
                                      directories=[f"127.0.0.1:{fport}"])
            srv, tasks = await mp.start(args)
            server = srv.mp_server
            await until(lambda: f"127.0.0.1:{fport}" in server.public_status, 3, "an announcement")
            await asyncio.sleep(0.6)
            assert not tasks.done(), "the background tasks died"
            assert server.public_status[f"127.0.0.1:{fport}"]["ok"] is False
            await asyncio.wait_for(mp.stop(srv, tasks), 6)
            assert server.udp is None and tasks.done()
    finally:
        for s in (fake, slow):
            s.close()


class Records(logging.Handler):
    """Every record of one level and up (a test that checks a warning was given)."""

    def __init__(self, level=logging.WARNING):
        super().__init__(level=level)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage()[:300])


@test
async def hosting_works_without_lan_discovery():
    """start() binds UDP on the TCP port's number for LAN discovery. When that UDP port can't be had (another program
    holds it; on Windows, a range Hyper-V reserves fails with WSAEACCES, which review round 3 hit with a TCP port 0)
    hosting still works: a warning says LAN discovery is off, server.udp stays None, players join, stop() is clean."""
    warnings = Records()
    mp.log.addHandler(warnings)
    hold = None
    try:
        for _ in range(5):
            # A UDP port held here whose number is free for TCP. Tried for a while, not 50 times in a row: while other
            # programs open and close many connections, Windows refuses TCP the numbers of hundreds of UDP ports in
            # a row for seconds (WSAEACCES; seen with the other test suites running alongside).
            deadline = time.monotonic() + 20
            while True:
                hold = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):       # (Windows: nobody may share it, whatever they ask)
                    hold.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                hold.bind(("127.0.0.1", 0))
                port = hold.getsockname()[1]
                t = socket.socket()
                try:
                    t.bind(("127.0.0.1", port))
                    break
                except OSError:
                    hold.close()
                    if time.monotonic() > deadline:
                        raise AssertionError("no UDP port whose number is free for TCP") from None
                finally:
                    t.close()
                await asyncio.sleep(0.005)
            assert port not in LIVE_PORTS
            args = argparse.Namespace(host="127.0.0.1", port=port, password="", motd=None, max_players=None,
                                      data=str(tmpdir("noudp")), hosted=False, name="Casa")
            try:
                srv, tasks = await mp.start(args)
                break
            except OSError:                          # the TCP port was taken in between: another one
                hold.close()
        else:
            raise AssertionError("start() never got a TCP port")
        server = srv.mp_server
        try:
            assert server.port == port and server.udp is None
            assert any("LAN discovery is off" in r for r in warnings.records), warnings.records
            ana = Peer("Ana")
            assert (await ana.join(port))["t"] == "welcome"
            assert server.discover_reply()["players"] == 1
            await asyncio.sleep(0.2)
            assert not tasks.done(), "the background tasks died without the UDP endpoint"
            ana.drop()
        finally:
            await asyncio.wait_for(mp.stop(srv, tasks), 6)
        assert server.udp is None and tasks.done()
    finally:
        mp.log.removeHandler(warnings)
        if hold is not None:
            hold.close()


@test
async def shared_state_epoch_refs_removal_and_cap():
    """Interface item 1: the welcome and shared_all carry the data folder's epoch (kept across restarts, new for a new
    folder, created for an old folder without one); every "shared" carries the sender's ref to everyone; set_refused
    carries ref and current; value null removes the key (and its file); the total size is capped (too_big)."""
    data = tmpdir("epoch")
    server, srv = await serve(data)
    try:
        a, b = Peer("Ana"), Peer("Ben")
        wa = await a.join(server.port)
        await b.join(server.port)
        epoch = wa["epoch"]
        assert isinstance(epoch, str) and len(epoch) >= 8
        await a.send({"t": "shared_get"})
        assert (await a.wait(lambda m: m.get("t") == "shared_all", 2))["epoch"] == epoch
        await a.send({"t": "set", "key": "family", "value": {"n": 1}, "ref": "r1"})
        m = await b.wait(lambda m: m.get("t") == "shared" and m.get("ref") == "r1", 2, "the ref, at Ben's")
        assert m["ref_by"] == wa["id"]
        await b.send({"t": "set", "key": "family", "value": {"n": 0}, "base": 0, "ref": "r2"})
        m = await b.wait(lambda m: m.get("t") == "set_refused", 2)
        assert m["ref"] == "r2" and m["current"]["value"] == {"n": 1}
        await a.send({"t": "set", "key": "family", "value": None, "ref": "r3"})
        m = await b.wait(lambda m: m.get("ref") == "r3", 2)
        assert m["item"] is None and "family" not in server.shared
        assert not list((data / "shared").glob(mp.hashlib.sha1(b"family").hexdigest()[:20] + "*"))
        with Patched(MAX_SHARED_TOTAL=12_000):
            await a.send({"t": "set", "key": "house:1:live", "value": "x" * 6000, "ref": "big1"})
            await a.wait(lambda m: m.get("ref") == "big1" and m.get("t") == "shared", 2)
            await a.send({"t": "set", "key": "house:2:live", "value": "y" * 6000, "ref": "big2"})
            m = await a.wait(lambda m: m.get("ref") == "big2", 2)
            assert m["t"] == "error" and m["code"] == "too_big" and m["key"] == "house:2:live", m
            await a.send({"t": "set", "key": "house:1:live", "value": None, "ref": "free"})
            await a.wait(lambda m: m.get("ref") == "free" and m.get("t") == "shared", 2)
            await a.send({"t": "set", "key": "house:2:live", "value": "y" * 6000, "ref": "big3"})
            assert (await a.wait(lambda m: m.get("ref") == "big3", 2))["t"] == "shared"
    finally:
        await unserve(server, srv)
    server, srv = await serve(data)
    try:
        assert (await Peer("Cat").join(server.port))["epoch"] == epoch
    finally:
        await unserve(server, srv)
    server, srv = await serve()
    try:
        assert (await Peer("Dan").join(server.port))["epoch"] != epoch
    finally:
        await unserve(server, srv)
    legacy = tmpdir("legacy")
    (legacy / "shared").mkdir()
    (legacy / "shared" / "_seq.json").write_text('{"seq": 7}', "utf-8")
    s1 = mp.Server(mp.Store(legacy))
    assert s1.seq == 7 and s1.store.shared_epoch
    assert mp.Server(mp.Store(legacy)).store.shared_epoch == s1.store.shared_epoch


def shared_file(data: Path, key: str) -> list:
    return list((data / "shared").glob(mp.hashlib.sha1(key.encode()).hexdigest()[:20] + ".json*"))


@test
async def full_shared_state_clears_old_lots():
    """The cap is no dead end: nothing else ever removes shared keys (every lot the couple lived on keeps its house and
    object keys), so when a set would pass the total size (or the key count) whole old lots go first - the one changed
    longest ago first, as item-null removals everyone gets, before the set itself. Never the set's own lot, the
    family's home, a lot in use (changed in the last LOT_IN_USE s) or a key that isn't a lot's; nothing at all when
    even that wouldn't make room (too_big); a removal that can't be saved refuses the set (storage_failed); a restart
    brings back exactly what's left."""
    day = 86400
    data = tmpdir("room")
    server, srv = await serve(data)
    try:
        a, b = Peer("Ana"), Peer("Ben")
        await a.join(server.port)
        await b.join(server.port)

        async def put(key, value, age=0.0, expect="shared"):
            ref = f"put-{key}-{len(a.msgs)}"
            await a.send({"t": "set", "key": key, "value": value, "ref": ref})
            m = await a.wait(lambda m: m.get("ref") == ref, 2, key)
            assert (m["t"], m.get("code")) == (("shared", None) if expect == "shared" else ("error", expect)), m
            if expect == "shared" and age:
                server.shared[key]["at"] = mp.now() - age            # (as if it was set that long ago)
            return m

        def removals(peer):
            return [m["key"] for m in peer.msgs if m.get("t") == "shared" and m.get("item") is None]
        await put("family", {"together": True, "home_site": 5})
        await put("house:5:live", "h" * 3000, 40 * day)             # the family's home: oldest of all, never cleared
        await put("house:1:exact", "e" * 3000, 31 * day)            # an old home...
        await put("house:1:live", "l" * 500, 30 * day)
        await put("obj:1:tv", {"on": True}, 29 * day)               # (a lot's age: its newest key)
        await put("house:2:live", "m" * 3000, 20 * day)             # ...and a less old one
        await put("obj:2:lamp", {"on": False}, 20 * day)
        await put("house:3:live", "n" * 3000, 60)                   # in use: changed a minute ago
        await put("misc", "z" * 500, 50 * day)                      # not a lot's key
        lot1 = ["house:1:exact", "house:1:live", "obj:1:tv"]
        assert all(shared_file(data, k) for k in lot1)

        with Patched(MAX_SHARED_TOTAL=server.shared_total + 1000):
            before = len(b.msgs)
            m = await put("house:4:live", "x" * 2500, expect="shared")       # grows by 2700: lot 1 makes room
            await b.wait(lambda x: x.get("ref") == m["ref"], 2, "the set, at Ben's")
            got = b.msgs[before:]
            gone = [x["key"] for x in got if x.get("t") == "shared" and x.get("item") is None]
            assert sorted(gone) == sorted(lot1), gone
            assert all(x["ref_by"] is None for x in got if x.get("item") is None)
            assert [x.get("ref") for x in got][-1] == m["ref"], "the removals come before the set"
            assert not any(k in server.shared for k in lot1) and not any(shared_file(data, k) for k in lot1)
            assert {"house:2:live", "obj:2:lamp", "house:3:live", "house:5:live", "misc", "family"} <= set(server.shared)
            assert server.shared_total <= mp.MAX_SHARED_TOTAL

            n = len(removals(b))
            await put("house:4:live", "x" * 9000, expect="too_big")         # even without lot 2 it wouldn't fit
            await b.none(lambda x: len(removals(b)) > n, 0.3, "a lot cleared for a set that was refused anyway")
            assert "house:2:live" in server.shared

        with Patched(MAX_SHARED_KEYS=len(server.shared)):
            await put("obj:4:radio", {"on": True})                          # a new key past the count: lot 2 goes
            assert "house:2:live" not in server.shared and "obj:2:lamp" not in server.shared
        with Patched(MAX_SHARED_KEYS=len(server.shared)):
            await put("obj:4:fan", {"on": True}, expect="too_big")          # only the home, lot 3 (in use), lot 4
            assert {"house:3:live", "house:5:live", "misc", "family"} <= set(server.shared)

            server.shared["house:3:live"]["at"] = mp.now() - day            # lot 3 is old now, but its removal...
            seq = server.seq

            def disk_full(self, seq):
                raise OSError(28, "No space left on device")
            with Patched(mp.Store, save_seq=disk_full):
                await put("obj:4:fan", {"on": True}, expect="storage_failed")  # ...can't be saved: nothing changed
            assert "house:3:live" in server.shared and "obj:4:fan" not in server.shared and server.seq == seq
            await put("obj:4:fan", {"on": True})                            # (and then it can)
            assert "house:3:live" not in server.shared

        await put("house:1:live", None)                                     # removing what isn't there: harmless
        await put("house:4:live", "y" * 100)                                # shrinking: always taken
        await a.send({"t": "admin_claim", "code": server.store.settings["admin_code"], "ref": "claim"})
        await a.wait(lambda m: m.get("t") == "admin" and m.get("ref") == "claim", 2)
        n = len(removals(b))                                                # an admin's removal: the same batch path
        await a.send({"t": "admin", "op": "shared_remove", "prefix": "obj:4:", "ref": "rm"})
        m = await a.wait(lambda m: m.get("t") == "admin" and m.get("ref") == "rm", 2, "the admin's answer")
        assert m["ok"] and m["removed"] == 2, m
        await b.wait(lambda x: len(removals(b)) == n + 2, 2, "both removals")
        assert not any(k.startswith("obj:4:") for k in server.shared) and not shared_file(data, "obj:4:radio")
        keys, seq = {k: it["v"] for k, it in server.shared.items()}, server.seq
    finally:
        await unserve(server, srv)
    again = mp.Server(mp.Store(data))
    assert {k: it["v"] for k, it in again.shared.items()} == keys and again.seq >= seq
    assert again.shared_total == sum(again.shared_size.values())


@test
async def family_move_at_the_cap_keeps_the_new_home():
    """Review round 2: a "family" set that moves the home (Ana moves in with Ben) when the shared state is full must
    never clear the new home's keys to make room for itself (it used to: only the stored home was kept, so Ben's house
    went for everyone and the old home stayed). The old home, no longer the home after the set, goes only if nothing
    else is enough."""
    day = 86400
    server, srv = await serve()
    try:
        a = Peer("Ana")
        await a.join(server.port)

        async def put(key, value, age=0.0, expect="shared"):
            ref = f"put-{key}-{len(a.msgs)}"
            await a.send({"t": "set", "key": key, "value": value, "ref": ref})
            m = await a.wait(lambda m: m.get("ref") == ref, 2, key)
            assert (m["t"], m.get("code")) == (("shared", None) if expect == "shared" else ("error", expect)), m
            if expect == "shared" and age:
                server.shared[key]["at"] = mp.now() - age
            return m

        def family(home, pad):
            return {"together": True, "home_site": home, "members": ["pa", "pb"], "pad": "p" * pad}
        await put("family", family(5, 0))
        await put("house:5:live", "a" * 3000, 9 * day)               # Ana's home (the family's): the oldest lot
        await put("house:6:live", "b" * 3000, 8 * day)               # Ben's home, where she's moving: older than 7
        await put("obj:6:tv", {"on": True}, 8 * day)
        await put("house:7:live", "c" * 3000, 3 * day)               # some other lot
        with Patched(MAX_SHARED_TOTAL=server.shared_total + 100):
            await put("family", family(6, 1000))                    # grows by ~1000: one lot must go
        assert server.home_lot() == "6"
        assert "house:7:live" not in server.shared, "lot 7 should have made room"
        assert {"house:5:live", "house:6:live", "obj:6:tv"} <= set(server.shared), \
            f"the new home (or the old one, though lot 7 was enough) was cleared: {sorted(server.shared)}"

        # They move on to lot 9 (5 days old: by age it would go before lot 6, the home they leave). Making room needs
        # two lots: lot 5 (now just an old lot), then the old home 6 - never the new home 9.
        await put("house:9:live", "d" * 3000, 5 * day)
        cap = server.shared_total + 100
        with Patched(MAX_SHARED_TOTAL=cap):
            await put("family", family(9, 4500))                    # grows by ~3500: more than lot 5 alone
        assert server.home_lot() == "9" and "house:9:live" in server.shared, sorted(server.shared)
        assert not any(k.startswith(("house:5:", "house:6:", "obj:6:")) for k in server.shared), sorted(server.shared)
        assert server.shared_total <= cap
    finally:
        await unserve(server, srv)


def write_shared(data: Path, items: dict, seq_file: bool = True) -> None:
    """A shared state on disk as an older server left it: items = {key: (value, age in seconds)}, no epoch."""
    folder = data / "shared"
    folder.mkdir(parents=True, exist_ok=True)
    for v, (key, (value, age)) in enumerate(items.items(), 1):
        (folder / (mp.hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")).write_text(json.dumps(
            {"key": key, "value": value, "v": v, "by": None, "by_name": "old", "at": mp.now() - age}), "utf-8")
    if seq_file:
        (folder / "_seq.json").write_text(json.dumps({"seq": len(items)}), "utf-8")


@test
async def over_cap_folder_is_trimmed_at_start():
    """Review round 2: a data folder whose shared state is over the cap (written by a server that had none: nothing
    limited it) is brought under the cap when the server starts, before anyone joins. Otherwise every welcome carries all
    of it, past what a companion reads in one line (32 MB), nobody can join (an admin neither, to clear it), and no set
    ever comes to make room. make_room's rules: whole lots, oldest first, never the family's home nor a key that isn't
    a lot's; lots in use (changed in the last 10 minutes) only if the rest isn't enough. What's left survives a restart
    (same epoch); a removal that can't be saved never stops the server from starting."""
    day, mb = 86400, 1024 * 1024
    # The review's folder, at full size: ten old exact houses of 4 MB (a couple that moved many times) and the home.
    data = tmpdir("overcap")
    items = {"family": ({"together": True, "home_site": 3, "members": ["pa", "pb"]}, day)}
    for lot in range(10):
        items[f"house:{lot}:exact"] = ("x" * (4 * mb - 300), (20 - lot) * day)    # lot 0 the oldest; lot 3 the home
    write_shared(data, items)
    server = mp.Server(mp.Store(data))
    assert server.shared_total <= mp.MAX_SHARED_TOTAL, server.shared_total
    assert {"family", "house:3:exact"} <= set(server.shared), sorted(server.shared)
    kept = sorted(int(k.split(":")[1]) for k in server.shared if k.startswith("house:") and k != "house:3:exact")
    assert kept == list(range(10 - len(kept), 10)), f"not the oldest lots first: kept {kept}"
    for lot in range(10):
        assert bool(shared_file(data, f"house:{lot}:exact")) == (f"house:{lot}:exact" in server.shared)
    line = mp.encode(server.welcome(type("C", (), {"id": "p00000000"})()))
    assert len(line) < mp.MAX_SHARED_TOTAL + 64 * 1024, f"welcome {len(line) >> 20} MB"
    keys, epoch = {k: it["v"] for k, it in server.shared.items()}, server.store.shared_epoch
    again = mp.Server(mp.Store(data))
    assert {k: it["v"] for k, it in again.shared.items()} == keys and again.store.shared_epoch == epoch
    assert again.seq >= server.seq > len(items)                       # the removals used versions: never handed out again
    del server, again, line

    # Small caps: the rules in detail.
    def folder():
        d = tmpdir("overcap-rules")
        write_shared(d, {
            "family": ({"together": True, "home_site": 1}, 40 * day),
            "house:1:live": ("h" * 3000, 30 * day),                    # the home: oldest of all, never cleared
            "house:2:live": ("o" * 3000, 20 * day), "obj:2:tv": ({"on": True}, 20 * day),
            "house:3:live": ("p" * 3000, 10 * day),
            "house:4:live": ("q" * 3000, 120),                         # in use before the restart
            "house:5:live": ("r" * 3000, 30),                          # in use, more recently
            "misc": ("z" * 3000, 50 * day),                            # not a lot's key
        })
        return d
    d = folder()
    whole = mp.Server(mp.Store(d)).shared_total                       # (under the real cap: untouched)
    assert len(mp.Server(mp.Store(d)).shared) == 8
    with Patched(MAX_SHARED_TOTAL=whole - 5000):                      # lots 2 and 3 are enough
        s = mp.Server(mp.Store(folder()))
        assert set(s.shared) == {"family", "house:1:live", "house:4:live", "house:5:live", "misc"}, sorted(s.shared)
    with Patched(MAX_SHARED_TOTAL=whole - 9000):                      # and then lot 4 (in use), never lot 1 or misc
        s = mp.Server(mp.Store(folder()))
        assert set(s.shared) == {"family", "house:1:live", "house:5:live", "misc"}, sorted(s.shared)
    with Patched(MAX_SHARED_TOTAL=1000):                              # can't get under: every lot but the home goes
        s = mp.Server(mp.Store(folder()))
        assert set(s.shared) == {"family", "house:1:live", "misc"}, sorted(s.shared)
    with Patched(MAX_SHARED_KEYS=5):                                  # the key count too: lot 2 (2 keys), lot 3
        s = mp.Server(mp.Store(folder()))
        assert set(s.shared) == {"family", "house:1:live", "house:4:live", "house:5:live", "misc"}, sorted(s.shared)

    d = folder()                                                      # a disk that can't be written: it still starts
    errors = Errors()
    mp.log.addHandler(errors)

    def disk_full(self, seq):
        raise OSError(28, "No space left on device")
    try:
        with Patched(MAX_SHARED_TOTAL=whole - 5000), Patched(mp.Store, save_seq=disk_full):
            s = mp.Server(mp.Store(d))
            assert len(s.shared) == 8 and s.shared_total == whole
        assert any("couldn't clear lot" in r for r in errors.records), errors.records
    finally:
        mp.log.removeHandler(errors)
    with Patched(MAX_SHARED_TOTAL=whole - 5000):                      # the next start does it
        assert len(mp.Server(mp.Store(d)).shared) == 5


@test
async def file_connections_belong_to_joined_players():
    """Review round 2, #31 for file connections: on a server without a password a stranger used to hold every slot with
    file connections (they may sit idle for 10 minutes) without joining, and the real partner got "busy". From another
    machine a file hello now needs its player on the server (or in the linger after a broken connection) and makes no
    player record otherwise; a player has at most MAX_FILE_CONNS (the quietest go first); they close when their player
    leaves. From this machine (the host's own program) a file hello needs no joined player and its connections stay
    when the player leaves, but the per-player cap holds there too (review round 3: PROTOCOL.md said "not limited")."""
    with Patched(LINGER=0.5, MAX_CONNS=6, MAX_CONNS_PER_IP=6):
        server, srv = await serve()
        try:
            with as_stranger():
                for _ in range(6):                                    # the review's attack: file hellos, no join
                    s = Peer("Mal", key="m" * 20)
                    await s.open(server.port)
                    err = await s.hello(files=True)
                    assert err["t"] == "error" and err["code"] == "not_joined" and err["fatal"], err
                    assert "password" not in err["error"] and "version mismatch" not in err["error"], err
                    s.drop()
                assert not server.store.players, "a refused file hello made a player record"
                await until(lambda: not server.conns, 4, "the refused file connections closed")
                ben = Peer("Ben")
                wb = await ben.join(server.port)                      # the real partner gets in
                files = []
                for i in range(mp.MAX_FILE_CONNS):
                    f = Peer("Ben", key=ben.key)
                    await f.open(server.port)
                    assert (await f.hello(files=True))["t"] == "files_ok"
                    f.read()
                    files.append(f)
                    await asyncio.sleep(0.05)
                await files[0].send({"t": "ping"})                    # the first is busy now: the second is quietest
                await files[0].wait(lambda m: m.get("t") == "pong", 2, "pong")
                extra = Peer("Ben", key=ben.key)
                await extra.open(server.port)
                assert (await extra.hello(files=True))["t"] == "files_ok"
                extra.read()
                await until(lambda: files[1].eof, 4, "the quietest file connection closed")
                assert not files[0].eof and not files[2].eof and not extra.eof
                assert sum(1 for o in server.conns if o.files and o.id == wb["id"] and not o.closed) == \
                    mp.MAX_FILE_CONNS
                await extra.send({"t": "blob_check", "h": ["0" * 64]})
                assert (await extra.wait(lambda m: m.get("t") == "blob_missing", 2))["h"] == ["0" * 64]

                await ben.send({"t": "bye"})                          # Ben leaves: his file connections go too
                ben.drop()
                await until(lambda: files[0].eof and files[2].eof and extra.eof, 4, "file connections closed")
                late = Peer("Ben", key=ben.key)                       # known, but not on the server
                await late.open(server.port)
                assert (await late.hello(files=True))["code"] == "not_joined"
                late.drop()

                ben2 = Peer("Ben", key=ben.key)                       # back; then his connection breaks (no bye)
                await ben2.join(server.port)
                ben2.drop()
                f = Peer("Ben", key=ben.key)                          # in the linger: still his
                await f.open(server.port)
                assert (await f.hello(files=True))["t"] == "files_ok"
                f.read()
                await until(lambda: f.eof, 4, "the file connection closed when the linger ended")
                assert wb["id"] not in server.clients
            local = Peer("Host")                                      # this machine: no join needed
            await local.open(server.port)
            assert (await local.hello(files=True))["t"] == "files_ok"
            local.read()
            hid = server.store.find_player(local.key)
            lfiles = [local]
            for _ in range(mp.MAX_FILE_CONNS):                        # ... but the same cap: one more than allowed
                await asyncio.sleep(0.05)
                f = Peer("Host", key=local.key)
                await f.open(server.port)
                assert (await f.hello(files=True))["t"] == "files_ok"
                f.read()
                lfiles.append(f)
            await until(lambda: local.eof, 4, "the quietest local file connection closed")
            assert not any(f.eof for f in lfiles[1:])
            assert sum(1 for o in server.conns if o.files and o.id == hid and not o.closed) == mp.MAX_FILE_CONNS
            host = Peer("Host", key=local.key)                        # the host joins and leaves: they stay
            await host.join(server.port)
            await host.send({"t": "bye"})
            host.drop()
            await until(lambda: hid not in server.clients, 4, "the host left")
            await asyncio.sleep(0.2)
            assert not any(f.eof for f in lfiles[1:]), "a local file connection closed when its player left"
            await lfiles[1].send({"t": "blob_check", "h": ["0" * 64]})
            assert (await lfiles[1].wait(lambda m: m.get("t") == "blob_missing", 2))["h"] == ["0" * 64]
            for f in lfiles:
                f.drop()
        finally:
            await unserve(server, srv)


@test
async def recovering_from_the_tmp_never_loses_it():
    """A file recovered from its .tmp (the primary missing or damaged) is renamed into place, never rewritten: if the
    rename fails (a lock that outlasts the retries) the .tmp is still there for the next start, which must not fall
    back to the older .bak. A .tmp that can't be read right now (locked) isn't written over from the .bak either."""
    data = tmpdir("tmprecover")
    store = mp.Store(data)
    folder = data / "shared"
    folder.mkdir(exist_ok=True)
    path = folder / "abc.json"
    tmp, bak = path.with_name("abc.json.tmp"), path.with_name("abc.json.bak")

    def leftovers():                                 # an old-style crash: older .bak, complete newer .tmp, no primary
        for p in (path, tmp, bak):
            if p.exists():
                p.unlink()
        bak.write_text(json.dumps({"key": "family", "value": "old", "v": 1}), "utf-8")
        tmp.write_text(json.dumps({"key": "family", "value": "new", "v": 2}), "utf-8")
    real_replace = os.replace

    def locked_swap(src, dst):
        if str(src).endswith("abc.json.tmp"):
            raise PermissionError(32, "in use (an antivirus)")
        return real_replace(src, dst)
    leftovers()
    with Patched(os, replace=locked_swap):
        assert store._load(path, None)["value"] == "new"
    assert tmp.exists(), "the only copy of the newest content was deleted"
    assert mp.Store(data)._load(path, None)["value"] == "new"                # the next start: still the newest
    assert path.exists() and not tmp.exists() and json.loads(bak.read_text("utf-8"))["value"] == "old"
    assert mp.Store(data)._load(path, None)["value"] == "new"

    leftovers()
    real_read = Path.read_text

    def locked_read(self, *args, **kw):
        if self.name == "abc.json.tmp":
            raise PermissionError(32, "in use (an antivirus)")
        return real_read(self, *args, **kw)
    with Patched(Path, read_text=locked_read):
        assert store._load(path, None)["value"] == "old"                    # (what can be read now)
    assert not path.exists() and json.loads(tmp.read_text("utf-8"))["value"] == "new", "written over the .tmp"
    assert mp.Store(data)._load(path, None)["value"] == "new"

    leftovers()                                      # a .bak alone (the .tmp damaged) is still written back
    tmp.write_text("{damaged", "utf-8")
    assert store._load(path, None)["value"] == "old" and json.loads(path.read_text("utf-8"))["value"] == "old"

    leftovers()                                      # a damaged primary that can't be moved aside (locked)...
    tmp.write_text("{damaged", "utf-8")
    path.write_text("{damaged too", "utf-8")
    with Patched(mp.Store, _set_aside=lambda self, p: None):
        assert store._load(path, None)["value"] == "old"
    assert json.loads(bak.read_text("utf-8"))["value"] == "old", "the damaged file became the backup"
    assert mp.Store(data)._load(path, None)["value"] == "old" and json.loads(path.read_text("utf-8"))["value"] == "old"


class DiskLog:
    """Records, in order, what the server does to the disk and when it tells anyone: every fsync, every folder flush
    (Store._sync_folder: a no-op on Windows, but its call is what makes a rename or a delete last through a power cut
    on Linux), every delete and replace, each removal broadcast and each blob_ok. Used as a context manager."""

    def __init__(self):
        self.events: list[tuple] = []
        self.patches = []

    def __enter__(self):
        ev, real_sync, real_unlink = self.events, mp.Store._sync_folder, mp._unlink
        real_fsync, real_replace, real_flush = os.fsync, os.replace, mp.fsync_file
        real_bc, real_push = mp.Server.broadcast_now, mp.Client.push

        def broadcast_now(server, msg, exclude=None):
            if msg.get("t") == "shared" and msg.get("item") is None:
                ev.append(("said", msg["key"]))
            return real_bc(server, msg, exclude)

        def push(c, data, key=None):
            if b'"t":"blob_ok"' in data:
                ev.append(("blob_ok",))
            return real_push(c, data, key)
        self.patches = [
            Patched(mp.Store, _sync_folder=staticmethod(lambda p: (ev.append(("sync", Path(p).parent.name)),
                                                                   real_sync(p))[1])),
            Patched(mp, _unlink=lambda p: (ev.append(("unlink", Path(p).name)), real_unlink(p))[1],
                    fsync_file=lambda p: (ev.append(("flush", Path(p).name)), real_flush(p))[1]),
            Patched(os, fsync=lambda fd: (ev.append(("fsync",)), real_fsync(fd))[1],
                    replace=lambda a, b: (ev.append(("replace", Path(a).name, Path(b).name)), real_replace(a, b))[1]),
            Patched(mp.Server, broadcast_now=broadcast_now), Patched(mp.Client, push=push)]
        for p in self.patches:
            p.__enter__()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.__exit__(*exc)

    def index(self, event: tuple) -> int:
        assert event in self.events, f"no {event} in {self.events}"
        return self.events.index(event)


def shared_name(key: str) -> str:
    return mp.hashlib.sha1(key.encode()).hexdigest()[:20] + ".json"


@test
async def removals_are_on_disk_before_anyone_hears():
    """Review round 3: a removal (a set to null, an admin's shared_remove, make_room clearing an old lot) went to
    everyone before it was on disk for good: the key's files were deleted, but the folder was never flushed after, so
    on a Pi a power cut right after brought the key back at the next start (with an older version than the removal
    everyone saw: the admin's removal undone, a cleared lot back). Now the folder is flushed after a removed key's
    deletes and before its item-null goes out; a batch (remove_shared) deletes every key's files, flushes once and
    only then tells anyone; one that fails part way tells exactly the keys whose files are gone, and a restart agrees."""
    day = 86400
    data = tmpdir("rmflush")
    server, srv = await serve(data)
    try:
        a = Peer("Ana")
        await a.join(server.port)

        async def put(key, value, age=0.0):
            ref = f"put-{key}-{len(a.msgs)}"
            await a.send({"t": "set", "key": key, "value": value, "ref": ref})
            m = await a.wait(lambda m: m.get("ref") == ref, 2, key)
            assert m["t"] == "shared", m
            if age:
                server.shared[key]["at"] = mp.now() - age

        for k in ("obj:1:tv", "obj:1:lamp", "obj:1:fan", "obj:5:a", "obj:5:b", "obj:5:c"):
            await put(k, {"on": True})
        await put("house:2:live", "h" * 3000, 3 * day)
        await put("obj:2:radio", {"on": False}, 3 * day)
        await put("house:3:live", "n" * 500)                          # in use: never cleared for room

        def durable(log: DiskLog, keys) -> None:
            for k in keys:
                said = log.index(("said", k))
                deletes = [i for i, e in enumerate(log.events) if e == ("unlink", shared_name(k))]
                assert deletes and max(deletes) < said, f"{k}: not deleted before it was said: {log.events}"
                assert ("sync", "shared") in log.events[max(deletes) + 1:said], \
                    f"{k}: said removed before the folder was flushed after its delete: {log.events}"

        with DiskLog() as log:                                         # (1) a set to null
            await a.send({"t": "set", "key": "obj:1:tv", "value": None, "ref": "rm1"})
            await a.wait(lambda m: m.get("ref") == "rm1", 2)
        durable(log, ["obj:1:tv"])
        assert not shared_file(data, "obj:1:tv")

        with DiskLog() as log:                                         # (2) an admin's shared_remove: one batch
            out = await server.admin_op("shared_remove", {"prefix": "obj:1:"}, "test")
            assert out == {"ok": True, "removed": 2}, out
        durable(log, ["obj:1:lamp", "obj:1:fan"])
        assert log.events.count(("sync", "shared")) == 2, f"a batch flushes twice in all (_seq, deletes): {log.events}"
        first_said = min(i for i, e in enumerate(log.events) if e[0] == "said")
        assert not [e for e in log.events[first_said:] if e[0] in ("unlink", "sync")], \
            f"a removal was said before every file of the batch was gone and flushed: {log.events}"

        with DiskLog() as log, Patched(MAX_SHARED_TOTAL=server.shared_total + 1000):    # (3) make_room clears lot 2
            await put("house:4:live", "x" * 2500)
        durable(log, ["house:2:live", "obj:2:radio"])
        assert "house:3:live" in server.shared

        real_drop = mp.Store.drop_shared                               # (4) a batch whose 2nd delete fails

        def locked(store, key):
            if key == "obj:5:b":
                raise PermissionError(13, "in use (an antivirus)")
            return real_drop(store, key)
        before = server.seq
        with DiskLog() as log, Patched(mp.Store, drop_shared=locked):
            try:
                server.remove_shared(["obj:5:a", "obj:5:b", "obj:5:c"])
                raise AssertionError("the failed delete wasn't raised")
            except PermissionError:
                pass
        durable(log, ["obj:5:a"])
        assert [e[1] for e in log.events if e[0] == "said"] == ["obj:5:a"], log.events
        assert "obj:5:a" not in server.shared and {"obj:5:b", "obj:5:c"} <= set(server.shared)
        assert shared_file(data, "obj:5:b") and shared_file(data, "obj:5:c") and not shared_file(data, "obj:5:a")
        assert server.seq == before + 1
        keys, seq = {k: it["v"] for k, it in server.shared.items()}, server.seq
    finally:
        await unserve(server, srv)
    again = mp.Server(mp.Store(data))
    assert {k: it["v"] for k, it in again.shared.items()} == keys and again.seq >= seq


@test
async def uploaded_files_are_on_disk_before_blob_ok_and_damaged_ones_heal():
    """Review round 3: an uploaded mod file was confirmed (blob_ok) without ever being flushed to disk: no fsync of its
    content, no folder flush after its rename. On a Pi a power cut soon after left blobs/<sha> empty or cut short for
    good: blob_check said the server had it (so its owner never sent it again), blob_get served it damaged (the
    partner's download failed, again and again). Now its content is flushed, then it's renamed and the folder flushed,
    and only then blob_ok. A blob already damaged that way (not the size its owner's list gives, nor its content its
    sha256) is reported missing by blob_check, and by a world offer, and deleted: it's uploaded again. One whose list
    gives another size but whose content is right stays (the list is what's wrong)."""
    import base64
    import hashlib
    data = tmpdir("blobflush")
    server, srv = await serve(data)
    try:
        ana = Peer("Ana")
        await ana.join(server.port)
        body = {n: f"mod file {n} ".encode() * (2000 + 37 * i) for i, n in enumerate("abcde")}
        sha = {n: hashlib.sha256(b).hexdigest() for n, b in body.items()}
        sizes = {n: len(b) for n, b in body.items()}
        sizes["c"] += 1                                                # (c: a list that's wrong about its size)
        files = [{"p": f"{n}.pak", "h": sha[n], "s": sizes[n]} for n in "abcd"]
        pid = server.store.find_player(ana.key)
        await ana.send({"t": "mods", "mods": [{"id": "m1", "name": "M1", "files": files}]})
        await until(lambda: pid in server.store.all_mods(), 3, "Ana's mod list stored")
        f = Peer("Ana", key=ana.key)
        await f.open(server.port)
        assert (await f.hello(files=True))["t"] == "files_ok"
        f.read()

        async def upload(n):
            at = len(f.msgs)
            await f.send({"t": "blob_put", "h": sha[n], "o": 0, "d": base64.b64encode(body[n]).decode(), "end": True})
            await until(lambda: any(m.get("t") in ("blob_ok", "blob_bad") for m in f.msgs[at:]), 5, f"{n} answered")
            return next(m for m in f.msgs[at:] if m.get("t") in ("blob_ok", "blob_bad"))

        async def check(names):
            at = len(f.msgs)
            await f.send({"t": "blob_check", "h": [sha[n] for n in names]})
            await until(lambda: any(m.get("t") == "blob_missing" for m in f.msgs[at:]), 5, "blob_missing")
            got = next(m for m in f.msgs[at:] if m.get("t") == "blob_missing")["h"]
            return [n for n in names if sha[n] in got]

        with DiskLog() as log:
            assert (await upload("a"))["t"] == "blob_ok"
        part = f"{sha['a']}.{pid}.part"
        flush = log.index(("flush", part))
        fsync = log.events.index(("fsync",), flush) if ("fsync",) in log.events[flush:] else -1
        replace, sync, ok = log.index(("replace", part, sha["a"])), log.index(("sync", "blobs")), log.index(("blob_ok",))
        assert flush < fsync < replace < sync < ok, f"blob_ok before the file was on disk for good: {log.events}"
        assert server.store.blob(sha["a"]).read_bytes() == body["a"]

        blobs = data / "blobs"                                         # what a power cut left behind
        (blobs / sha["b"]).write_bytes(b"")                            # b: empty
        (blobs / sha["c"]).write_bytes(body["c"])                      # c: whole (its list is wrong about the size)
        (blobs / sha["d"]).write_bytes(body["d"][:1000])               # d: cut short
        assert await check(list("abcd")) == ["b", "d"]
        assert not (blobs / sha["b"]).exists() and not (blobs / sha["d"]).exists(), "a damaged blob was kept"
        assert (blobs / sha["c"]).read_bytes() == body["c"], "a whole blob was deleted"
        for n in "bd":
            assert (await upload(n))["t"] == "blob_ok"
            assert (blobs / sha[n]).read_bytes() == body[n]
        assert await check(list("abcd")) == []

        (blobs / sha["e"]).write_bytes(body["e"][:-1])                 # a world file cut short: the offer needs it
        await ana.send({"t": "world_save_offer", "files": [{"p": "w.usvx", "h": sha["e"], "s": len(body["e"])},
                                                           {"p": "a.bin", "h": sha["a"], "s": len(body["a"])}]})
        m = await ana.wait(lambda m: m.get("t") == "world_save_offer", 5)
        assert m.get("need") == [sha["e"]], m
        assert not (blobs / sha["e"]).exists()
        assert (await upload("e"))["t"] == "blob_ok" and (blobs / sha["e"]).read_bytes() == body["e"]
        f.drop()
    finally:
        await unserve(server, srv)


@test
async def admin_restart_is_scheduled_even_with_a_stuck_player():
    """#12: the restart is scheduled before players are told, and the admin's answer comes at once."""
    with Patched(SEND_TIMEOUT=60, TIMEOUT=600, MAX_BACKLOG=256 * 1024 * 1024):
        server, srv = await serve()
        try:
            server.restartable, server.stop_event = True, asyncio.Event()
            zed = Peer("Zed")
            await zed.join(server.port, read=False, rcvbuf=2048)
            ana = Peer("Ana")
            await ana.join(server.port)
            await stall(server, ana, "Zed")
            t0 = time.monotonic()
            out = await asyncio.wait_for(server.admin_op("restart", {"in": 5}, "test"), 2)
            assert out["ok"] and server.restart_requested and time.monotonic() - t0 < 0.5
            await ana.wait(lambda m: m.get("t") == "restart_soon", 2, "restart_soon")
        finally:
            await unserve(server, srv)


CHILD = r"""
import asyncio, json, logging, os, socket, sys, tempfile, time, argparse
sys.path.insert(0, sys.argv[1])
import inzoi_mp_server as mp
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
mp.SEND_TIMEOUT, mp.TIMEOUT, mp.LINGER = 60, 600, 5

async def main():
    args = argparse.Namespace(host="127.0.0.1", port=0, password="", motd=None, max_players=None,
                              data=tempfile.mkdtemp(prefix="robust-child-"), hosted=False, name="Child")
    srv, tasks = await mp.start(args)
    server = srv.mp_server
    port = server.port
    async def hello(name, rcvbuf=None):
        if rcvbuf:
            s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf); s.setblocking(False)
            await asyncio.get_running_loop().sock_connect(s, ("127.0.0.1", port))
            r, w = await asyncio.open_connection(sock=s, limit=mp.MAX_LINE)
        else:
            r, w = await asyncio.open_connection("127.0.0.1", port, limit=mp.MAX_LINE)
        w.write((json.dumps({"t": "hello", "protocol": 1, "key": name * 20, "name": name}) + "\n").encode())
        await w.drain()
        await r.readline()
        return r, w
    zr, zw = await hello("z", 2048)
    ar, aw = await hello("a")
    gone_r, gone_w = await hello("g")
    blob = json.dumps({"t": "event", "kind": "blob", "data": "x" * 1000000}).encode() + b"\n"
    got = []
    async def read_a():
        while True:
            line = await ar.readline()
            if not line:
                return
            got.append(json.loads(line))
    reader = asyncio.ensure_future(read_a())
    zc = next(c for c in server.conns if c.name == "z")
    for _ in range(400):
        if zc.out or zc.aborted:
            break
        aw.write(blob)
        await aw.drain()
        await asyncio.sleep(0.005)
    gone_w.close()                                    # lingering when the server stops
    await asyncio.sleep(0.2)
    t0 = time.monotonic()
    await asyncio.wait_for(mp.stop(srv, tasks, "goodbye"), 8)
    took = time.monotonic() - t0
    await asyncio.sleep(0.3)
    print("STOPPED in %.2f s, goodbye=%s" % (took, any(m.get("text") == "goodbye" for m in got)))
    reader.cancel()
    for w in (zw, aw):
        w.close()

asyncio.run(main())
import gc; gc.collect()
print("CHILD DONE")
"""


@test
async def standalone_stop_is_prompt_and_logs_no_errors():
    """#12 / #30: stopping a server (main_async's path: stop()) with a stuck player and a lingering one finishes in
    about a second, the goodbye goes out, and nothing is logged at ERROR: no "exception was never retrieved", no
    "Task was destroyed but it is pending" (checked in a child process, where the event loop really closes)."""
    out = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", CHILD, str(SERVER_DIR)],
                                  capture_output=True, text=True, timeout=60)
    text = out.stdout + out.stderr
    assert out.returncode == 0 and "CHILD DONE" in text, text[-2000:]
    line = next(x for x in text.splitlines() if x.startswith("STOPPED"))
    assert float(line.split()[2]) < 3 and "goodbye=True" in line, line
    for bad in ("ERROR", "Traceback", "never retrieved", "destroyed but it is pending"):
        assert bad not in text, f"{bad!r} in the child's output:\n{text[-3000:]}"


# ====================================================================== the welcome always fits in one line
def mod_list(who: str, count: int, files: int = 3) -> list:
    return [{"id": f"{who}m{i}", "name": f"{who} mod {i}",
             "files": [{"p": f"f{j}.pak", "h": mp.hashlib.sha256(f"{who}/{i}/{j}".encode()).hexdigest(), "s": 1}
                       for j in range(files)]} for i in range(count)]


@test
async def welcome_always_fits_one_line():
    """Review round 4: nothing a player can store or relay makes the welcome bigger than a companion reads in one line
    (MAX_LINE: it would reconnect forever, and after every restart too). A house's "lot" is listed short (also from a
    file an older server kept it in as it came); a mod list past MAX_MODS keeps the mods that fit (the player is
    told); the mod set past MAX_MODSET keeps the mods of the lists updated last (also from older files); a Zoi, state
    or world past its cap is ignored; and a welcome still past WELCOME_ROOM (many big Zois) leaves out the Zois,
    states and mod set, which follow it at once on their own lines."""
    errors = Errors()
    mp.log.addHandler(errors)
    server, srv = await serve()
    try:
        a, b = Peer("Ana"), Peer("Ben")
        wa = await a.join(server.port)
        wb = await b.join(server.port)
        await a.send({"t": "house_put", "house": {"name": "n", "lot": "L" * (17 * 1024 * 1024)}})   # the review's
        m = await b.wait(lambda m: m.get("t") == "house_updated", 15, "Ana's house")
        assert m["meta"]["lot"] == "L" * 200, len(str(m["meta"]["lot"]))
        await b.send({"t": "house_put", "house": {"name": "m", "lot": {"x": ["y" * 300]}}})
        m = await a.wait(lambda m: m.get("t") == "house_updated" and m["meta"]["owner"] == wb["id"], 3, "Ben's house")
        assert isinstance(m["meta"]["lot"], str) and len(m["meta"]["lot"]) == 200, m["meta"]
        await b.send({"t": "house_put", "house": {"name": "m", "lot": 42}})
        await a.wait(lambda m: m.get("t") == "house_updated" and m["meta"]["lot"] == 42, 3, "Ben's lot 42")
        c = Peer("Cat")                                  # (kept: a Peer's reading task must not be collected)
        wc = await c.join(server.port)
        assert {h["owner"]: h["lot"] for h in wc["houses"]} == {wa["id"]: "L" * 200, wb["id"]: 42}, wc["houses"]
        assert len(json.dumps(wc)) < 64 * 1024

        # Zoi, state, world past their caps: ignored, what was there stays.
        with Patched(MAX_PROFILE=5000, MAX_STATE=500, MAX_WORLD=500):
            await a.send({"t": "profile", "profile": {"look": "x" * 6000}})
            await a.wait(lambda m: m.get("t") == "error" and m.get("code") == "too_big", 2, "the Zoi refused")
            await a.send({"t": "profile", "profile": {"look": "ok"}})
            await b.wait(lambda m: m.get("t") == "profile" and m["profile"] == {"look": "ok"}, 2, "the small Zoi")
            assert not b.find(lambda m: m.get("t") == "profile" and "xxx" in str(m["profile"]))
            assert server.store.players[wa["id"]]["profile"] == {"look": "ok"}
            await a.send({"t": "state", "state": {"n": 0}})
            await b.wait(lambda m: m.get("t") == "state" and m["state"] == {"n": 0}, 2, "the small state")
            # (a marker nothing else can hold: player ids are "p" + hex, so a plain "pad" matched one 1 run in 85)
            await a.send({"t": "state", "state": {"OVERSIZED_PAD": "s" * 600}})
            await a.send({"t": "world", "world": {"OVERSIZED_PAD": "w" * 600}})  # (Ana leads: the first to join)
            await a.send({"t": "ping", "echo": "after"})
            await a.wait(lambda m: m.get("t") == "pong" and m.get("echo") == "after", 2, "the pong")
            assert conn_of(server, "Ana").state == {"n": 0} and server.world is None
            await b.none(lambda m: "OVERSIZED_PAD" in json.dumps(m), 0.2, "a state or world past its cap")

        # Mod lists past MAX_MODS keep what fits (in order); the pooled set past MAX_MODSET keeps the newest lists.
        with Patched(MAX_MODS=20_000, MAX_MODSET=30_000):
            await a.send({"t": "mods", "mods": mod_list("a", 60)})
            e = await a.wait(lambda m: m.get("t") == "error" and "mod list" in m.get("error", ""), 2, "the mods notice")
            assert e["code"] == "too_big" and not e.get("fatal"), e
            stored = server.store.all_mods()[wa["id"]]["mods"]
            assert 30 < len(stored) < 60 and mp.json_size(stored) <= 20_000, (len(stored), mp.json_size(stored))
            assert [m["id"] for m in stored] == [f"am{i}" for i in range(len(stored))]
            await asyncio.sleep(0.05)
            await b.send({"t": "mods", "mods": mod_list("b", 40)})              # fits alone; both don't
            ms = await a.wait(lambda m: m.get("t") == "modset" and any(x["id"] == "bm0" for x in m["mods"]), 2)
            ids = [x["id"] for x in ms["mods"]]
            assert mp.json_size(ms["mods"]) <= 30_000 and {f"bm{i}" for i in range(40)} <= set(ids), ids
            assert 0 < sum(1 for i in ids if i.startswith("am")) < len(stored), ids
            assert ids == [x["id"] for x in sorted(ms["mods"], key=lambda m: m["name"].lower())]

        # A welcome past WELCOME_ROOM: the Zois, states and mod set come right after it, as their own lines.
        await a.send({"t": "profile", "profile": {"look": "A" * 3000}})
        await b.send({"t": "profile", "profile": {"look": "B" * 3000}})
        await b.send({"t": "state", "state": {"n": 7}})
        await a.send({"t": "state", "state": {"n": 8}})
        await a.send({"t": "mods", "mods": mod_list("a", 2)})
        await until(lambda: conn_of(server, "Ben").state == {"n": 7} and conn_of(server, "Ana").state == {"n": 8}
                    and server.store.players[wb["id"]]["profile"] == {"look": "B" * 3000}
                    and len(server.modset()["mods"]) == 42, 3, "the Zois, states and mods in")
        d = Peer("Dan")
        full = await d.join(server.port)
        room = len(json.dumps(full, separators=(",", ":"))) - 100
        with Patched(WELCOME_ROOM=room):
            e = Peer("Eve")
            slim = await e.join(server.port)
            assert len(json.dumps(slim, separators=(",", ":"))) <= room and slim["modset"] is None
            assert all(p["profile"] is None and p["state"] is None for p in slim["players"]), slim["players"]
            assert {k: v for k, v in slim.items() if k not in ("players", "modset", "id", "admin")} == \
                   {k: v for k, v in full.items() if k not in ("players", "modset", "id", "admin")}
            want = {(k, p["id"]): p[k] for p in full["players"] for k in ("profile", "state") if p.get(k) is not None}
            assert len(want) == 4, sorted(want)
            await until(lambda: len(e.msgs) >= 1 + len(want), 3, "the lines after the welcome")
            assert e.msgs[0] == {"t": "modset", **full["modset"]}, str(e.msgs[0])[:200]
            got = {(m["t"], m["id"]): m[m["t"]] for m in e.msgs[1:1 + len(want)]}
            assert got == want, (sorted(got), sorted(want))
            others = {p["id"] for p in full["players"]} | {full["id"]}
            assert {p["id"] for p in slim["players"]} == others
            assert not errors.records, errors.records
        before = set(server.clients)
        with Patched(WELCOME_ROOM=300):                  # still too big without them: refused, logged, nothing half done
            f = Peer("Fay")
            await f.open(server.port)
            err = await f.hello()
            assert err["t"] == "error" and err["fatal"] and err["code"] == "bad_request", err
            assert set(server.clients) == before
            assert any("can't join" in r for r in errors.records), errors.records
    finally:
        mp.log.removeHandler(errors)
        await unserve(server, srv)

    # A data folder an older server filled: a house's lot of 1 MB, mod lists far past the caps. Listed short at once.
    data = tmpdir("legacy-welcome")
    (data / "houses").mkdir(parents=True)
    (data / "houses" / "p11111111.json").write_text(json.dumps({"meta": {
        "owner": "p11111111", "updated": 5, "name": "old", "by": "x", "lot": "L" * 1_000_000}, "house": {"n": 1}}),
        "utf-8")
    (data / "houses" / "p33333333.json").write_text(json.dumps({"meta": {"lot": ["odd"], "updated": "?"}}), "utf-8")
    (data / "mods").mkdir()
    for i, pid in enumerate(("p11111111", "p22222222")):
        (data / "mods" / f"{pid}.json").write_text(json.dumps(          # (the older list's mods: bigger)
            {"name": pid, "updated": i, "mods": mod_list(pid, 100, 6 - 3 * i)}), "utf-8")
    with Patched(MAX_MODSET=30_000):
        s = mp.Server(mp.Store(data))
        lines = s.welcome_lines(type("C", (), {"id": "p00000000", "name": "Zed"})())
        assert len(lines) == 1, len(lines)
        w = json.loads(lines[0][0])
        assert {h["owner"]: h["lot"] for h in w["houses"]} == {"p11111111": "L" * 200, "p33333333": "['odd']"}
        assert s.store.get_house("p11111111")["meta"]["lot"] == "L" * 200
        mods = w["modset"]["mods"]
        assert mods and mp.json_size(mods) <= 30_000 and all(m["id"].startswith("p22222222") for m in mods), \
            [m["id"] for m in mods][:5]
    with Patched(MAX_HOUSES=1):
        assert [h["owner"] for h in s.houses()] == ["p11111111"]          # the newest

    # At full size: the shared state at its cap and 16 players online with Zois at MAX_PROFILE (about 48 MB in one
    # line) still join, every line within what a companion reads.
    data = tmpdir("full-welcome")
    write_shared(data, {f"house:{i}:exact": ("x" * (mp.MAX_SHARED - 1000), 60) for i in range(4)})
    s = mp.Server(mp.Store(data))
    assert s.shared_total > mp.MAX_SHARED_TOTAL - 8000 and len(s.shared) == 4

    class Online:
        def __init__(self, i):
            self.id = f"p{i:08x}"

        def public(self):
            return {"id": self.id, "name": "n", "profile": {"look": "z" * (mp.MAX_PROFILE - 100)}, "state": {"n": 1},
                    "leader": False, "version": "t"}
    s.clients = {o.id: o for o in (Online(i) for i in range(1, 17))}
    lines = s.welcome_lines(type("C", (), {"id": "p00000000", "name": "Zed"})())
    assert lines and len(lines[0][0]) <= mp.WELCOME_ROOM and all(len(x) <= mp.MAX_LINE for x, _ in lines)
    assert sum(1 for x, _ in lines if x.startswith(b'{"t":"profile"')) == 16
    del s, lines


@test
async def wall_clock_steps_cut_nobody():
    """Review round 4: timeouts, the keepalive's backstop, rate limits, LAN discovery, the directory's listings and the
    uptime measure time on a clock that only goes forward (mono): a step of the wall clock (a Raspberry Pi without a
    clock battery setting its time after players joined, a manual fix) cuts nobody off and neither ends nor freezes a
    limit; as time really goes on, they all still work. (The wall clock is stepped for real: time.time.)"""
    real_time, real_mono = time.time, mp.mono

    def wall(step):
        return Patched(time, time=lambda: real_time() + step)
    server, srv = await serve(directory=True)
    try:
        a, b = Peer("Ana"), Peer("Ben")
        await a.join(server.port)
        await b.join(server.port)
        s = Peer("Late")                                 # connected, not joined yet
        await s.open(server.port)
        await s.send({"t": "ping"})
        assert (await s.line())["t"] == "pong"
        for step in (300, -3600):                        # forward (the probe: everyone was cut) and back
            with wall(step):
                server.tick()
                assert not any(c.aborted for c in server.conns), step
                await s.send({"t": "ping"})              # (before hello: the deadline counted from connecting)
                assert (await s.line())["t"] == "pong", step
                assert server.info()["uptime"] >= 0
        assert (await s.hello())["t"] == "welcome"
        server.hello_fails.add("10.1.2.3")
        with wall(-3600):                                # a step back doesn't freeze the limit...
            assert server.hello_fails.count("10.1.2.3") == 1
            with Patched(mono=lambda: real_mono() + 61):     # ...which ends when a minute really went by
                assert server.hello_fails.count("10.1.2.3") == 0

        class Sent:
            def __init__(self):
                self.to = []

            def sendto(self, data, addr):
                self.to.append(addr)
        d = mp.Discovery(server)
        d.transport = Sent()
        d.datagram_received(mp.DISCOVER_ASK, ("127.0.0.1", 5000))
        with wall(-3600), Patched(mono=lambda: real_mono() + 1):
            d.datagram_received(mp.DISCOVER_ASK, ("127.0.0.1", 5000))
        assert len(d.transport.to) == 2, d.transport.to

        server.directory["abcdef12"] = {"id": "abcdef12", "seen": real_time(), "heard": real_mono()}
        with wall(3600):
            server.prune_directory()
            assert "abcdef12" in server.directory
        with Patched(mono=lambda: real_mono() + mp.LISTING_TTL + 1):
            server.prune_directory()
            assert "abcdef12" not in server.directory

        with Patched(mono=lambda: real_mono() + 3 * mp.TIMEOUT + 1):     # silence for real: the backstop still cuts
            server.tick()
        await until(lambda: a.eof and b.eof, 3, "players silent for 3 x TIMEOUT cut off")
    finally:
        await unserve(server, srv)


# ---------------------------------------------------------------------- runner
def run_one(fn) -> tuple[bool, str]:
    errors = []
    contacts = len(LIVE_CONTACTS)

    async def wrapper():
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, ctx: errors.append(f"{ctx.get('message')} {ctx.get('exception')!r}"))
        await asyncio.wait_for(fn(), 120)
    try:
        asyncio.run(wrapper())
    except Exception as e:                           # noqa: BLE001
        import traceback
        return False, "".join(traceback.format_exception(type(e), e, e.__traceback__))[-3000:]
    if len(LIVE_CONTACTS) > contacts:
        return False, f"tried to contact the live ports (stopped): {LIVE_CONTACTS[contacts:][:5]}"
    if errors:
        return False, f"loop exceptions: {errors[:3]}"
    return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("only", nargs="*", help="run only the tests whose name contains one of these")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    # Every record reaches the tests' own handlers (Errors); only the console is quiet unless -v.
    console = logging.StreamHandler()
    console.setLevel(logging.INFO if args.verbose else logging.CRITICAL)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[console])
    failed = []
    for fn in TESTS:
        if args.only and not any(o in fn.__name__ for o in args.only):
            continue
        t0 = time.monotonic()
        ok, detail = run_one(fn)
        print(f"  {'ok  ' if ok else 'FAIL'} {fn.__name__} ({time.monotonic() - t0:.1f} s)")
        if not ok:
            failed.append(fn.__name__)
            print("      " + detail.replace("\n", "\n      "))
    print(f"{'all server robustness tests passed' if not failed else f'{len(failed)} failing: {failed}'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
