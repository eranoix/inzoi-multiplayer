"""inZOI Multiplayer, all in one program. Double-click "inZOI Multiplayer.exe": inZOI starts, with Multiplayer on its
main menu. The program has no window of its own: the servers, joining, hosting, your name and the admin console are
all on the game's Multiplayer page, and the program closes by itself once inZOI has closed.

    python client/launcher.py [--play | --host | --host-only] [--server ADDR] [--port N] [--password X]
                              [--name NAME] [--motd TEXT] [--menu-button] [-v]

--host also runs a server on this PC (the page's "Host a Server" does the same; it's remembered either way).
--host-only runs just a server, in a window of its own, on a PC that stays on (no game needed). --menu-button puts the
main menu's Multiplayer button back after its files were deleted. tools/build_release.py packages this with the
companion, the server and the mod into the exe.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "server")]
import companion  # noqa: E402
import inzoi_mp_server as mpserver  # noqa: E402
import menu  # noqa: E402
import netinfo  # noqa: E402
import firewall  # noqa: E402
import ue4ss  # noqa: E402
import updater  # noqa: E402

log = logging.getLogger("launcher")
SERVER_DATA = companion.SERVER_DATA
GAME_URL = "steam://rungameid/2456740"
DEFAULT_SERVER = os.environ.get("INZOI_MP_DEFAULT_SERVER", "100.99.177.28")   # coop-casal: Arthur's PC (Tailscale)
GAME_STARTED = False                    # this program already started inZOI (after adding the menu button)
NEVER_STARTED = 1800                    # inZOI didn't come up this long after the program started: give up


def interactive() -> bool:
    return companion.interactive()


def testing() -> bool:
    """The exe tests: no inZOI started or followed, no message boxes."""
    return bool(os.environ.get("INZOI_MP_TEST"))


# ---------------------------------------------------------------------------------------------- telling the player
def message_box(text: str, error: bool = False):
    import ctypes
    flags = (0x10 if error else 0x40) | 0x10000 | 0x40000   # MB_ICONERROR / MB_ICONINFORMATION, SETFOREGROUND, TOPMOST
    ctypes.windll.user32.MessageBoxW(None, text, "inZOI Multiplayer", flags)


def windowless() -> bool:
    """The exe without a window: messages go in message boxes."""
    return os.name == "nt" and getattr(sys, "frozen", False) and not interactive() and not testing()


def notify(text: str, error: bool = False):
    """Tell the player something, without waiting for them: in the window when there is one, else a message box."""
    log.log(logging.WARNING if error else logging.INFO, "%s", " ".join(text.split()))
    if windowless():
        threading.Thread(target=message_box, args=(text, error), daemon=True).start()
    else:
        print("\n  " + text.replace("\n", "\n  ") + "\n")


def fail(text: str):
    """The program can't go on: say why before it closes."""
    if windowless():
        message_box(text, error=True)
    else:
        print(f"\n  {text}\n")
        pause()


def pause():
    """A double-clicked exe's window closes the moment it exits: keep it open so the message can be read."""
    if getattr(sys, "frozen", False) and interactive():
        try:
            input("  Press Enter to close this window.")
        except (EOFError, KeyboardInterrupt, RuntimeError):
            pass


def quiet_streams():
    """The exe has no window: print() goes nowhere (the log file in %APPDATA%\\inzoi-mp keeps everything)."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")                               # noqa: SIM115 - for the whole run
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")                               # noqa: SIM115


def make_console():
    """Host only runs just a server, so it gets a window for its messages (started from a console, it uses that)."""
    if os.name != "nt":
        return
    import ctypes
    k32 = ctypes.windll.kernel32
    if k32.AllocConsole():
        sys.stdout = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")    # noqa: SIM115
        sys.stderr = sys.stdout
        sys.stdin = open("CONIN$", encoding="utf-8", errors="replace")                         # noqa: SIM115
    k32.SetConsoleTitleW("inZOI Multiplayer server")
    # In the classic console a click starts a text selection, and that freezes the program until Esc: turn off
    # QuickEdit (0x40), keeping ENABLE_EXTENDED_FLAGS (0x80) so the change sticks.
    stdin, mode = k32.GetStdHandle(-10), ctypes.c_uint()
    if k32.GetConsoleMode(stdin, ctypes.byref(mode)):
        k32.SetConsoleMode(stdin, (mode.value & ~0x40) | 0x80)


# ---------------------------------------------------------------------------------------------- one copy at a time
def replace_older() -> bool:
    """coop-casal: the copy already running is an older version (an old exe downloaded elsewhere, which may never
    update to this fork): close it so this one runs. True when it was closed."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:24243/status", timeout=3) as r:
            theirs = str(json.loads(r.read()).get("version") or "")
    except (OSError, ValueError, AttributeError):
        return False
    if theirs and updater.version_tuple(theirs) >= updater.version_tuple(companion.VERSION):
        return False
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    pids = {line.split()[-1] for line in out.splitlines()
            if "127.0.0.1:24243 " in line and "LISTEN" in line.upper() and line.split()[-1].isdigit()}
    pids.discard(str(os.getpid()))
    for pid in pids:
        log.info("closing the older inZOI Multiplayer running (%s, pid %s)", theirs or "before 0.8", pid)
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return bool(pids)


def single_instance(wait: float) -> bool:
    """Only one copy runs per settings folder. True: this is it. (After an update or a restart the old copy is still
    closing: wait for it.)"""
    if os.name != "nt":
        return True
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    name = "Local\\inZOIMultiplayer-" + hashlib.sha1(str(companion.CONFIG_DIR).lower().encode()).hexdigest()[:12]
    h = k32.CreateMutexW(None, False, name)
    if not h:
        return True
    single_instance.handle = h                                   # held until the program exits
    return k32.WaitForSingleObject(ctypes.c_void_p(h), int(wait * 1000)) in (0, 0x80)   # mine, or left by a crash


DESKTOP_NAME = "inZOI Co-op.exe"
OTHER_COPIES = ("inZOI-Multiplayer*.exe", "inZOI-Coop*.exe", "inZOI-Multiplayer*.zip",
                "inZOI*Co-op*.lnk", "inZOI-Coop*.lnk", "inZOI-Multiplayer*.lnk")


def settle_on_desktop(args: list[str]) -> bool:
    """coop-casal: one program to open, on the Desktop. A copy started from anywhere else (Downloads: sent over
    Taildrop) puts itself there as "inZOI Co-op.exe", starts that one and closes (True: the caller exits); the one on
    the Desktop deletes the other copies and shortcuts (Desktop, Downloads). A copy installed in
    %LOCALAPPDATA%\\Programs with a shortcut (the host's PC) stays as it is."""
    if not updater.frozen() or os.environ.get("INZOI_MP_NO_SETTLE"):
        return False
    if testing() and not os.environ.get("INZOI_MP_DESKTOP"):
        return False                                             # (the exe tests: never the real Desktop)
    exe = Path(sys.executable).resolve()
    programs = Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "Programs"
    if str(exe).lower().startswith(str(programs.resolve()).lower() + os.sep):
        return False
    desktop = companion.desktop_dir()
    home = desktop / DESKTOP_NAME
    if str(exe).lower() != str(home.resolve()).lower():
        try:
            if home.exists():
                plain_file(home)
            shutil.copy2(exe, home)
            try:
                os.remove(str(home) + ":Zone.Identifier")          # (no "downloaded from the internet" warning)
            except OSError:
                pass
            env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
            subprocess.Popen([str(home), *args, "--restarted"], env=env, close_fds=True)
            log.info("moved to the Desktop: %s (from %s)", home, exe)
            return True
        except OSError as e:
            log.warning("couldn't put the program on the Desktop: %s", e)
            return False
    places = [desktop, Path.home() / "Desktop", Path.home() / "Downloads"]
    for place in dict.fromkeys(p for p in places if p.is_dir()):
        for f in [f for pat in OTHER_COPIES for f in place.glob(pat)] + \
                 [f for d in place.iterdir() if d.is_dir() for pat in OTHER_COPIES for f in d.glob(pat)]:
            if f.is_file() and f.resolve() != exe:
                for _ in range(10):                              # (the copy that started this one may still be closing)
                    try:
                        f.unlink()
                        log.info("deleted another copy: %s", f)
                        break
                    except PermissionError:
                        time.sleep(1)
                    except OSError as e:
                        log.warning("couldn't delete %s: %s", f, e)
                        break
    old = Path.home() / "inZOI-Coop"                             # the very first install (from the source, entrar.bat)
    if (old / "entrar.bat").exists() and (old / "client" / "companion.py").exists():
        shutil.rmtree(old, ignore_errors=True)
        log.info("deleted the old install: %s", old)
    return False


def plain_file(path: Path):
    """Make a hidden or read-only file writable again before replacing it."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)        # FILE_ATTRIBUTE_NORMAL
    except (OSError, AttributeError):
        pass


def bring_up_game():
    """The program is running already (double-clicked again): show inZOI, or start it."""
    hwnd = menu.game_window() if os.name == "nt" else None
    if not hwnd:
        start_game()
        return
    import ctypes
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                               # SW_RESTORE
    user32.SetForegroundWindow(hwnd)


def relaunch(args: list[str]):
    """Start this program again (it waits for this copy to close)."""
    me = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    subprocess.Popen([*me, *args, "--restarted"], env=env, close_fds=True)


# ---------------------------------------------------------------------------------------------- hosting
def host_settings(cfg: dict, args) -> tuple[int, str, str]:
    """Your own server's port/password/motd: from the command line, else remembered, else asked once (host only)."""
    if args.port:
        cfg["host_port"] = args.port
    if args.password is not None:
        cfg["host_password"] = args.password
    if args.motd is not None:
        cfg["host_motd"] = args.motd
    if "host_password" not in cfg and interactive():
        cfg["host_password"] = companion.ask("  Pick a password so only your friends can join (or just press Enter for none): ").strip()
        print()
    cfg.setdefault("host_port", 24242)
    cfg.setdefault("host_password", "")
    cfg.setdefault("host_motd", "")
    return int(cfg["host_port"]), cfg["host_password"], cfg["host_motd"]


def show_addresses(port: int, password: str):
    at = "" if port == 24242 else f":{port}"
    _, ts_ip = netinfo.tailscale_self()                  # the IP, not the name: a renamed PC gets a new name
    lan = netinfo.lan_address()
    print("\n  Your server is running. Tell your friends to type this as the server address:\n")
    if ts_ip:
        print(f"    {ts_ip + at:<22} friends anywhere (over Tailscale: share this PC with them in Tailscale first)")
    if lan:
        print(f"    {lan + at:<22} friends in your home (on the same Wi-Fi or network)")
    if not ts_ip:
        print("\n  Friends outside your home can't reach this PC yet: install Tailscale here and share this PC with")
        print(f"  them (see the README), or forward port {port} (TCP) to this PC in your router.")
    print(f"\n  Password: {password}" if password else "\n  No password: anyone who can reach this PC can join.")
    print("  If Windows asks whether inZOI Multiplayer may use the network, click Allow.")
    print("  Keep this window open while friends play.")
    print("  (To play on this PC instead, type play and press Enter.)\n")


def watch_console(cfg: dict):
    """Host only: typing play in its window makes this PC a player again (the program comes back without a window)."""
    def run():
        while True:
            try:
                line = input()
            except (EOFError, OSError, RuntimeError):
                return
            if line.strip().lower() == "play":
                cfg["mode"] = "play"
                companion.save_config(cfg)
                relaunch(["--play"])
                logging.shutdown()
                os._exit(0)
    threading.Thread(target=run, daemon=True).start()


async def fetch_update():
    """Host only (no game): download a newer version now and then; it's applied when the program next starts."""
    while updater.frozen():
        try:
            info = await asyncio.to_thread(updater.latest, companion.VERSION)
            if info and not updater.pending(companion.VERSION):
                await asyncio.to_thread(updater.download, info)
                log.info("inZOI Multiplayer %s is downloaded: it's applied the next time you start the program",
                         info["version"])
        except (OSError, ValueError) as e:
            log.debug("update check: %s", e)
        await asyncio.sleep(2 * 3600)


# ---------------------------------------------------------------------------------------------- running
KEEP_HOSTING = False                    # host mode: the program (and the server) stays up while inZOI is closed


async def follow_game(keep: bool | None = None) -> str:
    """The program lives as long as inZOI: returns once inZOI has closed (or never came up). keep (hosting): never
    returns: the server stays up for the friend while inZOI is closed or restarting, and the program waits for the
    game to come back (closing the program's window stops it)."""
    keep = KEEP_HOSTING if keep is None else keep
    if os.name != "nt" or testing():
        await asyncio.Event().wait()
    seen, gone, start = False, 0, time.monotonic()
    up_since = None
    while True:
        await asyncio.sleep(3)
        try:
            up = await asyncio.to_thread(menu.game_window)
        except Exception as e:                               # noqa: BLE001 - never end the program over this
            log.debug("game window: %s", e)
            continue
        if up:
            if keep and seen and gone >= 30:
                log.info("inZOI is back")
                if HOST_RESUME:
                    await HOST_RESUME()                      # coop-casal: listening again for the friend
            if up_since is None:
                up_since = time.monotonic()
            seen, gone = True, 0
        elif seen:
            if up_since is not None:
                await asyncio.to_thread(note_game_session, time.monotonic() - up_since)
                up_since = None
            gone += 3
            if gone == 30 and keep:
                log.info("inZOI closed: the server stops listening (the port is closed) until inZOI is back. Start "
                         "inZOI again to play, or close this window to stop")
                if HOST_PAUSE:
                    await HOST_PAUSE()                       # coop-casal: nothing listens while inZOI is closed
            if gone >= 30 and not keep:                      # (not just a restart of the game)
                return "inZOI closed"
        elif time.monotonic() - start > NEVER_STARTED and not keep:
            return "inZOI didn't start"


HOST_PAUSE = HOST_RESUME = None         # coop-casal (host mode): stop/start the server as inZOI closes/comes back
EARLY_EXIT = 90                         # inZOI's window gone this soon after it came up: it likely crashed


def note_game_session(seconds: float):
    """coop-casal: UE4SS (the main menu's button) is a newer build than the one upstream tested. If inZOI dies
    within EARLY_EXIT seconds twice in a row while our UE4SS is in its folder, take UE4SS out (the game just closed,
    so its files are free) and don't put it back: the menu item is drawn over the game instead, as without it."""
    cfg = companion.read_config()
    if seconds >= EARLY_EXIT:
        if cfg.pop("early_exits", None):
            companion.save_config(cfg)
        return
    cfg["early_exits"] = int(cfg.get("early_exits") or 0) + 1
    log.warning("inZOI closed %.0f s after it started (%d time(s) in a row)", seconds, cfg["early_exits"])
    where = cfg.get("ingame_installed")
    if cfg["early_exits"] >= 2 and where:
        try:
            ue4ss.uninstall(Path(where))
            cfg.update(ingame_menu=False, early_exits=0)
            cfg.pop("ingame_installed", None)
            log.warning("took UE4SS out of %s: inZOI kept closing right after starting with it", where)
            notify("inZOI fechou logo depois de abrir duas vezes seguidas. O botão Multiplayer do menu (UE4SS) foi "
                   "removido por segurança; o multiplayer continua funcionando pelo painel.", error=True)
        except OSError as e:
            log.warning("couldn't take UE4SS out: %s", e)
    companion.save_config(cfg)


async def run(mode: str, cfg: dict, comp):
    # coop-casal: up 25 s = no rollback (a broken version fails within seconds; a closed game or a program closed
    # while inZOI was still starting isn't a failed start)
    asyncio.get_running_loop().call_later(25, updater.mark_healthy)
    if mode == "host-only":
        args = argparse.Namespace(host="0.0.0.0", port=cfg["host_port"], password=cfg["host_password"],
                                  motd=cfg["host_motd"], max_players=None, data=str(SERVER_DATA), hosted=False,
                                  name=cfg.get("host_name") or None, directory=bool(cfg.get("host_directory")))
        await asyncio.to_thread(firewall.ensure, args.port, cfg)    # coop-casal: friends can reach this server
        companion.save_config(cfg)
        try:
            hosted = await mpserver.start(args)
        except OSError as e:
            raise SystemExit(f"Can't host on port {args.port}: {e.strerror or e}. Is a server already running on "
                             "this PC?") from None
        show_addresses(args.port, hosted[0].mp_server.password)
        watch_console(cfg)
        asyncio.create_task(fetch_update())
        await asyncio.Event().wait()
    if mode == "host":
        try:
            await comp.start_hosting(play=True)          # host and play: the host's mods are everyone's
            show_addresses(*comp.hosting)
        except ValueError as e:
            log.warning("couldn't start your server: %s", e)
            comp.notice = f"Couldn't start your server: {e}"
    asyncio.create_task(fetch_update())                  # coop-casal: updates come quietly, applied at next start
    main = asyncio.create_task(comp.run())
    global KEEP_HOSTING, HOST_PAUSE, HOST_RESUME
    KEEP_HOSTING = mode == "host"
    if KEEP_HOSTING:
        async def pause():
            if comp.hosted:
                await comp.stop_hosting()
                cfg["mode"] = "host"                     # (stop_hosting says "play": it comes back as a host)
                companion.save_config(cfg)

        async def resume():
            if not comp.hosted:
                try:
                    await comp.start_hosting(play=True)
                    log.info("hosting again")
                except ValueError as e:
                    log.warning("couldn't host again: %s", e)
        HOST_PAUSE, HOST_RESUME = pause, resume
    game = asyncio.create_task(follow_game())
    done, _ = await asyncio.wait({main, game}, return_when=asyncio.FIRST_COMPLETED)
    if main in done:
        main.result()                                    # (it couldn't start: SystemExit says why)
        return
    log.info("%s: closing", game.result())
    hosting = cfg.get("mode")
    if comp.hosted:
        await comp.stop_hosting()                        # friends hear that the host closed the server
        cfg["mode"] = hosting                            # and it's back the next time
        companion.save_config(cfg)
    comp.shutdown()


def main():
    ap = argparse.ArgumentParser(description="inZOI Multiplayer: play together, host a server, or both")
    how = ap.add_mutually_exclusive_group()
    how.add_argument("--play", dest="mode", action="store_const", const="play", help="just play (no server here)")
    how.add_argument("--host", dest="mode", action="store_const", const="host", help="host on this PC and play")
    how.add_argument("--host-only", dest="mode", action="store_const", const="host-only",
                     help="just run a server, in a window (no game needed)")
    ap.add_argument("--server", help="a friend's server address to join")
    ap.add_argument("--port", type=int, help="the server's port (play: theirs, host: yours)")
    ap.add_argument("--password", help="the server's password (play: theirs, host: yours)")
    ap.add_argument("--name", help="your name, as friends see it")
    ap.add_argument("--motd", help="welcome message for players joining your server (host)")
    ap.add_argument("--menu-button", action="store_true",
                    help="put the Multiplayer button back on inZOI's main menu (after its files were deleted)")
    ap.add_argument("--restarted", action="store_true", help=argparse.SUPPRESS)    # (by an update: wait for the old)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    saved = companion.read_config()
    mode = args.mode or (saved.get("mode") if saved.get("mode") in ("play", "host", "host-only") else "play")
    if not args.mode and mode == "play" and (saved.get("server") or DEFAULT_SERVER) in netinfo.local_ipv4():
        mode = "host"                                     # coop-casal: the same exe on the host's PC: joining itself
    if mode == "host-only" or (mode == "host" and getattr(sys, "frozen", False)):
        make_console()                                    # (host: a window to close to stop it, now that it stays up)
    else:
        quiet_streams()
    companion.setup_logging(args.verbose)
    updater.clean_old()
    if not single_instance(20 if args.restarted else 1) and not (replace_older() and single_instance(20)):
        log.info("inZOI Multiplayer is running already: bringing inZOI up")
        if mode != "host-only":
            bring_up_game()
        return
    if updater.startup_check(companion.VERSION, [a for a in sys.argv[1:] if a != "--restarted"]):
        return                                           # coop-casal: this version kept failing: back to the last one
    waiting = updater.pending(companion.VERSION) if updater.frozen() else None
    if waiting:
        log.info("updating to %s", waiting.stem)
        try:
            updater.apply(waiting, [a for a in sys.argv[1:] if a != "--restarted"])
            return
        except OSError as e:
            log.warning("couldn't apply the update: %s", e)
    if settle_on_desktop([a for a in sys.argv[1:] if a != "--restarted"]):
        return

    if mode == "host-only":
        cfg = saved                                       # a server needs no player settings
        host_settings(cfg, args)
        cfg["mode"] = mode
        companion.save_config(cfg)
        print(f"\n  inZOI Multiplayer {companion.VERSION}: server only")
        asyncio.run(run(mode, cfg, None))
        return
    if not saved.get("name") and not args.name:
        args.name = ue4ss.steam_name()                    # friends know them by it (else the Windows user name)
    if mode == "play" and not args.server and not saved.get("server") and DEFAULT_SERVER:
        args.server, args.port = DEFAULT_SERVER, args.port or 24242
    joining = args if mode == "play" else argparse.Namespace(server=None, port=None, password=None, name=args.name)
    cfg = companion.load_config(joining, ask_server=False, ask=False)
    if mode == "host":
        host_settings(cfg, args)
    if args.menu_button:
        cfg["ingame_menu"] = True
        cfg.pop("ingame_installed", None)
    cfg["mode"] = mode
    companion.save_config(cfg)

    comp = companion.Companion(cfg)
    comp.restart_args = []                               # an update comes back the way the settings say
    companion.on_console_close(comp.shutdown)
    where = "hosting on this PC" if mode == "host" else f"server {cfg['server']}" if cfg.get("server") else "no server yet"
    log.info("inZOI Multiplayer %s - %s, your name %s", companion.VERSION, where, cfg["name"])
    setup_ingame(cfg)
    start_game()
    try:
        asyncio.run(run(mode, cfg, comp))
    except KeyboardInterrupt:
        comp.shutdown()
        time.sleep(0.5)
    updater.mark_healthy()                               # a normal exit (inZOI closed) isn't a failed start
    log.info("stopped")


# ---------------------------------------------------------------------------------------------- the game
def script_running(wait: float = 4) -> bool:
    """Our script in the running game says it's alive (it writes game.json every second)."""
    beat = companion.CONFIG_DIR / "ingame" / "game.json"
    end = time.monotonic() + wait
    while True:
        try:
            if time.time() - float(json.loads(beat.read_text("utf-8")).get("alive", 0)) < 5:
                return True
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        if time.monotonic() >= end:
            return False
        time.sleep(0.5)


def setup_ingame(cfg: dict):
    """The real "Multiplayer" button on inZOI's main menu (and the servers page inside the game) come from UE4SS, a
    script loader next to the game. Installed and kept up to date by itself every start; the first time, the player
    is told where it is and how to take it out, and inZOI restarts if it's sitting on its main menu. Deleting it
    (as that message says) is respected: it isn't put back."""
    if os.name != "nt" or not ue4ss.available() or cfg.get("ingame_menu") is False:
        return
    binaries = ue4ss.game_binaries(cfg)
    if not binaries:
        log.info("inZOI's folder not found yet: the main menu's Multiplayer button comes once inZOI has run")
        return
    where = str(binaries)
    st = ue4ss.state(binaries)
    if cfg.get("ingame_installed") == where and not (st["ue4ss"] and st["script"]):
        cfg["ingame_menu"] = False
        cfg.pop("ingame_installed", None)
        companion.save_config(cfg)
        log.info("UE4SS was taken out of %s: it stays out (start the program with --menu-button to put it back)",
                 where)
        return
    first = cfg.get("ingame_installed") != where
    try:
        st = ue4ss.install(binaries, companion.VERSION, companion.CONFIG_DIR / "ingame")
    except (OSError, RuntimeError) as e:
        if first:
            notify(f"Couldn't add the Multiplayer button to inZOI's main menu: {e}", error=True)
        else:
            log.warning("couldn't update the main menu's Multiplayer button: %s", e)
        return
    cfg.update(game_bin=where, ingame_menu=True, ingame_installed=where)
    companion.save_config(cfg)
    running = bool(menu.game_window())
    lobby = lobby_window() if running and (st.get("restart") or not script_running()) else None
    lines = []
    if first:
        lines.append("Added a \"Multiplayer\" button to inZOI's main menu. It runs on UE4SS, a free script loader "
                     f"for Unreal games, which is now in this folder:\n\n{where}\n\n"
                     "To take it out, delete the file dwmapi.dll and the folder ue4ss there.")
    if lobby:
        lines.append("inZOI restarts now so the button shows up.")
    elif first and running:
        lines.append("It shows up the next time you start inZOI.")
    if lines:
        notify("\n\n".join(lines))
    if lobby:
        restart_game(lobby)


def lobby_window() -> str | None:
    """inZOI's main menu window, if that's where the game is (a restart loses nothing there)."""
    import game as game_mod
    g = game_mod.Game()
    try:
        state = g.call("game.get_state")
        if not (isinstance(state, dict) and state.get("phase") == "lobby"):
            return None
        res = g.call("ui.list_windows")
        text = res.get("_text", "") if isinstance(res, dict) else ""
        data = json.loads(text[text.index("{"):]) if "{" in text else res
        return next((w["window"] for w in data.get("windows") or [] if w.get("ui_type") == "MainMenu"), None)
    except (game_mod.GameError, OSError, ValueError, AttributeError) as e:
        log.info("couldn't ask inZOI where it is: %s", e)
        return None


def restart_game(lobby: str):
    """inZOI loads the button when it starts: close it through its own Exit Game, then start it again."""
    import game as game_mod
    log.info("restarting inZOI so the Multiplayer button shows up")
    try:
        game_mod.Game().call("ui.click", ref=f"{lobby}/BW_WBP_Button_Exit/BW_Button", force=True)
    except (game_mod.GameError, OSError, ValueError) as e:
        log.info("couldn't restart inZOI: %s", e)
        return
    for _ in range(120):                                 # (it saves its settings and closes)
        if not menu.game_window():
            break
        time.sleep(0.5)
    else:
        log.info("inZOI didn't close")
        return
    time.sleep(2)
    start_game()


def start_game():
    """The Multiplayer button on the game's main menu needs this program running: start inZOI along with it."""
    global GAME_STARTED
    if os.name != "nt" or testing() or GAME_STARTED or menu.game_window():
        return
    GAME_STARTED = True
    os.startfile(GAME_URL)                                           # noqa: S606 - Steam starts the game
    log.info("starting inZOI")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        if e.code not in (None, 0):
            if isinstance(e.code, str):
                log.warning("%s", e.code)
                fail(e.code)
            sys.exit(e.code if isinstance(e.code, int) else 1)
    except Exception:                                     # noqa: BLE001 - show it, don't just vanish
        traceback.print_exc()
        logging.getLogger().exception("crashed")
        fail(f"Something went wrong, so inZOI Multiplayer closed (details in {companion.CONFIG_DIR / 'companion.log'}).")
        sys.exit(1)
