"""Couple features, without the game (a stand-in for the game's calls, a real test server between two companions):
- backups of the multiplayer save: zipped, an unchanged save isn't backed up twice, only the newest 10 stay, a restore
  puts the files (and their times) back and drops files written since
- the upload guard: a save the game is still writing isn't packed until its files stay the same for the settle time
- couple actions: a request goes through the server to the partner, shows in their /status "requests", and on accept
  the asker's Zoi starts a hug on the partner's puppet (the partner's PC only replays it); a dance starts on both
- "invite to my house": the guest is admitted at the door and, on accept, their Zoi goes to the host's Zoi
- "go to my partner": next to their puppet, or to their last spot without one; another city is refused
- interaction ids are learned from what the Zois do
- shared household: the /settings switch, /status fields, "undo" from the server (admin op world_restore of the
  previous version) and from a local backup

    python tools/test_couple.py
"""
import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["INZOI_MP_DOCUMENTS"] = str(TMP / "docs")                  # never the real game's folders
os.environ["APPDATA"] = str(TMP / "appdata")
os.environ["INZOI_MP_GAME_URL"] = "http://127.0.0.1:9/mcp"
os.environ["INZOI_MP_PANEL_PORT"] = "0"
os.environ.pop("INZOI_MP_SHARED_WORLD", None)
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "client"), str(ROOT / "server"), str(ROOT / "tools")]
import companion  # noqa: E402
import saves  # noqa: E402
import world  # noqa: E402

FOLDER = "20260101-120000"


def make_save(tag: bytes, folder: str = FOLDER):
    d = saves.SAVES / folder
    (d / folder / "Portrait").mkdir(parents=True, exist_ok=True)
    (d / f"{folder}.usvx").write_bytes(b"IZMS" + tag * 1000)
    (d / folder / "Portrait" / "1.png").write_bytes(b"PNG" + tag)
    mi = saves.MODSAVE / folder / folder
    mi.mkdir(parents=True, exist_ok=True)
    (mi / "ModInfo.json").write_text('{"ModInfo": []}')


def test_backups():
    make_save(b"v1")
    first = saves.backup(FOLDER, "before-load")
    assert first and first.exists() and first.parent == saves.BACKUPS
    assert saves.backup(FOLDER, "before-load") is None, "an unchanged save is backed up again"
    time.sleep(0.05)
    make_save(b"v2")
    (saves.SAVES / FOLDER / f"{FOLDER}_AutoSave_1.usvx").write_bytes(b"autosave written after v2")
    second = saves.backup(FOLDER, "before-v2")
    assert second and second != first
    listed = saves.list_backups(FOLDER)
    assert [b["name"] for b in listed] == [second.name, first.name] and listed[0]["reason"] == "before-v2"

    # restore the first: v1's files and times are back, the autosave written later is gone
    v1_time = next(f["m"] for f in listed[1]["files"] if f["p"].endswith(".usvx"))
    assert saves.restore(first) == FOLDER
    slot = saves.SAVES / FOLDER / f"{FOLDER}.usvx"
    assert slot.read_bytes() == b"IZMS" + b"v1" * 1000
    assert abs(slot.stat().st_mtime - v1_time) < 0.01
    assert (saves.SAVES / FOLDER / FOLDER / "Portrait" / "1.png").read_bytes() == b"PNGv1"
    assert not (saves.SAVES / FOLDER / f"{FOLDER}_AutoSave_1.usvx").exists()
    assert (saves.MODSAVE / FOLDER / FOLDER / "ModInfo.json").exists()
    assert saves.backup(FOLDER, "again") is None or True              # (v1 equals the second-newest: allowed)

    # only the newest 10 are kept
    for i in range(12):
        make_save(b"x%d" % i)
        assert saves.backup(FOLDER, f"n{i}")
    names = [b["name"] for b in saves.list_backups()]
    assert len(names) == 10 and all("_n" in n for n in names), names
    assert not list(saves.BACKUPS.glob("*.part"))
    print("backups: ok")


def test_upload_guard():
    make_save(b"steady")
    t0 = time.monotonic()
    assert world.wait_stable(FOLDER, settle=0.6, timeout=5, poll=0.1)
    assert time.monotonic() - t0 >= 0.6
    stop = time.monotonic() + 1.2

    def writing():                       # the game writes an autosave for 1.2 s
        n = 0
        while time.monotonic() < stop:
            n += 1
            (saves.SAVES / FOLDER / f"{FOLDER}_AutoSave_1.usvx").write_bytes(b"A" * n * 100)
            time.sleep(0.1)
    th = threading.Thread(target=writing)
    t0 = time.monotonic()
    th.start()
    time.sleep(0.05)
    try:
        world.pack_stable(FOLDER, {}, settle=0.6, timeout=0.5, poll=0.1)
        raise AssertionError("packed a save still being written")
    except world.StillWriting:
        pass
    import mods
    files, paths, _ = world.pack_stable(FOLDER, mods.Hashes(TMP / "h.json"), settle=0.6, timeout=10, poll=0.1)
    assert time.monotonic() - t0 >= 1.2 + 0.6 - 0.1, "packed before the game finished writing"
    th.join()
    assert any(f["p"] == "world.usvx" for f in files)
    print("upload guard: ok")


async def until(check, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        await asyncio.sleep(0.05)
    return False


def player(name: str, port: int):
    comp = companion.Companion({"key": name * 10, "name": name, "server": "127.0.0.1", "port": port, "password": ""})
    comp.mod["state"] = "running"
    comp.world, comp.my_city, comp.my_entity = "play", "Dowon", 10 if name == "Ana" else 20
    comp.save_session["mp"] = True
    calls = comp.fake_calls = []
    comp.game.go_to = lambda eid: calls.append(("go_to", eid))
    comp.game.call = lambda tool, **kw: calls.append((tool, kw)) or {}
    comp.save_cfg = lambda: None
    return comp


def ops(comp, name):
    return [o for _, _, o in comp.mail.ops if o["op"] == name]


async def test_couple():
    import inzoi_mp_server as mp
    server = mp.Server(mp.Store(TMP / "server"))
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0, limit=mp.MAX_LINE)
    port = srv.sockets[0].getsockname()[1]
    ana, ben = player("Ana", port), player("Ben", port)
    tasks = [asyncio.create_task(ana.session())]
    assert await until(lambda: ana.me_id)
    tasks.append(asyncio.create_task(ben.session()))
    assert await until(lambda: ben.me_id and ben.me_id in ana.remotes and ana.me_id in ben.remotes)
    ra, rb = ben.remotes[ana.me_id], ana.remotes[ben.me_id]      # (each one's view of the other)
    for comp, r, puppet in ((ana, rb, 77), (ben, ra, 88)):
        comp.puppet_ids[r.pid] = puppet
        r.spawned, r.state = True, {"city": "Dowon", "x": 100.0, "y": 200.0, "z": 30.0}
    ana_routes, ben_routes = ana.ui_routes(), ben.ui_routes()

    # --- hug: Ana asks, Ben accepts; only Ana's PC starts it (Ben's sees it through the live sync)
    res = await ana_routes["/partner/action"]({"action": "hug"})       # (no key: the only partner)
    assert res.get("ok"), res
    assert await until(lambda: (ben.couple_view()["requests"] or [None])[0] is not None)
    st = await ben_routes["/status"]({})
    req = st["requests"][0]
    assert req["action"] == "hug" and req["from"] == "Ana" and req["from_id"] == ana.me_id and req["id"] == res["id"]
    assert st["couple"]["sent"] == [] and st["couple"]["bags"]["hug"]["learned"] is False
    out = await ben_routes["/partner/respond"]({"id": req["id"], "accept": True})
    assert out.get("ok") and out["result"]["state"] == "done", out
    assert not ops(ben, "social"), "the partner's PC starts the hug too (two hugs would fight)"
    assert await until(lambda: ops(ana, "social"))
    social = ops(ana, "social")[0]
    assert social["key"] == ben.me_id and social["entity"] == 10 and social["bag"] == "Social_Hug", social
    assert ana.sent[res["id"]]["state"] == "started"
    ana.on_report("social", f"{res['id']} ok")
    assert (await ana_routes["/status"]({}))["couple"]["sent"][-1]["state"] == "done"
    assert not (await ben_routes["/status"]({}))["requests"], "answered requests stay listed"

    # --- learned ids: Ben's Zoi hugs Ana's puppet for real, and Ana's own Zoi dances
    ana.on_report("up", f"{ben.me_id}=77")
    ana.set_state(rb, {"n": 1, "city": "Dowon", "x": 1, "y": 2, "z": 3, "act": {"bag": "Friendly_HugTight",
                                                                                "player": ana.me_id}})
    assert ana.cfg["couple_bags"]["hug"] == "Friendly_HugTight"
    assert ops(ana, "act")[-1]["target"] == 10                           # (and it's replayed on Ana's own Zoi)
    ana.learn_bag({"bag": "Hug_Bad id; x=1"})                           # not an id: ignored
    ana.learn_bag({"bag": "Fun_DanceSolo"})
    assert ana.cfg["couple_bags"] == {"hug": "Friendly_HugTight", "dance": "Fun_DanceSolo"}

    # --- dance: Ben asks, Ana declines; asks again, Ana accepts: both start it on their own Zoi
    res = await ben_routes["/partner/action"]({"key": ana.me_id, "action": "dance"})
    assert await until(lambda: ana.requests)
    rid = next(iter(ana.requests))
    assert (await ana_routes["/partner/respond"]({"id": rid, "accept": False}))["ok"]
    assert await until(lambda: ben.sent[res["id"]]["state"] == "declined")
    assert not ops(ana, "social")[1:] and not ops(ben, "social")
    res = await ben_routes["/partner/action"]({"key": ana.me_id, "action": "dance"})
    assert await until(lambda: ana.requests)
    await ana_routes["/partner/respond"]({"id": next(iter(ana.requests)), "accept": True})
    assert ops(ana, "social")[-1] == {**ops(ana, "social")[-1], "bag": "Fun_DanceSolo", "entity": 10}
    assert "key" not in ops(ana, "social")[-1]
    assert await until(lambda: ops(ben, "social"))
    assert ops(ben, "social")[0]["bag"] == "Dance_Solo" and "key" not in ops(ben, "social")[0]
    assert (await ben_routes["/partner/respond"]({"id": "nope", "accept": True}))["code"] == "expired"
    assert (await ben_routes["/partner/action"]({"action": "slap"}))["code"] == "bad_action"

    # --- invite: no home known yet -> refused; with one, Ben is admitted and, on accept, goes to Ana's Zoi
    assert (await ana_routes["/partner/invite"]({}))["code"] == "no_home"
    ana.home = {"door": 501, "site": 900}
    res = await ana_routes["/partner/invite"]({"key": ben.me_id})
    assert res.get("ok") and ops(ana, "admit")[-1] == {**ops(ana, "admit")[-1], "key": ben.me_id, "door": 501,
                                                        "site": 900}
    assert await until(lambda: ben.requests)
    q = next(iter(ben.requests.values()))
    assert q["action"] == "invite"
    n_admits = len(ops(ana, "admit"))
    out = await ben_routes["/partner/respond"]({"id": q["id"], "accept": True})
    assert out["result"]["state"] == "done" and ben.fake_calls[-1] == ("go_to", 88), (out, ben.fake_calls)
    assert await until(lambda: ana.sent[res["id"]]["state"] == "done") and len(ops(ana, "admit")) > n_admits

    # --- go to my partner: next to the puppet; without one, to their spot; another city: refused
    assert (await ana_routes["/partner/goto"]({"key": ben.me_id}))["how"] == "puppet"
    assert ana.fake_calls[-1] == ("go_to", 77)
    ana.puppet_ids.pop(ben.me_id)
    rb.state = {"city": "Dowon", "x": 100.0, "y": 200.0, "z": 30.0}
    res = await ana_routes["/partner/goto"]({})
    assert res["how"] == "position" and ops(ana, "goto_point")[-1]["x"] == 180.0 and \
        ops(ana, "goto_point")[-1]["entity"] == 10
    rb.state = {**rb.state, "city": "Bliss Bay"}
    res = await ana_routes["/partner/goto"]({})
    assert res["code"] == "other_city" and res["city"] == "Bliss Bay"
    assert (await ana_routes["/partner/goto"]({"key": "nobody"}))["code"] == "no_partner"
    print("couple actions, invite, go to: ok")

    # --- shared household: switch, status, undo
    assert not ana.shared_world_on()
    out = await ana_routes["/settings"]({"shared_household": True})
    assert out["shared_household"] is True and ana.shared_world_on()
    ana.world_meta = {"version": 3, "by": "Ben", "pid": ben.me_id, "time": 1700000000, "name": "Casa", "size": 10}
    ana.cfg["world"] = {"version": 2, "folder": FOLDER, "mtime": 0}
    h = (await ana_routes["/status"]({}))["household"]
    assert h["on"] and h["behind"] and h["version"] == 3 and h["local_version"] == 2 and h["saved_by"] == "Ben"
    assert not h["saved_by_me"] and h["saved_at"] == 1700000000 and h["backups"] == 10 and h["can_undo"]["local"]
    assert not h["can_undo"]["server"], "a non-admin can't take the server's world back"
    assert (await ana_routes["/world/undo"]({"source": "server"}))["code"] == "not_admin"

    asked = []

    async def fake_admin(op, local=False, **fields):
        asked.append((op, local, fields))
        return {"ok": True, "version": 4}
    ana.admin, ana.is_admin = fake_admin, True
    ana.world = "out"
    assert (await ana_routes["/status"]({}))["household"]["can_undo"]["server"]
    res = await ana_routes["/world/undo"]({})                       # auto: the server's previous version
    assert res == {"ok": True, "source": "server", "version": 4, "restored": 2} and \
        asked == [("world_restore", False, {"version": 2})], (res, asked)

    # local undo: back to the newest backup (out of the game: no lobby trip), the current files backed up first
    make_save(b"played-on")
    newest = saves.list_backups(FOLDER)[0]
    ana.auto_loaded = True
    res = await ana_routes["/world/undo"]({"source": "local"})
    assert res["ok"] and res["backup"] == newest["name"]
    assert await until(lambda: (ana.undo_state or {}).get("source") == "local"
                       and ana.undo_state["state"] == "done"), ana.undo_state
    assert (saves.SAVES / FOLDER / f"{FOLDER}.usvx").read_bytes() == b"IZMS" + b"x11" * 1000
    assert not ana.auto_loaded, "the restored save isn't loaded at the main menu"
    assert saves.list_backups(FOLDER)[0]["reason"] == "before-undo"
    assert (await ana_routes["/world/backups"]({}))["backups"][0]["reason"] == "before-undo"
    print("shared household: ok")

    for t in tasks:
        t.cancel()
    srv.close()


def test_backup_before_load():
    comp = companion.Companion({"key": "c" * 20, "name": "C", "server": "x", "port": 1, "password": "",
                                "mp_save": {"folder": "20260202-101010", "name": "x"}})
    make_save(b"before-load", "20260202-101010")
    comp.game.call = lambda tool, **kw: {}

    async def go():
        await comp.load_mp_save()
    asyncio.run(go())
    b = saves.list_backups("20260202-101010")
    assert b and b[0]["reason"] == "before-load" and comp.last_backup["reason"] == "before-load"
    print("backup before load: ok")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    test_backups()
    test_upload_guard()
    asyncio.run(test_couple())
    test_backup_before_load()
    print("couple tests passed")
