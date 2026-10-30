-- Helpers for building inZOI Multiplayer's screens out of the game's own widgets.
-- Everything here runs on the game thread (UE4SS LoopInGameThreadWithDelay / hooks): Lua must never run on two
-- threads at once, and UObjects may only be touched on the game thread.
-- Never pass a style struct (FButtonStyle, FSlateBrush, FSlateFontInfo...) to a function: UE4SS can't marshal their
-- object pointers and the game crashes. Reuse the game's widget classes (their styles come with them) and set
-- images with SetBrushFromTexture, colours with simple structs.
local json = require("mp_json")
local U = { json = json }

U.VISIBLE, U.COLLAPSED, U.HIDDEN, U.HIT_TEST_INVISIBLE, U.SELF_HIT_TEST_INVISIBLE = 0, 1, 2, 3, 4

function U.log(s) print("[MP] " .. tostring(s) .. "\n") end

-- The same line at most once per `every` seconds (errors repeated every tick would flood UE4SS.log).
local logged, logged_n = {}, 0
function U.log_limited(s, every)
  s = tostring(s)
  local now = os.clock()
  local last = logged[s]
  if last and now - last < (every or 10) then return end
  if not last then
    logged_n = logged_n + 1
    if logged_n > 200 then logged, logged_n = {}, 1 end    -- (messages with changing text: keep it bounded)
  end
  logged[s] = now
  U.log(s)
end

function U.valid(o) return o ~= nil and o.IsValid ~= nil and o:IsValid() end

function U.addr(o) return U.valid(o) and o:GetAddress() or 0 end

function U.same(a, b) return U.valid(a) and U.valid(b) and a:GetAddress() == b:GetAddress() end

function U.class_name(o)
  local ok, n = pcall(function() return o:GetClass():GetFName():ToString() end)
  return ok and n or ""
end

local PanelWidget, UserWidget
function U.find(w, name)
  PanelWidget = PanelWidget or StaticFindObject("/Script/UMG.PanelWidget")
  UserWidget = UserWidget or StaticFindObject("/Script/UMG.UserWidget")
  if not U.valid(w) then return nil end
  if w:GetFName():ToString() == name then return w end
  if w:IsA(UserWidget) then
    local t = w.WidgetTree
    if U.valid(t) then return U.find(t.RootWidget, name) end
    return nil
  end
  if w:IsA(PanelWidget) then
    for i = 0, w:GetChildrenCount() - 1 do
      local r = U.find(w:GetChildAt(i), name)
      if r then return r end
    end
  end
  return nil
end

-- A named part of a widget: its bound property when the game exposes one, else found in its tree (cached).
local parts = {}
function U.part(owner, name)
  if not U.valid(owner) then return nil end
  local key = owner:GetAddress() .. "/" .. name
  local w = parts[key]
  if U.valid(w) then return w end
  local ok, p = pcall(function() return owner[name] end)
  if ok and U.valid(p) then w = p else w = U.find(owner, name) end
  parts[key] = w
  return w
end

-- Set a text / visibility only when what's on screen differs. (Compared with the widget itself, not with what we
-- set last time: the page's own code changes some of them after us.)
function U.text(w, s)
  if not U.valid(w) then return end
  s = tostring(s or "")
  local ok, cur = pcall(function() return w:GetText():ToString() end)
  if ok and cur == s then return end
  w:SetText(FText(s))
end

function U.show(w, on, hidden_kind)
  if not U.valid(w) then return end
  local v = on and U.VISIBLE or (hidden_kind or U.COLLAPSED)
  if on == "hit" then v = U.SELF_HIT_TEST_INVISIBLE end
  if w:GetVisibility() == v then return end
  w:SetVisibility(v)
end

function U.forget(w) end

-- ------------------------------------------------------------------------------------------------ the game's icons
U.ICONS = {
  people = "/Game/UI/Common/Tex/Shape/Common_Icon_Group_001.Common_Icon_Group_001",
  person = "/Game/UI/Common/Tex/Shape/Common_Man_004_UIT.Common_Man_004_UIT",
  mods = "/Game/UI/Common/Tex/Shape/Common_Icon_Sell.Common_Icon_Sell",
  version = "/Game/UI/Common/Tex/Shape/Common_Reward_002.Common_Reward_002",
  clock = "/Game/UI/Common/Tex/Shape/Common_Clock_002.Common_Clock_002",
  plus = "/Game/UI/Common/Tex/Shape/Common_Icon_Plus.Common_Icon_Plus",
  edit = "/Game/UI/Common/Tex/Shape/Common_Icon_Edit.Common_Icon_Edit",
  trash = "/Game/UI/Common/Tex/Shape/Common_Icon_Trash_002.Common_Icon_Trash_002",
  refresh = "/Game/UI/Common/Tex/Shape/Common_Icon_Refresh.Common_Icon_Refresh",
  lock = "/Game/UI/Common/Tex/Shape/Common_Icon_Lock.Common_Icon_Lock",
  unlock = "/Game/UI/Common/Tex/Shape/Common_Icon_UnLock_002.Common_Icon_UnLock_002",
  enter = "/Game/UI/Common/Tex/Shape/Common_Icon_Enter.Common_Icon_Enter",
  home = "/Game/UI/Common/Tex/Shape/Common_Icon_Home.Common_Icon_Home",
  settings = "/Game/UI/Common/Tex/Shape/Common_Icon_Setting_UIT.Common_Icon_Setting_UIT",
  info = "/Game/UI/Common/Tex/Shape/Common_Icon_Info.Common_Icon_Info",
  download = "/Game/UI/Common/Tex/Shape/Common_Icon_Download.Common_Icon_Download",
  close = "/Game/UI/Common/Tex/Shape/Common_Icon_Close.Common_Icon_Close",
  check = "/Game/UI/Common/Tex/Shape/Common_Check_001.Common_Check_001",
  send = "/Game/UI/Common/Tex/Shape/Common_Icon_Send.Common_Icon_Send",
  stop = "/Game/UI/Common/Tex/Shape/Common_Icon_Stop.Common_Icon_Stop",
  copy = "/Game/UI/Common/Tex/Shape/Common_Icon_Copy.Common_Icon_Copy",
  link = "/Game/UI/Common/Tex/Shape/Common_Link_001.Common_Link_001",
  visitor = "/Game/UI/Common/Tex/Shape/Common_Icon_Visitor_002.Common_Icon_Visitor_002",
  caution = "/Game/UI/Common/Tex/Shape/Common_Icon_Caution.Common_Icon_Caution",
  shield_ok = "/Game/UI/Mod/Tex/Validation_Icon_ShieldCheck_UIT.Validation_Icon_ShieldCheck_UIT",
  shield_alert = "/Game/UI/Mod/Tex/Validation_Icon_ShieldAlert_UIT.Validation_Icon_ShieldAlert_UIT",
  empty_thumb = "/Game/UI/Mod/Tex/Item_Image_EmptyMod_UIT.Item_Image_EmptyMod_UIT",
  city_blue = "/Game/UI/CitySelect/Tex/CitySelect_Thumbnail_BlueCity.CitySelect_Thumbnail_BlueCity",
  city_red = "/Game/UI/CitySelect/Tex/CitySelect_Thumbnail_RedCity.CitySelect_Thumbnail_RedCity",
  city_cat = "/Game/UI/CitySelect/Tex/CitySelect_Thumbnail_CatIsland.CitySelect_Thumbnail_CatIsland",
}

local textures = {}
function U.texture(name)
  local path = U.ICONS[name] or name
  if not path or path == "" then return nil end
  local t = textures[path]
  if U.valid(t) then return t end
  t = StaticFindObject(path)
  if not U.valid(t) and LoadAsset then                  -- not loaded yet: load it (by its full Path.Name)
    pcall(LoadAsset, path)
    t = StaticFindObject(path)
  end
  if U.valid(t) then textures[path] = t; return t end
  textures[path] = nil
  if name == "empty_thumb" then return U.texture("city_blue") end
  return nil
end

-- A texture the game has loaded somewhere (e.g. on its own mod rows), kept for the names that point at it.
function U.remember_texture(o)
  local full = o:GetFullName():match("^%S+%s+(.+)$")
  if full then textures[full] = o end
end

local icon_shown = {}
function U.icon(image, name)
  if not U.valid(image) or not name then return end
  local t = U.texture(name)
  if not t then U.log("no texture for " .. tostring(name)); return end
  local a = image:GetAddress()
  if icon_shown[a] == t:GetAddress() then return end
  icon_shown[a] = t:GetAddress()
  image:SetBrushFromTexture(t, false)
end

-- ------------------------------------------------------------------------------------------------ clicks and checks
-- The game's buttons don't use script delegates, so ours are routed through two harmless widget functions we hook:
-- OnClicked -> Widget:ForceLayoutPrepass, OnCheckStateChanged -> Widget:ForceVolatile(bool).
MP = MP or {}
MP.clicks = MP.clicks or {}
MP.checks = MP.checks or {}
MP.handler_w = MP.handler_w or {}                     -- address -> the widget it was registered for (see prune)

function U.on_click(button, fn)
  if not U.valid(button) then return end
  local d = button.OnClicked
  pcall(function() d:Remove(button, "ForceLayoutPrepass") end)
  d:Add(button, "ForceLayoutPrepass")
  MP.clicks[button:GetAddress()] = fn
  MP.handler_w[button:GetAddress()] = button
end

function U.on_check(checkbox, fn)
  if not U.valid(checkbox) then return end
  local d = checkbox.OnCheckStateChanged
  d:Clear()                                           -- also drops the game's own handler (it would act on mod data)
  d:Add(checkbox, "ForceVolatile")
  MP.checks[checkbox:GetAddress()] = fn
  MP.handler_w[checkbox:GetAddress()] = checkbox
end

function U.drop_handlers(w)
  local a = U.addr(w)
  MP.clicks[a], MP.checks[a], MP.handler_w[a] = nil, nil, nil
end

-- Forget the handlers of widgets that are gone (a page or menu that was rebuilt), so the tables don't grow and a new
-- widget at a reused address doesn't run an old page's handler. Handlers registered without a widget kept (before a
-- reload of this file) are left alone.
function U.prune_handlers()
  local gone = {}
  for a, w in pairs(MP.handler_w) do
    local ok, alive = pcall(U.valid, w)
    if not (ok and alive) then gone[#gone + 1] = a end
  end
  for _, a in ipairs(gone) do MP.clicks[a], MP.checks[a], MP.handler_w[a] = nil, nil, nil end
  return #gone
end

-- ------------------------------------------------------------------------------------------------ talking to the program
-- %APPDATA%/inzoi-mp/ingame, or the folder named in signals_dir.txt next to these scripts (for testing)
local function signals_dir()
  local f = io.open((MP.dir or "") .. "signals_dir.txt", "r")
  if f then
    local d = (f:read("l") or ""):gsub("%s+$", "")
    f:close()
    if d ~= "" then
      if not d:find("[/\\]$") then d = d .. "\\" end
      return d
    end
  end
  return (os.getenv("APPDATA") or ".") .. "\\inzoi-mp\\ingame\\"
end
U.SIGNALS = signals_dir()
local seq = 0

function U.read(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local s = f:read("a")
  f:close()
  return s
end

function U.send(event)                                       -- one event = one file, picked up by the program
  seq = seq + 1
  event.at = os.time()
  local name = string.format("%sevents\\%d-%06d.json", U.SIGNALS, os.time(), seq)
  local tmp = name .. ".part"
  local f = io.open(tmp, "wb")
  if not f then U.log("can't write " .. tmp); return false end
  f:write(json.encode(event))
  f:close()
  os.remove(name)
  return os.rename(tmp, name)
end

-- The program rewrites program.json every 2 s; on Windows the file is briefly missing while it's replaced, so a
-- failed read keeps the last answer.
local last_alive, last_info = 0, nil
function U.program_alive()
  local s = U.read(U.SIGNALS .. "program.json")
  local p = s and json.decode(s)
  if p and tonumber(p.alive) then last_alive, last_info = tonumber(p.alive), p end
  return (os.time() - last_alive) < 8, last_info
end

return U
