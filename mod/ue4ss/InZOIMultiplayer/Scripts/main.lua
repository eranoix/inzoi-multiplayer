-- inZOI Multiplayer, inside the game (a UE4SS Lua mod, installed by the inZOI Multiplayer program).
-- Puts a real "Multiplayer" item on inZOI's main menu and turns the game's Mods page into the servers page.
-- All code runs on the game thread: hooks fire there, and the one timer is LoopInGameThreadWithDelay. (LoopAsync
-- runs Lua on a second thread at the same time as the game thread, which crashes the game.)
local dir = (debug.getinfo(1, "S").source:sub(2):gsub("main%.lua$", ""))
package.path = dir .. "?.lua;" .. package.path

MP = MP or {}
MP.dir = dir

local MODULES = { "mp_json", "mp_util", "mp_menu", "mp_dialog", "mp_page" }

local function log(s) print("[MP] " .. tostring(s) .. "\n") end

local function load_modules()
  for _, m in ipairs(MODULES) do package.loaded[m] = nil end
  local ok, err = pcall(function()
    MP.U = require("mp_util")
    MP.menu = require("mp_menu")
    MP.page_mod = require("mp_page")
  end)
  if not ok then log("loading failed: " .. tostring(err)) end
  return ok
end

-- (the same error at most once per 10 s: the tick runs 10x a second)
local errs_at, errs_n = {}, 0
local function guarded(name, fn, ...)
  local ok, err = pcall(fn, ...)
  if not ok then
    local msg = name .. ": " .. tostring(err)
    local last = errs_at[msg]
    if last and os.clock() - last < 10 then return end
    if not last then
      errs_n = errs_n + 1
      if errs_n > 200 then errs_at, errs_n = {}, 1 end
    end
    errs_at[msg] = os.clock()
    log(msg)
  end
end

-- Hooks are registered once per game run; they call whatever module code is loaded now, so a reload swaps it.
if not MP.notified then
  MP.notified = true
  -- the game building its main menu, or a Mods page (fires as the object is created, before it's drawn)
  NotifyOnNewObject("/Game/UI/Lobby/WBP_Lobby.WBP_Lobby_C", function(w)
    if MP.menu then guarded("menu", MP.menu.on_new, w) end
  end)
  NotifyOnNewObject("/Game/UI/Mod/WBP_ModManagement.WBP_ModManagement_C", function(w)
    if MP.page_mod then guarded("page", MP.page_mod.on_new, w) end
  end)
end
if not MP.hooked then
  MP.hooked = true
  RegisterHook("/Script/UMG.Widget:ForceLayoutPrepass", function(ctx)
    local w = ctx:get()
    local fn = MP.clicks and MP.clicks[w:GetAddress()]
    if fn then guarded("click", fn) end
  end)
  RegisterHook("/Script/UMG.Widget:ForceVolatile", function(ctx, force)
    local w = ctx:get()
    local fn = MP.checks and MP.checks[w:GetAddress()]
    if fn then guarded("check", fn, force:get()) end
  end)
end

load_modules()
guarded("start", function() MP.menu.start() end)

local SIGNALS = MP.U and MP.U.SIGNALS or ""
local function version()
  local f = io.open(dir .. "version.txt", "r")
  if not f then return "dev" end
  local v = (f:read("l") or ""):gsub("%s+$", "")
  f:close()
  return v
end
MP.VERSION = version()

-- game.json: the script is running (the program then draws no "Multiplayer" item of its own over the menu)
-- Written to a temp file and renamed into place, so the program never reads half a file. (On Windows os.rename won't
-- replace an existing file: then the old one is removed first; the program treats a briefly missing file as no news.)
local function heartbeat()
  local path = SIGNALS .. "game.json"
  local tmp = path .. ".part"
  local f = io.open(tmp, "wb")
  if not f then return end
  f:write(string.format('{"alive":%d,"version":"%s","menu":%s,"page":%s}', os.time(), MP.VERSION,
    tostring(MP.U.valid(MP.menu_button)), tostring(MP.U.valid(MP.page))))
  f:close()
  if not os.rename(tmp, path) then
    os.remove(path)
    if not os.rename(tmp, path) then os.remove(tmp) end
  end
end
local slow = 0
if MP.loop then pcall(CancelDelayedAction, MP.loop) end
MP.loop = LoopInGameThreadWithDelay(100, function()
  if MP.page_mod then guarded("tick", MP.page_mod.tick) end
  slow = slow + 1
  if slow >= 10 then                                         -- once a second
    slow = 0
    if MP.menu then guarded("menu", MP.menu.refresh) end
    guarded("heartbeat", heartbeat)
    local f = io.open(SIGNALS .. "reload", "r")              -- the program updated these files
    if f then
      f:close()
      os.remove(SIGNALS .. "reload")
      log("reloading")
      if load_modules() then guarded("start", function() MP.menu.start() end) end
    end
  end
end)
log("inZOI Multiplayer " .. MP.VERSION .. " loaded from " .. dir)
