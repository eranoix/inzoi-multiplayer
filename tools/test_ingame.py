"""Tests for the Multiplayer page inside the game (client/ingame.py) and the UE4SS installer (client/ue4ss.py).

The in-game page's brain runs against a stand-in for the program's calls; the installer against a stand-in game
folder. No game, no network.

    python tools/test_ingame.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# (never the real game's Mods folder: launcher.start_game installs the mod there - this repo's files, mid-session,
# under a running game: its program then put its own back and reloaded the mod)
os.environ["INZOI_MP_DOCUMENTS"] = tempfile.mkdtemp()
sys.path.insert(0, str(ROOT / "client"))
import ingame  # noqa: E402
import ue4ss  # noqa: E402

PI = "100.107.110.78:24242"
PUB = "203.0.113.5:24242"


def row(address, name, **kw):
    r = {"address": address, "name": name, "online": True, "players": 1, "max_players": 16, "version": "0.7.0",
         "mods_count": 2, "ping": 30, "password": False, "public": False, "saved": False, "names": ["Ana"],
         "check": {"verdict": "ready", "line": "Ready to join", "mods": [], "problems": [], "missing": 0}}
    r.update(kw)
    return r


class FakeProgram:
    def __init__(self):
        self.calls = []
        self.saved = [row(PI, "The Pi", saved=True, password=True, password_saved=False)]
        self.update = None
        self.public = [row(PUB, "Open World", public=True, players=3)]
        self.status = {"ok": True, "settings": {"name": "The Pi", "description": "", "motd": "hi", "public": False,
                                                "password": "", "max_players": 16},
                       "players": [{"id": "p1", "name": "Ana", "admin": True}], "known": [{"id": "p2", "name": "Bo"}],
                       "bans": [], "admins": [{"id": "p1", "name": "Ana"}], "restartable": True, "uptime": 3600,
                       "version": "0.7.0", "admin_code": "abc123"}

    async def api(self, path, body):
        self.calls.append((path, body))
        if path == "/servers/state":
            return {"name": "Ana", "version": "0.7.0", "connected": False, "current": None, "saved": self.saved,
                    "public": self.public, "directories": ["x"], "hosting": None, "admin": True, "mods": {},
                    "update": self.update}
        if path == "/servers/save":
            if not body.get("address"):
                return {"error": "Type the server's address."}
            r = row(body["address"], body.get("name") or body["address"], saved=True)
            self.saved.append(r)
            return {"ok": True, "row": r}
        if path == "/servers/remove":
            self.saved = [r for r in self.saved if r["address"] != body["address"]]
            return {"ok": True}
        if path == "/servers/join":
            return {"ok": True}
        if path == "/servers/check":
            return {"address": body["address"]}
        if path == "/admin":
            if body["op"] == "status":
                return self.status
            if body["op"] == "set":
                self.status["settings"].update({k: v for k, v in body.items() if k not in ("op", "local")})
            return {"ok": True}
        return {"ok": True}


async def opened_tasks():
    await asyncio.sleep(0.05)


def check(cond, what):
    if not cond:
        raise AssertionError(what)
    print("  ok:", what)


async def brain_tests(tmp: Path):
    prog = FakeProgram()
    opened, toasts = [], []

    async def open_mods():
        opened.append(1)

    async def toast(text):
        toasts.append(text)

    g = ingame.InGame(tmp / "ingame", prog.api, open_mods, toast)
    g.heartbeat("0.7.0")
    alive = json.loads((tmp / "ingame" / "program.json").read_text())
    check(alive["version"] == "0.7.0" and abs(alive["alive"] - __import__("time").time()) < 5, "heartbeat written")
    check(not g.game_running(), "no game heartbeat yet")
    (tmp / "ingame" / "game.json").write_text(json.dumps({"alive": int(__import__("time").time())}))
    check(g.game_running(), "game heartbeat seen")

    await g.handle({"t": "open"})
    check(opened == [1] and g.open, "open: the game is asked to open the Mods page")
    vm = json.loads((tmp / "ingame" / "page.json").read_text())
    check(vm["heading"] == "Public Servers" and vm["rows"][0]["name"] == "Open World", "public list shown")
    check(vm["rev"] >= 1 and len(vm["filters"]) == 5 and vm["filters"][0]["options"][0] == "All Status", "filters")
    check(vm["side"]["buttons"][0]["enabled"] is False, "no server picked: Join is dimmed")

    await g.handle({"t": "tab", "value": "private"})
    v = g.view()
    check(v["heading"] == "Private Servers" and [p["id"] for p in v["pills"]] == ["add", "host", "name"],
          "private tab, pills")
    await g.handle({"t": "select", "id": PI})
    await opened_tasks()
    v = g.view()
    check(v["rows"][0]["selected"] and v["details"]["name"] == "The Pi", "selected server's details")
    check([b["id"] for b in v["side"]["buttons"]] == ["join", "check", "edit", "remove"], "its buttons")

    # join a server with a password: the password dialog first
    await g.handle({"t": "button", "id": "join"})
    check(g.modal and g.modal["id"] == "password", "join asks for the password")
    await g.handle({"t": "dialog", "button": "join", "fields": {"password": "pw"}})
    joins = [b for p, b in prog.calls if p == "/servers/join"]
    check(joins and joins[-1]["password"] == "pw" and joins[-1]["address"] == PI, "joined with the password typed")

    # add a server: an error keeps the dialog with what was typed
    await g.handle({"t": "pill", "id": "add"})
    check(g.modal["id"] == "server" and g.modal["title"] == "Add a Server", "add-server dialog")
    await g.handle({"t": "dialog", "button": "save", "fields": {"address": "", "name": "X"}})
    check(g.modal and g.modal.get("error") and g.modal["fields"][2]["value"] == "X", "error shown, fields kept")
    await g.handle({"t": "dialog", "button": "save", "fields": {"address": "1.2.3.4", "name": "New"}})
    check(g.modal is None and g.selected["private"] == "1.2.3.4", "saved and selected")

    # your name: changed from the page
    await g.handle({"t": "pill", "id": "name"})
    check(g.modal["id"] == "name" and g.modal["fields"][0]["value"] == "Ana", "name dialog, with the name now")
    await g.handle({"t": "dialog", "button": "save", "fields": {"name": "  "}})
    check(g.modal and g.modal.get("error"), "an empty name is refused")
    await g.handle({"t": "dialog", "button": "save", "fields": {"name": " Ana  Banana "}})
    names = [b for p, b in prog.calls if p == "/settings"]
    check(g.modal is None and names == [{"name": "Ana Banana"}], "name saved")

    # an update: offered as the page opens, then downloaded and put in place from the page
    prog.update = {"version": "9.9.9", "title": "Big one", "state": "available", "done": 0, "total": 10485760,
                   "error": None, "text": "inZOI Multiplayer 9.9.9 is out (Big one). Update now?"}
    await g.handle({"t": "open"})
    check(g.modal and g.modal["id"] == "update" and g.modal["desc"].startswith("inZOI Multiplayer 9.9.9 is out"),
          "a new version: offered as the page opens")
    check(g.view()["pills"][0] == {"id": "update", "text": "Update to 9.9.9", "icon": "download"}, "an Update pill")
    check("9.9.9 is out" in g.view()["footer"], "and a word in the footer")
    await g.handle({"t": "dialog", "button": "now", "fields": {}})
    ups = [b for p, b in prog.calls if p == "/update"]
    check(ups == [{"key": "9.9.9", "action": "update"}], "Update Now: the program updates")
    prog.update.update(state="downloading", done=5242880)
    await g.refresh()
    v = g.view()
    check(v["dialog"]["id"] == "updating" and "50%" in v["dialog"]["desc"] and not v["dialog"]["buttons"]
          and v["pills"][0]["text"] == "Updating... 50%", "the download's progress, live")
    prog.update.update(state="error", error="GitHub can't be reached.")
    await g.refresh()
    v = g.view()
    check(v["dialog"]["title"] == "The Update Didn't Work" and "GitHub" in v["dialog"]["desc"], "what went wrong")
    await g.handle({"t": "dialog", "button": "retry", "fields": {}})
    ups = [b for p, b in prog.calls if p == "/update"]
    check(len(ups) == 2 and ups[-1]["action"] == "update" and g.modal["id"] == "updating", "Try Again")
    prog.update.update(state="available")
    await g.handle({"t": "open"})
    check(g.modal is None, "offered once: then it's the pill")
    await g.handle({"t": "pill", "id": "update"})
    await g.handle({"t": "dialog", "button": "later", "fields": {}})
    ups = [b for p, b in prog.calls if p == "/update"]
    check(ups[-1] == {"key": "9.9.9", "action": "later"} and g.modal is None, "Later")
    prog.update = None
    await g.refresh()

    # search and filters
    await g.handle({"t": "search", "value": "new"})
    check([r["name"] for r in g.view()["rows"]] == ["New"], "search")
    await g.handle({"t": "search", "value": ""})
    await g.handle({"t": "filter", "index": 4, "value": "Password"})
    check([r["name"] for r in g.view()["rows"]] == ["The Pi"], "access filter")
    await g.handle({"t": "reset"})
    check(len(g.view()["rows"]) == 2, "reset")

    # remove with a confirmation
    await g.handle({"t": "select", "id": "1.2.3.4"})
    await g.handle({"t": "button", "id": "remove"})
    check(g.modal["id"] == "remove", "remove asks first")
    await g.handle({"t": "dialog", "button": "remove", "fields": {}})
    check(not any(r["address"] == "1.2.3.4" for r in prog.saved), "removed")

    # checked rows: bulk buttons
    await g.handle({"t": "check", "id": PI, "on": True})
    side = g.view()["side"]
    check(side["title"] == "Checked" and side["count"] == "1" and side["uncheck"], "checked: bulk view")
    await g.handle({"t": "uncheck_all"})
    check(g.view()["side"]["title"] == "Selected", "unchecked (nothing selected after the remove)")

    # the admin console: settings saved through the server's "set"
    await g.console(False)
    check(g.modal["id"] == "console" and "abc123" in g.modal["desc"], "console shows the server")
    await g.handle({"t": "dialog", "button": "c_settings", "fields": {}})
    check(g.modal["id"] == "c_settings" and g.modal["fields"][2]["value"] == "hi", "settings dialog filled in")
    await g.handle({"t": "dialog", "button": "save", "fields": {"name": "Pi 2", "description": "", "motd": "yo",
                                                                "password": "", "max_players": "8", "public": True}})
    sets = [b for p, b in prog.calls if p == "/admin" and b.get("op") == "set"]
    check(sets and sets[-1]["max_players"] == 8 and sets[-1]["public"] is True and sets[-1]["name"] == "Pi 2",
          "settings sent")
    check(g.modal["id"] == "console", "back to the console")
    await g.handle({"t": "dialog", "button": "c_players", "fields": {}})
    await g.handle({"t": "dialog", "button": "kick", "fields": {"who": "nobody here"}})
    check(g.modal["id"] == "c_players" and g.modal.get("error"), "unknown player: error")
    await g.handle({"t": "dialog", "button": "ban", "fields": {"who": "bo", "reason": "rude"}})
    bans = [b for p, b in prog.calls if p == "/admin" and b.get("op") == "ban"]
    check(bans and bans[-1]["id"] == "p2" and bans[-1]["reason"] == "rude", "ban by name")

    # events from the game, through files
    ev = tmp / "ingame" / "events"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "1-000001.json").write_text(json.dumps({"t": "tab", "value": "public"}))
    (ev / "1-000002.json").write_text(json.dumps({"t": "closed"}))
    for e in g.take_events():
        await g.handle(e)
    check(g.tab == "public" and not g.open and not list(ev.glob("*.json")), "event files read in order and removed")


def installer_tests(tmp: Path):
    if not ue4ss.available():
        print("  (skipped: run tools/build_release.py once for build/ue4ss_runtime)")
        return
    game = tmp / "game"
    game.mkdir()
    (game / ue4ss.GAME_EXE).write_bytes(b"")
    signals = tmp / "signals"
    st = ue4ss.install(game, "0.7.0", signals)
    check(st["ue4ss"] and st["ours"] and st["script"] == "0.7.0" and st["restart"], "fresh install")
    mods = (game / "ue4ss" / "Mods" / "mods.txt").read_text()
    check("InZOIMultiplayer : 1" in mods and "ConsoleEnablerMod : 0" in mods, "only our mod on")
    check((game / "ue4ss" / "Mods" / "InZOIMultiplayer" / "Scripts" / "main.lua").exists(), "script copied")
    check(not (game / "ue4ss" / "Mods" / "InZOIMultiplayer" / "Scripts" / "signals_dir.txt").exists(),
          "no test folder setting shipped")
    st = ue4ss.install(game, "0.7.0", signals)
    check(not st["changed"], "same version: nothing to do")
    st = ue4ss.install(game, "0.7.1", signals)
    check(st["changed"] and not st["restart"] and (signals / "reload").read_text() == "0.7.1", "update: reload signal")
    ue4ss.uninstall(game)
    check(not (game / "dwmapi.dll").exists() and not (game / "ue4ss").exists(), "uninstall: all ours gone")

    # the player's own UE4SS: only our mod goes in, and comes out again
    (game / "ue4ss" / "Mods").mkdir(parents=True)
    (game / "dwmapi.dll").write_bytes(b"theirs")
    (game / "ue4ss" / "UE4SS.dll").write_bytes(b"theirs")
    (game / "ue4ss" / "Mods" / "mods.txt").write_text("TheirMod : 1\n\n; Built-in keybinds, do not move up!\nKeybinds : 1\n")
    st = ue4ss.install(game, "0.7.0")
    lines = (game / "ue4ss" / "Mods" / "mods.txt").read_text().splitlines()
    check(st["ue4ss"] and not st["ours"] and (game / "dwmapi.dll").read_bytes() == b"theirs", "their UE4SS kept")
    check(lines.index("InZOIMultiplayer : 1") < lines.index("Keybinds : 1") and "TheirMod : 1" in lines,
          "our mod added before the keybinds")
    ue4ss.uninstall(game)
    check((game / "ue4ss" / "UE4SS.dll").exists() and "InZOIMultiplayer" not in
          (game / "ue4ss" / "Mods" / "mods.txt").read_text(), "uninstall leaves their UE4SS")

    # someone else's dwmapi.dll (another loader): refuse
    (game / "ue4ss" / "UE4SS.dll").unlink()
    try:
        ue4ss.install(game, "0.7.0")
        raise AssertionError("installed over another loader")
    except RuntimeError:
        print("  ok: another dwmapi.dll: refused")


def launcher_tests(tmp: Path):
    """First start with inZOI on its main menu: UE4SS goes in by itself, the player is told where, and inZOI is
    restarted through its own Exit Game (then started again). In a save it's left alone."""
    if not ue4ss.available():
        print("  (skipped: no UE4SS runtime)")
        return
    import contextlib
    import io
    os.environ["APPDATA"] = str(tmp / "appdata")
    import companion
    import game as game_mod
    import launcher
    import menu
    companion.CONFIG_DIR = tmp / "appdata" / "inzoi-mp"
    calls, started = [], []
    running = {"game": True, "phase": "lobby"}

    class FakeGame:
        def __init__(self, *a):
            pass

        def call(self, tool, **args):
            calls.append((tool, args))
            if tool == "game.get_state":
                return {"phase": running["phase"]}
            if tool == "ui.list_windows":
                return {"_text": "1 window(s).\n" + json.dumps({"windows": [{"window": "WBP_Lobby_C_1",
                                                                              "ui_type": "MainMenu"}]})}
            if tool == "ui.click":
                running["game"] = False
                return {"clicked": True}
            return {}

    cfgs = {}
    for case in ("lobby", "in_game"):
        game = tmp / f"game_{case}"
        game.mkdir()
        (game / ue4ss.GAME_EXE).write_bytes(b"")
        running.update(game=True, phase="lobby" if case == "lobby" else "in_game")
        calls.clear()
        started.clear()
        launcher.GAME_STARTED = False
        cfg = {}
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            orig = (ue4ss.game_binaries, menu.game_window, game_mod.Game, os.startfile, launcher.time.sleep,
                    companion.save_config)
            ue4ss.game_binaries = lambda cfg=None, g=game: g
            menu.game_window = lambda: 1 if running["game"] else None
            game_mod.Game = FakeGame
            os.startfile = lambda url: started.append(url)
            launcher.time.sleep = lambda s: None
            companion.save_config = lambda c: None
            try:
                launcher.setup_ingame(cfg)
                launcher.start_game()
            finally:
                (ue4ss.game_binaries, menu.game_window, game_mod.Game, os.startfile, launcher.time.sleep,
                 companion.save_config) = orig
        text = out.getvalue()
        check((game / "dwmapi.dll").exists() and cfg.get("ingame_menu") is True, f"{case}: installed without asking")
        check(str(game) in text and "dwmapi.dll" in text and "ue4ss" in text, f"{case}: told where it is")
        clicked = [a for tool, a in calls if tool == "ui.click"]
        if case == "lobby":
            check(clicked and clicked[0]["ref"].endswith("BW_WBP_Button_Exit/BW_Button") and started == [launcher.GAME_URL],
                  "on the main menu: inZOI restarted once")
        else:
            check(not clicked and not started and "next time" in text, "in a save: left alone")
        cfgs[case] = cfg
    off = {"ingame_menu": False}
    launcher.setup_ingame(off)
    check(off == {"ingame_menu": False}, "turned off: nothing happens")

    def again(case, cfg):
        out = io.StringIO()
        orig = (ue4ss.game_binaries, menu.game_window, companion.save_config)
        ue4ss.game_binaries = lambda cfg=None, g=tmp / f"game_{case}": g
        menu.game_window = lambda: None
        companion.save_config = lambda c: None
        try:
            with contextlib.redirect_stdout(out):
                launcher.setup_ingame(cfg)
        finally:
            ue4ss.game_binaries, menu.game_window, companion.save_config = orig
        return out.getvalue()
    check(again("in_game", cfgs["in_game"]) == "" and cfgs["in_game"]["ingame_menu"] is True,
          "the next start: kept up to date, nothing said")
    (tmp / "game_lobby" / "dwmapi.dll").unlink()
    again("lobby", cfgs["lobby"])
    check(cfgs["lobby"]["ingame_menu"] is False and not (tmp / "game_lobby" / "dwmapi.dll").exists(),
          "deleted by the player: it stays out")


def lifetime_tests():
    """No window: the program lives as long as inZOI. Once inZOI has closed it closes too (a host's server says
    goodbye to friends, and comes back the next time)."""
    import launcher
    import menu
    windows = iter([None, None, 7, 7] + [None] * 20)
    orig = (menu.game_window, asyncio.sleep, menu.game_running)
    real_sleep = asyncio.sleep
    menu.game_window = lambda: next(windows)
    menu.game_running = lambda: False                    # (inZOI's process gone too: never the real one's)
    asyncio.sleep = lambda s, *a: real_sleep(0)
    try:
        check(asyncio.run(launcher.follow_game()) == "inZOI closed", "inZOI closed: the program closes too")
        menu.game_window = lambda: None
        launcher.NEVER_STARTED, keep = -1, launcher.NEVER_STARTED
        check(asyncio.run(launcher.follow_game()) == "inZOI didn't start", "inZOI never came: it doesn't linger")
        launcher.NEVER_STARTED = keep
    finally:
        menu.game_window, asyncio.sleep, menu.game_running = orig

    class FakeComp:
        def __init__(self, cfg):
            self.cfg, self.hosted, self.events = cfg, None, []
            self.hosting = (24242, "")

        async def start_hosting(self, play=True):
            self.hosted = True
            self.events.append("hosting")

        async def stop_hosting(self):
            self.hosted = None
            self.cfg["mode"] = "play"
            self.events.append("stopped")

        async def run(self):
            await asyncio.Event().wait()

        def shutdown(self):
            self.events.append("shutdown")

    async def closed():
        return "inZOI closed"
    cfg = {"mode": "host"}
    comp = FakeComp(cfg)
    orig = (launcher.follow_game, launcher.show_addresses, launcher.companion.save_config)
    launcher.follow_game, launcher.show_addresses, launcher.companion.save_config = closed, lambda *a: None, \
        lambda c: None
    try:
        asyncio.run(launcher.run("host", cfg, comp))
    finally:
        launcher.follow_game, launcher.show_addresses, launcher.companion.save_config = orig
    check(comp.events == ["hosting", "stopped", "shutdown"] and cfg["mode"] == "host",
          "hosting: the server closes with inZOI, and comes back next time")


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        print("in-game page:")
        asyncio.run(brain_tests(tmp))
        print("installer:")
        installer_tests(tmp)
        print("first start:")
        launcher_tests(tmp)
        print("no window:")
        lifetime_tests()
    print("all in-game tests passed")


if __name__ == "__main__":
    main()
