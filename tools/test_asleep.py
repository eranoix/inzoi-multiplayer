"""A Zoi asleep isn't talked to - a partner's Zoi asleep in their game is asleep here too (the game's own "Sleep" buff,
which its talk rule checks), run for real (lupa) against a fake game, and the companion's side:
- B2 (lua/B2/Main.lua, asleep): their Zoi here gets the game's "Sleep" buff while they sleep (with a buff detail: the
  game drops an AddBuff without one), loses it when they wake; a buff the game cleared is given again, one left from the
  save is taken off; never taken off a Zoi sleeping by its own action; "wake" takes off what an earlier session gave
- the companion: whether my Zoi is asleep goes out with my state; a partner's goes to the controller on a change only
  (and once at the start), off while they're hidden or quiet; an older program's "sleep" guess counts; the Zois given
  the buff are kept, and a fresh controller takes it off them first

    python tools/test_asleep.py
"""
import json
import os
import re
import sys
import tempfile
import time
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

B2 = Path(__file__).resolve().parents[1] / "mod" / "InZOIMultiplayer" / "lua" / "B2" / "Main.lua"

# Two Zois: mine (24534) and the partner's here (25674). AddBuff is the game's: deferred to its next tick (FLUSH), and
# dropped without a detail.
FAKE = r"""
CHARS = {}
local function zoi(id)
    local z = { buffs = {}, pending = {}, bag = nil }
    z.comp = {
        HasBuff = function(_, b) return z.buffs[b] == true end,
        AddBuff = function(_, b, detail, emo) if detail ~= nil then z.pending[#z.pending + 1] = b end end,
        RemoveBuff = function(_, b, canceled) z.buffs[b] = nil end,
    }
    z.char = { GetComponent = function(_, n) return n == 9 and z.comp or nil end, z = z }
    CHARS[id] = z
end
zoi(24534); zoi(25674); zoi(30000)
function FLUSH() for _, z in pairs(CHARS) do for _, b in ipairs(z.pending) do z.buffs[b] = true end; z.pending = {} end end
function char(n) local z = CHARS[n]; return z and z.char or nil end
function current_bag(c) return c.z.bag end
inzoi = { EB2ComponentType = { Buff = 9 } }
INZOI = { FB1BuffDetail = function() return {} end }
PUPPETS = {}
function has(id) return CHARS[id].buffs.Sleep == true end
"""


def b2_asleep():
    if LuaRuntime is None:
        print("asleep tests (B2): lupa isn't installed (pip install lupa): skipped")
        return
    src = B2.read_text("utf-8")
    block = re.search(r"^-- -+ asleep\n(.*?)^-- -+ puppets\n", src, re.M | re.S)
    asleep = re.search(r"^    asleep = function\(op\)\n(.*?)^    end,\n", src, re.M | re.S)
    wake = re.search(r"^    wake = function\(op\)\n(.*?)^    end,\n", src, re.M | re.S)
    assert block and asleep and wake, "the asleep section / asleep, wake ops not found in B2/Main.lua"
    lua = LuaRuntime()
    lua.execute(FAKE)
    g = lua.globals()
    lua.execute(block.group(1) + "\nASLEEP = function(op)\n" + asleep.group(1) + "end\n"
                "WAKE = function(op)\n" + wake.group(1) + "end\n"
                "HAS_SLEEP, KEEP = has_sleep, sleep_keep")
    lua.execute("P = { id = 25674 }; PUPPETS.p1 = P")

    # They fall asleep in their game: their Zoi here gets the game's Sleep buff (a detail with it, or it's dropped)
    text = g.ASLEEP(lua.eval("{ key = 'p1', on = true }"))
    lua.execute("FLUSH()")
    assert g.has(25674) and g.P.asleep is True, text
    assert text == "p1 asleep here (the game's Sleep buff)", text
    assert g.ASLEEP(lua.eval("{ key = 'p1', on = true }")) is None, "given again while it has it"
    assert g.ASLEEP(lua.eval("{ key = 'nobody', on = true }")) is None
    print("B2: their Zoi asleep there gets the game's Sleep buff here (once): ok")

    # The game clears it (their Zoi's actions cleared here): given again a moment later
    lua.execute("CHARS[25674].buffs.Sleep = nil")
    g.KEEP()
    lua.execute("FLUSH()")
    assert g.has(25674), "a cleared buff wasn't given again"
    print("B2: a Sleep buff the game cleared is given again: ok")

    # They wake up: off
    text = g.ASLEEP(lua.eval("{ key = 'p1', on = false }"))
    assert not g.has(25674) and text == "p1 awake here (the game's Sleep buff)", text
    # one the save kept (a past session's): taken off by the keep
    lua.execute("CHARS[25674].buffs.Sleep = true")
    g.KEEP()
    assert not g.has(25674), "a buff left from the save stayed"
    # a Zoi sleeping by its own action (the game's sleep): never taken off
    lua.execute("CHARS[25674].buffs.Sleep = true; CHARS[25674].bag = 'Sleep_Bed'")
    g.KEEP()
    assert g.has(25674), "taken off a Zoi asleep by itself"
    lua.execute("CHARS[25674].bag = 'Object_Sofa_Dozing'")
    g.KEEP()
    assert g.has(25674), "taken off a Zoi dozing by itself"
    lua.execute("CHARS[25674].bag = nil")
    print("B2: awake there, awake here; a buff the save kept comes off, a Zoi's own sleep is left alone: ok")

    # an earlier session's (the save keeps buffs): "wake" takes them off, not off one asleep by itself, nor gone ones
    lua.execute("CHARS[25674].buffs.Sleep = true; CHARS[30000].buffs.Sleep = true; CHARS[30000].bag = 'Sleep_Bed'")
    text = g.WAKE(lua.eval("{ ids = { 25674, 30000, 99999 } }"))
    assert not g.has(25674) and g.has(30000), text
    assert text == "wake: 1 Zoi(s) an earlier session put to sleep woke", text
    assert g.WAKE(lua.eval("{ ids = { 25674 } }")) is None
    # my own Zoi: read for the report
    lua.execute("CHARS[24534].buffs.Sleep = true")
    assert g.HAS_SLEEP(24534) is True and g.HAS_SLEEP(25674) is False
    print("B2: wake takes off what an earlier session gave; my Zoi's sleep is read from the game's buff: ok")


def companion_side():
    comp = companion.Companion({"key": "k" * 20, "name": "me", "server": "x", "port": 1, "password": ""})
    comp.mod["state"] = "running"
    comp.me_id, comp.my_entity, comp.leader = "me", 24534, "me"
    comp.state_base = {"act": None}
    r = companion.Remote("p1", "Chey")
    comp.remotes["p1"] = r
    r.spawned, r.hidden = True, False
    comp.puppet_ids["p1"] = 25674

    # mine: from the controller's report, out with my state
    comp.on_report("asleep", "1")
    assert comp.my_asleep is True and comp.state_base["asleep"] is True and comp.extra_state({})["asleep"] is True
    comp.on_report("asleep", "0")
    assert comp.my_asleep is False and comp.state_base["asleep"] is False and comp.extra_state({})["asleep"] is False
    print("companion: whether my Zoi is asleep goes out with my state: ok")

    def ops():
        out = [(o["key"], o["on"]) for _, _, o in comp.mail.ops if o["op"] == "asleep"]
        comp.mail.ops.clear()
        return out

    def saved():
        return json.loads(companion.CONFIG_FILE.read_text("utf-8")).get("slept")

    r.state, r.state_at = {"x": 1, "y": 1, "z": 1, "asleep": False}, time.time()
    comp.hold_sleep()
    assert ops() == [("p1", False)], "not told once at the start"
    comp.hold_sleep()
    assert ops() == []
    r.state["asleep"] = True
    comp.hold_sleep()
    assert ops() == [("p1", True)] and comp.cfg["slept"] == [25674] and saved() == [25674]
    comp.hold_sleep()
    assert ops() == [], "sent again unchanged"
    r.hidden = True
    comp.hold_sleep()
    assert ops() == [("p1", False)] and comp.cfg["slept"] == [] and saved() == []
    r.hidden = False
    comp.hold_sleep()
    assert ops() == [("p1", True)]
    r.state_at = time.time() - 10                # (they went quiet)
    comp.hold_sleep()
    assert ops() == [("p1", False)]
    r.state_at = time.time()
    r.state = {"x": 1, "y": 1, "z": 1, "sleep": True}     # (an older program: its "sleep" guess)
    comp.hold_sleep()
    assert ops() == [("p1", True)]
    del comp.puppet_ids["p1"]                    # (their Zoi isn't here)
    comp.hold_sleep()
    assert ops() == [] and r.sleep_sig is None
    comp.puppet_ids["p1"] = 25999                # (another one: told again)
    comp.hold_sleep()
    assert ops() == [("p1", True)] and 25999 in comp.cfg["slept"]
    print("companion: their sleep goes to the controller on a change only; off hidden or quiet: ok")

    # a fresh controller: the last one's Sleep comes off first, then theirs is given again
    comp.on_report("restart", "123456")
    wake = [o for _, _, o in comp.mail.ops if o["op"] == "wake"]
    assert len(wake) == 1 and wake[0]["ids"] == [25674, 25999], wake
    assert comp.cfg["slept"] == [] and r.sleep_sig is None and saved() == []
    comp.mail.ops.clear()
    comp.hold_sleep()
    assert ops() == [("p1", True)]
    print("companion: a fresh controller takes the last one's Sleep off, then gives theirs again: ok")


def main():
    b2_asleep()
    companion_side()
    print("asleep tests passed")


main()
