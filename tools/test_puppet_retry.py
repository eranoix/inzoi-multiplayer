"""Puppet spawn retry and mailbox rules, without the game:
- a puppet asked for but missing from the controller's heartbeat for >10 s (its op gone) is asked for again
- "spawn_failed" and an unexpected "despawned" bring it back; heartbeats listing a puppet we despawned are stale
- MPCmd.lua is rewritten only when something changed, MPSeq.lua follows it; a held file doesn't raise
- connection loss: friends' Zois stay through the grace period, and leave after it

    python tools/test_puppet_retry.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["INZOI_MP_DOCUMENTS"] = tempfile.mkdtemp()                 # never the real game's mod folder
os.environ["INZOI_MP_GAME_URL"] = "http://127.0.0.1:9/mcp"
os.environ["APPDATA"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import companion  # noqa: E402
import mailbox  # noqa: E402


def make():
    comp = companion.Companion({"key": "k" * 20, "name": "me", "server": "x", "port": 1, "password": ""})
    comp.mod["state"] = "running"
    r = companion.Remote("p1", "Ana")
    r.profile, r.state = {"gender": "F", "growth_step": "Adult"}, {"city": "", "x": 0, "y": 0, "z": 0}
    comp.remotes["p1"] = r
    return comp, r


def leftovers():
    """Puppets saved into the game (nobody plays them now): all removed, without tripping over the dict."""
    comp = companion.Companion({"key": "k" * 20, "name": "me", "server": "x", "port": 1, "password": ""})
    comp.writer, comp.me_id, comp.connected_at = object(), "me", time.time() - 60
    comp.on_report("up", "pa=5,pb=6,pc=7")
    assert not comp.puppet_ids and set(comp.despawning) == {"pa", "pb", "pc"}, (comp.puppet_ids, comp.despawning)
    print("  ok: three leftover puppets removed in one heartbeat")


async def main():
    leftovers()
    comp, r = make()
    comp.game.template_for = lambda *a, **k: 42
    await comp.manage_puppets()
    assert r.spawned and comp.mail.pending("p1", "spawn")

    # Heartbeat without it while the op is still queued: wait.
    r.spawn_requested -= 11
    comp.on_report("up", "")
    assert r.spawned
    # The op ran out and it never came: ask again.
    comp.mail.ops.clear()
    comp.on_report("up", "")
    assert not r.spawned
    await comp.manage_puppets()
    assert r.spawned

    # It shows up: kept, even long after.
    comp.on_report("up", "p1=77")
    assert r.spawned and comp.puppet_ids == {"p1": 77}
    r.spawn_requested -= 60
    comp.mail.ops.clear()
    comp.on_report("up", "p1=77")
    assert r.spawned

    # The controller says the spawn failed: tried again a few seconds later.
    comp.on_report("spawn_failed", "p1")
    assert not r.spawned and 0 < time.time() - r.spawn_requested < 8
    r.spawn_requested -= 4
    await comp.manage_puppets()
    assert r.spawned

    # Lost by the controller (we didn't ask): back again. Asked by us: stays gone, stale heartbeats ignored.
    comp.on_report("spawned", "p1 78")
    comp.on_report("despawned", "p1")
    assert not r.spawned
    comp.on_report("spawned", "p1 79")
    r.spawned = True
    comp.drop_remote(r)
    assert not r.spawned and "p1" in comp.despawning
    comp.on_report("up", "p1=79")                     # written before the despawn ran
    assert not r.spawned and "p1" not in comp.puppet_ids
    comp.on_report("despawned", "p1")
    assert "p1" not in comp.despawning and not r.spawned

    # Mailbox: unchanged -> not rewritten; MPSeq after MPCmd; a held file doesn't raise.
    d = Path(tempfile.mkdtemp())
    mb = mailbox.Mailbox(d)
    assert mb.flush() and (d / "MPCmd.lua").exists() and (d / "MPSeq.lua").exists()
    seq = mb.seq
    assert mb.flush() and mb.seq == seq
    assert (d / "MPSeq.lua").read_text() == f"return {{seq = {seq}}}\n"
    mb.add_point("p1", 1, 0, 0, 0, 0)
    assert mb.flush() and mb.seq == seq + 1
    # Points from their states (small counts) and their skeleton frames (from 10 000 000) mixed, as at a loading
    # screen: the controller gets one rising count (a drop read as "their program restarted": teleported back and
    # forth, seen 10 times in half a second); a repeat of the same sender's count isn't added twice.
    for src in (10_000_001, 10_000_002, 57, 58, 58, 10_000_003, 59):
        mb.add_point("p2", src, src % 1000, 0, 0, 0)
    sent = [pt[0] for pt in mb.trails["p2"]]
    assert len(sent) == 6 and sent == sorted(sent) and len(set(sent)) == 6, sent
    time.sleep(0.1)                                           # (this program restarted later: still above - the
    mb2 = mailbox.Mailbox(Path(tempfile.mkdtemp()))           # count starts from the clock, 100 a second, points come 30)
    mb2.add_point("p2", 1, 0, 0, 0, 0)
    assert mb2.trails["p2"][0][0] > sent[-1], (mb2.trails["p2"], sent)
    print("points from states and skeleton frames: one rising count for the controller: ok")
    mb.set_member("p1", "{}")
    orig = mailbox.write_atomic

    def held(path, text):
        raise PermissionError(path)
    mailbox.write_atomic = held
    try:
        assert mb.flush() is False
    finally:
        mailbox.write_atomic = orig
    assert mb.flush() and "p1" in (d / "MPBulk.lua").read_text()
    assert f"bulk = {mb.bulk_seq}" in (d / "MPCmd.lua").read_text()
    # Paused: ops don't run out.
    mb.paused = True
    mb.op("x")
    mb.ops = [(e - 10, m, o) for e, m, o in mb.ops]   # as if 10 s went by...
    mb.last_flush -= 10                               # ...all of them paused
    mb.flush()
    assert mb.pending(None, "x")

    # Connection loss: Zois stay through the grace period, leave after.
    comp, r = make()
    r.spawned = True
    comp.puppet_ids["p1"] = 5
    task = asyncio.create_task(comp.drop_after_grace(0.2))
    await asyncio.sleep(0.05)
    assert "p1" in comp.remotes
    await task
    assert not comp.remotes and "p1" in comp.despawning

    # Their skeleton drives their Zoi and no action of theirs was seen yet ("skip"): a state is still read whole (it
    # raised on "skip".get and the rest - their new outfit - was lost), and an odd "act" (not an object) is ignored.
    for act in (None, "Sleep_Bed", {"bag": "Sit", "npc": True}):
        comp, r = make()
        r.spawned, r.act_sig = True, None
        comp.puppet_ids["p1"] = 5
        comp.pose_live = lambda _r: True
        ops = []
        comp.mail.op = lambda name, **f: ops.append((name, f))
        comp.set_state(r, {"x": 1, "y": 2, "z": 3, "act": act, "anim": "A1", "outfit": "Sleep"})
        assert ("outfit", {"key": "p1", "category": "Sleep"}) in ops, (act, ops)
    # This save's copy of them (adopted family member) takes their look as their game has it: when it's adopted, and
    # when their profile changes (a haircut, aging: their own game's look under their skeleton)
    comp, r = make()
    r.adopted, r.profile_hash = True, "h1"
    ops = []
    comp.mail.op = lambda name, **f: ops.append((name, f))
    comp.on_report("spawned", "p1 5")
    assert ("relook", {"key": "p1"}) in ops, ops
    print("an adopted partner's Zoi takes their current look: ok")

    # Aging (the players' choice: adults, aging slowly): every phase lasts cfg "aging_slower" (200) times the game's own,
    # patched into the game's Aging table once per loaded world; my Zoi and this save's copy of the partner age
    # (a stop left by 0.9.56 lifted); cfg "stop_aging": true stops it while playing together, as 0.9.56 did.
    comp, r = make()
    r.adopted = True
    comp.puppet_ids["p1"] = 5
    comp.my_entity, comp.world = 10, "play"
    comp.mp_active = lambda: True
    table = {s: 1440 for s in companion.Companion.AGING_STEPS}
    calls = []

    def fake(tool, **kw):
        calls.append((tool, kw))
        if tool == "data.table_get":
            return {"_row_name": kw["Row"], "timeToNextAge": table[kw["Row"]]}
        if tool == "data.patch":
            step = kw["Query"].split("Id=")[1].split("]")[0]
            table[step] = int(kw["Value"])
            return {"result": "Applied"}
        return {}
    comp.game.call = fake
    companion.save_config = lambda c: None
    await comp.keep_young()
    await comp.keep_young()
    assert set(table.values()) == {288000}, table
    assert comp.cfg["aging_base"]["Adult"] == 1440 and comp.cfg["aging_factor"] == 200
    ages = [kw for tool, kw in calls if tool == "life_and_death.stop_aging"]
    assert sorted(kw["entity_id"] for kw in ages) == [5, 10] and all(kw["Enable"] is False for kw in ages), ages
    assert len([c for c in calls if c[0] == "data.patch"]) == 9, "patched more than once in one loaded world"
    print("every phase of life lasts 200x longer, both Zois age (a 0.9.56 stop lifted): ok")

    calls.clear()                                             # (the program restarts, the game still patched)
    comp.save_session = {"started": 123.0}
    await comp.keep_young()
    assert set(table.values()) == {288000} and not [c for c in calls if c[0] == "data.patch"], "patched again on top"
    assert not [c for c in calls if c[0] == "life_and_death.stop_aging"], "stop_aging again (a cheat notice on screen)"
    table.update({s: 1440 for s in table})                    # (the game started again: its own values back)
    comp.save_session = {"started": 124.0}
    await comp.keep_young()
    assert set(table.values()) == {288000}, table
    comp.cfg["aging_slower"] = 5                              # (another pace chosen: from the game's own value)
    comp.save_session = {"started": 125.0}
    await comp.keep_young()
    assert set(table.values()) == {7200}, table
    table.update({s: 2000 for s in table})                    # (a game update changed its own values)
    comp.save_session = {"started": 126.0}
    await comp.keep_young()
    assert set(table.values()) == {10000} and comp.cfg["aging_base"]["Adult"] == 2000, table
    print("the aging pace isn't multiplied twice, follows a new factor and a game update: ok")

    calls.clear()
    comp.cfg["stop_aging"] = True                             # (0.9.56's way: nobody ages while playing together)
    comp.save_session = {"started": 200.0}
    await comp.keep_young()
    ages = [kw for tool, kw in calls if tool == "life_and_death.stop_aging"]
    assert sorted(kw["entity_id"] for kw in ages) == [5, 10] and all(kw["Enable"] is True for kw in ages), ages
    print("stop_aging: true still stops it while playing together: ok")

    # A partner's Zoi standing in WalkToContext (it has no walk of its own: their skeleton moves it) is freed after
    # STUCK_WALK s - not before, not while it plays the social itself, not a hidden one.
    comp, r = make()
    r.spawned, r.hidden = True, False
    comp.puppet_ids["p1"] = 25674
    now = {"action_type": "WalkToContext", "interaction_bag_id": "friendship_R03_2_FeedBack", "playing": True}
    calls = []
    comp.game.call = lambda tool, **kw: calls.append((tool, kw)) or (now if tool == "interaction.get_current" else {})
    resets = lambda: [kw for tool, kw in calls if tool == "movement.reset_zoi"]               # noqa: E731
    await comp.unstick_puppets()
    await comp.unstick_puppets()
    assert not resets(), "freed at once"
    comp._walk_stuck["p1"] = (comp._walk_stuck["p1"][0], time.time() - companion.STUCK_WALK - 1)
    await comp.unstick_puppets()
    assert resets() == [{"entity_id": 25674, "Teleport": False}], resets()
    calls.clear()
    now.update(action_type="PlayAnimContext")                 # (walked into place / playing it: left alone)
    await comp.unstick_puppets()
    now.update(action_type="WalkToContext", interaction_bag_id="LoverHugDance_Feedback")
    await comp.unstick_puppets()
    comp._walk_stuck["p1"] = (comp._walk_stuck["p1"][0], time.time() - companion.STUCK_WALK - 1)
    r.hidden = True                                           # (elsewhere: not looked at)
    await comp.unstick_puppets()
    assert not resets() and "p1" not in comp._walk_stuck
    print("a partner's Zoi stuck walking into place is freed after a few seconds: ok")

    # Two bodies in one place: my Zoi lying on the side of the bed where my partner's lies in their game. Whoever got
    # there later moves to the other side (only my own Zoi is moved); the one there first stays; on a tie the host stays.
    def spot(comp, r, mine_age, their_age, gap=20.0, host=False):
        now = time.time()
        comp.me_id, comp.leader, comp.my_entity, comp.cur_target = "me", ("me" if host else "p1"), 24534, 15725
        comp.cur_act = {"bag": "Sleep_Bed", "obj": "Bed01", "ox": 73657.0, "oy": 25786.0, "oz": 76.0}
        comp.cur_act_since, comp.fast_pos = now - mine_age, {"x": 73767.0, "y": 25849.0, "z": 76.0, "at": now}
        comp._reslot_at = 0
        r.state, r.state_at = {"x": 73767.0 + gap, "y": 25849.0, "z": 76.0, "ts": now, "act_t": now - their_age,
                               "act": {"bag": "Sleep_Bed", "obj": "Bed01", "ox": 73657.0, "oy": 25786.0, "oz": 76.0}}, now
        comp.mail.ops.clear()
        comp.check_spot()
        return [o for _, _, o in comp.mail.ops if o.get("op") == "reslot"]
    comp, r = make()
    moved = spot(comp, r, mine_age=5, their_age=60)                  # (I lay down after she did)
    assert len(moved) == 1 and moved[0]["target"] == 15725 and moved[0]["bag"] == "Sleep_Bed", moved
    assert abs(moved[0]["ax"] - 73787.0) < 1, moved
    assert not spot(comp, r, mine_age=60, their_age=5), "the one there first was moved"
    assert not spot(comp, r, mine_age=5, their_age=60, gap=150), "moved though 1.5 m apart"
    assert not spot(comp, r, mine_age=1, their_age=60), "moved while still getting there"
    assert not spot(comp, r, mine_age=10, their_age=10, host=True), "the host moved on a tie"
    assert spot(comp, r, mine_age=10, their_age=10, host=False), "the other PC didn't move on a tie"
    print("two bodies on one spot: the later one moves to the other side (the host stays on a tie): ok")

    # Needs first (Chey's Zoi starved chatting with Arthur's, 2026-11-04): hungry and busy with the partner's Zoi ->
    # that's stopped; while hungry, a request left unanswered is declined, not accepted.
    comp, r = make()
    comp.my_entity, comp.puppet_ids["p1"], comp.cur_target = 24534, 25674, 25674
    comp.cur_act = {"bag": "Co_Business_R07", "player": "p1"}
    calls = []
    stats = {"Hunger": {"value": -70}, "Sleep": {"value": 20}, "Toilet": {"value": 0}, "Energy": {"value": 10}}
    comp.game.call = lambda tool, **kw: calls.append((tool, kw)) or ({"stats": stats} if tool == "stat.get_all" else {})
    await comp.needs_guard()
    assert comp.needs_low == ["Hunger"] and ("interaction.cancel", {"entity_id": 24534}) in calls, calls
    calls.clear()
    stats["Hunger"]["value"] = 50
    await comp.needs_guard()
    assert comp.needs_low == [] and not [c for c in calls if c[0] == "interaction.cancel"]
    comp.needs_low = ["Hunger"]
    answered = []

    async def respond(rid, accept, always=False, by_player=False):
        answered.append((rid, accept, by_player))
    comp.couple_respond = respond
    comp.requests["q1"] = {"id": "q1", "from": "Ana", "from_id": "p1", "action": "game", "bag": "Co_Business_R07",
                           "at": time.time() - 10}
    task = asyncio.get_running_loop().create_task(comp.answer_loop())
    await asyncio.sleep(1.3)
    task.cancel()
    assert answered and answered[0][1] is False, answered
    print("needs first: hungry, the chat with the partner's Zoi is stopped and their request declined: ok")

    # A need near the end (Chey's Zoi died of sleep, 2026-11-04): said in the panel - the worst one, not every 3 s; the
    # bladder is the game's "Toilet".
    comp, r = make()
    comp.my_entity = 24534
    stats = {"Hunger": {"value": -80}, "Sleep": {"value": -95}, "Toilet": {"value": -50}, "Energy": {"value": 10}}
    comp.game.call = lambda tool, **kw: {"stats": stats} if tool == "stat.get_all" else {}
    await comp.needs_guard()
    lines = [c["text"] for c in comp.chat if c.get("system")]
    assert lines == ["Your Zoi urgently needs sleep"], lines
    assert "Toilet" in comp.needs_low, comp.needs_low
    await comp.needs_guard()
    assert len([c for c in comp.chat if c.get("system")]) == 1, "warned again at once"
    comp.needs_warned_at -= companion.NEED_WARN_EVERY + 1
    stats["Sleep"]["value"] = 50
    await comp.needs_guard()
    assert comp.chat[-1]["text"] == "Your Zoi urgently needs food", comp.chat[-1]
    print("a need near the end: the worst one said in the panel, again a while later; the bladder read: ok")

    # Picking the partner's Zoi from the family portraits while they're online (they play it): back to mine at once;
    # offline, it can be played here.
    comp, r = make()
    comp.puppet_ids["p1"] = 25674
    calls = []
    comp.game.call = lambda tool, **kw: calls.append((tool, kw)) or {}
    assert await comp.own_zoi(24534) == 24534                      # (mine: remembered)
    r.state_at = time.time()
    assert await comp.own_zoi(25674) == 24534, "kept playing the online partner's Zoi"
    assert ("entity.possess", {"entity_id": 24534}) in calls, calls
    calls.clear()
    r.state_at = time.time() - 60                                  # (they're offline)
    assert await comp.own_zoi(25674) == 25674 and not calls
    print("an online partner's Zoi can't be played here: back to mine; offline it can: ok")

    # The lock files: the online partner's Zoi for the game's switch (B2: MPLock.lua in the mailbox) and the portraits
    # (B1: in/locked.cfg), stamped with the time; offline: nothing locked. A refused switch is explained in the chat.
    import install
    comp, r = make()
    comp.world = "play"
    comp.puppet_ids["p1"] = 25674
    r.state_at = time.time()
    comp.tell_lock()
    cfg = (install.INSTALL_DIR / "in" / "locked.cfg").read_text("utf-8")
    box = (install.MAILBOX_DIR / "MPLock.lua").read_text("utf-8")
    assert cfg.endswith("|25674") and abs(int(cfg.split("|")[0]) - time.time()) < 3, cfg
    assert "ids = { 25674 }" in box and "t = " in box, box
    stamp = cfg
    comp.lock_at -= companion.LOCK_EVERY + 1                       # (rewritten while locked: the time moves on)
    time.sleep(1.1)
    comp.tell_lock()
    assert (install.INSTALL_DIR / "in" / "locked.cfg").read_text("utf-8") != stamp
    comp.on_refused("25674")
    assert any("Ana is online playing their own Zoi" in c["text"] for c in comp.chat), comp.chat
    comp.on_refused("25674")                                       # (said once, not for every key press)
    assert sum("is online playing" in c["text"] for c in comp.chat) == 1
    r.state_at = time.time() - 60                                  # (they're offline)
    comp.tell_lock()
    assert (install.INSTALL_DIR / "in" / "locked.cfg").read_text("utf-8").endswith("|")
    assert "ids = {  }" in (install.MAILBOX_DIR / "MPLock.lua").read_text("utf-8")
    assert str(install.INSTALL_DIR).startswith(os.environ["INZOI_MP_DOCUMENTS"])
    print("lock files: the online partner's Zoi locked (time-stamped, refreshed), unlocked offline; a refusal "
          "explained once: ok")
    print("puppet retry tests passed")


if __name__ == "__main__":
    asyncio.run(main())
