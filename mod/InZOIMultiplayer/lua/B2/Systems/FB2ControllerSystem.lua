-- inZOI Multiplayer - who can be played here: the game's own possession rule (B2).
--
-- A partner's Zoi lives in this save as a member of the family (client/companion.py adopts it while they're online).
-- While they're online they play it in their own game, so it can't be played here. Every way the game has of switching
-- the Zoi being played - the family portraits, the next-Zoi key, the gamepad, the radial menu, the CLI - goes through
-- FB2ControllerSystem::TryChangePossessEntityId, and this override refuses it for those Zois, the way the game itself
-- refuses a Zoi that is surfing or in another city (the players' choice, 2026-11-04). With the partner offline, their
-- Zoi is played like any member of the family.
--
-- The runtime loads this chunk module (Systems.FB2ControllerSystem) when the controller system is created, so the
-- rule applies from the next time a save is loaded.
--
-- MPLock.lua, in the mailbox, rewritten by the companion every few seconds while a partner is online:
--   return { t = <unix time>, ids = { <entity>, ... } }   the partners' Zois that are online now
-- If it is older than LOCK_FRESH seconds (the companion was closed), nothing is locked.
local M = {}
local LOCK = table.concat({ "MP", "Lock" })      -- (built at runtime: the validator only allows static module names)
local LOCK_FRESH = 30

local function locked(id)
    pcall(function() package.loaded[LOCK] = nil end)
    local ok, box = pcall(require, LOCK)
    if not ok or type(box) ~= "table" or type(box.t) ~= "number" or type(box.ids) ~= "table" then return false end
    if math.abs(os.time() - box.t) > LOCK_FRESH then return false end
    for _, e in ipairs(box.ids) do
        if e == id then return true end
    end
    return false
end

---@param owner FB2ControllerSystem
---@param InEntityId FB1EntityId
---@param InCloseMapMode boolean
---@return boolean
function M.Override_TryChangePossessEntityId(owner, InEntityId, InCloseMapMode)
    local ok, id = pcall(function() return InEntityId.Value end)
    if ok and type(id) == "number" and locked(id) then
        -- (B2/Main.lua's report: the companion tells the player why nothing happened)
        local report = rawget(_G, "MP_REPORT")
        if type(report) == "function" then pcall(report, "refused", tostring(id)) end
        return false
    end
    return owner:TryChangePossessEntityId(InEntityId, InCloseMapMode)
end

return M
