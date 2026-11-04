"""The home's dust, wear and colours on the server (client/detailsync.py): the host's dust growth counts, a cleaning
from either PC goes up, what grows by itself on the other PC is put back; colours go up when changed and are put on the
other game part by part; at a world's first read the game is brought to the server.

    python tools/test_detailsync.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["APPDATA"] = tempfile.mkdtemp()                 # (never the real config)
os.environ["INZOI_MP_DOCUMENTS"] = tempfile.mkdtemp()      # (never the real game's Mods folder)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import detailsync  # noqa: E402
from detailsync import detail_key as obj_key  # noqa: E402


class Mail:
    def __init__(self):
        self.ops = []

    def op(self, name, **kw):
        self.ops.append((name, kw))


class Game:
    def __init__(self):
        self.calls, self.looks = [], {}

    def call(self, tool, **kw):
        self.calls.append((tool, kw))
        if tool == "build.get_customize_info":
            return {"entity_id": int(kw["entity_id"]), "parts": self.looks.get(kw["entity_id"], [])}
        return {}


class D(detailsync.DetailSync):
    def __init__(self, me, leader):
        self.me_id, self.leader = me, leader
        self.shared, self.shared_inflight = {}, {}
        self.mail, self.game = Mail(), Game()
        self.world, self.mod = "play", {"state": "running", "instance": "i1"}
        self.sent = []
        self.detail_init()

    def mp_active(self):
        return True

    def mod_instance(self):
        return self.mod["instance"]

    async def home_site_cached(self):
        return 1038

    async def share(self, key, value, base=None, wait=False):
        self.sent.append((key, value, base))
        return True

    def shared_task(self, coro):
        return asyncio.get_event_loop().create_task(coro)


def report(objs):
    """[(entity, table, x, y, z, dust, grade)] -> the controller's "dirt" text."""
    return "1038;" + ";".join(f"{e}|{t}|{x}|{y}|{z}|{d}|{w}" for e, t, x, y, z, d, w in objs)


BATH, TILE, LAMP = ("15778", "Bath01", 100, 200, 0), ("15709", "Tile01", 300, 200, 0), ("15767", "Lighting04", 50, 50, 250)
KB, KT, KL = obj_key("Bath01", 100, 200, 0), obj_key("Tile01", 300, 200, 0), obj_key("Lighting04", 50, 50, 250)


def server_item(value, by, v=7):
    return {"key": "dirt:1038", "value": value, "v": v, "by": by}


async def dirt_tests():
    # the host, the server has nothing yet: its home goes up whole
    host = D("arthur", "arthur")
    host.on_dirt_report(report([(*BATH, 150, 0), (*TILE, 1000, 2), (*LAMP, -1, -1)]))
    await asyncio.sleep(0)
    assert len(host.sent) == 1 and host.sent[0][0] == "dirt:1038" and host.sent[0][2] is None, host.sent
    doc = host.sent[0][1]["o"]
    assert doc[KB][4:] == [150, 0] and doc[KT][4:] == [1000, 2] and KL not in doc, doc
    print("the host's dust and wear go up when the server has none: ok")

    # Chey's game, the host's on the server: her first read is brought to it (nothing sent)
    chey = D("chey", "arthur")
    chey.shared["dirt:1038"] = server_item({"o": doc}, "arthur")
    chey.on_dirt_report(report([(*BATH, 975, 2), (*TILE, 1000, 2)]))
    await asyncio.sleep(0)
    assert not chey.sent
    sets = [kw for op, kw in chey.mail.ops if op == "set_dirt"]
    assert len(sets) == 1 and sets[0]["items"] == [{"table": "Bath01", "x": 100, "y": 200, "z": 0, "d": 150, "w": 0}], sets
    print("the other PC's first read: brought to the server's dust and wear: ok")

    # her game grows dust by itself: put back; she cleans the tiles: it goes up (on the version it was made on)
    chey.mail.ops.clear()
    chey.on_dirt_report(report([(*BATH, 300, 0), (*TILE, 0, 2)]))
    await asyncio.sleep(0)
    sets = [kw for op, kw in chey.mail.ops if op == "set_dirt"]
    assert sets and sets[0]["items"][0]["table"] == "Bath01" and sets[0]["items"][0]["d"] == 150, sets
    assert len(chey.sent) == 1 and chey.sent[0][2] == 7, chey.sent
    assert chey.sent[0][1]["o"][KT][4:] == [0, 2] and chey.sent[0][1]["o"][KB][4:] == [150, 0]
    print("her own dust growth is put back; her cleaning goes up: ok")

    # the host gets her cleaning from the server: put on his tiles; his own later growth still goes up
    host.shared["dirt:1038"] = server_item(chey.sent[0][1], "chey", v=9)
    host.sent.clear()
    host.mail.ops.clear()
    host.on_shared_detail("dirt:1038", host.shared["dirt:1038"], snapshot=False)
    await asyncio.sleep(0)
    sets = [kw for op, kw in host.mail.ops if op == "set_dirt"]
    assert sets and {"table": "Tile01", "x": 300, "y": 200, "z": 0, "d": 0, "w": 2} in sets[0]["items"], sets
    assert not host.sent
    host.on_dirt_report(report([(*BATH, 225, 0), (*TILE, 0, 2)]))        # (his bath got dustier: his counts)
    await asyncio.sleep(0)
    assert len(host.sent) == 1 and host.sent[0][1]["o"][KB][4] == 225 and host.sent[0][2] == 9, host.sent
    print("her cleaning reaches the host's game; the host's growth still goes up: ok")

    # her cleaning answered by an older value of the host's (his reading crossed it): sent again
    chey.sent.clear()
    chey.mail.ops.clear()
    chey.on_dirt_report(report([(*BATH, 0, 0), (*TILE, 0, 2)]))           # (she cleans the bath)
    await asyncio.sleep(0)
    assert chey.sent and chey.sent[-1][1]["o"][KB][4] == 0
    older = {"o": dict(chey.sent[-1][1]["o"])}
    older["o"][KB] = ["Bath01", 100, 200, 0, 225, 0]
    chey.shared["dirt:1038"] = server_item(older, "arthur", v=11)
    chey.sent.clear()
    chey.on_shared_detail("dirt:1038", chey.shared["dirt:1038"], snapshot=False)
    await asyncio.sleep(0)
    assert chey.sent and chey.sent[-1][1]["o"][KB][4] == 0 and chey.sent[-1][2] == 11, chey.sent
    assert not [kw for op, kw in chey.mail.ops if op == "set_dirt" and kw["items"][0]["d"] == 225]
    print("a cleaning answered with the host's older value goes again: ok")


async def look_tests():
    assert detailsync.normalize_look({"parts": [
        {"index": 0, "parts_row_key": "RiceCooker01", "color": {"r": 0.0, "g": 0.5, "b": 1.0, "a": 1.0}},
        {"index": 1, "parts_row_key": "RiceCooker01"},
        {"index": 2, "parts_row_key": "X", "emissive_multi": 0.5512, "material_group": 0, "material_index": 2}]}) == {
        "0": {"color": [0.0, 0.5, 1.0, 1.0]}, "2": {"emissive_multi": 0.551, "material_group": 0, "material_index": 2}}
    print("colours read the same in both games (linear, rounded, only what can be put back): ok")

    host = D("arthur", "arthur")
    host.on_dirt_report(report([(*BATH, 150, 0), (*LAMP, -1, -1)]))
    host.game.looks = {"15767": [{"index": 1, "parts_row_key": "Lighting04", "color": {"r": 1, "g": 0, "b": 0, "a": 1}}]}
    await asyncio.sleep(0)                             # (the dust read's own send first)
    host.sent.clear()
    await host.look_scan(1038)
    await asyncio.sleep(0)
    keys = {k for k, _, _ in host.sent}
    assert keys == {f"look:1038:{KL}", f"look:1038:{KB}"}, host.sent
    lamp = next(v for k, v, _ in host.sent if k.endswith(KL))
    assert lamp == {"t": "Lighting04", "p": [50, 50, 250], "m": {"1": {"color": [1.0, 0.0, 0.0, 1.0]}}}, lamp
    print("the host's colours go up when the server has none: ok")

    # Chey's game: the server's lamp is red, hers blue; her bath has a colour the server doesn't: reset
    chey = D("chey", "arthur")
    chey.on_dirt_report(report([(*BATH, 150, 0), (*LAMP, -1, -1)]))
    chey.shared[f"look:1038:{KL}"] = {"key": f"look:1038:{KL}", "value": lamp, "v": 3, "by": "arthur"}
    chey.shared[f"look:1038:{KB}"] = {"key": f"look:1038:{KB}", "value": {"t": "Bath01", "p": [100, 200, 0], "m": {}},
                                      "v": 4, "by": "arthur"}
    chey.game.looks = {"15767": [{"index": 1, "color": {"r": 0, "g": 0, "b": 1, "a": 1}}],
                       "15778": [{"index": 0, "color": {"r": 0, "g": 1, "b": 0, "a": 1}}]}
    await chey.look_scan(1038)
    calls = [(t, kw) for t, kw in chey.game.calls if t != "build.get_customize_info"]
    assert ("build.set_material", {"entity_id": "15767", "part_index": 1, "Color": "#FF0000"}) in calls, calls
    lin = [0.1119, 0.0452, 0.0262, 1.0]                # (#5E3C2D as the game reads it: linear)
    assert detailsync.srgb_hex(lin) == "#5E3C2D", detailsync.srgb_hex(lin)                # (read linear, put sRGB)
    assert ("build.reset_material", {"entity_id": "15778", "part_index": 0}) in calls, calls
    assert not chey.sent
    print("the other PC's first scan: brought to the server's colours, part by part: ok")

    # the game's rendering of what was put isn't a change; a real change later goes up on its version
    chey.game.looks["15767"] = [{"index": 1, "color": {"r": 1, "g": 0, "b": 0, "a": 1}, "roughness": 0.5}]
    chey.game.looks["15778"] = []
    await chey.look_scan(1038)
    assert not chey.sent, chey.sent
    chey.game.looks["15767"] = [{"index": 1, "color": {"r": 1, "g": 1, "b": 0, "a": 1}, "roughness": 0.5}]
    await chey.look_scan(1038)
    await asyncio.sleep(0)
    assert len(chey.sent) == 1 and chey.sent[0][0] == f"look:1038:{KL}" and chey.sent[0][2] == 3, chey.sent
    assert chey.sent[0][1]["m"] == {"1": {"color": [1.0, 1.0, 0.0, 1.0], "roughness": 0.5}}
    print("a colour changed in her game goes up; the game's rendering of a put look doesn't: ok")

    # the host gets it: put on his lamp at once
    item = {"key": f"look:1038:{KL}", "value": chey.sent[0][1], "v": 5, "by": "chey"}
    host.shared[item["key"]] = item
    host.look_site, host.look_inst = 1038, "i1"
    host.game.calls.clear()
    host.on_shared_detail(item["key"], item, snapshot=False)
    for _ in range(20):
        await asyncio.sleep(0.01)
    sets = [kw for t, kw in host.game.calls if t == "build.set_material"]
    assert sets == [{"entity_id": "15767", "part_index": 1, "Color": "#FFFF00", "Roughness": 0.5}], host.game.calls
    print("a partner's colour change is put on my object at once: ok")

    # two objects of a kind 45 cm apart (pieces of lawn) are two keys, not one
    assert obj_key("Landscape01_v03", 73479, 25224, 68) != obj_key("Landscape01_v03", 73522, 25205, 68)
    # the host drops look keys of objects that aren't in the home (an older program's keys)
    host.shared["look:1038:0ld0ld0ld0ld"] = {"key": "look:1038:0ld0ld0ld0ld", "value": {}, "v": 2, "by": "arthur"}
    host.sent.clear()
    await host.look_scan(1038)
    await asyncio.sleep(0)
    assert ("look:1038:0ld0ld0ld0ld", None, None) in host.sent, host.sent
    print("objects 45 cm apart are two; stale keys are dropped by the host: ok")


async def main():
    await dirt_tests()
    await look_tests()
    print("detailsync tests passed")


asyncio.run(main())
