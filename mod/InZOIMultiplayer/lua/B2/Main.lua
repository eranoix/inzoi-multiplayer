-- inZOI Multiplayer - B2 (simulation) puppet controller.
--
-- Other players' Zois live in this world as "puppets": real characters in their own household, autonomy off,
-- walking where the remote player walks. The companion app (client/companion.py) drives this script through two
-- mailbox modules it rewrites on disk; this script re-reads them a few times a second:
--
--   MPCmd   return { seq = <int>, bulk = <int>, ops = { {id = "<unique>", op = "...", ...}, ... } }
--   MPBulk  return { seq = <int>, members = { [<player key>] = <FB1CustomizedCharacterInfo table>, ... } }
--   MPSeq   return { seq = <int> }   tiny, rewritten right after MPCmd: while it's unchanged MPCmd isn't re-read
--           (optional: without it MPCmd is read on every poll, as before)
--
-- Ops (each runs once per id; the companion may repeat an op for a while so it can't be missed):
--   spawn   {key, template, x, y, z, home_door?, home_site?}   create the puppet for player `key` from MPBulk
--   trail   {key, pts = {{n, x, y, z, yaw}, ...}}              newest positions of that player, n = sample number
--   hide    {key, on}                                          remote player is elsewhere (other city, loading...)
--   admit   {key, door, site}                                  let the puppet through a lot's doors like a guest
--   adopt   {key, entity, x?, y?, z?}                          the player's Zoi already lives here (a member of the
--           family in this save): drive that one instead of a copy; despawn gives it back to the game
--   despawn {key}
--   social  {rid, bag, entity?, key?}   couple actions: the player's own Zoi (`entity`, else the possessed one) runs
--           interaction `bag` on the puppet of `key` (or on its own without key); "anim:<id>" plays a motion instead.
--           Reports "social <rid> ok|fail <text>".
--   move_home {household, site}         the family moves to that lot (moving in together where nobody lives)
--   goto_point {entity?, x, y, z}       teleport the player's own Zoi to a spot ("go to my partner" without a puppet)
--   debug ops: walk {entity, x, y, z}, status {entity}, reset {entity}, probe {entity, r}
--
-- Everything learned the hard way (see project notes): walks use ClickMove_NonCancelable with no target, redirects
-- need a Clear-cancel and the new order on a later tick, unreachable/occupied destinations are dropped silently,
-- puppets need the knock-door guest privilege to enter homes, and new characters must be switched from
-- TimelineSimulateOnly to Simulate|GameSpawned by hand.

local M = {}
local P = "[mp] "
local CMD = table.concat({ "MP", "Cmd" })      -- built at runtime: the validator only allows static module names
local BULK = table.concat({ "MP", "Bulk" })
local SEQ = table.concat({ "MP", "Seq" })

local S = { clock = 0, poll = 0, seq = nil, bulkSeq = nil, members = {}, done = {}, doneOrder = {},
            seqFile = nil, seqMiss = 0, cmdT = -10, box = nil, retry = {} }
local find_me
local PUPPETS = {}      -- key -> {id, trail = {...}, dest, lastOrderT, pending, hidden}

local INSTANCE = string.format("%06d", math.random(0, 999999))     -- tells the companion when it's a fresh controller
local REPORTS = 0

-- Reports reach the companion through the B1 script bound to the player's Zoi (see B1/MPClient.lua).
local function report(kind, text)
    if not S.me then return end
    REPORTS = REPORTS + 1
    local msg = string.format("%s %d %s %s", INSTANCE, REPORTS, kind, text or "")
    pcall(function() inzoi.message.send_to_client(S.me, "mp", msg) end)
end

local function log(s)
    print(P .. string.format("%.2f ", S.clock) .. s)
    report("log", s)
end

-- The same line at most once per `every` seconds: a broken state must not write a report file every frame.
local LOGGED, LOGGED_N = {}, 0
local function log_limited(s, every)
    local last = LOGGED[s]
    if last and S.clock - last < (every or 10) then return end
    if not last then
        LOGGED_N = LOGGED_N + 1
        if LOGGED_N > 200 then LOGGED, LOGGED_N = {}, 1 end     -- (messages with changing text: keep it bounded)
    end
    LOGGED[s] = S.clock
    log(s)
end

local function eid(n)
    local id = INZOI.FB1EntityId()
    id.Value = n
    return id
end

local function em() return inzoi.getB2Manager("FB2EntityManager") end
local function char(n) local m = em(); return m:FindCharacter(eid(n)) end
local function cli(cmd) return inzoi.cli.execute(cmd) end
local ATAN2 = math.atan2 or math.atan
local function dist2(ax, ay, bx, by) return math.sqrt((ax - bx) ^ 2 + (ay - by) ^ 2) end

-- ---------------------------------------------------------------- data -> engine struct builder
-- Rebuilds engine structs from the typed tables produced by client/lua_data.py.
local ENUMS, BUILD_ERRS = {}, {}

local function enum(name, v)
    if type(v) == "number" then return v end
    local e = inzoi[name] or INZOI[name]
    if not e then error("no enum " .. name) end
    local key = tostring(v)
    local cache = ENUMS[name]
    if cache and cache[key] ~= nil then return cache[key] end
    for _, cand in ipairs({ key, key:sub(1, 1):upper() .. key:sub(2), key:upper(), key:lower() }) do
        local ok, val = pcall(function() return e[cand] end)
        if ok and val ~= nil then
            ENUMS[name] = cache or {}
            ENUMS[name][key] = val
            return val
        end
    end
    error("enum " .. name .. " has no value " .. key)
end

local build, fill
build = function(v, path)
    if type(v) ~= "table" then return v end
    if v.__e then return enum(v.__e, v.v) end
    if v.__o then return build(v.__o, path) end
    if v.__s then
        local okI, cls = pcall(function() return INZOI[v.__s] end)
        if not okI or not cls then
            local okU, ucls = pcall(function() return UE[v.__s] end)
            cls = okU and ucls or nil
        end
        if not cls then error("no struct type " .. v.__s) end
        local s = cls()
        fill(s, v, path)
        return s
    end
    error("cannot build " .. path)
end

fill = function(dst, t, path)
    for k, v in pairs(t) do
        if k ~= "__s" then
            local ok, err = pcall(function()
                if type(v) == "table" and v.__a then
                    local arr = dst[k]
                    arr:Clear()
                    for i, e in ipairs(v.i) do arr:Add(build(e, path .. "." .. k .. "[" .. i .. "]")) end
                    dst[k] = arr
                elseif type(v) == "table" and v.__set then
                    local set = dst[k]
                    set:Clear()
                    for _, e in ipairs(v.i) do set:Add(build(e, path .. "." .. k)) end
                    dst[k] = set
                elseif type(v) == "table" and v.__m then
                    local m = dst[k]
                    m:Clear()
                    for _, kv in ipairs(v.i) do m:Add(build(kv[1], path .. "." .. k), build(kv[2], path .. "." .. k)) end
                    dst[k] = m
                elseif type(v) == "table" and v.__s then
                    local sub = dst[k]
                    fill(sub, v, path .. "." .. k)
                    dst[k] = sub
                else
                    dst[k] = build(v, path .. "." .. k)
                end
            end)
            if not ok then BUILD_ERRS[#BUILD_ERRS + 1] = path .. "." .. k .. ": " .. tostring(err) end
        end
    end
end

local function build_report()
    local n, shown = #BUILD_ERRS, {}
    for i = 1, math.min(n, 4) do shown[i] = BUILD_ERRS[i] end
    BUILD_ERRS = {}
    return n == 0 and "ok" or string.format("%d field errors: %s", n, table.concat(shown, " | "))
end

-- ---------------------------------------------------------------- movement primitives
local IC_CANCEL_CLEAR = function() return inzoi.EB1InteractionCancelReason.Clear end

local function interaction(c) return c:GetComponent(inzoi.EB2ComponentType.Interaction) end

local function is_walking(c)
    return INZOI.FB2InteractionComponent.IsPlayingInteraction(interaction(c))
end

-- Queue a walk to a floor point. Returns the queue result (37 = accepted; dropped orders still return 37).
local function order_walk(c, x, y, z, bag)
    local dest = UE.FVector(x, y, z - 77)
    return INZOI.FB2InteractionComponent.AddInteractionQueueByParam(interaction(c), INZOI.FB1EntityId(), dest,
        bag or "ClickMove_NonCancelable", inzoi.EB1InteractionPlayReason.None, true, false, "mp_puppet", true, false)
end

local function cancel_walk(c)
    local ic = interaction(c)
    local IC = INZOI.FB2InteractionComponent
    IC.TryCancelInteraction(ic, IC.GetCurrentInteractionQueueId(ic), IC_CANCEL_CLEAR(), "mp_redirect")
end

local function freeze(id, on)
    local am = inzoi.getB2Manager("FB2AutonomyOptionManager")
    local T = inzoi.EB1AutonomyOptionType
    local frozen = on ~= false
    -- (with "follow the global autonomy" on, the game ignores each Zoi's own setting: the puppet had a will of its own)
    if frozen then pcall(function() am:SetFollowGlobalAutonomy(false) end) end
    am:SetIndividualToggleOption(eid(id), T.AutonomyEnable, not frozen) -- no free will
    am:SetIndividualToggleOption(eid(id), T.FollowSchedule, not frozen) -- no wandering off to work/school
    am:SetIndividualToggleOption(eid(id), T.FreezeStat, frozen)         -- needs don't decay: never hungry or tired
end

-- The engine's own teleport: no "cheat applied" notice on screen (movement.teleport shows one every time).
local function teleport(id, x, y, z)
    local c = char(id)
    if not c then return end
    c:Teleport(UE.FVector(x, y, z), c:GetRotation(), false, false, false, true, false, inzoi.EB1LoadingContext.None,
        inzoi.EB1TeleportReason.None)
end

-- Native: stop what a Zoi is doing (current interaction and queue), without a cheat notice.
local function clear_all(id)
    local c = char(id)
    if not c then return end
    INZOI.FB2InteractionComponent.ClearInteractionQueue(interaction(c), IC_CANCEL_CLEAR(), true)
end

-- Native: queue an interaction like the game does when the player picks it (target: a character/object or none).
-- at: where the remote player stood when they started it; the game picks the object's slot nearest to that point (the
-- same side of the bed, the same chair at the table).
local function queue_interaction(id, bag, target, at)
    local c = char(id)
    if not c then return nil end
    local t = target and target > 0 and eid(target) or INZOI.FB1EntityId()
    local point = at and UE.FVector(at.x, at.y, at.z or c:GetLocation().Z) or UE.FVector(0, 0, 0)
    return INZOI.FB2InteractionComponent.AddInteractionQueueByParam(interaction(c), t, point, bag,
        inzoi.EB1InteractionPlayReason.None, true, false, "mp_act", true, false)
end

-- Turn on the spot to a world yaw (degrees).
local function face(c, yaw, how)
    local rot = UE.FRotator(0, yaw, 0)
    if how == "simul" then
        c:SetRotationOnlySimul(rot)
    elseif how == "look" then
        local l = c:GetLocation()
        local r = math.rad(yaw)
        c:StartLookAtLoc(UE.FVector(l.X + 300 * math.cos(r), l.Y + 300 * math.sin(r), l.Z + 60), inzoi.EB1LookAtReason.Script)
    else
        c:Teleport(c:GetLocation(), rot, false, false, false, false, true, inzoi.EB1LoadingContext.None,
            inzoi.EB1TeleportReason.None)
    end
end

local function yaw_gap(a, b)
    local d = (a - b) % 360
    return d > 180 and 360 - d or d
end

-- The object a remote player is using, found again in this world: same table id, about the same spot, on the site
-- the puppet is standing on (public places are identical in every game).
local function find_object(c, table_id, x, y, z)
    local m = em()
    -- what the puppet carries first (the dish it took out of the microwave is its own, in its hands)
    local okK, kids = pcall(function() return c:GetChildEntityIds() end)
    if okK and kids then
        for i = 1, kids:Num() do
            local id = kids:Get(i)
            local o = m:FindGameObject(id)
            if o and tostring(o:GetObjectTableId()) == table_id then return id.Value end
        end
    end
    local site = m:FindSite(c:GetLocatedSiteId())
    if not site then return nil end
    local objs = site:GetObjects()
    local best, bestD = nil, 150
    for pass = 1, 2 do                                  -- (then a wider look: food put down on another table...)
        for i = 1, objs:Num() do
            local id = objs:Get(i)
            local o = m:FindGameObject(id)
            if o and tostring(o:GetObjectTableId()) == table_id then
                local l = o:GetLocation()
                local d = math.sqrt((l.X - x) ^ 2 + (l.Y - y) ^ 2 + (l.Z - z) ^ 2)
                if d < bestD then best, bestD = id.Value, d end
            end
        end
        if best then return best end
        bestD = 400
    end
    return best
end

local function current_bag(c)
    local ic = interaction(c)
    local ok, q = pcall(INZOI.FB2InteractionComponent.GetCurrentInteractionBagId, ic)
    return ok and tostring(q) or nil
end

-- A partner's Zoi answering a social one of the players here does to it - the receiving half the game gives it (a
-- handshake, a hug, a chat, a dance: "..._FeedBack"), the turn to face them that comes first (MoveRotate), or a
-- couple social (Co_, Coop, Social) - is part of what a player does, not its own doing. Stopped as "its own", every
-- social done to a partner's Zoi failed here (the receiving half cancelled: seen on both PCs, dozens a session).
local function answering(bag)
    if not bag then return false end
    local b = bag:lower()
    return b:find("feedback", 1, true) ~= nil or b == "moverotate" or b:sub(1, 3) == "co_" or b:sub(1, 4) == "coop"
        or b:find("social", 1, true) ~= nil
end

-- ---------------------------------------------------------------- puppets
local function spawn_puppet(op)
    local member = S.members[op.key]
    if not member then error("no profile in MPBulk for " .. tostring(op.key)) end
    local old = PUPPETS[op.key]
    if old then                                         -- rebuilding (their Zoi changed): the old one goes
        pcall(cli, string.format("zoi.destroy entity_id=%d", old.id))
        PUPPETS[op.key] = nil
    end
    local m = em()
    local tpl = char(op.template)
    local attr = tpl:MakeCharacterAttribute()          -- template supplies meshes/skeleton for this gender+age
    local info = INZOI.FB1CustomizedHouseholdInfo()
    info.HouseholdName = "MP " .. op.key
    info.CreateReason = inzoi.EB2EntityCreateReason.UserCreate
    local mem = INZOI.FB1CustomizedCharacterInfo()
    mem.AppearanceSetting = attr.AppearanceSetting
    mem.Personality = attr.Personality
    mem.Characteristics = attr.Characteristics
    mem.Voice = attr.Voice
    mem.GenderIdentitySetting = attr.GenderIdentitySetting
    mem.CategorizedStyles = attr.CategorizedStyles
    fill(mem, member, "Member")
    mem.CategorizedStyles = mem.AppearanceSetting.Outfit.CategorizedStyles
    local built = build_report()
    info.HouseholdMemberCustomizedInfo:Add(mem)
    local hh = m:RegisterHousehold(tpl.LocatedCityName, info)
    local id = m:FindHousehold(hh):GetHouseholdMembers():Get(1).Value
    local okSetup, errSetup = pcall(function()
        local c = char(id)
        -- New characters exist only as background simulation until switched into the world by hand.
        c:SetLocationOnlySimul(UE.FVector(op.x, op.y, op.z))
        local me = char(op.near or op.template)
        c:SetLocatedSiteId(me:GetLocatedSiteId())
        local ES, R = inzoi.EB2EntityState, inzoi.EB2EntityStateChangeReason
        c:RemoveEntityState(ES.TimelineSimulateOnly, R.RequestSpawn)
        c:AddEntityState(ES.Simulate, R.RequestSpawn)
        c:AddEntityState(ES.GameSpawned, R.RequestSpawn)
        freeze(id)
        cli(string.format("life_and_death.stop_aging entity_id=%d enable=true", id))  -- a puppet never ages or dies here
        cli(string.format("life_and_death.immortal entity_id=%d enable=true", id))
        teleport(id, op.x, op.y, op.z)
        if op.home_door and op.home_site then
            c:GrantKnockDoorVisitSitePrivilege(eid(op.home_door), eid(op.home_site))
        end
    end)
    if not okSetup then                                 -- a failed spawn is retried: don't leave a half-made Zoi behind
        pcall(cli, string.format("zoi.destroy entity_id=%d", id))
        error(errSetup, 0)
    end
    PUPPETS[op.key] = { id = id, trail = {}, dest = nil, lastOrderT = -10, pending = nil, hidden = false }
    report("spawned", op.key .. " " .. id)
    return string.format("spawned %s as %d (household %d), build %s", op.key, id, hh.Value, built)
end

-- Shared world: a player's Zoi already lives in this world (their own household). Drive that one instead of building
-- a copy: same character, same household, same home and relationships.
local function adopt_puppet(op)
    local id = tonumber(op.entity)
    id = id and math.floor(id)
    if not id or not char(id) then error("no character " .. tostring(op.entity)) end
    local old = PUPPETS[op.key]
    if old and old.id ~= id and not old.adopted then         -- a copy made before: the real one takes over
        pcall(cli, string.format("zoi.destroy entity_id=%d", old.id))
    end
    freeze(id)
    pcall(clear_all, id)
    pcall(function() char(id):SetCharacterHiddenInGame(false, "mp_away") end)
    if op.x and op.y and op.z then teleport(id, op.x, op.y, op.z) end
    if op.home_door and op.home_site then
        pcall(function() char(id):GrantKnockDoorVisitSitePrivilege(eid(op.home_door), eid(op.home_site)) end)
    end
    PUPPETS[op.key] = { id = id, trail = {}, dest = nil, lastOrderT = -10, pending = nil, hidden = false,
                        adopted = true }
    report("spawned", op.key .. " " .. id)
    return string.format("adopted %s: %d", op.key, id)
end

local function despawn_puppet(op)
    local p = PUPPETS[op.key]
    if not p then return "no puppet " .. tostring(op.key) end
    if p.adopted then
        pcall(function()
            local mc = char(p.id):GetMovementComponent()
            mc.InputVector = UE.FVector(0, 0, 0)
            mc.WalkSpeed = 190
        end)
        freeze(p.id, false)                             -- their player left: the game looks after them again
        pcall(clear_all, p.id)
        pcall(function() char(p.id):SetCharacterHiddenInGame(false, "mp_away") end)
    else
        cli(string.format("zoi.destroy entity_id=%d", p.id))
    end
    PUPPETS[op.key] = nil
    report("despawned", op.key)
    return (p.adopted and "released " or "despawned ") .. op.key
end

local function add_trail(op)
    local p = PUPPETS[op.key]
    if not p then return nil end
    for _, pt in ipairs(op.pts or {}) do                -- batches overlap; only take samples we haven't seen
        if p.lastN and pt[1] + 100 < p.lastN then      -- (their program restarted: its count starts over)
            p.lastN, p.trail = nil, {}
            log(op.key .. ": their sample count started over (their program restarted): following again")
        end
        if pt[1] > (p.lastN or -1) then
            p.lastN = pt[1]
            local last = p.trail[#p.trail]
            if not last or dist2(last.x, last.y, pt[2], pt[3]) > 5 then
                -- t: last change (moved or turned); at: when it moved here (turning on the spot doesn't count)
                p.trail[#p.trail + 1] = { t = S.clock, at = S.clock, x = pt[2], y = pt[3], z = pt[4], yaw = pt[5], st = pt[6] }
            elseif pt[5] and yaw_gap(last.yaw or pt[5], pt[5]) > 10 then
                last.yaw, last.t = pt[5], S.clock      -- turned on the spot
            end
        end
    end
    while #p.trail > 120 do table.remove(p.trail, 1) end
    return nil      -- trails are too frequent to log
end

-- Follow logic: head for the player's newest position (led a little ahead, see below). Redirecting a walk costs a
-- short stop, so only re-aim when the player has moved REAIM cm away from where we're headed (or we've stopped);
-- sprint when far behind; teleport when hopelessly behind (other side of town, loading screen...).
local REAIM, SPRINT, TELEPORT, MIN_GAP = 150, 600, 600, 0.15
-- Where they are, exactly: closer than ARRIVED cm is there; a walk that stopped short (refused, a door, someone's
-- home) is finished with a teleport after SNAP_AFTER s, a walk going nowhere after STUCK_FOR s.
local ARRIVED, SNAP_AFTER, STUCK_FOR = 70, 1.2, 2.2
local LEAP_AT, LEAP_EVERY = 250, 1.5
-- Direct drive (how networked games move remote players): every tick the puppet walks, with the game's own
-- locomotion (the movement component's InputVector, like WASD, and its WalkSpeed), towards where its player was
-- RENDER_DELAY s ago, interpolated between the two trail points around that moment (by the sender's clock), or a short
-- extrapolation past the newest one. No path orders, so no stop-and-go. Another floor or DIRECT_TP away: placed there.
local DIRECT, RENDER_DELAY, EXTRAPOLATE, DIRECT_TP, DIRECT_K, DIRECT_MAX = true, -0.25, 0.45, 350, 6.0, 900
local SKEL_JUMP = 500         -- cm: a puppet whose skeleton comes in is set where they are; this far, a real teleport
local STANDING_AFTER = 0.35   -- no new point for this long: they're standing (velocity 0, no more prediction)      -- running and this far behind: a small leap to 1 m behind them
-- Realtime: the player's own position goes out from here ME_EVERY s (read straight from the engine); a puppet aims
-- where its player will be LATENCY s from now (their speed, from the sender's clock on each point) and walks at their
-- speed; anything it starts on its own while its player is online is cancelled (only what they do is done).
local ME_EVERY, POLL_EVERY, LATENCY, OWN_CHECK = 0.1, 0.03, 0.35, 0.4
local ACT_LEAVE = 600       -- while copying an action, only a remote player this far from the puppet means "they left"
local ACT_MOVED = 120       -- ...or one who walked this far from where they started it
local ACT_WITH = 300        -- (unless the puppet does it too and is still this close: the action itself walks them)
local FACE_GAP, FACE_EVERY = 25, 1.5
-- Smoothing (no new engine calls: only where the puppet aims and when it redirects):
--  * the trail arrives late (~0.5-1 s), so while the player is walking aim LEAD cm past their newest point, along the
--    direction of their last LEAD_BACK cm: the goal stays ahead of the puppet instead of being reached and stopped at
--    (it can end up to LEAD cm past where they stop: well inside the "arrived" margin below);
--  * a redirect costs a short stop, so while the new goal lies straight on along the walk under way, wait until it's
--    REAIM_STRAIGHT away (a turn still re-aims at REAIM);
--  * trail points carry no sender timestamps (t/at are when they reached us, in batches), so walk vs sprint stays
--    chosen by distance (SPRINT).
local LEAD, LEAD_BACK, MOVING_FOR, REAIM_STRAIGHT, STRAIGHT_COS = 200, 150, 0.6, 450, 0.85

-- How fast its player moves (cm/s), from the sender's times on the last points (0 when standing or unknown).
local function remote_speed(p)
    local n = #p.trail
    local last = p.trail[n]
    if not last or not last.st or S.clock - (last.at or 0) > MOVING_FOR then return 0 end
    for i = n - 1, math.max(1, n - 10), -1 do
        local q = p.trail[i]
        if q.st and last.st - q.st >= 0.3 then
            return dist2(q.x, q.y, last.x, last.y) / (last.st - q.st)
        end
    end
    return 0
end

-- Walk at its player's pace (a bit faster when behind): the engine's walk speed for that character.
local function match_speed(p, speed, far)
    -- movement.set_speed (no notice on screen, unlike movement.teleport): their pace + 15%, more when behind
    local catch = far > 120 and math.min(350, (far - 120) * 0.9) or 0
    local want = speed > 30 and math.max(170, math.min(450, speed * 1.15 + catch)) or (far > 200 and 300 or 190)
    p.fast = speed > 330 or far > SPRINT               -- (a fast walk doesn't go faster: the game's own sprint)
    if math.abs(want - (p.speedSet or 190)) < 30 or S.clock - (p.speedT or -10) < 0.4 then return end
    p.speedSet, p.speedT = want, S.clock
    pcall(cli, string.format("movement.set_speed entity_id=%d action=set Speed=%.0f", p.id, want))
end

-- Where to send the puppet for trail point `pick`: pick itself, or a point LEAD ahead while the player walks straight.
local function lead_point(p, pick, me)
    if not pick.at or S.clock - pick.at > MOVING_FOR then return pick end     -- standing still (or only turning)
    if math.abs(me.Z - pick.z) > 150 then return pick end                     -- another floor: follow exactly
    local n, back = #p.trail, nil
    for i = n - 1, math.max(1, n - 8), -1 do
        local q = p.trail[i]
        if math.abs(q.z - pick.z) > 50 then break end                         -- stairs/slope: no extrapolating
        back = q
        if dist2(q.x, q.y, pick.x, pick.y) >= LEAD_BACK then break end
    end
    if not back then return pick end
    local dx, dy = pick.x - back.x, pick.y - back.y
    local len = math.sqrt(dx * dx + dy * dy)
    if len < 50 then return pick end                                          -- too little movement to trust a direction
    local ahead = math.max(LEAD, math.min(550, remote_speed(p) * (LATENCY + 0.6) + 80))
    local k = ahead / len
    return { x = pick.x + dx * k, y = pick.y + dy * k, z = pick.z }
end

-- The new goal is straight on from the current one, along the walk under way (so carrying on loses nothing).
local function straight_on(p, aim)
    local o, d = p.orderFrom, p.dest
    if not (o and d) then return false end
    local ax, ay = d.x - o.x, d.y - o.y
    local bx, by = aim.x - d.x, aim.y - d.y
    local la, lb = math.sqrt(ax * ax + ay * ay), math.sqrt(bx * bx + by * by)
    if la < 50 or lb < 1 then return false end
    return (ax * bx + ay * by) / (la * lb) > STRAIGHT_COS
end

-- Where the player was RENDER_DELAY s ago (sender's clock), and their velocity (cm/s). nil without sender times.
local function interp_target(p)
    local n = #p.trail
    local last = p.trail[n]
    if not last or not last.st then return nil end
    local now_st = last.st + (S.clock - last.at)
    local t = now_st - RENDER_DELAY
    local vx, vy = 0, 0
    for i = n - 1, math.max(1, n - 8), -1 do
        local q = p.trail[i]
        if q.st and last.st - q.st >= 0.15 then
            vx, vy = (last.x - q.x) / (last.st - q.st), (last.y - q.y) / (last.st - q.st)
            break
        end
    end
    if S.clock - last.at > STANDING_AFTER then vx, vy = 0, 0 end   -- (no new point for a while: standing)
    if t >= last.st then
        local ahead = math.min(EXTRAPOLATE, t - last.st)
        return { x = last.x + vx * ahead, y = last.y + vy * ahead, z = last.z, yaw = last.yaw }, vx, vy
    end
    for i = n - 1, 1, -1 do
        local a, b = p.trail[i], p.trail[i + 1]
        if a.st and b.st and a.st <= t and t <= b.st then
            local k = (b.st > a.st) and (t - a.st) / (b.st - a.st) or 1
            return { x = a.x + (b.x - a.x) * k, y = a.y + (b.y - a.y) * k, z = a.z + (b.z - a.z) * k, yaw = b.yaw }, vx, vy
        end
    end
    return { x = last.x, y = last.y, z = last.z, yaw = last.yaw }, vx, vy
end

local function stop_direct(c)
    pcall(function()
        local mc = c:GetMovementComponent()
        mc.InputVector = UE.FVector(0, 0, 0)
    end)
end

local function start_act(c, p)
    local a = p.actQueued
    p.actQueued = nil
    local ok, err = pcall(queue_interaction, p.id, a.bag, a.target, a.at)
    if not ok then log(string.format("%s act %s: the game refused to queue it (%s)", p.key or "?", a.bag, tostring(err))) end
    p.act.started = S.clock
end

-- 1.5 s after an action was queued: is it really running on the puppet? (the telemetry report shows why not)
local function check_act(key, c, p)
    if not p.act or not p.act.started or p.act.checked or S.clock - p.act.started < 1.5 then return end
    p.act.checked = true
    local cur = current_bag(c)
    local walking = cur and cur:match("^ClickMove")
    log(string.format("%s act %s: %s (the Zoi is doing %s)", key, p.act.bag,
        cur == p.act.bag and "running" or (walking and "walking there" or "NOT running"), tostring(cur)))
end

local function drive(key, p, dt)
    p.key = key
    local c = char(p.id)
    if not c then error("no character " .. tostring(p.id)) end     -- counted by on_tick: gone for good -> dropped
    if p.pending then                                   -- second half of a redirect: the old walk is gone now
        p.pending.t = p.pending.t + dt
        if not is_walking(c) or p.pending.t > 0.08 then
            order_walk(c, p.pending.x, p.pending.y, p.pending.z, p.pending.bag)
            p.pending = nil
        end
        return
    end
    check_act(key, c, p)
    if p.actQueued then                                 -- an action waits for the cancelled walk to clear
        p.actQueued.t = p.actQueued.t + dt
        if not is_walking(c) or p.actQueued.t > 0.3 then start_act(c, p) end
        return
    end
    if not p.act and not p.actQueued and S.clock - (p.ownT or -10) > OWN_CHECK then
        p.ownT = S.clock
        local bag = current_bag(c)
        p.answering = answering(bag)
        -- (walks are ours; answering a social done to it here stays: answering)
        if bag and bag ~= "None" and bag ~= "nil" and bag ~= "" and not bag:match("^ClickMove") and not p.answering then
            pcall(clear_all, p.id)
            log_limited(string.format("%s's Zoi started %s on its own: stopped (only what they do is done)", key, bag), 30)
        end
    elseif p.act then
        p.answering = false
    end
    if p.skel and not p.hidden and #p.trail > 0 then
        -- Their skeleton comes in (the companion's "skel"): the pose is theirs, frame by frame, so the puppet doesn't
        -- walk (a walk lags, gets stuck on furniture, is put back by teleports - the bones then slid around a body in
        -- another place). Every tick it's set where they are, smoothed over the trail (interp_target), simulation only
        -- (no teleport effects); only another floor or a big jump is a real teleport.
        -- Not while it does a social with my Zoi (p.act: the only actions acted out while their skeleton comes in) or
        -- answers one (p.answering): the game lines the two up for it (a hug, a dance: their bones, from their game
        -- where they do the same with my Zoi there, fit mine where the game puts them here - not where they stand in
        -- their world). Never steered meanwhile either (0.9.61-0.9.64 pushed an answering one around; 0.9.65 pinned it
        -- where they are). A walk into place it can't do by itself is freed by the companion (unstick_puppets).
        if p.act or p.answering then return end
        local tgt = interp_target(p) or p.trail[#p.trail]
        local mc = c:GetMovementComponent()
        mc.InputVector = UE.FVector(0, 0, 0)
        local me = c:GetLocation()
        if dist2(me.X, me.Y, tgt.x, tgt.y) > SKEL_JUMP or math.abs(me.Z - tgt.z) > 150 then
            teleport(p.id, tgt.x, tgt.y, tgt.z)
        else
            c:SetLocationOnlySimul(UE.FVector(tgt.x, tgt.y, tgt.z))
        end
        if tgt.yaw then c:SetRotationOnlySimul(UE.FRotator(0, tgt.yaw, 0)) end
        return
    end
    if #p.trail == 0 or p.hidden or S.clock - p.lastOrderT < MIN_GAP then return end
    local pick = p.trail[#p.trail]
    local me = c:GetLocation()
    local far = dist2(me.X, me.Y, pick.x, pick.y)
    if p.pose then                                      -- (a posture: exactly where and how they are, no walking)
        local mc = c:GetMovementComponent()
        mc.InputVector = UE.FVector(0, 0, 0)
        local yawOff = pick.yaw and yaw_gap(c:GetRotation().Yaw, pick.yaw) or 0
        if (far > 25 or yawOff > 12) and S.clock - (p.pinT or -10) > 0.4 then
            p.pinT = S.clock
            c:Teleport(UE.FVector(pick.x, pick.y, pick.z), UE.FRotator(0, pick.yaw or c:GetRotation().Yaw, 0), false,
                false, false, false, true, inzoi.EB1LoadingContext.None, inzoi.EB1TeleportReason.None)
        end
        return
    end
    if p.act then
        -- Doing what the remote player does (the interaction walks the puppet to the object/person itself): stay on
        -- it until the action ends (act_stop), they walk off from where it started (ACT_MOVED), or they're clearly
        -- somewhere else.
        local moved = p.act.x and dist2(pick.x, pick.y, p.act.x, p.act.y) > ACT_MOVED
        if not moved and far < ACT_LEAVE then return end
        local doing = current_bag(c) == p.act.bag
        -- (an action walks its Zoi to the fridge, the sofa...: doing it too and still close, it carries on)
        if moved and doing and far < ACT_WITH then return end
        if doing then clear_all(p.id) end
        p.act, p.actQueued = nil, nil
    end
    if DIRECT and pick.st then
        local tgt, vx, vy = interp_target(p)
        if tgt then
            local dx, dy = tgt.x - me.X, tgt.y - me.Y
            local err = math.sqrt(dx * dx + dy * dy)
            local mc = c:GetMovementComponent()
            local bag = current_bag(c)
            if bag and bag:match("^ClickMove") then cancel_walk(c) end     -- (no path orders while driven)
            if math.abs(me.Z - tgt.z) > 150 or err > DIRECT_TP then
                mc.InputVector = UE.FVector(0, 0, 0)
                teleport(p.id, tgt.x, tgt.y, tgt.z)
                log_limited(string.format("%s placed where they are (%.1f m, %s)", key, err / 100,
                    math.abs(me.Z - tgt.z) > 150 and "another floor" or "too far"), 10)
                return
            end
            local spd = math.sqrt(vx * vx + vy * vy)
            if err < 18 and spd < 25 then
                mc.InputVector = UE.FVector(0, 0, 0)
                if tgt.yaw and yaw_gap(c:GetRotation().Yaw, tgt.yaw) > FACE_GAP and S.clock - (p.facedT or -10) > 0.5 then
                    p.facedT = S.clock
                    face(c, tgt.yaw, "simul")
                end
                return
            end
            mc.WalkSpeed = math.max(60, math.min(DIRECT_MAX, spd + err * DIRECT_K))
            face(c, math.deg(ATAN2(dy, dx)), "simul")
            mc.InputVector = UE.FVector(1, 0, 0)
            return
        end
    end
    if far > TELEPORT then
        teleport(p.id, pick.x, pick.y, pick.z)
        p.lastOrderT, p.dest, p.aimFor = S.clock, pick, pick
        log(string.format("%s teleported (%.0f m behind)", key, far / 100))
        return
    end
    local walking = is_walking(c)
    -- A dropped order (destination occupied or unreachable) leaves the puppet standing: retry 80 cm shorter, once.
    if p.dest and not walking and not p.retried and S.clock - p.lastOrderT > 1.0 and p.orderFrom
        and dist2(me.X, me.Y, p.orderFrom.x, p.orderFrom.y) < 10 and dist2(me.X, me.Y, p.dest.x, p.dest.y) > 120 then
        local d = dist2(me.X, me.Y, p.dest.x, p.dest.y)
        local k = (d - 80) / d
        p.retried, p.lastOrderT = true, S.clock
        order_walk(c, me.X + (p.dest.x - me.X) * k, me.Y + (p.dest.y - me.Y) * k, p.dest.z)
        return
    end
    if math.abs(me.Z - pick.z) > 150 then               -- another floor: never "arrived", walk there (stairs)
        if not walking and p.aimFor == pick and S.clock - p.lastOrderT > 3 then
            teleport(p.id, pick.x, pick.y, pick.z)      -- the walk was refused: go straight there
            p.lastOrderT = S.clock
            log(string.format("%s teleported to their floor", key))
            return
        end
        far = math.max(far, 300)
    end
    local stopped = pick.at and S.clock - pick.at > 0.6
    if stopped and far >= 150 and S.clock - p.lastOrderT > SNAP_AFTER then
        if walking then cancel_walk(c) end
        teleport(p.id, pick.x, pick.y, pick.z)
        p.lastOrderT, p.dest, p.aimFor, p.facedT = S.clock, pick, pick, nil
        log_limited(string.format("%s placed where they stopped (%.1f m off)", key, far / 100), 20)
        return
    end
    local spd = remote_speed(p)
    if not stopped and spd > 250 and far > LEAP_AT and S.clock - (p.leapT or -10) > LEAP_EVERY then
        local back = p.trail[math.max(1, #p.trail - 3)]
        local dx, dy = pick.x - back.x, pick.y - back.y
        local len = math.sqrt(dx * dx + dy * dy)
        if len > 30 and math.abs(me.Z - pick.z) < 150 then
            p.leapT = S.clock
            teleport(p.id, pick.x - dx / len * 100, pick.y - dy / len * 100, pick.z)
            p.lastOrderT, p.dest, p.aimFor = S.clock - MIN_GAP, nil, nil
            return
        end
    end
    -- Exactly where they are: a walk the game refused or gave up on (a locked door, someone else's home, a spot it
    -- can't reach) leaves the puppet standing short of them. Once the order has had SNAP_AFTER s, put it there.
    if not walking and p.aimFor == pick and far >= ARRIVED and S.clock - p.lastOrderT > SNAP_AFTER then
        teleport(p.id, pick.x, pick.y, pick.z)
        p.lastOrderT, p.dest, p.facedT = S.clock, pick, nil
        log_limited(string.format("%s placed where they are (the walk stopped %.1f m short)", key, far / 100), 20)
        return
    end
    -- Walking without getting anywhere (stuck on furniture, a door that won't open): same.
    if walking then
        local pr = p.progress
        if not pr or dist2(me.X, me.Y, pr.x, pr.y) > 40 then
            p.progress = { x = me.X, y = me.Y, t = S.clock }
        elseif S.clock - pr.t > STUCK_FOR and far >= ARRIVED then
            cancel_walk(c)
            teleport(p.id, pick.x, pick.y, pick.z)
            p.lastOrderT, p.dest, p.aimFor, p.progress = S.clock, pick, pick, nil
            log_limited(string.format("%s was stuck: placed where they are", key), 20)
            return
        end
    else
        p.progress = nil
    end
    -- (aimFor: the trail point the last order was for; dest may lie LEAD past it)
    if far < ARRIVED then
        -- Arrived (or as close as it gets: the spot can be taken): also face the way they face, since a walk ends
        -- facing the walking direction.
        if not walking and far < 300 and pick.yaw and S.clock - (p.facedT or -10) > FACE_EVERY
            and S.clock - pick.t > 0.5 and yaw_gap(c:GetRotation().Yaw, pick.yaw) > FACE_GAP then
            p.facedT = S.clock
            face(c, pick.yaw, p.faceHow)
        end
        return
    end
    match_speed(p, remote_speed(p), far)
    local aim = lead_point(p, pick, me)
    local reaim = (walking and far <= SPRINT and straight_on(p, aim)) and REAIM_STRAIGHT or REAIM
    local stale = not p.dest or dist2(p.dest.x, p.dest.y, aim.x, aim.y) > reaim
    if walking and not stale then return end
    if not walking and p.aimFor == pick then return end
    local bag = p.fast and "ClickMove_Sprint_Close" or nil
    p.lastOrderT, p.dest, p.aimFor, p.retried, p.orderFrom = S.clock, aim, pick, false, { x = me.X, y = me.Y }
    if walking then
        cancel_walk(c)
        p.pending = { x = aim.x, y = aim.y, z = aim.z, bag = bag, t = 0 }
    else
        order_walk(c, aim.x, aim.y, aim.z, bag)
    end
end

-- After a reload (of this script or the whole game) find our puppets again by their "MP <key>" households.
local function rediscover()
    local m = em()
    local all = m:GetAllEntities(inzoi.EB2EntityType.Household)
    local found = 0
    for i = 1, all:Num() do
        local ok, err = pcall(function()
            local hh = m:FindHousehold(all:Get(i):GetEntityId())
            local name = tostring(hh:GetHouseholdName())
            local key = name:match("^MP (p%x+)$")
            local members = hh:GetHouseholdMembers()
            if key and members:Num() > 0 and not PUPPETS[key] and members:Get(1).Value ~= S.me then   -- not the one being played
                PUPPETS[key] = { id = members:Get(1).Value, trail = {}, lastOrderT = -10, hidden = false }
                found = found + 1
            end
        end)
    end
    return found
end

-- ---------------------------------------------------------------- mailbox
local OPS = {
    spawn = spawn_puppet,
    adopt = adopt_puppet,
    despawn = despawn_puppet,
    trail = add_trail,
    hide = function(op)
        local p = PUPPETS[op.key]
        if not p then return nil end
        p.hidden = op.on ~= false
        char(p.id):SetCharacterHiddenInGame(p.hidden, "mp_away")
        return string.format("%s %s", op.key, p.hidden and "hidden" or "shown")
    end,
    admit = function(op)
        local p = PUPPETS[op.key]
        if not p then return nil end
        char(p.id):GrantKnockDoorVisitSitePrivilege(eid(op.door), eid(op.site))
        return "admitted " .. op.key .. " to site " .. tostring(op.site)
    end,
    -- The remote player started an interaction: {key, bag, target?} (a character here) or {key, bag, obj, ox, oy, oz}
    -- (an object, found again by table id near that spot). x, y = where the remote player stood when it started.
    act = function(op)
        local p = PUPPETS[op.key]
        if not p then return nil end
        local c = char(p.id)
        local target = op.target
        if not target and not op.obj and tostring(op.bag):match("^Self_") then
            target = p.id                               -- (on oneself: queued without a target, the game drops it)
        end
        if op.obj then
            target = find_object(c, op.obj, op.ox, op.oy, op.oz)
            if not target then                          -- something only their world has (a plate they cooked...)
                if op.anim then cli(string.format("motion.play entity_id=%d anim_id=%s", p.id, op.anim)) end
                return string.format("%s: no %s here for %s%s", op.key, op.obj, op.bag, op.anim and ", showing the motion" or "")
            end
        end
        p.pending = nil
        stop_direct(c)
        if is_walking(c) or p.act then cancel_walk(c) end
        p.act = { bag = op.bag, x = op.x, y = op.y }
        p.actQueued = { bag = op.bag, target = target, t = 0, at = op.x and { x = op.x, y = op.y } or nil }
        return string.format("%s does %s%s", op.key, op.bag, target and (" on " .. target) or "")
    end,
    -- Object states of a lot (on/off, open/closed, dirty..., the TV channel), for the companion's objsync.py: one line a
    -- game object "entity|table|x|y|z|group:state,group:state|channel" (the Slot groups, who sits where, are left out:
    -- the skeletons show that), reported as "states" with the lot first.
    read_states = function(op)
        local m = em()
        local site = m:FindSite(eid(op.site))
        if not site then return nil end
        local SC, T = INZOI.FB2StateComponent, inzoi.EB2ComponentType
        local objs, lines = site:GetObjects(), { tostring(op.site) }
        for i = 1, objs:Num() do
            local id = objs:Get(i)
            local o = m:FindGameObject(id)
            local ok, sc = pcall(function() return o and o:GetComponent(T.State) end)
            if ok and sc then
                local parts = {}
                pcall(function()
                    local keys = sc.States:Keys()
                    for k = 1, keys:Num() do
                        local st = sc.States:Find(keys:Get(k))
                        local g = st and tostring(st.StateGroupId) or ""
                        if st and g ~= "" and not g:match("^Slot") then parts[#parts + 1] = g .. ":" .. tostring(st.StateId) end
                    end
                end)
                table.sort(parts)
                local l = o:GetLocation()
                lines[#lines + 1] = string.format("%d|%s|%.0f|%.0f|%.0f|%s|%s", id.Value, tostring(o:GetObjectTableId()),
                    l.X, l.Y, l.Z, table.concat(parts, ","), tostring(SC.GetCurrentChannelId(sc)))
            end
        end
        report("states", table.concat(lines, ";"))
        return nil
    end,
    -- Every object of a lot with its dust and wear, for the companion's detailsync.py: one line an object
    -- "entity|table|x|y|z|dust|grade" (dust in 1/1000, the wear grade; -1: the object has none), "dirt" with the lot
    -- first. (Objects without either are listed too: the colours are read by entity.)
    read_dirt = function(op)
        local m = em()
        local site = m:FindSite(eid(op.site))
        if not site then return nil end
        local D, W = INZOI.FB2DustyComponent, INZOI.FB2ObjectWeatheringComponent
        local objs, lines = site:GetObjects(), { tostring(op.site) }
        for i = 1, objs:Num() do
            local id = objs:Get(i)
            local o = m:FindGameObject(id)
            if o then
                local d, w = -1, -1
                pcall(function()
                    local dc = o:GetComponent(38)              -- (EB2ComponentType.Dusty)
                    if dc then d = math.floor(D.GetDustyValue(dc) * 1000 + 0.5) end
                end)
                pcall(function()
                    local wc = o:GetComponent(39)              -- (EB2ComponentType.ObjectWeathering)
                    if wc then w = math.floor(W.GetGrade(wc)) end
                end)
                local l = o:GetLocation()
                lines[#lines + 1] = string.format("%d|%s|%.0f|%.0f|%.0f|%d|%d", id.Value, tostring(o:GetObjectTableId()),
                    l.X, l.Y, l.Z, d, w)
            end
        end
        report("dirt", table.concat(lines, ";"))
        return nil
    end,
    -- {site, items = {{table, x, y, z, d, w}, ...}}: each object here (same kind, nearest within a metre) gets that dust
    -- (1/1000) and wear grade (-1: left as it is). (The setters need their last argument: without it nothing changes.)
    set_dirt = function(op)
        local m = em()
        local site = m:FindSite(eid(op.site))
        if not site then return nil end
        local D, W = INZOI.FB2DustyComponent, INZOI.FB2ObjectWeatheringComponent
        local objs, byTable = site:GetObjects(), {}
        for i = 1, objs:Num() do
            local o = m:FindGameObject(objs:Get(i))
            if o then
                local t = tostring(o:GetObjectTableId())
                byTable[t] = byTable[t] or {}
                table.insert(byTable[t], o)
            end
        end
        local done, missing = 0, 0
        for _, it in ipairs(op.items or {}) do
            local best, bestD = nil, 100
            for _, o in ipairs(byTable[it.table] or {}) do
                local l = o:GetLocation()
                local dd = math.sqrt((l.X - it.x) ^ 2 + (l.Y - it.y) ^ 2 + (l.Z - it.z) ^ 2)
                if dd < bestD then best, bestD = o, dd end
            end
            if best then
                if it.d and it.d >= 0 then pcall(function() D.SetDustyValue(best:GetComponent(38), it.d / 1000, true) end) end
                if it.w and it.w >= 0 then pcall(function() W.SetWeatheringGrade(best:GetComponent(39), it.w, true) end) end
                done = done + 1
            else
                missing = missing + 1
            end
        end
        return string.format("dust and wear set on %d object(s)%s", done, missing > 0 and (", " .. missing .. " not here") or "")
    end,
    -- {entity, bag, target, ax, ay}: my Zoi is on the spot of an object (a side of the bed, a seat) where my partner's Zoi
    -- is in their game - two bodies in one place. Mine goes to the object's other spot: the same slot on the other
    -- side (InteractionSlot_01_l <-> _01_r), else the slot farthest from them; its action is queued again there (the
    -- game picks the slot nearest to the point given).
    reslot = function(op)
        local m = em()
        local c, o = char(op.entity), m:FindGameObject(eid(op.target))
        if not c or not o then return "reslot: nothing to move" end
        local SC = INZOI.FB2SlotComponent
        local okC, sc = pcall(function() return o:GetComponent(14) end)  -- (EB2ComponentType.Slot)
        if not okC or not sc then return "reslot: " .. tostring(o:GetObjectTableId()) .. " has no spots" end
        local me = c:GetLocation()
        local slots, mine, mineD = {}, nil, 1e12
        local infos = SC.GetSlotStateInfos(sc)
        for i = 1, infos:Num() do
            local it = infos:Get(i)
            local name = tostring(it.SlotName)
            local okL, has, loc = pcall(SC.GetSlotLocationBySlotName, sc, it.SlotName)
            if okL and has and loc and not name:match("Fx") and not name:match("Reform") then
                local s = { name = name, x = loc.X, y = loc.Y, z = loc.Z }
                slots[#slots + 1] = s
                local d = (loc.X - me.X) ^ 2 + (loc.Y - me.Y) ^ 2
                if d < mineD then mine, mineD = s, d end
            end
        end
        if not mine then return "reslot: no spots found on " .. tostring(o:GetObjectTableId()) end
        local function swap(n)
            if n:match("_l$") then return n:sub(1, -3) .. "_r" end
            if n:match("_r$") then return n:sub(1, -3) .. "_l" end
            local a, b = n:match("^(.*)_l(_%a+)$")
            if a then return a .. "_r" .. b end
            a, b = n:match("^(.*)_r(_%a+)$")
            if a then return a .. "_l" .. b end
            return nil
        end
        local function far(s) return math.sqrt((s.x - op.ax) ^ 2 + (s.y - op.ay) ^ 2) end
        local want, other = nil, swap(mine.name)
        for _, s in ipairs(slots) do
            if s.name == other and far(s) > 60 then want = s end
        end
        if not want then
            local best = 80
            for _, s in ipairs(slots) do
                if s.name ~= mine.name and far(s) > best then want, best = s, far(s) end
            end
        end
        if not want then return "reslot: no free spot on " .. tostring(o:GetObjectTableId()) end
        pcall(clear_all, op.entity)
        queue_interaction(op.entity, op.bag, op.target, { x = want.x, y = want.y, z = want.z })
        return string.format("reslot: %s moved from %s to %s on %s", tostring(op.bag), mine.name, want.name,
            tostring(o:GetObjectTableId()))
    end,
    -- {site, table, x, y, z, states = {"group:state", ...}, channel}: that object here (same kind, within a metre) is put in
    -- those states (the game's own state change: lights, screens and sounds follow) and on that channel.
    set_states = function(op)
        local m = em()
        local site = m:FindSite(eid(op.site))
        if not site then return nil end
        local SC, T = INZOI.FB2StateComponent, inzoi.EB2ComponentType
        local objs, best, bestD = site:GetObjects(), nil, 100
        for i = 1, objs:Num() do
            local o = m:FindGameObject(objs:Get(i))
            if o and tostring(o:GetObjectTableId()) == op.table then
                local l = o:GetLocation()
                local d = math.sqrt((l.X - op.x) ^ 2 + (l.Y - op.y) ^ 2 + (l.Z - op.z) ^ 2)
                if d < bestD then best, bestD = o, d end
            end
        end
        if not best then return string.format("no %s here to set (%s)", op.table, table.concat(op.states or {}, ",")) end
        local sc = best:GetComponent(T.State)
        local want = op.states or {}
        if #want == 0 then want = { "Normal:Normal" } end
        for _, s in ipairs(want) do
            local g, st = s:match("^([^:]+):(.+)$")
            if g then pcall(SC.ChangeState, sc, g, st, INZOI.FB1EntityId()) end
        end
        if op.channel and op.channel ~= "" and tostring(SC.GetCurrentChannelId(sc)) ~= op.channel then
            pcall(SC.ChangeChannel, sc, op.channel, INZOI.FB1EntityId())
        end
        return string.format("%s set to %s%s", op.table, table.concat(want, ","),
            op.channel and op.channel ~= "" and op.channel ~= "None" and (" ch " .. op.channel) or "")
    end,
    -- Their action has started and the puppet is still on its way: put it exactly where and how they are (their
    -- action is queued again right after, so it starts on the spot instead of after a walk).
    snap = function(op)
        local p = PUPPETS[op.key]
        if not p then return nil end
        local c = char(p.id)
        stop_direct(c)
        pcall(clear_all, p.id)
        p.act, p.actQueued, p.pending = nil, nil, nil
        c:Teleport(UE.FVector(op.x, op.y, op.z), UE.FRotator(0, op.yaw or c:GetRotation().Yaw, 0), false, false, false,
            false, true, inzoi.EB1LoadingContext.None, inzoi.EB1TeleportReason.None)
        return string.format("%s snapped to where they are for %s", op.key, tostring(op.bag))
    end,
    -- A body posture plays on their Zoi (the companion mirrors the montages): hold it at their exact spot and facing.
    pose = function(op)
        local p = PUPPETS[op.key]
        if not p then return nil end
        p.pose = op.on and true or nil
        if p.pose then stop_direct(char(p.id)) end
        return nil
    end,
    -- {key}: a Zoi of this save stands for that player (adopted: their family member here): it takes their look as
    -- their own game has it (hair, face, body, age's look...: their profile from MPBulk), which this save's copy of them
    -- doesn't follow by itself - a haircut, aging, a new style there showed here as the old look under their skeleton
    relook = function(op)
        local p, member = PUPPETS[op.key], S.members[op.key]
        if not p or not member then return nil end
        local look = member.AppearanceSetting
        if type(look) ~= "table" then return "no look in " .. op.key .. "'s profile" end
        local AC = INZOI.FB2AppearanceComponent
        local comp = char(p.id):GetComponent(inzoi.EB2ComponentType.Appearance)
        local setting = comp.AppearanceSetting
        fill(setting, look, "Look")
        AC.SetAppearanceSetting(comp, setting)
        pcall(AC.RefreshAppearance, comp)
        return "look of " .. op.key .. " applied: " .. build_report()
    end,
    -- {key, on}: that player's skeleton frames come in (on) or stopped: the puppet is put where they are every tick
    -- instead of walking there (drive)
    skel = function(op)
        local p = PUPPETS[op.key]
        if not p then return nil end
        local on = op.on and true or nil
        if on and not p.skel then
            p.act, p.actQueued, p.pending, p.dest, p.aimFor = nil, nil, nil, nil, nil
            local c = char(p.id)
            if c then pcall(clear_all, p.id) end
        end
        p.skel = on
        return string.format("%s: %s", op.key, on and "set where they are (skeleton)" or "walks again")
    end,
    act_stop = function(op)
        local p = PUPPETS[op.key]
        if not p or not p.act then return nil end
        local c = char(p.id)
        if current_bag(c) == p.act.bag then clear_all(p.id) end
        p.act, p.actQueued = nil, nil
        return op.key .. " stopped"
    end,
    -- A motion with nothing to mirror it with (an emote, a pose): just play it.
    anim = function(op)
        local p = PUPPETS[op.key]
        if not p then return nil end
        local cmd = string.format("motion.play entity_id=%d anim_id=%s", p.id, op.anim)
        if op.pos then cmd = cmd .. string.format(" position=%.2f", op.pos) end
        cli(cmd)
        return nil
    end,
    outfit = function(op)
        local p = PUPPETS[op.key]
        if not p then return nil end
        cli(string.format("appearance.set_outfit_category entity_id=%d category=%s", p.id, op.category))
        return op.key .. " wears " .. op.category
    end,
    face = function(op)                                 -- debug: turn a puppet or any Zoi
        local p = op.key and PUPPETS[op.key]
        local c = char(p and p.id or op.entity)
        if p and op.how then p.faceHow = op.how end
        face(c, op.yaw, op.how)
        return string.format("face %s -> %.0f (now %.0f)", op.how or "tp", op.yaw, c:GetRotation().Yaw)
    end,
    -- Couple actions (hug, kiss, dance, wave): the player's own Zoi does it, on the partner's puppet or alone.
    -- The same engine calls as the puppets' own actions (interaction.execute / motion.play).
    social = function(op)
        local me = tonumber(op.entity) or S.me
        local rid, bag = tostring(op.rid or "?"), tostring(op.bag or "")
        local function done(ok, text)
            report("social", string.format("%s %s %s", rid, ok and "ok" or "fail", tostring(text or ""):sub(1, 160)))
            return string.format("social %s %s: %s", rid, bag, ok and "ok" or ("failed: " .. tostring(text)))
        end
        if not me then return done(false, "no Zoi to act with") end
        if not bag:match("^[%w_:]+$") then return done(false, "bad interaction id") end
        local target
        if op.key then
            local p = PUPPETS[op.key]
            if not p then return done(false, "no puppet for " .. tostring(op.key)) end
            target = p.id
        end
        local anim = bag:match("^anim:([%w_]+)$")
        local ok, msg
        if op.now then      -- a couple action the player just asked for: it starts now, not after what's playing
            pcall(clear_all, me)
        end
        if anim then
            ok, msg = cli(string.format("motion.play entity_id=%d anim_id=%s", me, anim))
        elseif target then
            ok, msg = cli(string.format("interaction.execute entity_id=%d interaction_bag_id=%s target_entity_id=%d insert_front=true",
                me, bag, target))
        else
            ok, msg = cli(string.format("interaction.execute entity_id=%d interaction_bag_id=%s insert_front=true", me, bag))
        end
        return done(ok, msg)
    end,
    -- Moving in together where nobody lives yet: {household, site}. The family moves to that lot (the game's own
    -- residence change), so the house is theirs. Reports "home <household> <site it lives on now>".
    move_home = function(op)
        local hh = em():FindHousehold(eid(op.household))
        if not hh then error("no household " .. tostring(op.household)) end
        local before = hh:GetResidanceSiteId().Value
        if before ~= op.site then hh:ChangeResidanceSite(eid(op.site)) end
        local now = hh:GetResidanceSiteId().Value
        report("home", string.format("%d %d", op.household, now))
        return string.format("household %d: home lot %d -> %d", op.household, before, now)
    end,
    goto_point = function(op)
        local me = tonumber(op.entity) or S.me
        if not me then error("no Zoi to move") end
        teleport(me, op.x, op.y, op.z)
        return string.format("moved %d to %.0f,%.0f", me, op.x, op.y)
    end,
    -- debugging
    eval = function(op)                                 -- {code}: runs a chunk (em, char, cli in scope), logs what it returns
        local f, err = _G.load(op.code, "eval", "t", setmetatable({ em = em, char = char, cli = cli, eid = eid,
                                                                     log = log }, { __index = _G }))
        if not f then error(err) end
        return "eval: " .. tostring(f())
    end,
    eval_b1 = function(op)                              -- {code}: the same, run by the B1 script (out/eval.cfg)
        inzoi.message.send_to_client(S.me, "mp_eval", op.code)
        return nil
    end,
    probe_move = function(op)                           -- {entity, x, y, secs, speed?}: "hold a direction key" for it
        local c = char(op.entity)
        local l = c:GetLocation()
        S.probe = { id = op.entity, x = op.x, y = op.y, untilT = S.clock + (op.secs or 2), speed = op.speed,
                    fx = l.X, fy = l.Y }
        return string.format("probe_move %d from %.0f,%.0f", op.entity, l.X, l.Y)
    end,
    walk = function(op) return "walk " .. tostring(order_walk(char(op.entity), op.x, op.y, op.z, op.bag)) end,
    freeze = function(op) freeze(op.entity); return "froze " .. op.entity end,
    reset = function(op) return "reset " .. tostring(select(2, cli("movement.reset_zoi entity_id=" .. op.entity .. " teleport=false"))) end,
    status = function(op)
        local out = {}
        for key, p in pairs(PUPPETS) do
            local l = char(p.id):GetLocation()
            out[#out + 1] = string.format("%s=%d@%.0f,%.0f trail %d%s", key, p.id, l.X, l.Y, #p.trail, p.hidden and " hidden" or "")
        end
        return "puppets: " .. (#out > 0 and table.concat(out, "; ") or "none")
    end,
}

-- The Zoi the player controls: reports are addressed to it. Re-checked now and then (the player can switch Zois).
function find_me()
    local ok, _, data = cli("entity.get_possessed")
    local me = ok and tonumber(tostring(data):match('"entity_id"%s*:%s*(%d+)'))
    if me and me > 0 then S.me = me end
end

local function remember(id)
    S.done[id] = true
    S.doneOrder[#S.doneOrder + 1] = id
    if #S.doneOrder > 500 then S.done[table.remove(S.doneOrder, 1)] = nil end
end

local function load(name)
    pcall(function() package.loaded[name] = nil end)
    local ok, box = pcall(require, name)
    return ok and type(box) == "table" and box or nil
end

-- A failed spawn isn't remembered: it's tried again while the companion keeps sending it (it drops ops after a few
-- seconds), but at most every SPAWN_RETRY s, not on every poll. S.retry: op id -> {at = clock of the next try, key}.
local SPAWN_RETRY = 2.0

local function run_op(op)
    local f = OPS[op.op]
    local ok, res = pcall(f or function() error("unknown op " .. tostring(op.op)) end, op)
    if ok or op.op ~= "spawn" then
        if op.id then remember(op.id); S.retry[op.id] = nil end
        if ok and op.op == "spawn" then                 -- other failed copies of this spawn are moot now
            for id, r in pairs(S.retry) do
                if r.key == op.key then remember(id); S.retry[id] = nil end
            end
        end
    else
        if op.id then S.retry[op.id] = { at = S.clock + SPAWN_RETRY, key = op.key } end
        -- With its profile here the failure is real (a spawn without its profile yet just waits for MPBulk): tell the
        -- companion, which resets that spawn.
        if S.members[op.key] then report("spawn_failed", tostring(op.key)) end
    end
    if res ~= nil or not ok then
        log(string.format("%s: %s", tostring(op.op), ok and tostring(res) or ("ERR " .. tostring(res))))
    end
end

local function due(op)
    if op.id and S.done[op.id] then return false end
    local r = op.id and S.retry[op.id]
    return not r or S.clock >= r.at
end

-- Nothing new in the mailbox: only the failed spawns whose wait is over.
local function retry_due(box)
    if not box then return end
    local any = false
    for _ in pairs(S.retry) do any = true; break end
    if not any then return end
    for _, op in ipairs(box.ops or {}) do
        if op.id and S.retry[op.id] and due(op) then run_op(op) end
    end
end

-- MPSeq says whether MPCmd changed since we last read it, so an unchanged MPCmd isn't re-read (and recompiled)
-- 10x/s. Without MPSeq (older companion), or if it proves unreliable, MPCmd is read on every poll as before; it's
-- also re-read at least every CMD_EVERY s regardless, which is how a stale MPSeq gets noticed.
local CMD_EVERY = 1.0

local function poll()
    local mark = S.seqMiss < 3 and load(SEQ) or nil
    local mseq = mark and mark.seq
    if mseq ~= nil and mseq == S.seqFile and S.box and S.clock - S.cmdT < CMD_EVERY then
        retry_due(S.box)
        return
    end
    local box = load(CMD)
    if not box then return end
    S.cmdT = S.clock
    if mseq ~= nil and mseq == S.seqFile and box.seq ~= S.seq then
        S.seqMiss = S.seqMiss + 1                       -- MPSeq said "unchanged" but MPCmd had changed
        if S.seqMiss >= 3 then log("MPSeq looks stale: reading MPCmd on every poll") end
    end
    S.box = box
    if mseq ~= nil then S.seqFile = mseq end            -- (MPCmd is written before MPSeq: this box is at least that new)
    if box.seq == S.seq then
        retry_due(box)
        return
    end
    S.seq = box.seq
    if box.bulk and box.bulk ~= S.bulkSeq then
        local bulk = load(BULK)
        if bulk and bulk.seq == box.bulk then
            S.bulkSeq = bulk.seq
            S.members = bulk.members or {}
            for _, r in pairs(S.retry) do r.at = 0 end  -- new profiles: a waiting spawn may work now
        end
    end
    local live = {}
    for _, op in ipairs(box.ops or {}) do
        if op.id then live[op.id] = true end
        if due(op) then run_op(op) end
    end
    for id in pairs(S.retry) do                         -- the companion dropped it: stop waiting on it
        if not live[id] then S.retry[id] = nil end
    end
end

-- ---------------------------------------------------------------- lifecycle
local DRIVE_ERRS, DRIVE_ERR_SPAN = 3, 1.0

function M.on_begin(self)
    require("Objects.B2Entity")
    require("Objects.B2Actor")
    require("Objects.B2Character")
    require("Components.B2CharacterMovementComponent")
    require("Systems.B2AutonomyOptionManager")
    require("Objects.B2Site")
    require("Objects.B2GameObject")
    find_me()
    local box = load(CMD)
    S.seq = box and box.seq or nil          -- whatever is already in the mailbox was meant for a previous session
    local ok, n = pcall(rediscover)
    log("puppet controller up; " .. (ok and (n .. " puppet(s) found from before") or ("rediscover ERR " .. tostring(n))))
end

function M.on_tick(self, dt)
    dt = dt or 0
    S.clock = S.clock + dt
    if S.probe then
        local pr = S.probe
        local okP, errP = pcall(function()
            local c = char(pr.id)
            local mc = c:GetMovementComponent()
            if S.clock < pr.untilT then
                mc.InputVector = UE.FVector(pr.x, pr.y, 0)
                if pr.speed then mc.WalkSpeed = pr.speed end
            else
                mc.InputVector = UE.FVector(0, 0, 0)
                local l = c:GetLocation()
                local v = mc.Velocity
                log(string.format("probe_move %d done: moved %.0f cm (to %.0f,%.0f), walk speed %s", pr.id,
                    dist2(pr.fx, pr.fy, l.X, l.Y), l.X, l.Y, tostring(mc.WalkSpeed)))
                S.probe = nil
            end
        end)
        if not okP then log("probe_move ERR " .. tostring(errP)); S.probe = nil end
    end
    S.meT = (S.meT or 0) + dt
    if S.me and S.meT >= ME_EVERY then
        S.meT = 0
        pcall(function()
            local c = char(S.me)
            if not c then return end
            local l, r = c:GetLocation(), c:GetRotation()
            local site = -1
            pcall(function() site = c:GetLocatedSiteId().Value end)
            report("me", string.format("%.1f %.1f %.1f %.1f %d", l.X, l.Y, l.Z, r.Yaw, site))
        end)
    end
    S.poll = S.poll + dt
    if S.poll >= POLL_EVERY then
        S.poll = 0
        local ok, err = pcall(poll)
        if not ok then log_limited("poll ERR " .. tostring(err)) end
    end
    S.beat = (S.beat or 0) + dt
    if S.beat >= 2 then                                 -- heartbeat: the companion knows the mod is loaded and alive
        S.beat = 0
        pcall(find_me)
        local list = {}
        for key, p in pairs(PUPPETS) do list[#list + 1] = key .. "=" .. p.id end
        report("up", table.concat(list, ","))
    end
    for key, p in pairs(PUPPETS) do
        local ok, err = pcall(drive, key, p, dt)
        if ok then
            p.errs = nil
        else
            -- A puppet whose character is gone (or that fails every tick) is dropped after DRIVE_ERRS errors in a row
            -- over at least DRIVE_ERR_SPAN s (not one bad frame), and reported "despawned" so the companion spawns it
            -- again. (Clearing a field during pairs() is allowed.)
            p.errs = (p.errs or 0) + 1
            p.errT = p.errs == 1 and S.clock or p.errT
            if p.errs >= DRIVE_ERRS and S.clock - p.errT >= DRIVE_ERR_SPAN then
                PUPPETS[key] = nil
                if p.adopted then
                    pcall(freeze, p.id, false)
                else
                    pcall(cli, string.format("zoi.destroy entity_id=%d", p.id))   -- if it's still there: no duplicate
                end
                report("despawned", key)
                log(string.format("%s dropped after %d drive errors: %s", key, p.errs, tostring(err)))
            else
                log_limited(key .. " drive ERR " .. tostring(err))
            end
        end
    end
end

return M
