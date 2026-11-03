"""Stress / fuzz harness for the inZOI Multiplayer server (server/inzoi_mp_server.py).

Starts the REAL server in-process (port 0, a throwaway temp data dir), then drives it hard with fake
players and malformed traffic, asserting the invariants a production server must keep. It does NOT touch
%APPDATA%, the user's config, the running server, or ports 24242/24243: every connection is to the
loopback port the kernel hands us for a brand-new server, and every path is under tempfile.mkdtemp().

    python tools/stress_server.py                 # ~60 s of fuzzing + the defect probes
    python tools/stress_server.py --seconds 120   # longer fuzz
    python tools/stress_server.py --seed 7        # a different (still deterministic) run

What it runs
    1. FUZZ  - a pool of fake players that randomly join / leave / reconnect with the same key (with and
       without "bye"), spray pose keyframes/frames at ~15/s, live state, chat, and overlapping shared
       "set"s (with and without "base"), while junk lines (bad JSON, arrays, wrong types, huge strings
       near the limits, non-UTF-8 bytes) rain on throwaway connections. A mid-run restart on the same data
       dir happens once.
    2. RESTART - the server is stopped and rebuilt on the same data dir; the shared state must come back.
    3. PROBES - targeted inputs that a correct server must survive (a slow/stalled client, a lone UTF-16
       surrogate in a stored value, a deeply-nested shared value, and a full shared state: players racing on
       many lots under a small cap, old lots cleared to make room).

Invariants (a failure of any -> a line in the report and a non-zero exit):
  * the server never raises an unhandled exception (loop exception handler) and never logs at ERROR;
  * once quiet, every connected player's shared view == the server's shared state (same key -> v + value);
  * shared versions strictly increase and survive a restart;
  * "who" lists exactly the connected players once the linger window has passed;
  * memory stays bounded (last_pose/conns/clients don't outgrow the live players).

Exit code 0 only if every "must hold" invariant held AND no defect probe exposed a server bug.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import socket
import sys
import tempfile
import time
from pathlib import Path

# ---- HARD SAFETY: never let an imported client module see the real config. (The server module itself
# reads no config, but a stray client import must not touch %APPDATA%.) Set this before any such import.
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="stress-appdata-")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import inzoi_mp_server as mp  # noqa: E402

LOG = logging.getLogger("stress")


# ---------------------------------------------------------------------- watchers
class ErrorCapture(logging.Handler):
    """Every ERROR (or worse) the server logs is an invariant violation: a dispatch blew up, a save failed,
    a message couldn't be encoded. We keep them all."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records: list[str] = []

    def emit(self, record):
        msg = record.getMessage()
        if record.exc_info and record.exc_info[1]:
            msg += f"  !! {record.exc_info[1]!r}"
        self.records.append(msg)


class Harness:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.loop_errors: list[str] = []
        self.errcap = ErrorCapture()
        self.results: list[tuple[str, bool, str]] = []   # (name, must_hold_ok / defect_absent, detail)

    def record(self, name: str, ok: bool, detail: str = ""):
        self.results.append((name, ok, detail))
        LOG.info("%s %s%s", "PASS" if ok else "FAIL", name, f"  ({detail})" if detail else "")

    def install(self):
        mp.log.addHandler(self.errcap)
        mp.log.setLevel(logging.INFO)
        logging.getLogger("asyncio").addHandler(self.errcap)
        asyncio.get_running_loop().set_exception_handler(self._loop_exc)

    def _loop_exc(self, loop, context):
        exc = context.get("exception")
        self.loop_errors.append(f"{context.get('message', '')} :: {exc!r}")
        LOG.error("LOOP EXCEPTION: %s :: %r", context.get("message", ""), exc)


# ---------------------------------------------------------------------- a fake player
class Player:
    """One companion-like connection: reads every line (so it never stalls), answers pings, and keeps a
    local view of the shared state exactly the way client/shared.py does (snapshot in welcome, then each
    'shared' message applied; a null item removes the key)."""

    def __init__(self, h: Harness, port: int, key: str, name: str):
        self.h, self.port, self.key, self.name = h, port, key, name
        self.reader = self.writer = None
        self.id = None
        self.task = None
        self.alive = False
        self.shared: dict[str, dict] = {}          # key -> item (as this player believes it)
        self.last_v = 0                            # highest shared version this player has seen
        self.version_monotonic = True
        self.who: list | None = None
        self.poses = 0
        self.pongs = 0
        self.echoes: set = set()                  # the "echo" of each pong received (settle's barrier)
        self.fatal = None
        self.removed = 0                           # "shared" removals (item null) seen
        self.too_big = 0                           # my sets refused as too big
        self.run: list[str] = []                   # the server's removals (ref_by null) since the last other "shared"
        self.home_cleared: list = []               # (home lot, keys): a home cleared to make room (never allowed)

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.port, limit=mp.MAX_LINE)
        await self._raw({"t": "hello", "protocol": mp.PROTOCOL, "key": self.key, "name": self.name, "version": "s"})
        line = await asyncio.wait_for(self.reader.readline(), 10)
        welcome = json.loads(line)
        assert welcome.get("t") == "welcome", welcome
        self.id = welcome["id"]
        self._apply_snapshot(welcome.get("shared"), welcome.get("seq") or 0)
        self.alive = True
        self.task = asyncio.create_task(self._read())
        return self

    def _apply_snapshot(self, items, seq):
        self.run = []
        self.shared = {k: v for k, v in (items or {}).items() if isinstance(v, dict)}
        self.last_v = max([self.last_v, int(seq or 0)] + [int(v.get("v") or 0) for v in self.shared.values()])

    async def _read(self):
        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    break
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                await self._on(m)
        except (ConnectionError, OSError):
            pass
        finally:
            self.alive = False

    async def _on(self, m: dict):
        t = m.get("t")
        if t == "ping":
            await self._raw({"t": "pong"})
        elif t == "welcome":
            self.id = m["id"]
            self._apply_snapshot(m.get("shared"), m.get("seq") or 0)
        elif t == "shared":
            v = int(m.get("v") or 0)
            if v <= self.last_v:
                self.version_monotonic = False
            self.last_v = max(self.last_v, v)
            key, item = str(m.get("key") or ""), m.get("item")
            self._check_room(key, item, m.get("ref_by"))
            if key:
                if item:
                    self.shared[key] = item
                else:
                    self.shared.pop(key, None)
                    self.removed += 1
        elif t == "shared_all":
            self._apply_snapshot(m.get("shared"), m.get("seq") or 0)
        elif t == "event" and m.get("kind") == "pose":
            self.poses += 1
        elif t == "pong":
            self.pongs += 1
            if isinstance(m.get("echo"), str):
                self.echoes.add(m["echo"])
        elif t == "who":
            self.who = m.get("players")
        elif t in ("error",) and m.get("fatal"):
            self.fatal = m
        elif t == "error" and m.get("code") == "too_big":
            self.too_big += 1

    def _home(self, family) -> str | None:
        value = (family or {}).get("value")
        site = value.get("home_site") if isinstance(value, dict) else None
        return None if site is None else str(site)

    def _check_room(self, key: str, item, ref_by):
        """The removals the server makes to make room for a set come to everyone right before that set (item null,
        ref_by null). They must never be of the family's home: for a "family" set that moves the home, of the NEW home
        (the old one may go, last); for any other set, of the home as it stands."""
        if item is None and ref_by is None:
            self.run.append(key)
            return
        if self.run:
            home = self._home(item) if key == "family" else self._home(self.shared.get("family"))
            bad = [k for k in self.run if home is not None and mp.Server.lot_of(k) == home]
            if bad:
                self.home_cleared.append((home, key, bad))
        self.run = []

    async def _raw(self, msg: dict):
        self.writer.write((json.dumps(msg) + "\n").encode())
        await self.writer.drain()

    async def send(self, msg: dict) -> bool:
        if not self.writer or self.writer.is_closing():
            return False
        try:
            await self._raw(msg)
            return True
        except (ConnectionError, OSError):
            return False

    async def bye(self):
        await self.send({"t": "bye"})
        await self.drop()

    async def drop(self):
        """Abrupt disconnect (no bye): the broken-connection case."""
        self.alive = False
        if self.task:
            self.task.cancel()
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass
        self.task = None

    def shared_view(self) -> dict:
        return {k: (int(v.get("v") or 0), json.dumps(v.get("value"), sort_keys=True)) for k, v in self.shared.items()}


def server_view(server) -> dict:
    return {k: (int(v.get("v") or 0), json.dumps(v.get("value"), sort_keys=True)) for k, v in server.shared.items()}


async def quiesce(seconds: float = 1.0):
    await asyncio.sleep(seconds)


async def settle(server, players, timeout: float = 30.0) -> bool:
    """A barrier before comparing views with the server (a fixed sleep isn't one: on a loaded machine the server can
    still be committing sets, each two fsync'd saves, when it ends). Each live player pings with a fresh echo and
    waits for its pong: a connection's lines are handled in order, so once every pong is back, every set sent before
    it has been handled. Then every view must catch up with the server's (the broadcasts of the last sets may still
    be on their way). A pong replaces no view, so a broadcast a player missed still shows up as a divergence. False if
    that doesn't happen within timeout: the caller's own comparison then says what differs."""
    live = [p for p in players if p.alive]
    tag = f"settle-{time.monotonic_ns()}"
    for p in live:
        await p.send({"t": "ping", "echo": tag})
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        live = [p for p in live if p.alive]
        if all(tag in p.echoes for p in live):
            sv = server_view(server)
            if all(p.shared_view() == sv for p in live):
                return True
        await asyncio.sleep(0.05)
    LOG.warning("settle: not quiet within %.0f s", timeout)
    return False


# ---------------------------------------------------------------------- server lifecycle (safe)
async def new_server(data: Path):
    server = mp.Server(mp.Store(data))
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
    port = srv.sockets[0].getsockname()[1]
    return server, srv, port


async def stop_server(srv, server):
    srv.close()
    for c in list(server.conns):
        await c.close()
    try:
        await asyncio.wait_for(srv.wait_closed(), 3)
    except asyncio.TimeoutError:
        pass


# ---------------------------------------------------------------------- junk generators
def junk_lines(rng: random.Random) -> list[bytes]:
    big = str(rng.randint(0, 9)) * (mp.MAX_LINE_BEFORE_HELLO // 2)
    return [
        b"not json at all\n",
        b"[1,2,3]\n",                                             # valid json, not an object
        b"12345\n",
        b'"a string"\n',
        b"null\n",
        b'{"t":12345}\n',                                         # wrong type for t
        b'{"t":"chat","text":' + str(rng.random()).encode() + b"}\n",   # wrong type for text (number)
        b'{"t":"set","key":"' + b"A" * 200 + b'","value":1}\n',  # bad key (too long / uppercase)
        b'{"t":"set","key":"ok","value":"' + big.encode() + b'"}\n',    # a big-ish string value
        b'{"t":"\xff\xfe\x00bad"}\n',                            # non-UTF-8 bytes
        b'{"t":"unknown_type_here"}\n',
        b'{"t":"state"' + b" " * 10 + b"}\n",                    # state with no state field
        b'{"t":"event","kind":"pose","data":"not a dict"}\n',
        b'{"t":"ping","echo":[1,2,3]}\n',
        b"{bad json,}\n",
    ]


async def fire_junk(port: int, rng: random.Random):
    """A throwaway connection that says hello then dumps a junk line (or sends junk before hello)."""
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port, limit=mp.MAX_LINE)
    except OSError:
        return
    try:
        line = rng.choice(junk_lines(rng))
        if rng.random() < 0.5:
            w.write(line)                                        # junk BEFORE any hello
            await w.drain()
        else:
            key = "".join(rng.choice("abcdef0123456789") for _ in range(20))
            w.write((json.dumps({"t": "hello", "protocol": mp.PROTOCOL, "key": key, "name": "junk"}) + "\n").encode())
            await w.drain()
            try:
                await asyncio.wait_for(r.readline(), 3)
            except asyncio.TimeoutError:
                pass
            w.write(line)
            await w.drain()
        try:
            await asyncio.wait_for(r.readline(), 1.0)
        except asyncio.TimeoutError:
            pass
    except (OSError, ConnectionError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


def rand_value(rng: random.Random):
    """A shared value that is weird but NOT one of the catastrophic poisons (those get their own probes)."""
    kind = rng.randint(0, 6)
    if kind == 0:
        return {"objects": rng.randint(0, 9), "on": rng.random() < 0.5}
    if kind == 1:
        return rng.randint(-10 ** 9, 10 ** 9)
    if kind == 2:
        return "x" * rng.choice([1, 100, 10_000, 1_000_000])     # up to ~1 MB, under MAX_SHARED
    if kind == 3:
        return [rng.randint(0, 5) for _ in range(rng.randint(0, 50))]
    if kind == 4:
        return None                                              # removal
    if kind == 5:
        return {"nested": {"a": [1, {"b": 2}], "s": "açaí 日本語 🙂"}}   # normal unicode incl. emoji
    return {"members": ["pA", "pB"], "together": True, "home_site": rng.randint(1000, 9999)}


# ====================================================================== STAGE 1: fuzz
async def stage_fuzz(h: Harness, seconds: float) -> None:
    data = Path(tempfile.mkdtemp(prefix="stress-fuzz-"))
    mp.LINGER = 1.0
    server, srv, port = await new_server(data)
    LOG.info("fuzz server on 127.0.0.1:%d, data %s", port, data)

    names = ["Ana", "Ben", "Cat", "Dan"]
    keys = {n: (n[0].lower() * 20) for n in names}
    players: dict[str, Player] = {}
    keyset = ["house:1038:live", "house:1038:exact", "family", "obj:1038:lamp", "obj:1038:tv", "obj:2001:door"]

    async def ensure(n):
        if n not in players or not players[n].alive:
            try:
                players[n] = await Player(h, port, keys[n], n).connect()
            except (OSError, ConnectionError, AssertionError, asyncio.TimeoutError) as e:
                LOG.warning("join %s failed: %r", n, e)

    for n in names[:2]:
        await ensure(n)

    end = time.monotonic() + seconds
    restarted = False
    pose_blob_k = "1|0,0,0,0|1|0|" + ",".join(str(i) for i in range(1260))
    pose_blob_d = "1|5|0,0,0,0|" + ",".join(str(i) for i in range(1260))
    tick = 0
    while time.monotonic() < end:
        tick += 1
        # a mid-run restart, once, about halfway
        if not restarted and time.monotonic() > end - seconds / 2:
            restarted = True
            LOG.info("--- mid-run restart on the same data dir ---")
            seq_before = server.seq
            view_before = server_view(server)
            for p in list(players.values()):
                await p.drop()
            await stop_server(srv, server)
            server, srv, port = await new_server(data)
            ok = server.seq >= seq_before and server_view(server) == view_before
            h.record("restart: shared state + seq survive (mid-run)", ok,
                      f"seq {seq_before}->{server.seq}, keys {sorted(view_before)} -> {sorted(server_view(server))}")
            players.clear()
            for n in names[:2]:
                await ensure(n)

        roll = h.rng.random()
        if roll < 0.30:                                          # poses from whoever is on
            for p in list(players.values()):
                if p.alive:
                    await p.send({"t": "event", "kind": "pose",
                                  "data": {"k" if tick % 30 == 0 else "d": pose_blob_k if tick % 30 == 0 else pose_blob_d}})
        elif roll < 0.45:                                        # live state
            for p in list(players.values()):
                if p.alive:
                    await p.send({"t": "state", "state": {"n": tick, "x": h.rng.random(), "lot": "1038"}})
        elif roll < 0.72:                                        # overlapping shared sets
            p = h.rng.choice(list(players.values())) if players else None
            if p and p.alive:
                key = h.rng.choice(keyset)
                msg = {"t": "set", "key": key, "value": rand_value(h.rng), "ref": f"r{tick}"}
                if h.rng.random() < 0.5:                         # half carry a base (maybe stale -> refused)
                    cur = server.shared.get(key)
                    msg["base"] = int((cur or {}).get("v") or 0) if h.rng.random() < 0.6 else h.rng.randint(0, 5)
                await p.send(msg)
        elif roll < 0.80:                                        # chat (incl. normal unicode)
            p = h.rng.choice(list(players.values())) if players else None
            if p and p.alive:
                await p.send({"t": "chat", "text": h.rng.choice(["oi", "hey 🙂", "açaí", "x" * 600])})
        elif roll < 0.90:                                        # join / reconnect churn
            await ensure(h.rng.choice(names))
        elif roll < 0.96:                                        # leave (half with bye, half abrupt)
            on = [p for p in players.values() if p.alive]
            if len(on) > 1:
                p = h.rng.choice(on)
                await (p.bye() if h.rng.random() < 0.5 else p.drop())
        else:                                                    # junk
            await fire_junk(port, h.rng)

        await asyncio.sleep(1 / 15)

    # ---- settle, then check the "must hold" invariants
    await quiesce(mp.LINGER + 1.5)
    for n in names:                                              # bring everyone back for the convergence check
        await ensure(n)
    # push one more set from each, then go quiet so every view can catch up
    for i, p in enumerate([p for p in players.values() if p.alive]):
        await p.send({"t": "set", "key": "house:1038:live", "value": {"settle": i, "t": tick}})
    await settle(server, players.values())

    live = [p for p in players.values() if p.alive]
    sv = server_view(server)
    diverged = [(p.name, p.shared_view()) for p in live if p.shared_view() != sv]
    h.record("convergence: every connected player's shared view == server", not diverged,
             "all agree" if not diverged else f"server={sv} diverged={diverged}")

    h.record("shared versions strictly increase per player (no v seen twice/backwards)",
             all(p.version_monotonic for p in live),
             "ok" if all(p.version_monotonic for p in live) else
             f"offenders={[p.name for p in live if not p.version_monotonic]}")

    # who after linger: drop one abruptly, wait out the linger, ask who from another
    if len(live) >= 2:
        victim, asker = live[0], live[1]
        await victim.drop()
        await quiesce(mp.LINGER + 1.0)
        asker.who = None
        await asker.send({"t": "who"})
        end = time.monotonic() + 10
        while asker.who is None and asker.alive and time.monotonic() < end:
            await asyncio.sleep(0.02)
        who_ids = {pp.get("id") for pp in (asker.who or [])}
        server_ids = set(server.clients)
        ok = victim.id not in who_ids and who_ids == server_ids
        h.record("who lists exactly the connected players after linger", ok,
                 f"who={sorted(who_ids)} server.clients={sorted(server_ids)} dropped={victim.id}")

    # memory bounded: nothing outgrows the live population / the keys actually used. ("ok" is a legitimate
    # key: one of the junk lines is a well-formed set with key "ok" that the server correctly stores.)
    live_ids = set(server.clients)
    allowed_keys = set(keyset) | {"ok"}
    mem_ok = (set(server.last_pose) <= live_ids
              and len(server.conns) <= len(live_ids) + 3
              and set(server.shared) <= allowed_keys)
    h.record("memory bounded (last_pose/conns/shared don't outgrow the live state)", mem_ok,
             f"last_pose={sorted(server.last_pose)} live={sorted(live_ids)} conns={len(server.conns)} "
             f"shared_keys={sorted(server.shared)}")

    # no unhandled loop exceptions and no ERROR logs during the whole fuzz
    h.record("no unhandled loop exceptions during fuzz", not h.loop_errors,
             "clean" if not h.loop_errors else f"{len(h.loop_errors)}: {h.loop_errors[:3]}")
    h.record("no ERROR log records during fuzz", not h.errcap.records,
             "clean" if not h.errcap.records else f"{len(h.errcap.records)}: {h.errcap.records[:3]}")

    for p in list(players.values()):
        await p.drop()
    await quiesce(mp.LINGER + 0.5)
    await stop_server(srv, server)
    return data


# ====================================================================== STAGE 2: restart persistence
async def stage_restart(h: Harness):
    data = Path(tempfile.mkdtemp(prefix="stress-restart-"))
    mp.LINGER = 1.0
    server, srv, port = await new_server(data)
    a = await Player(h, port, "a" * 20, "Ana").connect()
    b = await Player(h, port, "b" * 20, "Ben").connect()
    for i in range(10):
        await a.send({"t": "set", "key": "house:1038:live", "value": {"objects": i}})
        await b.send({"t": "set", "key": "family", "value": {"together": True, "members": ["pa", "pb"], "n": i}})
        await asyncio.sleep(0.03)
    await settle(server, [a, b])
    seq_before = server.seq
    view_before = server_view(server)
    await a.drop()
    await b.drop()
    await stop_server(srv, server)

    server2, srv2, port2 = await new_server(data)
    ok_persist = server2.seq >= seq_before and server_view(server2) == view_before
    h.record("restart: seq + shared values reload from disk unchanged", ok_persist,
             f"seq {seq_before}->{server2.seq}; match={server_view(server2) == view_before}")
    # a new set after restart must get a strictly higher version than anything before
    c = await Player(h, port2, "c" * 20, "Cat").connect()
    reload_ok = c.shared_view() == view_before
    await c.send({"t": "set", "key": "house:1038:live", "value": {"after": "restart"}})
    await settle(server2, [c])
    strictly_higher = server2.seq > seq_before
    h.record("restart: a joiner reloads the exact state; new version > pre-restart seq",
             reload_ok and strictly_higher, f"joiner_match={reload_ok} new_seq={server2.seq}>{seq_before}")
    await c.drop()
    await quiesce(mp.LINGER + 0.5)
    await stop_server(srv2, server2)


# ====================================================================== STAGE 3: defect probes
async def probe_slow_client(h: Harness):
    """A production server must not let one stalled client freeze relay to everyone else. We connect a
    client that never reads, let the server back up on it, and check whether a healthy player keeps
    receiving and the sender itself is still answered. If either stops while the stalled client is still
    connected -> head-of-line blocking.

    "Backed up" = more than 64 KB waiting in its transport (asyncio's high-water mark: the point where an
    awaited drain() starts blocking the sender), or anything waiting in the server's own outbound queue
    for it (Client.out, when the server has one)."""
    data = Path(tempfile.mkdtemp(prefix="stress-slow-"))
    saved = mp.TIMEOUT
    mp.TIMEOUT = 600
    try:
        server, srv, port = await new_server(data)

        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
        s.setblocking(False)
        await asyncio.get_running_loop().sock_connect(s, ("127.0.0.1", port))
        rz, wz = await asyncio.open_connection(sock=s, limit=mp.MAX_LINE)
        wz.write((json.dumps({"t": "hello", "protocol": mp.PROTOCOL, "key": "z" * 20, "name": "Zed"}) + "\n").encode())
        await wz.drain()
        await asyncio.wait_for(rz.readline(), 5)                 # read the welcome, then never read again

        ana = await Player(h, port, "a" * 20, "Ana").connect()
        ben = await Player(h, port, "b" * 20, "Ben").connect()
        await quiesce(0.3)
        blob = "x" * 2_000_000
        stalled_buf = queued = 0
        frozen_base = None                                      # ben.poses when Zed's buffer first backs up
        pongs_base = 0
        sent_after = got_after = 0
        for i in range(200):
            await ana.send({"t": "event", "kind": "pose", "data": {"d": blob}})
            for c in server.clients.values():
                if c.name == "Zed" and not c.closed:
                    stalled_buf = max(stalled_buf, c.writer.transport.get_write_buffer_size())
                    queued = max(queued, len(getattr(c, "out", ())))
            await asyncio.sleep(0.05)
            if stalled_buf > 65536 or queued:
                if frozen_base is None:                         # backpressure just hit: start the clock
                    frozen_base, pongs_base = ben.poses, ana.pongs
                    await ana.send({"t": "ping", "echo": "stalled"})
                else:
                    sent_after += 1
                    got_after = ben.poses - frozen_base
                    if sent_after >= 15:                        # 15 more poses sent after the stall began
                        break
        await quiesce(0.5)
        zed_connected = any(c.name == "Zed" for c in server.clients.values())
        answered = ana.pongs > pongs_base
        # Defect: after the stalled client backed up, we sent 15 more poses, yet the HEALTHY player received
        # almost none of them (or the sender's own ping went unanswered) while the stalled client was still
        # connected -> head-of-line blocking (a broadcast waiting on the stalled client's drain()).
        hol = frozen_base is not None and zed_connected and (got_after < 5 or not answered)
        h.record("DEFECT: one stalled client blocks broadcast to everyone (head-of-line)", not hol,
                 f"stalled_server_buf={stalled_buf} queued={queued} healthy_player_got={got_after} of {sent_after} "
                 f"sent after stall; sender_ping_answered={answered}; zed_still_connected={zed_connected}")
        h.record("slow-client probe really stalled the client (the check above isn't vacuous)",
                 frozen_base is not None, f"stalled_server_buf={stalled_buf} queued={queued}")
        try:
            wz.close()
        except Exception:
            pass
        await ana.drop()
        await ben.drop()
        await quiesce(0.5)
        await stop_server(srv, server)
    finally:
        mp.TIMEOUT = saved


async def probe_surrogate(h: Harness):
    """A lone UTF-16 surrogate in a client-supplied value that the server stores and later re-encodes
    (a name, a profile, a shared value). json.dumps(..., ensure_ascii=False) raises UnicodeEncodeError;
    send()/save only catch ConnectionError/OSError, so the value poisons in-memory state and every later
    welcome fails -> nobody can join until the process restarts."""
    data = Path(tempfile.mkdtemp(prefix="stress-surrogate-"))
    server, srv, port = await new_server(data)
    try:
        ana = await Player(h, port, "a" * 20, "Ana").connect()
        # a valid JSON message whose value holds a lone surrogate
        await ana.send({"t": "set", "key": "house:1038:live", "value": "\ud800"})
        await quiesce(0.3)
        poisoned = "house:1038:live" in server.shared
        # can a fresh player still join?
        joined = True
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port, limit=mp.MAX_LINE)
            w.write((json.dumps({"t": "hello", "protocol": mp.PROTOCOL, "key": "b" * 20, "name": "Ben"}) + "\n").encode())
            await w.drain()
            m = json.loads(await asyncio.wait_for(r.readline(), 5))
            joined = (m.get("t") == "welcome")
            w.close()
        except (asyncio.TimeoutError, ValueError, OSError):
            joined = False
        defect = poisoned and not joined
        h.record("DEFECT: a lone-surrogate stored value bricks the server (no one can join)", not defect,
                 f"value_stored_in_memory={poisoned} fresh_join_ok={joined}")
        await ana.drop()
        await quiesce(0.3)
        await stop_server(srv, server)
    except Exception as e:                                       # noqa: BLE001
        await stop_server(srv, server)
        h.record("DEFECT: a lone-surrogate stored value bricks the server (no one can join)", False, f"raised {e!r}")


async def probe_deep_nesting(h: Harness):
    """A deeply-nested JSON value in a shared "set". It is accepted, PERSISTED to disk, then every welcome
    fails to encode it (RecursionError); worse, on the next start Store.load_shared re-reads the file and
    RecursionError (not caught by _load) stops the server from starting at all - a brick that survives
    restarts and needs a human to delete the file."""
    # --- facet 1: a single deeply-nested line makes the connection handler raise an UNCAUGHT RecursionError
    # (json.loads is only guarded against ValueError; RecursionError escapes handle() to the loop handler).
    data = Path(tempfile.mkdtemp(prefix="stress-deep-"))
    server, srv, port = await new_server(data)
    before = len(h.loop_errors)
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port, limit=mp.MAX_LINE)
        w.write((json.dumps({"t": "hello", "protocol": mp.PROTOCOL, "key": "a" * 20, "name": "Ana"}) + "\n").encode())
        await w.drain()
        await asyncio.wait_for(r.readline(), 5)
        n = 20000                                                # past the decode limit
        w.write(b'{"t":"set","key":"house:1038:live","value":' + b"[" * n + b"]" * n + b"}\n")
        await w.drain()
        await quiesce(0.6)
        w.close()
    except (OSError, ValueError, asyncio.TimeoutError):
        pass
    uncaught = len(h.loop_errors) > before
    await stop_server(srv, server)

    # --- facet 2: Store._load catches only (OSError, ValueError); a pathological shared file (here a deeply
    # nested value, exactly the shape save_shared writes) makes json.loads raise RecursionError, which escapes
    # _load -> load_shared -> Store() -> the server cannot start AT ALL. _load is meant to be the "a damaged
    # file never crashes us, fall back to .bak" path, so this is a startup-denial / data-robustness bug.
    import hashlib
    data2 = Path(tempfile.mkdtemp(prefix="stress-deep2-"))
    (data2 / "shared").mkdir()
    key = "house:1038:live"
    fn = hashlib.sha1(key.encode()).hexdigest()[:20] + ".json"
    n = 20000
    (data2 / "shared" / fn).write_text(
        '{"key":"' + key + '","v":1,"by":"p","by_name":"x","at":0,"value":' + "[" * n + "]" * n + "}", "utf-8")
    (data2 / "shared" / "_seq.json").write_text('{"seq":1}', "utf-8")
    startup_crash = False
    try:
        server2, srv2, port2 = await new_server(data2)
        await stop_server(srv2, server2)
    except RecursionError:
        startup_crash = True
    except Exception as e:                                       # noqa: BLE001
        startup_crash = True
        LOG.info("start on a pathological shared file raised %r", e)

    defect = uncaught or startup_crash
    h.record("DEFECT: deep JSON nesting - uncaught RecursionError (handler and/or startup)", not defect,
             f"uncaught_handler_exception={uncaught} _load_crashes_server_start={startup_crash}")


async def probe_full_shared_state(h: Harness):
    """The total cap with players racing on many lots (a small cap, and every lot old enough to go): the server makes
    room by clearing old lots (item-null removals to everyone) between the sets, while the family now and then moves
    home (moving in with the partner: a bigger "family" set). Every player's view must still equal the server's,
    versions only go up, the cap and the size bookkeeping hold, the family's home is never cleared to make room (for a
    move: the new home), no set is refused as too big (there's always an old lot to clear), a restart brings back
    exactly what's left, and a restart under a smaller cap trims the oldest lots before anyone joins (never the home
    nor a key that isn't a lot's)."""
    name = "full shared state: old lots cleared, views converge, cap + home kept, restart exact, trimmed at start"
    saved = mp.MAX_SHARED_TOTAL, mp.LOT_IN_USE
    mp.MAX_SHARED_TOTAL, mp.LOT_IN_USE = 300_000, 0
    data = Path(tempfile.mkdtemp(prefix="stress-full-"))
    server, srv, port = await new_server(data)
    players: list[Player] = []
    errors_before = len(h.errcap.records)
    moves = 0
    try:
        for n in ("Ana", "Ben", "Cat"):
            players.append(await Player(h, port, n[0].lower() * 20, n).connect())
        await players[0].send({"t": "set", "key": "family", "value": {"together": True, "home_site": 7}})
        await players[0].send({"t": "set", "key": "house:7:live", "value": "h" * 50_000})
        await settle(server, players)
        for i in range(400):
            p = h.rng.choice(players)
            lot = h.rng.randint(1, 30)
            r = h.rng.random()
            if r < 0.06:                                         # the family moves home (often growing the state)
                order = server.lots_by_age({server.home_lot()})[0]
                if order and h.rng.random() < 0.5:               # to the lot changed longest ago: the first to go
                    lot = int(order[0])                          # for room, unless it's kept as the new home
                key, value = "family", {"together": True, "home_site": lot, "members": ["pa", "pb"],
                                        "pad": "p" * h.rng.randint(0, 40_000)}
                moves += 1
            elif r < 0.7:
                key, value = f"house:{lot}:live", "x" * h.rng.choice([100, 5_000, 20_000, 60_000])
            else:
                key, value = f"obj:{lot}:o{h.rng.randint(0, 9)}", {"on": h.rng.random() < 0.5}
            msg = {"t": "set", "key": key, "value": value, "ref": f"full{i}"}
            if h.rng.random() < 0.3:                             # some with a base (current, or stale: refused)
                cur = server.shared.get(key)
                msg["base"] = int((cur or {}).get("v") or 0) if h.rng.random() < 0.7 else 1
            await p.send(msg)
            if i % 10 == 0:
                await asyncio.sleep(0.01)
        await settle(server, players, timeout=60)        # every set handled and seen (not a fixed sleep)
        sv = server_view(server)
        diverged = [p.name for p in players if p.shared_view() != sv]
        cleared = min(p.removed for p in players)
        too_big = sum(p.too_big for p in players)
        cap_ok = server.shared_total <= mp.MAX_SHARED_TOTAL and server.shared_total == sum(server.shared_size.values())
        home_cleared = [x for p in players for x in p.home_cleared]
        home_ok = "family" in server.shared and not home_cleared
        mono = all(p.version_monotonic for p in players)
        seq, total = server.seq, server.shared_total
        for p in players:
            await p.drop()
        await stop_server(srv, server)
        again = mp.Server(mp.Store(data))
        restart_ok = server_view(again) == sv and again.seq >= seq

        # A restart under half the cap: the oldest lots go at start (no one is on), the home and other keys stay.
        mp.MAX_SHARED_TOTAL = max(1, total // 2)
        trimmed = mp.Server(mp.Store(data))
        home = trimmed.home_lot()
        stays = {k for k in sv if mp.Server.lot_of(k) is None or (home is not None and mp.Server.lot_of(k) == home)}
        _, lots, last = again.lots_by_age({home})
        kept = {mp.Server.lot_of(k) for k in trimmed.shared} - {None, home}
        gone = set(lots) - kept
        trim_ok = (trimmed.shared_total <= mp.MAX_SHARED_TOTAL or set(trimmed.shared) == stays) \
            and stays <= set(trimmed.shared) and bool(gone) \
            and all(sv.get(k) == server_view(trimmed)[k] for k in trimmed.shared) \
            and (not kept or not gone or min(last[x] for x in kept) >= max(last[x] for x in gone)) \
            and server_view(mp.Server(mp.Store(data))) == server_view(trimmed) and trimmed.seq >= seq
        new_errors = h.errcap.records[errors_before:]
        ok = (not diverged and cap_ok and home_ok and mono and restart_ok and trim_ok and not new_errors
              and cleared > 0 and not too_big and moves > 0)
        h.record(name, ok, f"keys={len(sv)} total={total} cleared_keys={cleared} too_big={too_big} moves={moves} "
                           f"diverged={diverged} cap_ok={cap_ok} home_ok={home_ok} {home_cleared[:2]} "
                           f"monotonic={mono} restart_ok={restart_ok} trim_ok={trim_ok} (lots gone at start: "
                           f"{len(gone)}, kept: {len(kept)}) errors={new_errors[:2]}")
    except Exception as e:                                       # noqa: BLE001
        h.record(name, False, f"raised {e!r}")
        for p in players:
            await p.drop()
        await stop_server(srv, server)
    finally:
        mp.MAX_SHARED_TOTAL, mp.LOT_IN_USE = saved


# ---------------------------------------------------------------------- main
async def main_async(args):
    h = Harness(args.seed)
    h.install()
    LOG.info("=== FUZZ (%.0f s, seed %d) ===", args.seconds, args.seed)
    await stage_fuzz(h, args.seconds)
    LOG.info("=== RESTART ===")
    await stage_restart(h)
    LOG.info("=== PROBES ===")
    await probe_slow_client(h)
    await probe_surrogate(h)
    await probe_deep_nesting(h)
    await probe_full_shared_state(h)

    print("\n" + "=" * 72)
    print("STRESS / FUZZ REPORT")
    print("=" * 72)
    passed = [r for r in h.results if r[1]]
    failed = [r for r in h.results if not r[1]]
    for name, ok, detail in h.results:
        print(f"  [{'OK ' if ok else 'BUG'}] {name}")
        if not ok and detail:
            print(f"        {detail}")
    print("-" * 72)
    print(f"{len(passed)} ok, {len(failed)} failing")
    if h.loop_errors:
        print(f"loop exceptions: {len(h.loop_errors)}")
    if h.errcap.records:
        print(f"ERROR log records: {len(h.errcap.records)}")
        for rec in h.errcap.records[:8]:
            print(f"    - {rec[:200]}")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description="Stress/fuzz the inZOI Multiplayer server")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        datefmt="%H:%M:%S")
    code = asyncio.run(main_async(args))
    sys.exit(code)


if __name__ == "__main__":
    main()
