-- The "Multiplayer" item on inZOI's main menu: a real WBP_Lobby_Button (the game's own menu item, with its hover
-- butterfly, glow and sounds) placed right under "New Game". Clicking it asks the program to open the servers page.
local U = require("mp_util")
local M = {}

local WBL

local function slot_of(widget)
  local s = widget.Slot
  local p, z = s.Padding, s.Size
  return {
    pad = { Left = p.Left, Top = p.Top, Right = p.Right, Bottom = p.Bottom },
    size = { SizeRule = z.SizeRule, Value = z.Value },
    h = s.HorizontalAlignment, v = s.VerticalAlignment,
  }
end

local function add(box, widget, from)
  local slot = box:AddChildToVerticalBox(widget)
  slot:SetPadding(from.pad)
  slot:SetSize(from.size)
  slot:SetHorizontalAlignment(from.h)
  slot:SetVerticalAlignment(from.v)
end

local function has(box, widget)
  for i = 0, box:GetChildrenCount() - 1 do
    if U.same(box:GetChildAt(i), widget) then return true end
  end
  return false
end

-- Put the item into this main menu (once per menu: a new menu is built each time the game comes back to it).
function M.attach(lobby)
  if not U.valid(lobby) then return end
  local ng = lobby.BW_WBP_Button_NewGame
  if not U.valid(ng) then return end
  local box = ng:GetParent()
  if U.valid(MP.menu_button) and U.addr(MP.menu_lobby) == lobby:GetAddress() and has(box, MP.menu_button) then
    U.on_click(MP.menu_button.BW_Button, function() MP.menu.clicked() end)     -- (re)bind to the code loaded now
    return
  end
  pcall(U.prune_handlers)                     -- a new menu: the old one's item (and any closed page) is gone
  WBL = WBL or StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
  local idx = box:GetChildIndex(ng)
  local w = WBL:Create(lobby, ng:GetClass(), lobby:GetOwningPlayer())
  if not U.valid(w) then U.log("couldn't make the Multiplayer item"); return end
  local keep = slot_of(ng)
  local tail = {}
  for i = box:GetChildrenCount() - 1, idx + 1, -1 do
    local c = box:GetChildAt(i)
    table.insert(tail, 1, { w = c, s = slot_of(c) })
    box:RemoveChildAt(i)
  end
  add(box, w, keep)
  for _, t in ipairs(tail) do add(box, t.w, t.s) end
  w:SetButtonText(FText("Multiplayer"))
  MP.menu_button, MP.menu_lobby, MP.menu_dimmed = w, lobby, nil
  U.on_click(w.BW_Button, function() MP.menu.clicked() end)
  M.refresh()
  U.log("Multiplayer item added to the main menu")
end

-- Dimmed (the game's own dimmed look) while the program isn't running: then there's nothing to open.
function M.refresh()
  local b = MP.menu_button
  if not U.valid(b) then return end
  local alive = U.program_alive()
  if MP.menu_dimmed ~= (not alive) then
    MP.menu_dimmed = not alive
    pcall(function() b:SetButtonDimmed(not alive) end)
  end
end

function M.clicked()
  local alive = U.program_alive()
  if not alive then
    U.log("Multiplayer clicked, but the inZOI Multiplayer program isn't running")
    return
  end
  MP.armed = os.clock()                 -- the next Mods page the game opens becomes the servers page
  U.send({ t = "open" })
end

-- The game just created a main menu: add the item once it's built (next frame).
function M.on_new(w)
  ExecuteInGameThread(function()
    local ok, err = pcall(M.attach, w)
    if not ok then U.log("menu: " .. tostring(err)) end
  end)
end

function M.start()
  local lobby = FindFirstOf("WBP_Lobby_C")
  if U.valid(lobby) and U.valid(lobby.BW_WBP_Button_NewGame) then M.attach(lobby) end
end

return M
