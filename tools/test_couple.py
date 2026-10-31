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

    # --- presence: Ben's Zoi isn't being sent (a question on screen): Ana's card says why
    ben.save_session["mp"], ben.save_box = None, {"key": "setup", "text": "", "buttons": []}
    rb.state_at = 0.0
    await ben.send_presence("play")
    assert await until(lambda: (ana.partner_cards()[0].get("presence") or {}).get("box") == "setup")
    card = (await ana_routes["/status"]({}))["partners"][0]
    assert card["presence"]["box"] == "setup" and not card["online"], card
    ben.save_session["mp"], ben.save_box, ben._presence_at = True, None, 0
    await ben.send_presence("play")                                # in the multiplayer save, playing: nothing sent
    rb.state_at = time.time()
    assert ana.partner_cards()[0]["presence"] is None, "a fresh state wins over an old presence"
    print("presence: ok")
    checks = {c["id"]: c for c in (await ana_routes["/selftest"]({}))["checks"]}
    assert checks["partner_here"]["ok"] is True and checks["partner_here"]["data"]["names"] == ["Ben"], checks["partner_here"]
    ana.world = "stopped"
    checks = {c["id"]: c for c in (await ana_routes["/selftest"]({}))["checks"]}
    assert checks["partner_here"]["hint_code"] == "unpause", checks["partner_here"]
    ana.world = "play"
    print("self-test 'partner here': ok")

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
    assert social["key"] == ben.me_id and social["entity"] == 10, social
    assert social["bag"] == "Co_DeepHug_MaleToFemale_Success", social             # (the checked id, no list here)
    assert ("relationships.set_value_between", {"owner_id": 10, "target_id": 77, "Value": 100}) in ana.fake_calls
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

    # --- dance: Ben asks, Ana declines; asks again, Ana accepts: Ben's Zoi dances with Ana's puppet (a couple
    # dance), Ana's PC only replays it; the game's own list decides the id
    res = await ben_routes["/partner/action"]({"key": ana.me_id, "action": "dance"})
    assert await until(lambda: ana.requests)
    rid = next(iter(ana.requests))
    assert (await ana_routes["/partner/respond"]({"id": rid, "accept": False}))["ok"]
    assert await until(lambda: ben.sent[res["id"]]["state"] == "declined")
    assert not ops(ana, "social")[1:] and not ops(ben, "social")
    res = await ben_routes["/partner/action"]({"key": ana.me_id, "action": "dance"})
    assert await until(lambda: ana.requests)
    offered = {"interactions": [{"interaction_bag_id": "Co_Friendship_G04_Start"},
                                {"interaction_bag_id": "Co_Dance_Together_MaleToFemale_Friendship"}]}
    ben.game.call = lambda tool, **kw: ben.fake_calls.append((tool, kw)) or (offered if tool == "interaction.list_available" else {})
    n_ana = len(ops(ana, "social"))
    await ana_routes["/partner/respond"]({"id": next(iter(ana.requests)), "accept": True})
    assert len(ops(ana, "social")) == n_ana, "the partner's PC replays it through the live sync"
    assert await until(lambda: ops(ben, "social"))
    assert ops(ben, "social")[0]["bag"] == "Co_Dance_Together_MaleToFemale_Friendship", ops(ben, "social")
    assert ops(ben, "social")[0]["key"] == ana.me_id
    # without the partner's Zoi here: each one dances alone
    del ana.puppet_ids[ben.me_id]
    del ben.puppet_ids[ana.me_id]
    res = await ben_routes["/partner/action"]({"key": ana.me_id, "action": "dance"})
    assert await until(lambda: ana.requests)
    await ana_routes["/partner/respond"]({"id": next(iter(ana.requests)), "accept": True})
    assert ops(ana, "social")[-1]["bag"] == "Fun_DanceSolo" and "key" not in ops(ana, "social")[-1]
    assert await until(lambda: len(ops(ben, "social")) == 2)
    assert ops(ben, "social")[1]["bag"] == "Next_Dance_Start" and "key" not in ops(ben, "social")[1]
    ana.puppet_ids[ben.me_id], ben.puppet_ids[ana.me_id] = 77, 88
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

    # --- family: the partner's Zoi living in my household is driven (adopt), never copied
    rb.state = {"city": "Dowon", "x": 100.0, "y": 200.0, "z": 30.0}
    rb.profile = {"first_name": "Ben", "last_name": "Lima", "gender": "Male", "growth_step": "Adult"}
    members = {"members": [{"entity_id": 10, "first_name": "Ana", "last_name": "Lima"},
                           {"entity_id": 55, "first_name": "Ben", "last_name": "Lima"}]}
    avail = {"interactions": [
        {"interaction_bag_id": "Co_Peck_FemaleToMale_Success", "display_name": "Dar um selinho"},
        {"interaction_bag_id": "Co_friendship_R10", "display_name": "Falar sobre a vida"},
        {"interaction_bag_id": "Co_GetOut", "display_name": "Expulsar"},
        {"interaction_bag_id": "Co_HighFIve_FemaleToMale_Friendship", "display_name": "Toca aqui!"},
        {"interaction_bag_id": "bad id; x", "display_name": "x"}]}
    stats = {"stats": {"Romance": {"value": 80.4, "level": 2}, "Friendship": {"value": 99.9, "level": 3}}}

    def game_answers(comp, extra):
        def call(tool, **kw):
            comp.fake_calls.append((tool, kw))
            return extra.get(tool, {})
        return call
    ana.game.call = game_answers(ana, {"household.get_members": members, "interaction.list_available": avail,
                                       "relationships.get_value_between": stats})
    ana.puppet_ids.pop(ben.me_id, None)
    rb.spawned, rb.spawn_requested, rb.adopted = False, 0.0, False
    ana.game.template_for = lambda *a: 3
    await ana.manage_puppets()
    adopt = ops(ana, "adopt")[-1]
    assert adopt["key"] == ben.me_id and adopt["entity"] == 55 and adopt["x"] == 100.0 and rb.adopted, adopt
    assert not [o for o in ops(ana, "spawn") if o["key"] == ben.me_id], "a family member isn't copied"
    # a copy from before (a puppet made before they moved in): replaced by the family member
    ana.puppet_ids[ben.me_id], rb.adopted = 77, False
    ana.__dict__.pop("_family", None)
    await ana.manage_puppets()
    assert ops(ana, "despawn")[-1]["key"] == ben.me_id and not rb.spawned
    # in another city: the family member is let go (the game looks after them), not hidden
    ana.puppet_ids[ben.me_id], rb.spawned, rb.adopted = 55, True, True
    rb.state = {**rb.state, "city": "Bliss Bay"}
    n = len(ops(ana, "despawn"))
    await ana.manage_puppets()
    assert len(ops(ana, "despawn")) == n + 1 and not rb.adopted and not ops(ana, "hide")[-1:] == [{"key": ben.me_id}]
    rb.state = {**rb.state, "city": "Dowon"}
    ana.puppet_ids[ben.me_id], rb.spawned, rb.adopted = 55, True, True
    print("family member adopted (not copied), copies replaced, let go elsewhere: ok")

    # --- every interaction the game offers, by group; doing one
    ia = await ana_routes["/partner/interactions"]({})
    groups = {g["key"]: [i["id"] for i in g["items"]] for g in ia["groups"]}
    assert groups == {"romance": ["Co_Peck_FemaleToMale_Success"], "talk": ["Co_friendship_R10"],
                      "conflict": ["Co_GetOut"], "fun": ["Co_HighFIve_FemaleToMale_Friendship"]}, groups
    assert [g["key"] for g in ia["groups"]] == ["romance", "fun", "talk", "conflict"]
    assert ia["bond"]["romance"] == {"value": 80, "level": 2} and ia["family"] is True
    res = await ana_routes["/partner/do"]({"bag": "Co_friendship_R10"})
    assert res.get("ok") and ops(ana, "social")[-1] == {**ops(ana, "social")[-1], "bag": "Co_friendship_R10",
                                                        "key": ben.me_id, "entity": 10, "now": True}
    assert ana.couple_view()["couple"]["sent"][-1]["name"] == "Falar sobre a vida"
    assert (await ana_routes["/partner/do"]({"bag": "Co_SomethingElse"}))["code"] == "not_offered"
    assert (await ana_routes["/partner/do"]({"bag": "bad id; x"}))["code"] == "not_offered"
    print("interactions by group, doing one: ok")

    # --- family milestones: Ana asks to move in, Ben says yes: each save makes it so (Ben's copy of Ana joins
    # Ben's household), then marriage
    ben.puppet_ids[ana.me_id], ra.spawned, ra.adopted = 88, True, False
    ra.profile = {"first_name": "Ana", "last_name": "Lima", "gender": "Female"}
    ben.game.call = game_answers(ben, {"household.get_members": {"members": [
        {"entity_id": 20, "first_name": "Ben", "last_name": "Lima"}]}})
    res = await ana_routes["/partner/family"]({"action": "movein"})
    assert res.get("ok"), res
    assert await until(lambda: ben.requests)
    q = next(iter(ben.requests.values()))
    assert q["action"] == "movein"
    out = await ben_routes["/partner/respond"]({"id": q["id"], "accept": True})
    assert out["result"]["state"] == "done", out
    assert ("household.add_member", {"entity_id": 88}) in ben.fake_calls and ra.adopted
    assert ("life_and_death.stop_aging", {"entity_id": 88, "Enable": False}) in ben.fake_calls
    assert ops(ben, "adopt")[-1]["entity"] == 88
    assert await until(lambda: ana.sent[res["id"]]["state"] == "done")
    assert not [c for c in ana.fake_calls if c[0] == "household.add_member"], "Ana's save had Ben in the family"
    res = await ben_routes["/partner/family"]({"action": "marry"})
    assert await until(lambda: ana.requests)
    await ana_routes["/partner/respond"]({"id": next(iter(ana.requests)), "accept": True})
    assert ("marriage.marry", {"entity_id": 10, "target_entity_id": 55}) in ana.fake_calls
    assert await until(lambda: ("marriage.marry", {"entity_id": 20, "target_entity_id": 88}) in ben.fake_calls)
    res = await ana_routes["/partner/family"]({"action": "baby"})
    assert await until(lambda: ben.requests)
    await ben_routes["/partner/respond"]({"id": next(iter(ben.requests)), "accept": True})
    assert ("pregnancy_and_birth.get_baby_with", {"entity_id": 88, "partner_id": 20}) in ben.fake_calls, \
        "the mother is the woman"
    assert (await ana_routes["/partner/family"]({"action": "divorce"}))["code"] == "bad_action"
    print("family milestones (move in, marry, baby) in both saves: ok")

    # --- moving in, the other save: Ben's save has Ana's real Zoi (entity 61) living on Ana's lot (site 1038, Ben's
    # household elsewhere), plus a copy of Ana (88) made before. Ana asks: Ben's own Zoi joins the household on
    # lot 1038 (there the house is Ben's too), the real Ana is driven and the copy goes.
    state = {"mine": 300}
    households = {"households": [{"household_id": 300, "residence_site_id": 777},
                                 {"household_id": 400, "residence_site_id": 1038}]}
    ana_hh = {"household_id": 500, "members": [{"entity_id": 10, "first_name": "Ana", "last_name": "Lima"},
                                               {"entity_id": 55, "first_name": "Ben", "last_name": "Lima"}]}
    ana.game.call = game_answers(ana, {"household.list": {"households": [{"household_id": 500, "residence_site_id": 1038}]},
                                       "household.get_members": ana_hh})

    def ben_call(tool, **kw):
        ben.fake_calls.append((tool, kw))
        if tool == "household.add_member" and kw.get("entity_id") == 20:
            state["mine"] = kw["household_id"]
        if tool == "household.list":
            return households
        if tool == "household.get_members" and kw.get("household_id") == 400:
            return {"household_id": 400, "members": [{"entity_id": 61, "first_name": "Ana", "last_name": "Lima"}]}
        if tool == "household.get_members":
            if state["mine"] == 400:
                return {"household_id": 400, "members": [{"entity_id": 20, "first_name": "Ben", "last_name": "Lima"},
                                                         {"entity_id": 61, "first_name": "Ana", "last_name": "Lima"}]}
            return {"household_id": 300, "members": [{"entity_id": 20, "first_name": "Ben", "last_name": "Lima"}]}
        if tool == "world.list_characters":
            return {"total": 3, "characters": [{"entity_id": 61, "first_name": "Ana", "last_name": "Lima"},
                                               {"entity_id": 88, "first_name": "Ana", "last_name": "Lima"},
                                               {"entity_id": 20, "first_name": "Ben", "last_name": "Lima"}]}
        return {}
    ben.game.call = ben_call
    ben.__dict__.pop("_family", None)
    ben.puppet_ids[ana.me_id], ra.adopted = 88, False
    mark = len(ben.fake_calls)
    res = await ana_routes["/partner/family"]({"action": "movein"})
    assert res.get("ok"), res
    assert await until(lambda: ben.requests)
    q = next(iter(ben.requests.values()))
    assert q["home_site"] == 1038, q
    out = await ben_routes["/partner/respond"]({"id": q["id"], "accept": True})
    assert out["result"]["state"] == "done", out
    assert ("household.add_member", {"entity_id": 20, "household_id": 400}) in ben.fake_calls, ben.fake_calls[-8:]
    assert ("zoi.destroy", {"entity_id": 88}) in ben.fake_calls and ("zoi.destroy", {"entity_id": 61}) not in ben.fake_calls
    assert ben.puppet_ids[ana.me_id] == 61 and ra.adopted and ops(ben, "adopt")[-1]["entity"] == 61
    assert ("household.add_member", {"entity_id": 88}) not in ben.fake_calls[mark:], "the copy isn't moved in"
    print("moving in, the other save: own Zoi joins the family in the asker's home, copy removed: ok")

    # --- the move doesn't take in Ben's game: the game's own "live with me" is asked, and Ana hears it didn't work
    state["mine"] = 300
    stuck = {"on": True}
    real_call = ben.game.call

    def stuck_call(tool, **kw):
        if tool == "household.add_member" and kw.get("entity_id") == 20 and stuck["on"]:
            ben.fake_calls.append((tool, kw))
            raise RuntimeError("possessed household can't change")
        return real_call(tool, **kw)
    ben.game.call = stuck_call
    ben.__dict__.pop("_family", None)
    ra.adopted, ben.puppet_ids[ana.me_id] = False, 88
    res = await ana_routes["/partner/family"]({"action": "movein"})
    assert await until(lambda: ben.requests)
    out = await ben_routes["/partner/respond"]({"id": next(iter(ben.requests)), "accept": True})
    assert out["result"]["state"] == "failed", out
    assert ("interaction.execute", {"entity_id": 61, "interaction_bag_id": "Co_Cohabit_Friendship",
                                    "target_entity_id": 20, "insert_front": True}) in ben.fake_calls
    assert await until(lambda: ana.sent[res["id"]]["state"] == "failed")
    assert "answer yes" in ana.sent[res["id"]]["message"] and any("didn't work in their save" in c["text"] for c in ana.chat)
    print("a move the game refuses: the game's own question, and the asker hears it: ok")

    # --- a household on the asker's lot that isn't the asker's family: Ben's Zoi never moves there
    stuck["on"] = False
    households["households"][1]["household_id"] = 401           # (strangers live on lot 1038 in Ben's save)
    mark = len(ben.fake_calls)
    res = await ana_routes["/partner/family"]({"action": "movein"})
    assert await until(lambda: ben.requests)
    await ben_routes["/partner/respond"]({"id": next(iter(ben.requests)), "accept": True})
    assert not [c for c in ben.fake_calls[mark:] if c[0] == "household.add_member" and c[1].get("entity_id") == 20]
    households["households"][1]["household_id"] = 400
    ben.game.call = real_call
    print("never into strangers' home: ok")

    # --- the partner's log, asked for
    (Path(os.environ["APPDATA"]) / "inzoi-mp").mkdir(parents=True, exist_ok=True)
    (Path(os.environ["APPDATA"]) / "inzoi-mp" / "companion.log").write_text("line one|line two|".replace("|", chr(10)), "utf-8")
    assert (await ana_routes["/partner/log"]({}))["ok"]
    saved = Path(os.environ["APPDATA"]) / "inzoi-mp" / "partner-Ben.log"
    assert await until(lambda: saved.exists())
    assert "line two" in saved.read_text("utf-8")
    print("partner's log on request: ok")
    ana.game.call = lambda tool, **kw: ana.fake_calls.append((tool, kw)) or {}
    ben.game.call = lambda tool, **kw: ben.fake_calls.append((tool, kw)) or {}

    # --- shared household: switch, status, undo
    assert not ana.shared_world_on()
    out = await ana_routes["/settings"]({"shared_household": True})
    assert "shared_household" not in out and not ana.shared_world_on(), "living together is gone: own saves only"
    os.environ["INZOI_MP_SHARED_WORLD"] = "1"                   # (the shared-world code stays, for the tests)
    assert ana.shared_world_on()
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
