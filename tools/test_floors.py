"""Each room's floor, the same in both games - read from the game's own built data (B2 read_floors, run for real with
lupa against a fake site), kept on the server per room (floor:<lot>:<key>), put through the game's command:
- B2: one line a room - storey (ground 0, 1...; basements -1...), its border nodes, material group and index, its tint
  (linear) or none, the pattern's numbers; a floor as built has group -1
- the companion: a room's key is its storey and border (the same in both saves, whatever order the game lists them
  in); a node key is its row and column on the 25 cm grid, and the room is found in build.scan_floors' list by its
  first node (else its middle); a first reading shares nothing (neither game's is newer); a room repainted here goes
  up (on the version it was made on); a partner's change is put here (build.set_floor_material: group, index, tint as sRGB hex), and the
  game's own reading of what was put isn't taken for a change; not put away from home (tried again at the next read)

    python tools/test_floors.py
"""
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ["INZOI_MP_DOCUMENTS"] = tempfile.mkdtemp()                 # never the real game's mod folder
os.environ["INZOI_MP_GAME_URL"] = "http://127.0.0.1:9/mcp"
os.environ["APPDATA"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

import companion  # noqa: E402
import detailsync  # noqa: E402

B2 = Path(__file__).resolve().parents[1] / "mod" / "InZOIMultiplayer" / "lua" / "B2" / "Main.lua"

# The kitchen (13 nodes, from the real game) painted 1/50 white; a room as built; one in a basement.
FAKE = r"""
local function arr(t) return { Num = function() return #t end, Get = function(_, i) return t[i] end } end
local function room(nodes, ints, floats, color)
    local mv = { Int32Map = ints, FloatMap = floats, ColorMap = {} }
    if color then mv.ColorMap.MaterialColor = { R = color[1], G = color[2], B = color[3], A = color[4] } end
    return { RoomBorder = arr(nodes), MaterialValue = mv }
end
SITE = { GetSiteEntireSaveData = function() return { BuiltData = {
    GroundFloors = arr({
        { CustomRoomBottomFloor = arr({
            room({ 2490448, 2490429, 1900605, 1900598, 2687030, 3276854, 3276835, 3407907, 4522019, 4522066, 3342418,
                   2490450, 2490449 }, { MaterialGroup = 1, MaterialIndex = 50, MirrorUVY = 1 },
                 { PatternScale = 1.0931034, PatternOffsetX = 0.6594827, PatternOffsetY = 0.6594827 }, { 1, 1, 1, 1 }),
            room({ 2687011, 3276835, 3276854, 2687030 }, {}, {}, nil) }) },
        { CustomRoomBottomFloor = arr({}) } }),
    Basements = arr({ { CustomRoomBottomFloor = arr({ room({ 100, 101, 102 }, { MaterialGroup = 3, MaterialIndex = 1 },
        { MaterialRoughness = 0.5 }, { 0.2, 0.3, 0.4, 1 }) }) } }),
} } end }
function em() return { FindSite = function(_, id) return id.Value == 1038 and SITE or nil end } end
function eid(n) return { Value = n } end
REPORTS = {}
function report(kind, text) REPORTS[#REPORTS + 1] = kind .. " " .. text end
"""


def b2_read_floors():
    if LuaRuntime is None:
        print("floors tests (B2): lupa isn't installed (pip install lupa): skipped")
        return None
    src = B2.read_text("utf-8")
    m = re.search(r"^    read_floors = function\(op\)\n(.*?)^    end,\n", src, re.M | re.S)
    assert m, "read_floors op not found in B2/Main.lua"
    lua = LuaRuntime()
    lua.execute(FAKE)
    lua.execute("READ = function(op)\n" + m.group(1) + "end")
    g = lua.globals()
    assert g.READ(lua.eval("{ site = 999 }")) is None and len(g.REPORTS) == 0
    g.READ(lua.eval("{ site = 1038 }"))
    kind, text = g.REPORTS[1].split(" ", 1)
    assert kind == "floors", kind
    lines = text.split(";")
    assert lines[0] == "1038" and len(lines) == 4, lines
    assert lines[1] == ("0|2490448,2490429,1900605,1900598,2687030,3276854,3276835,3407907,4522019,4522066,3342418,"
                        "2490450,2490449|1|50|1.0000,1.0000,1.0000,1.0000|1.0931,0.6595,0.6595,0.0000"), lines[1]
    assert lines[2] == "0|2687011,3276835,3276854,2687030|-1|-1|-|0.0000,0.0000,0.0000,0.0000", lines[2]
    assert lines[3].startswith("-1|100,101,102|3|1|0.2000,0.3000,0.4000,1.0000|"), lines[3]
    print("B2: every room's floor read from the game's built data (storeys, border, material, tint): ok")
    return text


def parsing(text):
    text = text or ("1038;0|2490448,2490429,1900605|1|50|1.0000,1.0000,1.0000,1.0000|1.0931,0.6595,0.6595,0.0000;"
                    "0|2687011,3276835,3276854,2687030|-1|-1|-|0,0,0,0;bad|x;0||1|1|-|0")
    site, rooms = detailsync.parse_floors(text)
    assert site == 1038
    kitchen = next(r for r in rooms.values() if r["g"] == 1)
    assert kitchen["c"] == [1.0, 1.0, 1.0, 1.0] and kitchen["x"][0] == 1.0931 and kitchen["lv"] == 0
    built = next(r for r in rooms.values() if r["g"] == -1)
    assert built["c"] is None
    # the same room listed in another order (the other save): the same key
    k = detailsync.floor_key(0, kitchen["n"])
    assert k in rooms and detailsync.floor_key(0, list(reversed(kitchen["n"]))) == k
    assert detailsync.floor_key(1, kitchen["n"]) != k
    # nodes on the grid, as the real game's kitchen: (38, 80) -> 2012.5, 962.5
    assert detailsync.node_xy(2490448) == (2012.5, 962.5)
    assert detailsync.node_xy(917566) == (1562.5, 362.5)
    assert detailsync.node_xy(-2 * 65536 - 3) == (-62.5, -37.5)             # (row -2, column -3)
    print("companion: a room is its storey and border, the same in both saves; nodes on the 25 cm grid: ok")
    return rooms


SCAN = {"floors": [{"floor_level": 0, "rooms": [
    {"room_index": 0, "first_node": {"x": 2037.5, "y": 137.5}, "center": {"x": 1987.5, "y": 150.0}},
    {"room_index": 6, "first_node": {"x": 2012.5, "y": 962.5}, "center": {"x": 1543.3, "y": 1151.0}},
    {"room_index": 8, "first_node": {"x": 887.5, "y": 1037.5}, "center": {"x": 1125.0, "y": 1150.0}}]},
    {"floor_level": 1, "rooms": [{"room_index": 0, "first_node": {"x": 2012.5, "y": 962.5}, "center": {"x": 0, "y": 0}}]}]}


def finding():
    kitchen = {"lv": 0, "n": [2490448, 2490429]}
    assert detailsync.room_index(SCAN, kitchen) == 6
    assert detailsync.room_index(SCAN, {"lv": 1, "n": [2490448]}) == 0, "the room on the other storey"
    # listed from another node: found by its middle
    other = {"lv": 0, "n": [(46 << 16) + 44, (46 << 16) + 46, (45 << 16) + 44]}
    x, y = detailsync.node_xy(other["n"][0])
    assert (x, y) != (887.5, 1037.5)
    near = {"lv": 0, "n": [(45 << 16) + 44, (46 << 16) + 45, (47 << 16) + 46]}      # middle ~1125, 1150
    assert detailsync.room_index(SCAN, near) == 8
    assert detailsync.room_index(SCAN, {"lv": 0, "n": [(1 << 16) + 1]}) is None, "a room that isn't here"
    print("companion: the room is found in the game's list by its first node, else its middle: ok")


class FakeGame:
    def __init__(self):
        self.calls = []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "build.scan_floors":
            return SCAN
        return {}


def companion_side(rooms):
    async def run():
        comp = companion.Companion({"key": "k" * 20, "name": "me", "server": "x", "port": 1, "password": ""})
        comp.mod["state"], comp.mod["instance"] = "running", "1"
        comp.me_id, comp.my_entity, comp.leader = "me", 24534, "me"
        comp.world = "play"
        comp.game = FakeGame()
        home = {"yes": True}
        comp.at_home_now = lambda site: home["yes"]
        sent = []

        async def share(key, value, base=None, wait=False):
            sent.append((key, value, base))
            comp.shared[key] = {"v": (comp.shared.get(key) or {}).get("v", 0) + 1, "by": comp.me_id, "value": value}
            return True
        comp.share = share

        def text(paint):
            return "1038;" + ";".join(f"{r['lv']}|{','.join(map(str, r['n']))}|{paint.get(k, (r['g'], r['i']))[0]}|"
                                      f"{paint.get(k, (r['g'], r['i']))[1]}|"
                                      f"{'-' if not r['c'] else ','.join(map(str, r['c']))}|0,0,0,0"
                                      for k, r in rooms.items())

        async def report(paint=None):
            comp.on_floors_report(text(paint or {}))
            for _ in range(5):
                await asyncio.sleep(0)
            for t in list(comp.__dict__.get("_shared_tasks", ())):
                await t

        kitchen = next(k for k, r in rooms.items() if r["g"] == 1)
        built = next(k for k, r in rooms.items() if r["g"] == -1)

        # the first reading shares nothing (neither game's is newer); a room repainted here goes up
        await report()
        assert sent == [], sent
        await report()
        assert sent == [], "sent again unchanged"
        await report({kitchen: (2, 2)})
        assert len(sent) == 1 and sent[0][0] == f"floor:1038:{kitchen}" and sent[0][1]["g"] == 2 and sent[0][2] is None
        await report({kitchen: (2, 3)})
        assert len(sent) == 2 and sent[1][1]["i"] == 3 and sent[1][2] == 1, "not on the version it was made on"
        sent.clear()
        print("companion: the first reading shares nothing; a room repainted here goes up: ok")

        # a partner repainted it: put here through the game's command, the tint as sRGB hex
        key = f"floor:1038:{kitchen}"
        comp.shared[key] = {"v": 5, "by": "p1", "value": {"lv": 0, "n": rooms[kitchen]["n"], "g": 1, "i": 50,
                                                          "c": [0.2140, 0.2140, 0.2140, 1.0], "x": [0, 0, 0, 0]}}
        comp.game.calls.clear()
        await report({kitchen: (2, 3)})
        sets = [kw for n, kw in comp.game.calls if n == "build.set_floor_material"]
        assert sets == [{"material_group": 1, "material_index": 50, "floor_level": 0, "room_index": 6,
                         "Color": "#7F7F7F"}], sets
        assert sent == [], "a partner's floor sent back"
        # the game's reading of what was put (the tint a hair off): not a change
        comp.game.calls.clear()
        rooms[kitchen]["c"] = [0.2155, 0.2140, 0.2140, 1.0]
        await report()
        assert sent == [] and not [n for n, _ in comp.game.calls if n == "build.set_floor_material"]
        print("companion: a partner's floor is put here (material, tint); what the game reads back isn't a change: ok")

        # away from home: not put, tried again at the next read
        comp.shared[key] = {"v": 6, "by": "p1", "value": {"lv": 0, "n": rooms[kitchen]["n"], "g": 4, "i": 7, "c": None}}
        home["yes"] = False
        comp.game.calls.clear()
        await report()
        assert not [n for n, _ in comp.game.calls if n == "build.set_floor_material"]
        home["yes"] = True
        await report()
        sets = [kw for n, kw in comp.game.calls if n == "build.set_floor_material"]
        assert sets == [{"material_group": 4, "material_index": 7, "floor_level": 0, "room_index": 6}], sets
        assert sent == []
        print("companion: not put away from home - put at the next read once home: ok")

        # taken back to as built in their game: no command puts that - left as it is, never sent back
        comp.shared[key] = {"v": 8, "by": "p1", "value": {"lv": 0, "n": rooms[kitchen]["n"], "g": -1, "i": -1, "c": None}}
        comp.game.calls.clear()
        await report({kitchen: (4, 7)})
        await report({kitchen: (4, 7)})
        assert not [n for n, _ in comp.game.calls if n == "build.set_floor_material"] and sent == [], sent
        print("companion: a floor taken back to as built there is left as it is here (no command puts it): ok")

        # another PC's first reading shares nothing either; a room it paints later goes up
        comp2 = companion.Companion({"key": "j" * 20, "name": "you", "server": "x", "port": 1, "password": ""})
        comp2.mod["state"], comp2.mod["instance"] = "running", "2"
        comp2.me_id, comp2.my_entity, comp2.leader, comp2.world = "you", 1, "me", "play"
        comp2.game, comp2.at_home_now = FakeGame(), (lambda site: True)
        sent2 = []

        async def share2(key, value, base=None, wait=False):
            sent2.append((key, value, base))
            return True
        comp2.share = share2
        comp2.on_floors_report(text({}))
        for _ in range(5):
            await asyncio.sleep(0)
        assert sent2 == [], "a first reading went up"
        comp2.on_floors_report(text({built: (5, 5)}))
        for _ in range(5):
            await asyncio.sleep(0)
        for t in list(comp2.__dict__.get("_shared_tasks", ())):
            await t
        assert [k for k, _, _ in sent2] == [f"floor:1038:{built}"], sent2
        print("companion: on the other PC too, only a room painted later goes up: ok")

        # a partner's floor coming in triggers a read at once
        comp.mail.ops.clear()
        comp.mp_active = lambda: True             # (connected, playing)
        comp.on_shared_detail(key, {"v": 7, "by": "p1", "value": {}}, snapshot=False)
        assert [o["op"] for _, _, o in comp.mail.ops] == ["read_floors"]
        print("companion: a partner's floor change has the room read (and put) at once: ok")

    asyncio.run(run())


def main():
    text = b2_read_floors()
    rooms = parsing(text)
    finding()
    companion_side(rooms)
    print("floors tests passed")


main()
