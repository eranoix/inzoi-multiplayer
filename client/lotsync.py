"""The whole home lot, moved between the two saves with the game's own "save property" / "property presets".

Reading the lot object by object (housesync.py) misses what the game's commands can't read: wall and floor paint and
materials, floors outside rooms, roofs (their size), stairs, object customization. The game itself can carry all of
that: in build mode, the toolbar's Save Site button saves the lot as a property preset (Documents/inZOI/Creations/
MySites/<id>/: meta.json, site.dat - the lot's walls, floors, paint, roofs and every object with its customization, as
the game serializes it - and a thumbnail), and the property preset list applies one to the lot ("the preset will be
applied to the whole house"). This drives those two screens:

- save_lot: enter build mode on the lot, Save Site, type the name (the B1 script sets the text box: B1/Main.lua runs
  in/b1cmd.cfg, it ticks while build mode pauses the simulation), wake the thumbnail camera (the dialog refuses to save
  until the thumbnail was adjusted: two notches of the mouse wheel over it), Save, leave build mode; the new folder's
  files are returned (and the folder removed, so the player's own creations stay as they were).
- apply_lot: write the files as a new preset whose thumbnail has a size no other entry has (that's how the entry is
  found again: the list's entries carry no id, but each shows its thumbnail texture), enter build mode on the lot, find
  that entry (B1: the displayed entry whose texture has that size), click it, confirm "Apply", leave build mode (which
  saves the lot), remove the folder.

Everything runs in a worker thread (game calls block). Errors raise LotError; the caller falls back to housesync.
"""
import base64
import ctypes
import hashlib
import json
import logging
import random
import re
import secrets
import struct
import subprocess
import time
import uuid
import zlib
from pathlib import Path

import install

log = logging.getLogger("lotsync")

IN_DIR = install.INSTALL_DIR / "in"              # (B1/Main.lua reads in/b1cmd.cfg)
OUT_DIR = install.INSTALL_DIR / "out"
MYSITES = install.INSTALL_DIR.parents[2] / "Creations" / "MySites"     # Documents/inZOI/Creations/MySites
FILES = ("meta.json", "site.dat")
UI_W, UI_H = 2291, 1360          # (ui.get_tree reports positions in the game's render size; the window may differ)
VOLATILE = {"iD", "entityId", "ownerId", "childCharId", "authorName", "AuthorName", "roofs"}


class LotError(Exception):
    pass


# ---------------------------------------------------------------- site.dat
def lot_content(site_dat: bytes) -> tuple[dict, dict]:
    """site.dat -> (built data, objects): [u32 size][u32 packed size][zlib: small header, built JSON, objects JSON]."""
    size, packed = struct.unpack_from("<II", site_dat, 0)
    raw = zlib.decompress(site_dat[8:8 + packed])
    text = raw[raw.index(b"{"):].decode("utf-8", "replace")
    dec = json.JSONDecoder()
    built, end = dec.raw_decode(text)
    objs, _ = dec.raw_decode(text, text.index('{"objects"', end))
    return built, objs


def lot_hash(site_dat: bytes) -> str:
    """The same house gives the same hash in both saves (entity ids and authors differ, roof outlines are a cache)."""
    def clean(v):
        if isinstance(v, dict):
            return {k: clean(x) for k, x in sorted(v.items()) if k not in VOLATILE}
        if isinstance(v, list):
            items = [clean(x) for x in v]
            if items and all(isinstance(x, dict) for x in items):     # (the game writes some lists in any order)
                items.sort(key=lambda x: json.dumps(x, sort_keys=True))
            return items
        if isinstance(v, float):
            return round(v, 1)
        return v
    built, objs = lot_content(site_dat)
    flat = []
    for o in objs.get("objects") or []:
        flat.append(clean(o.get("gameObjectData") or {}))
        flat.extend(clean(c) for c in o.get("childrenGameObjectData") or [])
    flat.sort(key=lambda g: json.dumps(g, sort_keys=True))
    return hashlib.sha256(json.dumps([clean(built), flat], sort_keys=True).encode()).hexdigest()[:16]


def pack(folder: Path) -> dict:
    files = {n: base64.b64encode((folder / n).read_bytes()).decode() for n in FILES}
    thumbs = sorted(folder.glob("thumbnail*.jpg"))
    if thumbs:
        files["thumbnail1.jpg"] = base64.b64encode(thumbs[0].read_bytes()).decode()
    return files


# ---------------------------------------------------------------- game screens
class Screens:
    def __init__(self, call):
        self.call = call

    def tree(self, window: str) -> str:
        try:
            return self.call("ui.get_tree", Window=window).get("_text", "") or ""
        except Exception:                                # noqa: BLE001 (that window isn't on screen)
            return ""

    def ref(self, window: str, pattern: str) -> str | None:
        m = re.search(r'"ref":"([^"]*' + pattern + r')"', self.tree(window))
        return m.group(1) if m else None

    def windows(self) -> list[str]:
        try:
            text = self.call("ui.list_windows").get("_text", "")
            return [w["ui_type"] for w in json.loads(text.split("\n", 1)[1])["windows"]]
        except Exception:                                # noqa: BLE001
            return []

    def wait(self, check, timeout: float, every: float = 0.4):
        end = time.time() + timeout
        while time.time() < end:
            v = check()
            if v:
                return v
            time.sleep(every)
        return None

    def build_mode(self, site_id: str):
        st = self.call("build.get_mode_state")
        if st.get("in_build_mode") and st.get("site_id") != site_id:
            self.leave_build_mode()
        if not self.call("build.get_mode_state").get("in_build_mode"):
            self.call("build.enter_mode", site_id=site_id)
        if not self.wait(lambda: self.call("build.get_mode_state").get("phase") == "ready", 60, 0.5):
            raise LotError("build mode didn't open")
        self.wait(lambda: "BuildModeToolbar" in self.windows(), 10)

    def leave_build_mode(self):
        try:
            for _ in range(3):
                if self.call("build.get_mode_state").get("phase") == "none":
                    return
                self.call("build.exit_mode")
                if self.wait(lambda: self.call("build.get_mode_state").get("phase") == "none", 30, 0.5):
                    return
        except Exception as e:                           # noqa: BLE001
            log.warning("leaving build mode: %s", e)

    def b1(self, code: str, timeout: float = 8.0) -> str:
        """Run a Lua chunk in the client (B1/Main.lua's mailbox); -> what it returned. Raises LotError on error."""
        cid = uuid.uuid4().hex[:10]
        out = OUT_DIR / "b1.cfg"
        IN_DIR.mkdir(parents=True, exist_ok=True)
        (IN_DIR / "b1cmd.cfg").write_text(cid + code.encode("utf-8").hex(), "utf-8")      # (hex only)
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.25)
            try:
                text = out.read_text("utf-8", errors="replace")
            except OSError:
                continue
            if cid in text:
                res = text.split(cid, 1)[1].strip()
                if res.startswith("ERR "):
                    raise LotError("client script: " + res[4:200])
                return res
        raise LotError("the game's client script didn't answer (is the mod loaded?)")


def game_window():
    """The game's main window (the biggest visible "UnrealWindow"; the game has small ones too) and its client rect."""
    u = ctypes.windll.user32
    best = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(h, _):
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(ctypes.c_void_p(h), cls, 64)
        if cls.value == "UnrealWindow" and u.IsWindowVisible(ctypes.c_void_p(h)):
            r = (ctypes.c_long * 4)()
            u.GetClientRect(ctypes.c_void_p(h), r)
            if not best or r[2] * r[3] > best[1][2] * best[1][3]:
                best[:] = [h, r]
        return True
    u.EnumWindows(proto(visit), 0)
    if not best or best[1][2] < 400:
        raise LotError("game window not found")
    return ctypes.c_void_p(best[0]), best[1]


def wheel_over(x: float, y: float, notches: int = -2):
    """Turn the mouse wheel over a point of the game window (ui coordinates), then put the cursor back."""
    u = ctypes.windll.user32
    prev = None
    try:
        prev = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))     # (per-monitor v2, this thread only)
    except (AttributeError, OSError):
        pass
    try:
        h, rect = game_window()
        pt = (ctypes.c_long * 2)(0, 0)
        u.ClientToScreen(h, pt)
        sx, sy = pt[0] + x * rect[2] / UI_W, pt[1] + y * rect[3] / UI_H
        old = (ctypes.c_long * 2)()
        u.GetCursorPos(old)
        u.SetForegroundWindow(h)
        time.sleep(0.25)
        u.SetCursorPos(int(sx), int(sy))
        time.sleep(0.15)
        for _ in range(abs(notches)):
            u.mouse_event(0x0800, 0, 0, -120 if notches < 0 else 120, 0)
            time.sleep(0.12)
        time.sleep(0.2)
        u.SetCursorPos(old[0], old[1])
    finally:
        if prev:
            try:
                u.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))
            except (AttributeError, OSError):
                pass


def jpeg_sized(src: bytes, w: int, h: int, dest: Path):
    """dest = src as a JPEG of exactly w x h (Windows' own imaging, no Python packages needed)."""
    tmp = dest.with_suffix(".src.jpg")
    tmp.write_bytes(src)
    ps = ("Add-Type -AssemblyName System.Drawing; $s=[System.Drawing.Image]::FromFile('%s'); "
          "$b=New-Object System.Drawing.Bitmap %d,%d; $g=[System.Drawing.Graphics]::FromImage($b); "
          "$g.DrawImage($s,0,0,%d,%d); $g.Dispose(); $s.Dispose(); "
          "$b.Save('%s',[System.Drawing.Imaging.ImageFormat]::Jpeg); $b.Dispose()") % (tmp, w, h, w, h, dest)
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                       timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        tmp.unlink(missing_ok=True)
    if not dest.exists():
        raise LotError("couldn't write the preset's thumbnail")


SET_TITLE = '''
local arr = UE.TArray(UE.UUserWidget)
UE.UWidgetBlueprintLibrary.GetAllWidgetsOfClass(owner, arr, INZOI.UB1BuildSavePresetWidget, false)
if arr:Length() == 0 then return "no dialog" end
local w = arr:Get(1)
w.BW_EditableTextBox_Title:SetText("%s")
w:HandleTitleTextChanged("%s")
return "ok"
'''

FIND_ENTRY = '''
local arr = UE.TArray(UE.UUserWidget)
UE.UWidgetBlueprintLibrary.GetAllWidgetsOfClass(owner, arr, INZOI.UB1PresetListContainer, false)
if arr:Length() == 0 then return "no list" end
local ws = arr:Get(1).BW_TileView_Preset:GetDisplayedEntryWidgets()
local found = {}
for i = 1, ws:Length() do
    local w = ws:Get(i)
    local ok, x, y = pcall(function()
        local t = w.Image_Preset.Brush.ResourceObject
        return t:Blueprint_GetSizeX(), t:Blueprint_GetSizeY()
    end)
    if ok and x == %d and y == %d then found[#found + 1] = w:GetName() end
end
return #found == 1 and found[1] or ("found " .. #found)
'''


def save_lot(call, site_id: str, title: str) -> dict:
    """Save the lot as a property preset; -> {"files": {name: base64}, "hash"}. Leaves build mode afterwards."""
    s = Screens(call)
    MYSITES.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in MYSITES.iterdir()}
    s.build_mode(site_id)
    try:
        ref = s.ref("BuildModeToolbar", "WBP_Save_Site/BW_Button")
        if not ref:
            raise LotError("no Save Site button in build mode")
        call("ui.click", ref=ref)
        if not s.wait(lambda: "BuildSavePreset" in s.windows(), 10):
            raise LotError("the save dialog didn't open")
        time.sleep(0.8)
        if re.search(r'"text":"\(0/0\)"', s.tree("BuildSavePreset")):
            raise LotError("the game's save dialog is locked in this session (name limit 0)")
        if s.b1(SET_TITLE % (title, title)) != "ok":
            raise LotError("couldn't name the preset")
        folder = None
        for attempt in range(4):
            tree = s.tree("BuildSavePreset")
            wall = re.search(r'BW_Button_Wall/Button_Wall0","rect":\[(\d+),(\d+)', tree)
            if wall:                                     # (the thumbnail: up and right of its wireframe button)
                wheel_over(int(wall.group(1)) + 198, int(wall.group(2)) - 133)
            time.sleep(0.6)
            save = s.ref("BuildSavePreset", "BW_Button_Save/BW_Button")
            if not save:
                break
            try:
                call("ui.click", ref=save)
            except Exception:                            # noqa: BLE001 (disabled for a moment: try again)
                pass
            folder = s.wait(lambda: next((p for p in MYSITES.iterdir() if p.name not in before
                                          and (p / "site.dat").exists()), None), 6)
            if folder:
                break
        if not folder:
            raise LotError("the game didn't save the property")
        time.sleep(1.0)                                  # (files finish writing)
        files = pack(folder)
        digest = lot_hash((folder / "site.dat").read_bytes())
        _remove(folder)
        return {"files": files, "hash": digest}
    finally:
        for _ in range(3):                               # (a dialog or toast still open: close it)
            if "BuildSavePreset" not in s.windows():
                break
            cancel = s.ref("BuildSavePreset", "BW_Button_Cancel/BW_Button")
            if cancel:
                try:
                    call("ui.click", ref=cancel)
                except Exception:                        # noqa: BLE001
                    pass
            time.sleep(0.5)
        s.leave_build_mode()


def apply_lot(call, site_id: str, files: dict, title: str) -> str:
    """Apply a saved property (files as save_lot returns them) to the lot; -> its hash. Leaves build mode."""
    s = Screens(call)
    data = {n: base64.b64decode(files[n]) for n in FILES}
    digest = lot_hash(data["site.dat"])
    folder = MYSITES / uuid.uuid4().hex.upper()
    folder.mkdir(parents=True)
    try:
        meta = json.loads(data["meta.json"].decode("utf-8"))
        meta.update(Title=title, CreateTime=int(time.time()), Tags=[], SystemTags=[])
        w, h = random.randint(601, 999), random.randint(401, 799)          # (a size no other entry has)
        meta["ThumbnailFiles"] = [{"filename": "thumbnail1.jpg", "filesize": 0}]
        (folder / "site.dat").write_bytes(data["site.dat"])
        src = base64.b64decode(files["thumbnail1.jpg"]) if files.get("thumbnail1.jpg") else None
        if not src:
            raise LotError("the property came without its thumbnail")
        jpeg_sized(src, w, h, folder / "thumbnail1.jpg")
        meta["ThumbnailFiles"][0]["filesize"] = (folder / "thumbnail1.jpg").stat().st_size
        (folder / "meta.json").write_text(json.dumps(meta, indent="\t", ensure_ascii=False), "utf-8")
        s.build_mode(site_id)
        try:
            name = None
            for _ in range(10):                          # (the list fills and loads its thumbnails)
                time.sleep(0.6)
                res = s.b1(FIND_ENTRY % (w, h))
                if res.startswith("WBP_"):
                    name = res
                    break
            if not name:
                raise LotError("the property didn't show in the preset list (%s)" % res)
            entry = s.ref("BuildMode", re.escape(name) + "/BW_Button_Click")
            if not entry:
                raise LotError("the preset's entry isn't clickable")
            call("ui.click", ref=entry)
            if not s.wait(lambda: "CommonPopup" in s.windows(), 8):
                raise LotError("the apply popup didn't open")
            ok = s.ref("CommonPopup", r'WBP_Common_Button_Popup_C_\d+/BW_Button(?=","text":"(?:Aplicar|Apply)")')
            if not ok:
                tree = s.tree("CommonPopup")
                refs = re.findall(r'"ref":"([^"]*WBP_Common_Button_Popup_C_\d+/BW_Button)"', tree)
                ok = refs[-1] if refs else None          # (the confirm button is the last one)
            if not ok:
                raise LotError("no Apply button")
            call("ui.click", ref=ok)
            s.wait(lambda: "CommonPopup" not in s.windows(), 8)
            time.sleep(2.0)                              # (the lot is rebuilt)
        finally:
            s.leave_build_mode()
        return digest
    finally:
        _remove(folder)


def _remove(folder: Path):
    try:
        for f in folder.iterdir():
            f.unlink()
        folder.rmdir()
    except OSError as e:
        log.debug("removing %s: %s", folder, e)


def new_title(who: str) -> str:
    return re.sub(r"[^\w \-]", "", f"inZOI Co-op {who} {secrets.token_hex(2)}")[:60]


# ---------------------------------------------------------------- exact house on reopen
# The preset list (which entries exist, and their content) is read when the game app starts and kept for the whole
# session: a property written now is only usable after the game restarts. So a partner's house is written as a
# property right away, and applied (apply_by_title) the next time this game enters the world.
TITLE_PREFIX = "inZOI Co-op"


class NotListed(LotError):
    """The property isn't in the game's preset list yet (written while the game was running: needs a restart)."""


def write_pending(files: dict, title: str) -> str:
    """Write a received house as a property (first in the list: newest); -> its folder name."""
    data = {n: base64.b64decode(files[n]) for n in files}
    if "site.dat" not in data or "meta.json" not in data:
        raise LotError("the house came incomplete")
    folder = MYSITES / uuid.uuid4().hex.upper()
    folder.mkdir(parents=True)
    meta = json.loads(data["meta.json"].decode("utf-8"))
    meta.update(Title=title, CreateTime=int(time.time()), Tags=[], SystemTags=[])
    (folder / "site.dat").write_bytes(data["site.dat"])
    if data.get("thumbnail1.jpg"):
        (folder / "thumbnail1.jpg").write_bytes(data["thumbnail1.jpg"])
        meta["ThumbnailFiles"] = [{"filename": "thumbnail1.jpg", "filesize": len(data["thumbnail1.jpg"])}]
    (folder / "meta.json").write_text(json.dumps(meta, indent="\t", ensure_ascii=False), "utf-8")
    return folder.name


def cleanup(keep=()):
    """Remove this program's properties (title "inZOI Co-op...") except `keep` folders, and the meta.json-only
    leftovers the game writes back for properties it still had in its list."""
    if not MYSITES.exists():
        return
    for folder in MYSITES.iterdir():
        if not folder.is_dir() or folder.name in keep:
            continue
        try:
            title = json.loads((folder / "meta.json").read_text("utf-8")).get("Title") or ""
        except (OSError, ValueError):
            title = ""
        if title.startswith(TITLE_PREFIX):
            _remove(folder)


TOOLTIP_TITLE = re.compile(r'"text":"([^"]*)"')


def apply_by_title(call, site_id: str, title: str) -> bool:
    """Apply the property named `title` from the build-mode preset list to the lot. Raises NotListed if it isn't
    there. Leaves build mode (which saves the lot)."""
    s = Screens(call)
    s.build_mode(site_id)
    try:
        time.sleep(1.5)
        entries = re.findall(r'"ref":"([^"]*WBP_Build_PresetListEntry_C_\d+/BW_Button_Click)"', s.tree("BuildMode"))
        target = None
        for e in entries[:30]:                           # (user properties come first, newest first)
            seen = None
            for _ in range(2):                           # (hover: the tooltip shows the entry's title)
                try:
                    call("ui.click", ref=e, Button="right")
                except Exception:                        # noqa: BLE001
                    break
                for _ in range(6):
                    time.sleep(0.4)
                    m = TOOLTIP_TITLE.findall(s.tree("Tooltip"))
                    if m:
                        seen = m[0]
                        break
            if seen == title:
                target = e
                break
        if not target:
            raise NotListed(title)
        call("ui.click", ref=target)
        if not s.wait(lambda: "CommonPopup" in s.windows(), 8):
            raise LotError("the apply popup didn't open")
        ok = s.ref("CommonPopup", r'WBP_Common_Button_Popup_C_\d+/BW_Button(?=","text":"(?:Aplicar|Apply)")')
        if not ok:
            refs = re.findall(r'"ref":"([^"]*WBP_Common_Button_Popup_C_\d+/BW_Button)"', s.tree("CommonPopup"))
            ok = refs[-1] if refs else None
        if not ok:
            raise LotError("no Apply button")
        call("ui.click", ref=ok)
        s.wait(lambda: "CommonPopup" not in s.windows(), 8)
        time.sleep(2.5)
        return True
    finally:
        s.leave_build_mode()


def restart_game(call):
    """Save, go to the main menu, close the game and open it again (the program loads the multiplayer save)."""
    s = Screens(call)
    s.leave_build_mode()
    end, last = time.time() + 90, None
    while True:                                          # ("Cannot save right now" while loading, mid-action...)
        try:
            call("game.goto_lobby", save=True)
            break
        except Exception as e:                           # noqa: BLE001
            last = e
            if time.time() > end:
                log.warning("going to the main menu: %s", last)
                raise LotError("couldn't save and leave the world") from None
            time.sleep(3)
    if not s.wait(lambda: call("game.get_state").get("is_lobby"), 120, 1.0):
        raise LotError("couldn't save and leave the world")
    time.sleep(2)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for name in ("inZOI-Win64-Shipping.exe", "inZOI.exe"):
        subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True, creationflags=flags)
    time.sleep(8)
    import os
    os.startfile("steam://rungameid/2456740")
