"""The family home kept the same in both saves (client/housesync.py), with two stand-in games and a local server:
- comparing: the same kind within 10 cm / 5 degrees is the same object; the rest is new or gone
- Ana rearranges the home in build mode and leaves it: Ben is asked ("2 new, 1 removed"), says yes, and his game
  enters build mode, removes and places what differs, and leaves it; that doesn't send the house back
- a house that isn't Ben's home is ignored; "always yes" applies it without asking

    python tools/test_housesync.py
"""
import asyncio
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


def obj(eid, table, x, y, yaw=0.0, floor=0, z=7.5):
    return {"entity_id": eid, "table_id": table, "floor_level": floor, "location": {"x": x, "y": y, "z": z}, "yaw": yaw}


class StandIn:
    """A game: a home lot (site 1038) with objects, build mode, and the calls the house sync makes."""

    def __init__(self, objects, home=1038):
        self.objects, self.home, self.phase, self.calls, self.next_id = list(objects), home, "none", [], 900

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
    assert await until(lambda: any(q.get("action") == "house" and q.get("label") for q in ben.requests.values()))
    q = next(q for q in ben.requests.values() if q["action"] == "house")
    assert q["label"] == "2 new, 1 removed", q
    assert any("changed the house: 2 new, 1 removed" in c["text"] for c in ben.chat)
    st = ben.couple_view()["requests"][-1]
    assert st["action"] == "house" and st["label"] == "2 new, 1 removed"
    out = await ben.couple_respond(q["id"], True)
    assert out["result"]["state"] == "done" and out["result"]["placed"] == 2 and out["result"]["removed"] == 1, out
    tools = [c[0] for c in gb.calls]
    assert tools.index("build.enter_mode") < tools.index("build.remove_object") < tools.index("building.place_object") \
        < tools.index("build.exit_mode")
    assert sorted(o["table_id"] for o in gb.objects) == ["Chair02", "Plant01", "TV03", "Table03"]
    # leaving build mode after applying doesn't send the house back
    n = len([1 for q in ana.requests.values() if q.get("action") == "house"])
    await ben.house_tick()
    await asyncio.sleep(0.5)
    assert len([1 for q in ana.requests.values() if q.get("action") == "house"]) == n
    print("Ana's changes reach Ben: asked, applied in build mode, not sent back: ok")

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
    for t in tasks:
        t.cancel()
    print("house sync tests passed")


if __name__ == "__main__":
    asyncio.run(main())
