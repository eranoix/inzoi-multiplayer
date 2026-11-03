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

    # Nobody ages while playing together: my Zoi and this save's copy of the partner, once per loaded world
    comp, r = make()
    r.adopted = True
    comp.puppet_ids["p1"] = 5
    comp.my_entity, comp.world = 10, "play"
    comp.mp_active = lambda: True
    calls = []
    comp.game.call = lambda tool, **kw: calls.append((tool, kw)) or {}
    await comp.keep_young()
    await comp.keep_young()
    aged = sorted(kw["entity_id"] for tool, kw in calls if tool == "life_and_death.stop_aging")
    assert aged == [5, 10] and all(kw["Enable"] is True for _, kw in calls), calls
    comp.save_session = {"started": 123.0}                    # (another save loaded: aging is back, stopped again)
    await comp.keep_young()
    assert len(calls) == 4, calls
    comp.cfg["stop_aging"] = False
    comp.save_session = {"started": 456.0}
    await comp.keep_young()
    assert len(calls) == 4
    print("nobody ages while playing together: ok")
    print("puppet retry tests passed")


if __name__ == "__main__":
    asyncio.run(main())
