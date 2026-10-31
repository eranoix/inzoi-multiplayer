-- inZOI Multiplayer - B1 (client) boot script. Ticks while the simulation is paused (build mode), so it runs the
-- companion's client-side commands: <mod>/in/b1cmd.cfg = "<10-digit hex id><code as hex>" (read with uimod.cfg_load: the game's
-- mod check refuses requiring modules it doesn't ship, and cfg_load turns quotes into apostrophes, hence the hex);
-- each new id runs once (with `owner`, a family Zoi actor of this world, for widget lookups) and its result goes to
-- out/b1.cfg.
local M = {}
local MOD = "InZOIMultiplayer"
local LAST, EVERY, t = nil, 0.25, 0

local function result(text)
    inzoi.cli.execute(string.format('uimod.cfg_save mod_id=%s section=out key=b1 value="%s"', MOD,
        (tostring(text):gsub('"', "'"))))
end

local function read_box()
    local called, ok, _, data = pcall(inzoi.cli.execute, "uimod.cfg_load mod_id=" .. MOD .. " section=in key=b1cmd")
    local v = called and ok and tostring(data):match("value%W+(%x+)")
    if not v or #v < 10 then return nil end
    local id, hex = v:sub(1, 10), v:sub(11)
    return id, (hex:gsub("%x%x", function(h) return string.char(tonumber(h, 16)) end))
end

function M.on_begin() end

function M.on_tick(dt)
    t = t + (tonumber(dt) or 0.03)
    if t < EVERY then return end
    t = 0
    local id, code = read_box()
    if not id or id == LAST then return end
    LAST = id
    local f, err = load(code, "b1cmd", "t", setmetatable({ owner = _G.MP_B1_OWNER }, { __index = _G }))
    local okR, res = false, err
    if f then okR, res = pcall(f) end
    result(id .. " " .. ((okR and "" or "ERR ") .. tostring(res)))
end

function M.on_end() end

return M
