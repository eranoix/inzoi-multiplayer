-- inZOI Multiplayer - free will, Zoi by Zoi: the game's own autonomy rule (B2).
--
-- A partner's Zoi here (a copy, or their member of my family) is played by them, in their game: here it has no will of
-- its own - no free will, no schedule (work, school), needs that don't decay. Every Zoi's free will is decided by
-- FB2AutonomyOptionManager::ResolveToggleOption; this override answers for the partners' Zois only (B2/Main.lua's
-- MP_FROZEN: entity -> true) and leaves every other Zoi - the player's own first - to the game. (Until 0.9.85 the
-- game's "follow the global autonomy" switch was turned off instead, which took the free will of every Zoi whose own
-- toggle was off: Chey's Zoi stopped looking after itself and died, twice.)
--
-- The runtime loads this chunk module (Systems.B2AutonomyOptionManager) when the manager is created: the rule is in
-- place from the next time a save is loaded.
local M = {}

---@param owner FB2AutonomyOptionManager
---@param InCharacter FB2Character
---@param InType EB1AutonomyOptionType
---@return boolean
function M.Override_ResolveToggleOption(owner, InCharacter, InType)
    local frozen = rawget(_G, "MP_FROZEN")
    if type(frozen) == "table" and next(frozen) ~= nil then
        local ok, id = pcall(function() return InCharacter:GetEntityId().Value end)
        if ok and frozen[id] then
            local T = inzoi.EB1AutonomyOptionType
            if InType == T.AutonomyEnable or InType == T.FollowSchedule then return false end
            if InType == T.FreezeStat then return true end
        end
    end
    return owner:ResolveToggleOption(InCharacter, InType)
end

return M
