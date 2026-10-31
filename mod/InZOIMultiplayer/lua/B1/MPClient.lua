-- inZOI Multiplayer - B1 (client) side. Bound to the player's family Zois only to receive the B2 puppet controller's
-- reports (inzoi.message.send_to_client) and hand them to the companion app: each report is saved with the game's
-- uimod.cfg_save into <mod>/out/m<slot>.cfg, which the companion reads.
--   content = "<instance> <n> <kind> <text>"   (n counts up per controller instance; 32 slots are reused)
local M = {}
local MOD = "InZOIMultiplayer"

---@param self table
---@param owner AB1Character
---@param msgType string
---@param content string
-- B1/Main.lua (ticks in build mode too, unlike these per-Zoi scripts) needs an actor of this world as the context for
-- widget lookups: the latest family Zoi seen here.
---@param self table
---@param owner AB1Character
function M.on_begin(self, owner)
    _G.MP_B1_OWNER = owner
end

---@param self table
---@param owner AB1Character
function M.on_tick(self, owner, dt)
    _G.MP_B1_OWNER = owner
end

function M.on_lua_message(self, owner, msgType, content)
    if tostring(msgType) == "mp_eval" then              -- debugging: run a chunk here (B1), result in out/eval.cfg
        local f, err = load(tostring(content), "eval_b1", "t", setmetatable({ owner = owner }, { __index = _G }))
        local ok, res = false, err
        if f then ok, res = pcall(f) end
        inzoi.cli.execute(string.format('uimod.cfg_save mod_id=%s section=out key=eval value="%s"', MOD,
            ((ok and "" or "ERR ") .. tostring(res)):gsub('"', "'")))
        return
    end
    if tostring(msgType) ~= "mp" then return end
    content = tostring(content)
    local n = tonumber(content:match("^%S+ (%d+) ")) or 0
    inzoi.cli.execute(string.format('uimod.cfg_save mod_id=%s section=out key=m%d value="%s"', MOD, n % 32,
        (content:gsub('"', "'"))))
end

return M
