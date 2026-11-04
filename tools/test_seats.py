"""Two bodies never share one place - the spots a partner's Zoi occupies in their game are its own here, run for real
(lupa) against a fake game, and the companion's side:
- B2 (lua/B2/Main.lua, seats): the spots my Zoi occupies are read from the game's own slots (each once, sorted); the
  spots a partner's Zoi has there are made its occupancy here on the same objects (the same kind, where it is: not
  another chair of the set), given back when it gets up or goes, taken again when the game drops them, never taken from
  a Zoi of this save (that's a clash, told once); spots an earlier session held are given back
- the companion: my spots go out with my state; a partner's go to the controller on a change only (and once at the
  start, to clear), none while they're hidden or their skeleton isn't coming; a clash moves my Zoi only if it got there
  later (the host stays on a tie); a fresh controller gives back what the last one held

    python tools/test_seats.py
"""
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

# A house: my Zoi (24534) on the desk chair before the computer, a second chair of the same kind 2 m off, a bed.
FAKE = r"""
local function arr(t) return { Num = function() return #t end, Get = function(_, i) return t[i] end } end
function slot(name, comp) return { SlotName = name, ComponentName = comp or name, OccupyingEntityId = { Value = -1 } } end
OBJ = {}
local function obj(id, t, x, y, z, slots)
    OBJ[id] = { id = id, t = t, x = x, y = y, z = z, slots = slots }
end
obj(26032, "Chair04_v03", 73172, 25748, 76, { slot("Chair04", "InteractionSlot_Occupying_R"), slot("Chair04", "InteractionSlot_Normal"),
    slot("ExtraRequireSlot") })
obj(26034, "Computer01_v01", 73098, 25598, 150, { slot("InteractionSlot"), slot("InteractionSlot1") })
obj(26205, "Chair04_v03", 73172, 25948, 76, { slot("Chair04") })
obj(15725, "Bed01", 73657, 25786, 76, { slot("InteractionSlot_01_l"), slot("InteractionSlot_01_r") })
obj(99999, "Lamp01", 90000, 90000, 76, { slot("InteractionSlot") })
CHARS = { [24534] = { x = 73173, y = 25746, z = 166 }, [25674] = { x = 73178, y = 25596, z = 166 } }
local function game_obj(o)
    return {
        GetObjectTableId = function() return o.t end,
        GetLocation = function() return { X = o.x, Y = o.y, Z = o.z } end,
        GetComponent = function(_, n) return n == 14 and o or nil end,
    }
end
local MANAGER = {
    FindSite = function(_, id) return { GetObjects = function()
        local ids = {}
        for k in pairs(OBJ) do ids[#ids + 1] = { Value = k } end
        table.sort(ids, function(a, b) return a.Value < b.Value end)
        return arr(ids)
    end } end,
    FindGameObject = function(_, id) local o = OBJ[id.Value]; return o and game_obj(o) or nil end,
    FindCharacter = function(_, id)
        local c = CHARS[id.Value]
        if not c then return nil end
        return { GetLocation = function() return { X = c.x, Y = c.y, Z = c.z } end,
                 GetLocatedSiteId = function() return { Value = 1038 } end }
    end,
}
function em() return MANAGER end
function char(n) return MANAGER:FindCharacter({ Value = n }) end
TRIES = 0
INZOI = { FB2SlotComponent = {
    GetSlotStateInfos = function(sc) return arr(sc.slots) end,
    TryOccupySlot = function(sc, kind, p)
        TRIES = TRIES + 1
        for _, s in ipairs(sc.slots) do
            if s.SlotName == p.SlotName then
                if s.OccupyingEntityId.Value ~= -1 then return false end
                s.OccupyingEntityId = { Value = p.OccupyEntityId.Value }
                s.reason = p.OccupyingReason
                return true
            end
        end
        return false
    end,
    RemoveOccupyingReasonBySlotName = function(sc, name, reason)
        for _, s in ipairs(sc.slots) do
            if s.SlotName == name and s.reason == reason then s.OccupyingEntityId = { Value = -1 }; s.reason = nil end
        end
    end,
}, FB2TargetSlotParam = function() return {} end, FB1EntityId = function() return {} end }
inzoi = { EB1SlotType = { Interaction = 1 } }
S = { clock = 0, me = 24534 }
PUPPETS = {}
REPORTS = {}
function report(kind, text) REPORTS[#REPORTS + 1] = kind .. " " .. (text or "") end
function occ(id, name)
    for _, s in ipairs(OBJ[id].slots) do
        if s.SlotName == name and s.OccupyingEntityId.Value ~= -1 then return s.OccupyingEntityId.Value end
    end
    return -1
end
"""


def b2_seats():
    if LuaRuntime is None:
        print("seats tests (B2): lupa isn't installed (pip install lupa): skipped")
        return
    src = B2.read_text("utf-8")
    eid = re.search(r"^local function eid\(n\)\n.*?^end\n", src, re.M | re.S)
    block = re.search(r"^-- -+ seats\n(.*?)^-- -+ puppets\n", src, re.M | re.S)
    unseat = re.search(r"^    unseat = function\(op\)\n(.*?)^    end,\n", src, re.M | re.S)
    assert eid and block and unseat, "the seats section / unseat op not found in B2/Main.lua"
    lua = LuaRuntime()
    lua.execute(FAKE)
    g = lua.globals()
    lua.execute(eid.group(0) + block.group(1) + "\nUNSEAT = function(op)\n" + unseat.group(1) + "end\n"
                "MY_SEATS, SET, FREE, KEEP, EID = my_seats, seats_set, seats_free, seats_keep, eid")

    def reports():
        out = list(g.REPORTS.values())
        lua.execute("REPORTS = {}")
        return out

    # My Zoi at the computer: its game gave it the desk chair - two of the chair's entries carry the name, one occupied
    lua.execute("OBJ[26032].slots[1].OccupyingEntityId = { Value = 24534 }; "
                "OBJ[26034].slots[1].OccupyingEntityId = { Value = 24534 }; OBJ[99999].slots[1].OccupyingEntityId = { Value = 24534 }")
    assert g.MY_SEATS() == "Chair04_v03 73172 25748 76 Chair04|Computer01_v01 73098 25598 150 InteractionSlot", g.MY_SEATS()
    lua.execute("OBJ[26032].slots[2].OccupyingEntityId = { Value = 24534 }")
    assert g.MY_SEATS().count("Chair04_v03") == 1, "one spot sent twice (two entries of one slot)"
    lua.execute("for _, o in pairs(OBJ) do for _, s in ipairs(o.slots) do s.OccupyingEntityId = { Value = -1 } end end")
    assert g.MY_SEATS() == ""
    print("B2: the spots my Zoi occupies are read from the game's slots (near it, each once, sorted): ok")

    # Their Zoi sits at the computer in their game: the same chair (not the other one of the set) and the computer
    lua.execute("""P = { id = 25674 }; PUPPETS.p1 = P
        WANT = { { t = "Chair04_v03", x = 73172.4, y = 25747.6, z = 76, s = "Chair04" },
                 { t = "Computer01_v01", x = 73098, y = 25598, z = 150, s = "InteractionSlot" },
                 { t = "Chair04_v03", x = 80000, y = 80000, z = 76, s = "Chair04" } }""")
    text = g.SET("p1", g.P, g.WANT, g.EID(1038))
    assert g.occ(26032, "Chair04") == 25674 and g.occ(26034, "InteractionSlot") == 25674, text
    assert g.occ(26205, "Chair04") == -1, "another chair of the same kind was taken"
    assert "holds Chair04_v03 Chair04" in text and "holds Computer01_v01 InteractionSlot" in text, text
    assert reports() == ["seat held 26032 Chair04 25674|26034 InteractionSlot 25674"]
    tries = g.TRIES
    assert g.SET("p1", g.P, g.WANT, g.EID(1038)) is None and g.TRIES == tries, "the same spots taken again"
    assert reports() == []
    print("B2: their spots are their Zoi's here - the same objects, where they are in their game; once: ok")

    # The game drops one (their Zoi's actions cleared here): taken again a little later, not every tick
    lua.execute("OBJ[26032].slots[1].OccupyingEntityId = { Value = -1 }")
    g.KEEP()
    assert g.occ(26032, "Chair04") == -1, "taken again at once"
    lua.execute("S.clock = S.clock + 2.5")
    g.KEEP()
    assert g.occ(26032, "Chair04") == 25674, "a dropped spot wasn't taken again"
    print("B2: a spot the game took from their Zoi is taken again (every 2 s): ok")

    # They stand up from the chair, keep the computer: the chair is given back
    lua.execute("WANT2 = { WANT[2] }")
    text = g.SET("p1", g.P, g.WANT2, g.EID(1038))
    assert g.occ(26032, "Chair04") == -1 and g.occ(26034, "InteractionSlot") == 25674, text
    assert "gave back Chair04_v03 Chair04" in text, text
    assert reports() == ["seat held 26034 InteractionSlot 25674"]

    # My Zoi lay down on the left side as theirs did in their game, at the same moment: told once, never taken
    lua.execute("OBJ[15725].slots[1].OccupyingEntityId = { Value = 24534 }; "
                "WANT3 = { { t = 'Bed01', x = 73657, y = 25786, z = 76, s = 'InteractionSlot_01_l' } }")
    text = g.SET("p1", g.P, g.WANT3, g.EID(1038))
    assert g.occ(15725, "InteractionSlot_01_l") == 24534, "taken from my Zoi"
    assert g.occ(26034, "InteractionSlot") == -1, "the computer wasn't given back"
    rs = reports()
    assert "seat clash p1 15725 InteractionSlot_01_l 24534" in rs and rs.count(rs[0]) == 1, rs
    lua.execute("S.clock = S.clock + 2.5")
    g.KEEP()
    assert not any("clash" in r for r in reports()), "a clash told again"
    # mine got up: their Zoi gets it
    lua.execute("OBJ[15725].slots[1].OccupyingEntityId = { Value = -1 }; S.clock = S.clock + 2.5")
    g.KEEP()
    assert g.occ(15725, "InteractionSlot_01_l") == 25674
    assert reports() == ["seat held 15725 InteractionSlot_01_l 25674"]
    print("B2: a spot my Zoi has is never taken from it (a clash, told once); free again, theirs gets it: ok")

    # They leave: everything given back
    g.FREE(g.P)
    assert g.occ(15725, "InteractionSlot_01_l") == -1 and g.P.seats is None
    assert reports() == ["seat held "]
    # an earlier session's spots (the save kept them): given back - only if that Zoi still has them
    lua.execute("OBJ[26205].slots[1].OccupyingEntityId = { Value = 25674 }; OBJ[26205].slots[1].reason = 1; "
                "OBJ[15725].slots[2].OccupyingEntityId = { Value = 24534 }; OBJ[15725].slots[2].reason = 1")
    text = g.UNSEAT(lua.eval("{ holds = { '26205 Chair04 25674', '15725 InteractionSlot_01_r 25674', 'junk' } }"))
    assert g.occ(26205, "Chair04") == -1 and g.occ(15725, "InteractionSlot_01_r") == 24534, text
    assert "1 spot" in text, text
    print("B2: their spots given back when they go; a past session's too (not one my Zoi has now): ok")


def companion_side():
    comp = companion.Companion({"key": "k" * 20, "name": "me", "server": "x", "port": 1, "password": ""})
    comp.mod["state"] = "running"
    comp.me_id, comp.my_entity, comp.leader = "me", 24534, "me"
    comp.state_base = {"act": None}
    r = companion.Remote("p1", "Chey")
    comp.remotes["p1"] = r
    r.spawned, r.hidden = True, False
    comp.puppet_ids["p1"] = 25674
    comp.pose_from["p1"] = time.time()           # (their skeleton is coming)

    # my spots: parsed from the controller's report, out with my state
    comp.on_report("seats", "Chair04_v03 73172 25748 76 Chair04|Computer01_v01 73098 25598 150 Interaction Slot|bad")
    assert comp.my_seats == [{"t": "Chair04_v03", "x": 73172.0, "y": 25748.0, "z": 76.0, "s": "Chair04"},
                             {"t": "Computer01_v01", "x": 73098.0, "y": 25598.0, "z": 150.0, "s": "Interaction Slot"}]
    assert comp.state_base["seats"] == comp.my_seats and comp.extra_state({})["seats"] == comp.my_seats
    comp.on_report("seats", "")
    assert comp.my_seats == [] and comp.state_base["seats"] == []
    print("companion: the spots my Zoi occupies go out with my state: ok")

    def seat_ops():
        out = [o for _, _, o in comp.mail.ops if o["op"] == "seat"]
        comp.mail.ops.clear()
        return out

    seats = [{"t": "Chair04_v03", "x": 73172.0, "y": 25748.0, "z": 76.0, "s": "Chair04"}]
    r.state, r.state_at = {"x": 73173, "y": 25596, "z": 166, "site": 1038, "seats": seats}, time.time()
    comp.hold_seats()
    ops = seat_ops()
    assert len(ops) == 1 and ops[0]["key"] == "p1" and ops[0]["seats"] == seats and ops[0]["site"] == 1038, ops
    comp.hold_seats()
    assert seat_ops() == [], "sent again unchanged"
    r.hidden = True
    comp.hold_seats()
    assert [o["seats"] for o in seat_ops()] == [[]], "a hidden Zoi kept its spots"
    r.hidden = False
    comp.hold_seats()
    assert [o["seats"] for o in seat_ops()] == [seats]
    comp.pose_from["p1"] = 0                     # (their skeleton stopped: their Zoi here acts by itself)
    comp.hold_seats()
    assert [o["seats"] for o in seat_ops()] == [[]]
    comp.pose_from["p1"] = time.time()
    r.state_at = time.time() - 10                # (they went quiet)
    comp.hold_seats()
    assert seat_ops() == []
    r.state_at = time.time()
    comp.puppet_ids["p1"] = 25999                # (their Zoi here again, another one: held again)
    comp.hold_seats()
    assert [o["seats"] for o in seat_ops()] == [seats]
    r2 = companion.Remote("p2", "Old")           # (an older program: no "seats" - once [] to clear, then nothing)
    r2.spawned, r2.state, r2.state_at = True, {"x": 1, "y": 1, "z": 1}, time.time()
    comp.remotes["p2"], comp.puppet_ids["p2"] = r2, 30000
    comp.hold_seats()
    comp.hold_seats()
    assert [(o["key"], o["seats"]) for o in seat_ops()] == [("p2", [])]
    print("companion: their spots go to the controller on a change only; none hidden, quiet or without skeleton: ok")

    # held spots kept; a fresh controller gives back the last one's, and holds theirs again
    comp.on_report("seat", "held 26032 Chair04 25674|26034 InteractionSlot 25674")
    assert comp.cfg["held_seats"] == ["26032 Chair04 25674", "26034 InteractionSlot 25674"]
    import json
    assert json.loads(companion.CONFIG_FILE.read_text("utf-8"))["held_seats"] == comp.cfg["held_seats"], "not saved"
    comp.on_report("restart", "123456")
    unseat = [o for _, _, o in comp.mail.ops if o["op"] == "unseat"]
    assert len(unseat) == 1 and unseat[0]["holds"] == ["26032 Chair04 25674", "26034 InteractionSlot 25674"], unseat
    assert comp.cfg["held_seats"] == [] and r.seat_sig is None
    comp.mail.ops.clear()
    comp.hold_seats()
    assert [o["key"] for o in seat_ops()] == ["p1", "p2"]
    print("companion: held spots kept; a fresh controller gives back the last one's and holds theirs again: ok")

    # a clash: mine got there later - it moves (gets up: their spot); first - it stays; someone else's - not mine
    def reslots():
        out = [o for _, _, o in comp.mail.ops if o["op"] == "reslot"]
        comp.mail.ops.clear()
        return out

    now = time.time()
    comp.cur_act, comp.cur_target, comp.cur_act_since = {"bag": "Computer_Use"}, 26034, now - 1
    r.state.update({"ts": now, "act_t": now - 30, "x": 73100, "y": 25600})
    r.state_at = now
    comp.on_report("seat", "clash p1 26034 InteractionSlot 24534")
    ops = reslots()
    assert len(ops) == 1 and ops[0]["entity"] == 24534 and ops[0]["target"] == 26034 and ops[0]["stand"] is True \
        and ops[0]["swap_only"] is True and ops[0]["mine"] is True, ops
    comp._reslot_at = 0
    comp.on_report("seat", "clash p1 26032 Chair04 24534")
    assert reslots()[0]["mine"] is False, "the chair at the computer isn't what my Zoi does"
    comp._reslot_at = 0
    comp.on_report("seat", "clash p1 26034 InteractionSlot 77777")
    assert reslots() == [], "moved my Zoi off a spot someone else has"
    comp.cur_act_since = now - 60
    comp.on_report("seat", "clash p1 26034 InteractionSlot 24534")
    assert reslots() == [], "mine got there first and moved anyway"
    comp.cur_act_since, r.state["act_t"] = now - 5, now - 5     # (a tie: the host - me - stays)
    comp.on_report("seat", "clash p1 26034 InteractionSlot 24534")
    assert reslots() == []
    comp.leader = "p1"
    comp.on_report("seat", "clash p1 26034 InteractionSlot 24534")
    assert len(reslots()) == 1
    print("companion: a clash moves my Zoi only when it got there later (the host stays on a tie): ok")

    # check_spot leaves partners that send their spots to the game's own occupancy
    comp.fast_pos = {"x": 73100, "y": 25600}
    comp.cur_act = {"bag": "Computer_Use", "obj": "Computer01_v01", "ox": 73098, "oy": 25598}
    comp.cur_act_since, comp._reslot_at = now - 1, 0
    r.state.update({"act": {"bag": "Computer_Use", "obj": "Computer01_v01", "ox": 73098, "oy": 25598}})
    comp.check_spot()
    assert reslots() == [], "check_spot moved my Zoi off a spot the game holds"
    print("companion: check_spot leaves spots the game holds to the game: ok")


def main():
    b2_seats()
    companion_side()
    print("seats tests passed")


main()
