"""Skeleton mirroring's rig (mod/InZOIMultiplayer/lua/B1/Main.lua), run for real (lupa) against a fake game:
- one reference only: the two poseable meshes (body, head) are never drawn; every mesh of the Zoi that is drawn - its
  own body and head too - follows them (hair and beard the head's, clothes the body's)
- the received bones go onto the poseables, sizes included
- the game pointing clothes back at its own body: following the rig again within CHECK_EVERY s
- no frame for STALE s: the Zoi given back - body and head moved by the game again (no leader), the rest following
  them, the poseables gone, nothing hidden
- a rig left by an earlier run (and the old kind: drawn poseables, the Zoi's own body and head hidden): cleaned up

    python tools/test_b1_rig.py
"""
import sys
from pathlib import Path

try:
    import lupa  # noqa: F401
    from lupa import LuaRuntime
except ImportError:
    print("B1 rig tests: lupa isn't installed (pip install lupa): skipped")
    sys.exit(0)

MAIN = Path(__file__).resolve().parents[1] / "mod" / "InZOIMultiplayer" / "lua" / "B1" / "Main.lua"

FAKE_GAME = r"""
local cfg = {}                                     -- section.key -> value
SAVES = {}
CFG = cfg
local function q(s) return (s:gsub('\\', '\\\\'):gsub('"', '\\"')) end
inzoi = { cli = { execute = function(cmd)
    local sec, key = cmd:match("uimod%.cfg_load mod_id=%S+ section=(%S+) key=(%S+)")
    if sec then
        local v = cfg[sec .. "." .. key]
        if v == nil then return false end
        return true, nil, '{"value":"' .. q(v) .. '"}'
    end
    local sec2, key2, val = cmd:match('uimod%.cfg_save mod_id=%S+ section=(%S+) key=(%S+) value="(.*)"$')
    if sec2 then
        cfg[sec2 .. "." .. key2] = val
        SAVES[#SAVES + 1] = { key = key2, value = val }
    end
    return true
end } }

local function class(name) return { GetName = function() return name end } end
local nextId = 0
function Component(name, cls, asset, bones)
    nextId = nextId + 1
    local c = { name = name, cls = class(cls), visible = true, bones = bones or {}, parent = nil, destroyed = false, set = {} }
    c.asset = asset and { GetName = function() return asset end, bones = c.bones } or nil
    c.LeaderPoseComponent = nil
    function c:GetName() return self.name end
    function c:GetClass() return self.cls end
    function c:IsVisible() return self.visible end
    function c:SetVisibility(v) self.visible = v end
    function c:GetSkinnedAsset() return self.asset end
    function c:SetSkinnedAssetAndUpdate(a) self.asset = a; self.bones = a.bones end
    function c:SetLeaderPoseComponent(l) self.LeaderPoseComponent = l end
    function c:K2_AttachToComponent(p, sock)
        if p == nil then error("attach to nil") end
        self.parent, self.socket = p, sock
        return true
    end
    function c:GetAttachParent() return self.parent end
    function c:GetAttachSocketName() return (self.socket == nil or self.socket == "") and "None" or self.socket end
    function c:children()
        local out = {}
        for _, x in ipairs(self.actor and self.actor.comps or {}) do if rawequal(x.parent, self) then out[#out + 1] = x end end
        return out
    end
    function c:GetNumChildrenComponents() return #self:children() end
    function c:GetChildComponent(i) return self:children()[i + 1] end
    function c:K2_SetRelativeTransform() end
    function c:GetNumMaterials() return 1 end
    function c:GetMaterial() return "M" end
    function c:SetMaterial() end
    function c:GetNumBones() return #self.bones end
    function c:GetBoneName(b) return self.bones[b + 1] end
    function c:GetParentBone(n)                       -- (a chain: each bone's parent is the one before it)
        for k, nm in ipairs(self.bones) do if nm == n then return self.bones[k - 1] or "None" end end
        return "None"
    end
    function c:SetBoneTransformByName(n, tr, space) self.set[n] = { tr = tr, space = space } end
    c.reads, c.wave = 0, 0
    function c:GetSocketTransform(n, space)
        self.reads = self.reads + 1
        local i = 0
        for k, nm in ipairs(self.bones) do if nm == n then i = k end end
        return { Translation = { X = i + self.wave, Y = 0, Z = 0 }, Rotation = { X = 0, Y = 0, Z = 0, W = 1 },
                 Scale3D = { X = 1, Y = 1, Z = 1 } }
    end
    function c:K2_DestroyComponent() self.destroyed = true; self.actor:remove(self) end
    return c
end

function Actor(entity)
    local a = { comps = {}, loc = { X = 0, Y = 0, Z = 0 }, yaw = 0 }
    a.EntityId = { Value = entity }
    function a:GetClass() return "ZoiClass" end
    function a:add(c) c.actor = self; self.comps[#self.comps + 1] = c; return c end
    function a:remove(c) for i, x in ipairs(self.comps) do if x == c then table.remove(self.comps, i) return end end end
    function a:K2_GetComponentsByClass(cls)
        local out = {}
        for _, c in ipairs(self.comps) do
            if (cls == "USkinnedMeshComponent" and c.skinned ~= false) or c.cls:GetName() == "PoseableMeshComponent" then
                out[#out + 1] = c
            end
        end
        return { Length = function() return #out end, Get = function(_, i) return out[i] end }
    end
    function a:AddComponentByClass(cls)
        return self:add(Component("PoseableMeshComponent_" .. nextId, "PoseableMeshComponent", nil, {}))
    end
    function a:K2_GetActorLocation() return self.loc end
    function a:K2_GetActorRotation() return { Yaw = self.yaw } end
    function a:K2_SetActorLocationAndRotation(l, r) self.loc = { X = l.X, Y = l.Y, Z = l.Z }; self.yaw = r.Yaw end
    return a
end

ACTORS = {}
UE = {
    USkinnedMeshComponent = "USkinnedMeshComponent", UPoseableMeshComponent = "UPoseableMeshComponent",
    AActor = "AActor",
    TArray = function() local t = { items = {} }
        function t:Length() return #self.items end
        function t:Get(i) return self.items[i] end
        return t end,
    UGameplayStatics = { GetAllActorsOfClass = function(_, _, arr) for _, a in ipairs(ACTORS) do arr.items[#arr.items + 1] = a end end },
    FVector = function(x, y, z) return { X = x, Y = y, Z = z } end,
    FRotator = function(p, y, r) return { Pitch = p, Yaw = y, Roll = r } end,
    FQuat = function(x, y, z, w) return { X = x, Y = y, Z = z, W = w } end,
    FTransform = function(q, p, s) return { Rotation = q, Translation = p, Scale3D = s } end,
}

BODY = { "root", "pelvis", "spine" }
HEAD = { "root", "pelvis", "spine", "head" }
function Zoi(entity)
    local a = Actor(entity)
    local body = a:add(Component("CharacterMesh0", "SkeletalMeshComponent", "SK_Body", BODY))
    local head = a:add(Component("Head", "SkeletalMeshComponentBudgeted", "SK_Head", HEAD))
    local hair = a:add(Component("Hair", "SkeletalMeshComponentBudgeted", "SK_Hair", HEAD))
    hair.LeaderPoseComponent = head
    local beard = a:add(Component("FaceHair", "SkeletalMeshComponent", "SK_Beard", HEAD))
    beard.LeaderPoseComponent = head
    local dress = a:add(Component("Garment", "SkeletalMeshComponent", "SK_Dress", BODY))
    dress.LeaderPoseComponent = body
    local glasses = a:add(Component("Glasses_0", "StaticMeshComponent", nil, nil))   -- (not skinned: at a socket)
    glasses.skinned, glasses.socket = false, "SOC_M_Glasses"
    head.parent, hair.parent, beard.parent, dress.parent, glasses.parent = body, head, head, body, head
    ACTORS[#ACTORS + 1] = a
    return a
end
function by(a, name) for _, c in ipairs(a.comps) do if c.name == name then return c end end end
function poseables(a)
    local out = {}
    for _, c in ipairs(a.comps) do if c.cls:GetName() == "PoseableMeshComponent" then out[#out + 1] = c end end
    return out
end
function same(x, y) return rawequal(x, y) end
function leader_is(c, l) return rawequal(c.LeaderPoseComponent, l) end
function poseable_on(a, parentName)
    for _, c in ipairs(poseables(a)) do if c.parent and c.parent.name == parentName then return c end end
end
"""


def frame(kid, seq, n1, n2, scale=1000):
    """-> (posek, posed) texts: every bone at (b, 0, 0) unturned, its size `scale`/1000."""
    vals, scales = [], []
    for b in range(n1 + n2):
        vals += [b * 10, 0, 0, 0, 0, 0, 10000]
        scales += [scale, scale, scale]
    head = "100,200,300,9000"
    k = f"{kid}|{head}|{n1}|{n2}|{','.join(map(str, vals))},{','.join(map(str, scales))}"
    return k, f"{kid}|{seq}|{head}|"


LI = None


def run_ticks(lua, main, seconds, feed=None, dt=0.05):
    for _ in range(int(seconds / dt)):
        if feed:
            feed()
        main.on_tick(dt)


def sender_tests():
    """This player's own Zoi: the body and the head (face, neck, shoulders) read 60 times a second (0.9.67: the
    players' choice; at 15 the shoulders moved at half the body's pace); every frame carries both (A)."""
    lua = LuaRuntime()
    lua.execute(FAKE_GAME)
    g = lua.globals()
    me = g.Zoi(24534)
    g.MP_B1_OWNER = me
    g.CFG["in.me"] = "24534"
    m = lua.execute(MAIN.read_text("utf-8"))
    body, head = g.by(me, "CharacterMesh0"), g.by(me, "Head")
    run_ticks(lua, m, 3.0, dt=1 / 60)                  # (the first second: me.cfg read, a keyframe)
    body.reads = head.reads = 0
    saves_before = len(list(g.SAVES.values()))

    def walk():
        body.wave += 1                                # (every bone moves: each frame lists all of them)
        head.wave += 1
    run_ticks(lua, m, 2.0, walk, dt=1 / 60)
    nb, nh = len(g.BODY), len(g.HEAD)
    body_rate, head_rate = body.reads / nb / 2.0, head.reads / nh / 2.0
    assert 57 <= body_rate <= 61, f"body read {body_rate:.1f} times a second (60 wanted)"
    assert 57 <= head_rate <= 61, f"head read {head_rate:.1f} times a second (60 wanted)"
    frames = [s.value for s in list(g.SAVES.values())[saves_before:] if s.key == "posed"]
    parts = [f.split("|")[3] for f in frames]
    assert 115 <= len(frames) <= 122 and set(parts) == {"A"}, (len(frames), set(parts))
    for f in frames:
        kid, seq, headp, part, rest = f.split("|")
        listed = [int(v) for i, v in enumerate(rest.split(",")) if rest and i % 8 == 0]
        if part == "B":
            assert listed and max(listed) < nb, f"a B frame lists a face bone: {listed}"
        elif rest:
            assert max(listed) >= nb, "an A frame without the face"
    keys = [s.value for s in g.SAVES.values() if s.key == "posek"]
    k = keys[-1].split("|")
    assert int(k[2]) == nb and int(k[3]) == nh and len(k[4].split(",")) == (nb + nh) * 10, "keyframe without sizes"
    print(f"sending: body {body_rate:.0f}/s, head {head_rate:.0f}/s, every frame with both, keyframes with sizes: ok")

    # nobody near enough to see it (the companion's in/poserate.cfg): a few frames a second, then full rate again
    g.CFG["in.poserate"] = "2"
    run_ticks(lua, m, 1.5, walk, dt=1 / 60)
    body.reads = 0
    run_ticks(lua, m, 3.0, walk, dt=1 / 60)
    far = body.reads / nb / 3.0
    assert 1.5 <= far <= 2.7, f"far: body read {far:.1f} times a second (2 wanted)"
    g.CFG["in.poserate"] = "60"
    run_ticks(lua, m, 1.5, walk, dt=1 / 60)
    body.reads = 0
    run_ticks(lua, m, 2.0, walk, dt=1 / 60)
    near = body.reads / nb / 2.0
    assert 57 <= near <= 61, f"near again: {near:.1f}/s"
    print(f"nobody near: {far:.0f} frames a second; near again: {near:.0f}: ok")


def face_frame_tests():
    """Receiving: a B frame leaves the face as the last A frame had it; an A frame without a face bone puts it back."""
    lua = LuaRuntime()
    lua.execute(FAKE_GAME)
    g = lua.globals()
    zoi = g.Zoi(25674)
    g.MP_B1_OWNER = g.Zoi(24534)
    cfg = g.CFG
    m = lua.execute(MAIN.read_text("utf-8"))
    cfg["in.posemap"] = "pd699142e=25674.1;"
    nb, nh = 3, 4
    k, _ = frame(1, 0, nb, nh)
    cfg["in.posek_pd699142e"] = k
    hb = nb + 3                                         # (the head mesh's "head" bone)
    seq = {"n": 0}

    def send(part, bones):
        seq["n"] += 1
        rest = ",".join(f"{b},{999},0,0,0,0,0,10000" for b in bones)
        cfg["in.posed_pd699142e"] = f"1|{seq['n']}|100,200,300,9000|{part}|{rest}"
        run_ticks(lua, m, 0.1)
    send("A", [])
    send("A", [hb, 1])
    ph = g.poseable_on(zoi, "Head")
    assert ph.set["head"].tr.Translation.X == 99.9
    send("B", [2])
    assert ph.set["head"].tr.Translation.X == 99.9, "a B frame put the face back to the keyframe"
    pb = g.poseable_on(zoi, "CharacterMesh0")
    assert pb.set["pelvis"].tr.Translation.X == 1.0, "a body bone missing from a B frame wasn't put back"
    assert pb.set["spine"].tr.Translation.X == 99.9
    send("B", [])
    assert ph.set["head"].tr.Translation.X == 99.9
    send("A", [])
    assert ph.set["head"].tr.Translation.X == 6.0, "an A frame without the face bone left it moved"
    print("receiving: the face stays as the last A frame had it through B frames, A frames put it back: ok")

    # a bone held where it was (listed again, the same values): not set again; one that moves is
    sets = {"n": 0}
    real_set = ph.SetBoneTransformByName
    def count_set(self, n, tr, space):
        sets["n"] += 1
        real_set(self, n, tr, space)
    ph.SetBoneTransformByName = count_set
    pb.SetBoneTransformByName = count_set
    send("A", [hb, 1])
    sets["n"] = 0
    send("A", [hb, 1])
    send("A", [hb, 1])
    assert sets["n"] == 0, f"{sets['n']} bones set again with nothing changed"
    send("A", [hb])                                    # (bone 1 back to the keyframe: it and its child, spine)
    assert sets["n"] == 2 and pb.set["pelvis"].tr.Translation.X == 1.0, sets
    print("receiving: a bone that didn't move since the last frame isn't set again; a moved one's children are: ok")

    # a partner not drawn here (far away: no actor): their frames aren't even read
    loads = {"n": 0}
    real_execute = g.inzoi.cli.execute
    ACTORS = g.ACTORS
    ACTORS[1] = g.MP_B1_OWNER                           # (only my own Zoi left: theirs is far away, not drawn)
    ACTORS[2] = None
    lua.execute("ACTORS = {ACTORS[1]}")
    m2 = lua.execute(MAIN.read_text("utf-8"))           # (a fresh run of the script: no rig yet)

    def counting(cmd):
        if "posed_" in cmd:
            loads["n"] += 1
        return real_execute(cmd)
    g.inzoi.cli.execute = counting
    run_ticks(lua, m2, 1.0)
    g.inzoi.cli.execute = real_execute
    assert loads["n"] == 0, f"read a far partner's frames {loads['n']} times"
    print("a partner too far to be drawn: their frames aren't read: ok")


def main():
    lua = LuaRuntime()
    lua.execute(FAKE_GAME)
    g = lua.globals()
    global LI
    LI = g.leader_is
    zoi = g.Zoi(25674)
    owner = g.Zoi(24534)
    g.MP_B1_OWNER = owner
    cfg = g.CFG
    src = MAIN.read_text("utf-8")
    m = lua.execute(src)
    cfg["in.posemap"] = "pd699142e=25674.1;"
    seq = {"n": 0}
    k, _ = frame(1, 0, 3, 4, scale=1100)
    cfg["in.posek_pd699142e"] = k

    def feed():
        seq["n"] += 1
        cfg["in.posed_pd699142e"] = frame(1, seq["n"], 3, 4)[1]

    run_ticks(lua, m, 0.5, feed)
    assert cfg["out.b1"] is None or "error" not in str(cfg["out.b1"]), cfg["out.b1"]
    pb, ph = g.poseable_on(zoi, "CharacterMesh0"), g.poseable_on(zoi, "Head")
    assert pb and ph, "no rig built"
    assert not pb.visible and not ph.visible, "a poseable mesh is drawn (two references: it and the Zoi's own)"
    body, head, hair, beard, dress = (g.by(zoi, n) for n in ("CharacterMesh0", "Head", "Hair", "FaceHair", "Garment"))
    for c in (body, head, hair, beard, dress):
        assert c.visible, f"{c.name} hidden"
    assert LI(body, pb) and LI(head, ph), "the Zoi's own body/head follow the game"
    assert LI(hair, ph) and LI(beard, ph), "hair/beard don't follow the received head"
    assert LI(dress, pb), "the dress doesn't follow the received body"
    print("one reference: every drawn mesh (own body and head too) follows the received skeleton, poseables never drawn: ok")
    glasses = g.by(zoi, "Glasses_0")
    assert g.same(glasses.parent, ph) and glasses.socket == "SOC_M_Glasses", "the glasses aren't on the received head"
    assert g.same(hair.parent, head), "a skinned mesh was moved like a prop"
    print("what hangs on the Zoi's own head (glasses) hangs on the received head, same socket: ok")

    st = ph.set["head"]
    assert st is not None and st.space == 1, "head bone not set in component space"
    assert abs(st.tr.Scale3D.X - 1.1) < 1e-9 and st.tr.Translation.X == 60 / 10, (st.tr.Scale3D.X, st.tr.Translation.X)
    assert pb.set["spine"].tr.Translation.X == 2.0
    print("the received bones (place, turn, size) go onto the poseables: ok")

    dress.LeaderPoseComponent = body                  # (the game points the dress back at its own body)
    phone = zoi.add(zoi, g.Component("Phone", "StaticMeshComponent", None, None))   # (and puts a phone in her hand)
    phone.skinned, phone.socket, phone.parent = False, "hand_r", body
    run_ticks(lua, m, 0.7, feed)
    assert g.same(phone.parent, pb) and phone.socket == "hand_r", "something taken in hand stayed on the game's body"
    assert LI(dress, pb), "the dress stayed on the game's body"
    assert LI(body, pb)
    print("clothes the game points back at its own body, and what it puts in a hand, follow the rig again: ok")

    run_ticks(lua, m, 2.0)                             # (no frames: stale)
    assert len(list(g.poseables(zoi).values())) == 0, "poseables left after restore"
    assert body.LeaderPoseComponent is None and head.LeaderPoseComponent is None, "body/head still follow something"
    assert LI(hair, head) and LI(beard, head) and LI(dress, body)
    assert all(c.visible for c in (body, head, hair, beard, dress))
    assert g.same(glasses.parent, head) and glasses.socket == "SOC_M_Glasses", "the glasses weren't given back"
    assert g.same(phone.parent, body) and phone.socket == "hand_r"
    print("no frames for STALE s: the Zoi given back whole (own body and head moved by the game, nothing hidden): ok")

    # an old (0.9.56) rig left on a Zoi: drawn poseables, its own body and head hidden; a new run of the script sweeps it
    lua2 = LuaRuntime()
    lua2.execute(FAKE_GAME)
    g2 = lua2.globals()
    LI = g2.leader_is
    z2 = g2.Zoi(25674)
    g2.MP_B1_OWNER = g2.Zoi(24534)
    b2, h2 = g2.by(z2, "CharacterMesh0"), g2.by(z2, "Head")
    old_b = z2.AddComponentByClass(z2, "x")
    old_b.parent = b2
    old_h = z2.AddComponentByClass(z2, "x")
    old_h.parent = h2
    g2.by(z2, "Hair").LeaderPoseComponent = old_h
    g2.by(z2, "Glasses_0").parent = old_h
    g2.by(z2, "Garment").LeaderPoseComponent = old_b
    b2.visible = h2.visible = False
    m2 = lua2.execute(src)
    run_ticks(lua2, m2, 0.2)
    assert len(list(g2.poseables(z2).values())) == 0, "an earlier run's poseables stayed"
    assert LI(g2.by(z2, "Hair"), h2) and LI(g2.by(z2, "Garment"), b2), "an earlier run's followers stayed on its poseables"
    assert b2.visible and h2.visible, "the old rig's hidden body/head stayed hidden"
    gl = g2.by(z2, "Glasses_0")
    assert g2.same(gl.parent, h2) and gl.socket == "SOC_M_Glasses", "glasses left on an earlier run's poseable"
    assert "pose-swept 2" in str(g2.CFG["out.b1"]), g2.CFG["out.b1"]
    print("a rig left by an earlier run (old kind too) is cleaned up: ok")
    sender_tests()
    face_frame_tests()
    print("B1 rig tests passed")


main()
