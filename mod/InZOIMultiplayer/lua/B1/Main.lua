-- inZOI Multiplayer - B1 (client) boot script. Ticks while the simulation is paused (build mode) too.
--
-- 1. The companion's client-side commands: <mod>/in/b1cmd.cfg = "<10-digit hex id><code as hex>" (read with
--    uimod.cfg_load, which answers JSON: the game's mod check refuses requiring modules it doesn't ship; hex keeps the
--    code clear of JSON escaping); each new id runs once (with `owner`, a family Zoi actor of this world, for widget
--    lookups) and its result goes to out/b1.cfg.
--
-- 2. Skeleton mirroring: what one PC shows of a player's Zoi is exactly what the other PC computed, bone by bone.
--    Sending: this player's own Zoi (the possessed one): its actor's place and facing and the bones of its body (every
--    POSE_EVERY s: 60 a second) and head (the face, neck and shoulders: every FACE_EVERY s, also 60 a second) meshes (component space: position
--    x10, rotation quaternion x10000, as integers) go to
--      out/posek.cfg  a keyframe "kid|x,y,z,yaw|nbody|nhead|v,v,v,...,s,s,s,..." (7 values a bone, then 3 sizes a
--                     bone) every KEY_EVERY s
--      out/posed.cfg  "kid|seq|x,y,z,yaw|P|i,v,v,v,v,v,v,v,i,..." the bones that differ from keyframe kid: P = A, all
--                     of them (body and face read), or B, the body's only (the face as in the last A frame)
--    (a frame lost on the way doesn't matter: each one is complete against its keyframe, the face against the last A).
--    Receiving: in/posemap.cfg "pid=entity;..." says which Zoi here is whose; in/posed_<pid>.cfg and
--    in/posek_<pid>.cfg are their frames. That Zoi gets two poseable meshes (body and head) that are never drawn and
--    hold the bones received, as received; every mesh of the Zoi that is drawn - its own body and head, hair, clothes,
--    accessories, and so what's attached to them - follows only those (one reference: the skeleton received). The
--    actor glides to their place. No new frame for STALE s: the Zoi is given back to the game as it was.
local M = {}
local MOD = "InZOIMultiplayer"
local LAST, EVERY, t = nil, 0.25, 0
-- (60 a second, body and head, since 0.9.67 - the players' choice; the head at the body's pace since 0.9.66: at 15
-- the head mesh's neck and shoulders moved at half the body's - out of step. A game drawing fewer frames than that
-- sends one a frame. B frames - the body only - stay for a FACE_EVERY above POSE_EVERY.)
local POSE_EVERY, FACE_EVERY, KEY_EVERY, STALE, MAP_EVERY, CHECK_EVERY = 1 / 60, 1 / 60, 1.0, 1.5, 1.0, 0.5
local SLACK = 0.002          -- s early a frame may go: a 60 fps frame isn't exactly 1/60 s (not every other one)
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

local function asset_name(c)
    local ok, a = pcall(function() return c:GetSkinnedAsset() end)
    return ok and a and a:GetName() or ""
end

-- sending ----------------------------------------------------------
local me, meAt = nil, -10
local send = { kid = 0, seq = 0, key = nil, keyAt = -10, faceAt = -10, cache = {}, vals = nil }

-- (in/me.cfg, written by the companion: asking the game for the possessed Zoi from here hangs this script)
local function my_entity()
    if clock - meAt > 2.0 then
        meAt = clock
        local id = tonumber(cfg_load("in", "me") or "")
        me = id and id > 0 and id or nil
    end
    return me
end

-- One mesh's bones into vals from bone o on (and their sizes into scales, for a keyframe). Bone names are looked up once
-- a mesh (send.cache): looking them up every time, and reading every size, was more than half the cost of a frame
-- (1260 bones: 6-8 ms).
local function read_mesh(c, vals, o, scales)
    local m, names = c.mesh, c.names
    for b = 0, c.n - 1 do
        local tr = m:GetSocketTransform(names[b], 2)                  -- (component space)
        local p, q = tr.Translation, tr.Rotation
        local k = (o + b) * 7
        vals[k + 1], vals[k + 2], vals[k + 3] = round(p.X * 10), round(p.Y * 10), round(p.Z * 10)
        vals[k + 4], vals[k + 5] = round(q.X * 10000), round(q.Y * 10000)
        vals[k + 6], vals[k + 7] = round(q.Z * 10000), round(q.W * 10000)
        if scales then
            -- (the bone's size: the Zoi's proportions - body shape, age - are bone scales, 153 of 370 body bones;
            -- left at 1 the mirrored skull and body came out bigger than the hair made for them: "bald", hair behind)
            local sz, j = tr.Scale3D, (o + b) * 3
            scales[j + 1], scales[j + 2], scales[j + 3] = round(sz.X * 1000), round(sz.Y * 1000), round(sz.Z * 1000)
        end
    end
end

local function send_pose()
    local id = my_entity()
    local actor = id and actor_of(id)
    if not actor then return end
    local by, fresh = meshes(actor), false
    for li, name in ipairs(LEADERS) do
        local m = by[name]
        if not m then return end
        local c = send.cache[li]
        if not c or c.mesh ~= m or c.asset ~= asset_name(m) then          -- (new body or head: new clothes, a reload)
            c = { mesh = m, asset = asset_name(m), n = m:GetNumBones(), names = {} }
            for b = 0, c.n - 1 do c.names[b] = m:GetBoneName(b) end
            send.cache[li], fresh = c, true
        end
    end
    local c1, c2 = send.cache[1], send.cache[2]
    if fresh or not send.vals then send.vals = {} end
    local vals, key = send.vals, send.key
    local keyframe = fresh or not key or clock - send.keyAt >= KEY_EVERY or key.n1 ~= c1.n or key.n2 ~= c2.n
    local face = keyframe or clock - send.faceAt >= FACE_EVERY - 0.008
    local scales = keyframe and {} or nil
    read_mesh(c1, vals, 0, scales)
    if face then
        read_mesh(c2, vals, c1.n, scales)
        send.faceAt = clock
    end
    local l, r = actor:K2_GetActorLocation(), actor:K2_GetActorRotation()
    local head = string.format("%d,%d,%d,%d", round(l.X * 10), round(l.Y * 10), round(l.Z * 10), round(r.Yaw * 100))
    send.seq = send.seq + 1
    if keyframe then
        send.kid = send.kid % 100000 + 1
        send.key, send.keyAt = { vals = table.move(vals, 1, #vals, 1, {}), n1 = c1.n, n2 = c2.n }, clock
        -- (the bones' sizes after their place and turn: an older receiver reads 7 values a bone and ignores the rest)
        cfg_save("posek", string.format("%d|%s|%d|%d|%s,%s", send.kid, head, c1.n, c2.n, table.concat(vals, ","),
            table.concat(scales, ",")))
        cfg_save("posed", string.format("%d|%d|%s|A|", send.kid, send.seq, head))
        return
    end
    local kv, parts = key.vals, {}
    for b = 0, (face and c1.n + c2.n or c1.n) - 1 do
        local o = b * 7
        for j = 1, 7 do
            if math.abs(vals[o + j] - kv[o + j]) >= 2 then
                parts[#parts + 1] = string.format("%d,%d,%d,%d,%d,%d,%d,%d", b, vals[o + 1], vals[o + 2], vals[o + 3],
                    vals[o + 4], vals[o + 5], vals[o + 6], vals[o + 7])
                break
            end
        end
    end
    cfg_save("posed", string.format("%d|%d|%s|%s|%s", send.kid, send.seq, head, face and "A" or "B",
        table.concat(parts, ",")))
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

-- One reference only: the skeleton received. Each mirrored Zoi gets two poseable meshes (body and head) that are never
-- drawn - they only hold the bones received, as received - and every mesh of the Zoi that is drawn follows them: its
-- own body and head, its hair, beard, clothes and accessories (leader pose). Nothing drawn follows the game's own
-- animation any more, and the Zoi's own meshes keep what only they have - the face's shape (morph targets: drawn on a
-- copy of the head without them the scalp came through the hair, "bald" with a tuft at one side). What hangs on the
-- Zoi's own body or head (glasses at a socket of the head) hangs on the rig's instead, at the same socket.
local function poseable(c) return c:GetClass():GetName() == "PoseableMeshComponent" end

-- which of the two a mesh follows: the head (and what hangs on it: hair, beard, glasses...) or the body
local function side(name, leader)
    if name == LEADERS[1] then return 1 end
    if name == LEADERS[2] then return 2 end
    local ln = leader and leader:GetName()
    if ln == LEADERS[2] then return 2 end
    if ln == LEADERS[1] then return 1 end
    return HEAD_FOLLOWERS[name] and 2 or 1
end

local function socket_of(c)
    local s = tostring(c:GetAttachSocketName())
    return s ~= "None" and s or ""
end

-- What hangs on a mesh that isn't itself one of the Zoi's skinned meshes (glasses at the head's socket, footprints at
-- the feet, what's held in a hand). A Zoi's own mesh following the rig is drawn right, but what hangs on it isn't moved
-- when only the bones change: the glasses stayed where the game's own pose had put them (47 cm off the face, then 18).
local function props_of(m, by)
    local out = {}
    local ok, n = pcall(function() return m:GetNumChildrenComponents() end)
    if not ok or not n then return out end
    for j = 0, n - 1 do
        local ch = m:GetChildComponent(j)
        if ch then
            local nm = ch:GetName()
            if not (by[nm] and by[nm] == ch) then out[#out + 1] = ch end
        end
    end
    return out
end

-- A Zoi given back to the game whole: no poseable mesh left on it (they're only ever this script's: one left by an
-- earlier run of it - the Lua reloaded, the mod remounted - would hold the Zoi frozen in its last pose), its body and
-- head moved by the game again, everything else following those. Body and head first (a mesh following one that itself
-- follows another doesn't move).
local function clean_actor(actor)
    local by = meshes(actor)
    local own = { by[LEADERS[1]], by[LEADERS[2]] }
    local n = 0
    for _, c in pairs(by) do if poseable(c) then n = n + 1 end end
    if n == 0 then return 0 end
    for pass = 1, 2 do
        for name, c in pairs(by) do
            local isLeader = name == LEADERS[1] or name == LEADERS[2]
            if not poseable(c) and ((pass == 1) == isLeader) then
                local ok, leader = pcall(function() return c.LeaderPoseComponent end)
                if ok and leader and poseable(leader) then
                    pcall(function()
                        local li = side(name, leader:GetAttachParent())   -- (a rig's head is on the Zoi's own head)
                        c:SetLeaderPoseComponent(not isLeader and own[li] or nil, true, false)
                    end)
                end
            end
        end
    end
    for _, c in pairs(by) do
        if poseable(c) then
            pcall(function()                            -- (what hangs on it: back where the poseable itself hangs)
                local parent = c:GetAttachParent()
                for _, ch in ipairs(props_of(c, by)) do ch:K2_AttachToComponent(parent, socket_of(ch), 0, 0, 0, false) end
            end)
            pcall(function() c:K2_DestroyComponent(c) end)
        end
    end
    -- (rigs of 0.9.56 and older hid the Zoi's own body and head: shown again)
    for _, L in pairs(own) do pcall(function() if not L:IsVisible() then L:SetVisibility(true, false) end end) end
    return n
end

-- The Zoi given back to the game. Every step on its own: one that fails (a mesh the game just replaced - a new look, new
-- clothes) mustn't leave the rest undone. Body and head first, then what followed them; then whatever is still left of
-- the rig on the actor goes too (clean_actor).
restore = function(r)
    local rig = r.rig
    r.rig = nil
    if not rig then return end
    local by = {}
    pcall(function() by = meshes(rig.actor) end)
    for ch, at in pairs(rig.props) do                   -- (what hung on the rig: back on the Zoi's own body/head)
        pcall(function()
            local p = ch:GetAttachParent()
            if p ~= rig.poseables[1] and p ~= rig.poseables[2] then return end   -- (taken off by the game meanwhile)
            local home = rig.leaders[at.li]
            if not pcall(function() ch:K2_AttachToComponent(home, at.sock, 0, 0, 0, false) end) then
                ch:K2_AttachToComponent(by[LEADERS[at.li]], at.sock, 0, 0, 0, false)
            end
        end)
    end
    for pass = 1, 2 do
        for name, c in pairs(rig.followers) do
            local isLeader = name == LEADERS[1] or name == LEADERS[2]
            if (pass == 1) == isLeader then
                pcall(function() c:SetLeaderPoseComponent(rig.orig[c] or nil, true, false) end)
            end
        end
    end
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

-- a mesh of the Zoi made to follow the rig (its own leader remembered for giving it back)
local function follow(rig, name, c)
    local ok, leader = pcall(function() return c.LeaderPoseComponent end)
    leader = ok and leader or nil
    if leader == rig.poseables[1] or leader == rig.poseables[2] then return end
    if rig.followers[name] ~= c then rig.orig[c] = leader end
    c:SetLeaderPoseComponent(rig.poseables[side(name, leader)], true, false)
    rig.followers[name] = c
end

-- what hangs on the Zoi's own body or head goes onto the rig's, at the same socket (rig.props: where it came from)
local function move_props(rig, by)
    for li = 1, 2 do
        for _, ch in ipairs(props_of(rig.leaders[li], by)) do
            local sock = socket_of(ch)
            if pcall(function() ch:K2_AttachToComponent(rig.poseables[li], sock, 0, 0, 0, false) end) then
                rig.props[ch] = { li = li, sock = sock }
            end
        end
    end
end

local function build_rig(actor)
    clean_actor(actor)                                  -- (nothing of an earlier rig stays on it)
    local by = meshes(actor)
    local rig = { actor = actor, leaders = {}, poseables = {}, followers = {}, orig = {}, names = {}, assets = {},
                  props = {}, parents = {} }
    for li, name in ipairs(LEADERS) do
        local L = by[name]
        if not L then return nil end
        local pm = actor:AddComponentByClass(UE.UPoseableMeshComponent, true, UE.FTransform(), false)
        pm:SetSkinnedAssetAndUpdate(L:GetSkinnedAsset(), true)
        pm:K2_AttachToComponent(L, "", 0, 0, 0, false)
        pm:K2_SetRelativeTransform(UE.FTransform(), false, nil, false)
        pm:SetVisibility(false, false)                  -- (only the bones received: never drawn itself)
        rig.leaders[li], rig.poseables[li], rig.assets[li] = L, pm, asset_name(L)
        local names, at, parents = {}, {}, {}
        for b = 0, pm:GetNumBones() - 1 do names[b] = pm:GetBoneName(b); at[tostring(names[b])] = b end
        for b = 0, pm:GetNumBones() - 1 do                           -- (each bone's parent: apply)
            local ok, pn = pcall(pm.GetParentBone, pm, names[b])
            parents[b] = ok and pn and at[tostring(pn)] or nil
        end
        rig.names[li], rig.parents[li] = names, parents
    end
    -- what follows the body or the head first (they then stop being anyone's leader), body and head last
    for name, c in pairs(by) do
        if name ~= LEADERS[1] and name ~= LEADERS[2] and not poseable(c) then follow(rig, name, c) end
    end
    for li, name in ipairs(LEADERS) do follow(rig, name, rig.leaders[li]) end
    move_props(rig, meshes(actor))
    return rig
end

-- The game changes a Zoi's clothes by itself too (sleepwear at bedtime, swimwear...): it builds new body and head
-- meshes (the body without what the clothes cover) and may point clothes back at its own body. Looked at every
-- CHECK_EVERY s: new body or head meshes -> false (the rig is built again on them); any other mesh not following the
-- rig follows it again.
local function check_rig(rig)
    local by = meshes(rig.actor)
    for li, name in ipairs(LEADERS) do
        local L = by[name]
        if not L or L ~= rig.leaders[li] or asset_name(L) ~= rig.assets[li] then return false end
    end
    for name, c in pairs(by) do
        if name ~= LEADERS[1] and name ~= LEADERS[2] and not poseable(c) then follow(rig, name, c) end
    end
    for li, name in ipairs(LEADERS) do follow(rig, name, rig.leaders[li]) end
    move_props(rig, by)                                 -- (put on since: new glasses, something taken in hand)
    for _, pm in pairs(rig.poseables) do if pm:IsVisible() then pm:SetVisibility(false, false) end end
    return true
end

local function apply(r, vals, changed)
    local rig = r.rig
    local sc = r.key.scales
    local base = 0
    for li = 1, 2 do
        local n = li == 1 and r.key.n1 or r.key.n2
        local pm, names, parents = rig.poseables[li], rig.names[li], rig.parents[li] or {}
        if pm:GetNumBones() == n then
            -- EBoneSpaces.ComponentSpace (= 1 in Unreal). By number: the game's mod check refuses that enum by name
            -- ("not in ue_global_table.json") and then won't load the mod when the game starts.
            local space = 1
            -- The poseable keeps each bone relative to its parent: a bone not set again goes along when its parent
            -- moves - a planted foot slid with the shin, a hand with the arm. So a body bone is set again when it
            -- changed or its parent was (parents come first, by number). Not the head mesh's: its face bones go along
            -- with the head as they should, and each SetBoneTransformByName costs the whole mesh (Unreal builds its
            -- pose again): 890 face bones set again every frame took Chey's laptop from 44 to 24 fps (0.9.68).
            local did, cascade = {}, li == 1
            for b = 0, n - 1 do
                local up = cascade and parents[b]
                if not changed or changed[base + b] or (up and did[up]) then
                    did[b] = true
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
    if not r.rig and not actor_of(r.e) then return end  -- (not drawn here - far away: their frames aren't even read)
    local d = cfg_load("in", "posed_" .. pid)
    if not d then return end
    local kid, seq, head, part, rest = d:match("^(%d+)|(%d+)|([%-%d,]+)|([AB])|(.*)$")
    if not kid then                                     -- (an older sender: every frame has all the bones)
        kid, seq, head, rest = d:match("^(%d+)|(%d+)|([%-%d,]+)|(.*)$")
        part = "A"
    end
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
    -- this frame = keyframe + its differences (bones not listed are as in the keyframe; in a B frame the face's bones
    -- - the head mesh's - aren't in it at all: as in the last A frame)
    local vals, n1 = r.key.vals, r.key.n1
    local diff = split_ints(rest)
    local cur, changed = r.cur, {}
    local now = {}
    if r.fresh or not cur then                                       -- (all bones from the keyframe)
        cur = {}
        for i = 1, #vals do cur[i] = vals[i] end
        r.cur, r.fresh, changed, r.lastDiff = cur, false, nil, {}
    end
    -- Only a bone whose place or turn is other than last frame's is set again (0.9.67): a bone held away from the
    -- keyframe is listed in every frame - the face's 890, mostly still, were all set again 40 times a second, ~5 ms
    -- of each frame on Chey's laptop.
    local listed = {}
    for i = 1, #diff - 7, 8 do listed[diff[i]] = i end
    if changed then
        for b in pairs(r.lastDiff or {}) do                         -- (moved last time, not now: as in the keyframe)
            if not listed[b] then
                if part == "A" or b < n1 then
                    local o = b * 7
                    for j = 1, 7 do
                        if cur[o + j] ~= vals[o + j] then cur[o + j] = vals[o + j]; changed[b] = true end
                    end
                else
                    now[b] = true                                   -- (a face bone: kept until the next A frame)
                end
            end
        end
    end
    for b, i in pairs(listed) do
        local o = b * 7
        for j = 1, 7 do
            local v = diff[i + j]
            if cur[o + j] ~= v then
                cur[o + j] = v
                if changed then changed[b] = true end
            end
        end
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

-- How often this player's skeleton goes out: in/poserate.cfg (frames a second), written by the companion - 60 while a
-- partner is near enough to be drawn, a few when nobody is (Unreal's relevancy: what nobody can see isn't sent at full
-- rate; the far side doesn't even read it - receive). Missing: full rate.
local sendT, sendEvery = 0, POSE_EVERY
local function read_rate()
    local rate = tonumber(cfg_load("in", "poserate") or "")
    sendEvery = (rate and rate > 0) and 1 / math.min(rate, 60) or POSE_EVERY
end

local poseT = 0
local function pose_tick(dt)
    if not swept then sweep() end
    poseT, sendT = poseT + dt, sendT + dt
    if clock - mapAt > MAP_EVERY then
        mapAt = clock
        read_map()
        read_rate()
    end
    if sendT >= sendEvery - SLACK then
        sendT = sendT - sendEvery
        if sendT < 0 or sendT > sendEvery then sendT = 0 end
        send_pose()
    end
    if poseT >= POSE_EVERY - SLACK then
        poseT = poseT - POSE_EVERY
        if poseT < 0 or poseT > POSE_EVERY then poseT = 0 end
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
