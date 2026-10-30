-- The Multiplayer page: inZOI's own Mods page (WBP_ModManagement), opened by the game itself and turned into the
-- servers page the moment it's built. Everything on it is the game's: the title bar and Back, the tabs, the search
-- box and filters, mod cards as server rows, the details panel, the buttons. The mod-specific controls are hidden
-- (and our rows' checkboxes are cut off from the mod code), so nothing on this page can touch the player's mods.
--
-- The program (inZOI Multiplayer) decides what's shown: it writes ingame/page.json, which is rendered whenever its
-- "rev" changes; clicks and typing go back as event files (mp_util.send).
local U = require("mp_util")
local P = {}

local WBL
local B1Button, ScrollBox
local LIST_PANEL = 1          -- BW_WidgetSwitcher_Tab: 0 = "Browse Mods" (the intro), 1 = "Manage Mods" (the list)

-- ------------------------------------------------------------------------------------------------ state
-- Kept in the global MP table so a reload of this file keeps the page it built.
MP.page = MP.page or nil
MP.pg = MP.pg or {}

local function S() return MP.pg end

function P.is_open() return U.valid(MP.page) end

-- ------------------------------------------------------------------------------------------------ building blocks
local function create(class, owner)
  WBL = WBL or StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
  return WBL:Create(owner or MP.page, class, MP.page:GetOwningPlayer())
end

-- A see-through game button (B1Button with nothing drawn): the row's click target. Set field by field: passing a
-- whole style struct to SetStyle crashes the game.
local function plain_button(name)
  B1Button = B1Button or StaticFindObject("/Script/BlueUMG.B1Button")
  local b = StaticConstructObject(B1Button, MP.page.WidgetTree, FName(name))
  local st = b.WidgetStyle
  st.Normal.DrawAs, st.Hovered.DrawAs, st.Pressed.DrawAs, st.Disabled.DrawAs = 0, 0, 0, 0
  for _, p in ipairs({ st.NormalPadding, st.PressedPadding }) do p.Left, p.Top, p.Right, p.Bottom = 0, 0, 0, 0 end
  b.IsFocusable = false
  return b
end

local function common_button(kind)
  local s = S()
  local src = kind == "fill" and s.fill_src or s.line_src
  local b = create(src:GetClass())
  pcall(function() b.ButtonType = src.ButtonType end)
  return b
end

local function set_dimmed(b, dimmed)
  if not U.valid(b) or not U.valid(b.BW_Button) then return end
  local key = b:GetAddress()
  S().dim = S().dim or {}
  if S().dim[key] == dimmed then return end
  S().dim[key] = dimmed
  b.BW_Button:SetIsEnabled(not dimmed)
  b.BW_Button:SetRenderOpacity(dimmed and 0.5 or 1)
end

-- ------------------------------------------------------------------------------------------------ turning the page
local function label_of(combo)                       -- the text above a filter: VerticalBox > ScaleBox > TextBlock
  local ok, t = pcall(function() return combo:GetParent():GetChildAt(0):GetChildAt(0) end)
  return ok and t or nil
end

function P.prepare(page)
  pcall(U.prune_handlers)                    -- the previous page's widgets are gone: drop their click handlers
  MP.page = page
  MP.pg = {}
  MP.dlg = nil
  local s = S()
  s.rev, s.rows, s.side, s.pills, s.tabs = nil, {}, {}, {}, {}
  s.title_text = U.find(page.WidgetTree.RootWidget, "B1Text_InstalledMods")
  s.checked_text = U.find(page.WidgetTree.RootWidget, "B1Text_Checked")
  s.check_icon = U.find(page.WidgetTree.RootWidget, "B1Image_Check")
  s.fill_src, s.line_src = page.BW_Button_EnableAll, page.BW_Button_ValidateAll
  s.side_box = page.BW_Button_EnableAll:GetParent()
  s.side_col = U.part(page, "BW_VerticalBox_SelectedBox")
  s.side_icon = U.find(s.side_col, "BW_Img_StorageIcon")
  s.side_sub = U.find(s.side_col, "BW_Text_Capacity")
  for _, n in ipairs({ "B1Spacer_3", "B1Spacer_4", "B1Spacer_5" }) do U.show(U.find(s.side_box, n), false) end
  s.card_class = page.BW_DragGhost:GetClass()
  local sb = page.WBP_ModSearchBox
  s.search = sb.BW_EditableText_Search
  s.combos = { sb.BW_ComboBoxString_Status, sb.BW_ComboBoxString_Validation, sb.BW_ComboBoxString_Platform,
               sb.BW_ComboBoxString_MainCategory, sb.BW_ComboBoxString_SubCategory }
  s.combo_labels = {}
  for i, c in ipairs(s.combos) do s.combo_labels[i] = label_of(c) end
  -- the mod controls: hidden, never clicked
  page.BW_ListView_ModList:SetRenderOpacity(0)
  for _, w in ipairs({ page.BW_Button_OpenFolder, page.BW_Button_EnableAll,
                       page.BW_Button_DisableAll, page.BW_Button_ValidateAll, page.BW_Button_DeleteAll,
                       page.WBP_ModSelectAll, page.BW_Button_Sorting, page.BW_Button_Filter,
                       U.find(page.BW_WidgetSwitcher_Tab, "B1ScrollBox_0") }) do
    U.show(w, false)
  end
  -- our list: a scroll box where the mod list was
  ScrollBox = ScrollBox or StaticFindObject("/Script/BlueUMG.B1ScrollBox")
  local list = StaticConstructObject(ScrollBox, page.WidgetTree, FName("MP_ServerList"))
  local slot = page.BW_VerticalBox_ModList:AddChildToVerticalBox(list)
  slot:SetSize({ SizeRule = 1, Value = 1 })
  slot:SetPadding({ Left = -32, Top = 0, Right = 0, Bottom = 0 })
  s.list = list
  -- the tabs: the page's own two tab buttons. Their own code also switches to the "Browse Mods" intro (hidden
  -- above); the list panel comes straight back on the next frame.
  for i, t in ipairs({ page.BW_TabButton_Management, page.BW_TabButton_Explore }) do
    local id = i == 1 and "public" or "private"
    U.on_click(t.BW_Button, function()
      P.event({ t = "tab", value = id })
      P.select_tab(id)
      ExecuteInGameThread(function() P.show_list() end)
    end)
    s.tabs[i] = { w = t, id = id }
  end
  -- Back still closes the page the game's way; Esc too
  U.log("servers page ready")
end

function P.select_tab(id)
  local s = S()
  for _, t in ipairs(s.tabs or {}) do
    local on = t.id == id
    U.show(t.w.BW_Image_Selected, on and "hit" or false, U.HIDDEN)
  end
  s.tab = id
end

-- The empty-mod picture and the shields only load when the game draws mod rows: take them from the page's own rows
-- (laid out, invisible, for the first frames).
function P.borrow_images(page)
  local ok, entries = pcall(function() return page.BW_ListView_ModList:GetDisplayedEntryWidgets() end)
  if not ok or not entries then return end
  local n = 0
  for i = 1, #entries do
    local card = entries[i]
    if U.valid(card) then
      local thumb = U.part(U.part(card, "BW_ModThumbnail"), "BW_Img_ModThumbnail")
      for _, img in ipairs({ thumb, U.part(card, "BW_Img_ValidationIcon") }) do
        pcall(function()
          local o = img.Brush.ResourceObject
          if U.valid(o) then U.remember_texture(o); n = n + 1 end
        end)
      end
    end
  end
end

function P.show_list()
  local sw = U.valid(MP.page) and MP.page.BW_WidgetSwitcher_Tab
  if U.valid(sw) and sw:GetActiveWidgetIndex() ~= LIST_PANEL then sw:SetActiveWidgetIndex(LIST_PANEL) end
end

-- ------------------------------------------------------------------------------------------------ rendering
local function render_header(vm)
  local s, page = S(), MP.page
  U.text(page.WBP_Common_Title.BW_Text_Title, vm.title or "Multiplayer")
  local tabs = vm.tabs or { "Public Servers", "Private Servers" }
  for i, t in ipairs(s.tabs) do U.text(t.w.BW_Text, tabs[i] or "") end
  if vm.tab and vm.tab ~= s.tab then P.select_tab(vm.tab) elseif not s.tab then P.select_tab("public") end
  U.text(s.title_text, vm.heading or "")
  U.text(U.part(page, "BW_Text_NumMods"), vm.count or "")
  U.text(U.part(page, "BW_Text_ModeSelectAllTitle"), vm.all_label or "All")
  U.text(U.part(page, "BW_Text_NumFilteredMods"), vm.all_count or "")
  P.show_list()
end

local function render_pills(vm)
  local s, page = S(), MP.page
  local row = page.BW_Button_OpenFolder:GetParent()
  local want = vm.pills or {}
  for i, p in ipairs(want) do
    local have = s.pills[i]
    if not (have and U.valid(have.w)) then
      local b = common_button("fill")
      pcall(function() b.ButtonType = 1 end)                   -- PrimaryPositive: the blue pill
      pcall(function() b.HeightOverride = 32 end)
      pcall(function() b.MinDesiredWidth = 0 end)
      local slot = row:AddChildToHorizontalBox(b)
      slot:SetPadding({ Left = i == 1 and 16 or 10, Top = 0, Right = 0, Bottom = 0 })
      slot:SetVerticalAlignment(2)
      have = { w = b }
      s.pills[i] = have
      U.on_click(b.BW_Button, function() P.event({ t = "pill", id = have.id }) end)
    end
    have.id = p.id
    U.text(have.w.BW_Text, p.text)
    if p.icon then U.icon(have.w.BW_Image_Icon, p.icon); U.show(have.w.BW_Image_Icon, "hit") end
    U.show(have.w, true)
  end
  for i = #want + 1, #s.pills do U.show(s.pills[i].w, false) end
end

local function render_filters(vm)
  local s = S()
  local fs = vm.filters or {}
  s.filter_values = s.filter_values or {}
  for i, c in ipairs(s.combos) do
    local f = fs[i]
    if U.valid(c) then
      if not f then
        U.show(c:GetParent(), false, U.HIDDEN)
      else
        U.show(c:GetParent(), "hit")
        U.text(s.combo_labels[i], f.label)
        local opts = f.options or {}
        local same = c:GetOptionCount() == #opts
        if same then
          for j, o in ipairs(opts) do
            if c:GetOptionAtIndex(j - 1):ToString() ~= o then same = false; break end
          end
        end
        if not same then
          c:ClearOptions()
          for _, o in ipairs(opts) do c:AddOption(o) end
        end
        if not c:GetIsEnabled() then c:SetIsEnabled(true) end
        local cur = c:GetSelectedOption():ToString()
        if f.value and cur ~= f.value and s.filter_values[i] ~= cur then
          c:SetSelectedOption(f.value)
        end
        s.filter_values[i] = c:GetSelectedOption():ToString()
      end
    end
  end
end

local function info_item(icon_w, text_w, item)
  if item then
    U.icon(icon_w, item.icon)
    U.text(text_w, item.text)
    U.show(icon_w, "hit"); U.show(text_w, "hit")
  else
    U.show(icon_w, false); U.show(text_w, false)
  end
end

local function render_tags(card, tags)
  local slots = { U.part(card, "BW_Tag_Premium"), U.part(card, "BW_Tag_Trial"), U.part(card, "BW_Tag_TrialEnd") }
  local style = { blue = 1, light = 2, gray = 3 }
  local used = {}
  for _, t in ipairs(tags or {}) do
    local k = style[t.style or "blue"] or 1
    local w = slots[k]
    if w and not used[k] then
      used[k] = true
      U.text(U.part(w, "B1Text_Tag"), t.text)
      U.show(w, "hit")
    end
  end
  for k, w in ipairs(slots) do if not used[k] then U.show(w, false) end end
end

local function make_row(i)
  local s = S()
  local card = create(s.card_class)
  local btn = plain_button("MP_Row_" .. i)
  btn:AddChild(card)
  local slot = s.list:AddChild(btn)
  local r = { button = btn, card = card }
  U.on_click(btn, function() if r.id then P.event({ t = "select", id = r.id }); P.mark_selected(r.id) end end)
  U.on_check(U.part(card, "BW_CheckBox"), function(on) if r.id then P.event({ t = "check", id = r.id, on = on }) end end)
  -- the mod-only parts of a card: load order arrows, drag handle, enable toggle, spinner, platform icon
  U.show(U.part(card, "BW_Button_Handle"), false, U.HIDDEN)
  for _, w in ipairs({ U.part(card, "BW_Btn_SwitchUp"), U.part(card, "BW_Btn_SwitchDown"), U.part(card, "BW_ValidatingCircle"),
                       U.part(card, "BW_HorizontalBox_ButtonActions"), U.part(card, "BW_Img_ModPlatformIcon"),
                       U.part(U.part(card, "BW_ModThumbnail"), "BW_Img_ModPlatformIcon") }) do
    U.show(w, false)
  end
  return r
end

function P.mark_selected(id)
  for _, r in ipairs(S().rows) do
    U.show(U.find(r.card, "B1Image_Selected"), r.id == id and "hit" or false)
  end
  S().selected = id
end

local function render_rows(vm)
  local s = S()
  local rows = vm.rows or {}
  for i, row in ipairs(rows) do
    local r = s.rows[i]
    if not (r and U.valid(r.card)) then r = make_row(i); s.rows[i] = r end
    r.id = row.id
    local c = r.card
    U.show(r.button, true)
    U.text(U.part(c, "BW_Text_DisplayIndex"), row.num or tostring(i))
    U.text(U.part(c, "BW_Text_ModName"), row.name)
    U.icon(U.part(U.part(c, "BW_ModThumbnail"), "BW_Img_ModThumbnail"), row.thumb or "empty_thumb")
    if row.badge then U.icon(U.part(c, "BW_Img_ValidationIcon"), row.badge); U.show(U.part(c, "BW_Img_ValidationIcon"), "hit")
    else U.show(U.part(c, "BW_Img_ValidationIcon"), false) end
    render_tags(c, row.tags)
    local info = row.info or {}
    info_item(U.part(c, "BW_Img_StorageIcon"), U.part(c, "BW_Text_Capacity"), info[1])
    info_item(U.part(c, "BW_Img_DateIcon"), U.part(c, "BW_Text_UpdateDate"), info[2])
    info_item(U.part(c, "BW_Img_VersionIcon"), U.part(c, "BW_Text_ModkitVersion"), info[3])
    U.show(U.find(c, "B1Text_Split_2"), info[2] and "hit" or false)
    U.show(U.find(c, "B1Text_Split"), info[3] and "hit" or false)
    local checked = row.checked and true or false
    if U.part(c, "BW_CheckBox"):IsChecked() ~= checked then U.part(c, "BW_CheckBox"):SetIsChecked(checked) end
    U.show(U.find(c, "B1Image_Selected"), row.selected and "hit" or false)
    if row.selected then s.selected = row.id end
  end
  for i = #rows + 1, #s.rows do U.show(s.rows[i].button, false); s.rows[i].id = nil end
  -- empty list: the page's own "No results found." view
  local page = MP.page
  if #rows == 0 then
    U.part(page, "BW_WidgetSwitcher_ModList"):SetActiveWidgetIndex(2)
    U.text(U.find(U.part(page, "BW_VerticalBox_NoFilteredMod"), "B1TextBlock_109"), vm.empty or "No servers.")
    U.show(U.part(page, "BW_Button_ResetFilter"), vm.empty_reset and true or false)
  elseif U.part(page, "BW_WidgetSwitcher_ModList"):GetActiveWidgetIndex() ~= 0 then
    U.part(page, "BW_WidgetSwitcher_ModList"):SetActiveWidgetIndex(0)
  end
end

local function render_details(vm)
  local panel = U.part(MP.page, "BW_Mod_PreviewPanel")
  local d = vm.details
  local sw = U.part(panel, "BW_WidgetSwitcher_State")
  if not d then
    if sw:GetActiveWidgetIndex() ~= 1 then sw:SetActiveWidgetIndex(1) end
    U.text(U.find(panel, "B1TextBlock_68"), vm.details_empty or "Select a server on the left to see it here.")
    return
  end
  if sw:GetActiveWidgetIndex() ~= 0 then sw:SetActiveWidgetIndex(0) end
  local v = U.part(panel, "BW_ModValidationState")
  if d.status then
    U.icon(U.part(v, "BW_Img_ValidationIcon"), d.status.icon or "shield_ok")
    U.text(U.part(v, "BW_Text_ValidationText"), d.status.text)
    U.show(U.part(v, "BW_Img_ValidationIcon"), "hit"); U.show(U.part(v, "BW_Text_ValidationText"), "hit")
    U.text(U.part(v, "BW_Text_ValidationError"), d.status.more or "")
    U.show(U.part(v, "BW_Text_ValidationError"), (d.status.more or "") ~= "" and "hit" or false)
    U.show(v, "hit")
  else
    U.show(v, false)
  end
  U.icon(U.part(U.part(panel, "BW_ModThumbnail"), "BW_Img_ModThumbnail"), d.thumb or "empty_thumb")
  U.show(U.part(U.part(panel, "BW_ModThumbnail"), "BW_Img_ModPlatformIcon"), false)
  U.text(U.part(panel, "BW_Text_ModName"), d.name)
  local rows = d.rows or {}
  info_item(U.part(panel, "BW_Img_AuthorIcon"), U.part(panel, "BW_Text_Author"), rows[1])
  info_item(U.part(panel, "BW_Img_StorageIcon"), U.part(panel, "BW_Text_Capacity"), rows[2])
  info_item(U.part(panel, "BW_Img_DateIcon"), U.part(panel, "BW_Text_UpdateDate"), rows[3])
  info_item(U.part(panel, "BW_Img_VersionIcon"), U.part(panel, "BW_Text_ModkitVersion"), rows[4])
  U.text(U.part(panel, "BW_Text_ModDescription"), d.desc or "")
  local cat = U.part(panel, "BW_HorizontalBox_Category")
  if d.category then
    U.text(U.part(panel, "BW_Text_MainCategory"), d.category)
    U.text(U.part(panel, "BW_Text_SubCategory"), d.subcategory or "")
    U.show(cat, "hit")
  else
    U.show(cat, false)
  end
end

local function render_side(vm)
  local s, page = S(), MP.page
  local side = vm.side or {}
  U.text(s.checked_text, side.title or "")
  U.text(U.part(page, "BW_Text_CheckedCount"), side.count or "")
  U.show(U.part(page, "BW_Button_Uncheck"), side.uncheck and true or false)
  if side.sub then
    U.icon(s.side_icon, side.sub.icon or "people")
    U.text(s.side_sub, side.sub.text)
    U.show(s.side_sub, "hit"); U.show(s.side_icon, "hit")
  else
    U.show(s.side_sub, false); U.show(s.side_icon, false)
  end
  if not s.uncheck_hooked then
    s.uncheck_hooked = true
    U.on_click(U.part(page, "BW_Button_Uncheck"), function() P.event({ t = "uncheck_all" }) end)
  end
  local want = side.buttons or {}
  s.side = s.side or {}
  for i, b in ipairs(want) do
    local have = s.side[i]
    if not (have and U.valid(have.w) and have.kind == (b.style or "line")) then
      if have and U.valid(have.w) then U.show(have.w, false) end
      local w = common_button(b.style or "line")
      pcall(function() w.BW_SizeBox_Button:SetWidthOverride(240) end)
      local slot = s.side_box:AddChildToVerticalBox(w)
      slot:SetHorizontalAlignment(2)
      slot:SetPadding({ Left = 0, Top = i == 1 and 0 or 12, Right = 0, Bottom = 0 })
      have = { w = w, kind = b.style or "line" }
      s.side[i] = have
      U.on_click(w.BW_Button, function() if have.id then P.event({ t = "button", id = have.id }) end end)
    end
    have.id = b.id
    U.text(have.w.BW_Text, b.text)
    if b.icon then U.icon(have.w.BW_Image_Icon, b.icon); U.show(have.w.BW_Image_Icon, "hit")
    else U.show(have.w.BW_Image_Icon, false, U.HIDDEN) end
    set_dimmed(have.w, b.enabled == false)
    U.show(have.w, true)
  end
  for i = #want + 1, #s.side do if U.valid(s.side[i].w) then U.show(s.side[i].w, false) end; s.side[i].id = nil end
end

local function render_footer(vm)
  local page = MP.page
  if vm.footer and vm.footer ~= "" then
    U.text(U.part(page, "BW_TextBlock_ViewModeGuide"), vm.footer)
    U.icon(U.find(U.part(page, "BW_Widget_ViewModeInfo"), "B1Image_357"), vm.footer_icon or "info")
    U.show(U.part(page, "BW_Widget_ViewModeInfo"), "hit")
  else
    U.show(U.part(page, "BW_Widget_ViewModeInfo"), false)
  end
end

local function render_dialog(vm)
  require("mp_dialog").render(MP.page, vm.dialog)
end

function P.render(vm)
  if not P.is_open() then return end
  local steps = { render_header, render_pills, render_filters, render_rows, render_details, render_side, render_footer,
                  render_dialog }
  for _, f in ipairs(steps) do
    local ok, err = pcall(f, vm)
    if not ok then U.log_limited("render: " .. tostring(err)) end      -- (re-applied every few seconds: don't flood)
  end
end

-- ------------------------------------------------------------------------------------------------ events and polling
function P.event(e) U.send(e) end

local ENFORCE_FOR, ENFORCE_SLOW = 10, 5

-- Called every tick while the page is up: news from the program, and what the player typed or picked.
function P.tick()
  local s = S()
  if not P.is_open() then
    if s.was_open then s.was_open = false; U.send({ t = "closed" }) end
    return
  end
  s.was_open = true
  P.show_list()                                -- (the tabs' own code switches to the mod intro)
  local raw = U.read(U.SIGNALS .. "page.json")
  if raw and raw ~= s.raw and raw ~= s.bad_raw then
    local vm, err = U.json.decode(raw)
    if vm then
      s.raw = raw
      s.vm = vm
      s.bad_raw = nil
      s.changed = os.clock()
      P.render(vm)
    else
      s.bad_raw = raw                           -- (not decoded again every tick until the program rewrites it)
      U.log_limited("page.json: " .. tostring(err))
    end
  elseif s.vm and (s.enforce or 0) < os.clock() then
    -- Unchanged: the page's own code may still be setting itself up, so re-apply, every second for a while after
    -- the page opened or changed, then only every ENFORCE_SLOW s (it's still self-healing, just cheaper).
    local settling = os.clock() - (s.changed or 0) < ENFORCE_FOR
    s.enforce = os.clock() + (settling and 1 or ENFORCE_SLOW)
    P.render(s.vm)
  end
  local ok = pcall(function()
    local q = s.search:GetText():ToString()
    if s.query == nil then s.query = q end
    if q ~= s.query then s.query = q; U.send({ t = "search", value = q }) end
    for i, c in ipairs(s.combos) do
      local v = c:GetSelectedOption():ToString()
      if s.filter_values and s.filter_values[i] ~= nil and v ~= s.filter_values[i] then
        s.filter_values[i] = v
        U.send({ t = "filter", index = i, value = v })
      end
    end
  end)
end

-- The game just created a Mods page. Right after "Multiplayer" was clicked, it becomes the servers page: hidden for
-- the frame it's being built in, turned into the servers page on the next one, then shown.
function P.on_new(w)
  if not (MP.armed and os.clock() - MP.armed < 8) then return end
  MP.armed = nil
  w.RenderOpacity = 0
  ExecuteInGameThread(function()
    local ok, err = pcall(P.prepare, w)
    if not ok then
      U.log("prepare: " .. tostring(err))
      pcall(function() w:SetRenderOpacity(1) end)
      return
    end
    U.send({ t = "opened" })
    -- a few frames with the page's own (invisible) mod list laid out, so the game loads the mod images we use
    ExecuteInGameThreadWithDelay(90, function()
      pcall(function()
        P.borrow_images(w)
        U.show(w.BW_ListView_ModList, false)
        local raw = U.read(U.SIGNALS .. "page.json")
        local vm = raw and U.json.decode(raw)
        if vm then S().raw, S().vm, S().changed = raw, vm, os.clock(); P.render(vm) end
      end)
      pcall(function() w:SetRenderOpacity(1) end)
    end)
  end)
end

return P
