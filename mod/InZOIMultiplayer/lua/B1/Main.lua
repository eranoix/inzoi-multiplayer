-- inZOI Multiplayer - B1 (client) boot script. Ticks while the simulation is paused (build mode), so it runs the
-- companion's client-side commands: lua/B1/MPB1Cmd.lua = return { id = "...", code = "..." }; each new id runs once
-- (with `owner`, a family Zoi actor of this world, for widget lookups) and its result goes to out/b1.cfg.
local M = {}
local MOD = "InZOIMultiplayer"
local LAST, EVERY, t = nil, 0.25, 0

local function result(text)
    inzoi.cli.execute(string.format('uimod.cfg_save mod_id=%s section=out key=b1 value="%s"', MOD,
        (tostring(text):gsub('"', "'"))))
end

function M.on_begin() end

function M.on_tick(dt)
    t = t + (tonumber(dt) or 0.03)
    if t < EVERY then return end
    t = 0
    package.loaded["MPB1Cmd"] = nil
    local ok, box = pcall(require, "MPB1Cmd")
    if not ok or type(box) ~= "table" or not box.id or box.id == LAST then return end
    LAST = box.id
    local f, err = load(box.code or "", "b1cmd", "t", setmetatable({ owner = _G.MP_B1_OWNER }, { __index = _G }))
    local okR, res = false, err
    if f then okR, res = pcall(f) end
    result(box.id .. " " .. ((okR and "" or "ERR ") .. tostring(res)))
end

function M.on_end() end

return M
