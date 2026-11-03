"""The "Multiplayer" item on inZOI's main menu, and the servers page it opens.

The game doesn't load mods' UI on its main menu (UI mods mount only once a save loads), and the ModKit can't change the
menu itself, so the program draws the item: a see-through window holding just the word "Multiplayer", in the game's
size and glow, right under "New Game" (where the game leaves a gap above "Creator Mode"). The companion shows it only
while the game's main menu is up and in front (Companion.menu_loop), and clicking it opens the servers page full
screen on the game's monitor (Microsoft Edge in kiosk mode, so there are no browser bars; any browser otherwise).

Windows only. Needs Pillow for the text (bundled in the exe); without it there's no menu item, everything else works.

    python client/menu.py        # a demo: the item at the top left of the screen, click it to print
"""
import ctypes
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("menu")

TEXT = "Multiplayer"
FONT_SIZE = 30.5                # Inter SemiBold, the game's UI font, at the menu's size (in its 1920x1080 UI units)
FONTS = [Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "page" / "fonts" / "Inter-SemiBold.ttf",
         Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "seguisb.ttf"]
INK = (0, 8)            # where our text's ink starts inside the game's 41 px item box, to line up with "New Game"
GAME_EXE = "inzoi-win64-shipping.exe"

if os.name == "nt":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.LoadCursorW.restype = wintypes.HANDLE
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
    user32.SetCursor.argtypes = [wintypes.HANDLE]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.HDC,
                                           ctypes.c_void_p, wintypes.COLORREF, ctypes.c_void_p, wintypes.DWORD]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.HANDLE, wintypes.DWORD]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x8, 0x80, 0x08000000
WM_DESTROY, WM_SETCURSOR, WM_MOUSEACTIVATE = 0x2, 0x20, 0x21
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSELEAVE = 0x200, 0x201, 0x202, 0x2A3
WM_UPDATE, WM_HIDE = 0x8001, 0x8002                    # WM_APP + n: from other threads
MA_NOACTIVATE, TME_LEAVE, IDC_HAND = 3, 2, 32649
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC if os.name == "nt" else
                                                                     ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("hwndTrack", wintypes.HWND),
                ("dwHoverTime", wintypes.DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def dpi_aware():
    """Work in real screen pixels, like the game does (otherwise Windows scaling would shift the item)."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))      # per monitor v2
    except (AttributeError, OSError):
        pass


# ---------------------------------------------------------------------------------------------- the game's window
_names: dict[int, str] = {}


def _exe_name(pid: int) -> str:
    if pid not in _names:
        name = ""
        h = kernel32.OpenProcess(0x1000, False, pid)                   # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            buf, n = ctypes.create_unicode_buffer(512), wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                name = os.path.basename(buf.value).lower()
            kernel32.CloseHandle(h)
        if len(_names) > 2000:
            _names.clear()
        if not name:
            return name                          # (not kept: a lookup that failed once is tried again next time)
        _names[pid] = name
    return _names[pid]


def _pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def game_running() -> bool | None:
    """inZOI's process is running (its window may be gone for a moment, or unreadable): from the system's process list,
    which needs no access to the process. None if the list can't be read."""
    if os.name != "nt":
        return None
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)                  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:      # (INVALID_HANDLE_VALUE)
        return None
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == GAME_EXE:
                return True
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
        return False
    finally:
        kernel32.CloseHandle(snap)


def game_window():
    """The game's main window (the biggest visible one of inZOI's process), or None."""
    found = []

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd) and _exe_name(_pid(hwnd)) == GAME_EXE:
            r = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(r))
            found.append((r.right * r.bottom, hwnd))
        return True
    user32.EnumWindows(WNDENUMPROC(each), 0)
    return max(found)[1] if found else None


def client_rect(hwnd) -> tuple[int, int, int, int]:
    """Where the game draws, on the screen: (left, top, width, height)."""
    r, p = wintypes.RECT(), POINT(0, 0)
    user32.GetClientRect(hwnd, ctypes.byref(r))
    user32.ClientToScreen(hwnd, ctypes.byref(p))
    return p.x, p.y, r.right, r.bottom


def in_front(hwnd, *also) -> bool:
    """The game (or one of `also`, e.g. our item) is the window in front, and it isn't minimized."""
    fg = user32.GetForegroundWindow()
    return bool(hwnd) and not user32.IsIconic(hwnd) and fg in (hwnd, *[a for a in also if a])


def monitor_of(hwnd) -> tuple[int, int, int, int]:
    """(left, top, width, height) of the monitor the game is on."""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
    m = info.rcMonitor
    return m.left, m.top, m.right - m.left, m.bottom - m.top


# ---------------------------------------------------------------------------------------------- the menu item
def render(scale: float, hover: bool):
    """The item as premultiplied BGRA bytes: (data, width, height, (ink_x, ink_y)). White text with the menu's soft
    white glow; the glow grows on hover, like the game's own items."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
    font = None
    for path in FONTS:
        try:
            font = ImageFont.truetype(str(path), max(8.0, FONT_SIZE * scale))
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    left, top, right, bottom = font.getbbox(TEXT)
    pad = max(6, round(16 * scale))
    w, h = right - left + 2 * pad, bottom - top + 2 * pad
    ink = Image.new("L", (w, h), 0)
    ImageDraw.Draw(ink).text((pad - left, pad - top), TEXT, font=font, fill=255)
    radius, strength = (7, 1.7) if hover else (5, 1.1)
    glow = ink.filter(ImageFilter.GaussianBlur(radius * scale)).point(lambda v: min(255, int(v * strength)))
    alpha = ImageChops.lighter(ink, glow)
    alpha = alpha.point(lambda v: max(v, 1))          # never fully clear: clicks between the letters still count
    white = alpha                                      # white text and glow, premultiplied: colour = alpha
    data = Image.merge("RGBA", (white, white, white, alpha)).tobytes()
    return data, w, h, (pad, pad)


class MenuItem:
    """The see-through window with "Multiplayer" in it. Runs its own Windows message loop on a thread; place() and
    hide() can be called from anywhere. on_click() runs on that thread."""

    def __init__(self, on_click):
        self.on_click = on_click
        self.hwnd = None
        self.want = None                 # (ink_x, ink_y, scale): where the text's ink should start on the screen
        self.shown = None                # what's on screen now: (ink_x, ink_y, scale, hover)
        self.hover = self.pressed = False
        self.ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)                # keep a reference: Windows calls it
        threading.Thread(target=self._run, name="menu-item", daemon=True).start()
        self.ready.wait(5)

    def place(self, ink_x: int, ink_y: int, scale: float):
        self.want = (int(ink_x), int(ink_y), round(scale, 3))
        if self.hwnd and (self.shown or ())[:3] != self.want:
            user32.PostMessageW(self.hwnd, WM_UPDATE, 0, 0)

    def hide(self):
        self.want = None
        if self.hwnd and self.shown:
            user32.PostMessageW(self.hwnd, WM_HIDE, 0, 0)

    def _run(self):
        try:
            dpi_aware()
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinst
            wc.lpszClassName = "inZOIMultiplayerMenuItem"
            user32.RegisterClassExW(ctypes.byref(wc))
            self.hwnd = user32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                                               wc.lpszClassName, "inZOI Multiplayer", WS_POPUP, 0, 0, 1, 1,
                                               None, None, hinst, None)
        finally:
            self.ready.set()
        if not self.hwnd:
            log.warning("couldn't make the main menu item (error %d)", ctypes.get_last_error())
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _draw(self):
        want = self.want
        if not want:
            return self._hide()
        x, y, scale = want
        if self.shown == (x, y, scale, self.hover):
            return
        data, w, h, (px, py) = render(scale, self.hover)
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        dib = gdi32.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(mem, dib)
        ctypes.memmove(bits, data, len(data))
        blend = BLENDFUNCTION(0, 0, 255, 1)                           # AC_SRC_OVER, per-pixel alpha
        user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(POINT(x - px, y - py)), ctypes.byref(SIZE(w, h)),
                                   mem, ctypes.byref(POINT(0, 0)), 0, ctypes.byref(blend), 2)      # ULW_ALPHA
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(dib)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
        if not self.shown:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        self.shown = (x, y, scale, self.hover)

    def _hide(self):
        if self.shown:
            user32.ShowWindow(self.hwnd, SW_HIDE)
        self.shown, self.hover, self.pressed = None, False, False

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_UPDATE:
                self._draw()
                return 0
            if msg == WM_HIDE:
                self._hide()
                return 0
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE                    # clicking it doesn't take the keyboard from the game
            if msg == WM_SETCURSOR:
                user32.SetCursor(user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND)))
                return 1
            if msg == WM_MOUSEMOVE and not self.hover:
                self.hover = True
                tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE, hwnd, 0)
                user32.TrackMouseEvent(ctypes.byref(tme))
                self._draw()
                return 0
            if msg == WM_MOUSELEAVE:
                self.hover = self.pressed = False
                self._draw()
                return 0
            if msg == WM_LBUTTONDOWN:
                self.pressed = True
                return 0
            if msg == WM_LBUTTONUP:
                if self.pressed:
                    self.pressed = False
                    try:
                        self.on_click()
                    except Exception:                      # noqa: BLE001 - never take the window down
                        log.exception("menu item click")
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:                                  # noqa: BLE001 - an exception can't cross into Windows
            log.exception("menu item")
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def item_spot(tree: dict) -> tuple[float, float] | None:
    """Where "Multiplayer" goes in the game's UI units, from ui.get_tree of the main menu: one row under "New Game"
    (the rows are 61 units apart, the gap above the "Creator Mode" label is one free row)."""
    rects = {}
    for n in tree.get("nodes", []):
        ref = n.get("ref", "")
        for key in ("Continue", "NewGame", "BuildWorld", "CharacterStudio"):
            if ref.endswith(f"BW_WBP_Button_{key}/BW_Button") and n.get("rect"):
                rects[key] = n["rect"]
    if "NewGame" not in rects:
        return None
    x, y = rects["NewGame"][:2]
    step = (rects["CharacterStudio"][1] - rects["BuildWorld"][1]) if {"CharacterStudio", "BuildWorld"} <= rects.keys() \
        else 61
    return x + INK[0], y + step + INK[1]


# ---------------------------------------------------------------------------------------------- the servers page
class Page:
    """The servers page, full screen on the game's monitor: Microsoft Edge in kiosk mode (no browser bars) with a
    profile of its own, else whatever browser opens links. The page asks the program for news every few seconds
    (seen()), which is how the program knows it's still open: Edge may hand the window to a process of its own."""

    def __init__(self, profile: Path):
        self.profile = profile
        self.proc = None
        self.last_seen = 0.0

    def seen(self):
        self.last_seen = time.time()

    def is_open(self) -> bool:
        return time.time() - self.last_seen < 8

    def open(self, url: str, monitor: tuple[int, int, int, int] | None = None):
        if self.is_open():
            return
        edge = _edge()
        if not edge:
            os.startfile(url)                                          # noqa: S606 - the user's browser
            return
        self.close()                                                   # a page left over (e.g. the program crashed)
        x, y = (monitor or (0, 0, 0, 0))[:2]
        self.proc = subprocess.Popen(
            [edge, f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
             "--disable-features=Translate,msEdgeSidebarV2", "--disable-sync", f"--window-position={x},{y}",
             "--kiosk", url, "--edge-kiosk-type=fullscreen"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.seen()

    def close(self):
        """Close the page: every Edge process of the page's own profile (never the player's own Edge windows)."""
        self.last_seen = 0.0
        if os.name != "nt":
            return
        pids = [str(self.proc.pid)] if self.proc and self.proc.poll() is None else []
        pids += _processes_using(self.profile)
        for pid in dict.fromkeys(pids):
            subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.proc = None


def _processes_using(profile: Path) -> list[str]:
    """Process ids of the Edge processes started with this profile folder."""
    query = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { $_.CommandLine -like "
             f"'*--user-data-dir={profile}*' }} | ForEach-Object {{ $_.ProcessId }}")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", query], capture_output=True,
                             text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip().isdigit()]


def _edge() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base and (Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe").exists():
            return str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    return shutil.which("msedge")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    done = threading.Event()
    item = MenuItem(lambda: (print("clicked"), done.set()))
    item.place(62, 370, 1.0)
    print("the item is at the top left (62, 370); click it")
    done.wait(30)
