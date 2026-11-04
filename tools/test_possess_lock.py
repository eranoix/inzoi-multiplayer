"""An online partner's Zoi can't be played here - the game's own possession rule and the family portraits, run for
real (lupa) against a fake game:
- B2 (lua/B2/Systems/FB2ControllerSystem.lua, the override of FB2ControllerSystem::TryChangePossessEntityId): a switch
  to a Zoi in the mailbox's MPLock.lua is refused (false, reported), any other goes to the game's own function; an old
  MPLock (the companion closed), an empty or missing one, lock nothing
- B1 (lua/B1/Main.lua): the portrait of a locked Zoi in the HUD's family list can't be clicked and is dimmed, again
  if the game enables it when it redraws; a portrait is known by its picture - the very texture its Zoi's portrait
  component holds (a restored one has a plain name), or the name "Portrait<entity>_" when the actor isn't here; it
  follows its Zoi when the portraits move or the picture is made again; back as the game had it when they go offline
  (or the lock file gets old); the widgets are found by their class after the first search, again after a save loads

    python tools/test_possess_lock.py
"""
import sys
import time
from pathlib import Path

try:
    from lupa import LuaRuntime
except ImportError:
    print("possess lock tests: lupa isn't installed (pip install lupa): skipped")
    sys.exit(0)

MOD = Path(__file__).resolve().parents[1] / "mod" / "InZOIMultiplayer" / "lua"
RULE = MOD / "B2" / "Systems" / "FB2ControllerSystem.lua"
B1 = MOD / "B1" / "Main.lua"


def b2_rule():
    lua = LuaRuntime()
    lua.execute("""
        LOCKBOX = nil
        package.preload["MPLock"] = function() if LOCKBOX == nil then error("no file") end return LOCKBOX end
        REPORTS = {}
        MP_REPORT = function(kind, text) REPORTS[#REPORTS + 1] = kind .. " " .. text end
        CALLS = {}
        OWNER = { TryChangePossessEntityId = function(self, id, close)
            CALLS[#CALLS + 1] = id.Value .. (close and "+map" or "")
            return true
        end }
        function id(n) return { Value = n } end
    """)
    m = lua.execute(RULE.read_text("utf-8"))
    g = lua.globals()
    now = int(time.time())

    def switch(n, close=False):
        return m.Override_TryChangePossessEntityId(g.OWNER, g.id(n), close)

    lua.execute(f"LOCKBOX = {{ t = {now}, ids = {{ 25674 }} }}")
    assert switch(25674) is False, "switched to the online partner's Zoi"
    assert len(g.CALLS) == 0, "the game's switch ran anyway"
    assert list(g.REPORTS.values()) == ["refused 25674"], list(g.REPORTS.values())
    assert switch(24534, True) is True and list(g.CALLS.values()) == ["24534+map"], list(g.CALLS.values())
    print("B2: the switch to the online partner's Zoi is refused (and reported); another Zoi goes to the game: ok")

    for box, why in ((f"{{ t = {now - 100}, ids = {{ 25674 }} }}", "an old lock (the companion closed)"),
                     (f"{{ t = {now}, ids = {{ }} }}", "nobody online"),
                     ("nil", "no lock file"),
                     ('{ t = "x", ids = 25674 }', "a broken lock file")):
        lua.execute(f"LOCKBOX = {box}; CALLS = {{}}")
        assert switch(25674) is True and list(g.CALLS.values()) == ["25674"], why
    lua.execute(f"LOCKBOX = {{ t = {now}, ids = {{ 25674 }} }}; CALLS = {{}}")
    assert switch(25674) is False and len(g.CALLS) == 0, "the lock file isn't read again on each switch"
    print("B2: an old, empty, missing or broken lock file locks nothing; it's read afresh on each switch: ok")


FAKE_B1 = r"""
CFG = {}
local function q(s) return (s:gsub('\\', '\\\\'):gsub('"', '\\"')) end
inzoi = { cli = { execute = function(cmd)
    local sec, key = cmd:match("uimod%.cfg_load mod_id=%S+ section=(%S+) key=(%S+)")
    if sec then
        local v = CFG[sec .. "." .. key]
        if v == nil then return false end
        return true, nil, '{"value":"' .. q(v) .. '"}'
    end
    local sec2, key2, val = cmd:match('uimod%.cfg_save mod_id=%S+ section=(%S+) key=(%S+) value="(.*)"$')
    if sec2 then CFG[sec2 .. "." .. key2] = val end
    return true
end } }
local function arr(items)
    local t = { items = items or {} }
    function t:Length() return #self.items end
    function t:Get(i) return self.items[i] end
    function t:Add(x) self.items[#self.items + 1] = x end
    return t
end
REALTIME, FULL_SEARCHES = 0, 0
WIDGETS = {}
UE = {
    UUserWidget = "UUserWidget", AActor = "AActor", UActorComponent = "UActorComponent",
    TArray = function() return arr() end,
    UGameplayStatics = { GetAllActorsOfClass = function(_, _, out) for _, a in ipairs(ACTORS) do out:Add(a) end end,
                         GetRealTimeSeconds = function() return REALTIME end },
    UWidgetBlueprintLibrary = { GetAllWidgetsOfClass = function(_, out, cls, _)
        if cls == UE.UUserWidget then FULL_SEARCHES = FULL_SEARCHES + 1 end
        for _, w in ipairs(WIDGETS) do
            if cls == UE.UUserWidget or w:GetClass() == cls then out:Add(w) end
        end
    end },
}
local function class(name) return { GetName = function() return name end } end
local function button()
    local b = { enabled = true }
    function b:GetIsEnabled() return self.enabled end
    function b:SetIsEnabled(v) self.enabled = v end
    return b
end
function tex(name) return { GetName = function() return name end } end      -- (a texture: one object, compared by identity)
function Entry(t)
    local e = { opacity = 1, BW_Button_Portrait = button(), BW_WBP_Portrait = { BW_Button_Image = button(),
        BW_Image_Portrait = { Brush = { ResourceObject = { TextureParameterValues = arr({ { ParameterValue = t } }) } } } } }
    function e:SetRenderOpacity(o) self.opacity = o end
    return e
end
function show(e, t) e.BW_WBP_Portrait.BW_Image_Portrait.Brush.ResourceObject.TextureParameterValues = arr({ { ParameterValue = t } }) end
-- The family's Zois: each has a portrait component holding its picture (a restored one has a plain texture name).
local charCls = class("BP_B1Character_C")
function Actor(entity, pic)
    local portrait = { cls = class("B1PortraitComponent"), Portrait = pic }
    local mesh = { cls = class("SkeletalMeshComponent") }
    for _, c in ipairs({ portrait, mesh }) do function c:GetClass() return self.cls end end
    local a = { EntityId = { Value = entity }, portrait = portrait, comps = { mesh, portrait } }
    function a:GetClass() return charCls end
    function a:GetName() return "BP_B1Character_C_" .. entity end
    function a:K2_GetComponentsByClass(cls) return arr(self.comps) end
    return a
end
PIC = { [24534] = tex("Portrait24534_Tex2D_1"), [25674] = tex("Texture2D_2146891674") }
ACTORS = { Actor(24534, PIC[24534]), Actor(25674, PIC[25674]) }
function locked(e) return not e.BW_Button_Portrait.enabled and not e.BW_WBP_Portrait.BW_Button_Image.enabled and e.opacity < 1 end
function normal(e) return e.BW_Button_Portrait.enabled and e.BW_WBP_Portrait.BW_Button_Image.enabled and e.opacity == 1 end
local hudCls, otherCls = class("WBP_HUD_PlayerChange_C"), class("WBP_Something_C")
LIST = { cls = hudCls }
function LIST:GetClass() return self.cls end
local other = { cls = otherCls }
function other:GetClass() return self.cls end
E = {}
for k = 0, 7 do
    E[k] = Entry(k == 0 and PIC[24534] or (k == 1 and PIC[25674] or nil))
    LIST["BW_WBP_PortraitEntry_" .. k] = E[k]
end
WIDGETS = { other, LIST }
MP_B1_OWNER = ACTORS[1]
"""


def b1_portraits():
    lua = LuaRuntime()
    lua.execute(FAKE_B1)
    g = lua.globals()
    main = lua.execute(B1.read_text("utf-8"))

    def run(seconds, dt=0.05):
        for _ in range(int(seconds / dt)):
            g.REALTIME = g.REALTIME + dt
            main.on_tick(None)

    def lock(ids, age=0):
        g.CFG["in.locked"] = f"{int(time.time()) - age}|{','.join(map(str, ids))}"

    E = g.E
    lock([25674])
    run(1.0)
    assert g.locked(E[1]), "the online partner's portrait (a plain texture name: its Zoi's own picture) can be clicked"
    assert all(g.normal(E[k]) for k in (0, 2, 3, 4, 5, 6, 7)), "another portrait was locked"
    E[1].BW_Button_Portrait.enabled = True                     # (the game redraws the list)
    E[1].opacity = 1
    run(0.6)
    assert g.locked(E[1]), "the portrait stayed clickable after the game redrew it"
    print("B1: the online partner's portrait can't be clicked and is dimmed, again after a redraw: ok")

    g.show(E[1], g.tex("Portrait30000_Tex2D_1"))                 # (the portraits moved: their Zoi is now the third)
    g.show(E[2], g.PIC[25674])
    run(0.6)
    assert g.normal(E[1]) and g.locked(E[2]), "the lock didn't follow the Zoi's portrait"
    new = g.tex("Texture2D_999")                                   # (the game made their picture again)
    g.ACTORS[2].portrait.Portrait = new
    g.show(E[2], new)
    run(0.6)
    assert g.locked(E[2]), "a new picture of the same Zoi wasn't recognised"
    lua.execute("SAVED = ACTORS; ACTORS = { ACTORS[1] }")          # (their Zoi's actor isn't here: by the name then)
    g.show(E[2], g.tex("Portrait25674_Tex2D_5"))
    run(1.2)
    assert g.locked(E[2]), "without its actor, the picture's name didn't lock it"
    g.show(E[2], g.tex("Texture2D_4"))
    run(0.6)
    assert g.normal(E[2]), "a picture nobody's known to have was locked"
    lua.execute("ACTORS = SAVED")
    g.show(E[2], new)
    lock([])                                                       # (they went offline)
    run(0.6)
    assert all(g.normal(E[k]) for k in range(8)), "a portrait stayed locked after they went offline"
    lock([25674])
    run(0.6)
    assert g.locked(E[2])
    lock([25674], age=100)                                         # (the companion closed: the file gets old)
    run(0.6)
    assert g.normal(E[2]), "an old lock file kept the portrait locked"
    assert g.FULL_SEARCHES == 1, f"searched all widgets {g.FULL_SEARCHES} times (once is enough: then by class)"
    assert "lock-error" not in str(g.CFG["out.b1"] or ""), g.CFG["out.b1"]
    print("B1: the lock follows its Zoi, goes when they're offline or the file is old; one full widget search: ok")

    # A save loaded: a loading screen without the HUD, then the HUD again - a new list, of a new class (the cached one
    # finds nothing). Searched for less and less often while it's missing; locked again once it's back.
    lock([25674])
    g.WIDGETS = lua.table()
    before = g.FULL_SEARCHES
    run(40.0)
    missing = g.FULL_SEARCHES - before
    assert 1 <= missing <= 4, f"{missing} full searches in 40 s without the HUD (each one is ~20 ms)"
    lua.execute("""
        local cls2 = { GetName = function() return "WBP_HUD_PlayerChange_C" end }
        LIST2 = { cls = cls2 }
        function LIST2:GetClass() return self.cls end
        E2 = {}
        for k = 0, 7 do
            E2[k] = Entry(k == 1 and ACTORS[2].portrait.Portrait or nil)
            LIST2["BW_WBP_PortraitEntry_" .. k] = E2[k]
        end
        WIDGETS = { LIST2 }
    """)
    run(31.0)
    assert g.locked(g.E2[1]), "the new HUD's portrait wasn't locked after the save loaded"
    found = g.FULL_SEARCHES
    run(5.0)
    assert g.FULL_SEARCHES == found, "kept searching all widgets after finding the new HUD"
    print(f"B1: a save loaded - the new HUD found again and its portrait locked ({missing} searches meanwhile): ok")


def main():
    b2_rule()
    b1_portraits()
    print("possess lock tests passed")


main()
