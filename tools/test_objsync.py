"""The home's object states (client/objsync.py), PCs with stand-in games through a real server (port 0, temp data):
- an object switched on in one game reaches the other, which puts it in that state through the controller (at its
  own object's spot); the change applied there isn't sent back (nor what its game does right after)
- the first read of the lot is only the starting point; a later joiner gets the server's states applied once
- a reload (a new in-game controller, or the world left) reads the lot from scratch: the save's old states aren't sent
  as changes, the server's are put back on it (a change that came while out of the world, my own unsaved change);
  nothing is applied while the world is out or paused, it's applied at the next read
- two changes of one object that cross on the way: both PCs and the server end with the same (the server's last);
  a PC never applies the partner's older version after its own newer one
- a change lost on the way (the connection black-holed) is sent again after reconnecting; if the partner changed the
  object meanwhile, theirs wins on both PCs instead
- several server keys for one object: only the newest is applied; once mine is confirmed the older ones are removed
- of two objects of the same kind 80 cm apart, the change goes to the nearest one, and my own change to the other
  right after is still sent
- Slot groups (who sits where) are ignored
- a change read inside the quiet time after a partner's state was put on an object isn't sent at once; when that
  time is over both PCs end with one state: a change made after the partner's state took is sent, anything else
  (it never showed, the game won't take it, a reloaded save put its old state back) gets the server's state again;
  a game that keeps putting an object back is settled once and then left (no flipping it to and fro)
- an object placed anew in its default states (the house sync's remove + place for a partner's turn or nudge, the
  exact house rebuilding the lot after the first read) sends nothing: it gets the server's state, or the one the
  object it replaces had; what was put on the old one a moment ago doesn't count for it
- an object moved (the same entity elsewhere) takes its state along: sent for the new spot unless the server's state
  there is its own already (a short move), the old spot's key removed; moved right after a partner's state was put
  on it, the partner's state goes along
- a change made offline survives leaving the world before the server is back: sent at the next first read if the
  save kept it and the server has nothing newer for that object
- a partner's change, then mine inside its quiet time, twice in a row (within settle_span()): mine reaches the server
  and the partner's game both times; the to-and-fro guard is only for a settle answering a settle (its value says so)
- a change made after the program started while the server couldn't be reached (nothing of it known yet) wins when
  the connection comes, over an older partner change of that object: pending, or kept through leaving the world

    python tools/test_objsync.py
"""
import asyncio
import json
import logging
import math
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["APPDATA"] = str(TMP / "appdata")             # (never the real config)
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "client"), str(ROOT / "server")]
import inzoi_mp_server as mp  # noqa: E402
import objsync  # noqa: E402
import shared  # noqa: E402

SITE = 1038


class Mail:
    def __init__(self):
        self.ops = []

    def op(self, name, **f):
        self.ops.append((name, f))


class Tele:
    def rec(self, k, **d):
        pass


class Game:
    """A save's home lot: objects with state groups, set_states as the controller does it (the nearest of that kind
    within 100 cm; no states = Normal:Normal)."""

    def __init__(self, objs):
        self.objs = [{"id": i, "t": t, "p": [x, y, z], "s": dict(s.split(":", 1) for s in st.split(",") if s),
                      "ch": "" if ch == "None" else ch} for i, t, x, y, z, st, ch in objs]

    def report(self):
        return ";".join([str(SITE)] + [
            f"{o['id']}|{o['t']}|{o['p'][0]}|{o['p'][1]}|{o['p'][2]}|"
            f"{','.join(sorted(f'{g}:{v}' for g, v in o['s'].items()))}|{o['ch'] or 'None'}" for o in self.objs])

    def run(self, mail):
        sets = [f for n, f in mail.ops if n == "set_states"]
        mail.ops.clear()
        for f in sets:
            best, bd = None, 100
            for o in self.objs:
                if o["t"] == f["table"]:
                    d = math.dist(o["p"], (f["x"], f["y"], f["z"]))
                    if d < bd:
                        best, bd = o, d
            if best:
                for s in f["states"] or ["Normal:Normal"]:
                    g, v = s.split(":", 1)
                    best["s"][g] = v
                if f.get("channel"):
                    best["ch"] = f["channel"]
        return sets

    def of(self, table, n=0):
        return [o for o in self.objs if o["t"] == table][n]

    def look(self, table, n=0):
        o = self.of(table, n)
        return (sorted(f"{g}:{v}" for g, v in o["s"].items() if not g.startswith("Slot")), o["ch"])


class PC(shared.SharedState, objsync.ObjSync):
    def __init__(self, name, game):
        self.name, self.key, self.game = name, (name * 16)[:16], game
        self.cfg, self.mail, self.tele = {}, Mail(), Tele()
        self.writer = self.me_id = None
        self.world, self.leader = "play", None
        self.mod = {"state": "running", "instance": "i1"}
        self.gate = asyncio.Event()
        self.gate.set()
        self.applied = []                                  # every set_states this PC's game ran
        self.shared_init()
        self.objsync_init()

    def save_cfg(self):
        pass

    def mp_active(self):
        return True

    async def home_site_cached(self):
        return SITE

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

    def tick(self):
        """The game runs what was asked of it, then the lot is read."""
        self.applied += self.game.run(self.mail)
        self.on_states_report(self.game.report())

    # stand-ins for the other mixins shared.py dispatches to
    def on_shared_house_live(self, *a): pass
    def on_shared_house_exact(self, *a): pass


async def until(check, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if check():
            return True
        await asyncio.sleep(0.02)
    return False


async def settle(*pcs):
    """Every change sent is confirmed (or given up) and every message read."""
    assert await until(lambda: not any(p.shared_out for p in pcs)), [p.shared_out for p in pcs]
    await asyncio.sleep(0.05)


LOT = [(1, "Microwave01_v02", 100, 200, 90, "", "None"), (2, "Tv01", 500, 0, 50, "OnOff:Off", "Off"),
       (3, "Chair03_v05", 10, 10, 0, "SlotOccupy:Occupy", "None")]


def moved(lot, dx=3, dy=-2, first_id=9):
    """The same home in another save: other ids, a few cm apart."""
    return [(first_id + n, t, x + dx, y + dy, z, s, ch) for n, (_, t, x, y, z, s, ch) in enumerate(lot)]


async def basics(server, port):
    ana, ben = await PC("Ana", Game(LOT)).connect(port), await PC("Ben", Game(moved(LOT))).connect(port)
    ana.tick(), ben.tick()
    await settle(ana, ben)
    assert not server.shared and not ana.applied and not ben.mail.ops, "the first read is only the starting point"

    # Ana switches the microwave on and the TV to a channel; her Zoi sits on the chair (not synced)
    ana.game.of("Microwave01_v02")["s"] = {"OnOff": "On"}
    ana.game.of("Tv01")["ch"] = "News"
    ana.game.of("Chair03_v05")["s"] = {}
    ana.tick()
    await settle(ana, ben)
    assert sorted(i["value"]["t"] for i in server.shared.values()) == ["Microwave01_v02", "Tv01"], server.shared
    sets = [f for n, f in ben.mail.ops if n == "set_states"]
    assert len(sets) == 2 and not ana.mail.ops, (sets, ana.mail.ops)
    mw = next(f for f in sets if f["table"] == "Microwave01_v02")
    assert mw["states"] == ["OnOff:On"] and mw["site"] == SITE and (mw["x"], mw["y"]) == (103, 198), mw
    assert next(f for f in sets if f["table"] == "Tv01")["channel"] == "News"
    print("an object changed in one game is put in that state in the other (at its own spot): ok")

    # Ben's game now reads them so (and the microwave flickers to something else right after): nothing goes back
    n = server.seq
    ben.tick()
    ben.game.of("Microwave01_v02")["s"] = {"Normal": "Normal"}
    ben.tick()
    await settle(ana, ben)
    assert server.seq == n, "the applied change was sent back"
    assert ben.game.look("Tv01") == (["OnOff:Off"], "News")
    print("an applied change isn't sent back: ok")

    # later, Ben switches the microwave off himself: it reaches Ana
    ben.obj_applied = [(a[0] - 100,) + a[1:] for a in ben.obj_applied]
    ben.game.of("Microwave01_v02")["s"] = {"OnOff": "Off"}
    ben.tick()
    await settle(ana, ben)
    ana.tick()
    assert ana.game.look("Microwave01_v02") == (["OnOff:Off"], ""), ana.game.look("Microwave01_v02")
    print("the partner's own change comes back the other way: ok")

    # a third PC joining: the server's states are applied once its lot is first read, and not again
    cat = await PC("Cat", Game(LOT)).connect(port)
    assert not cat.mail.ops
    cat.tick()
    got = sorted(f["table"] for n, f in cat.mail.ops if n == "set_states")
    assert got == ["Microwave01_v02", "Tv01"], got
    cat.tick()
    cat.on_shared_snapshot({k: dict(v) for k, v in server.shared.items()}, server.seq)
    cat.tick()
    assert not cat.mail.ops and len(cat.applied) == 2, cat.applied
    print("a later joiner gets the server's states once: ok")
    for p in (ana, ben, cat):
        await p.drop()


async def reload(server, port):
    """#5: a reload on the same lot puts the save's old states back; they aren't sent, the server's are re-applied."""
    lot = [(2, "Tv01", 500, 0, 50, "OnOff:Off", "None"), (4, "Lamp01", 900, 0, 0, "OnOff:Off", "None")]
    ana, ben = await PC("Ana2", Game(lot)).connect(port), await PC("Ben2", Game(moved(lot))).connect(port)
    ana.tick(), ben.tick()

    # Ben leaves the world (loading); meanwhile Ana switches the TV on: nothing is applied while he's out
    ben.world = "out"
    objsync.READ_EVERY = 0.02
    loop = asyncio.create_task(ben.objsync_loop())
    assert await until(lambda: ben.obj_site is None), "leaving the world didn't reset the object states"
    loop.cancel()
    objsync.READ_EVERY = 3.0
    ana.game.of("Tv01")["s"] = {"OnOff": "On"}
    ana.tick()
    await settle(ana, ben)
    assert not ben.mail.ops, "applied while out of the world"
    tv_key = next(k for k in server.shared if server.shared[k]["value"]["t"] == "Tv01")
    v = server.shared[tv_key]["v"]
    # back in: his save still has the TV off - the first read sends nothing and puts Ana's state on it
    ben.world = "play"
    ben.tick()
    ben.tick()
    await settle(ana, ben)
    assert server.shared[tv_key]["v"] == v and server.shared[tv_key]["by"] == ana.me_id, "the reload's state was sent"
    assert ben.game.look("Tv01") == (["OnOff:On"], ""), ben.game.look("Tv01")
    print("a change that came while out of the world is applied when back, nothing sent from the save: ok")

    # Ben switches the lamp on (his own change), then the game reloads without saving (a new controller): the lamp
    # is off again in the save - put back on, not sent as off; the TV, applied before, is put back on too
    ben.game.of("Lamp01")["s"] = {"OnOff": "On"}
    ben.tick()
    await settle(ana, ben)
    lamp_key = next(k for k in server.shared if server.shared[k]["value"]["t"] == "Lamp01")
    lv = server.shared[lamp_key]["v"]
    ben.game = Game(moved(lot))                              # (the save as it was: both off)
    ben.mod["instance"] = "i2"
    n = server.seq
    ben.tick()
    ben.tick()
    await settle(ana, ben)
    assert server.seq == n and server.shared[lamp_key]["v"] == lv, "the reloaded save's states were sent"
    assert ben.game.look("Tv01")[0] == ["OnOff:On"] and ben.game.look("Lamp01")[0] == ["OnOff:On"], ben.game.objs
    print("a reload: the save's old states aren't sent, the server's (mine too) are put back: ok")

    # paused: a change waits for the next read in a running game
    ben.world = "paused"
    ana.tick()                                               # (Ben's lamp reached her game a while ago...)
    ana.obj_applied.clear()                                  # (...past the quiet time)
    ana.game.of("Lamp01")["s"] = {"OnOff": "Off"}
    ana.tick()
    await settle(ana, ben)
    assert not ben.mail.ops, "applied while paused"
    ben.world = "play"
    ben.tick()
    ben.tick()
    assert ben.game.look("Lamp01")[0] == ["OnOff:Off"]
    print("nothing applied while paused; applied at the next read: ok")

    # the program restarts (seen versions gone, the game as it was): the first read brings it to the server's states
    ana.game.of("Tv01")["s"] = {"OnOff": "Off"}
    ana.tick()
    await settle(ana, ben)
    await ben.drop()
    ben.game.of("Tv01")["s"] = {"OnOff": "On"}                # (the old state, in a game that kept running)
    ben2 = PC("Ben2", ben.game)
    ben2.mod["instance"] = "i2"
    await ben2.connect(port)
    n = server.seq
    ben2.tick()
    ben2.tick()
    await settle(ana, ben2)
    assert server.seq == n and ben2.game.look("Tv01")[0] == ["OnOff:Off"], ben2.game.objs
    print("a restarted program brings its game to the server's states: ok")
    for p in (ana, ben2):
        await p.drop()


async def crossing(server, port, first):
    """#20: Ana and Ben change one object at the same moment; both sets are on the server before either PC reads."""
    lot = [(2, "Tv01", 500, 0, 50, "OnOff:Off", "None")]
    ana, ben = await PC(f"Ana{first}", Game(lot)).connect(port), await PC(f"Ben{first}", Game(lot)).connect(port)
    ana.tick(), ben.tick()
    ana.gate.clear(), ben.gate.clear()                       # (neither reads anything for now)
    n = server.seq
    order = [(ana, "On"), (ben, "Broken")] if first == "ana" else [(ben, "Broken"), (ana, "On")]
    for pc, state in order:
        pc.game.of("Tv01")["s"] = {"OnOff": state}
        pc.tick()
        assert await until(lambda: server.seq == n + 1), "the set didn't reach the server"
        n += 1
    ana.gate.set(), ben.gate.set()
    await settle(ana, ben)
    ana.tick(), ben.tick()
    await settle(ana, ben)
    ana.tick(), ben.tick()
    last = order[-1][1]
    key = next(iter(k for k in server.shared if k.startswith(f"obj:{SITE}:")))
    assert server.shared[key]["value"]["s"] == [f"OnOff:{last}"], server.shared[key]
    assert ana.game.look("Tv01")[0] == ben.game.look("Tv01")[0] == [f"OnOff:{last}"], (ana.game.objs, ben.game.objs)
    loser = order[0][0]
    assert not loser.applied or all(f["states"] == [f"OnOff:{last}"] for f in loser.applied), loser.applied
    winner = order[-1][0]
    assert not [f for f in winner.applied if f["states"] != [f"OnOff:{last}"]], \
        f"{winner.name} applied the older version after its own: {winner.applied}"
    print(f"crossing changes ({first}'s first): server and both games end with the last one: ok")
    for p in (ana, ben):
        await p.drop()


class Proxy:
    """Between a PC and the server: can swallow what the PC sends (a link dying) and cut the connection."""

    def __init__(self, port):
        self.port, self.hole, self.links = port, False, []

    async def start(self):
        self.srv = await asyncio.start_server(self.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        return self.srv.sockets[0].getsockname()[1]

    async def handle(self, cr, cw):
        sr, sw = await asyncio.open_connection("127.0.0.1", self.port, limit=mp.MAX_LINE)
        self.links.append((cw, sw))

        async def pipe(r, w, up):
            try:
                while data := await r.read(65536):
                    if not (up and self.hole):
                        w.write(data)
                        await w.drain()
            except (OSError, ConnectionError):
                pass
            finally:
                w.close()
        await asyncio.gather(pipe(cr, sw, True), pipe(sr, cw, False))

    def cut(self):
        for cw, sw in self.links:
            cw.close()
            sw.close()
        self.links, self.hole = [], False


async def lost(server, port):
    """#24: a set swallowed on the way is never confirmed: kept and sent again after reconnecting."""
    shared.CONFIRM_WITHIN, objsync.RETRY_FIRST = 0.3, 0.1
    proxy = Proxy(port)
    pport = await proxy.start()
    lot = [(2, "Tv01", 500, 0, 50, "OnOff:Off", "None"), (4, "Lamp01", 900, 0, 0, "OnOff:Off", "None")]
    ana, ben = await PC("Ana3", Game(lot)).connect(port), await PC("Ben3", Game(lot)).connect(pport)
    ana.tick(), ben.tick()
    proxy.hole = True
    ben.game.of("Tv01")["s"] = {"OnOff": "On"}
    ben.tick()
    assert await until(lambda: not ben.shared_out), "no word from the server was taken as a confirmation"
    assert not server.shared and ben.obj_pending, (server.shared, ben.obj_pending)
    proxy.cut()
    assert await until(lambda: ben.writer is None)
    await ben.connect(pport)
    await asyncio.sleep(0.15)
    ben.retry_objects()
    await settle(ana, ben)
    item = next(iter(server.shared.values()), None)
    assert item and item["by"] == ben.me_id and item["value"]["s"] == ["OnOff:On"], server.shared
    assert not ben.obj_pending
    ana.tick()
    assert ana.game.look("Tv01")[0] == ["OnOff:On"]
    print("a change lost on the way is sent again after reconnecting, and reaches the partner: ok")

    # lost again, and Ana changes that lamp meanwhile: hers wins on both PCs (mine went on an older version)...
    for live in (True, False):
        proxy.hole = True
        state = "On" if live else "Dim"
        ben.game.of("Lamp01")["s"] = {"OnOff": state}
        ben.tick()
        if not live:                                     # (...while Ben is away: his goes again, and is refused)
            assert await until(lambda: not ben.shared_out)
            proxy.cut()
            assert await until(lambda: ben.writer is None)
        ana.obj_applied.clear()
        ana.game.of("Lamp01")["s"] = {"OnOff": f"Broken{state}"}
        ana.tick()
        await settle(ana)
        assert await until(lambda: not ben.shared_out)
        if live:                                         # (...while his is on its way: it comes to him, and counts)
            proxy.cut()
            assert await until(lambda: ben.writer is None)
        await ben.connect(pport)
        await asyncio.sleep(0.15)
        ben.retry_objects()
        await settle(ana, ben)
        for _ in range(2):
            ben.tick()
            await settle(ana, ben)
        ana.tick()
        lamp = next(i for i in server.shared.values() if i["value"]["t"] == "Lamp01")
        assert lamp["by"] == ana.me_id and lamp["value"]["s"] == [f"OnOff:Broken{state}"], lamp
        assert ben.game.look("Lamp01")[0] == ana.game.look("Lamp01")[0] == [f"OnOff:Broken{state}"], (ben.game.objs,
                                                                                         ana.game.objs)
        assert not ben.obj_pending, ben.obj_pending
        ben.obj_applied.clear()
    print("a lost change of an object the partner changed meanwhile (seen live, or while away): theirs wins: ok")
    shared.CONFIRM_WITHIN, objsync.RETRY_FIRST = 10.0, 3.0
    for p in (ana, ben):
        await p.drop()
    proxy.srv.close()


async def aliases(server, port):
    """#32: two server keys for one TV (it moved): only the newest is applied, and mine replaces both."""
    raw = await PC("Raw4", Game([])).connect(port)
    tv = lambda x, s: {"t": "Tv01", "p": [x, 0, 50], "s": [f"OnOff:{s}"], "ch": ""}   # noqa: E731
    new, old = f"obj:{SITE}:{objsync.obj_key('Tv01', 540, 0, 50)}", f"obj:{SITE}:{objsync.obj_key('Tv01', 450, 0, 50)}"
    for key, value in ((new, tv(540, "Off")), (old, tv(450, "On")), (new, tv(540, "Off"))):
        assert await raw.share(key, value, wait=True)
    cat = await PC("Cat4", Game([(7, "Tv01", 520, 0, 50, "OnOff:On", "None")])).connect(port)
    cat.tick()
    sets = [f for n, f in cat.mail.ops if n == "set_states"]
    assert [f["states"] for f in sets] == [["OnOff:Off"]], sets
    cat.tick()
    cat.on_shared_snapshot({k: dict(v) for k, v in server.shared.items()}, server.seq)      # (a reconnect)
    cat.tick()
    assert cat.game.look("Tv01")[0] == ["OnOff:Off"] and len(cat.applied) == 1, cat.applied
    print("two keys for one object: only the newest is applied (first read and snapshot): ok")

    cat.obj_applied.clear()                                  # (later: past the quiet time of what was applied)
    cat.game.of("Tv01")["s"] = {"OnOff": "On"}
    cat.tick()
    await settle(cat, raw)
    assert await until(lambda: new not in server.shared and old not in server.shared), list(server.shared)
    mine = [k for k, i in server.shared.items() if i["value"]["t"] == "Tv01"]
    assert len(mine) == 1 and server.shared[mine[0]]["by"] == cat.me_id, server.shared
    print("once my change of it is confirmed, the older keys of that object are removed: ok")
    for p in (raw, cat):
        await p.drop()


async def near(server, port, reverse):
    """#33: two lamps 80 cm apart; the far one changes in Ana's game."""
    lot = [(1, "Lamp01", 0, 0, 0, "OnOff:On", "None"), (2, "Lamp01", 80, 0, 0, "OnOff:Off", "None")]
    theirs = moved(lot, 2, 1)
    tag = "r" if reverse else "f"
    ana = await PC(f"Ana5{tag}", Game(lot)).connect(port)
    ben = await PC(f"Ben5{tag}", Game(theirs[::-1] if reverse else theirs)).connect(port)
    ana.tick(), ben.tick()
    ana.game.objs[1]["s"] = {"OnOff": "On"}
    ana.tick()
    await settle(ana, ben)
    sets = [f for n, f in ben.mail.ops if n == "set_states"]
    assert len(sets) == 1 and (sets[0]["x"], sets[0]["states"]) == (82, ["OnOff:On"]), sets
    ben.tick()
    far = next(o for o in ben.game.objs if o["p"][0] == 82)
    assert far["s"] == {"OnOff": "On"}
    # Ben switches the other lamp off right away (inside the quiet time of the one just applied): still sent
    near_ = next(o for o in ben.game.objs if o["p"][0] == 2)
    near_["s"] = {"OnOff": "Off"}
    ben.tick()
    await settle(ana, ben)
    ana.tick()
    assert ana.game.objs[0]["s"] == {"OnOff": "Off"} and ana.game.objs[1]["s"] == {"OnOff": "On"}, ana.game.objs
    print(f"two of a kind 80 cm apart ({'reversed' if reverse else 'same'} order): the nearest is changed, "
          "and my change to the other is still sent: ok")
    for p in (ana, ben):
        await p.drop()


class Sticky(Game):
    """A save where the game puts some objects back on its own: "refuse" objects ignore set_states; "revert" objects
    take it (one read shows it) and then go back to how they were before it."""

    def __init__(self, objs, refuse=(), revert=()):
        super().__init__(objs)
        self.refuse, self.revert, self.back = set(refuse), set(revert), []

    def run(self, mail):
        for o, s in self.back:                           # (the game's own reaction, one step after the state was put)
            o["s"] = s
        self.back = []
        before = {o["id"]: dict(o["s"]) for o in self.objs}
        keep = {o["id"]: dict(o["s"]) for o in self.objs if o["t"] in self.refuse}
        sets = super().run(mail)
        for o in self.objs:
            if o["id"] in keep:
                o["s"] = keep[o["id"]]
            elif o["t"] in self.revert and o["s"] != before[o["id"]]:
                self.back.append((o, before[o["id"]]))
        return sets


def sets_of(pc, table):
    return [f["states"] for f in pc.applied if f["table"] == table]


async def quiet(server, port):
    """A change read on an object inside the quiet time of a partner's state put on it isn't sent at once (it may be
    the game's own reaction); once that time is over, both PCs end with one state: a change made after the partner's
    state took is sent (the last change wins), anything else is put back to the server's state - and a game that
    keeps putting an object back can't make the two PCs flip it to and fro."""
    objsync.QUIET_AFTER = 0.5
    lot = [(1, "Lamp01", 900, 0, 0, "OnOff:On", "None"), (2, "Fan01", 0, 900, 0, "OnOff:On", "None"),
           (3, "Stove01", 400, 400, 0, "OnOff:Off", "None"), (4, "Microwave01", 100, 200, 90, "OnOff:Off", "None"),
           (5, "Tv01", 500, 0, 50, "OnOff:Off", "None")]
    ana = await PC("Ana6", Sticky(lot, revert={"Microwave01"})).connect(port)
    ben = await PC("Ben6", Sticky(moved(lot), refuse={"Stove01"}, revert={"Microwave01"})).connect(port)
    ana.tick(), ben.tick()

    def item(table):
        return next(i for i in server.shared.values() if i["value"]["t"] == table)

    async def after_quiet(*pcs):
        await asyncio.sleep(objsync.QUIET_AFTER + 0.05)
        for pc in pcs:
            pc.tick()
        await settle(ana, ben)

    async def quiet_round(*pcs):
        """The games run what was put on their objects and are read (twice: their reaction shows), all inside the
        quiet time; then that time is over and they're read again."""
        for pc in pcs:
            pc.tick(), pc.tick()
        await after_quiet(*pcs)

    # (the finding's case) Ana switches the lamp off; it reaches Ben's game (a read shows it), and he switches it
    # back on at once: nothing goes inside the quiet time, his change goes once it's over and reaches Ana
    ana.game.of("Lamp01")["s"] = {"OnOff": "Off"}
    ana.tick()
    await settle(ana, ben)
    ben.tick()                                               # (his game puts it off; the read shows it)
    assert ben.game.look("Lamp01")[0] == ["OnOff:Off"]
    n = server.seq
    ben.game.of("Lamp01")["s"] = {"OnOff": "On"}
    ben.tick()
    await settle(ana, ben)
    assert server.seq == n, "a change inside the quiet time was sent at once"
    await after_quiet(ben)
    ana.tick()
    assert item("Lamp01")["by"] == ben.me_id and item("Lamp01")["value"]["s"] == ["OnOff:On"], item("Lamp01")
    assert ana.game.look("Lamp01")[0] == ben.game.look("Lamp01")[0] == ["OnOff:On"], (ana.game.objs, ben.game.objs)
    assert not ana.obj_pending and not ben.obj_pending
    print("a change made right after the partner's state took is sent once the quiet time is over: ok")

    # Ana switches the fan off, and Ben switches it back on before his game is read again: it never showed off here,
    # so the partner's state is put on it once more (nothing sent) and both PCs end with Ana's
    ana.game.of("Fan01")["s"] = {"OnOff": "Off"}
    ana.tick()
    await settle(ana, ben)
    n = server.seq
    ben.applied += ben.game.run(ben.mail)                    # (his game puts it off...)
    ben.game.of("Fan01")["s"] = {"OnOff": "On"}              # (...and it's back on before the next read)
    ben.on_states_report(ben.game.report())
    await after_quiet(ben)
    ben.tick()
    await settle(ana, ben)
    assert server.seq == n and item("Fan01")["by"] == ana.me_id, item("Fan01")
    assert ana.game.look("Fan01")[0] == ben.game.look("Fan01")[0] == ["OnOff:Off"], (ana.game.objs, ben.game.objs)
    print("a change back before the state even showed: the server's state is put on it again, nothing sent: ok")

    # Ben's game won't take the stove's state at all: put on it once more, then left - never sent (Ana's change
    # isn't undone because Ben's game refuses it)
    ana.game.of("Stove01")["s"] = {"OnOff": "On"}
    ana.tick()
    await settle(ana, ben)
    n = server.seq
    for _ in range(4):
        await after_quiet(ben)
    assert server.seq == n and item("Stove01")["by"] == ana.me_id, item("Stove01")
    assert len(sets_of(ben, "Stove01")) == 2, sets_of(ben, "Stove01")
    assert ana.game.look("Stove01")[0] == ["OnOff:On"]
    print("an object the game won't put in the state: tried once more, then left (nothing sent): ok")

    # the microwave goes back on its own in both games (a Zoi using it in Ana's, nobody in Ben's): each PC settles
    # it once, then it's left - the PCs don't flip it back and forth
    ana.game.of("Microwave01")["s"] = {"OnOff": "On"}      # (her own change sticks; what's put on it later doesn't)
    ana.tick()
    await settle(ana, ben)
    n = server.seq
    for _ in range(8):
        await quiet_round(ana, ben)
    assert 1 <= server.seq - n <= 2, f"the microwave went back and forth {server.seq - n} times"
    m, ana_m = server.seq, len(sets_of(ana, "Microwave01"))
    for _ in range(4):
        await quiet_round(ana, ben)
    assert server.seq == m and len(sets_of(ana, "Microwave01")) == ana_m, "it never stopped"
    print(f"a game that keeps putting an object back: settled {server.seq - n} time(s), then left: ok")

    # a reload: the first read brings the TV to the server's state; the save puts its old state back right after: the
    # server's state is put on it again, the save's isn't sent
    ana.game.of("Tv01")["s"] = {"OnOff": "On"}
    ana.tick()
    await settle(ana, ben)
    ben.tick()
    ben.game = Sticky(moved(lot), revert={"Tv01"})           # (the save as it was: the TV off, and it goes back off)
    ben.mod["instance"] = "i2"
    n = server.seq
    ben.tick()                                               # (first read: Ana's state is put on the TV...)
    ben.tick()                                               # (...it shows on...)
    ben.tick()                                               # (...and the save puts it off again)
    assert ben.game.look("Tv01")[0] == ["OnOff:Off"]
    await after_quiet(ben)
    ben.game.revert.clear()
    ben.tick()
    await settle(ana, ben)
    assert server.seq == n and item("Tv01")["by"] == ana.me_id, item("Tv01")
    assert ben.game.look("Tv01")[0] == ana.game.look("Tv01")[0] == ["OnOff:On"], ben.game.objs
    print("after a reload the save putting an old state back isn't sent; the server's is put on it again: ok")
    objsync.QUIET_AFTER = 15.0
    for p in (ana, ben):
        await p.drop()


def obj_items(server, table):
    return {k: i for k, i in server.shared.items() if i["value"]["t"] == table}


async def placed_again(server, port):
    """The house sync's remove + place (the partner turned or nudged something) and the exact house rebuilding the lot
    put new objects in their default states: nothing is sent from them; they get the server's state, or (nobody ever
    changed that object) the states of the one they replace."""
    lot = [(2, "Tv01", 500, 0, 50, "OnOff:Off", "None"), (4, "Lamp01", 900, 0, 0, "OnOff:On", "None"),
           (5, "Fan01", 0, 900, 0, "OnOff:On", "None")]
    ana, ben = await PC("Ana7", Game(lot)).connect(port), await PC("Ben7", Game(moved(lot))).connect(port)
    ana.tick(), ben.tick()
    ana.game.of("Tv01")["s"], ana.game.of("Tv01")["ch"] = {"OnOff": "On"}, "News"
    ana.tick()
    await settle(ana, ben)
    ben.tick(), ben.tick()
    assert ben.game.look("Tv01") == (["OnOff:On"], "News")
    ben.obj_applied.clear()                                  # (past the quiet time)
    n = server.seq

    def place_again(table, new_id, dx=0):
        o = ben.game.of(table)
        o.update(id=new_id, s={"OnOff": "Off"}, ch="")
        o["p"] = [o["p"][0] + dx, o["p"][1], o["p"][2]]

    # Ana turned her TV: Ben's house sync removes his and places it again at the same spot, off and on no channel
    place_again("Tv01", 100)
    ben.tick()
    await settle(ana, ben)
    assert server.seq == n, "the new TV's default states were sent as Ben's change"
    ben.tick()
    assert ben.game.look("Tv01") == (["OnOff:On"], "News"), ben.game.look("Tv01")
    # Ana moved the lamp 20 cm (nobody ever changed it: the server has nothing for it): placed again, it gets its
    # states back
    place_again("Lamp01", 101, dx=20)
    ben.tick(), ben.tick()
    await settle(ana, ben)
    assert server.seq == n and ben.game.look("Lamp01")[0] == ["OnOff:On"], (server.seq, n, ben.game.objs)
    # removed in one read, placed again in the next
    fan = ben.game.of("Fan01")
    ben.game.objs.remove(fan)
    ben.tick()
    fan.update(id=102, s={"OnOff": "Off"})
    ben.game.objs.append(fan)
    ben.tick(), ben.tick()
    await settle(ana, ben)
    assert server.seq == n and ben.game.look("Fan01")[0] == ["OnOff:On"], ben.game.objs
    print("an object placed again by the house sync (same read, or a read apart) sends nothing and keeps its "
          "states (the server's, or its own): ok")

    # Ana puts her TV on Sports and Ben's game shows it; right after, his house sync places the TV again: what was put
    # on the old one is history - the new one gets the state at once, and nothing is sent when the quiet time is over
    objsync.QUIET_AFTER = 0.3
    ana.obj_applied.clear()
    ana.game.of("Tv01")["ch"] = "Sports"
    ana.tick()
    await settle(ana, ben)
    ben.tick()
    assert ben.game.look("Tv01") == (["OnOff:On"], "Sports"), ben.game.look("Tv01")
    n = server.seq
    place_again("Tv01", 103)
    ben.tick(), ben.tick()
    assert ben.game.look("Tv01") == (["OnOff:On"], "Sports"), ben.game.look("Tv01")
    await asyncio.sleep(objsync.QUIET_AFTER + 0.05)
    ben.tick(), ben.tick()
    await settle(ana, ben)
    assert server.seq == n and ana.game.look("Tv01") == (["OnOff:On"], "Sports"), (server.seq, n, ana.game.objs)
    objsync.QUIET_AFTER = 15.0
    ben.obj_applied.clear()
    print("placed again right after the partner's state was put on the old one: the new one gets it at once, "
          "nothing sent: ok")

    # Ben's game starts again and the exact house rebuilds the lot right after the first read
    ben.mod["instance"] = "i2"
    ben.tick()
    for o in ben.game.objs:
        o.update(id=o["id"] + 1000, s={"OnOff": "Off"}, ch="")
    ben.tick(), ben.tick()
    await settle(ana, ben)
    ben.obj_applied = [(a[0] - 100,) + a[1:] for a in ben.obj_applied]     # (the quiet time over: settled)
    ben.tick()
    await settle(ana, ben)
    assert server.seq == n, "the rebuilt lot's default states were sent"
    assert ben.game.look("Tv01") == (["OnOff:On"], "Sports") and ben.game.look("Lamp01")[0] == ["OnOff:On"] \
        and ben.game.look("Fan01")[0] == ["OnOff:On"], ben.game.objs
    assert ana.game.look("Tv01") == (["OnOff:On"], "Sports")
    # Ben's own change of an object placed again is still sent
    ben.game.of("Lamp01")["s"] = {"OnOff": "Off"}
    ben.tick()
    await settle(ana, ben)
    ana.tick()
    assert server.seq == n + 1 and ana.game.look("Lamp01")[0] == ["OnOff:Off"], (server.seq, ana.game.objs)
    print("the exact house rebuilding the lot after the first read: nothing sent, every object keeps its states; "
          "a change made afterwards is sent: ok")
    for p in (ana, ben):
        await p.drop()


async def moves(server, port):
    """An object moved in build mode (the same object at another spot) takes its state along: the partner's house sync
    places it anew there, in its default states, and the server's state for the new spot is what it gets."""
    lot = [(2, "Tv01", 500, 0, 50, "OnOff:Off", "None"), (4, "Lamp01", 900, 0, 0, "OnOff:Off", "None"),
           (5, "Fan01", 0, 900, 0, "OnOff:On", "None")]
    ana, ben = await PC("Ana8", Game(lot)).connect(port), await PC("Ben8", Game(moved(lot))).connect(port)
    ana.tick(), ben.tick()
    ana.game.of("Tv01")["s"], ana.game.of("Tv01")["ch"] = {"OnOff": "On"}, "News"
    ana.game.of("Lamp01")["s"] = {"OnOff": "Dim"}
    ana.tick()
    await settle(ana, ben)
    ben.tick(), ben.tick()
    ana.obj_applied.clear(), ben.obj_applied.clear()
    old_tv = next(iter(obj_items(server, "Tv01")))

    def place_anew(table, new_id, x, y, z):                  # (Ben's house sync: remove, place, default states)
        ben.game.of(table).update(id=new_id, p=[x, y, z], s={"OnOff": "Off"}, ch="")

    # Ana moves her TV 3 m: its state goes for the new spot, and the old spot's key is removed once that's confirmed
    ana.game.of("Tv01")["p"] = [800, 300, 50]
    ana.tick()
    await settle(ana, ben)
    assert await until(lambda: old_tv not in server.shared), list(server.shared)
    tv = obj_items(server, "Tv01")
    new_tv = f"obj:{SITE}:{objsync.obj_key('Tv01', 800, 300, 50)}"
    assert list(tv) == [new_tv] and tv[new_tv]["by"] == ana.me_id, tv
    assert tv[new_tv]["value"] == {"t": "Tv01", "p": [800.0, 300.0, 50.0], "s": ["OnOff:On"], "ch": "News"}, tv
    n = server.seq
    place_anew("Tv01", 200, 803, 298, 50)
    ben.tick(), ben.tick()
    await settle(ana, ben)
    assert ben.game.look("Tv01") == (["OnOff:On"], "News") and server.seq == n, (ben.game.objs, server.seq, n)
    print("an object moved 3 m: its state is sent for the new spot, the old key removed, the partner's new one "
          "gets it: ok")

    # a short move (30 cm, another 50 cm cell): the server's state already is its own there - nothing sent
    ana.game.of("Lamp01")["p"] = [930, 0, 0]
    ana.tick()
    await settle(ana, ben)
    assert server.seq == n, "a short move sent the state the server has for it already"
    place_anew("Lamp01", 201, 933, -2, 0)
    ben.tick(), ben.tick()
    assert ben.game.look("Lamp01")[0] == ["OnOff:Dim"], ben.game.objs
    # an object nobody ever changed (on in both saves) moved 3 m: sent, so the partner's new one isn't left off
    ana.game.of("Fan01")["p"] = [300, 900, 0]
    ana.tick()
    await settle(ana, ben)
    assert [i["value"]["s"] for i in obj_items(server, "Fan01").values()] == [["OnOff:On"]], obj_items(server, "Fan01")
    place_anew("Fan01", 202, 303, 898, 0)
    ben.tick(), ben.tick()
    await settle(ana, ben)
    assert ben.game.look("Fan01")[0] == ["OnOff:On"], ben.game.objs
    print("a short move sends nothing; a never-changed object moved far is sent: ok")

    # Ben switches the lamp off and Ana moves hers 3 m before her game even put that on it: the state that goes for
    # the new spot is Ben's (not the old one her read still shows), and her lamp ends so too
    objsync.QUIET_AFTER = 0.3
    ben.obj_applied.clear()
    ben.game.of("Lamp01")["s"] = {"OnOff": "Off"}
    ben.tick()
    await settle(ana, ben)
    assert any(name == "set_states" for name, _ in ana.mail.ops)
    ana.game.of("Lamp01")["p"] = [600, 600, 0]
    ana.on_states_report(ana.game.report())                  # (read before her game ran the state put on it)
    await settle(ana, ben)
    ana.tick()                                               # (her game runs it - at the old spot: it misses)
    lamp = obj_items(server, "Lamp01")
    assert [i["value"]["s"] for i in lamp.values()] == [["OnOff:Off"]], lamp
    await asyncio.sleep(objsync.QUIET_AFTER + 0.05)
    ana.tick(), ana.tick()
    await settle(ana, ben)
    assert ana.game.look("Lamp01")[0] == ["OnOff:Off"], ana.game.objs
    assert [i["value"]["s"] for i in obj_items(server, "Lamp01").values()] == [["OnOff:Off"]], obj_items(server,
                                                                                                         "Lamp01")
    place_anew("Lamp01", 203, 603, 598, 0)
    ben.tick(), ben.tick()
    assert ben.game.look("Lamp01")[0] == ["OnOff:Off"], ben.game.objs
    objsync.QUIET_AFTER = 15.0
    print("moved right after the partner's state was put on it: the partner's state goes along: ok")
    for p in (ana, ben):
        await p.drop()


async def kept(server, port):
    """A change made while the server can't be reached isn't forgotten when the world is left before it's back: at the
    next first read it goes again if the game still has the object so and nothing newer came for it meanwhile."""
    proxy = Proxy(port)
    pport = await proxy.start()
    lot = [(2, "Tv01", 500, 0, 50, "OnOff:Off", "None"), (4, "Lamp01", 900, 0, 0, "OnOff:Off", "None"),
           (5, "Fan01", 0, 900, 0, "OnOff:Off", "None")]
    ana, ben = await PC("Ana9", Game(lot)).connect(pport), await PC("Ben9", Game(moved(lot))).connect(port)
    ana.tick(), ben.tick()
    proxy.cut()
    assert await until(lambda: ana.writer is None)
    for t in ("Tv01", "Lamp01", "Fan01"):
        ana.game.of(t)["s"] = {"OnOff": "On"}
    ana.tick()
    assert len(ana.obj_pending) == 3 and not server.shared, (ana.obj_pending, server.shared)
    ben.game.of("Fan01")["s"] = {"OnOff": "Dim"}            # (Ben changes the fan meanwhile)
    ben.tick()
    await settle(ben)
    # Ana leaves the world and loads it again before the connection is back: the save kept the TV and the fan on,
    # not the lamp
    ana.world = "out"
    ana.objsync_reset()
    assert not ana.obj_pending
    ana.world, ana.mod["instance"] = "play", "i2"
    ana.game.of("Lamp01")["s"] = {"OnOff": "Off"}
    ana.tick()
    assert sorted(ana.obj_pending) == sorted(f"obj:{SITE}:{objsync.obj_key(t, *ana.game.of(t)['p'])}"
                                             for t in ("Tv01", "Fan01")), ana.obj_pending
    await ana.connect(pport)
    ana.retry_objects()
    await settle(ana, ben)
    ana.tick(), ben.tick()
    await settle(ana, ben)
    tv, fan = obj_items(server, "Tv01"), obj_items(server, "Fan01")
    assert [(i["by"], i["value"]["s"]) for i in tv.values()] == [(ana.me_id, ["OnOff:On"])], tv
    assert [(i["by"], i["value"]["s"]) for i in fan.values()] == [(ben.me_id, ["OnOff:Dim"])], fan
    assert not obj_items(server, "Lamp01")
    for t, want in (("Tv01", "On"), ("Fan01", "Dim"), ("Lamp01", "Off")):
        assert ana.game.look(t)[0] == ben.game.look(t)[0] == [f"OnOff:{want}"], (t, ana.game.objs, ben.game.objs)
    assert not ana.obj_pending and not ana.obj_kept
    print("a change made offline, the world left before the server is back: sent at the next first read if the "
          "save kept it (the partner's newer change wins, one the save lost is dropped): ok")

    # offline again: Ana switches the TV off, Ben switches it to Broken; Ana leaves the world, gets back online while
    # out, then loads: the server's newer state counts, hers isn't sent
    proxy.cut()
    assert await until(lambda: ana.writer is None)
    ana.game.of("Tv01")["s"] = {"OnOff": "Off"}
    ana.tick()
    ben.obj_applied.clear()
    ben.game.of("Tv01")["s"] = {"OnOff": "Broken"}
    ben.tick()
    await settle(ben)
    ana.world = "out"
    ana.objsync_reset()
    await ana.connect(pport)
    n = server.seq
    ana.world, ana.mod["instance"] = "play", "i3"
    ana.tick(), ana.tick()
    await settle(ana, ben)
    assert server.seq == n and not ana.obj_pending, (server.seq, n, ana.obj_pending)
    assert ana.game.look("Tv01")[0] == ["OnOff:Broken"], ana.game.objs
    print("a kept change the partner's newer one replaced on the server isn't sent: ok")
    for p in (ana, ben):
        await p.drop()
    proxy.srv.close()


class FakeClock:
    """objsync's clock (it only calls time.time()), moved by hand: quiet times and settle spans without waiting."""

    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t


def newest(server, table):
    return max(obj_items(server, table).values(), key=lambda i: i["v"])


async def settle_again(server, port):
    """Ben switches the lamp on; Ana's game takes it and she switches it off inside the quiet time: sent once that
    time is over. Ben switches it on again within settle_span() and Ana off again: the once-a-span guard (there for a
    game that keeps putting an object back) used to leave it here, Ana's change never sent nor undone - her lamp off,
    Ben's and the server's on, for good. A settle's value says it's one; only a settle answering it is left."""
    real, clock = objsync.time, FakeClock()
    objsync.time = clock
    try:
        ana = await PC("AnaR1", Game([(4, "Lamp01", 900, 0, 0, "OnOff:Off", "None")])).connect(port)
        ben = await PC("BenR1", Game([(9, "Lamp01", 903, -2, 0, "OnOff:Off", "None")])).connect(port)

        async def tick(n=1):
            for _ in range(n):
                clock.t += objsync.READ_EVERY
                for pc in (ana, ben):
                    pc.retry_objects()
                    pc.tick()
                await settle(ana, ben)

        await tick()
        settled_at = None
        for round_ in (1, 2):
            ben.game.of("Lamp01")["s"]["OnOff"] = "On"
            await tick(2)                                    # (his change goes; Ana's game takes it, a read shows it)
            assert ana.game.look("Lamp01")[0] == ["OnOff:On"], (round_, ana.game.objs)
            ana.game.of("Lamp01")["s"]["OnOff"] = "Off"      # (inside the quiet time: not sent yet)
            await tick()
            assert ben.game.look("Lamp01")[0] == ["OnOff:On"], "a change inside the quiet time was sent at once"
            await tick(9)                                    # (the quiet time is over: settled)
            item = newest(server, "Lamp01")
            assert ana.game.look("Lamp01")[0] == ben.game.look("Lamp01")[0] == ["OnOff:Off"], \
                (round_, "the two games stayed apart", ana.game.objs, ben.game.objs, item)
            assert item["by"] == ana.me_id and item["value"]["s"] == ["OnOff:Off"], (round_, item)
            if settled_at is None:
                settled_at = clock.t
        assert clock.t - settled_at < objsync.settle_span(), "the second round wasn't within the settle span"
        assert newest(server, "Lamp01")["value"].get("settle") is True       # (what a settle sends says so)
        n = server.seq
        await tick(30)                                       # (nobody touches it: nothing goes to and fro)
        assert server.seq == n and ana.game.look("Lamp01")[0] == ben.game.look("Lamp01")[0] == ["OnOff:Off"]
        print("a partner's change, then mine inside its quiet time, twice within the settle span: mine reaches the "
              "server and the partner's game both times: ok")
        for p in (ana, ben):
            await p.drop()
    finally:
        objsync.time = real


async def before_joining(port, name):
    """Ben switched the TV and the lamp on long ago (Ana's game took it); Ana's program then starts again (the same
    player and save) while the server can't be reached: nothing of it is known in this run. -> (Ana's new PC, Ben)"""
    lot = [(2, "Tv01", 500, 0, 50, "OnOff:Off", "None"), (4, "Lamp01", 900, 0, 0, "OnOff:Off", "None")]
    ana = await PC("Ana" + name, Game(lot)).connect(port)
    ben = await PC("Ben" + name, Game(moved(lot))).connect(port)
    ana.tick(), ben.tick()
    ben.game.of("Tv01")["s"] = {"OnOff": "On"}
    ben.game.of("Lamp01")["s"] = {"OnOff": "On"}
    ben.tick()
    await settle(ana, ben)
    ana.tick()
    assert ana.game.look("Tv01")[0] == ana.game.look("Lamp01")[0] == ["OnOff:On"], ana.game.objs
    await ana.drop()
    again = PC("Ana" + name, ana.game)
    again.cfg = dict(ana.cfg)
    again.tick()                                             # (the first read: the starting point)
    return again, ben


async def blind_start(server, port):
    """A change made before the server was ever reached in this run used to go with base 0 (as if the server had
    nothing): the server refused it for Ben's old change and Ana's TV was put back on. It wins when the connection
    comes."""
    again, ben = await before_joining(port, "R2")
    again.game.of("Tv01")["s"] = {"OnOff": "Off"}
    again.tick()
    assert len(again.obj_pending) == 1, again.obj_pending
    await again.connect(port)
    again.retry_objects()
    await settle(again, ben)
    again.tick(), ben.tick()
    await settle(again, ben)
    tv = newest(server, "Tv01")
    assert tv["by"] == again.me_id and tv["value"]["s"] == ["OnOff:Off"], ("the server kept Ben's old change", tv)
    assert again.game.look("Tv01")[0] == ben.game.look("Tv01")[0] == ["OnOff:Off"], (again.game.objs, ben.game.objs)
    assert not again.obj_pending
    print("a change made before the server was ever reached in this run wins when the connection comes: ok")
    for p in (again, ben):
        await p.drop()


async def blind_kept(server, port):
    """The same, and the world is left (and loaded again) before the connection comes: the kept change used to be
    dropped at the next first read (its scope had no epoch yet), Ben's old one put on the lamp. It wins too."""
    third, ben = await before_joining(port, "R3")
    third.game.of("Lamp01")["s"] = {"OnOff": "Off"}
    third.tick()
    third.world = "out"
    third.objsync_reset()
    assert list(third.obj_kept) and not third.obj_pending
    await third.connect(port)
    third.world, third.mod["instance"] = "play", "i2"
    third.tick()                                             # (the first read of the reloaded world)
    third.retry_objects()
    await settle(third, ben)
    third.tick(), ben.tick()
    await settle(third, ben)
    lamp = newest(server, "Lamp01")
    assert lamp["by"] == third.me_id and lamp["value"]["s"] == ["OnOff:Off"], ("the kept change was dropped", lamp)
    assert third.game.look("Lamp01")[0] == ben.game.look("Lamp01")[0] == ["OnOff:Off"], (third.game.objs,
                                                                                          ben.game.objs)
    assert not third.obj_pending and not third.obj_kept
    print("... and one kept through leaving the world before the connection came wins too: ok")
    for p in (third, ben):
        await p.drop()


async def main():
    logging.basicConfig(level=logging.WARNING)
    for n, case in enumerate((basics, reload, lambda s, p: crossing(s, p, "ana"), lambda s, p: crossing(s, p, "ben"),
                              lost, aliases, lambda s, p: near(s, p, False), lambda s, p: near(s, p, True), quiet,
                              placed_again, moves, kept, settle_again, blind_start, blind_kept)):
        server = mp.Server(mp.Store(TMP / f"server{n}"))         # (each case on its own server: a clean lot)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        await case(server, srv.sockets[0].getsockname()[1])
        srv.close()
    assert objsync.parse_states("x")[0] is None and objsync.parse_states("5;bad")[1] == {}
    print("all object-state tests passed")


if __name__ == "__main__":
    asyncio.run(main())
