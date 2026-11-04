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
-- This PC's real time in ms (the engine's, read every tick in on_tick): the game hands on_tick no frame time (dt was
-- always the 0.03 fallback - every timer here ran 1.8x fast at 60 fps), and the frames' time stamps need real time.
local realMs = 0
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
    -- (5th: when it was read, this PC's real ms - the receiver plays the frames back on that timeline, interpolating;
    -- an older receiver reads the first 4)
    local head = string.format("%d,%d,%d,%d,%d", round(l.X * 10), round(l.Y * 10), round(l.Z * 10), round(r.Yaw * 100),
        math.floor(realMs))
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
local queue_frame, play_next   -- (the playback, defined above receive)

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

-- Playback (0.9.70) - snapshot interpolation, as Source (Valve), Overwatch and Unreal show other players: their Zoi is
-- drawn a little in the past, on the timeline of their frames' time stamps, every one of this game's frames
-- interpolated between the two frames received around that moment (place and turn of each bone, the actor's place and
-- facing). Frames come unevenly (their game's frame rate, the file hops, the network): shown as they came, the Zoi
-- jerked - two frames in one of ours, then none for two; drawn this way it moves at this game's frame rate.
-- How far in the past (playback delay) follows how unevenly they come: their usual frame gap + the spread of the
-- lateness seen over the last frames (Gaffer on Games' jitter buffer), 15 to 150 ms, eased so time never jumps.
local OFFSETS, Q_MAX = 60, 8

local function copy7(cur, b)
    local o = b * 7
    return { cur[o + 1], cur[o + 2], cur[o + 3], cur[o + 4], cur[o + 5], cur[o + 6], cur[o + 7] }
end

local function timing(r, t)
    if r.lastT and (t < r.lastT - 1000 or t - r.lastT > 5000) then r.offs, r.delay, r.gap, r.q = nil, nil, nil, nil end
    local gap = r.lastT and t - r.lastT
    r.lastT = t
    if gap and gap > 0 and gap < 500 then r.gap = r.gap and r.gap + (gap - r.gap) * 0.1 or gap end
    local offs = r.offs or {}
    r.offs, r.oi = offs, ((r.oi or 0) % OFFSETS) + 1
    offs[r.oi] = realMs - t                                  -- (their clock to mine + how late this one came)
    local sorted = {}
    for i = 1, #offs do sorted[i] = offs[i] end
    table.sort(sorted)
    r.omin = sorted[1]
    local spread = sorted[math.max(1, math.ceil(#sorted * 0.9))] - sorted[1]
    local want = math.max(15, math.min(150, (r.gap or 33) + spread + 4))
    r.delay = r.delay and r.delay + (want - r.delay) * 0.05 or want
end

-- A received frame (cur: all its bones, changed: those other than the frame before; nil = start over) goes into the
-- queue: the first one in full (r.base, what's shown), the next ones as their changes only.
queue_frame = function(r, spot, cur, changed)
    timing(r, spot.t)
    if not changed or not r.q or not r.base then
        r.base, r.show, r.q, r.pend = {}, {}, { spot }, {}
        for i = 1, #cur do r.base[i], r.show[i] = cur[i], cur[i] end
        apply(r, r.show, nil)
        r.target, r.exact = spot, true
        return
    end
    local d = {}
    for b in pairs(changed) do d[b] = copy7(cur, b) end
    spot.d = d
    local q = r.q
    q[#q + 1] = spot
    while #q > Q_MAX do play_next(r) end
end

-- The oldest frame done with: the next one's changes go into r.base (what it is, exactly), and those bones are set
-- to it unless the new interval moves them again (pend).
play_next = function(r)
    local q = r.q
    local nxt = q[2]
    for b, v in pairs(nxt.d) do
        local o = b * 7
        for j = 1, 7 do r.base[o + j] = v[j] end
        r.pend[b] = true
    end
    nxt.d = nil
    table.remove(q, 1)
end

local function render(r)
    local q = r.q
    if not q or not r.rig or not r.omin then return end
    local at = realMs - r.omin - r.delay                      -- (the moment drawn, on their timeline)
    while #q >= 2 and q[2].t <= at do play_next(r) end
    local a, b2 = q[1], q[2]
    local base, show, set = r.base, r.show, {}
    for b in pairs(r.pend) do
        local o = b * 7
        for j = 1, 7 do show[o + j] = base[o + j] end
        set[b] = true
    end
    r.pend = {}
    local spot = a
    if b2 and b2.t > a.t then
        local k = math.max(0, math.min(1, (at - a.t) / (b2.t - a.t)))
        for b, v in pairs(b2.d) do
            local o = b * 7
            show[o + 1] = base[o + 1] + (v[1] - base[o + 1]) * k
            show[o + 2] = base[o + 2] + (v[2] - base[o + 2]) * k
            show[o + 3] = base[o + 3] + (v[3] - base[o + 3]) * k
            -- (the turn: the shorter way - q and -q are the same turn - then made a unit again, x10000)
            local x0, y0, z0, w0 = base[o + 4], base[o + 5], base[o + 6], base[o + 7]
            local x1, y1, z1, w1 = v[4], v[5], v[6], v[7]
            if x0 * x1 + y0 * y1 + z0 * z1 + w0 * w1 < 0 then x1, y1, z1, w1 = -x1, -y1, -z1, -w1 end
            local x, y, z, w = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, z0 + (z1 - z0) * k, w0 + (w1 - w0) * k
            local n = math.sqrt(x * x + y * y + z * z + w * w)
            if n > 0 then n = 10000 / n end
            show[o + 4], show[o + 5], show[o + 6], show[o + 7] = x * n, y * n, z * n, w * n
            set[b] = true
            r.pend[b] = true                                  -- (exact once the interval is over)
        end
        local turn = ((b2.yaw - a.yaw + 540) % 360) - 180
        spot = { x = a.x + (b2.x - a.x) * k, y = a.y + (b2.y - a.y) * k, z = a.z + (b2.z - a.z) * k,
                 yaw = a.yaw + turn * k }
    end
    if next(set) then apply(r, show, set) end
    r.target, r.exact = spot, true
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
        r.newkey = true                 -- (a new keyframe, every second: the same Zoi going on - not started over)
    end
    r.seq, r.at = seq, clock
    local h = split_ints(head)
    local spot = { x = h[1] / 10, y = h[2] / 10, z = h[3] / 10, yaw = h[4] / 100, t = h[5] }
    if not spot.t then r.target, r.exact = spot, false end          -- (an older sender: shown as it comes, glided)
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
    -- (a new keyframe: this frame is built on it from scratch, then compared with the last frame below - before
    -- 0.9.70 the Zoi was set again whole every second, and the playback would have started over: a hitch a second)
    local prev = (r.newkey and not r.fresh and cur and #cur == #vals) and cur or nil
    r.newkey = false
    if r.fresh or not cur or prev then                               -- (all bones from the keyframe)
        cur = {}
        for i = 1, #vals do cur[i] = vals[i] end
        r.cur, r.fresh, r.lastDiff = cur, false, {}
        changed = prev and {} or nil
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
    if prev then                                                     -- (what differs from the last frame, all of it)
        changed = {}
        for b = 0, math.floor(#cur / 7) - 1 do
            local o = b * 7
            for j = 1, 7 do
                if cur[o + j] ~= prev[o + j] then changed[b] = true; break end
            end
        end
    end
    if not spot.t then
        apply(r, cur, changed)
        return
    end
    queue_frame(r, spot, cur, changed)
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
                if r.exact then k, kr = 1, 1 end                   -- (played back: exactly where it was then)
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
            if r.rig and clock - r.at > STALE then restore(r); r.q = nil end
        end
    end
    for _, r in pairs(remotes) do
        local ok, err = pcall(render, r)                   -- (every frame of this game: the playback)
        if not ok then result("play-error " .. tostring(err)) end
    end
    glide(dt)    -- (the drawn actor: B2 sets only the simulation where they are ("skel"), which doesn't move it)
end

function M.on_begin() end

local lastReal = nil
function M.on_tick(dt)
    dt = tonumber(dt) or 0.03
    local own = _G.MP_B1_OWNER
    if own then
        local ok, now = pcall(UE.UGameplayStatics.GetRealTimeSeconds, own)
        if ok and type(now) == "number" then
            if lastReal and now > lastReal and now - lastReal < 0.5 then dt = now - lastReal end
            lastReal = now
            realMs = now * 1000
        end
    end
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
