-- inZOI Multiplayer - B1 (client) boot script. Ticks while the simulation is paused (build mode) too.
--
-- 1. The companion's client-side commands: <mod>/in/b1cmd.cfg = "<10-digit hex id><code as hex>" (read with
--    uimod.cfg_load, which answers JSON: the game's mod check refuses requiring modules it doesn't ship; hex keeps the
--    code clear of JSON escaping); each new id runs once (with `owner`, a family Zoi actor of this world, for widget
--    lookups) and its result goes to out/b1.cfg.
--
-- 2. Skeleton mirroring: what one PC shows of a player's Zoi is exactly what the other PC computed, bone by bone.
--    Sending: POSE_EVERY s, this player's own Zoi (the possessed one): its actor's place and facing and every bone of
--    its body and head meshes (component space: position x10, rotation quaternion x10000, as integers) go to
--      out/posek.cfg  a keyframe "kid|x,y,z,yaw|nbody|nhead|v,v,v,..." (7 values a bone) every KEY_EVERY s
--      out/posed.cfg  "kid|seq|x,y,z,yaw|i,v,v,v,v,v,v,v,i,..." only the bones that differ from keyframe kid
--    (a frame lost on the way doesn't matter: each one is complete against its keyframe).
--    Receiving: in/posemap.cfg "pid=entity;..." says which Zoi here is whose; in/posed_<pid>.cfg and
--    in/posek_<pid>.cfg are their frames. That Zoi gets two poseable meshes (body and head: the same meshes and
--    materials), its clothes, hair and accessories follow them, the game's own meshes are hidden, and every frame
--    received is applied to the bones as it is; the actor glides to their place. No new frame for STALE s: the Zoi
--    is given back to the game as it was.
local M = {}
local MOD = "InZOIMultiplayer"
local LAST, EVERY, t = nil, 0.25, 0
local POSE_EVERY, KEY_EVERY, STALE, MAP_EVERY, CHECK_EVERY = 1 / 15, 1.0, 1.5, 1.0, 0.5
local LEADERS = { "CharacterMesh0", "Head" }
local HEAD_FOLLOWERS = { Hair = true, FaceHair = true, Necklace = true, Earring = true, Hat = true, Eyewear = true }

local function result(text)
    inzoi.cli.execute(string.format('uimod.cfg_save mod_id=%s section=out key=b1 value="%s"', MOD,
        (tostring(text):gsub('"', "'"))))
end

local function cfg_load(section, key)
    local called, ok, _, data = pcall(inzoi.cli.execute,
        "uimod.cfg_load mod_id=" .. MOD .. " section=" .. section .. " key=" .. key)
    if not (called and ok) then return nil end
    return tostring(data):match('"value"%s*:%s*"([^"]*)"')
end

local function cfg_save(key, value)
    pcall(inzoi.cli.execute, string.format('uimod.cfg_save mod_id=%s section=out key=%s value="%s"', MOD, key, value))
end

local function read_box()
    local v = cfg_load("in", "b1cmd")
    v = v and v:match("^(%x+)")
    if not v or #v < 10 then return nil end
    local id, hex = v:sub(1, 10), v:sub(11)
    return id, (hex:gsub("%x%x", function(h) return string.char(tonumber(h, 16)) end))
end

-- ------------------------------------------------------------------ skeleton mirroring
local clock = 0
local actors, actorsAt = {}, -10

local function actor_of(entity)
    if clock - actorsAt > 2.0 or (not actors[entity] and clock - actorsAt > 0.5) then
        actorsAt = clock
        actors = {}
        local own = _G.MP_B1_OWNER
        if not own then return nil end
        local arr = UE.TArray(UE.AActor)
        UE.UGameplayStatics.GetAllActorsOfClass(own, own:GetClass(), arr)
        for i = 1, arr:Length() do
            local a = arr:Get(i)
            local ok, id = pcall(function() return a.EntityId.Value end)
            if ok and id then actors[id] = a end
        end
    end
    return actors[entity]
end

local function meshes(actor)
    local out = {}
    local list = actor:K2_GetComponentsByClass(UE.USkinnedMeshComponent)
    for i = 1, list:Length() do
        local c = list:Get(i)
        out[c:GetName()] = c
    end
    return out
end

local function round(v) return v >= 0 and math.floor(v + 0.5) or -math.floor(-v + 0.5) end

-- sending ----------------------------------------------------------
local me, meAt = nil, -10
local send = { kid = 0, seq = 0, key = nil, keyAt = -10, at = 0 }

-- (in/me.cfg, written by the companion: asking the game for the possessed Zoi from here hangs this script)
local function my_entity()
    if clock - meAt > 2.0 then
        meAt = clock
        local id = tonumber(cfg_load("in", "me") or "")
        me = id and id > 0 and id or nil
    end
    return me
end

local function read_pose(actor)
    local by = meshes(actor)
    local vals, counts, scales = {}, {}, {}
    for li, name in ipairs(LEADERS) do
        local m = by[name]
        local n = m and m:GetNumBones() or 0
        counts[li] = n
        for b = 0, n - 1 do
            local tr = m:GetSocketTransform(m:GetBoneName(b), 2)          -- (component space)
            local p, q = tr.Translation, tr.Rotation
            local k = #vals
            vals[k + 1], vals[k + 2], vals[k + 3] = round(p.X * 10), round(p.Y * 10), round(p.Z * 10)
            vals[k + 4], vals[k + 5] = round(q.X * 10000), round(q.Y * 10000)
            vals[k + 6], vals[k + 7] = round(q.Z * 10000), round(q.W * 10000)
            -- (the bone's size: the Zoi's proportions - body shape, age - are bone scales, 153 of 370 body bones;
            -- left at 1 the mirrored skull and body came out bigger than the hair made for them: "bald", hair behind)
            local s, j = tr.Scale3D, #scales
            scales[j + 1], scales[j + 2], scales[j + 3] = round(s.X * 1000), round(s.Y * 1000), round(s.Z * 1000)
        end
    end
    local l, r = actor:K2_GetActorLocation(), actor:K2_GetActorRotation()
    return vals, counts, string.format("%d,%d,%d,%d", round(l.X * 10), round(l.Y * 10), round(l.Z * 10), round(r.Yaw * 100)),
        scales
end

local function send_pose()
    local id = my_entity()
    local actor = id and actor_of(id)
    if not actor then return end
    local vals, counts, head, scales = read_pose(actor)
    send.seq = send.seq + 1
    local key = send.key
    if not key or clock - send.keyAt >= KEY_EVERY or key.n1 ~= counts[1] or key.n2 ~= counts[2] then
        send.kid = send.kid % 100000 + 1
        send.key, send.keyAt = { vals = vals, n1 = counts[1], n2 = counts[2] }, clock
        -- (the bones' sizes after their place and turn: an older receiver reads 7 values a bone and ignores the rest)
        cfg_save("posek", string.format("%d|%s|%d|%d|%s,%s", send.kid, head, counts[1], counts[2], table.concat(vals, ","),
            table.concat(scales, ",")))
        cfg_save("posed", string.format("%d|%d|%s|", send.kid, send.seq, head))
        return
    end
    local kv, parts = key.vals, {}
    for b = 0, #vals / 7 - 1 do
        local o = b * 7
        for j = 1, 7 do
            if math.abs(vals[o + j] - kv[o + j]) >= 2 then
                parts[#parts + 1] = string.format("%d,%d,%d,%d,%d,%d,%d,%d", b, vals[o + 1], vals[o + 2], vals[o + 3],
                    vals[o + 4], vals[o + 5], vals[o + 6], vals[o + 7])
                break
            end
        end
    end
    cfg_save("posed", string.format("%d|%d|%s|%s", send.kid, send.seq, head, table.concat(parts, ",")))
end

-- receiving --------------------------------------------------------
local remotes = {}          -- pid -> {e, kid, key = {vals, n1, n2}, seq, at, rig, target = {x,y,z,yaw}}
local mapAt = -10

local function split_ints(s)
    local out = {}
    for v in s:gmatch("-?%d+") do out[#out + 1] = tonumber(v) end
    return out
end

local restore              -- (defined below clean_actor, which it uses)

local function asset_name(c)
    local ok, a = pcall(function() return c:GetSkinnedAsset() end)
    return ok and a and a:GetName() or ""
end

-- A Zoi given back to the game whole: no poseable mesh left on it (they're only ever this script's: one left by an
-- earlier run of it - the Lua reloaded, the mod remounted - would stay there frozen in its last pose, a second body),
-- its clothes following its own body and head again, those shown.
local function clean_actor(actor)
    local by = meshes(actor)
    local own = { by[LEADERS[1]], by[LEADERS[2]] }
    for name, c in pairs(by) do
        if name ~= LEADERS[1] and name ~= LEADERS[2] and c:GetClass():GetName() ~= "PoseableMeshComponent" then
            local ok, leader = pcall(function() return c.LeaderPoseComponent end)
            if ok and leader and leader:GetClass():GetName() == "PoseableMeshComponent" then
                local li = HEAD_FOLLOWERS[name] and 2 or 1
                pcall(function() c:SetLeaderPoseComponent(own[li], true, false) end)
            end
        end
    end
    local n = 0
    for _, c in pairs(by) do
        if c:GetClass():GetName() == "PoseableMeshComponent" then
            pcall(function() c:K2_DestroyComponent(c) end)
            n = n + 1
        end
    end
    for _, L in pairs(own) do pcall(function() L:SetVisibility(true, false) end) end
    return n
end

-- The Zoi given back to the game. Every step on its own: one that fails (a mesh the game just replaced - a new look, new
-- clothes) mustn't leave the rest undone - that left the frozen poseable head on screen with the hair following the
-- game's own hidden head (bald, the hair floating where the game's own pose had it). Then whatever is still left of the
-- rig on the actor goes too (clean_actor).
restore = function(r)
    local rig = r.rig
    r.rig = nil
    if not rig then return end
    for _, c in pairs(rig.followers) do pcall(function() c:SetLeaderPoseComponent(rig.orig[c] or nil, true, false) end) end
    for _, L in pairs(rig.leaders) do pcall(function() L:SetVisibility(true, false) end) end
    for _, pm in pairs(rig.poseables) do pcall(function() pm:K2_DestroyComponent(pm) end) end
    pcall(clean_actor, rig.actor)
end

-- Once per run of this script: what an earlier run left on any Zoi goes (clean_actor).
local swept = false
local function sweep()
    swept = true
    local own = _G.MP_B1_OWNER
    if not own then swept = false return end
    local arr = UE.TArray(UE.AActor)
    UE.UGameplayStatics.GetAllActorsOfClass(own, own:GetClass(), arr)
    local n = 0
    for i = 1, arr:Length() do
        local a = arr:Get(i)
        if a:K2_GetComponentsByClass(UE.UPoseableMeshComponent):Length() > 0 then n = n + clean_actor(a) end
    end
    if n > 0 then result("pose-swept " .. n .. " poseable mesh(es) left by an earlier run") end
end

local function build_rig(actor)
    clean_actor(actor)                                  -- (nothing of an earlier rig stays on it)
    local by = meshes(actor)
    local rig = { actor = actor, leaders = {}, poseables = {}, followers = {}, orig = {}, names = {}, assets = {} }
    for li, name in ipairs(LEADERS) do
        local L = by[name]
        if not L then return nil end
        local pm = actor:AddComponentByClass(UE.UPoseableMeshComponent, true, UE.FTransform(), false)
        pm:SetSkinnedAssetAndUpdate(L:GetSkinnedAsset(), true)
        pm:K2_AttachToComponent(L, "", 0, 0, 0, false)
        pm:K2_SetRelativeTransform(UE.FTransform(), false, nil, false)
        for i = 0, L:GetNumMaterials() - 1 do pm:SetMaterial(i, L:GetMaterial(i)) end
        rig.leaders[li], rig.poseables[li], rig.assets[li] = L, pm, asset_name(L)
        local names = {}
        for b = 0, pm:GetNumBones() - 1 do names[b] = pm:GetBoneName(b) end
        rig.names[li] = names
    end
    for name, c in pairs(by) do
        if name ~= LEADERS[1] and name ~= LEADERS[2] and c:GetClass():GetName() ~= "PoseableMeshComponent" then
            local ok, leader = pcall(function() return c.LeaderPoseComponent end)
            local li = (ok and leader == rig.leaders[2] or HEAD_FOLLOWERS[name]) and 2 or 1
            rig.orig[c] = ok and leader or nil
            c:SetLeaderPoseComponent(rig.poseables[li], true, false)
            rig.followers[name] = c
        end
    end
    for _, L in pairs(rig.leaders) do L:SetVisibility(false, false) end
    return rig
end

-- The game changes a Zoi's clothes by itself too (sleepwear at bedtime, swimwear...): it builds new body and head
-- meshes (the body without what the clothes cover) and may point clothes back at its own (hidden) body. Looked at
-- every CHECK_EVERY s: new body or head meshes -> false (the rig is built again on them); anything else following
-- the game's own meshes follows the rig again, and those stay hidden.
local function check_rig(rig)
    local by = meshes(rig.actor)
    for li, name in ipairs(LEADERS) do
        local L = by[name]
        if not L or L ~= rig.leaders[li] or asset_name(L) ~= rig.assets[li] then return false end
        if L:IsVisible() then L:SetVisibility(false, false) end
    end
    for name, c in pairs(by) do
        if name ~= LEADERS[1] and name ~= LEADERS[2] and c:GetClass():GetName() ~= "PoseableMeshComponent" then
            local ok, leader = pcall(function() return c.LeaderPoseComponent end)
            if not ok or (leader ~= rig.poseables[1] and leader ~= rig.poseables[2]) then
                local li = (ok and leader == rig.leaders[2] or HEAD_FOLLOWERS[name]) and 2 or 1
                if rig.followers[name] ~= c then rig.orig[c] = ok and leader or nil end
                c:SetLeaderPoseComponent(rig.poseables[li], true, false)
                rig.followers[name] = c
            end
        end
    end
    return true
end

local function apply(r, vals, changed)
    local rig = r.rig
    local sc = r.key.scales
    local base = 0
    for li = 1, 2 do
        local n = li == 1 and r.key.n1 or r.key.n2
        local pm, names = rig.poseables[li], rig.names[li]
        if pm:GetNumBones() == n then
            -- EBoneSpaces.ComponentSpace (= 1 in Unreal). By number: the game's mod check refuses that enum by name
            -- ("not in ue_global_table.json") and then won't load the mod when the game starts.
            local space = 1
            for b = 0, n - 1 do
                if not changed or changed[base + b] then
                    local o, so = (base + b) * 7, (base + b) * 3
                    local size = sc and UE.FVector(sc[so + 1] / 1000, sc[so + 2] / 1000, sc[so + 3] / 1000) or UE.FVector(1, 1, 1)
                    pm:SetBoneTransformByName(names[b], UE.FTransform(
                        UE.FQuat(vals[o + 4] / 10000, vals[o + 5] / 10000, vals[o + 6] / 10000, vals[o + 7] / 10000),
                        UE.FVector(vals[o + 1] / 10, vals[o + 2] / 10, vals[o + 3] / 10), size), space)
                end
            end
        end
        base = base + n
    end
end

local function read_map()
    local v = cfg_load("in", "posemap") or ""
    local seen = {}
    for pid, e, ver in v:gmatch("([%w_%-]+)=(%d+)%.(%d+)") do
        seen[pid] = true
        local r = remotes[pid]
        if not r or r.e ~= tonumber(e) or r.ver ~= ver then           -- (another Zoi, or new clothes: a new rig)
            if r then restore(r) end
            remotes[pid] = { e = tonumber(e), ver = ver, seq = -1, at = -10 }
        end
    end
    for pid, r in pairs(remotes) do
        if not seen[pid] then restore(r); remotes[pid] = nil end
    end
end

local function receive(pid, r)
    local d = cfg_load("in", "posed_" .. pid)
    if not d then return end
    local kid, seq, head, rest = d:match("^(%d+)|(%d+)|([%-%d,]+)|(.*)$")
    if not kid or tonumber(seq) == r.seq then return end
    kid, seq = tonumber(kid), tonumber(seq)
    if r.kid ~= kid then
        local k = cfg_load("in", "posek_" .. pid)
        local kk, khead, n1, n2, kv = (k or ""):match("^(%d+)|([%-%d,]+)|(%d+)|(%d+)|(.*)$")
        if not kk or tonumber(kk) ~= kid then return end              -- (its keyframe isn't here yet)
        local all, nb = split_ints(kv), tonumber(n1) + tonumber(n2)
        local vals, scales = all, nil
        if #all >= nb * 10 then                                     -- (sizes sent: 3 a bone after the 7)
            vals, scales = {}, {}
            for i = 1, nb * 7 do vals[i] = all[i] end
            for i = 1, nb * 3 do scales[i] = all[nb * 7 + i] end
        end
        r.kid, r.key = kid, { vals = vals, scales = scales, n1 = tonumber(n1), n2 = tonumber(n2) }
        r.fresh = true
    end
    r.seq, r.at = seq, clock
    local h = split_ints(head)
    r.target = { x = h[1] / 10, y = h[2] / 10, z = h[3] / 10, yaw = h[4] / 100 }
    local actor = actor_of(r.e)
    if not actor then return end
    if r.rig and r.rig.actor == actor and clock - (r.checkAt or 0) >= CHECK_EVERY then
        r.checkAt = clock
        local ok, good = pcall(check_rig, r.rig)
        if not ok or not good then restore(r) end           -- (new clothes: built again just below)
    end
    if not r.rig or r.rig.actor ~= actor then
        restore(r)
        local ok, rig = pcall(build_rig, actor)
        if not ok or not rig then return end
        r.rig, r.fresh, r.checkAt = rig, true, clock
    end
    -- this frame = keyframe + its differences (bones not listed are as in the keyframe)
    local vals = r.key.vals
    local diff = split_ints(rest)
    local cur, changed = r.cur, {}
    if r.fresh or not cur then                                       -- (all bones from the keyframe)
        cur = {}
        for i = 1, #vals do cur[i] = vals[i] end
        r.cur, r.fresh, changed, r.lastDiff = cur, false, nil, {}
    else
        for b in pairs(r.lastDiff or {}) do                         -- (moved last time, not now: as in the keyframe)
            local o = b * 7
            for j = 1, 7 do cur[o + j] = vals[o + j] end
            changed[b] = true
        end
    end
    local now = {}
    for i = 1, #diff - 7, 8 do
        local b = diff[i]
        local o = b * 7
        for j = 1, 7 do cur[o + j] = diff[i + j] end
        if changed then changed[b] = true end
        now[b] = true
    end
    r.lastDiff = now
    apply(r, cur, changed)
end

-- The drawn actor goes where they're drawn, smoothly (place and facing); far off (a jump, another floor), at once.
-- An actor the game let go (a loading screen, the Zoi spawned again) gives its rig back, and it's built on the new one.
local function glide(dt)
    for _, r in pairs(remotes) do
        local tg, rig = r.target, r.rig
        if tg and rig then
            local ok = pcall(function()
                local a = rig.actor
                local l, yaw = a:K2_GetActorLocation(), a:K2_GetActorRotation().Yaw
                local k, kr = math.min(1, dt * 12), math.min(1, dt * 14)
                local nx, ny, nz = l.X + (tg.x - l.X) * k, l.Y + (tg.y - l.Y) * k, l.Z + (tg.z - l.Z) * k
                local turn = ((tg.yaw - yaw + 540) % 360) - 180
                local nyaw = yaw + turn * kr
                if (tg.x - l.X) ^ 2 + (tg.y - l.Y) ^ 2 > 300 * 300 or math.abs(tg.z - l.Z) > 150 then
                    nx, ny, nz, nyaw = tg.x, tg.y, tg.z, tg.yaw
                end
                a:K2_SetActorLocationAndRotation(UE.FVector(nx, ny, nz), UE.FRotator(0, nyaw, 0), false, nil, false)
            end)
            if not ok then restore(r) end
        end
    end
end

local poseT = 0
local function pose_tick(dt)
    if not swept then sweep() end
    poseT = poseT + dt
    if clock - mapAt > MAP_EVERY then
        mapAt = clock
        read_map()
    end
    if poseT >= POSE_EVERY then
        poseT = 0
        send_pose()
        for pid, r in pairs(remotes) do
            local ok, err = pcall(receive, pid, r)
            if not ok then result("pose-error " .. tostring(err)) end
            if r.rig and clock - r.at > STALE then restore(r) end
        end
    end
    glide(dt)    -- (the drawn actor: B2 sets only the simulation where they are ("skel"), which doesn't move it)
end

function M.on_begin() end

function M.on_tick(dt)
    dt = tonumber(dt) or 0.03
    clock = clock + dt
    if _G.MP_TICK then                                  -- (per-frame work installed by a command)
        local ok, err = pcall(_G.MP_TICK, dt)
        if not ok then _G.MP_TICK = nil; result("tick-error " .. tostring(err)) end
    end
    if _G.MP_B1_OWNER then
        local ok, err = pcall(pose_tick, dt)
        if not ok then result("pose-error " .. tostring(err)) end
    end
    t = t + dt
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

function M.on_end()
    for _, r in pairs(remotes) do restore(r) end
end

return M
