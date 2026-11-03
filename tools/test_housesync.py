"""The family home kept the same in both saves (client/housesync.py), with two stand-in games and a local server:
- comparing: the same kind within 10 cm / 5 degrees is the same object; the rest is new or gone
- Ana rearranges the home in build mode and leaves it: Ben is asked ("2 new, 1 removed"), says yes, and his game
  enters build mode, removes and places what differs, and leaves it; that doesn't send the house back
- a house that isn't Ben's home is ignored; "always yes" applies it without asking
- walls: one long wall and the two short ones that make it up are the same; Ana builds a room and knocks a wall
  down, raises the ceiling: Ben's game deletes and builds those walls (before placing doors) and sets the height

    python tools/test_housesync.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["APPDATA"] = str(TMP / "appdata")
os.environ["INZOI_MP_DOCUMENTS"] = str(TMP / "Documents")
os.environ["INZOI_MP_GAME_URL"] = "http://127.0.0.1:9/mcp"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import companion  # noqa: E402
import housesync  # noqa: E402


def wall(x0, y0, x1, y1, floor=0):
    return {"floor_level": floor, "left": {"x": x0, "y": y0}, "right": {"x": x1, "y": y1}}


def obj(eid, table, x, y, yaw=0.0, floor=0, z=7.5):
    return {"entity_id": eid, "table_id": table, "floor_level": floor, "location": {"x": x, "y": y, "z": z}, "yaw": yaw}


class StandIn:
    """A game: a home lot (site 1038) with objects, build mode, and the calls the house sync makes."""

    def __init__(self, objects, home=1038, walls=(), height=300.0):
        self.objects, self.home, self.phase, self.calls, self.next_id = list(objects), home, "none", [], 900
        self.walls, self.height = list(walls), height
        self.where = home                               # the lot my Zoi is on

    def call(self, tool, **kw):
        self.calls.append((tool, kw))
        if tool == "build.get_mode_state":
            return {"in_build_mode": self.phase != "none", "phase": self.phase, "site_entity_id": 1038,
                    "site_id": "RedCity_Residence_Free03"}
        if tool == "build.enter_mode":
            self.phase = "ready"
            return {}
        if tool == "build.exit_mode":
            self.phase = "none"
            return {}
        if tool == "build.scan_objects":
            off = kw.get("Offset", 0)
            return {"objects": self.objects[off:off + kw.get("Limit", 500)]}
        if tool == "build.remove_object":
            self.objects = [o for o in self.objects if str(o["entity_id"]) != kw["entity_id"]]
            return {}
        if tool == "building.place_object":
            self.next_id += 1
            self.objects.append(obj(self.next_id, kw["object_table_id"], kw["X"], kw["Y"], kw["Yaw"], kw["floor_level"],
                                    kw["Z"]))
            return {}
        if tool == "entity.reflect":
            return {"worldTransform": {"translation": {"x": 0, "y": 0, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0,
                                                                                             "w": 1}},
                    "locatedCityName": "Dowon", "locatedSiteId": {"value": self.where}}
        if tool == "build.get_site_info":
            return {"site_id": "RedCity_Residence_Free03"}
        if tool == "build.scan_walls":
            return {"_text": "Truncated: (as the game answers)" + chr(10) + json.dumps({"walls": self.walls})}
        if tool == "build.scan_floors":
            return {"floors": [{"floor_level": 0, "heights": [{"wall_height": self.height, "floor_height": 0.0,
                                                               "base_height": 0.0}] if self.walls else []}]}
        if tool == "building.construct_wall":
            w = wall(kw["start_x"], kw["start_y"], kw["end_x"], kw["end_y"], kw["floor_level"])
            if kw.get("is_delete"):
                self.walls = [o for o in self.walls if housesync.wall_key(o) != housesync.wall_key(w)]
            else:
                self.walls.append(w)
            return {}
        if tool == "building.set_storey_wall_height":
            self.height = kw["target_height"]
            return {}
        if tool == "household.get_members":
            return {"household_id": 1, "members": []}
        if tool == "household.list":
            return {"total": 1, "households": [{"household_id": 1, "residence_site_id": self.home, "member_count": 2}]}
        return {}


async def until(check, timeout=10):
    for _ in range(int(timeout / 0.05)):
        if check():
            return True
        await asyncio.sleep(0.05)
    return False


def player(name, port, game):
    comp = companion.Companion({"key": name * 10, "name": name, "server": "127.0.0.1", "port": port, "password": ""})
    comp.mod["state"] = "running"
    comp.world, comp.my_city, comp.my_entity = "play", "Dowon", 10
    comp.game.call = game.call
    comp.save_cfg = lambda: None
    comp.lot_file = TMP / f"{name}-lot.json"
    comp.preset_file = TMP / f"{name}-preset.json"
    comp.cfg["exact_house"] = False
    return comp


async def main():
    # comparing
    a = [housesync.object_key(obj(1, "Sofa01", 100, 100)), housesync.object_key(obj(2, "Lamp01", 300, 50, 90))]
    b = [dict(housesync.object_key(obj(7, "Sofa01", 104, 97)), entity_id=7),
         dict(housesync.object_key(obj(8, "Lamp01", 300, 50, 180)), entity_id=8),
         dict(housesync.object_key(obj(9, "Plant01", 10, 10)), entity_id=9)]
    place, remove = housesync.diff(a, b)
    assert [o["table_id"] for o in place] == ["Lamp01"] and sorted(o["entity_id"] for o in remove) == [8, 9]
    print("compare: same kind within 10 cm / 5 degrees is the same object: ok")
    k = housesync.wall_key
    long_ = [k(wall(12.5, 12.5, 512.5, 12.5))]
    halves = [k(wall(512.5, 12.5, 212.5, 12.5)), k(wall(12.5, 12.5, 212.5, 12.5))]
    assert housesync.wall_diff(long_, halves) == ([], []) and housesync.wall_diff(halves, long_) == ([], [])
    build, delete = housesync.wall_diff([k(wall(12.5, 12.5, 312.5, 12.5))], long_)
    assert build == [k(wall(12.5, 12.5, 312.5, 12.5))] and delete == long_
    assert housesync.wall_diff(long_, [k(wall(12.5, 12.5, 512.5, 12.5, floor=1))])[0] == long_
    print("compare walls: the same line however it's split; a shorter wall is rebuilt; floors apart: ok")

    import inzoi_mp_server as mp
    server = mp.Server(mp.Store(TMP / "server"))
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
    port = srv.sockets[0].getsockname()[1]
    home = [obj(1, "Sofa01", 100, 100), obj(2, "Table03", 500, 500, 90), obj(3, "TV03", 800, 200)]
    ga, gb = StandIn(home), StandIn(home)
    ana, ben = player("Ana", port, ga), player("Ben", port, gb)
    tasks = [asyncio.create_task(ana.session()), asyncio.create_task(ben.session())]
    assert await until(lambda: ana.me_id and ben.me_id and ben.me_id in ana.remotes and ana.me_id in ben.remotes)

    # Ana in build mode: moves the TV, adds a plant and a chair, takes the sofa out
    ga.phase = "ready"
    await ana.house_tick()
    ga.objects = [obj(2, "Table03", 500, 500, 90), obj(3, "TV03", 800, 200), obj(4, "Plant01", 50, 60),
                  obj(5, "Chair02", 520, 470, 180)]
    ana.build_site["at"] = 0
    await ana.house_tick()
    assert len(ana.build_site["objects"]) == 4
    ga.phase = "none"
    await ana.house_tick()                                   # left build mode: sent
    # the server's version reaches Ben and his game is brought to it, without asking (it's the truth), once
    assert await until(lambda: sorted(o["table_id"] for o in gb.objects) == ["Chair02", "Plant01", "TV03", "Table03"])
    item = ana.shared["house:1038:live"]
    assert item["by"] == ana.me_id and ben.shared["house:1038:live"]["v"] == item["v"]
    assert any("changed the house: 2 new, 1 removed" in c["text"] for c in ben.chat)
    assert await until(lambda: ben.seen("house:1038:live") == item["v"] and ana.seen("house:1038:live") == item["v"])
    tools = [c[0] for c in gb.calls]
    assert tools.index("build.enter_mode") < tools.index("build.remove_object") < tools.index("building.place_object") \
        < tools.index("build.exit_mode")
    # Ben leaving build mode after applying doesn't send the house back: the server's stays Ana's version
    await ben.house_tick()
    await asyncio.sleep(0.5)
    assert ana.shared["house:1038:live"]["v"] == item["v"] and ana.shared["house:1038:live"]["by"] == ana.me_id
    n = len(gb.calls)
    ben.shared_changed("house:1038:live", ben.shared["house:1038:live"], snapshot=True)    # (a restart: seen, kept)
    await asyncio.sleep(0.3)
    assert len(gb.calls) == n
    print("Ana's change goes through the server: Ben's game brought to it once, not sent back: ok")

    # Ben's house drifted (an object gone in his save): "get my partner's house" brings it back to the server's version
    gb.objects = [o for o in gb.objects if o["table_id"] != "Chair02"]
    assert (await ben.request_house(ben.remotes[ana.me_id]))["ok"]
    assert await until(lambda: any(o["table_id"] == "Chair02" for o in gb.objects))
    print("a drifted house is brought back to the server's version: ok")

    # Ben changes the house while the server can't be reached: kept as changed, not overwritten by the server's older
    # version, pushed as soon as it can be
    writer = ben.writer
    ben.writer = None
    lot = dict(ben.scan_lot(), site=1038, site_id="X")
    lot["objects"].append(housesync.object_key(obj(0, "Rug07", 300, 300)) | {"entity_id": 77})
    await ben.publish_house(lot)
    assert ben.dirty(1038, "live")
    ben.on_shared_house_live("house:1038:live", dict(item, v=item["v"] + 50), snapshot=True)
    await asyncio.sleep(0.3)
    assert not [q for q in ben.requests.values() if q.get("action") == "house"], "the older server version won"
    ben.writer = writer
    gb.objects.append(obj(77, "Rug07", 300, 300))
    gb.where = 1038
    await ben.push_dirty_house()
    assert await until(lambda: ana.shared.get("house:1038:live", {}).get("by") == ben.me_id)
    assert not ben.dirty(1038, "live")
    assert await until(lambda: any(o["table_id"] == "Rug07" for o in ga.objects))
    print("a change made without the server is kept, pushed when it can be, and reaches the partner: ok")

    # another lot (not Ben's home): ignored; "always yes": applied without asking
    gb.home = 2000
    ben.on_house(ana.me_id, {"site": 1038, "site_id": "X", "objects": [housesync.object_key(obj(0, "Lamp09", 1, 1))]})
    await asyncio.sleep(0.5)
    assert not [q for q in ben.requests.values() if q.get("action") == "house"]
    gb.home = 1038
    ben.cfg["always_yes"] = ["house"]
    ben.on_house(ana.me_id, {"site": 1038, "site_id": "X", "objects": [housesync.object_key(o) for o in gb.objects] +
                             [housesync.object_key(obj(0, "Lamp09", 1, 1))]})
    assert await until(lambda: any(o["table_id"] == "Lamp09" for o in gb.objects))
    print("another lot ignored; always yes applies by itself: ok")

    # walls: Ana builds a room (4 walls), knocks the old wall down, puts a door in, raises the storey to 3.9 m
    ben.cfg["always_yes"] = []
    room = [wall(1012.5, 1012.5, 1312.5, 1012.5), wall(1312.5, 1012.5, 1312.5, 1312.5),
            wall(1312.5, 1312.5, 1012.5, 1312.5), wall(1012.5, 1312.5, 1012.5, 1012.5)]
    gb.walls = [wall(12.5, 12.5, 512.5, 12.5)] + room[:1]
    gb.calls.clear()
    lot = {"site": 1038, "site_id": "X", "objects": [housesync.object_key(o) for o in gb.objects] +
           [housesync.object_key(obj(0, "Door01", 1100, 1012.5))],
           "walls": [housesync.wall_key(w) for w in room], "heights": {"0": {"wall_height": 390.0}}}
    ben.on_house(ana.me_id, lot)
    assert await until(lambda: any(q.get("action") == "house" and q.get("label") for q in ben.requests.values()))
    q = next(q for q in ben.requests.values() if q["action"] == "house")
    assert q["label"] == "1 new, 0 removed, 4 walls", q
    out = await ben.couple_respond(q["id"], True)
    assert out["result"]["walls"] == 4 and not out["result"]["failed"], out
    assert sorted(map(str, map(housesync.wall_key, gb.walls))) == sorted(map(str, map(housesync.wall_key, room)))
    assert gb.height == 390.0
    tools = [c[0] for c in gb.calls]
    assert tools.index("building.construct_wall") < tools.index("building.place_object")
    assert any(o["table_id"] == "Door01" for o in gb.objects)
    print("walls: built and knocked down like hers, storey height set, doors placed after the walls: ok")

    # the exact house (the game's property): Ana's leaving build mode saves it and sends it; Ben says yes to her
    # house, it's written as a property, and applied when his game enters the world again; the same house twice
    # isn't asked about
    import lotsync
    lotsync.MYSITES = TMP / "MySites"
    saved, applied = [], []

    def fake_save(call, site_id, title):
        saved.append(site_id)
        return {"hash": "h1", "files": {"meta.json": __import__("base64").b64encode(b'{"Title":"x"}').decode(),
                                        "site.dat": __import__("base64").b64encode(b"DAT").decode()}}
    lotsync.save_lot = fake_save
    lotsync.apply_by_title = lambda call, site_id, title: applied.append(title) or True
    ana.cfg["exact_house"] = True
    ben.cfg["always_yes"] = []
    for q in [q for q in list(ben.requests.values()) if q.get("action") == "house"]:
        ben.requests.pop(q["id"], None)
    ga.phase = "none"
    await ana.house_tick()                                   # (out of the build mode her applying left)
    ga.phase = "ready"
    await ana.house_tick()
    ana.build_site["at"] = 0
    ga.objects.append(obj(30, "Lamp77", 1, 1))
    await ana.house_tick()
    ga.phase = "none"
    await ana.house_tick()
    assert await until(lambda: saved and ana.cfg.get("lot_hash") == "h1")
    assert await until(lambda: ben.cfg.get("pending_lot"))                  # (through the server, no question)
    p = ben.cfg.get("pending_lot")
    assert p and p["hash"] == "h1" and (lotsync.MYSITES / p["folder"] / "site.dat").read_bytes() == b"DAT", p
    assert ana.shared["house:1038:exact"]["value"]["hash"] == "h1"
    assert any("comes in when you reopen" in c["text"] for c in ben.chat)
    ben.world, ben.in_build_mode = "play", False
    await ben.maybe_apply_pending()
    assert applied == [p["title"]] and not ben.cfg.get("pending_lot") and ben.cfg["lot_hash"] == "h1"
    assert not (lotsync.MYSITES / p["folder"]).exists()
    ben.on_shared_house_exact("house:1038:exact", ana.shared["house:1038:exact"], snapshot=True)
    await asyncio.sleep(0.3)
    assert not ben.cfg.get("pending_lot")
    print("exact house: saved by the builder, through the server, applied on reopen, same house ignored: ok")
    for t in tasks:
        t.cancel()
    print("house sync tests passed")


if __name__ == "__main__":
    asyncio.run(main())
