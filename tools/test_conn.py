"""The companion's connection to the server (client/companion.py, posesync.py, servers.py), without the game:
- a dropped session that had joined is retried at once, even after an outage made the retries slow: the server's
  LINGER keeps it a silent "rejoined" for the partner; a server that's down still backs off
- the reader never waits on the network or the game: chat keeps flowing while the server takes nothing and the
  game's chat bubble hangs (one bubble call at a time); a server that takes nothing for SEND_STALL s counts as gone
- a dead connection is aborted: loops parked sending on it wake up and the skeleton frames go out on the new one; a
  send cut off by the abort reports failure
- my Zoi's state and skeleton frames are latest-wins: while the link is stuck only the newest of each waits (no pile
  of tasks or bytes, nothing old replayed after), the newest keyframe before the newest delta; a burst of a
  partner's states is applied as its newest
- on an uplink slower than the skeleton stream, frames reach the partner fresh and a shared change gets through fast
- a big set over a slow uplink that never stops keeps its connection however long it takes (progress counts, not a
  speed), arrives whole (it goes out in pieces, nothing written in its middle) and is confirmed
- switching away from a hung server, or picking another server while connecting, goes there at once (and files
  nothing of the old server under the new address), quietly; a server nobody answers is said to be unreachable,
  and a connection made after its attempt was given up on is closed
- the server counts as gone after SILENT_FOR s of silence, not when one line takes longer: a big welcome on a slow
  link joins on the first try; a message over the size cap ends the connection
- odd partner records in the welcome (states relayed as sent) can't keep the rest of it, the shared state above all,
  from being taken
- the server's epoch reaches on_shared_snapshot (welcome and shared_all); answers about my sets reach shared.py

    python tools/test_conn.py [test_name ...]
"""
import asyncio
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="test-conn-"))
os.environ["APPDATA"] = str(TMP / "appdata")                  # never the real settings (set before importing them)
os.environ["LOCALAPPDATA"] = str(TMP / "localappdata")
os.environ["INZOI_MP_DOCUMENTS"] = str(TMP / "docs")          # never the real game's folders
os.environ["INZOI_MP_DESKTOP"] = str(TMP / "desktop")
os.environ["INZOI_MP_GAME_URL"] = "http://127.0.0.1:9/mcp"   # no game
os.environ["INZOI_MP_UPDATE_API"] = "http://127.0.0.1:9/none"
os.environ["INZOI_MP_PANEL_PORT"] = "0"
os.environ["INZOI_MP_NO_MENU"] = "1"
os.environ.pop("INZOI_MP_SHARED_WORLD", None)
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "client"), str(ROOT / "server")]
import companion  # noqa: E402
import inzoi_mp_server as mp  # noqa: E402
from posesync import write_atomic  # noqa: E402

assert str(companion.CONFIG_DIR).startswith(str(TMP)), companion.CONFIG_DIR
assert str(companion.install.INSTALL_DIR).startswith(str(TMP)), companion.install.INSTALL_DIR
OUT = companion.install.INSTALL_DIR / "out"
LOOPS = ("game_loop", "act_loop", "mailbox_loop", "mods_loop", "update_loop", "menu_loop", "directory_loop",
         "ingame_loop", "lan_loop", "pose_loop", "shared_loop", "objsync_loop")


def line(msg: dict) -> bytes:
    return (json.dumps(msg) + "\n").encode()


def welcome(pid="p1", players=(), **extra) -> bytes:
    return line({"t": "welcome", "id": pid, "players": list(players), **extra})


BEN = {"id": "p2", "name": "Ben"}


def make(name: str, port: int, keep=()) -> companion.Companion:
    """A companion in the multiplayer save, playing; run()'s background loops idle except those in keep."""
    comp = companion.Companion({"key": (name * 20)[:20], "name": name, "server": "127.0.0.1", "port": port,
                                "password": ""})
    comp.save_cfg = lambda: None
    comp.world, comp.save_session["mp"] = "play", True
    for n in LOOPS:
        if n not in keep:
            async def idle():
                await asyncio.Event().wait()
            idle.__name__ = n
            setattr(comp, n, idle)
    return comp


async def no_panel(*a, **kw):
    return None


companion.webui.serve = no_panel                                # (run() starts the panel's web server)


async def start(comp):
    task = asyncio.create_task(comp.run())
    await asyncio.sleep(0)
    return task


async def stop(comp, task):
    task.cancel()
    for t in getattr(comp, "tasks", []):
        t.cancel()
    if comp.writer:
        comp.writer.transport.abort()
    await asyncio.sleep(0.05)


async def until(check, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        await asyncio.sleep(0.02)
    return bool(check())


async def serve(handler, limit: int = 1024 * 1024):
    """A stand-in server. (limit: what it reads ahead while its handler doesn't read: twice that, then the link backs
    up, as with a stuck server.)"""
    srv = await asyncio.start_server(handler, "127.0.0.1", 0, limit=limit)
    return srv, srv.sockets[0].getsockname()[1]


async def real_server():
    server = mp.Server(mp.Store(Path(tempfile.mkdtemp(prefix="srv-", dir=TMP))))
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
    return server, srv, srv.sockets[0].getsockname()[1]


class Raw:
    """A bare player on the real server (the partner who watches)."""

    def __init__(self, name):
        self.name, self.msgs, self.events = name, [], []

    async def join(self, port):
        self.r, self.w = await asyncio.open_connection("127.0.0.1", port, limit=mp.MAX_LINE)
        self.w.write(line({"t": "hello", "protocol": mp.PROTOCOL, "key": (self.name * 20)[:20], "name": self.name,
                           "version": companion.VERSION}))
        await self.w.drain()
        self.task = asyncio.create_task(self.read())
        assert await until(lambda: any(m.get("t") == "welcome" for m in self.msgs), 5), "the partner didn't join"
        return self

    async def read(self):
        while True:
            raw = await self.r.readline()
            if not raw:
                return
            m = json.loads(raw)
            m["_at"] = time.time()
            self.msgs.append(m)
            if m.get("t") == "ping":
                self.w.write(b'{"t":"pong"}\n')

    def close(self):
        self.task.cancel()
        self.w.transport.abort()


class Proxy:
    """A hop between a companion and the server standing in for Wi-Fi / Tailscale: it can refuse connections (the
    server unreachable), cut them (a blip: no "bye"), and limit what goes up to the server and what comes down from
    it (bytes/s)."""

    def __init__(self, target: int, up_rate: float = 0, down_rate: float = 0):
        self.target, self.up_rate, self.down_rate = target, up_rate, down_rate
        self.refuse, self.pairs, self.accepted = False, [], []

    async def start(self):
        if self.up_rate:             # (a slow link holds little: the backlog shows. Set before the handshake, so it's
            sock = socket.socket()   # the window the connection starts with, not only what this side reads ahead)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024)
            sock.bind(("127.0.0.1", 0))
            self.srv = await asyncio.start_server(self.handle, sock=sock, limit=16 * 1024)
            self.port = sock.getsockname()[1]
        else:
            self.srv, self.port = await serve(self.handle, limit=16 * 1024)   # (holds little itself: like a real link)
        return self

    async def handle(self, cr, cw):
        self.accepted.append(time.monotonic())
        if self.refuse:
            cw.transport.abort()
            return
        if self.up_rate:                                          # (a slow link holds little: the backlog shows)
            cw.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024)
        sr, sw = await asyncio.open_connection("127.0.0.1", self.target, limit=64 * 1024 * 1024)
        self.pairs.append((cw, sw))
        await asyncio.gather(self.pump(cr, sw, self.up_rate), self.pump(sr, cw, self.down_rate))

    async def pump(self, r, w, rate):
        try:
            while True:
                data = await r.read(16384)
                if not data:
                    break
                w.write(data)
                await w.drain()
                if rate:
                    await asyncio.sleep(len(data) / rate)
        except (OSError, ConnectionError):
            pass
        w.transport.abort()

    def cut(self):
        for cw, sw in self.pairs:
            cw.transport.abort()
            sw.transport.abort()
        self.pairs.clear()


async def b1_script(k_size: int, d_size: int, log: list | None = None):
    """The game's B1 script: a skeleton keyframe every second and a delta 15 times a second, each stamped with when it
    was written and which it is ("<time>|k<n>|..." and "<time>|d<i>|k<n>|...")."""
    OUT.mkdir(parents=True, exist_ok=True)
    i = 0
    while True:
        if i % 15 == 0:
            write_atomic(OUT / "posek.cfg", f"{time.time():.3f}|k{i // 15}|" + "1" * k_size)
            if log is not None:
                log.append(f"k{i // 15}")
        write_atomic(OUT / "posed.cfg", f"{time.time():.3f}|d{i}|k{i // 15}|" + "2" * d_size)
        if log is not None:
            log.append(f"d{i}")
        i += 1
        await asyncio.sleep(1 / 15)


def frame_tag(text: str) -> str:
    return text.split("|", 2)[1]


class Oddities(logging.Handler):
    """What the companion logged as unexpected (loop_error's "unexpected error", any traceback): a connection problem
    must come out as one run() knows (a quiet switch, "Can't reach the server"), never as one of these."""

    def __enter__(self):
        self.records = []
        logging.getLogger().addHandler(self)
        return self

    def __exit__(self, *exc):
        logging.getLogger().removeHandler(self)

    def emit(self, record):
        if record.exc_info or "unexpected error" in record.getMessage():
            self.records.append(f"{record.getMessage()} {record.exc_info[1]!r}" if record.exc_info
                                else record.getMessage())


# ---------------------------------------------------------------------------------------------------------------------
async def test_backoff():
    """After an outage the retries are slow (2, 4, 8 s...). A session that then joined and lasted is retried at once
    when it drops, inside the server's LINGER, so the partner sees "rejoined", never "left". Down again: backs off."""
    mp.LINGER = 3.0
    companion.SESSION_OK = 1.0
    server, srv, sport = await real_server()
    proxy = await Proxy(sport).start()
    ben = await Raw("Ben").join(sport)
    proxy.refuse = True                                           # the server can't be reached for a while...
    ana = make("Ana", proxy.port)
    task = await start(ana)
    assert await until(lambda: len(proxy.accepted) >= 2, 8), proxy.accepted        # (tries at 0, ~2 s; next at ~6)
    proxy.refuse = False                                          # ...then it can: Ana joins
    assert await until(lambda: ana.me_id is not None, 12), "Ana never joined after the outage"
    assert await until(lambda: any(m.get("t") == "joined" for m in ben.msgs), 3)
    await asyncio.sleep(1.5)                                      # a session that works (could be hours)...
    n, cut_at = len(proxy.accepted), time.monotonic()
    proxy.cut()                                                   # ...then one blip: no bye, the server lingers her
    assert await until(lambda: len(proxy.accepted) > n, 6), "no reconnect within 6 s"
    gap = proxy.accepted[n] - cut_at
    assert gap < 2.0, f"after a blip in a working session the retry waited {gap:.1f} s (LINGER is {mp.LINGER} s)"
    assert await until(lambda: ana.me_id is not None, 3)
    await asyncio.sleep(mp.LINGER + 0.5)
    seen = [m["t"] for m in ben.msgs if m.get("t") in ("joined", "left", "rejoined")]
    assert seen == ["joined", "rejoined"], f"the partner saw {seen}: Ana's Zoi left and came back"
    print(f"  ok: a blip after a working session is retried in {gap:.1f} s: the partner sees a silent rejoin")

    proxy.refuse = True                                           # the server goes away: retries back off again
    n = len(proxy.accepted)
    proxy.cut()
    assert await until(lambda: len(proxy.accepted) >= n + 3, 12), "no retries while the server is down"
    gaps = [b - a for a, b in zip(proxy.accepted[n:], proxy.accepted[n + 1:])]
    assert gaps[1] > gaps[0], f"a server that's down isn't backed off: {gaps}"
    print(f"  ok: a server that stays down is retried less and less often ({', '.join(f'{g:.1f}' for g in gaps)} s)")
    await stop(ana, task)
    ben.close()
    srv.close()


async def test_reader_never_blocks():
    """The server keeps talking but stops reading (its side stuck), and the game's chat bubble hangs: every chat line is
    still handled, one bubble call at a time. With SEND_STALL short, such a connection is given up (aborted)."""
    companion.SEND_STALL = 60
    stop_talking = asyncio.Event()

    async def talks_never_reads(r, w):
        await r.readline()
        w.write(welcome("p1", [BEN]))
        i = 0
        try:
            while not stop_talking.is_set():
                i += 1
                w.write(line({"t": "ping"}) + line({"t": "chat", "id": "p2", "name": "Ben", "text": f"msg {i}"}))
                await w.drain()
                await asyncio.sleep(0.1)
        except (OSError, ConnectionError):
            pass

    srv, port = await serve(talks_never_reads)
    ana = make("Ana", port, keep=("pose_loop",))
    hung, bubbling, most = threading.Event(), [], [0]

    def bubble(eid, text, seconds=6):                             # the game stopped answering
        bubbling.append(text)
        most[0] = max(most[0], len(bubbling))
        hung.wait(30)
        bubbling.remove(text)
    ana.game.bubble = bubble
    ana.puppet_ids["p2"] = 77
    sess = asyncio.create_task(ana.session())
    pose = asyncio.create_task(ana.pose_loop())
    b1 = asyncio.create_task(b1_script(200_000, 200_000))         # big frames: the way to the server fills up fast
    try:
        # (backed up: more waits than the transport's high-water mark. A big frame goes out in pieces, so it holds
        # little more than that, and the system's own buffer is full)
        assert await until(lambda: ana.me_id and ana.writer and ana.writer.transport.get_write_buffer_size() > 64 * 1024,
                           20), "the link never backed up"
        heard = lambda: len([c for c in ana.chat if not c.get("system")])        # noqa: E731
        before = heard()
        await asyncio.sleep(2.0)
        got = heard() - before
        assert got >= 10, f"only {got} chat lines handled in 2 s while sending was stuck (the server sent ~20)"
        assert not sess.done(), "the session ended"
        assert most[0] == 1, f"{most[0]} bubble calls at once for one Zoi"
        assert len(ana.bg_tasks) <= 2, f"{len(ana.bg_tasks)} background tasks piled up (pongs waiting for room?)"
        print(f"  ok: {got} chat lines handled in 2 s while the server took nothing and the game hung (1 bubble call)")
    finally:
        hung.set()
        for t in (sess, pose, b1):
            t.cancel()
        if ana.writer:
            ana.writer.transport.abort()
        stop_talking.set()

    companion.SEND_STALL = 1.0                                     # now give such a server up
    stop_talking.clear()
    ana = make("Ana", port, keep=("pose_loop",))
    sess = asyncio.create_task(ana.session())
    pose = asyncio.create_task(ana.pose_loop())
    b1 = asyncio.create_task(b1_script(200_000, 200_000))
    try:
        assert await until(lambda: ana.me_id is not None, 5)
        t0 = time.monotonic()
        assert await until(lambda: sess.done(), 20), "a server that takes nothing is never given up"
        try:
            sess.result()
            raise AssertionError("the session ended without an error")
        except (ConnectionError, OSError) as e:
            print(f"  ok: a server that took nothing for SEND_STALL was given up after {time.monotonic() - t0:.1f} s "
                  f"({e})")
    finally:
        stop_talking.set()
        for t in (sess, pose, b1):
            t.cancel()
        companion.SEND_STALL = 30
    srv.close()


async def test_abort_wakes_senders():
    """Connection 1 goes silent and stops reading while skeleton frames are being sent (pose_loop parked waiting for
    room on it). It's declared dead, aborted, and the frames go out on connection 2."""
    companion.SILENT_FOR, companion.SEND_STALL = 2.0, 30
    conns, frames, stuck = [], {1: 0, 2: 0}, []

    async def handle(r, w):
        n = len(conns) + 1
        conns.append(n)
        await r.readline()
        w.write(welcome("p1", [BEN]))
        await w.drain()
        if n == 1:                                                # alive but stuck: never reads, never writes
            stuck.append(w)
            await asyncio.Event().wait()
        while True:
            raw = await r.readline()
            if not raw:
                return
            if b'"pose"' in raw:
                frames[n] += 1

    srv, port = await serve(handle)
    ana = make("Ana", port, keep=("pose_loop",))
    b1 = asyncio.create_task(b1_script(200_000, 200_000))
    task = await start(ana)
    try:
        assert await until(lambda: 2 in conns, 15), "never reconnected"
        assert await until(lambda: frames[2] >= 5, 8), f"{frames[2]} skeleton frames on the new connection in 8 s"
        print(f"  ok: the dead connection was aborted; {frames[2]} skeleton frames went out on the new one")
    finally:
        b1.cancel()
        await stop(ana, task)
        companion.SILENT_FOR = 45
    srv.close()

    # a send parked waiting for room, cut off by the abort, says it failed (share() keeps the change to push again)
    held = []

    async def never_reads(r, w):
        held.append(w)
        await asyncio.Event().wait()
    srv, port = await serve(never_reads)
    ana = make("Ana", port)
    _, ana.writer = await asyncio.open_connection("127.0.0.1", port)
    ana.me_id = "p1"
    sender = None
    for _ in range(400):
        sender = asyncio.create_task(ana.send({"t": "chat", "text": "x" * 500_000}))
        await asyncio.sleep(0.02)
        if not sender.done():
            break
    assert sender and not sender.done(), "the link never backed up"
    ana.writer.transport.abort()
    try:
        await asyncio.wait_for(sender, 5)
        raise AssertionError("a send thrown away by the abort reported success")
    except ConnectionError:
        print("  ok: a send cut off by the abort raises ConnectionError")
    srv.close()


async def test_latest_wins():
    """The link is stuck for 6 s while my Zoi moves (10 positions/s) and the skeleton streams: no pile of tasks or
    bytes; once it flows again the server gets only a few, the newest state and the newest keyframe and delta (the
    keyframe first), not 6 s of old ones."""
    resume, got = asyncio.Event(), []

    async def stuck_then_reads(r, w):
        await r.readline()
        w.write(welcome("p1", [BEN]))
        await w.drain()
        await resume.wait()
        while True:
            raw = await r.readline()
            if not raw:
                return
            m = json.loads(raw)
            if m.get("t") == "state":
                got.append(("state", m["state"]["ts"]))
            elif m.get("kind") == "pose":
                for kind, text in m["data"].items():
                    got.append((kind, frame_tag(text)))

    srv, port = await serve(stuck_then_reads, limit=256 * 1024)
    ana = make("Ana", port, keep=("pose_loop",))
    sess = asyncio.create_task(ana.session())
    assert await until(lambda: ana.me_id is not None, 5)
    ana.sending_state, ana.state_base = True, {"act": None}
    ana.last_full_state = {"city": "Dowon", "pad": "p" * 100_000}   # (big: the kernel's buffers can't hide them all)
    written = []
    pose = asyncio.create_task(ana.pose_loop())
    b1 = asyncio.create_task(b1_script(100_000, 50_000, written))
    await asyncio.sleep(0.3)
    base = len(asyncio.all_tasks())
    most_tasks = most_buf = 0
    last_ts = None
    for i in range(60):
        ana.on_report("me", f"{i} 2 3 90 1038")
        last_ts = ana.fast_pos["at"]
        await asyncio.sleep(0.1)
        most_tasks = max(most_tasks, len(asyncio.all_tasks()) - base)
        most_buf = max(most_buf, ana.writer.transport.get_write_buffer_size())
    b1.cancel()
    await asyncio.sleep(0.2)
    newest_k = [x for x in written if x.startswith("k")][-1]
    newest_d = [x for x in written if x.startswith("d")][-1]
    resume.set()
    await asyncio.sleep(2.0)
    states = [ts for kind, ts in got if kind == "state"]
    assert most_tasks <= 3, f"{most_tasks} tasks piled up sending while the link was stuck"
    assert most_buf < 2_000_000, f"{most_buf} bytes piled up waiting to be sent"
    assert len(states) < 30, f"{len(states)} of 60 old positions replayed after the link came back"
    assert states and abs(states[-1] - round(last_ts, 3)) < 0.002, "the newest position never went out"
    kinds = [(k, tag) for k, tag in got if k in ("k", "d")]
    assert ("k", newest_k) in kinds and ("d", newest_d) in kinds, (newest_k, newest_d, kinds[-6:])
    assert kinds.index(("k", newest_k)) < kinds.index(("d", newest_d)), "the delta went out before its keyframe"
    deltas = [tag for k, tag in kinds if k == "d"]
    assert len(deltas) < 40, f"{len(deltas)} of {len(written)} old skeleton frames replayed"
    print(f"  ok: stuck 6 s: at most {most_tasks} send task(s) and {most_buf // 1024} KB waiting; after, "
          f"{len(states)} states and {len(deltas)} deltas arrived, ending with the newest (keyframe first)")
    for t in (sess, pose):
        t.cancel()
    ana.writer.transport.abort()
    srv.close()


async def test_state_burst():
    """A burst of a partner's states read in one go (their link was stuck, or an older program sent every one) is
    applied as its newest only; states of a partner who left in the same burst aren't applied."""
    async def handle(r, w):
        await r.readline()
        w.write(welcome("p1", [BEN, {"id": "p3", "name": "Cy"}]))
        await w.drain()
        await asyncio.sleep(0.3)
        burst = b"".join(line({"t": "state", "id": pid, "state": {"n": i, "x": i, "y": 0, "z": 0}})
                         for i in range(1, 51) for pid in ("p2", "p3"))
        w.write(burst + line({"t": "left", "id": "p3", "name": "Cy"}))
        await w.drain()
        while await r.readline():
            pass
    srv, port = await serve(handle)
    ana = make("Ana", port)
    applied = []
    real = ana.set_state
    ana.set_state = lambda r, st: (applied.append((r.pid, st["n"])), real(r, st))
    sess = asyncio.create_task(ana.session())
    assert await until(lambda: ("p2", 50) in applied, 5), applied[-3:]
    await asyncio.sleep(0.2)
    mine = [n for pid, n in applied if pid == "p2"]
    assert len(mine) <= 5 and mine[-1] == 50, f"{len(mine)} of 50 states applied: {mine[:8]}..."
    assert "p3" not in ana.remotes and not [n for pid, n in applied if pid == "p3"], applied
    print(f"  ok: a burst of 50 states applied as {len(mine)} (the newest); none for a partner who left meanwhile")
    sess.cancel()
    ana.writer.transport.abort()
    srv.close()


async def test_slow_uplink():
    """Ana's uplink (250 KB/s) is slower than her skeleton stream (~400 KB/s) plus her position (10/s): the stream thins
    out instead of piling up, so the partner's view of her stays a couple of seconds behind at most (what the link
    itself holds), keyframes keep coming, and a shared change she makes doesn't wait behind a backlog."""
    server, srv, sport = await real_server()
    proxy = await Proxy(sport, up_rate=250_000).start()
    ben = await Raw("Ben").join(sport)
    ana = make("Ana", proxy.port, keep=("pose_loop",))
    task = await start(ana)
    b1 = None
    try:
        assert await until(lambda: ana.me_id is not None and ben.name in [r.name for r in ana.remotes.values()], 5)
        ana.sending_state, ana.state_base = True, {"act": None}
        ana.last_full_state = {"city": "Dowon", "pad": "p" * 1500}
        b1 = asyncio.create_task(b1_script(60_000, 22_000))
        t_end = time.monotonic() + 14
        sets = {}
        k = 0
        while time.monotonic() < t_end:
            for _ in range(10):
                ana.on_report("me", "1 2 3 90 1038")
                await asyncio.sleep(0.1)
            k += 1
            if k % 4 == 0:
                sets[f"obj:1038:lamp{k}"] = time.time()
                await ana.send({"t": "set", "key": f"obj:1038:lamp{k}", "value": {"on": k}})
        await until(lambda: set(sets) <= {m.get("key") for m in ben.msgs if m.get("t") == "shared"}, 5)
        poses = [(m["_at"], kind, text) for m in ben.msgs if m.get("kind") == "pose"
                 for kind, text in (m.get("data") or {}).items()]
        late = [(at, at - float(text.split("|", 1)[0])) for at, kind, text in poses if kind == "d"]
        recent = [age for at, age in late if at > time.time() - 8]
        keys = [at for at, kind, _ in poses if kind == "k" and at > time.time() - 8]
        assert recent, "no skeleton frames reached the partner"
        assert max(recent) < 4.0, f"the partner sees Ana's skeleton {max(recent):.1f} s late"
        assert len(keys) >= 3, f"only {len(keys)} keyframes in 8 s"
        confirm = {m["key"]: m["_at"] for m in ben.msgs if m.get("t") == "shared"}
        waits = [confirm[key] - at if key in confirm else 99 for key, at in sets.items()]
        assert max(waits) < 4.0, f"shared changes took {[round(w, 1) for w in waits]} s to get through"
        print(f"  ok: uplink slower than the stream: skeleton at most {max(recent):.1f} s late, {len(keys)} keyframes "
              f"in 8 s, shared changes through in {max(waits):.1f} s at most")
    finally:
        if b1:
            b1.cancel()
        await stop(ana, task)
        ben.close()
        srv.close()


async def test_slow_big_set():
    """Ana's uplink is slow (16 KB/s, a weak relay) but never stops, and she shares a set much bigger than what the
    system's buffers hold (an exact house) while her skeleton and position stream and the server pings her: the
    connection is kept however long that takes (what counts is that the link keeps taking bytes, not a speed it must
    reach), the set arrives whole (nothing written in its middle: no pong, no frame) and is confirmed, the pongs that
    came meanwhile wait as one, and the streams carry on after it. (SEND_STALL is scaled down from 30 s, the size
    with it: about 40 s of upload, 3 times SEND_STALL.)"""
    rate, size = 16_000, 600_000
    companion.SEND_STALL, mp.PING_EVERY = 12.0, 2
    server, srv, sport = await real_server()
    keep = asyncio.create_task(server.keepalive())                # (pings: Ana's reader answers them meanwhile)
    proxy = await Proxy(sport, up_rate=rate).start()
    ben = await Raw("Ben").join(sport)
    ana = make("Ana", proxy.port, keep=("pose_loop",))
    task = await start(ana)
    b1 = walk = None
    stalls = []

    class Stalls(logging.Handler):
        def emit(self, record):
            if "took nothing" in record.getMessage():
                stalls.append(record.getMessage())
    watch = Stalls()
    logging.getLogger("companion").addHandler(watch)
    try:
        assert await until(lambda: ana.me_id is not None and ana.shared_conn is ana.writer, 5)
        first = ana.writer
        ana.sending_state, ana.state_base = True, {"act": None}
        ana.last_full_state = {"city": "Dowon", "pad": "p" * 300}
        b1 = asyncio.create_task(b1_script(4_000, 400))
        most_tasks = [0]

        async def walking():
            i = 0
            while True:
                i += 1
                ana.on_report("me", f"{i} 2 3 90 1038")
                most_tasks[0] = max(most_tasks[0], len(ana.bg_tasks))
                await asyncio.sleep(0.1)
        walk = asyncio.create_task(walking())
        await asyncio.sleep(1.0)
        value = {"site": 1038, "hash": "h", "files": {"site.dat": "x" * size}}
        t0 = time.monotonic()
        res = await asyncio.wait_for(ana.share_ex("house:1038:exact", value), 80)
        took = time.monotonic() - t0
        assert not stalls, stalls
        assert res == "ok", f"the set over a slow link that never stopped came back {res!r} after {took:.0f} s"
        assert ana.writer is first and len(proxy.accepted) == 1, f"{len(proxy.accepted)} connections: it was cut"
        assert took > 0.5 * size / rate, f"took only {took:.1f} s: the link wasn't slow"
        got = [m for m in ben.msgs if m.get("t") == "shared" and m.get("key") == "house:1038:exact"]
        assert got and got[0]["item"]["value"] == value, "the partner didn't get the set whole"
        assert most_tasks[0] <= 2, f"{most_tasks[0]} background tasks piled up waiting to answer pings"
        n_states, n_frames = (len([m for m in ben.msgs if m.get("t") == "state"]),
                              len([m for m in ben.msgs if m.get("kind") == "pose"]))
        await asyncio.sleep(3.0)
        more_states = len([m for m in ben.msgs if m.get("t") == "state"]) - n_states
        more_frames = len([m for m in ben.msgs if m.get("kind") == "pose"]) - n_frames
        assert more_states >= 5 and more_frames >= 5, (more_states, more_frames)
        assert ana.writer is first and not stalls, stalls
        print(f"  ok: a {size // 1000} KB set over a {rate // 1000} KB/s uplink took {took:.0f} s (SEND_STALL "
              f"{companion.SEND_STALL:g} s) on one connection, arrived whole and was confirmed; then {more_states} "
              f"states and {more_frames} skeleton frames in 3 s")
    finally:
        logging.getLogger("companion").removeHandler(watch)
        for t in (b1, walk, keep):
            if t:
                t.cancel()
        await stop(ana, task)
        ben.close()
        srv.close()
        companion.SEND_STALL, mp.PING_EVERY = 30, 15


async def test_cancelled_big_send():
    """A big message going out in pieces whose sender stops waiting (cancelled half-way) is still finished whole, and
    what's sent after it follows it: the server reads both, never a line cut short with another message in it."""
    got, reading = [], asyncio.Event()

    async def handle(r, w):
        await r.readline()                                        # (the hello)
        w.write(welcome("p1", []))
        await w.drain()
        w.transport.pause_reading()                               # takes nothing for a while: the link backs up
        await reading.wait()
        w.transport.resume_reading()
        while raw := await r.readline():
            got.append(raw)
    size = 6_000_000
    srv, port = await serve(handle, limit=16 * 1024 * 1024)
    ana = make("Ana", port)
    sess = asyncio.create_task(ana.session())
    try:
        assert await until(lambda: ana.me_id is not None, 5)
        big = asyncio.create_task(ana.send({"t": "chat", "text": "x" * size}))
        assert await until(lambda: ana.writer.transport.get_write_buffer_size() > 64 * 1024, 5), "never backed up"
        big.cancel()
        await asyncio.sleep(0.1)
        assert big.cancelled(), "the big send wasn't cut off half-way"
        after = asyncio.create_task(ana.send({"t": "chat", "text": "after"}))
        await asyncio.sleep(0.1)
        reading.set()
        await asyncio.wait_for(after, 10)
        assert await until(lambda: len(got) >= 2, 10), f"{len(got)} line(s) arrived"
        first, second = json.loads(got[0]), json.loads(got[1])
        assert len(first["text"]) == size and second["text"] == "after", (len(first.get("text", "")), second)
        assert not ana.links[ana.writer].lock.locked()
        print(f"  ok: a {size // 1000000} MB message whose sender was cancelled half-way still went out whole, then "
              f"the next one")
    finally:
        reading.set()
        sess.cancel()
        if ana.writer:
            ana.writer.transport.abort()
        srv.close()


async def test_switch():
    """Switching away from a server that hangs (takes nothing more while the skeleton was streaming to it) gets to the
    new one in a moment; picking another server while the connection to the old one is still being made goes to the
    new one, and the old server's id isn't filed under the new address."""
    def server(sid, hang=False):
        async def handle(r, w):
            seen.append(sid)
            await r.readline()
            w.write(welcome("p1", [BEN], server={"id": sid, "name": f"Server {sid}", "public": False}))
            await w.drain()
            if hang:
                await asyncio.Event().wait()
            while await r.readline():
                pass
        return handle

    seen = []
    sa, pa = await serve(server("A"))
    sb, pb = await serve(server("B", hang=True), limit=64 * 1024)
    ana = make("Ana", pb, keep=("pose_loop",))
    b1 = asyncio.create_task(b1_script(200_000, 200_000))
    odd = Oddities().__enter__()
    task = await start(ana)
    assert await until(lambda: (ana.server_info or {}).get("id") == "B", 5)
    assert await until(lambda: ana.writer and ana.writer.transport.get_write_buffer_size() > 64 * 1024, 20), \
        "the link to B never backed up"
    b1.cancel()
    t0 = time.monotonic()
    await ana.join(f"127.0.0.1:{pa}", "", "Server A")                 # B hangs: leaving it mustn't wait on it
    assert await until(lambda: (ana.server_info or {}).get("id") == "A" and ana.me_id, 10), "never got to A"
    print(f"  ok: switching away from a hung server took {time.monotonic() - t0:.1f} s")

    real_open, connecting = asyncio.open_connection, asyncio.Event()

    async def slow_open(*a, **kw):                                  # a handshake that takes a moment (Tailscale relay)
        connecting.set()
        await asyncio.sleep(1.0)
        return await real_open(*a, **kw)
    seen.clear()
    asyncio.open_connection = slow_open
    ana.writer.transport.abort()                                    # A drops: reconnecting to A, slowly...
    try:
        await asyncio.wait_for(connecting.wait(), 6)
        await asyncio.sleep(0.2)
        assert ana.me_id is None
        t0 = time.monotonic()
        await ana.join(f"127.0.0.1:{pb}", "", "Server B")           # ...and the player picks B meanwhile
        assert await until(lambda: (ana.server_info or {}).get("id") == "B" and ana.me_id, 6), \
            f"stayed on {(ana.server_info or {}).get('id')} (connections: {seen})"
        took = time.monotonic() - t0
    finally:
        asyncio.open_connection = real_open
    entries = {s["address"]: s.get("id") for s in ana.saved()}
    assert entries.get(f"127.0.0.1:{pb}") in ("B", "", None), f"A's id filed under B's address: {entries}"
    assert "A" not in seen, f"joined A after B was picked: {seen}"
    odd.__exit__()
    assert not odd.records, f"switching servers logged unexpected errors: {odd.records}"
    assert not ana.notice.startswith("Can't reach"), f"a switch was shown as an unreachable server: {ana.notice}"
    print(f"  ok: picking B while still connecting to A got to B in {took:.1f} s, quietly; saved: {entries}")
    await stop(ana, task)
    sa.close()
    sb.close()


async def test_connect_timeout():
    """The partner who hosts over Tailscale turned their PC off: the connection attempt gets no answer at all. Each
    attempt gives up after CONNECT_WITHIN, the page says the server can't be reached, and nothing is logged as an
    unexpected error. A handshake that still completes after it was given up on is closed, not left open."""
    companion.CONNECT_WITHIN = 0.5
    real_open, tries = asyncio.open_connection, []

    async def no_answer(*a, **kw):
        tries.append(time.monotonic())
        await asyncio.Event().wait()
    ana = make("Ana", 1)
    task = None
    try:
        with Oddities() as odd:
            asyncio.open_connection = no_answer
            try:
                task = await start(ana)
                assert await until(lambda: ana.notice.startswith("Can't reach the server"), 5), \
                    f"no attempt was shown as unreachable: {ana.notice!r}"
                assert await until(lambda: len(tries) >= 2, 8), f"{len(tries)} attempt(s): not tried again"
            finally:
                asyncio.open_connection = real_open
                if task:
                    await stop(ana, task)
        assert not odd.records, f"an unanswered connection attempt logged unexpected errors: {odd.records}"
        print(f"  ok: an attempt nobody answers gives up after {companion.CONNECT_WITHIN:g} s (\"{ana.notice}\"), "
              f"{len(tries)} attempts, nothing unexpected logged")

        accepted, closed = [], []

        async def handle(r, w):
            accepted.append(w)
            try:
                await r.read(1)
            except (OSError, ConnectionError):
                pass
            closed.append(time.monotonic())
        srv, port = await serve(handle)

        async def stubborn(*a, **kw):                              # (a handshake that finishes anyway)
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                pass
            return await real_open(*a, **kw)
        asyncio.open_connection = stubborn
        try:
            await ana.open_server("127.0.0.1", port)
            raise AssertionError("open_server returned a connection it had given up on")
        except asyncio.TimeoutError:
            pass
        finally:
            asyncio.open_connection = real_open
        assert await until(lambda: accepted, 3), "the late handshake never reached the server"
        assert await until(lambda: closed, 3), "a connection made after its attempt was given up on was left open"
        print("  ok: a connection made after its attempt was given up on is closed at once")
        srv.close()
    finally:
        asyncio.open_connection = real_open
        companion.CONNECT_WITHIN = 20


async def test_slow_welcome():
    """A welcome much bigger than what the link moves in SILENT_FOR (the whole shared state, two exact houses, over a
    Tailscale relay): as long as bytes keep coming it's waited for, and the companion joins on its first connection
    instead of downloading it again forever. A server that goes quiet in the middle of a message is still given up
    after SILENT_FOR, and a message over the size cap ends the connection."""
    companion.SILENT_FOR = 1.0
    try:
        server, srv, sport = await real_server()
        ben = await Raw("Ben").join(sport)
        for i in range(2):                                        # ~1.3 MB of shared state: the welcome carries it
            ben.w.write(line({"t": "set", "key": f"big:{i}", "value": {"pad": "x" * 650_000}, "ref": f"r{i}"}))
        await ben.w.drain()
        assert await until(lambda: len([m for m in ben.msgs if m.get("t") == "shared"]) >= 2, 5), \
            [m.get("t") for m in ben.msgs][-5:]
        proxy = await Proxy(sport, down_rate=300_000).start()     # (~4 s for the welcome: 4 times SILENT_FOR)
        ana = make("Ana", proxy.port)
        t0 = time.monotonic()
        task = await start(ana)
        try:
            assert await until(lambda: ana.me_id is not None, 15), \
                f"never joined ({len(proxy.accepted)} connection(s): the welcome was cut and downloaded again)"
            took = time.monotonic() - t0
            assert took > 2 * companion.SILENT_FOR, f"the welcome took only {took:.1f} s: not a slow one"
            assert len(proxy.accepted) == 1, f"{len(proxy.accepted)} connections: the welcome was downloaded again"
            assert ana.shared_conn is ana.writer and {"big:0", "big:1"} <= set(ana.shared), sorted(ana.shared)
            print(f"  ok: a 1.3 MB welcome at 300 KB/s ({took:.1f} s, SILENT_FOR {companion.SILENT_FOR:g} s) joined "
                  f"on the first connection")
        finally:
            await stop(ana, task)
            ben.close()
            srv.close()
            proxy.srv.close()

        async def half_then_quiet(r, w):
            await r.readline()
            w.write(b'{"t": "welcome", "id": "p1", "players": [], "pad": "' + b"x" * 50_000)
            await w.drain()
            await asyncio.Event().wait()
        srv, port = await serve(half_then_quiet)
        ana = make("Ana", port)
        t0 = time.monotonic()
        sess = asyncio.create_task(ana.session())
        done, _ = await asyncio.wait({sess}, timeout=5)
        took = time.monotonic() - t0
        assert sess in done, "a server gone quiet in the middle of a message was never given up"
        assert isinstance(sess.exception(), asyncio.TimeoutError), repr(sess.exception())
        assert took < companion.SILENT_FOR + 1.0, f"given up after {took:.1f} s of silence"
        print(f"  ok: a server gone quiet in the middle of a message was given up after {took:.1f} s "
              f"({sess.exception()})")
        ana.writer.transport.abort()
        srv.close()

        companion.MAX_LINE = 100_000

        async def endless(r, w):
            await r.readline()
            w.write(b'{"t": "welcome", "pad": "' + b"x" * 300_000)
            await w.drain()
            await asyncio.Event().wait()
        srv, port = await serve(endless)
        ana = make("Ana", port)
        sess = asyncio.create_task(ana.session())
        done, _ = await asyncio.wait({sess}, timeout=5)
        assert sess in done and isinstance(sess.exception(), ConnectionError), \
            repr(sess.exception() if sess in done else "still reading")
        print(f"  ok: a message over the cap ends the connection ({sess.exception()})")
        ana.writer.transport.abort()
        srv.close()
    finally:
        companion.SILENT_FOR, companion.MAX_LINE = 45, 32 * 1024 * 1024


async def test_welcome_odd_partner():
    """A welcome whose partner records are odd (states the server relayed as they were sent: a position without z,
    an action that's a bare string; a profile without its look; a record that isn't one; a server summary that isn't
    one), with their Zois already
    in my game (a reconnect): the rest of the welcome is still taken, the other partners and the shared state, so this
    session can share (and the server confirms it); a "joined" with such a state still says who joined."""
    joined = asyncio.Event()

    async def talk(w):
        players = [{"id": "p2", "name": "Ben", "version": companion.VERSION, "state": {"n": 1, "x": 1, "y": 2}},
                   {"id": "p3", "name": "Cy", "version": companion.VERSION,
                    "state": {"n": 1, "x": 1, "y": 2, "z": 3, "act": "Sit"}},
                   "junk",
                   {"id": "p4", "name": "Di", "version": companion.VERSION, "state": {"n": 1, "x": 4, "y": 5, "z": 6}},
                   {"id": "p6", "name": "Fay", "version": companion.VERSION, "profile": {"first_name": "Fay"},
                    "state": {"n": 1, "x": 7, "y": 8, "z": 9}}]
        w.write(welcome("p1", players, server="not a summary", seq=1, epoch="e1",
                        shared={"big:0": {"key": "big:0", "value": 1, "v": 1, "by": "p2", "by_name": "Ben"}}))
        await w.drain()
        await joined.wait()
        w.write(line({"t": "joined", "player": {"id": "p5", "name": "Eve", "version": companion.VERSION,
                                                "state": {"n": 1, "x": 1}}}))
        await w.drain()

    async def server(r, w):
        await r.readline()
        reply = asyncio.create_task(talk(w))
        v = 1
        while True:
            raw = await r.readline()
            if not raw:
                reply.cancel()
                return
            m = json.loads(raw)
            if m.get("t") == "set":                               # (the server's confirmation: my ref, back to me)
                v += 1
                w.write(line({"t": "shared", "key": m["key"], "v": v, "ref": m.get("ref"), "ref_by": "p1",
                              "item": {"key": m["key"], "value": m.get("value"), "v": v, "by": "p1", "by_name": "Ana"}}))
                await w.drain()

    srv, port = await serve(server)
    ana = make("Ana", port)
    for pid, eid in (("p2", 72), ("p3", 73), ("p5", 75)):        # (their Zois are in my game already)
        ana.puppet_ids[pid] = eid
    sess = asyncio.create_task(ana.session())
    try:
        assert await until(lambda: ana.me_id is not None, 5), "never joined"
        assert ana.shared_conn is ana.writer, "the shared state wasn't taken: nothing can be shared this session"
        assert "big:0" in ana.shared, sorted(ana.shared)
        assert {"p2", "p3", "p4"} <= set(ana.remotes), sorted(ana.remotes)
        assert (ana.remotes["p4"].state or {}).get("x") == 4, "a partner after the odd ones wasn't taken"
        assert (ana.remotes["p6"].state or {}).get("x") == 7, "an odd profile kept the partner's state out"
        res = await asyncio.wait_for(ana.share_ex("test:1", {"on": 1}), 5)
        assert res == "ok", f"a shared change after that welcome: {res}"
        joined.set()
        assert await until(lambda: any(c.get("text") == "Eve joined" for c in ana.chat), 3), \
            "a joined message with an odd state didn't say who joined"
        assert "p5" in ana.remotes
        assert not sess.done(), "the session ended"
        print("  ok: odd partner records in the welcome: the others and the shared state were still taken, a change "
              "was shared and confirmed; an odd \"joined\" still said who joined")
    finally:
        sess.cancel()
        if ana.writer:
            ana.writer.transport.abort()
        srv.close()


async def test_epoch():
    """The server's epoch (which shared state the versions belong to) is handed to on_shared_snapshot, from the welcome
    and from a shared_all; a refused set and an error about one of my sets (it carries my ref) reach
    on_shared_refused, so a share() waiting for them gets its answer."""
    async def handle(r, w):
        await r.readline()
        w.write(welcome("p1", [], shared={}, seq=3, epoch="e1"))
        w.write(line({"t": "shared_all", "shared": {}, "seq": 4, "epoch": "e2"}))
        w.write(line({"t": "set_refused", "key": "family", "ref": "r1", "current": {"v": 4}}))
        w.write(line({"t": "error", "code": "too_big", "error": "too big", "key": "house:1:exact", "ref": "r2"}))
        await w.drain()
        while await r.readline():
            pass
    srv, port = await serve(handle)
    ana = make("Ana", port)
    calls, refused = [], []

    def snapshot(items, seq=0, epoch=None):
        calls.append((seq, epoch))
    ana.on_shared_snapshot = snapshot
    ana.on_shared_refused = lambda m: refused.append(m.get("ref"))
    sess = asyncio.create_task(ana.session())
    assert await until(lambda: len(calls) >= 2 and len(refused) >= 2, 5), (calls, refused)
    assert calls == [(3, "e1"), (4, "e2")], calls
    assert refused == ["r1", "r2"], refused
    assert not sess.done(), "a non-fatal error ended the session"
    print("  ok: the server's epoch reaches on_shared_snapshot (welcome, shared_all); refusals and errors about my "
          "sets reach on_shared_refused")
    sess.cancel()
    ana.writer.transport.abort()
    srv.close()


async def main(names):
    tests = (test_epoch, test_welcome_odd_partner, test_slow_welcome, test_connect_timeout, test_backoff,
             test_reader_never_blocks, test_abort_wakes_senders, test_latest_wins, test_state_burst, test_slow_uplink,
             test_slow_big_set, test_cancelled_big_send, test_switch)
    for test in tests:
        if not names or test.__name__ in names:
            print(test.__name__)
            await asyncio.wait_for(test(), 90)
    print("connection tests passed")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
