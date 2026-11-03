"""The family home kept the same in both saves (client/housesync.py), with two stand-in games and a local server:
- comparing: the same kind within 10 cm / 5 degrees is the same object; the rest is new or gone; walls by coverage;
  a change (build mode opening -> closing) put on top of a newer version keeps both, and a move both made stays the
  newer one's; the three-way plan keeps what the server never knew about and removes only what the partner removed
- Ana rearranges the home in build mode and leaves it: it goes through the server, Ben's game is brought to it without
  asking (2 new, 1 removed) and that doesn't send it back; a drifted house is offered back from the panel
- regressions: a fridge bought in play mode survives the partner's next version and reaches the partner; nothing is
  applied while Ben is in build mode on the home, and his edits go up on top of hers; two versions close together are
  applied once each, one at a time; an object the game won't place doesn't reopen build mode on getting home, is
  tried at most twice and is said once; a stale house:<other lot> key doesn't stop the home's; a change made offline
  goes up on top of the partner's newer one (both kept); both leaving build mode at once: both changes kept; an apply
  that fails as a whole is tried twice and said once, and doesn't stop Ben's own session going up; nothing happens
  while another save is loaded
- a house that isn't Ben's home is ignored; "always yes" applies it without asking; walls built and knocked down like
  hers, storey height set; the exact house: through the server, applied on reopen; an offline exact house loses to the
  partner's newer one, with a chat line
- a change whose word came too late is sent again without being taken for a conflict (no chat line, no undo, no second
  version); what's done the moment build mode opens still goes up (counted from the last reading in this world
  session) and isn't put back, while an older save loaded isn't sent as removals; a partner's newer exact house waiting
  for the game's next start isn't overwritten by mine (my game doesn't have it yet: mine isn't saved, said once) - it
  comes in on reopening, and my next one goes on top of it; an apply that finishes after moving to another server
  doesn't count the old server's version number there
- build mode open when the world goes (a crash, the menu) and an older save comes back: only that session's own edits
  go up (never the reload's differences as removals), nothing before the companion knows which world it's in, no
  exact house of the reloaded lot; the exact house goes up one upload at a time (the newest saved one after it, never
  the same one twice, a mark from an older program not refused over and over); a partner's exact house kept for the
  next start is applied when the game starts again (the companion running all along), and neither an offline build
  session nor an older program's mark holds it back or goes over it
- a save loaded from before (a crash, the autosave): what was deleted since never goes back up as "bought" (from the
  first reading of the home, nor through the next version's apply), what it lacks comes back, purchases after it (and
  across a loading screen that keeps the save) still go up; what the player changes in the build mode an apply has open
  goes up, while the apply's own work (walls, storey height, doors, what it placed or couldn't) doesn't; an exact house
  the server doesn't confirm is sent again only after a wait, longer each time
- (rollback_and_restart) something bought in play mode and not read yet reaches the partner across a restart of this
  program with the game in the same world, and across save-quit-load of the newest save; an older save loaded while
  this program was down brings back what the partner deleted: it stays only in that game (said once) while a purchase
  in it goes up; and the exact house (on) isn't saved while the home has such a thing (said once), so the partner's
  next start doesn't bring it back - once it's taken out, the exact house goes again
- (conflicts) a move made offline beaten by the partner's move or removal: one copy in each game and on the server,
  said once (her version in first, or mine going up while hers is being put in); a change that got to the server with
  its word lost, then taken back out by the partner: not sent again; the exact house (paint) of a game that hasn't
  taken the partner's newer one isn't saved over it, theirs ends in both games; both in build mode: my exact house
  waits until my game has the partner's removal; build mode right after loading an older save: no exact house with
  what the partner deleted
- (races) the partner's change reaching my PC while my exact house is saved: that save isn't sent, it's saved again
  with the change (one saved before my own change is confirmed goes only on the house that change makes); an exact
  house saved offline whose live house changed meanwhile: saved again before it goes; a partner's newer exact house
  arriving while the older one is applied:
  kept for the next start; removals/moves made after a partner's exact house was kept: its start doesn't bring them
  back for good; bought before the server had any version of the house (new, or started over): reaches the partner,
  even with nobody building (the first reading sends the home whole)
- (joining) a first join to a server whose house has a history (or back after its shared state started over): what the
  partner moved or took out before goes from the joiner's game too, nothing comes back; versions without the whole
  history (an older program's): the joiner's extras stay in that game only; my change going up while build mode opens
  for an older version: that one isn't put in, my change never undone; an offline build session's exact house survives
  the game being closed before the server is back
- (regressions) a removal or move made offline, then an older save loaded or the partner's exact house applied at the
  next start: not undone or doubled once it lands; the first version after a reset doesn't bring back what the partner
  deleted; back from another server: what changed there goes up; a purchase not read yet survives the partner's exact
  house at the next start; edits made while an apply builds walls go up; my own version never said as my partner's

    python tools/test_housesync.py [--rollback | --conflicts | --races | --joining | --regressions[=<one>]]
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["APPDATA"] = str(TMP / "appdata")          # (before importing the companion: never the real config)
os.environ["INZOI_MP_DOCUMENTS"] = str(TMP / "Documents")
os.environ["INZOI_MP_GAME_URL"] = "http://127.0.0.1:9/mcp"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import companion  # noqa: E402
import housesync  # noqa: E402

assert str(companion.CONFIG_DIR).startswith(str(TMP)), companion.CONFIG_DIR
KEY = "house:1038:live"


def wall(x0, y0, x1, y1, floor=0):
    return {"floor_level": floor, "left": {"x": x0, "y": y0}, "right": {"x": x1, "y": y1}}


def obj(eid, table, x, y, yaw=0.0, floor=0, z=7.5):
    return {"entity_id": eid, "table_id": table, "floor_level": floor, "location": {"x": x, "y": y, "z": z}, "yaw": yaw}


def key(o):
    return housesync.object_key(o)


class StandIn:
    """A game: a home lot (site 1038) with objects, build mode, and the calls the house sync makes."""

    def __init__(self, objects, home=1038, walls=(), height=300.0):
        self.objects, self.home, self.phase, self.calls, self.next_id = list(objects), home, "none", [], 900
        self.walls, self.height = list(walls), height
        self.where = home                               # the lot my Zoi is on
        self.cant_place, self.slow = set(), 0.0         # (table ids the game refuses to place; s per object)

    def tables(self):
        return sorted(o["table_id"] for o in self.objects)

    def count(self, tool, **match):
        return sum(1 for t, kw in self.calls if t == tool and all(kw.get(k) == v for k, v in match.items()))

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
            return {"objects": [dict(o) for o in self.objects[off:off + kw.get("Limit", 500)]]}
        if tool == "build.remove_object":
            time.sleep(self.slow)
            self.objects = [o for o in self.objects if str(o["entity_id"]) != kw["entity_id"]]
            return {}
        if tool == "building.place_object":
            time.sleep(self.slow)
            if kw["object_table_id"] in self.cant_place:
                raise RuntimeError("the game refused to place it")
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


def idle(*players):
    """Nothing of the house sync running: no apply queued or under way, no change on its way to the server."""
    return all(not p.house_next and not (p.house_worker and not p.house_worker.done()) and not p.house_pushing
               and not p.house_lock.locked() for p in players)


async def settle(*players, timeout=15):
    await asyncio.sleep(0.1)
    assert await until(lambda: idle(*players), timeout), "the house sync didn't settle"
    await asyncio.sleep(0.1)
    assert await until(lambda: idle(*players), timeout), "the house sync didn't settle"


PLAYERS = iter(range(1, 10 ** 6))                       # (each player its own files, whatever its name)


def player(name, port, game):
    comp = companion.Companion({"key": name * 10, "name": name, "server": "127.0.0.1", "port": port, "password": ""})
    comp.mod["state"] = "running"
    comp.world, comp.my_city, comp.my_entity = "play", "Dowon", 10
    comp.save_session["mp"] = True                       # (the multiplayer save is the one loaded)
    comp.save_session["started"] = time.time()           # (this world session: its readings of the home count)
    comp.game.saw_lobby = False                          # (the companion has looked at which save it is: watch_save)
    comp.game.call = game.call
    comp.save_cfg = lambda: None
    n = next(PLAYERS)                                    # (two sections' players of the same name - Eve, Finn - never
    comp.lot_file = TMP / f"{name}-{n}-lot.json"         # share a reading or a home-lot-sync.json: a section starts
    comp.preset_file = TMP / f"{name}-{n}-preset.json"   # from nothing of another's - its "gone", its base)
    comp.cfg["exact_house"] = False
    return comp


def reopen(p, restarted: bool = True, same_save: bool = False):
    """Back in the game's world, as the companion's watch_save notes it: a new world session. A save loaded (from the
    main menu; restarted: in a new run of the game, its process - the game was closed and started again) gets a
    save_session of its own; a loading screen that keeps the save (same_save: moving to another lot) carries it over."""
    s = p.save_session
    started = max(time.time(), s["started"] + 0.001)
    if same_save:
        p.save_session = {**s, "started": started, "checked": started}
    else:
        p.save_session = {"started": started, "folder": s.get("folder"), "checked": 0.0, "where": s.get("where"),
                          "game": (s.get("game") or 0) + 1 if restarted else s.get("game"), "mp": s.get("mp")}
    p.game.saw_lobby = False


async def build(p, game, change):
    """The player enters build mode on the home, changes it (change(game)), and leaves it."""
    game.phase = "ready"
    await p.house_tick()
    change(game)
    p.build_site["at"] = 0
    await p.house_tick()
    game.phase = "none"
    await p.house_tick()


def pure():
    a = [key(obj(1, "Sofa01", 100, 100)), key(obj(2, "Lamp01", 300, 50, 90))]
    b = [dict(key(obj(7, "Sofa01", 104, 97)), entity_id=7), dict(key(obj(8, "Lamp01", 300, 50, 180)), entity_id=8),
         dict(key(obj(9, "Plant01", 10, 10)), entity_id=9)]
    place, remove = housesync.diff(a, b)
    assert [o["table_id"] for o in place] == ["Lamp01"] and sorted(o["entity_id"] for o in remove) == [8, 9]
    print("compare: same kind within 10 cm / 5 degrees is the same object: ok")
    k = housesync.wall_key
    long_ = [k(wall(12.5, 12.5, 512.5, 12.5))]
    halves = [k(wall(512.5, 12.5, 212.5, 12.5)), k(wall(12.5, 12.5, 212.5, 12.5))]
    assert housesync.wall_diff(long_, halves) == ([], []) and housesync.wall_diff(halves, long_) == ([], [])
    build_, delete = housesync.wall_diff([k(wall(12.5, 12.5, 312.5, 12.5))], long_)
    assert build_ == [k(wall(12.5, 12.5, 312.5, 12.5))] and delete == long_
    assert housesync.wall_diff(long_, [k(wall(12.5, 12.5, 512.5, 12.5, floor=1))])[0] == long_
    print("compare walls: the same line however it's split; a shorter wall is rebuilt; floors apart: ok")

    # a change made on one version, put on top of a newer one: both kept; a move both made stays the newer one's
    sofa, table, tv = key(obj(1, "Sofa01", 100, 100)), key(obj(2, "Table03", 500, 500, 90)), key(obj(3, "TV03", 800, 200))
    entry = {"objects": [sofa, table, tv], "walls": long_, "heights": {}}
    mine = {"objects": [table, key(obj(4, "TV03", 900, 200)), key(obj(5, "Rug07", 300, 300))],
            "walls": [k(wall(12.5, 12.5, 212.5, 12.5))], "heights": {}}             # (sofa out, TV moved, wall cut)
    d = housesync.session_delta(entry, mine)
    assert sorted(o["table_id"] for o in d["added"]) == ["Rug07", "TV03"] and len(d["removed"]) == 2, d
    same_base, dropped, _ = housesync.fold(dict(entry, site=1038), d)
    assert not dropped and housesync.diff(same_base["objects"], mine["objects"]) == ([], [])
    assert housesync.wall_diff(same_base["walls"], mine["walls"]) == ([], []), same_base["walls"]
    theirs = {"objects": [sofa, table, key(obj(6, "TV03", 820, 260)), key(obj(7, "Plant01", 50, 60))],
              "walls": long_ + [k(wall(1012.5, 12.5, 1012.5, 312.5))], "heights": {}}  # (they moved the TV first)
    merged, dropped, undo = housesync.fold(theirs, d)
    assert dropped == 1 and [o["x"] for o in undo] == [900], "my move of the TV they had moved is dropped"
    assert sorted(o["table_id"] for o in merged["objects"]) == ["Plant01", "Rug07", "TV03", "Table03"], merged
    assert housesync.has(merged["objects"], key(obj(0, "TV03", 820, 260)))
    assert housesync.covered(k(wall(1012.5, 12.5, 1012.5, 312.5)), merged["walls"])
    assert not housesync.covered(k(wall(312.5, 12.5, 512.5, 12.5)), merged["walls"])
    assert housesync.covered(k(wall(12.5, 12.5, 212.5, 12.5)), merged["walls"])
    # the same change put again on the version that has it already (the server took it; its word came late or went
    # with the connection): my own move isn't counted as beaten, nothing to undo, and nothing changes
    again, dropped, undo = housesync.fold(same_base, d)
    assert not dropped and not undo, (dropped, undo)
    assert housesync.same_house(again, same_base) and not housesync.same_house(again, entry)
    print("a change on top of a newer version: both kept, the same object moved by both stays theirs: ok")

    # the three-way plan: only what they removed since the base goes; what the server never knew about stays
    base = {"value": {"objects": [sofa, table, tv], "walls": [], "heights": {}},
            "lot": {"objects": [sofa, table, tv], "walls": [], "heights": {}}}
    fridge = dict(key(obj(50, "Fridge09", 700, 700)), entity_id=50)
    here = [dict(sofa, entity_id=1), dict(table, entity_id=2), dict(tv, entity_id=3), fridge]
    theirs = {"objects": [table, tv, key(obj(0, "Lamp02", 1, 1))], "walls": [], "heights": {}}      # (sofa out, lamp)
    plan = housesync.house_plan(theirs, {"objects": here, "walls": [], "heights": {}}, "merge", base)
    assert [o["table_id"] for o in plan["place"]] == ["Lamp02"] and [o["table_id"] for o in plan["remove"]] == ["Sofa01"]
    full = housesync.house_plan(theirs, {"objects": here, "walls": [], "heights": {}}, "full")
    assert sorted(o["table_id"] for o in full["remove"]) == ["Fridge09", "Sofa01"]
    add = housesync.house_plan(dict(theirs, change={"removed": [sofa]}), {"objects": here}, "add")
    assert [o["table_id"] for o in add["remove"]] == ["Sofa01"], "no base: only what that version says it removed"
    print("three-way plan: their removals only, what the server never knew about stays: ok")

    # the player's own edits in the build mode an apply had open: what the apply did itself isn't counted (placed,
    # placed a little off - a door snapped onto its wall -, refused, a removal the game didn't do); the player's are
    lamp, rug, door = key(obj(0, "Lamp02", 1, 1)), key(obj(0, "Rug07", 300, 300)), key(obj(0, "Door01", 100, 12.5))
    heights = {"0": {"wall_height": 300.0}}
    before = {"objects": [sofa, table], "walls": long_, "heights": heights}           # (the TV removed by the apply)
    plan = {"place": [lamp, door, key(obj(0, "Statue99", 5, 5))], "remove": [dict(tv, entity_id=3)]}
    snapped = key(obj(0, "Door01", 130, 12.5))
    after = {"objects": [sofa, table, tv, lamp, snapped], "walls": long_, "heights": heights}
    assert housesync.apply_edits(plan, before, after) is None, housesync.apply_edits(plan, before, after)
    edits = housesync.apply_edits(plan, before, dict(after, objects=[table, tv, lamp, snapped, rug]))
    assert [o["table_id"] for o in edits["added"]] == ["Rug07"], edits
    assert [o["table_id"] for o in edits["removed"]] == ["Sofa01"], edits
    side = k(wall(612.5, 12.5, 612.5, 312.5))
    edits = housesync.apply_edits(plan, before, dict(after, walls=long_ + [side]))
    assert edits and edits["walls_add"] == [side] and not (edits["added"] or edits["removed"] or edits["heights"])
    # (walls or storeys a reading couldn't read aren't "all built"; a door gone from a wall the apply knocked down went
    # with it - the game's doing, a moment late -, not the player's: never sent as removed)
    assert housesync.apply_edits(plan, dict(before, walls=[], heights={}), dict(after, walls=long_ + [side])) is None
    in_wall = key(obj(0, "Door09", 300, 12.5))
    assert housesync.apply_edits(dict(plan, delete=[long_[0]]), dict(before, objects=[sofa, table, in_wall]),
                                 after) is None
    print("edits made while an apply had build mode open: the player's counted, the apply's own work isn't: ok")


async def main():
    pure()
    import inzoi_mp_server as mp
    server = mp.Server(mp.Store(TMP / "server"))
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
    port = srv.sockets[0].getsockname()[1]
    home = [obj(1, "Sofa01", 100, 100), obj(2, "Table03", 500, 500, 90), obj(3, "TV03", 800, 200)]
    ga, gb = StandIn(home), StandIn(home)
    ana, ben = player("Ana", port, ga), player("Ben", port, gb)
    tasks = [asyncio.create_task(ana.session()), asyncio.create_task(ben.session())]
    assert await until(lambda: ana.me_id and ben.me_id and ben.me_id in ana.remotes and ana.me_id in ben.remotes)
    assert await until(lambda: ana.shared_conn is ana.writer and ben.shared_conn is ben.writer)

    # Ana in build mode: adds a plant and a chair, takes the sofa out
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
    assert await until(lambda: gb.tables() == ["Chair02", "Plant01", "TV03", "Table03"])
    await settle(ana, ben)
    item = ana.shared[KEY]
    assert item["by"] == ana.me_id and ben.shared[KEY]["v"] == item["v"]
    assert any("changed the house: 2 new, 1 removed" in c["text"] for c in ben.chat)
    assert ben.seen(KEY) == item["v"] and ana.seen(KEY) == item["v"]
    tools = [c[0] for c in gb.calls]
    assert tools.index("build.enter_mode") < tools.index("build.remove_object") < tools.index("building.place_object") \
        < tools.index("build.exit_mode")
    # Ben leaving build mode after applying doesn't send the house back: the server's stays Ana's version
    await ben.house_tick()
    await settle(ana, ben)
    assert ana.shared[KEY]["v"] == item["v"] and ana.shared[KEY]["by"] == ana.me_id
    n = len(gb.calls)
    ben.shared_changed(KEY, ben.shared[KEY], snapshot=True)                     # (a restart: seen, kept)
    await settle(ben)
    assert len(gb.calls) == n
    print("Ana's change goes through the server: Ben's game brought to it once, not sent back: ok")

    # Ben's house drifted (an object gone in his save): "get my partner's house" offers it back, and brings it back
    gb.objects = [o for o in gb.objects if o["table_id"] != "Chair02"]
    assert (await ben.request_house(ben.remotes[ana.me_id]))["ok"]
    assert await until(lambda: any(q.get("action") == "house" and q.get("label") for q in ben.requests.values()))
    q = next(q for q in ben.requests.values() if q["action"] == "house")
    assert q["label"] == "1 new, 0 removed", q
    out = await ben.couple_respond(q["id"], True)
    assert out["result"]["state"] == "done", out
    assert any(o["table_id"] == "Chair02" for o in gb.objects)
    print("a drifted house is offered back from the panel and brought back: ok")

    # --- #6: something bought in play mode isn't deleted by the partner's next version, and reaches the partner
    gb.objects.append(obj(200, "Fridge09", 700, 700))      # (bought in play mode: never through build mode)
    enters = gb.count("build.enter_mode")
    for _ in range(2):                                       # (Ben goes out and comes home: nothing applied again)
        ben.last_full_state = {"site": 5}
        await ben.house_tick()
        ben.last_full_state = {"site": 1038}
        ben.asked_at = 0
        await ben.house_tick()
        await settle(ben)
    assert gb.count("build.enter_mode") == enters, "getting home opened build mode"

    def move_tv(g):
        g.objects = [o for o in g.objects if o["table_id"] != "TV03"] + [obj(31, "TV03", 900, 200)]
    await build(ana, ga, move_tv)
    assert await until(lambda: any(o["table_id"] == "TV03" and o["location"]["x"] == 900 for o in gb.objects))
    await settle(ana, ben)
    assert "Fridge09" in gb.tables(), "the fridge bought in play mode was deleted"
    assert await until(lambda: "Fridge09" in ga.tables()), "the fridge didn't reach Ana"
    await settle(ana, ben)
    assert ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    print("#6 bought in play mode: kept through the partner's change, sent up, in both games: ok")

    # --- #7: nothing is applied while Ben is in build mode on the home; his edits go up on top of hers
    gb.phase = "ready"
    await ben.house_tick()
    gb.objects += [obj(50, "Bed07", 300, 300), obj(51, "Lamp02", 320, 300)]
    ben.build_site["at"] = 0
    await ben.house_tick()
    enters, seen_before = gb.count("build.enter_mode"), ben.seen(KEY)
    await build(ana, ga, lambda g: g.objects.append(obj(60, "Rug01", 200, 200)))
    assert await until(lambda: ben.shared[KEY]["v"] > seen_before)
    await asyncio.sleep(1.0)
    assert gb.phase == "ready" and gb.count("build.enter_mode") == enters, "applied while Ben was in build mode"
    assert ben.seen(KEY) == seen_before and "Bed07" in gb.tables()
    gb.phase = "none"
    await ben.house_tick()                                   # Ben leaves build mode: his edits go up, hers come in
    assert await until(lambda: "Rug01" in gb.tables() and "Bed07" in ga.tables() and "Lamp02" in ga.tables())
    await settle(ana, ben)
    assert ga.tables() == gb.tables() and ana.shared[KEY]["by"] == ben.me_id, (ga.tables(), gb.tables())
    assert ben.seen(KEY) == ana.seen(KEY) == ana.shared[KEY]["v"]
    print("#7 build mode: the partner's version waits, Ben's edits go up on top of it, both games the same: ok")

    # --- #21: two versions close together (the game is slow): applied one at a time, nothing placed twice
    gb.slow = 0.25
    await build(ana, ga, lambda g: g.objects.extend(obj(70 + i, f"Stool{i:02}", 100 + 60 * i, 900) for i in range(4)))
    assert await until(lambda: gb.count("building.place_object", object_table_id="Stool00") > 0)
    await build(ana, ga, lambda g: g.objects.append(obj(80, "Lamp01", 5, 5)))
    assert await until(lambda: "Lamp01" in gb.tables(), 20)
    await settle(ana, ben)
    gb.slow = 0.0
    counts = {t: gb.tables().count(t) for t in gb.tables()}
    assert all(c == 1 for c in counts.values()), counts
    assert ga.tables() == gb.tables() and ben.seen(KEY) == ana.shared[KEY]["v"]
    print("#21 two versions close together: one apply at a time, nothing twice, seen at the newest: ok")

    # what Ben does in the build mode his game's apply has open (the game shows it while it places Ana's things): a
    # rug placed, a lamp taken out - both go up, as his change; nothing of the apply's own work does
    real_call, edited = gb.call, []

    def meanwhile(tool, **kw):
        out = real_call(tool, **kw)
        if tool == "building.place_object" and not edited:
            edited.append(1)
            gb.objects.append(obj(84, "Rug55", 250, 650))
            gb.objects = [o for o in gb.objects if o["table_id"] != "Lamp02"]
        return out
    ben.game.call = meanwhile
    v_before = ana.shared[KEY]["v"]
    await build(ana, ga, lambda g: g.objects.extend([obj(85, "Vase55", 640, 80), obj(86, "Book55", 660, 80)]))
    assert await until(lambda: edited and "Rug55" in ga.tables() and "Lamp02" not in ga.tables()), \
        "what Ben changed while his game was being brought to Ana's house never went up"
    await settle(ana, ben)
    ben.game.call = real_call
    assert ga.tables() == gb.tables() and not ben.pending(KEY), (ga.tables(), gb.tables())
    assert ana.shared[KEY]["by"] == ben.me_id and ana.shared[KEY]["v"] == v_before + 2, ana.shared[KEY]["v"] - v_before
    print("edits made in the build mode an apply had open: they go up, the apply's own work doesn't: ok")

    # --- #22: an object the game won't place: not retried on getting home, tried twice at most, said once
    gb.cant_place = {"Statue99"}
    await build(ana, ga, lambda g: g.objects.append(obj(90, "Statue99", 10, 10)))
    await settle(ana, ben)
    assert "Statue99" not in gb.tables() and ben.seen(KEY) == ana.shared[KEY]["v"]
    enters = gb.count("build.enter_mode")
    for _ in range(3):
        ben.last_full_state = {"site": 5}
        await ben.house_tick()
        ben.last_full_state = {"site": 1038}
        ben.asked_at = 0
        await ben.house_tick()
        ben.reapply_shared_house()                           # (the partner's Zoi arriving does this once a session)
        await settle(ben)
    assert gb.count("build.enter_mode") == enters, "build mode reopened for the object the game won't place"
    for i, t in enumerate(("Vase01", "Book01", "Clock02")):
        await build(ana, ga, lambda g, t=t, i=i: g.objects.append(obj(91 + i, t, 40 + 30 * i, 40)))
        assert await until(lambda: t in gb.tables())
        await settle(ana, ben)
    assert gb.count("building.place_object", object_table_id="Statue99") == housesync.MAX_TRIES
    said = [c for c in ben.chat if "couldn't be put in your game" in c["text"]]
    assert len(said) == 1, said
    assert ga.tables() == sorted(gb.tables() + ["Statue99"])
    await build(ana, ga, lambda g: g.objects.remove(next(o for o in g.objects if o["table_id"] == "Statue99")))
    await settle(ana, ben)
    assert ga.tables() == gb.tables()
    gb.cant_place = set()
    print("#22 what the game won't place: no build mode on getting home, tried twice, said once: ok")

    # --- #23: a stale house:<other lot> key doesn't stop the home's from being applied (in either order)
    assert await ana.share("house:958:live", {"site": 958, "site_id": "Old", "objects": [], "walls": [],
                                              "heights": {}}, base=0, wait=True)
    await settle(ana, ben)
    ben.world = "out"                                        # (Ben's game away: the next version waits)
    await build(ana, ga, lambda g: g.objects.append(obj(95, "Piano01", 1200, 1200)))
    assert await until(lambda: ben.shared[KEY]["v"] > ben.seen(KEY))
    ben.house_next.clear()
    ben.world = "play"
    ben.shared_changed(KEY, ben.shared[KEY], snapshot=True)         # (as a snapshot hands them: the home's first,
    ben.shared_changed("house:958:live", ben.shared["house:958:live"], snapshot=True)    # then the stale lot's)
    assert await until(lambda: "Piano01" in gb.tables())
    await settle(ana, ben)
    assert ben.seen(KEY) == ben.shared[KEY]["v"] and not ben.seen("house:958:live")
    assert gb.count("build.enter_mode", site_id="Old") == 0
    print("#23 a stale key for another lot: ignored, the home's applied: ok")

    # --- #25: Ben changes the home while the server can't be reached; Ana changes it meanwhile: both kept
    writer = ben.writer
    ben.writer = None
    await build(ben, gb, lambda g: g.objects.append(obj(77, "Rug07", 300, 600)))
    assert ben.dirty(1038, "live") and ben.pending(KEY)
    await build(ana, ga, lambda g: g.objects.append(obj(78, "Plant05", 600, 600)))
    assert await until(lambda: "Plant05" in gb.tables())     # (hers comes in: his unsent rug stays)
    await settle(ana, ben)
    assert "Rug07" in gb.tables() and ben.dirty(1038, "live")
    ben.writer = writer
    await ben.push_dirty_house()
    assert await until(lambda: "Rug07" in ga.tables())
    await settle(ana, ben)
    assert not ben.dirty(1038, "live") and ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    assert "Plant05" in ga.tables(), "Ana's newer change was overwritten"
    print("#25 a change made offline goes up on top of the partner's newer one: both kept: ok")

    # a change the server took but whose word came too late (taken as lost) goes again, on top of the version that
    # has it: not counted as beaten by anyone (no chat line, nothing to undo) and not published a second time
    real_ex = ben.share_ex

    async def late_word(key_, value, base=None):
        ben.share_ex = real_ex
        out = await real_ex(key_, value, base)
        return "lost" if out == "ok" else out            # (the server has it; its word came after the wait ran out)
    ben.share_ex = late_word
    since = time.time()

    def move_rug(g):
        g.objects = [o for o in g.objects if o["table_id"] != "Rug07"] + [obj(79, "Rug07", 300, 750)]
    await build(ben, gb, move_rug)
    assert await until(lambda: any(o["table_id"] == "Rug07" and o["location"]["y"] == 750 for o in ga.objects))
    await settle(ana, ben)
    v_landed = ana.shared[KEY]["v"]
    ben.house_push_at = 0
    await ben.push_dirty_house()                             # (kept as unconfirmed, sent again)
    await settle(ana, ben)
    assert not ben.pending(KEY) and not ben.dirty(1038, "live")
    assert ana.shared[KEY]["v"] == v_landed, "the same change was published again"
    assert not [c for c in ben.chat if c["time"] >= since and "weren't kept" in c["text"]], list(ben.chat)[-5:]
    assert not (ben.hs().get("undo") or {}).get(KEY)
    assert ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    print("a change whose word came late, sent again: not taken for a conflict, not published twice: ok")

    # what Ben does the moment build mode opens (before the first reading in build mode) still goes up - counted from
    # the last reading of the home in this world session -, and Ana's next version doesn't put it back
    gb.phase = "ready"
    gb.objects = [o for o in gb.objects if o["table_id"] != "Plant05"]     # (deleted at once)
    await ben.house_tick()                                   # (the first reading in build mode: already without it)
    gb.phase = "none"
    await ben.house_tick()
    assert await until(lambda: "Plant05" not in ga.tables()), "a delete made right away never reached the server"
    await settle(ana, ben)
    await build(ana, ga, lambda g: g.objects.append(obj(121, "Fern01", 650, 650)))
    assert await until(lambda: "Fern01" in gb.tables())
    await settle(ana, ben)
    assert "Plant05" not in gb.tables() and ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    # ...but a save loaded again (maybe one from before) isn't a change of mine: readings of the home from before it
    # don't count, so what that save lacks isn't sent as removed (and it's brought back)
    reopen(ben, restarted=False)
    gb.objects = [o for o in gb.objects if o["table_id"] != "Fern01"]      # (that save is from before the fern)
    await build(ben, gb, lambda g: g.objects.append(obj(122, "Stool77", 660, 660)))
    assert await until(lambda: "Stool77" in ga.tables())
    await settle(ana, ben)
    assert "Fern01" in ga.tables(), "a save loaded from before deleted the partner's things"
    assert await until(lambda: "Fern01" in gb.tables())
    await settle(ana, ben)
    assert ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    print("build mode: what's done at once goes up and isn't put back; an older save loaded isn't sent as removals: ok")

    # build mode open on the home when the world goes (the game crashes, or goes to the menu from build mode), and the
    # game comes back with a save from before Ana's last change: only what that session's own readings show goes up -
    # never what the reloaded save lacks, as removals of her things -, nothing is done before the companion knows which
    # world it's in, and no exact house is saved of the reloaded lot
    captured = []

    async def no_capture(b):
        captured.append(b)
    ben.capture_lot, ben.cfg["exact_house"] = no_capture, True
    await build(ana, ga, lambda g: g.objects.append(obj(123, "Gift01", 680, 120)))
    assert await until(lambda: "Gift01" in gb.tables())
    await settle(ana, ben)
    on_disk = [dict(o) for o in gb.objects if o["table_id"] != "Gift01"]       # (Ben's save: from before the gift)
    gb.phase = "ready"
    await ben.house_tick()
    gb.objects.append(obj(124, "Chair77", 700, 140))         # (Ben's edit in that session, read before the crash)
    ben.build_site["at"] = 0
    await ben.house_tick()
    v_before = ana.shared[KEY]["v"]
    ben.world = "out"                                        # (the game crashed: no ticks while the world is out)
    gb.objects, gb.phase = [dict(o) for o in on_disk], "none"      # (it starts again, with the older save)
    ben.world, ben.game.saw_lobby = "play", True             # (back in a world from the main menu, before the
    await ben.house_tick()                                   # companion has looked at which save it is)
    await asyncio.sleep(0.3)
    assert ben.build_site is not None and ana.shared[KEY]["v"] == v_before, "acted before the world was known"
    reopen(ben)
    await ben.house_tick()
    assert ben.build_site is None
    assert await until(lambda: "Chair77" in ga.tables()), "the session's own edit didn't go up"
    await settle(ana, ben)
    assert "Gift01" in ga.tables(), "a save loaded after a crash in build mode deleted the partner's things"
    assert await until(lambda: "Gift01" in gb.tables() and "Chair77" in gb.tables())   # (Ben's game brought back)
    await settle(ana, ben)
    assert ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    # the same when no new world session is seen (a loading screen taken for the same save): a tick that comes long
    # after the last one means the world was out meanwhile
    gb.phase = "ready"
    await ben.house_tick()
    on_disk = [dict(o) for o in gb.objects if o["table_id"] != "Gift01"]
    gb.objects.append(obj(125, "Stool88", 720, 160))
    ben.build_site["at"] = 0
    await ben.house_tick()
    gb.objects, gb.phase = on_disk, "none"
    ben.house_ticked = time.time() - 2 * housesync.TICK_GAP
    await ben.house_tick()
    assert ben.build_site is None
    assert await until(lambda: "Stool88" in ga.tables()), "the session's own edit didn't go up"
    await settle(ana, ben)
    assert "Gift01" in ga.tables(), "a world left in build mode deleted the partner's things"
    assert await until(lambda: "Gift01" in gb.tables() and "Stool88" in gb.tables())
    await settle(ana, ben)
    assert ga.tables() == gb.tables() and not captured, (ga.tables(), gb.tables(), len(captured))
    del ben.capture_lot
    ben.cfg["exact_house"] = False
    print("build mode open when the world went: only that session's own edits go up, no exact house of the reload: ok")

    # both leave build mode at the same moment, both having moved the table and each added something
    def ana_edit(g):
        g.objects = [o for o in g.objects if o["table_id"] != "Table03"] + [obj(110, "Table03", 520, 600, 90),
                                                                            obj(111, "Mirror01", 30, 700)]

    def ben_edit(g):
        g.objects = [o for o in g.objects if o["table_id"] != "Table03"] + [obj(112, "Table03", 480, 400, 90),
                                                                            obj(113, "Clock01", 30, 800)]
    for p, g, edit in ((ana, ga, ana_edit), (ben, gb, ben_edit)):
        g.phase = "ready"
        await p.house_tick()
        edit(g)
        p.build_site["at"] = 0
        await p.house_tick()
    ga.phase = gb.phase = "none"
    await asyncio.gather(ana.house_tick(), ben.house_tick())
    assert await until(lambda: "Clock01" in ga.tables() and "Mirror01" in gb.tables())
    await settle(ana, ben)
    assert ga.tables() == gb.tables() and ga.tables().count("Table03") == 1, (ga.tables(), gb.tables())
    ta = next(o for o in ga.objects if o["table_id"] == "Table03")["location"]
    tb = next(o for o in gb.objects if o["table_id"] == "Table03")["location"]
    assert (ta["x"], ta["y"]) == (tb["x"], tb["y"]), (ta, tb)
    assert any("weren't kept" in c["text"] for c in ana.chat + ben.chat)
    print("both leave build mode at once: both changes kept, the table moved by both is the first one's: ok")

    # Ben's game crashes and he loads a save older than the house (the autosave): what Ana deleted since is back in his
    # game - it must never go up as "bought in play mode" (which would put it back in hers), neither from his first
    # reading of the home nor through the apply of her next version; what that save lacks comes back. What he buys
    # after that first reading still goes up, and so does a purchase across a loading screen that keeps the save.
    await settle(ana, ben)
    ben.last_full_state = {"site": 1038}
    autosave = [dict(o) for o in gb.objects]                 # (Ben's save on disk, written now)
    await build(ana, ga, lambda g: g.objects.append(obj(115, "Easel01", 60, 950)))
    assert await until(lambda: "Easel01" in gb.tables())
    await settle(ana, ben)
    await build(ana, ga, lambda g: g.objects.remove(next(o for o in g.objects if o["table_id"] == "Mirror01")))
    assert await until(lambda: "Mirror01" not in gb.tables())
    await settle(ana, ben)
    since = time.time()
    ben.world = "out"                                        # (the crash; the game starts again with that save)
    gb.objects = [dict(o) for o in autosave]
    ben.world, ben.game.saw_lobby = "play", True
    await ben.house_tick()
    reopen(ben)
    for _ in range(3):
        await ben.house_tick()                               # (the 2 s loop, at home in play mode)
        await settle(ana, ben)
    on_server = [o["table_id"] for o in ana.shared[KEY]["value"]["objects"]]
    assert "Mirror01" not in on_server and "Mirror01" not in ga.tables(), "a save loaded from before put back what " \
        "Ana had deleted"
    assert "Easel01" in gb.tables() and "Mirror01" in gb.tables(), "what the save loaded lacks didn't come back"
    said = [c for c in ben.chat if c["time"] >= since and "taken out of the shared house" in c["text"]]
    assert len(said) == 1, said
    await build(ana, ga, lambda g: g.objects.append(obj(116, "Globe01", 80, 950)))
    assert await until(lambda: "Globe01" in gb.tables())
    await settle(ana, ben)
    assert "Mirror01" not in ga.tables(), "the apply of Ana's next version put back what she had deleted"
    refresh, housesync.REFRESH_EVERY = housesync.REFRESH_EVERY, 0      # (every tick at home reads the home)
    try:
        gb.objects.append(obj(117, "Kettle01", 720, 720))
        await ben.house_tick()
        assert await until(lambda: "Kettle01" in ga.tables()), "something bought after the save was loaded didn't go up"
        await settle(ana, ben)
        gb.objects.append(obj(118, "Toaster01", 740, 720))
        reopen(ben, same_save=True)                          # (a loading screen: to another lot and back)
        await ben.house_tick()
        assert await until(lambda: "Toaster01" in ga.tables()), "a loading screen kept a purchase from going up"
        await settle(ana, ben)
    finally:
        housesync.REFRESH_EVERY = refresh
    await build(ben, gb, lambda g: g.objects.remove(next(o for o in g.objects if o["table_id"] == "Mirror01")))
    await settle(ana, ben)                                   # (Ben takes the old mirror out himself: the same again)
    assert ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    print("a save loaded from before: what was deleted since never goes back up, what it lacks comes back; purchases "
          "after it still go up: ok")

    # a version that can't be applied at all (build mode won't open): tried twice, then left until the house changes
    # (said once), never again on getting home; the failed apply doesn't leave "applying" on, so Ben's own build mode
    # session still goes up - and brings the version that failed in with it
    real, tries = gb.call, []

    def busy(tool, **kw):
        if tool == "build.enter_mode":
            tries.append(1)
            raise RuntimeError("the game is busy")
        return real(tool, **kw)
    ben.game.call = busy
    v_before = ana.shared[KEY]["v"]
    await build(ana, ga, lambda g: g.objects.append(obj(150, "Sculpt01", 900, 900)))
    assert await until(lambda: ben.shared[KEY]["v"] > v_before)
    v_failed = ben.shared[KEY]["v"]
    for _ in range(40):                                      # (the 2 s loop, kicking the queue)
        if ben.house_gave_up(KEY, v_failed):
            break
        await ben.house_tick()
        await asyncio.sleep(0.25)
    assert ben.house_gave_up(KEY, v_failed) and len(tries) == housesync.MAX_TRIES, tries
    assert not ben.applying_house
    for _ in range(2):
        ben.last_full_state = {"site": 5}
        await ben.house_tick()
        ben.last_full_state = {"site": 1038}
        await ben.house_tick()
        await settle(ben)
    assert len(tries) == housesync.MAX_TRIES and "Sculpt01" not in gb.tables()
    assert len([c for c in ben.chat if "couldn't be brought into your game" in c["text"]]) == 1
    ben.game.call = real
    await build(ben, gb, lambda g: g.objects.append(obj(151, "Vase77", 950, 900)))
    assert await until(lambda: "Vase77" in ga.tables() and "Sculpt01" in gb.tables())
    await settle(ana, ben)
    assert ga.tables() == gb.tables()
    print("an apply that fails: tried twice, said once, left; Ben's next session still goes up: ok")

    # another save loaded (not the multiplayer one): its lots aren't the couple's home - nothing applied, nothing read
    ben.save_session["mp"] = False
    await build(ana, ga, lambda g: g.objects.append(obj(160, "Shelf01", 950, 950)))
    await asyncio.sleep(0.5)
    gb.phase = "ready"
    await ben.house_tick()
    assert "Shelf01" not in gb.tables() and ben.build_site is None and ben.house_next
    gb.phase = "none"
    ben.save_session["mp"] = True
    await ben.house_tick()
    assert await until(lambda: "Shelf01" in gb.tables())
    await settle(ana, ben)
    print("another save loaded: the house waits for the multiplayer save: ok")

    # another lot (not Ben's home): ignored; "always yes": applied without asking
    gb.home = 2000
    ben.on_house(ana.me_id, {"site": 1038, "site_id": "X", "objects": [key(obj(0, "Lamp09", 1, 1))]})
    await asyncio.sleep(0.5)
    assert not [q for q in ben.requests.values() if q.get("action") == "house"]
    gb.home = 1038
    ben.cfg["always_yes"] = ["house"]
    ben.on_house(ana.me_id, {"site": 1038, "site_id": "X", "objects": [key(o) for o in gb.objects] +
                             [key(obj(0, "Lamp09", 1, 1))]})
    assert await until(lambda: any(o["table_id"] == "Lamp09" for o in gb.objects))
    await settle(ben)
    print("another lot ignored; always yes applies by itself: ok")

    # walls: Ana builds a room (4 walls), knocks the old wall down, puts a door in, raises the storey to 3.9 m
    ben.cfg["always_yes"] = []
    room = [wall(1012.5, 1012.5, 1312.5, 1012.5), wall(1312.5, 1012.5, 1312.5, 1312.5),
            wall(1312.5, 1312.5, 1012.5, 1312.5), wall(1012.5, 1312.5, 1012.5, 1012.5)]
    gb.walls = [wall(12.5, 12.5, 512.5, 12.5)] + room[:1]
    gb.calls.clear()
    lot = {"site": 1038, "site_id": "X", "objects": [key(o) for o in gb.objects] + [key(obj(0, "Door01", 1100, 1012.5))],
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

    # walls through the server: Ana builds two walls with a door in them and sets the storey's height; Ben's game is
    # brought to it, and what that apply did itself (walls, the height, the door) isn't taken for edits of Ben's
    await settle(ana, ben)
    await build(ana, ga, lambda g: g.objects.append(obj(132, "Fern09", 2200, 200)))
    assert await until(lambda: "Lamp09" in ga.tables() and "Door01" in ga.tables())    # (Ben's from the popups above:
    await settle(ana, ben)                                                           # up with his next apply)
    hall = [wall(2012.5, 12.5, 2312.5, 12.5), wall(2312.5, 12.5, 2312.5, 312.5)]
    v_before = ana.shared[KEY]["v"]

    def ana_hall(g):
        g.walls += hall
        g.height = 350.0
        g.objects.append(obj(131, "Door02", 2100, 12.5))
    await build(ana, ga, ana_hall)
    assert await until(lambda: any(o["table_id"] == "Door02" for o in gb.objects))
    await settle(ana, ben)
    ben_walls = [housesync.wall_key(w) for w in gb.walls]
    assert all(housesync.covered(housesync.wall_key(w), ben_walls) for w in hall) and gb.height == 350.0
    assert not ben.pending(KEY) and ana.shared[KEY]["v"] == v_before + 1 and ana.shared[KEY]["by"] == ana.me_id, \
        "Ben's apply sent a change of its own"
    assert ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    print("walls through the server: built, height set, the door after them; nothing of that sent back: ok")

    # the exact house (the game's property): Ana's leaving build mode saves it and sends it; it's written as a property
    # on Ben's side and applied when his game enters the world again; the same house twice isn't asked about
    import lotsync
    lotsync.MYSITES = TMP / "MySites"
    saved, applied = [], []

    def fake_save(call, site_id, title):
        saved.append(site_id)
        return {"hash": f"h{len(saved)}", "files": {"meta.json": __import__("base64").b64encode(b'{"Title":"x"}').decode(),
                                                    "site.dat": __import__("base64").b64encode(b"DAT").decode()}}
    lotsync.save_lot = fake_save
    lotsync.apply_by_title = lambda call, site_id, title: applied.append(title) or True
    ana.cfg["exact_house"] = True
    for q in [q for q in list(ben.requests.values()) if q.get("action") == "house"]:
        ben.requests.pop(q["id"], None)
    await settle(ana, ben)
    await build(ana, ga, lambda g: g.objects.append(obj(130, "Lamp77", 1, 1)))
    assert await until(lambda: saved and ana.cfg.get("lot_hash") == "h1")
    assert await until(lambda: ben.cfg.get("pending_lot"))                  # (through the server, no question)
    p = ben.cfg.get("pending_lot")
    assert p and p["hash"] == "h1" and (lotsync.MYSITES / p["folder"] / "site.dat").read_bytes() == b"DAT", p
    assert ana.shared["house:1038:exact"]["value"]["hash"] == "h1"
    assert any("comes in when you reopen" in c["text"] for c in ben.chat)
    await settle(ana, ben)
    probe = {"objects": [key(obj(0, "Probe01", 1, 1))], "walls": [], "heights": {}}
    assert ben.session_entry(1038, probe) is not probe       # (the last reading of the home counts...)
    ben.world, ben.in_build_mode = "play", False
    await ben.maybe_apply_pending()
    assert applied == [p["title"]] and not ben.cfg.get("pending_lot") and ben.cfg["lot_hash"] == "h1"
    assert not (lotsync.MYSITES / p["folder"]).exists()
    assert ben.session_entry(1038, probe) is probe, "a reading from before the exact house was applied was used"
    ben.on_shared_house_exact("house:1038:exact", ana.shared["house:1038:exact"], snapshot=True)
    await asyncio.sleep(0.3)
    assert not ben.cfg.get("pending_lot")
    print("exact house: saved by the builder, through the server, applied on reopen, same house ignored: ok")

    # Ben saves his exact house while the server can't be reached (a paint-only session: one that changes the live house
    # waits for the server's word on that before its exact house is saved - races); Ana's newer one gets there first:
    # hers is kept
    await settle(ana, ben)
    ben.cfg["exact_house"] = True
    writer = ben.writer
    ben.writer = None
    await build(ben, gb, lambda g: None)
    assert await until(lambda: ben.dirty(1038, "exact") and ben.cfg.get("lot_hash") == "h2")
    ben.writer = writer
    ben.cfg["exact_house"] = False
    await build(ana, ga, lambda g: g.objects.append(obj(141, "Lamp99", 3, 3)))
    assert await until(lambda: ana.shared["house:1038:exact"]["value"]["hash"] == "h3")
    await settle(ana, ben)
    await ben.push_dirty_house()
    await settle(ana, ben)
    assert ana.shared["house:1038:exact"]["value"]["hash"] == "h3" and not ben.dirty(1038, "exact")
    assert any("exact house" in c["text"] and "theirs is kept" in c["text"] for c in ben.chat)
    assert (ben.cfg.get("pending_lot") or {}).get("hash") == "h3"
    assert ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    print("an exact house saved offline loses to the partner's newer one (said in the chat), the rest is merged: ok")

    # Ana's exact house (her paint, say) waits on Ben's PC for his game's next start (the game lists properties only
    # when it starts); before that, Ben builds on the home: his game doesn't have her house yet, so his exact house would
    # put his older paint back over hers in both games - it isn't saved (said once), hers keeps waiting and comes in on
    # reopening; what he built goes up through the live house as ever, and his next exact house goes on top of hers
    reopened = []

    def apply_until_reopen(call, site_id, title):
        if not reopened:
            raise lotsync.NotListed(title)
        applied.append(title)
        return True
    lotsync.apply_by_title = apply_until_reopen
    exact_key = "house:1038:exact"
    p = ben.cfg.get("pending_lot")
    v_ana = ana.shared[exact_key]["v"]
    assert p and p["hash"] == "h3" and p["v"] == v_ana and p["key"] == exact_key, p
    assert ben.exact_held(exact_key) < v_ana and ben.exact_behind(exact_key)
    ana.cfg["exact_house"], ben.cfg["exact_house"] = False, True
    n_applied, n_saved, since = len(applied), len(saved), time.time()
    for i, t in enumerate(("Lamp55", "Lamp56")):
        await build(ben, gb, lambda g, t=t, i=i: g.objects.append(obj(142 + i, t, 4 + i, 4)))
        assert await until(lambda: t in ga.tables()), "what Ben built didn't go up"
        await settle(ana, ben)
    await asyncio.sleep(0.3)
    assert len(saved) == n_saved, "Ben's exact house was saved without Ana's newer one in it"
    assert ana.shared[exact_key]["v"] == v_ana and ana.shared[exact_key]["value"]["hash"] == "h3", \
        "Ben's exact house went over Ana's newer one"
    assert (ben.cfg.get("pending_lot") or {}).get("hash") == "h3", "Ana's exact house waiting for Ben was dropped"
    assert (lotsync.MYSITES / p["folder"]).exists() and not ana.cfg.get("pending_lot")
    said = [c["text"] for c in ben.chat if c["time"] >= since and "exact house" in c["text"]]
    assert len(said) == 1 and "Ana changed it first" in said[0] and "reopen" in said[0], said
    reopened.append(1)
    ben.world, ben.in_build_mode = "play", False
    reopen(ben)                                              # (Ben's game starts again: hers comes in)
    await ben.maybe_apply_pending()
    assert len(applied) == n_applied + 1 and not ben.cfg.get("pending_lot") and ben.cfg["lot_hash"] == "h3"
    assert ben.exact_held(exact_key) == v_ana and not ben.exact_behind(exact_key)
    await build(ben, gb, lambda g: g.objects.append(obj(144, "Lamp57", 6, 4)))     # (on top of hers now)
    assert await until(lambda: ana.shared[exact_key]["value"]["hash"] == "h4"), ana.shared[exact_key]
    await settle(ana, ben)
    assert ana.shared[exact_key]["by"] == ben.me_id and ana.shared[exact_key]["v"] > v_ana
    assert (ana.cfg.get("pending_lot") or {}).get("hash") == "h4"
    # (only an older one of the same server goes: one kept from another server isn't known to be older)
    ben.cfg["pending_lot"] = dict(p, v=1, scope="another server:")
    ben.on_shared_house_exact(exact_key, ana.shared[exact_key], snapshot=True)
    assert ben.cfg.get("pending_lot"), "an exact house kept from another server was dropped"
    ben.cfg.pop("pending_lot")
    ben.cfg["exact_house"] = False
    assert ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    print("a partner's newer exact house waiting for the next start: mine isn't saved over it (said once), it comes in "
          "on reopening, and my next one goes on top of it: ok")

    # an exact house the game won't take (its apply popup never opens): tried in EXACT_TRIES runs of the game, then
    # left - each try opens build mode and pauses the game -, said once; "apply now" tries again
    tries, was = [], (lotsync.apply_by_title, lotsync.restart_game)

    def apply_fails(call, site_id, title):
        tries.append(title)
        raise lotsync.LotError("the apply popup didn't open")
    lotsync.apply_by_title = apply_fails
    ben.cfg["pending_lot"] = {"title": "inZOI Co-op Ana old", "folder": "OLD", "site_id": "S", "hash": "hx",
                              "from": "Ana", "at": time.time()}
    since = time.time()
    for _ in range(housesync.EXACT_TRIES + 2):
        reopen(ben)
        ben.world, ben.in_build_mode = "play", False
        await ben.maybe_apply_pending()
    assert len(tries) == housesync.EXACT_TRIES, tries
    said = [c["text"] for c in ben.chat if c["time"] >= since and "apply now" in c["text"]]
    assert len(said) == 1, said
    ben.world = "play"
    lotsync.restart_game = lambda call: True
    assert (await ben.apply_lot_now())["ok"] and "fails" not in ben.cfg["pending_lot"]
    reopen(ben)
    await ben.maybe_apply_pending()
    assert len(tries) == housesync.EXACT_TRIES + 1
    ben.cfg.pop("pending_lot")
    lotsync.apply_by_title, lotsync.restart_game = was
    print("an exact house the game won't take is tried a bounded number of times, said once, and again on apply now: ok")

    # one upload of the exact house at a time: the shared loop finding it unconfirmed while it's on its way (megabytes
    # on a slow link) doesn't send it again, and a newer save of the house made meanwhile goes once that one is
    # answered - two versions, not three; and one the server has already (its word came late) isn't sent again
    await settle(ana, ben)
    ana.world, ana.in_build_mode = "play", False
    reopen(ana)                                              # (Ana's game starts again and takes Ben's exact house:
    await ana.maybe_apply_pending()                          # hers can go on top of it now)
    assert not ana.cfg.get("pending_lot") and not ana.exact_behind(exact_key)
    sets, real_send = [], ana.send

    async def slow_send(msg):
        if msg.get("t") == "set" and str(msg.get("key")).endswith(":exact"):
            sets.append((msg["value"]["hash"], msg.get("base")))
            await asyncio.sleep(1.5)                         # (the property takes that long to reach the server)
        await real_send(msg)
    ana.send, ana.cfg["exact_house"] = slow_send, True
    await build(ana, ga, lambda g: g.objects.append(obj(143, "Lamp66", 5, 5)))
    assert await until(lambda: sets)
    await ana.push_dirty_house()                             # (the shared loop's turn, while it's on its way)
    await ana.capture_lot({"site": 1038, "site_id": "RedCity_Residence_Free03"})    # (a newer save meanwhile)
    assert await until(lambda: len(sets) >= 2 and not ana.exact_pushing, 15)
    await settle(ana, ben)
    await asyncio.sleep(0.5)
    assert len(sets) == 2 and sets[0][0] != sets[1][0], sets
    assert ana.shared[exact_key]["value"]["hash"] == sets[1][0] and not ana.dirty(1038, "exact")
    v_exact = ana.shared[exact_key]["v"]
    ana.set_dirty(1038, "exact", ana.exact_mark(v_exact - 1))     # (taken as lost: the server had it all along)
    await ana.push_dirty_house()
    assert len(sets) == 2 and not ana.dirty(1038, "exact") and ana.shared[exact_key]["v"] == v_exact, sets
    # a mark left by an older program, on a version this server never had (no scope): sent once, on top of the
    # server's current one (my own), not refused over and over (each try megabytes)
    ana.preset_file.write_text(json.dumps(dict(ana.saved_preset(1038), hash="h-legacy")), "utf-8")
    ana.set_dirty(1038, "exact", {"base": 10 ** 6})
    await ana.push_dirty_house()
    assert sets[2:] == [("h-legacy", v_exact)] and not ana.dirty(1038, "exact"), sets
    assert ana.shared[exact_key]["value"]["hash"] == "h-legacy"
    ana.send, ana.cfg["exact_house"] = real_send, False
    assert await until(lambda: (ben.cfg.get("pending_lot") or {}).get("hash") == "h-legacy")
    await settle(ana, ben)
    print("the exact house: one upload at a time, the newest saved one after it, never the same one twice: ok")

    # Ana's exact house kept on Ben's PC for his game's next start: tried in the running game (not listed: the game
    # lists properties only when it starts), not again in that run, and applied once the game has started again - the
    # companion running all along
    listed, tries = set(), []

    def apply_listed(call, site_id, title):
        tries.append(title)
        if title not in listed:
            raise lotsync.NotListed(title)
        applied.append(title)
        return True
    lotsync.apply_by_title = apply_listed
    p = ben.cfg["pending_lot"]
    for _ in range(40):                                      # (the 2 s loop, at home in play mode: until it's tried,
        await ben.house_tick()                               # however long the home's own work takes first)
        if tries:
            break
        await asyncio.sleep(0.25)
    for _ in range(3):
        await ben.house_tick()                               # (and not again in this run of the game)
    assert tries == [p["title"]] and ben.cfg.get("pending_lot"), tries
    ben.world = "out"                                        # (Ben closes the game and starts it again)
    listed.add(p["title"])                                   # (written before this start: the game lists it)
    ben.world = "play"
    reopen(ben)
    await ben.house_tick()                                   # (the home's first reading in this load: the live house
    await settle(ana, ben)                                   # comes in first - what that reading found the server
    await ben.house_tick()                                   # doesn't know goes up -, then the property)
    assert tries == [p["title"]] * 2 and applied[-1] == p["title"], tries
    assert not ben.cfg.get("pending_lot") and ben.cfg["lot_hash"] == p["hash"]
    await settle(ana, ben)                                   # (and the live house again, on top of the property)
    print("a partner's exact house kept for the next start: applied when the game starts again: ok")

    # Ana's exact house waits on Ben's PC for the next start; Ben builds while the server can't be reached and restarts
    # the game before it's back: no exact house of his is saved without hers in it, so nothing holds hers back - it
    # comes in on reopening (it's the server's: the newer one is kept) -, and what he built goes up once the server is
    # back. An exact house saved meanwhile by an older version of this program (its mark: the newest version it had
    # heard of - hers -, not what the game held) neither holds hers back nor goes over it once hers is in the game
    ana.cfg["exact_house"] = True
    await build(ana, ga, lambda g: g.objects.append(obj(145, "Lamp33", 7, 7)))
    assert await until(lambda: (ben.cfg.get("pending_lot") or {}).get("hash") == ana.shared[exact_key]["value"]["hash"])
    await settle(ana, ben)
    ana.cfg["exact_house"] = False
    p, v_ana, n_saved = ben.cfg["pending_lot"], ana.shared[exact_key]["v"], len(saved)
    listed.add(p["title"])
    writer = ben.writer
    ben.writer, ben.cfg["exact_house"] = None, True
    await build(ben, gb, lambda g: g.objects.append(obj(146, "Lamp22", 8, 8)))
    await asyncio.sleep(0.3)
    assert len(saved) == n_saved and not ben.dirty(1038, "exact") and ben.pending(KEY), (saved, ben.cfg["house_dirty"])
    ben.preset_file.write_text(json.dumps(dict(ben.saved_preset(1038), hash="h-old-program")), "utf-8")
    ben.cfg["house_dirty"]["1038:exact"] = {"base": v_ana}  # (as an older version marked it: no scope)
    ben.cfg["exact_house"] = False
    n_tries = len(tries)
    ben.world = "out"
    ben.world = "play"
    reopen(ben)                                              # (the game started again, still offline)
    for _ in range(40):                                      # (ticks as the program's: the home's again-apply of the
        await ben.house_tick()                               # start goes first, then the exact house - however long
        if len(tries) > n_tries:                             # that takes on a busy PC)
            break
        await asyncio.sleep(0.25)
    assert len(tries) == n_tries + 1 and not ben.cfg.get("pending_lot") and ben.cfg["lot_hash"] == p["hash"], \
        "Ana's newer exact house was held back"
    assert ben.exact_held(exact_key) == v_ana
    ben.writer = writer
    since = time.time()
    await ben.push_dirty_house()
    assert await until(lambda: "Lamp22" in ga.tables()), "what Ben built offline didn't go up"
    await settle(ana, ben)
    assert ana.shared[exact_key]["value"]["hash"] == p["hash"] and ana.shared[exact_key]["v"] == v_ana, \
        "an exact house of the older program went over Ana's"
    assert not ben.dirty(1038, "exact") and not ana.cfg.get("pending_lot") and not ben.cfg.get("pending_lot")
    assert any("theirs is kept" in c["text"] for c in ben.chat if c["time"] >= since)
    assert await until(lambda: ga.tables() == gb.tables()), (ga.tables(), gb.tables())
    await settle(ana, ben)
    print("a partner's exact house waiting for the next start: no offline save of mine (nor an older program's mark) "
          "holds it back or goes over it; what I built goes up: ok")

    # an exact house the server doesn't confirm (it couldn't keep it - a full disk answers storage_failed, a lost set
    # here - or its word didn't come): not uploaded again on every turn of the shared loop (megabytes each time), but
    # after PUSH_WAIT, longer each time; a newer save of the house goes at once, and the wait ends once one is confirmed
    await settle(ana, ben)
    ups, real_ex = [], ana.share_ex

    async def no_room(key_, value, base=None):
        if key_.endswith(":exact"):
            ups.append(value["hash"])
            return "lost"
        return await real_ex(key_, value, base)
    ana.share_ex = no_room
    on = {"v": housesync.version_of(ana.shared.get(KEY)), "scope": ana.shared_scope_id()}     # (saved on the house now)
    ana.preset_file.write_text(json.dumps(dict(ana.saved_preset(1038), hash="h-full", live=on)), "utf-8")
    await ana.publish_exact(ana.saved_preset(1038))
    for _ in range(4):
        await ana.push_dirty_house()                         # (the shared loop's turns, every few seconds)
    assert ups == ["h-full"] and ana.dirty(1038, "exact"), ups
    ana.exact_push_at[exact_key]["at"] = 0                   # (PUSH_WAIT[0] later)
    for _ in range(3):
        await ana.push_dirty_house()
    w = ana.exact_push_at[exact_key]
    assert ups == ["h-full"] * 2 and w["n"] == 2 and w["at"] - time.time() > housesync.PUSH_WAIT[0], (ups, w)
    await ana.publish_exact(ana.saved_preset(1038))          # (build mode left again, nothing changed: the same save)
    assert ups == ["h-full"] * 2, ups
    await ana.capture_lot({"site": 1038, "site_id": "RedCity_Residence_Free03"})    # (a newer save of the house)
    assert len(ups) == 3 and ups[2] != "h-full" and ana.exact_push_at[exact_key]["n"] == 1, (ups, ana.exact_push_at)
    ana.share_ex = real_ex
    await ana.push_dirty_house()
    assert len(ups) == 3 and ana.dirty(1038, "exact"), "sent again before its wait was over"
    ana.exact_push_at[exact_key]["at"] = 0
    await ana.push_dirty_house()
    assert not ana.dirty(1038, "exact") and exact_key not in ana.exact_push_at, ana.exact_push_at
    assert ana.shared[exact_key]["value"]["hash"] == ups[2]
    await settle(ana, ben)
    print("an exact house the server doesn't confirm: sent again after a wait, longer each time; a newer one at once: "
          "ok")

    # an apply under way when the connection moves to another server: that version's number (the old server's) isn't
    # counted on the new one - else the new server's house, at a lower number, would be ignored - and it comes in
    await settle(ana, ben)
    gb.slow = 0.3
    await build(ana, ga, lambda g: g.objects.extend(obj(170 + i, f"Crate{i:02}", 1400 + 60 * i, 100) for i in range(3)))
    assert await until(lambda: ben.applying_house and gb.count("building.place_object", object_table_id="Crate00"))
    v_old = ben.shared[KEY]["v"]
    other = dict(ben.shared[KEY], v=5, by="someone-else", by_name="Ana")
    other["value"] = dict(other["value"], objects=other["value"]["objects"] + [key(obj(0, "Desk01", 1500, 400))])
    assert v_old > 5
    ben.cfg["server_id"] = "another-server"
    ben.on_shared_snapshot({KEY: other}, 5, "another-epoch")      # (the other server's welcome, mid-apply)
    await settle(ben, timeout=30)
    gb.slow = 0.0
    assert "Crate02" in gb.tables() and "Desk01" in gb.tables(), "the new server's house was ignored"
    base = ben.hs().get("base") or {}
    assert ben.seen(KEY) == 5 and base.get("v") == 5 and base.get("scope") == ben.shared_scope_id(), \
        (ben.seen(KEY), base.get("v"), base.get("scope"))
    print("an apply that finishes after moving to another server: its old number isn't counted there: ok")
    for t in tasks:
        t.cancel()
    srv.close()
    await rollback_and_restart()
    await conflicts()
    await races()
    await joining()
    await regressions()
    print("house sync tests passed")


async def rollback_and_restart():
    """A restart of this program, save-quit-load, and an older save loaded while this program was down; the exact house
    (on) of a home with something kept only in that game. Two new players on a server of their own."""
    import base64
    import hashlib
    import inzoi_mp_server as mp
    import lotsync
    lotsync.MYSITES = TMP / "MySites-rollback"
    games = {}

    def fake_save(call, site_id, title):                 # (the property: the whole lot as the game holds it)
        blob = json.dumps(sorted((key(o) for o in games[call].objects), key=json.dumps)).encode()
        return {"hash": hashlib.sha1(blob).hexdigest()[:8],
                "files": {"site.dat": base64.b64encode(blob).decode(),
                          "meta.json": base64.b64encode(b'{"Title":"x"}').decode()}}

    def fake_apply(call, site_id, title):
        for folder in (p for p in lotsync.MYSITES.iterdir() if p.is_dir()):
            if json.loads((folder / "meta.json").read_text("utf-8")).get("Title") == title:
                lot = json.loads((folder / "site.dat").read_bytes())
                games[call].objects = [obj(3000 + i, o["table_id"], o["x"], o["y"], o["yaw"], o["floor"], o["z"])
                                       for i, o in enumerate(lot)]
                return True
        raise lotsync.NotListed(title)
    lotsync.save_lot, lotsync.apply_by_title = fake_save, fake_apply

    server = mp.Server(mp.Store(TMP / "server-rollback"))
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
    port = srv.sockets[0].getsockname()[1]
    home = [obj(1, "Sofa01", 100, 100), obj(2, "Lamp01", 300, 50)]
    ga, gb = StandIn([dict(o) for o in home]), StandIn([dict(o) for o in home])
    eve, finn = player("Eve", port, ga), player("Finn", port, gb)
    games[ga.call], games[gb.call] = ga, gb
    tasks = [asyncio.create_task(eve.session()), asyncio.create_task(finn.session())]
    assert await until(lambda: eve.me_id and finn.me_id and eve.shared_conn is eve.writer
                       and finn.shared_conn is finn.writer)
    eve.last_full_state = finn.last_full_state = {"site": 1038}
    await build(eve, ga, lambda g: g.objects.append(obj(50, "Plant01", 600, 600)))
    assert await until(lambda: "Plant01" in gb.tables())
    await settle(eve, finn)
    for p in (eve, finn):
        await p.house_tick()
    await settle(eve, finn)

    def restarted_here(p):
        """This program started again with the game in the same world (watch_save's "still in the same world as
        before this program restarted" branch): a new save_session, no load seen - nor known not to have happened."""
        s = p.save_session
        p.save_session = {"started": max(time.time(), s["started"] + 0.001), "folder": s.get("folder"),
                          "checked": time.time(), "where": s.get("where"), "game": s.get("game"), "mp": True}
        p.was_home = None

    async def ticks(p, n=3):
        for _ in range(n):
            await p.house_tick()                         # (the 2 s loop, at home in play mode)
            await settle(eve, finn)

    # bought in play mode, not read yet (the reading is every 5 min); this program restarts, the game stays in the world
    ga.objects.append(obj(60, "Kettle01", 720, 720))
    restarted_here(eve)
    await ticks(eve)
    assert await until(lambda: "Kettle01" in gb.tables()), "a purchase from before a restart of this program never " \
        "reached the partner"
    # bought, then the game saved, went to the menu and loaded that same newest save
    ga.objects.append(obj(61, "Toaster01", 740, 720))
    eve.world = "out"
    await eve.house_tick()
    eve.world, eve.game.saw_lobby = "play", True
    await eve.house_tick()
    reopen(eve, restarted=False)
    await ticks(eve)
    assert await until(lambda: "Toaster01" in gb.tables()), "a purchase saved and loaded again never reached the partner"
    await settle(eve, finn)
    assert not [c for c in eve.chat if "kept in your game only" in c["text"]], list(eve.chat)[-3:]
    assert ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    print("bought in play mode, then this program restarted or the newest save loaded again: it reaches the partner: ok")

    # Finn deletes the lamp; Eve's game is brought to it. While Eve's program is down she loads an older save (the lamp
    # is in it again) and buys a cup; then her program starts, the game in that world
    older = [dict(o) for o in ga.objects]
    await build(finn, gb, lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] != "Lamp01"]))
    assert await until(lambda: "Lamp01" not in ga.tables())
    await settle(eve, finn)
    since = time.time()
    ga.objects = older + [obj(62, "Cup01", 760, 720)]
    restarted_here(eve)
    await ticks(eve)
    assert await until(lambda: "Cup01" in gb.tables()), "a purchase in the save loaded never reached the partner"
    await settle(eve, finn)
    on_server = sorted(o["table_id"] for o in eve.shared[KEY]["value"]["objects"])
    assert "Lamp01" not in on_server and "Lamp01" not in gb.tables(), "the older save put back what Finn deleted"
    assert "Lamp01" in ga.tables()
    said = [c for c in eve.chat if c["time"] >= since and "taken out of the shared house" in c["text"]]
    assert len(said) == 1 and "kept in your game only" in said[0]["text"], said
    print("an older save loaded while this program was down: what the partner deleted stays only in that game, a "
          "purchase goes up: ok")

    # Eve builds on the home with the exact house on (the default): it isn't saved while her home has the lamp kept
    # only in her game (the property is the whole lot: it would put the lamp back in Finn's game), said once
    eve.cfg["exact_house"] = True
    saved_before = eve.cfg.get("lot_hash")
    for i, t in enumerate(("Rug01", "Vase01")):
        await build(eve, ga, lambda g, t=t, i=i: g.objects.append(obj(70 + i, t, 900 + 40 * i, 900)))
        assert await until(lambda: t in gb.tables())
        await settle(eve, finn)
    await asyncio.sleep(0.3)
    assert eve.cfg.get("lot_hash") == saved_before and not eve.shared.get("house:1038:exact"), \
        "an exact house with the lamp kept only in Eve's game was saved"
    assert not finn.cfg.get("pending_lot"), finn.cfg.get("pending_lot")
    said = [c for c in eve.chat if "exact house" in c["text"] and "kept only in your game" in c["text"]]
    assert len(said) == 1, said
    reopen(finn)                                         # (Finn's game starts again: nothing of Eve's to apply)
    finn.world, finn.in_build_mode = "play", False
    await finn.maybe_apply_pending()
    assert "Lamp01" not in gb.tables()
    # (nor is it in her whole home as it goes up to a server that has no house yet: a new one, or one started over)
    lot = eve.saved_lot(1038)
    assert "Lamp01" in [o["table_id"] for o in lot["objects"]]
    whole, _, _ = eve.house_value({"lot": lot, "deltas": [housesync.added_delta([])]}, None)
    tables = sorted(o["table_id"] for o in whole["objects"])
    assert "Lamp01" not in tables and "Rug01" in tables, tables
    # she takes the lamp out: the exact house is saved and goes again; Finn's next start applies it, without the lamp
    await build(eve, ga, lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] != "Lamp01"]))
    assert await until(lambda: (finn.cfg.get("pending_lot") or {}).get("hash")), "the exact house didn't go again"
    await settle(eve, finn)
    assert finn.cfg["pending_lot"]["hash"] == eve.cfg.get("lot_hash") != saved_before
    assert not eve.hs().get("local_only")
    reopen(finn)
    finn.world, finn.in_build_mode = "play", False
    await finn.maybe_apply_pending()
    assert not finn.cfg.get("pending_lot") and "Lamp01" not in gb.tables(), gb.tables()
    assert ga.tables() == gb.tables(), (ga.tables(), gb.tables())
    print("the exact house isn't saved while the home has something kept only in that game (said once); taken out, it "
          "goes again: ok")
    for t in tasks:
        t.cancel()
    srv.close()


async def conflicts():
    """Changes that cross. Two new players on a server of their own; the exact house's stand-in carries the paint (what
    only the property has) and the objects.
    - a move made while the server can't be reached, beaten by the partner's move or removal of the same thing: one
      copy in each game and on the server (theirs), said once - with the partner's version coming in before mine goes
      up, or mine going up while that version is being put in
    - a change that got to the server with its word lost, which the partner then took back out: not sent again on the
      way back (it stays out of both games)
    - the exact house of a game that hasn't taken the partner's newer one yet (kept for its next start): not saved over
      theirs (said once); theirs comes in on reopening, in both games; a paint-only build session right after a restart
      still goes up
    - both in build mode: my exact house waits until my game has my partner's removal, and goes without it
    - build mode opened right after loading an older save: no exact house with what the partner had deleted"""
    import base64
    import inzoi_mp_server as mp
    import lotsync
    lotsync.MYSITES = TMP / "MySites-conflicts"
    games, stash, listed = {}, {}, set()
    real_wp = lotsync.write_pending

    def fake_save(call, site_id, title):
        g = games[call]
        blob = json.dumps({"paint": g.paint, "objects": sorted((key(o) for o in g.objects), key=json.dumps)}).encode()
        return {"hash": __import__("hashlib").sha1(blob).hexdigest()[:8],
                "files": {"site.dat": base64.b64encode(blob).decode(),
                          "meta.json": base64.b64encode(b'{"Title":"x"}').decode()}}

    def keep_pending(files, title):                      # (each PC its own: the other's cleanup can't take it)
        stash[title] = json.loads(base64.b64decode(files["site.dat"]))
        return real_wp(files, title)

    def fake_apply(call, site_id, title):
        if title not in listed:
            raise lotsync.NotListed(title)               # (the game lists properties only when it starts)
        lot, g = stash[title], games[call]
        g.paint = lot["paint"]
        g.objects = [obj(4000 + i, o["table_id"], o["x"], o["y"], o["yaw"], o["floor"], o["z"])
                     for i, o in enumerate(lot["objects"])]
        return True
    lotsync.save_lot, lotsync.apply_by_title, lotsync.write_pending = fake_save, fake_apply, keep_pending
    exact_key = "house:1038:exact"

    def exact_paint(p):
        it = p.shared.get(exact_key)
        return it and json.loads(base64.b64decode(it["value"]["files"]["site.dat"]))["paint"]

    def spots(g, table="Table03"):
        return sorted((o["location"]["x"], o["location"]["y"]) for o in g.objects if o["table_id"] == table)

    def server_spots(p, table="Table03"):
        return sorted((o["x"], o["y"]) for o in p.shared[KEY]["value"]["objects"] if o["table_id"] == table)

    def move(to):
        return lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] != "Table03"]
                                 + [obj(900 + int(to[0]), "Table03", to[0], to[1], 90)])

    def kept_said(p, since):
        return [c["text"] for c in p.chat if c["time"] >= since and "weren't kept" in c["text"]]

    server = mp.Server(mp.Store(TMP / "server-conflicts"))
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
    port = srv.sockets[0].getsockname()[1]
    home = [obj(1, "Sofa01", 100, 100), obj(2, "Lamp01", 300, 50), obj(3, "Table03", 500, 500, 90)]
    ga, gb = StandIn([dict(o) for o in home]), StandIn([dict(o) for o in home])
    ga.paint = gb.paint = "white"
    eve, finn = player("Eve", port, ga), player("Finn", port, gb)
    games[ga.call], games[gb.call] = ga, gb
    tasks = {"eve": asyncio.create_task(eve.session()), "finn": asyncio.create_task(finn.session())}
    try:
        assert await until(lambda: eve.me_id and finn.me_id and eve.shared_conn is eve.writer
                           and finn.shared_conn is finn.writer)
        eve.last_full_state = finn.last_full_state = {"site": 1038}
        await build(eve, ga, lambda g: g.objects.append(obj(50, "Plant01", 600, 600)))
        assert await until(lambda: "Plant01" in gb.tables())
        await settle(eve, finn)
        for p in (eve, finn):
            await p.house_tick()
        await settle(eve, finn)

        # Finn moves the table while the server can't be reached; Eve moves it too; her version comes into his game
        # before his move goes up: his move is beaten - his copy goes, hers is placed (not both), said once
        writer, since = finn.writer, time.time()
        finn.writer = None
        await build(finn, gb, move((480, 400)))
        assert finn.pending(KEY)
        await build(eve, ga, move((520, 600)))
        assert await until(lambda: spots(gb) == [(520, 600)]), spots(gb)
        await settle(eve, finn)
        assert not finn.pending(KEY) and not finn.dirty(1038, "live"), finn.hs().get("pending")
        assert len(kept_said(finn, since)) == 1, list(finn.chat)[-4:]
        v_before = eve.shared[KEY]["v"]
        finn.writer = writer
        finn.house_push_at = 0.0
        await finn.push_dirty_house()
        await settle(eve, finn)
        assert eve.shared[KEY]["v"] == v_before and server_spots(eve) == spots(ga) == spots(gb) == [(520, 600)]
        assert len(kept_said(finn, since)) == 1 and ga.tables() == gb.tables(), (ga.tables(), gb.tables())

        # ...and when his move goes up while her version is being put in his game (the shared loop's turn comes right
        # after the plan): still one table each, on the server too, and the next version doesn't change that
        since = time.time()
        finn.writer = None
        await build(finn, gb, move((450, 650)))
        gb.slow = 0.3
        pushed = []

        def plan_then_push(q, mine, hold=True, state=None):
            out = housesync.HouseSync.plan_for(finn, q, mine, hold, state)
            if finn.applying_house and not pushed:
                pushed.append(1)
                finn.writer, finn.house_push_at = writer, 0.0
                finn.house_task(finn.push_dirty_house())
            return out
        finn.plan_for = plan_then_push
        await build(eve, ga, move((560, 640)))
        assert await until(lambda: pushed, 10)
        await settle(eve, finn, timeout=30)
        gb.slow = 0.0
        del finn.plan_for
        assert spots(gb) == spots(ga) == server_spots(eve) == [(560, 640)], (spots(ga), spots(gb), server_spots(eve))
        assert not finn.house_undo(KEY) and not finn.pending(KEY) and len(kept_said(finn, since)) == 1
        await build(eve, ga, lambda g: g.objects.append(obj(51, "Rug07", 300, 600)))
        assert await until(lambda: "Rug07" in gb.tables())
        await settle(eve, finn)
        assert spots(gb) == spots(ga) == [(560, 640)] and ga.tables() == gb.tables(), (ga.tables(), gb.tables())

        # ...and when she deleted the table instead: it's gone from his game too
        finn.writer = None
        await build(finn, gb, move((600, 700)))
        await build(eve, ga, lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] != "Table03"]))
        assert await until(lambda: not spots(gb)), spots(gb)
        await settle(eve, finn)
        finn.writer, finn.house_push_at = writer, 0.0
        await finn.push_dirty_house()
        await settle(eve, finn)
        assert not spots(ga) and not spots(gb) and not server_spots(eve) and ga.tables() == gb.tables()
        print("a move made offline, beaten by the partner's move or removal (her version in first, or mine going up "
              "during its apply): one copy everywhere, said once: ok")

        # Eve adds a chair; the server takes it, but her link goes before its word comes back; Finn deletes the chair;
        # Eve's link comes back: her change, which got there, isn't sent again - the chair stays out of both games
        real_msg = eve.on_shared_msg

        def swallow(m):
            if m.get("key") == KEY and m.get("ref_by") == eve.me_id:
                eve.writer.transport.abort()             # (the link is gone: nothing more comes in)
                return
            real_msg(m)
        eve.on_shared_msg = swallow
        await build(eve, ga, lambda g: g.objects.append(obj(60, "Chair09", 800, 800)))
        assert await until(lambda: tasks["eve"].done()), "Eve's session didn't end"
        eve.on_shared_msg = real_msg
        eve.writer, eve.me_id = None, None               # (as run() does after a session)
        assert await until(lambda: "Chair09" in gb.tables()), "the server didn't have Eve's chair"
        await settle(finn)
        since = time.time()
        await build(finn, gb, lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] != "Chair09"]))
        await settle(finn)
        assert "Chair09" not in [o["table_id"] for o in finn.shared[KEY]["value"]["objects"]]
        v_deleted = finn.shared[KEY]["v"]
        tasks["eve"] = asyncio.create_task(eve.session())
        assert await until(lambda: eve.me_id and eve.shared_conn is eve.writer)
        await settle(eve, finn)
        eve.house_push_at = 0.0
        await eve.push_dirty_house()                     # (the shared loop's turn)
        await settle(eve, finn)
        assert finn.shared[KEY]["v"] == v_deleted, "Eve's change that got there before was sent again"
        assert "Chair09" not in gb.tables() and "Chair09" not in ga.tables(), (ga.tables(), gb.tables())
        assert not eve.pending(KEY) and not eve.dirty(1038, "live") and not kept_said(eve, since)
        assert ga.tables() == gb.tables(), (ga.tables(), gb.tables())
        print("a change that got to the server, its word lost, then taken back out by the partner: not sent again: ok")

        # the exact house (on for both): in step first - Eve's goes up, Finn's game takes it on its next start
        eve.cfg["exact_house"] = finn.cfg["exact_house"] = True
        await build(eve, ga, lambda g: g.objects.append(obj(61, "Vase01", 40, 900)))
        assert await until(lambda: (finn.cfg.get("pending_lot") or {}).get("from") == "Eve")
        await settle(eve, finn)

        async def restart(p):
            listed.update(stash)                         # (written before this start: the game lists them)
            p.world, p.in_build_mode = "play", False
            reopen(p)
            await p.maybe_apply_pending()

        await restart(finn)
        assert not finn.cfg.get("pending_lot") and gb.paint == "white"
        # Finn paints the walls blue right after his game started (build mode before the first reading of the home):
        # only the property has that, and it still goes up
        await build(finn, gb, lambda g: setattr(g, "paint", "blue"))
        assert await until(lambda: exact_paint(eve) == "blue" and eve.shared[exact_key]["by"] == finn.me_id), \
            "a paint-only build session right after a restart never went up"
        assert await until(lambda: (eve.cfg.get("pending_lot") or {}).get("from") == "Finn")
        await settle(eve, finn)
        # Eve, the same evening (her game not restarted: Finn's house waits for her next start), puts a rug down: her
        # exact house (white walls) isn't saved over his, his keeps waiting, said once; the rug goes up as ever
        v_finn, since = eve.shared[exact_key]["v"], time.time()
        await build(eve, ga, lambda g: g.objects.append(obj(62, "Rug55", 900, 900)))
        assert await until(lambda: "Rug55" in gb.tables())
        await settle(eve, finn)
        await asyncio.sleep(0.3)
        assert eve.shared[exact_key]["v"] == v_finn and exact_paint(eve) == "blue", "Eve's white walls went over"
        assert (eve.cfg.get("pending_lot") or {}).get("from") == "Finn" and not finn.cfg.get("pending_lot")
        said = [c["text"] for c in eve.chat if c["time"] >= since and "exact house" in c["text"]]
        assert len(said) == 1 and "Finn changed it first" in said[0], said
        for p in (eve, finn):
            await restart(p)
        assert ga.paint == gb.paint == "blue", (ga.paint, gb.paint)
        refresh, housesync.REFRESH_EVERY = housesync.REFRESH_EVERY, 0
        try:
            for _ in range(2):
                await eve.house_tick()                   # (her first reading: the live house is brought in again)
                await settle(eve, finn)
        finally:
            housesync.REFRESH_EVERY = refresh
        assert "Rug55" in ga.tables() and ga.tables() == gb.tables(), (ga.tables(), gb.tables())
        await build(eve, ga, lambda g: g.objects.append(obj(63, "Book01", 60, 900)))    # (on top of his now)
        assert await until(lambda: eve.shared[exact_key]["by"] == eve.me_id and eve.shared[exact_key]["v"] > v_finn)
        assert exact_paint(eve) == "blue"
        await settle(eve, finn)
        await restart(finn)
        print("the exact house of a game that hasn't taken the partner's newer one: not saved over it (said once), "
              "theirs in both games on reopening; paint-only right after a restart goes up: ok")

        # both in build mode on the home: Finn opens it first; Eve (exact house off) deletes the lamp and leaves; then
        # Finn places a rug and leaves - his game still has the lamp (her version waits for his build mode to end): his
        # exact house waits until it's in, and goes without the lamp
        eve.cfg["exact_house"] = False
        refresh, housesync.REFRESH_EVERY = housesync.REFRESH_EVERY, 0
        try:
            for _ in range(2):
                await finn.house_tick()                  # (his first reading since the restart: his game is known
                await settle(eve, finn)                  # to be at the shared house again)
        finally:
            housesync.REFRESH_EVERY = refresh
        assert not finn.exact_behind(exact_key) and not finn.exact_hold(KEY), finn.exact_hold(KEY)
        gb.phase = "ready"
        await finn.house_tick()
        await build(eve, ga, lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] != "Lamp01"]))
        await settle(eve)
        assert await until(lambda: finn.house_next)
        assert "Lamp01" in gb.tables()
        gb.objects.append(obj(64, "Rug77", 950, 950))
        finn.build_site["at"] = 0
        await finn.house_tick()
        gb.phase = "none"
        v_before = eve.shared[exact_key]["v"]
        await finn.house_tick()
        assert await until(lambda: eve.shared[exact_key]["v"] > v_before), "Finn's exact house never went up"
        await settle(eve, finn)
        it = eve.shared[exact_key]
        tables = [o["table_id"] for o in json.loads(base64.b64decode(it["value"]["files"]["site.dat"]))["objects"]]
        assert it["by"] == finn.me_id and "Lamp01" not in tables and "Rug77" in tables, tables
        await restart(eve)
        assert "Lamp01" not in ga.tables() and "Rug77" in ga.tables(), ga.tables()
        print("both in build mode: my exact house waits until my game has my partner's removal: ok")

        # Finn's game loads an older save (the lamp is in it again) and he opens build mode on the home at once - before
        # the first reading that brings his game to the shared house -, places a stool and leaves: no exact house of
        # his with the lamp Eve deleted (said once); the live house doesn't get the lamp either
        await settle(eve, finn)
        refresh, housesync.REFRESH_EVERY = housesync.REFRESH_EVERY, 0
        try:
            for _ in range(2):
                await eve.house_tick()
                await finn.house_tick()
                await settle(eve, finn)
        finally:
            housesync.REFRESH_EVERY = refresh
        autosave = [dict(o) for o in gb.objects] + [obj(65, "Lamp01", 300, 50)]
        v_before, since = eve.shared[exact_key]["v"], time.time()
        finn.world = "out"
        gb.objects = autosave
        finn.world = "play"
        reopen(finn, restarted=False)
        await build(finn, gb, lambda g: g.objects.append(obj(66, "Stool01", 980, 400)))
        assert await until(lambda: "Stool01" in ga.tables())
        await settle(eve, finn)
        for _ in range(3):
            await finn.house_tick()
            await settle(eve, finn)
        assert eve.shared[exact_key]["v"] == v_before, "Finn's exact house with the lamp Eve deleted went up"
        assert "Lamp01" not in [o["table_id"] for o in eve.shared[KEY]["value"]["objects"]]
        assert "Lamp01" not in ga.tables() and not eve.cfg.get("pending_lot")
        said = [c["text"] for c in finn.chat if c["time"] >= since and "kept only in your game" in c["text"]]
        assert len(said) == 1, said
        print("build mode right after loading an older save: no exact house with what the partner deleted: ok")
    finally:
        lotsync.write_pending = real_wp
        for t in tasks.values():
            t.cancel()
        srv.close()


async def races():
    """What crosses the exact house (the game's property: saving it reopens build mode for seconds, applying it too)
    and the first version of the house. Players of their own, on servers of their own; the property's stand-in carries
    the paint and the objects.
    - my partner's change that reaches my PC while my exact house is being saved: that save isn't sent (it hasn't the
      change), it's saved again once the change is in; and right as build mode is left (my change not confirmed yet)
      it waits for the server's word
    - an exact house saved offline whose live house changed meanwhile: not sent as it is, saved again with the change
    - my partner's newer exact house that comes while the older one is being applied: kept for the next start (and the
      property the game is reading stays meanwhile); my exact house isn't held back by it afterwards
    - what I took out or moved after my partner's exact house was kept for the next start: that start brings it back,
      the live house (newer) takes it out again - nothing kept "only in my game", my exact house shared after
    - something bought before the server had any version of the house (a new server, then one whose shared state
      started over) reaches my partner with the first version"""
    import base64
    import hashlib
    import itertools
    import shutil
    import inzoi_mp_server as mp
    import lotsync
    games, props, numbers = {}, {}, itertools.count(1)
    real = (lotsync.save_lot, lotsync.apply_by_title, lotsync.write_pending, lotsync.cleanup)

    def fake_save(call, site_id, title):
        g = games[call]
        time.sleep(g.save_delay)                         # (build mode reopens, the game writes the property)
        blob = json.dumps({"paint": g.paint, "objects": sorted((key(o) for o in g.objects), key=json.dumps)}).encode()
        return {"hash": hashlib.sha1(blob).hexdigest()[:8],
                "files": {"site.dat": base64.b64encode(blob).decode(),
                          "meta.json": base64.b64encode(b'{"Title":"x"}').decode()}}

    def fake_write(files, title):
        folder = f"P{next(numbers):04}"
        props[folder] = {"title": title, "lot": json.loads(base64.b64decode(files["site.dat"])), "at": time.time()}
        return folder

    def fake_cleanup(keep=()):
        for f in [f for f in props if f not in keep]:
            del props[f]

    def fake_apply(call, site_id, title):
        g = games[call]
        folder = next((f for f, p in props.items() if p["title"] == title and p["at"] < g.started), None)
        if folder is None:
            raise lotsync.NotListed(title)               # (the game lists properties only when it starts)
        lot = props[folder]["lot"]
        time.sleep(g.apply_delay)                        # (read; putting it in and leaving build mode take seconds)
        g.kept_reading.append(folder in props)           # (the property the game reads stays meanwhile)
        g.paint, g.objects = lot["paint"], []
        for o in lot["objects"]:
            g.next_id += 1
            g.objects.append(obj(g.next_id, o["table_id"], o["x"], o["y"], o["yaw"], o["floor"], o["z"]))
        return True
    lotsync.save_lot, lotsync.apply_by_title, lotsync.write_pending, lotsync.cleanup = \
        fake_save, fake_apply, fake_write, fake_cleanup
    exact_key = "house:1038:exact"

    def game(objects):
        g = StandIn([dict(o) for o in objects])
        g.paint, g.save_delay, g.apply_delay, g.started, g.kept_reading = "white", 0.0, 0.0, time.time(), []
        return g

    def exact_lot(p):
        it = p.shared.get(exact_key)
        return it and json.loads(base64.b64decode(it["value"]["files"]["site.dat"]))

    def exact_tables(p):
        lot = exact_lot(p)
        return sorted(o["table_id"] for o in lot["objects"]) if lot else None

    def layout(g):
        return sorted((o["table_id"], round(o["location"]["x"]), round(o["location"]["y"])) for o in g.objects)

    def live_layout(p):
        return sorted((o["table_id"], round(o["x"]), round(o["y"])) for o in p.shared[KEY]["value"]["objects"])

    def said(p, since, text):
        return [c["text"] for c in p.chat if c["time"] >= since and text in c["text"]]

    def exact_by(p, who):
        return (p.shared.get(exact_key) or {}).get("by") == who.me_id

    async def restart(p, g):
        """The game is closed and started again, back in the world: it applies a partner's exact house kept for it."""
        await asyncio.sleep(0.02)
        g.started = time.time()                          # (it lists the properties written before this)
        p.world, p.in_build_mode = "play", False
        reopen(p)
        await p.maybe_apply_pending()

    async def ticks(players, secs):
        end = time.time() + secs
        while time.time() < end:
            for p in players:
                await p.house_tick()                     # (the companion's game loop)
            await asyncio.sleep(0.2)

    async def first_reading(*ps):
        refresh, housesync.REFRESH_EVERY = housesync.REFRESH_EVERY, 0
        try:
            for _ in range(2):
                for p in ps:
                    await p.house_tick()                 # (the home read: brought to the server's house again)
                await settle(*ps)
        finally:
            housesync.REFRESH_EVERY = refresh

    async def joined(*ps):
        assert await until(lambda: all(p.me_id and p.shared_conn is p.writer for p in ps)
                           and all(q.me_id in p.remotes for p in ps for q in ps if q is not p))

    server = mp.Server(mp.Store(TMP / "server-races"))
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
    port = srv.sockets[0].getsockname()[1]
    home = [obj(1, "Sofa01", 100, 100), obj(2, "Lamp01", 300, 50), obj(3, "Table03", 500, 500, 90)]
    ga, gb = game(home), game(home)
    ivy, joe = player("Ivy", port, ga), player("Joe", port, gb)
    games[ga.call], games[gb.call] = ga, gb
    tasks = [asyncio.create_task(ivy.session()), asyncio.create_task(joe.session())]
    srv2, tasks2 = None, []
    try:
        await joined(ivy, joe)
        ivy.last_full_state = joe.last_full_state = {"site": 1038}
        ivy.cfg["exact_house"] = joe.cfg["exact_house"] = True
        # in step first: Ivy adds a plant (her exact house goes up, Joe's game takes it on its next start)
        await build(ivy, ga, lambda g: g.objects.append(obj(50, "Plant01", 600, 600)))
        assert await until(lambda: (joe.cfg.get("pending_lot") or {}).get("from") == "Ivy" and "Plant01" in gb.tables())
        await settle(ivy, joe)
        await restart(ivy, ga)
        await restart(joe, gb)
        await first_reading(ivy, joe)
        assert not joe.exact_hold(KEY) and not ivy.exact_hold(KEY), (joe.exact_hold(KEY), ivy.exact_hold(KEY))

        # Joe places a rug and leaves build mode (his change not confirmed yet: his exact house is saved once its send
        # is answered, or noting the house it makes - preset_stale); his exact house is saved, 3 s. Meanwhile Ivy (exact
        # house off) takes the lamp out: her version reaches Joe's PC during his save. That save, with the lamp, isn't
        # sent; it's saved again once her change is in his game - Ivy's next start doesn't bring the lamp back
        ivy.cfg["exact_house"], gb.save_delay = False, 3.0
        await build(joe, gb, lambda g: g.objects.append(obj(77, "Rug01", 900, 900)))
        assert await until(lambda: joe.lot_busy and "Rug01" in ga.tables() and idle(ivy), 10)
        await build(ivy, ga, lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] != "Lamp01"]))
        assert await until(lambda: "Lamp01" not in [o["table_id"] for o in joe.shared[KEY]["value"]["objects"]], 5)
        assert joe.lot_busy, "(test timing) the lamp's removal reached Joe's PC after his save"
        await ticks((ivy, joe), 4)
        assert await until(lambda: exact_by(ivy, joe) and not joe.lot_busy and not joe.exact_wanted
                           and not joe.exact_pushing, 20), (exact_tables(ivy), list(joe.chat)[-3:])
        await settle(ivy, joe)
        assert "Lamp01" not in exact_tables(ivy) and "Rug01" in exact_tables(ivy), exact_tables(ivy)
        assert ga.tables() == gb.tables() and "Lamp01" not in gb.tables(), (ga.tables(), gb.tables())
        since = time.time()
        await restart(ivy, ga)                           # (Ivy's game takes Joe's exact house)
        await first_reading(ivy, joe)
        assert "Lamp01" not in ga.tables() and "Rug01" in ga.tables(), ga.tables()
        assert not said(ivy, since, "kept in your game only") and not ivy.hs().get("local_only")
        print("a partner's change that reaches my PC while my exact house is saved: that save isn't sent, it's saved "
              "again with the change; one saved before my change is confirmed goes only on the house it makes: ok")

        # Joe paints the walls blue while the server can't be reached (the live house unchanged: his exact house is
        # saved, unsent); meanwhile Ivy takes the table out. Back online, that save (with the table) isn't sent as it
        # is: it's saved again with her change in it
        gb.save_delay = 0.0
        writer = joe.writer
        joe.writer = None
        await build(joe, gb, lambda g: setattr(g, "paint", "blue"))
        assert await until(lambda: joe.dirty(1038, "exact"))
        stale = joe.saved_preset(1038)["hash"]
        await build(ivy, ga, lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] != "Table03"]))
        assert await until(lambda: "Table03" not in gb.tables())
        await settle(ivy, joe)
        joe.writer, joe.house_push_at = writer, 0.0
        for _ in range(3):
            await joe.push_dirty_house()                 # (the shared loop's turns)
            await settle(ivy, joe)
        assert await until(lambda: not joe.dirty(1038, "exact") and not joe.lot_busy and not joe.exact_wanted
                           and not joe.exact_pushing)
        lot = exact_lot(joe)
        assert exact_by(joe, joe) and joe.shared[exact_key]["value"]["hash"] != stale, joe.shared[exact_key]
        assert lot["paint"] == "blue" and "Table03" not in [o["table_id"] for o in lot["objects"]], lot
        print("an exact house saved offline, the live house changed meanwhile: not sent as it is, saved again: ok")

        # Ivy (exact house on) paints green: Joe keeps hers for his next start; his game starts again and takes it (it
        # takes a while); meanwhile Ivy paints yellow: hers is kept for the start after - not dropped with the one
        # applied, nor is the property the game reads removed meanwhile -, and Joe's exact house isn't held back after
        ivy.cfg["exact_house"] = True
        await restart(ivy, ga)                           # (Ivy's game takes Joe's blue house first)
        await first_reading(ivy, joe)
        await build(ivy, ga, lambda g: setattr(g, "paint", "green"))
        assert await until(lambda: (joe.cfg.get("pending_lot") or {}).get("from") == "Ivy")
        await settle(ivy, joe)
        v1 = joe.cfg["pending_lot"]["v"]
        gb.apply_delay = 1.5
        job = asyncio.create_task(restart(joe, gb))
        assert await until(lambda: joe.lot_busy, 5)
        await build(ivy, ga, lambda g: setattr(g, "paint", "yellow"))
        assert await until(lambda: int((joe.cfg.get("pending_lot") or {}).get("v") or 0) > v1, 5), \
            "(test timing) Ivy's yellow house didn't come during Joe's apply"
        await job
        gb.apply_delay = 0.0
        p = joe.cfg.get("pending_lot") or {}
        assert gb.paint == "green" and gb.kept_reading[-1], (gb.paint, gb.kept_reading)
        assert int(p.get("v") or 0) > v1 and p.get("folder") in props, ("the newer exact house was lost", p)
        await restart(joe, gb)
        assert gb.paint == ga.paint == "yellow" and not joe.cfg.get("pending_lot"), (gb.paint, ga.paint)
        await first_reading(ivy, joe)
        since = time.time()
        await build(joe, gb, lambda g: g.objects.append(obj(78, "Vase01", 40, 900)))
        assert await until(lambda: exact_by(ivy, joe), 15), list(joe.chat)[-3:]
        assert not said(joe, since, "changed it first"), said(joe, since, "exact house")
        await settle(ivy, joe)
        print("a partner's newer exact house that comes while the older one is applied: kept for the next start: ok")

        # Ivy adds a chair and paints red: Joe keeps her exact house for his next start. The same evening Joe takes the
        # chair out and moves the sofa. His game starts again: her exact house brings the chair and the sofa where it
        # was back - the live house, newer, takes them out again: one sofa where Joe put it, no chair, the same in both
        # games and on the server; nothing kept "only in his game"; his next exact house is shared
        await restart(ivy, ga)                           # (Ivy's game takes Joe's vase house first)
        await first_reading(ivy, joe)

        def chair_red(g):
            g.objects.append(obj(80, "Chair20", 700, 800))
            g.paint = "red"
        await build(ivy, ga, chair_red)
        assert await until(lambda: (joe.cfg.get("pending_lot") or {}).get("from") == "Ivy" and "Chair20" in gb.tables())
        await settle(ivy, joe)
        since = time.time()
        await build(joe, gb, lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] not in
                                                              ("Chair20", "Sofa01")] + [obj(81, "Sofa01", 200, 600)]))
        assert await until(lambda: layout(ga) == layout(gb))
        await settle(ivy, joe)
        await restart(joe, gb)
        assert gb.paint == "red" and "Chair20" in gb.tables(), (gb.paint, gb.tables())    # (her house: older)
        await first_reading(ivy, joe)
        assert layout(gb) == live_layout(joe) == layout(ga), (layout(gb), live_layout(joe), layout(ga))
        assert "Chair20" not in gb.tables() and [x for x in layout(gb) if x[0] == "Sofa01"] == [("Sofa01", 200, 600)]
        assert not joe.hs().get("local_only") and not said(joe, since, "kept in your game only"), \
            (joe.hs().get("local_only"), list(joe.chat)[-3:])
        await build(joe, gb, lambda g: g.objects.append(obj(82, "Book01", 60, 900)))
        assert await until(lambda: exact_by(ivy, joe), 15), list(joe.chat)[-3:]
        await settle(ivy, joe)
        lot = exact_lot(ivy)
        assert lot["paint"] == "red" and "Chair20" not in [o["table_id"] for o in lot["objects"]], lot
        print("my removals and moves made after the partner's exact house was kept: its start doesn't bring them back "
              "for good, nothing kept only in my game, my exact house shared after: ok")

        # a new server: Lou buys a fridge before it has any version of the house; nobody builds - the first reading of
        # his home sends it whole, as the first version: the fridge reaches Kim. Kim builds: both games have both. Then
        # the server's shared state starts over: a fridge Lou buys then reaches Kim with the next first version too
        # (Kim's, from her build session: Lou's fridge goes up after it)
        data = TMP / "server-races-first"
        server2 = mp.Server(mp.Store(data))
        srv2 = await asyncio.start_server(server2.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        home2 = [obj(1, "Sofa01", 100, 100), obj(2, "Lamp01", 300, 50)]
        gk, gl = game(home2), game(home2)
        kim, lou = player("Kim", srv2.sockets[0].getsockname()[1], gk), player("Lou", srv2.sockets[0].getsockname()[1], gl)
        games[gk.call], games[gl.call] = gk, gl
        tasks2 = [asyncio.create_task(kim.session()), asyncio.create_task(lou.session())]
        await joined(kim, lou)
        kim.last_full_state = lou.last_full_state = {"site": 1038}
        gl.objects.append(obj(90, "Fridge30", 880, 880))
        builds = gk.count("build.enter_mode") + gl.count("build.enter_mode")
        await first_reading(lou, kim)
        assert await until(lambda: "Fridge30" in gk.tables()), (gk.tables(), kim.shared.get(KEY))
        await settle(kim, lou)
        cur = kim.shared[KEY]                            # (Lou's first version - or Kim's, his fridge on top of it)
        assert cur["by"] == lou.me_id and (cur["value"].get("history") or {}).get("complete"), cur
        assert gk.count("build.enter_mode") + gl.count("build.enter_mode") == builds + 1, "(only Kim's apply)"
        assert gk.tables() == gl.tables() and not lou.pending(KEY) and not kim.pending(KEY), (gk.tables(), gl.tables())
        await build(kim, gk, lambda g: g.objects.append(obj(91, "Plant30", 60, 60)))
        assert await until(lambda: "Plant30" in gl.tables())
        await settle(kim, lou)
        assert "Fridge30" in [o[0] for o in live_layout(kim)] and gk.tables() == gl.tables(), (gk.tables(), gl.tables())
        await server2.shutdown()
        srv2.close()
        await asyncio.gather(*tasks2, return_exceptions=True)
        shutil.rmtree(data / "shared")                   # (its shared state starts over: a new epoch)
        server2 = mp.Server(mp.Store(data))
        srv2 = await asyncio.start_server(server2.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        for p in (kim, lou):
            p.writer, p.me_id = None, None               # (as run() does after a session)
            p.cfg["port"] = srv2.sockets[0].getsockname()[1]
        tasks2 = [asyncio.create_task(kim.session()), asyncio.create_task(lou.session())]
        await joined(kim, lou)
        assert not kim.shared.get(KEY)
        gl.objects.append(obj(92, "Fridge31", 870, 870))
        await build(kim, gk, lambda g: g.objects.append(obj(93, "Rug31", 300, 700)))
        assert await until(lambda: "Rug31" in gl.tables())
        assert await until(lambda: "Fridge31" in gk.tables()), (gk.tables(), live_layout(kim))
        await settle(kim, lou)
        assert gk.tables() == gl.tables(), (gk.tables(), gl.tables())
        print("bought before the server had any version of the house (new, or started over): reaches the partner with "
              "the first version, even with nobody building: ok")
    finally:
        lotsync.save_lot, lotsync.apply_by_title, lotsync.write_pending, lotsync.cleanup = real
        for t in tasks + tasks2:
            t.cancel()
        srv.close()
        if srv2:
            srv2.close()


async def joining():
    """A PC that has no base (its first time on a server whose house has a history, or back after the server's shared
    state started over while it was away), an apply overtaken while build mode opens, and an offline build session's
    exact house. Players and servers of their own; the exact house's stand-in carries the paint and the objects.
    - Ana alone moves the table, then takes the lamp out; Ben joins for the first time with his own save of the home:
      one table (where she put it), no lamp - in both games and on the server, nothing kept aside; the same with two
      removals and an addition, and with the server's shared state started over while Ben's link was down
    - the house's versions made by an older program (no history): what Ben has beyond the version stays in his game
      only (said once), the server's house and Ana's game aren't changed - nor by the next version
    - Ana's own change made offline goes up while build mode opens for Ben's older version: that version isn't put in
      (overtaken: nothing done), her game never takes her change out and puts it back - an addition, a removal -, and
      the partner's change is said once
    - Ben, offline, places a rug and paints blue in one build session and closes the game (the same save comes back)
      before the server is back: his blue exact house still reaches the server"""
    import base64
    import copy
    import hashlib
    import shutil
    import inzoi_mp_server as mp
    import lotsync
    games, props = {}, {}
    real = (lotsync.save_lot, lotsync.apply_by_title, lotsync.write_pending, lotsync.cleanup)

    def fake_save(call, site_id, title):
        g = games[call]
        blob = json.dumps({"paint": g.paint, "objects": sorted((key(o) for o in g.objects), key=json.dumps)}).encode()
        return {"hash": hashlib.sha1(blob).hexdigest()[:8],
                "files": {"site.dat": base64.b64encode(blob).decode(),
                          "meta.json": base64.b64encode(b'{"Title":"x"}').decode()}}

    def fake_write(files, title):
        folder = f"J{len(props) + 1:04}"
        props[folder] = title
        return folder

    def fake_cleanup(keep=()):
        for f in [f for f in props if f not in keep]:
            del props[f]

    def fake_apply(call, site_id, title):
        raise lotsync.NotListed(title)                   # (the game lists properties only when it starts)
    lotsync.save_lot, lotsync.apply_by_title, lotsync.write_pending, lotsync.cleanup = \
        fake_save, fake_apply, fake_write, fake_cleanup
    exact_key = "house:1038:exact"
    home = [obj(1, "Sofa01", 100, 100), obj(2, "Lamp01", 300, 50), obj(3, "Table03", 500, 500, 90),
            obj(4, "TV03", 800, 200)]
    servers, tasks = [], []

    async def serve(name):
        server = mp.Server(mp.Store(TMP / f"server-joining-{name}"))
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        servers.append((server, srv))
        return server, srv, srv.sockets[0].getsockname()[1]

    def players(names, port):
        out = []
        for name in names:
            g = StandIn(copy.deepcopy(home))
            g.paint = "white"
            p = player(name, port, g)
            p.last_full_state = {"site": 1038}
            games[g.call] = g
            out.append((p, g))
        return out

    def connect(*ps):
        for p in ps:
            tasks.append(asyncio.create_task(p.session()))

    async def stop():                                    # (the scenario's players leave)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()

    async def joined(*ps):
        assert await until(lambda: all(p.me_id and p.shared_conn is p.writer for p in ps)
                           and all(q.me_id in p.remotes for p in ps for q in ps if q is not p), 15)

    def layout(g):
        return sorted((o["table_id"], round(o["location"]["x"]), round(o["location"]["y"])) for o in g.objects)

    def live_layout(p):
        return sorted((o["table_id"], round(o["x"]), round(o["y"])) for o in p.shared[KEY]["value"]["objects"])

    def move_table(x, y):
        return lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] != "Table03"]
                                 + [obj(900 + x, "Table03", x, y, 90)])

    def take_out(table):
        return lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] != table])

    def said(p, since, text):
        return [c["text"] for c in p.chat if c["time"] >= since and text in c["text"]]

    try:
        # --- Ben's first time on a server whose house has a history: moves and removals made before he joined
        for tag, steps, want in (
                ("move", [move_table(200, 600), take_out("Lamp01")],
                 [("Sofa01", 100, 100), ("TV03", 800, 200), ("Table03", 200, 600)]),
                ("twice", [take_out("Sofa01"), take_out("Lamp01"),
                           lambda g: g.objects.append(obj(40, "Plant40", 60, 60))],
                 [("Plant40", 60, 60), ("TV03", 800, 200), ("Table03", 500, 500)])):
            _, _, port = await serve(tag)
            (ana, ga), (ben, gb) = players((f"Ana{tag}", f"Ben{tag}"), port)
            connect(ana)
            await joined(ana)
            for step in steps:                           # (Ana alone: the first version, then the next ones)
                await build(ana, ga, step)
                assert await until(lambda: ana.shared.get(KEY) and ana.seen(KEY) == ana.shared[KEY]["v"])
                await settle(ana)
            assert layout(ga) == live_layout(ana) == want, (layout(ga), live_layout(ana))
            v = ana.shared[KEY]["v"]
            connect(ben)                                 # (his first time on this server, with his own save of it)
            await joined(ana, ben)
            assert await until(lambda: layout(gb) == want, 15), (tag, layout(gb))
            await settle(ana, ben)
            assert layout(ga) == layout(gb) == live_layout(ana) == want, (tag, layout(ga), layout(gb), live_layout(ana))
            assert ana.shared[KEY]["v"] == v and not ben.pending(KEY) and not ben.hs().get("local_only"), tag
            await stop()
        print("a first join to a server whose house has a history: what the partner moved or took out before goes from "
              "the joiner's game too, nothing comes back (both games and the server the same): ok")

        # --- the server's shared state starts over while Ben's link is down; Ana makes two versions before he's back
        server, srv, port = await serve("wipe")
        (ana, ga), (ben, gb) = players(("AnaW", "BenW"), port)
        connect(ana, ben)
        await joined(ana, ben)
        await build(ana, ga, lambda g: g.objects.append(obj(41, "Shelf05", 700, 100)))
        assert await until(lambda: "Shelf05" in gb.tables())
        await settle(ana, ben)
        await server.shutdown()
        srv.close()
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()
        shutil.rmtree(TMP / "server-joining-wipe" / "shared")    # (its shared state starts over: a new epoch)
        _, srv, port = await serve("wipe")
        ana.writer, ana.me_id = None, None               # (as run() does after a session)
        ben.writer, ben.me_id = None, None
        ana.cfg["port"] = ben.cfg["port"] = port
        connect(ana)                                     # (Ben's link still down)
        await joined(ana)
        assert not ana.shared.get(KEY)
        for step in (move_table(200, 600), take_out("Lamp01")):
            await build(ana, ga, step)
            assert await until(lambda: ana.shared.get(KEY) and ana.seen(KEY) == ana.shared[KEY]["v"])
            await settle(ana)
        want = [("Shelf05", 700, 100), ("Sofa01", 100, 100), ("TV03", 800, 200), ("Table03", 200, 600)]
        assert layout(ga) == live_layout(ana) == want, (layout(ga), live_layout(ana))
        connect(ben)                                     # (Ben's back: no base on this life of the server)
        await joined(ana, ben)
        assert await until(lambda: layout(gb) == want, 15), layout(gb)
        await settle(ana, ben)
        assert layout(ga) == layout(gb) == live_layout(ana) == want, (layout(ga), layout(gb), live_layout(ana))
        await stop()
        print("back after the server's shared state started over: the partner's moves and removals since go from that "
              "game too, nothing duplicated or brought back: ok")

        # --- the house's versions made by an older program (no history): what Ben has beyond it stays his only
        _, _, port = await serve("old")
        (ana, ga), (ben, gb) = players(("AnaO", "BenO"), port)
        real_value = ana.house_value

        def old_value(p, cur):                           # (an older program: its versions keep no history)
            value, dropped, undo = real_value(p, cur)
            value.pop("history", None)
            return value, dropped, undo
        ana.house_value = old_value
        connect(ana)
        await joined(ana)
        for step in (move_table(200, 600), take_out("Lamp01")):
            await build(ana, ga, step)
            assert await until(lambda: ana.shared.get(KEY) and ana.seen(KEY) == ana.shared[KEY]["v"])
            await settle(ana)
        del ana.house_value
        want = layout(ga)
        v, since = ana.shared[KEY]["v"], time.time()
        connect(ben)
        await joined(ana, ben)
        assert await until(lambda: ben.seen(KEY) == v, 15)
        await settle(ana, ben)
        assert ana.shared[KEY]["v"] == v and layout(ga) == live_layout(ana) == want, (layout(ga), live_layout(ana))
        assert layout(gb) == sorted(want + [("Table03", 500, 500)]), layout(gb)     # (his own: kept, not sent)
        kept = ben.hs().get("local_only") or {}
        assert [o["table_id"] for o in kept.get("objects") or []] == ["Table03"] and not ben.pending(KEY), kept
        assert len(said(ben, since, "the shared house doesn't have")) == 1, list(ben.chat)[-3:]
        await build(ana, ga, lambda g: g.objects.append(obj(42, "Plant42", 650, 650)))    # (her next version: merged
        assert await until(lambda: "Plant42" in gb.tables())                              # on his base now)
        await settle(ana, ben)
        assert [x for x in live_layout(ana) if x[0] == "Table03"] == [("Table03", 200, 600)], live_layout(ana)
        assert layout(ga) == live_layout(ana) and len(said(ben, since, "the shared house doesn't have")) == 1
        await stop()
        print("versions without the whole history (an older program's): what the joiner has beyond it stays in that "
              "game only (said once), the server's house and the partner's game unchanged: ok")

        # --- Ana's own offline change goes up while build mode opens for Ben's older version
        for tag, change, mine in (("add", lambda g: g.objects.append(obj(111, "Plant07", 60, 900)), "Plant07"),
                                  ("remove", take_out("Sofa01"), "Sofa01")):
            _, _, port = await serve(f"over-{tag}")
            (ana, ga), (ben, gb) = players((f"Ana{tag[:3]}V", f"Ben{tag[:3]}V"), port)
            connect(ana, ben)
            await joined(ana, ben)
            await build(ana, ga, lambda g: g.objects.append(obj(110, "Shelf05", 700, 100)))
            assert await until(lambda: "Shelf05" in gb.tables())
            await settle(ana, ben)
            real_call = ga.call

            def opening(tool, **kw):                     # (the game takes 1.5 s to open build mode)
                if tool == "build.enter_mode":
                    ga.calls.append((tool, kw))
                    ga.phase, ga.ready_at = "opening", time.time() + 1.5
                    return {}
                if tool == "build.get_mode_state" and ga.phase == "opening" and time.time() >= ga.ready_at:
                    ga.phase = "ready"
                return real_call(tool, **kw)
            writer = ana.writer
            ana.writer = None                            # (her link down: her change waits)
            await build(ana, ga, change)
            assert ana.pending(KEY)
            ana.game.call, n, since = opening, ga.count("build.enter_mode"), time.time()
            await build(ben, gb, take_out("TV03"))       # (his version reaches her PC: build mode opens for it)
            assert await until(lambda: ga.count("build.enter_mode") > n and ga.phase == "opening")
            ana.writer, ana.house_push_at = writer, 0.0
            await ana.push_dirty_house()                 # (meanwhile her change goes up, on top of his version)
            assert ana.shared[KEY]["by"] == ana.me_id and ga.phase == "opening", "(test timing)"
            await settle(ana, ben)
            ana.game.call = real_call
            assert layout(ga) == layout(gb) == live_layout(ana) and "TV03" not in ga.tables(), (layout(ga), layout(gb))
            assert (mine in ga.tables()) == (tag == "add"), (tag, ga.tables())
            assert not ga.count("building.place_object", object_table_id=mine), f"{tag}: {mine} put back in her game"
            assert not ga.count("build.remove_object", entity_id="111"), f"{tag}: her plant taken out of her game"
            assert len(said(ana, since, "changed the house")) == 1, said(ana, since, "house")
            assert not said(ana, since, "2 removed") and not said(ana, since, "1 new"), said(ana, since, "house")
            await stop()
        # ...and when it lands once the apply is under way (after its plan), with walls to build: what's placed after
        # the walls is worked out against the same base and changes as the plan - her removal isn't put back
        _, _, port = await serve("over-walls")
        (ana, ga), (ben, gb) = players(("AnaWV", "BenWV"), port)
        connect(ana, ben)
        await joined(ana, ben)
        await build(ana, ga, lambda g: g.objects.append(obj(110, "Shelf05", 700, 100)))
        assert await until(lambda: "Shelf05" in gb.tables())
        await settle(ana, ben)
        writer, pushed = ana.writer, []
        ana.writer = None
        await build(ana, ga, take_out("Sofa01"))
        assert ana.pending(KEY)

        def plan_then_push(q, mine, hold=True, state=None):
            out = housesync.HouseSync.plan_for(ana, q, mine, hold, state)
            if ana.applying_house and hold and not pushed:
                pushed.append(1)                         # (the shared loop's turn comes right after the plan)
                ana.writer, ana.house_push_at = writer, 0.0
                ana.house_task(ana.push_dirty_house())
            return out
        ana.plan_for, ga.slow, since = plan_then_push, 0.3, time.time()

        def wall_and_tv(g):
            g.walls.append(wall(1012.5, 12.5, 1012.5, 312.5))
            g.objects = [o for o in g.objects if o["table_id"] != "TV03"]
        await build(ben, gb, wall_and_tv)
        assert await until(lambda: pushed, 10)
        await settle(ana, ben, timeout=30)
        del ana.plan_for
        ga.slow = 0.0
        assert ana.shared[KEY]["by"] == ana.me_id and ga.count("building.construct_wall") == 1, ga.calls[-5:]
        assert not ga.count("building.place_object", object_table_id="Sofa01"), "her removal was put back"
        assert layout(ga) == layout(gb) == live_layout(ana) and "Sofa01" not in ga.tables(), (layout(ga), layout(gb))
        assert not said(ana, since, "1 new"), said(ana, since, "house")
        await stop()
        print("my change going up while build mode opens for an older version, or while it's put in: that one isn't "
              "put in or is finished as planned, my change is never undone in my game (an addition, a removal), said "
              "once: ok")

        # --- Ben, offline, places a rug and paints blue in one build session, then closes the game (the same save
        # comes back) before the server is back: his exact house is saved then, and goes up once it's back
        _, _, port = await serve("paint")
        (ana, ga), (ben, gb) = players(("AnaP", "BenP"), port)
        ben.cfg["exact_house"] = True
        connect(ana, ben)
        await joined(ana, ben)
        await build(ana, ga, lambda g: g.objects.append(obj(43, "Shelf05", 700, 100)))
        assert await until(lambda: "Shelf05" in gb.tables())
        await settle(ana, ben)
        writer = ben.writer
        ben.writer = None

        def rug_blue(g):
            g.objects.append(obj(171, "Rug17", 100, 600))
            g.paint = "blue"
        await build(ben, gb, rug_blue)
        assert await until(lambda: ben.dirty(1038, "exact") and not ben.lot_busy), "the exact house wasn't saved"
        ben.world = "out"                                # (he closes the game; it starts again with the same save)
        await ben.house_tick()
        reopen(ben)
        ben.world = "play"
        refresh, housesync.REFRESH_EVERY = housesync.REFRESH_EVERY, 0
        try:
            for _ in range(3):
                await ben.house_tick()                   # (the game loop: the home read, brought to the shared house)
                await settle(ben)
        finally:
            housesync.REFRESH_EVERY = refresh
        ben.writer, ben.house_push_at = writer, 0.0      # (the server's back)
        for _ in range(3):
            await ben.push_dirty_house()                 # (the shared loop's turns)
            await settle(ana, ben)
        assert await until(lambda: (ana.shared.get(exact_key) or {}).get("by") == ben.me_id and not ben.exact_pushing)
        lot = json.loads(base64.b64decode(ana.shared[exact_key]["value"]["files"]["site.dat"]))
        assert lot["paint"] == "blue" and "Rug17" in [o["table_id"] for o in lot["objects"]], lot
        assert "Rug17" in ga.tables() and not ben.dirty(1038, "exact") and not ben.dirty(1038, "live")
        await stop()
        print("an offline build session (a rug, blue paint), the game closed before the server is back: the exact "
              "house still goes up: ok")
    finally:
        lotsync.save_lot, lotsync.apply_by_title, lotsync.write_pending, lotsync.cleanup = real
        await stop()
        for _, srv in servers:
            srv.close()


REGRESSIONS = ("offline_then_older_save", "offline_then_exact_at_start", "first_version_after_reset",
               "back_from_another_server", "purchase_then_exact_at_start", "edits_while_walls_built")


async def regressions(only: str | None = None):
    """Found by review, each failing before its fix. Players and servers of their own; only: one of REGRESSIONS.
    - offline_then_older_save: a removal (and a move) made while the server can't be reached, then an older save loaded
      before it's back: the removal isn't undone once it lands - not sent back up as bought -, the move isn't doubled;
      both games and the server the same, and my own change coming back isn't said as my partner's
    - offline_then_exact_at_start: the same, with the partner's exact house applied at the game's next start instead
    - first_version_after_reset: the server's shared state starts over and my home goes up whole as its first version:
      what the partner had deleted, back with an older save, stays only in my game (said once) - never in theirs; and my
      own first version coming in isn't logged as my partner's house
    - back_from_another_server: back on a server whose house didn't change meanwhile, after playing on another one: what
      I changed there is compared and goes up (the one base kept was the other server's)
    - purchase_then_exact_at_start: bought in play mode, not read before the game closed, the partner's exact house
      waiting for the next start: the purchase goes up before the property replaces the lot, and comes back after it
    - edits_while_walls_built: what the player changes in the build mode an apply has open while it builds the walls
      goes up as theirs, and what they took out isn't put back"""
    import base64
    import hashlib
    import logging
    import shutil
    import inzoi_mp_server as mp
    import lotsync
    real = (lotsync.save_lot, lotsync.apply_by_title, lotsync.MYSITES)
    lotsync.MYSITES = TMP / "MySites-regressions"
    lotsync.MYSITES.mkdir(parents=True, exist_ok=True)
    games, listed, servers, tasks = {}, set(), [], []
    home = [obj(1, "Sofa01", 100, 100), obj(2, "Lamp01", 300, 50), obj(3, "Table03", 500, 500)]

    def fake_save(call, site_id, title):                 # (the property: the whole lot as the game holds it)
        blob = json.dumps(sorted((key(o) for o in games[call].objects), key=json.dumps)).encode()
        return {"hash": hashlib.sha1(blob).hexdigest()[:8],
                "files": {"site.dat": base64.b64encode(blob).decode(),
                          "meta.json": base64.b64encode(b'{"Title":"x"}').decode()}}

    def fake_apply(call, site_id, title):                # (the game lists properties only when it starts: listed)
        for folder in (f for f in lotsync.MYSITES.iterdir() if f.is_dir() and f.name in listed):
            if json.loads((folder / "meta.json").read_text("utf-8")).get("Title") == title:
                lot = json.loads((folder / "site.dat").read_bytes())
                games[call].objects = [obj(3000 + i, o["table_id"], o["x"], o["y"], o["yaw"], o["floor"], o["z"])
                                       for i, o in enumerate(lot)]
                return True
        raise lotsync.NotListed(title)
    lotsync.save_lot, lotsync.apply_by_title = fake_save, fake_apply

    async def serve(data=None):
        data = data or TMP / f"server-regressions-{len(servers)}"
        server = mp.Server(mp.Store(data))
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
        servers.append((server, srv))
        return server, srv, srv.sockets[0].getsockname()[1], data

    def players(names, port):
        out = []
        for name in names:
            g = StandIn([dict(o, location=dict(o["location"])) for o in home])
            p = player(name, port, g)
            p.last_full_state = {"site": 1038}
            games[g.call] = g
            out.append((p, g))
        return out

    def connect(*ps):
        for p in ps:
            tasks.append(asyncio.create_task(p.session()))

    async def stop(*ps):
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()
        for p in ps:
            p.writer, p.me_id = None, None               # (as run() does after a session)

    async def joined(*ps):
        assert await until(lambda: all(p.me_id and p.shared_conn is p.writer for p in ps)
                           and all(q.me_id in p.remotes for p in ps for q in ps if q is not p), 15)

    def layout(g):
        return sorted((o["table_id"], round(o["location"]["x"]), round(o["location"]["y"])) for o in g.objects)

    def live(p):
        return sorted((o["table_id"], round(o["x"]), round(o["y"])) for o in p.shared[KEY]["value"]["objects"])

    async def ticks(p, *others, n=3):                    # (the 2 s loop at home, the home read at each one)
        refresh, housesync.REFRESH_EVERY = housesync.REFRESH_EVERY, 0
        try:
            for _ in range(n):
                await p.house_tick()
                await settle(p, *others)
        finally:
            housesync.REFRESH_EVERY = refresh

    def said(p, since, text):
        return [c["text"] for c in p.chat if c["time"] >= since and text in c["text"]]

    async def in_step(tag, exact=False):
        _, _, port, _ = await serve()
        (ana, ga), (ben, gb) = players((f"Ana{tag}", f"Ben{tag}"), port)
        ana.cfg["exact_house"] = exact
        connect(ana, ben)
        await joined(ana, ben)
        await build(ana, ga, lambda g: g.objects.append(obj(41, "Shelf05", 700, 100)))
        assert await until(lambda: "Shelf05" in gb.tables())
        if exact:
            assert await until(lambda: (ben.cfg.get("pending_lot") or {}).get("hash")), "Ana's exact house wasn't kept"
        await settle(ana, ben)
        await ticks(ben, ana)
        return ana, ga, ben, gb

    async def game_back(p, g, restarted, objects=None):
        """The game to the main menu and back (restarted: closed and started again - it lists the properties written
        before; else another save loaded: objects, its home)."""
        p.world = "out"
        await p.house_tick()
        reopen(p, restarted=restarted)
        if restarted:
            listed.update(f.name for f in lotsync.MYSITES.iterdir() if f.is_dir())
        if objects is not None:
            g.objects = objects
        p.world = "play"

    async def offline_change_then(lose, tag):
        """Ben, while the server can't be reached, takes the sofa out - or moves the table - in build mode; then his lot
        is replaced before it's back (lose: an older save loaded, or the partner's exact house at the next start)."""
        problems = []
        def take_sofa(g):
            g.objects = [o for o in g.objects if o["table_id"] != "Sofa01"]

        def move_table(g):
            g.objects = [o for o in g.objects if o["table_id"] != "Table03"] + [obj(903, "Table03", 200, 600)]
        for kind, change in (("rm", take_sofa), ("mv", move_table)):
            ana, ga, ben, gb = await in_step(f"{tag}{kind}", exact=lose == "exact")
            older = [dict(o, location=dict(o["location"])) for o in gb.objects]
            writer = ben.writer
            ben.writer = None                            # (the server can't be reached)
            await build(ben, gb, change)
            want = layout(gb)
            assert ben.pending(KEY), "(test: no change waiting)"
            if lose == "exact":
                await game_back(ben, gb, restarted=True)
            else:
                await game_back(ben, gb, restarted=False, objects=older)
            await ticks(ben, ana)
            assert lose != "exact" or not ben.cfg.get("pending_lot"), "(test: the exact house wasn't applied)"
            since = time.time()
            ben.writer, ben.house_push_at = writer, 0.0  # (the server's back)
            for _ in range(3):
                await ben.push_dirty_house()
                await settle(ana, ben)
            await ticks(ben, ana)
            await ticks(ana, ben)
            if not live(ana) == layout(ga) == layout(gb) == want:
                problems.append(f"{kind}: server {live(ana)}, Ana {layout(ga)}, Ben {layout(gb)}, want {want}")
            if said(ben, since, f"{ana.cfg['name']} changed the house"):
                problems.append(f"{kind}: my own change said as my partner's: {said(ben, since, 'changed the house')}")
            await stop(ana, ben)
        assert not problems, problems

    async def offline_then_older_save():
        await offline_change_then("older", "RO")
        print("a removal or move made offline, then an older save loaded before the server is back: not undone or "
              "doubled once it lands, in both games and on the server; my own change not said as my partner's: ok")

    async def offline_then_exact_at_start():
        await offline_change_then("exact", "RX")
        print("a removal or move made offline, then the partner's exact house applied at the next start: done again, "
              "not sent back up as bought: ok")

    async def first_version_after_reset():
        server, srv, port, data = await serve()
        (ana, ga), (ben, gb) = players(("AnaRF", "BenRF"), port)
        connect(ana, ben)
        await joined(ana, ben)
        older = [dict(o, location=dict(o["location"])) for o in ga.objects]     # (Ana's autosave: the lamp in it)
        await build(ben, gb, lambda g: setattr(g, "objects", [o for o in g.objects if o["table_id"] != "Lamp01"]))
        assert await until(lambda: "Lamp01" not in ga.tables())
        await settle(ana, ben)
        await stop(ana, ben)
        await server.shutdown()                          # (its shared state starts over: a new epoch)
        srv.close()
        shutil.rmtree(data / "shared")
        _, _, port, _ = await serve(data)
        ana.cfg["port"] = ben.cfg["port"] = port
        await game_back(ana, ga, restarted=False, objects=older)     # (Ana loads her older save: the lamp is back)
        connect(ana)
        await joined(ana)
        assert not ana.shared.get(KEY)
        logs, handler, hl = [], logging.Handler(), logging.getLogger("housesync")
        handler.emit = lambda r: logs.append(r.getMessage())
        level = hl.level
        hl.addHandler(handler)
        hl.setLevel(logging.INFO)
        since = time.time()
        try:
            await ticks(ana)                             # (her home's first reading: it goes up whole, the first)
            assert await until(lambda: ana.shared.get(KEY))
            await settle(ana)
            await ticks(ana)
        finally:
            hl.removeHandler(handler)
            hl.setLevel(level)
        problems = [f"my own first version logged as my partner's: {m}" for m in logs if "Your partner's house" in m]
        if "Lamp01" in [x[0] for x in live(ana)]:
            problems.append(f"the lamp Ben deleted is in the first version: {live(ana)}")
        if "Lamp01" not in ga.tables() or [o["table_id"] for o in (ana.hs().get("local_only") or {}).get("objects")
                                           or []] != ["Lamp01"]:
            problems.append(f"the lamp isn't kept in Ana's game only: {ana.hs().get('local_only')}")
        if len(said(ana, since, "kept in your game only")) != 1:
            problems.append(f"said {len(said(ana, since, 'kept in your game only'))} times")
        assert not problems, problems
        connect(ben)
        await joined(ana, ben)
        assert await until(lambda: ben.seen(KEY) >= ana.shared[KEY]["v"], 15)
        await settle(ana, ben)
        await ticks(ben, ana)
        await ticks(ana, ben)
        assert "Lamp01" not in gb.tables() and "Lamp01" not in [x[0] for x in live(ana)], (layout(gb), live(ana))
        assert layout(gb) == live(ana) == sorted(x for x in layout(ga) if x[0] != "Lamp01"), (layout(ga), layout(gb))
        assert len(said(ana, since, "kept in your game only")) == 1
        await stop(ana, ben)
        print("the first version after the server's shared state started over: what the partner had deleted, back with "
              "an older save, stays only in that game (said once), never in theirs; not logged as the partner's: ok")

    async def back_from_another_server():
        _, _, port_a, _ = await serve()
        _, _, port_b, _ = await serve()
        (ana, ga), (ben, gb) = players(("AnaRS", "BenRS"), port_a)
        for p in (ana, ben):                             # (the save each plays, their multiplayer save on A)
            p.save_session["folder"] = "MySave"
            p.cfg["mp_save"] = {"folder": "MySave", "name": "my save"}
        connect(ana, ben)
        await joined(ana, ben)
        await build(ana, ga, lambda g: g.objects.append(obj(41, "Shelf05", 700, 100)))
        assert await until(lambda: "Shelf05" in gb.tables())
        await settle(ana, ben)
        v_a = ana.shared[KEY]["v"]
        tasks.pop().cancel()                             # (Ben goes to server B for a while; Ana stays on A)
        await asyncio.sleep(0.1)
        ben.writer, ben.me_id = None, None
        ben.cfg["port"] = port_b
        connect(ben)
        await joined(ben)
        ben.cfg["mp_save"], ben.save_session["mp"] = {"folder": "MySave", "name": "my save"}, True    # ("use it")
        await ticks(ben)
        await build(ben, gb, lambda g: g.objects.append(obj(77, "Bed07", 300, 300)))
        assert await until(lambda: "Bed07" in [x[0] for x in live(ben)])
        await settle(ben)
        tasks.pop().cancel()                             # (back to A, whose house didn't change meanwhile)
        await asyncio.sleep(0.1)
        ben.writer, ben.me_id = None, None
        ben.cfg["port"] = port_a
        connect(ben)
        await joined(ana, ben)
        assert ben.mp_active() and ben.seen(KEY) >= v_a
        for _ in range(2):
            await settle(ana, ben)
            await ticks(ben, ana)
            await ticks(ana, ben)
        assert "Bed07" in ga.tables() and "Bed07" in [x[0] for x in live(ana)], (layout(ga), live(ana))
        assert layout(ga) == layout(gb) == live(ana), (layout(ga), layout(gb), live(ana))
        await stop(ana, ben)
        print("back on a server whose house didn't change, after another one: what I changed there is compared and "
              "goes up: ok")

    async def purchase_then_exact_at_start():
        ana, ga, ben, gb = await in_step("RP", exact=True)
        for _ in range(2):
            await ben.house_tick()                       # (play mode at home: the reading is fresh, not read again)
            await settle(ana, ben)
        gb.objects.append(obj(88, "Fridge09", 700, 700))     # (bought in play mode, not read yet)
        await ben.house_tick()
        await settle(ana, ben)
        assert "Fridge09" not in [x[0] for x in live(ana)], "(test: read already)"
        await game_back(ben, gb, restarted=True)         # (he quits; the game saved; the next start, the same save)
        for _ in range(3):
            await ben.house_tick()                       # (the 2 s loop at home: the 5 min reading isn't due)
            await settle(ana, ben)
        await ticks(ben, ana)
        await ticks(ana, ben)
        assert not ben.cfg.get("pending_lot"), "the partner's exact house wasn't applied"
        assert "Fridge09" in [x[0] for x in live(ana)] and "Fridge09" in ga.tables() and "Fridge09" in gb.tables(), \
            (live(ana), layout(ga), layout(gb))
        assert layout(ga) == layout(gb) == live(ana)
        await stop(ana, ben)
        print("bought in play mode, not read before the game closed, the partner's exact house waiting for the next "
              "start: it goes up first, and comes back after the property: ok")

    async def edits_while_walls_built():
        ana, ga, ben, gb = await in_step("RW")
        real_call, done = gb.call, []

        def slow_walls(tool, **kw):                      # (the game takes a moment per wall; Ben acts meanwhile)
            if tool == "building.construct_wall" and not done:
                done.append(1)
                gb.objects = [o for o in gb.objects if o["table_id"] != "Sofa01"] + [obj(99, "Rug05", 250, 250)]
            return real_call(tool, **kw)
        ben.game.call = slow_walls

        def wall_and_chair(g):
            g.walls.append(wall(1012.5, 12.5, 1012.5, 312.5))
            g.objects.append(obj(42, "Chair02", 520, 470))
        await build(ana, ga, wall_and_chair)
        assert await until(lambda: done and "Chair02" in gb.tables(), 15)
        await settle(ana, ben)
        ben.game.call = real_call
        for _ in range(2):
            await ticks(ben, ana)
            await ticks(ana, ben)
        assert not gb.count("building.place_object", object_table_id="Sofa01"), "the sofa Ben took out was put back"
        assert "Sofa01" not in ga.tables() and "Rug05" in ga.tables(), layout(ga)
        assert layout(ga) == layout(gb) == live(ana), (layout(ga), layout(gb), live(ana))
        assert gb.count("building.construct_wall") == 1, "Ben's edits made walls be built again"
        await stop(ana, ben)
        print("what the player changes while an apply builds the walls goes up as theirs, nothing of it put back: ok")

    cases = {"offline_then_older_save": offline_then_older_save,
             "offline_then_exact_at_start": offline_then_exact_at_start,
             "first_version_after_reset": first_version_after_reset,
             "back_from_another_server": back_from_another_server,
             "purchase_then_exact_at_start": purchase_then_exact_at_start,
             "edits_while_walls_built": edits_while_walls_built}
    assert set(cases) == set(REGRESSIONS)
    try:
        for name in REGRESSIONS:
            if only in (None, name):
                await cases[name]()
    finally:
        await stop()
        lotsync.save_lot, lotsync.apply_by_title, lotsync.MYSITES = real
        for _, srv in servers:
            srv.close()


if __name__ == "__main__":
    if "--rollback" in sys.argv:
        asyncio.run(rollback_and_restart())
    elif "--conflicts" in sys.argv:
        asyncio.run(conflicts())
    elif "--races" in sys.argv:
        asyncio.run(races())
    elif "--joining" in sys.argv:
        asyncio.run(joining())
    elif any(a.startswith("--regressions") for a in sys.argv):
        pick = next(a for a in sys.argv if a.startswith("--regressions")).partition("=")[2] or None
        assert pick in (None, *REGRESSIONS), f"--regressions=one of {', '.join(REGRESSIONS)}"
        asyncio.run(regressions(pick))
    else:
        asyncio.run(main())
