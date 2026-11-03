"""The home's object states (client/objsync.py), two PCs through a stand-in for the server's shared state:
- an object switched on in one game reaches the other, which puts it in that state through the controller
- the change applied there isn't sent back (nor what its game does right after)
- the first read of the lot is only the starting point; a later joiner gets the server's states applied once
- Slot groups (who sits where) are ignored

    python tools/test_objsync.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import objsync  # noqa: E402
import shared  # noqa: E402


class Server:
    def __init__(self):
        self.shared, self.v, self.pcs = {}, 0, []

    def set(self, pc, key, value):
        self.v += 1
        item = {"key": key, "value": value, "v": self.v, "by": pc.me_id, "by_name": pc.name}
        self.shared[key] = item
        for p in self.pcs:
            p.on_shared_msg({"key": key, "item": dict(item), "v": self.v})


class Mail:
    def __init__(self):
        self.ops = []

    def op(self, name, **f):
        self.ops.append((name, f))


class Tele:
    def rec(self, k, **d):
        pass


class PC(shared.SharedState, objsync.ObjSync):
    def __init__(self, name, server):
        self.name, self.me_id, self.server = name, name.lower(), server
        self.cfg, self.mail, self.tele = {}, Mail(), Tele()
        self.writer, self.world, self.leader = True, "play", None
        self.shared_init()
        self.objsync_init()
        server.pcs.append(self)

    def save_cfg(self):
        pass

    async def share(self, key, value, base=None):
        self.server.set(self, key, value)
        return True

    # stand-ins for the other mixins shared.py dispatches to
    def on_shared_house_live(self, *a): pass
    def on_shared_house_exact(self, *a): pass


def report(objs, site=1038):
    return ";".join([str(site)] + [f"{i}|{t}|{x}|{y}|{z}|{s}|{ch}" for i, t, x, y, z, s, ch in objs])


LOT = [(1, "Microwave01_v02", 100, 200, 90, "", "None"), (2, "Tv01", 500, 0, 50, "", "Off"),
       (3, "Chair03_v05", 10, 10, 0, "SlotOccupy:Occupy", "None")]


async def main():
    logging.basicConfig(level=logging.WARNING)
    srv = Server()
    ana, ben = PC("Ana", srv), PC("Ben", srv)
    ana.on_states_report(report(LOT))
    ben.on_states_report(report([(9, t, x + 3, y - 2, z, s, ch) for _, t, x, y, z, s, ch in LOT]))   # (ids differ)
    assert not srv.shared and not ana.mail.ops and not ben.mail.ops, "the first read is only the starting point"

    # Ana switches the microwave on and the TV to a channel; her Zoi sits on the chair (not synced)
    lot = [(1, "Microwave01_v02", 100, 200, 90, "OnOff:On", "None"), (2, "Tv01", 500, 0, 50, "", "News"),
           (3, "Chair03_v05", 10, 10, 0, "", "None")]
    ana.on_states_report(report(lot))
    await asyncio.sleep(0)
    assert sorted(i["value"]["t"] for i in srv.shared.values()) == ["Microwave01_v02", "Tv01"], srv.shared
    sets = [f for n, f in ben.mail.ops if n == "set_states"]
    assert len(sets) == 2 and not ana.mail.ops
    mw = next(f for f in sets if f["table"] == "Microwave01_v02")
    assert mw["states"] == ["OnOff:On"] and mw["site"] == 1038 and (mw["x"], mw["y"]) == (100, 200)
    assert next(f for f in sets if f["table"] == "Tv01")["channel"] == "News"
    print("an object changed in one game is put in that state in the other: ok")

    # Ben's game now reads them so (and the microwave flickers to something else right after): nothing goes back
    n = srv.v
    ben.on_states_report(report([(9, "Microwave01_v02", 103, 198, 90, "OnOff:On", "None"),
                                 (10, "Tv01", 503, -2, 50, "", "News")]))
    ben.on_states_report(report([(9, "Microwave01_v02", 103, 198, 90, "Normal:Normal", "None"),
                                 (10, "Tv01", 503, -2, 50, "", "News")]))
    await asyncio.sleep(0)
    assert srv.v == n, "the applied change was sent back"
    print("an applied change isn't sent back: ok")

    # later, Ben switches the microwave off himself: it reaches Ana
    for a in ben.obj_applied:
        ben.obj_applied[ben.obj_applied.index(a)] = (a[0] - 100,) + a[1:]
    ben.on_states_report(report([(9, "Microwave01_v02", 103, 198, 90, "OnOff:On", "None"),
                                 (10, "Tv01", 503, -2, 50, "", "News")]))
    ben.on_states_report(report([(9, "Microwave01_v02", 103, 198, 90, "Normal:Normal", "None"),
                                 (10, "Tv01", 503, -2, 50, "", "News")]))
    await asyncio.sleep(0)
    off = [f for n, f in ana.mail.ops if n == "set_states"]
    assert off and off[-1]["table"] == "Microwave01_v02" and off[-1]["states"] == ["Normal:Normal"], ana.mail.ops
    print("the partner's own change comes back the other way: ok")

    # a third PC joining: the server's states are applied once its lot is first read, and not again
    cat = PC("Cat", srv)
    cat.on_shared_snapshot({k: dict(v) for k, v in srv.shared.items()}, srv.v)
    assert not cat.mail.ops
    cat.on_states_report(report(LOT))
    got = sorted(f["table"] for n, f in cat.mail.ops if n == "set_states")
    assert got == ["Microwave01_v02", "Tv01"], got
    cat.mail.ops.clear()
    cat.on_shared_snapshot({k: dict(v) for k, v in srv.shared.items()}, srv.v)
    assert not cat.mail.ops
    print("a later joiner gets the server's states once: ok")

    assert objsync.parse_states("x")[0] is None and objsync.parse_states("5;bad")[1] == {}
    print("all object-state tests passed")


asyncio.run(main())
