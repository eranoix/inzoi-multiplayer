"""inZOI Multiplayer companion: runs next to the game on each player's PC.

- Installs the inZOI Multiplayer mod into the game and loads it (no Mod Kit needed).
- Publishes your Zoi to the server: its profile (looks, name, personality, voice) once, and ~5x per second where it
  is, which way it faces, what it's doing and what it's wearing.
- Mirrors everyone else: their Zois become puppets in your world (through the mod's in-game controller), appearing
  where they are, walking where they walk, using the same things, joining in on socials with your Zoi.
- Serves the in-game panel (players online, chat, join a friend) on http://127.0.0.1:24243.

    python client/companion.py [--server my-pi.local] [--port 24242] [--name Ana] [--password ...]

(Players run it through client/launcher.py, the "inZOI Multiplayer.exe", which can also host the server.)

Settings are remembered in %APPDATA%/inzoi-mp/config.json (the secret player key lives there too).
"""
import argparse
import asyncio
import collections
import datetime
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import couple
import housesync  # noqa: E402
import houses  # noqa: E402
import install  # noqa: E402
import mods  # noqa: E402
import saves  # noqa: E402
import servers  # noqa: E402
import updater  # noqa: E402
import webui  # noqa: E402
import world  # noqa: E402
from game import Game, GameError, describe_act  # noqa: E402
from lua_data import Registry  # noqa: E402
from mailbox import Mailbox  # noqa: E402
from profile import member_lua, profile_hash, read_profile  # noqa: E402
from reports import Reports  # noqa: E402

PROTOCOL = 1
VERSION = "0.9.18"
CLOCK_SLACK = 3               # game minutes the clocks may differ before the follower's is set
SLEEP_SPEED = 3                    # game speed while every player's Zoi is asleep
SLEEPING = re.compile(r"sleep|nap", re.I)  # in the action or motion of a sleeping Zoi
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "inzoi-mp"
CONFIG_FILE = CONFIG_DIR / "config.json"
SERVER_DATA = CONFIG_DIR / "server"         # a server hosted by this program keeps its data here

log = logging.getLogger("companion")


def interactive() -> bool:
    """Someone can answer questions in this window. (On Windows a redirected "NUL" input also claims to be a terminal,
    so check that it's a real console.)"""
    if not (sys.stdin and sys.stdin.isatty()):
        return False
    if os.name != "nt":
        return True
    import ctypes
    import msvcrt
    try:
        return bool(ctypes.windll.kernel32.GetConsoleMode(msvcrt.get_osfhandle(sys.stdin.fileno()),
                                                          ctypes.byref(ctypes.c_uint())))
    except (OSError, ValueError):
        return False


def ask(prompt: str) -> str:
    """input(), but the end of input counts as just pressing Enter."""
    try:
        return input(prompt)
    except EOFError:
        return ""


def save_time(entry: dict) -> float:
    """A game.list_saves entry's saved_time ("2026-10-11T05:47:05.616Z", UTC) as a timestamp."""
    try:
        return datetime.datetime.fromisoformat(entry["saved_time"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return 0.0


def save_label(entry: dict, extra: str = "") -> str:
    who = f"{entry.get('first_name', '')} {entry.get('last_name', '')}".strip()
    return f"{entry.get('slot') or who}" + (f" ({who}, {extra})" if extra else f" ({who})")


def size_text(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n < 100 * 1048576 else f"{n / 1073741824:.2f} GB" if n > 1073741824 \
        else f"{n // 1048576} MB"


def first_run_setup(cfg: dict, ask_server: bool = True):
    """Ask for what a new player must know (it can be changed later in the in-game panel)."""
    what = "Two quick questions" if ask_server else "One quick question"
    print(f"\n  Welcome to inZOI Multiplayer! {what} (change it any time on the game's Multiplayer page).\n")
    name = ask(f"  Your name, as friends will see it [{cfg.get('name') or os.environ.get('USERNAME', 'Player')}]: ").strip()
    if name:
        cfg["name"] = name
    for _ in range(5 if ask_server else 0):
        if cfg.get("server"):
            break
        addr = ask("  Server address (the friend who hosts gives it to you, e.g. 100.101.102.103): ").strip()
        if addr:
            host, _, port = addr.partition(":")
            cfg["server"] = host
            if port.isdigit():
                cfg["port"] = int(port)
    print()


_loop_errors = {}


def loop_error(where: str, e: BaseException):
    """Something unexpected in a long-running loop: logged (with the traceback at most once a minute per loop), and
    the loop carries on."""
    now = time.monotonic()
    if now - _loop_errors.get(where, -1e9) > 60:
        _loop_errors[where] = now
        log.warning("%s: unexpected error, carrying on: %r", where, e, exc_info=e)
    else:
        log.debug("%s: %r", where, e)


# ------------------------------------------------------------------ support: what the diagnostics zip carries
RECENT = collections.deque(maxlen=50)        # the latest notices and warnings/errors: {time, kind, text}
CLOCK_MODES = ("host", "catchup")            # cfg["clock_mode"]: the leader's clock is everyone's / furthest ahead wins
SECRET = re.compile(r"password|secret|token|^key$|admin_code", re.I)     # config keys left out of the diagnostics


class _RecentErrors(logging.Handler):
    def emit(self, record):
        try:
            RECENT.append({"time": record.created, "kind": record.levelname.lower(),
                           "text": f"{record.name}: {record.getMessage()}"[:500]})
        except Exception:                            # noqa: BLE001 - logging must never fail
            pass


if not any(isinstance(h, _RecentErrors) for h in logging.getLogger().handlers):
    logging.getLogger().addHandler(_RecentErrors(logging.WARNING))


def redact(v):
    """A copy of the settings without passwords and keys (they'd be in the diagnostics zip)."""
    if isinstance(v, dict):
        return {k: ("***" if SECRET.search(str(k)) and isinstance(x, str) and x else redact(x)) for k, x in v.items()}
    return [redact(x) for x in v] if isinstance(v, list) else v


def desktop_dir() -> Path:
    """The real Desktop (it can be redirected, e.g. into OneDrive). Tests set INZOI_MP_DESKTOP."""
    if os.environ.get("INZOI_MP_DESKTOP"):
        return Path(os.environ["INZOI_MP_DESKTOP"])
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:     # CSIDL_DESKTOPDIRECTORY
                return Path(buf.value)
        except (OSError, AttributeError):
            pass
    return Path.home() / "Desktop"


def tail_bytes(f: Path, limit: int) -> bytes:
    with open(f, "rb") as fh:
        size = fh.seek(0, 2)
        fh.seek(max(0, size - limit))
        return fh.read()


def write_diagnostics(status: dict, cfg: dict, extra: dict[str, str] | None = None, folder: Path | None = None) -> Path:
    """inzoi-coop-diagnostico-<date>.zip on the Desktop: the program's logs, the in-game mod's reports and mailbox,
    the game's and UE4SS's logs if found, the settings (no passwords or keys) and a status snapshot."""
    import zipfile
    folder = folder or desktop_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"inzoi-coop-diagnostico-{time.strftime('%Y-%m-%d_%H%M%S')}.zip"
    files = [(f, f"logs/{f.name}", 8 << 20) for f in sorted(CONFIG_DIR.glob("*.log*"))]
    files += [(f, f"logs/{f.name}", 1 << 20) for f in (getattr(updater, "DIR", CONFIG_DIR / "update") / "health.json",)]
    out_dir = install.INSTALL_DIR / "out"
    files += [(f, f"mod/out/{f.name}", 1 << 20) for f in sorted(out_dir.glob("*.cfg"))] if out_dir.exists() else []
    files += [(install.MAILBOX_DIR / n, f"mod/mailbox/{n}", 1 << 20) for n in ("MPCmd.lua", "MPSeq.lua", "MPBulk.lua")]
    files.append((install.INSTALL_DIR / "mod_manifest.json", "mod/mod_manifest.json", 1 << 20))
    game_logs = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BlueClient" / "Saved" / "Logs"
    if game_logs.exists():
        newest = sorted(game_logs.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)[:2]
        files += [(f, f"game/{f.name}", 4 << 20) for f in newest]
    try:
        import ue4ss
        binaries = ue4ss.game_binaries(cfg)
        if binaries:
            files += [(binaries / rel, f"game/{rel.replace('/', '_')}", 4 << 20)
                      for rel in ("UE4SS.log", "ue4ss/UE4SS.log")]
    except Exception as e:                           # noqa: BLE001 - one missing piece, not the whole zip
        log.debug("diagnostics: UE4SS log: %r", e)
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f, name, limit in files:
            try:
                if f.is_file():
                    z.writestr(name, tail_bytes(f, limit))
            except OSError as e:
                z.writestr(name + ".error.txt", str(e))
        z.writestr("config.json", json.dumps(redact(cfg), indent=1, ensure_ascii=False))
        z.writestr("status.json", json.dumps(status, indent=1, ensure_ascii=False, default=str))
        for name, text in (extra or {}).items():
            z.writestr(name, text)
    os.replace(tmp, path)
    return path


def read_config() -> dict:
    """The saved settings. A damaged file falls back to the copy kept by the save before (or to nothing): never a
    crash at start."""
    for f in (CONFIG_FILE, CONFIG_FILE.with_suffix(".json.bak")):
        try:
            cfg = json.loads(f.read_text("utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            log.warning("the settings file %s is damaged (%s)", f, e)
            continue
        if isinstance(cfg, dict):
            if f != CONFIG_FILE:
                log.warning("using the settings saved before (%s)", f)
            return cfg
    return {}


def load_config(args, ask_server: bool = True, ask: bool = True) -> dict:
    """Saved settings + command line. Asks for what's still missing when someone is there to answer (and ask).
    ask_server=False: no server needed (hosting, or one is picked on the game's Multiplayer page)."""
    cfg = read_config()
    need_server = ask_server and not cfg.get("server") and not args.server
    if (need_server or (not cfg.get("name") and not args.name)) and ask and interactive():
        first_run_setup(cfg, need_server)
    cfg.setdefault("key", secrets.token_urlsafe(24))
    for k in ("server", "port", "name", "password"):
        v = getattr(args, k, None)
        if v is not None:
            cfg[k] = v
    if ask_server:
        cfg.setdefault("server", "localhost")
    cfg.setdefault("port", 24242)
    cfg.setdefault("name", os.environ.get("USERNAME", "Player"))
    cfg.setdefault("password", "")
    cfg.pop("mailbox", None)                     # old development setting: the mailbox now lives inside the mod
    save_config(cfg)
    return cfg


def save_config(cfg: dict):
    """Atomically (temp file + rename), keeping the version before as config.json.bak."""
    tmp, bak = CONFIG_FILE.with_suffix(".json.tmp"), CONFIG_FILE.with_suffix(".json.bak")
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(cfg, indent=1))
            f.flush()
            os.fsync(f.fileno())
        for i in range(10):
            try:
                if CONFIG_FILE.exists():
                    os.replace(CONFIG_FILE, bak)
                os.replace(tmp, CONFIG_FILE)
                return
            except PermissionError:                  # (an antivirus or a reader has it open for a moment)
                if i == 9:
                    raise
                time.sleep(0.05)
    except OSError as e:
        log.warning("couldn't save the settings: %s", e)


class ProgressBar:
    """A progress bar in the program's window, redrawn in place:  Downloading mods [######------]  45%  12 of 27 MB.
    (Without a window, the log gets a line at every quarter.)"""

    def __init__(self, title: str, total: int):
        self.title, self.total, self.done, self.shown = title, max(1, total), 0, -1.0
        self.live = interactive()

    def add(self, n: int):
        self.done += n
        self.draw()

    def draw(self, final: bool = False):
        frac = min(1.0, self.done / self.total)
        step = 0.01 if self.live else 0.25
        if not final and frac - self.shown < step:
            return
        self.shown = frac
        bar = "#" * int(frac * 30) + "-" * (30 - int(frac * 30))
        line = f"  {self.title} [{bar}] {int(frac * 100):3d}%  {size_text(self.done)} of {size_text(self.total)}"
        if self.live:
            print("\r" + line, end="\n" if final else "", flush=True)
        else:
            log.info(line.strip())

    def finish(self):
        self.draw(final=True)


class Remote:
    """Another player, as seen from here."""

    def __init__(self, pid: str, name: str):
        self.pid, self.name = pid, name
        self.profile = None
        self.profile_hash = None
        self.state = None
        self.state_at = 0.0          # when their last state arrived
        self.presence = None         # why their Zoi isn't coming (menu, paused, another save...): {world, box, at}
        self.spawned = False
        self.spawn_requested = 0.0
        self.seen_at = 0.0           # when the controller's heartbeat last listed their puppet
        self.building = None         # profile hash whose puppet description is being built (build_member)
        self.hidden = False
        self.adopted = False         # their Zoi here is a member of my family (driven while they're on, not a copy)
        self.act_sig = None          # last mirrored action (json) / anim / outfit, to act on changes only
        self.anim = None
        self.outfit = None


class Companion(couple.Couple, housesync.HouseSync, servers.Servers):
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.version = VERSION
        self.game = Game()
        self.registry = Registry(self.game.lookup)
        self.mail = Mailbox(install.MAILBOX_DIR)
        self.reports = Reports(install.INSTALL_DIR / "out")
        self.remotes: dict[str, Remote] = {}
        self.fast_pos = None          # my Zoi's position as the in-game controller last read it (realtime path)
        self.last_full_state = None   # my last full state_of (city, site...) the fast positions go out with
        self.state_base = {}          # the rest of my state (action, outfit, clock...) as last sent
        self.sending_state = False    # the slow loop is sending my state (multiplayer on, in the world)
        self.cur_act = self.cur_anim = None
        self.me_id = None            # my player id on the server
        self.writer = None
        self.connected_at = 0.0
        self.n = 0                   # my position sample counter
        self.my_city = ""
        self.my_entity = None
        self.home = None             # {door, site} of my home, once known
        self.puppet_ids: dict[str, int] = {}     # player id -> puppet entity here (from the controller's heartbeat)
        self.mod = {"state": "waiting for the game", "beat": 0.0, "instance": None}
        self.chat = collections.deque(maxlen=60)
        self.notice = ""
        self.notice_code = None      # machine-readable reason for the notice (server error code, version_mismatch)
        self.despawning: dict[str, float] = {}  # puppet key -> when its despawn was asked (until the controller did it)
        self.grace = None            # task: friends' Zois stay a few seconds after the connection drops
        self.registry_lock = threading.Lock()
        self.world = "out"
        self.leader = None           # player whose clock everyone may follow (the server picks; usually the host)
        self.leader_world = None     # latest {hour, minute, speed, season, weather} from the leader
        self.world_synced = 0.0      # when season/weather were last shared or matched
        self.my_weather = {}         # as leader: my season and weather, refreshed once a minute
        self.my_clock = None         # (hour, minute, speed, paused) of my game, refreshed every second
        self.sleeping = False        # my Zoi is asleep
        self.houses: dict[str, dict] = {}       # owner player id -> shared house meta {name, by, site, updated}
        self.house_waiters: dict[str, asyncio.Future] = {}
        self.objects = {}            # entity id -> object descriptor (or None), cached
        self.sent_profile = None     # fingerprint of the profile the server has
        self.cleaning = {}           # leftover puppet key -> when we asked to remove it
        self.hosting = None          # (port, password) when this program also runs the server (launcher "Host and play")
        self.mods_local = None       # every mod on this PC (mods.scan), once scanned
        self.modset = None           # the server's {source, host, mods}: the mods everyone should have
        self.mod_offer = None        # mods this PC is missing: {key, mods, size, from, state, done, total, message}
        self.mod_share = None        # uploading my mods' files: {done, total}
        self.asking = False          # a question is waiting in the console (console_ask)
        self.console_queue = []      # questions after it: (text, on_answer)
        self.save_session = {"started": 0.0, "folder": None, "mp": None}  # the save being played (see watch_save)
        self.loading_save = None     # the save folder I asked the game to load
        self.auto_loaded = False     # the multiplayer save was loaded for this run already
        self.save_box = None         # the save question/notice the panel shows: {key, text, buttons}
        self.save_busy = False       # making the multiplayer copy / switching saves
        self.update = None           # a newer version: {version, title, state, done, total, path, error}
        self.restart_args = ["--play"]   # how the launcher started me (an update restarts the same way)
        self.world_meta = None       # the server's shared world: {version, by, time, name, files, size}, or None
        self.world_box = None        # a shared-world question for the panel: {key, text, buttons}
        self.world_busy = False      # downloading / uploading / saving the world
        self.world_job = None        # what the panel shows while busy: {what, done, total}
        self.world_waiters = {}      # reply type -> future (world_save_offer / world_save_put)
        self.world_hashes = mods.Hashes(CONFIG_DIR / "world_hashes.json")
        self.world_asked = False     # offered to make my multiplayer save the shared world (once per run)
        self.servers_init(SERVER_DATA, CONFIG_DIR / "page-browser")
        self.couple_init()
        self.house_init()                   # couple features: requests, backups, undo (couple.py)
        self.forget_shared_world()

    def forget_shared_world(self):
        """Own saves only now: a multiplayer save that was the host's shared world (downloaded by 0.8.6-0.8.8) is
        the host's game, not this player's. Forget it, so they're asked to make a copy of their own save."""
        mp = self.cfg.get("mp_save") or {}
        if self.shared_world_on() or not str(mp.get("name", "")).endswith("(shared world)"):
            return
        log.info("your multiplayer save was the host's shared world (%s): you'll make one of your own",
                 mp.get("name"))
        self.cfg["old_shared_mp_save"] = mp
        self.cfg.pop("mp_save", None)
        self.cfg.pop("world", None)
        self.cfg.pop("last_world", None)
        save_config(self.cfg)
        install.MAILBOX_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ helpers
    async def g(self, fn, *a, **kw):
        """Run a (blocking) game call off the event loop."""
        return await asyncio.to_thread(fn, *a, **kw)

    def save_cfg(self):
        save_config(self.cfg)

    def address(self) -> tuple[str, int, str]:
        """Where to connect: the server in this same program when hosting (and playing there), else the one picked."""
        if self.hosting and self.play_local:
            return "127.0.0.1", self.hosting[0], self.hosting[1]
        if self.lan_override:                            # (the picked server, found on the home network for now)
            return self.lan_override[0], self.lan_override[1], self.cfg.get("password", "")
        return self.cfg.get("server") or "", int(self.cfg.get("port") or 24242), self.cfg.get("password", "")

    async def send(self, msg: dict):
        if self.writer:
            self.writer.write((json.dumps(msg, separators=(",", ":")) + "\n").encode())
            await self.writer.drain()

    def puppet_owner(self, entity_id: int) -> str | None:
        for pid, eid in self.puppet_ids.items():
            if eid == entity_id:
                return pid
        return None

    # ------------------------------------------------------------------ server connection
    async def run(self):
        try:
            await webui.serve(self.ui_routes(), webui.page_files())
        except OSError:
            raise SystemExit("inZOI Multiplayer is already running in another window. Use that one "
                             "(or close it first).") from None
        self.tasks = [asyncio.create_task(self.keep_running(loop)) for loop in (
            self.game_loop, self.act_loop, self.mailbox_loop, self.mods_loop, self.update_loop, self.menu_loop, self.directory_loop,
            self.ingame_loop, self.lan_loop)]
        backoff = 2
        while True:
            self.wake.clear()
            if not self.address()[0]:
                self.connect_block = self.connect_block or "Not on a server yet: pick one on the servers page."
            if self.connect_block:                       # (wrong password, banned...): wait until something changes
                log.info("not connecting: %s", self.connect_block)
                await self.wake.wait()
                continue
            try:
                await self.session()
                backoff = 2
            except (OSError, asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError) as e:
                if not self.wake.is_set():
                    log.warning("server connection lost: %s", e or type(e).__name__)
                    self.notice = f"Can't reach the server ({self.address()[0]}): retrying"
            except Exception as e:                       # noqa: BLE001 - a bad message must never end the program
                loop_error("server connection", e)       # (hosting: that would close the friend's server too)
            w, self.writer, self.me_id = self.writer, None, None
            if w:
                w.close()
            if self.wake.is_set():                       # another server: these players aren't on it
                self.drop_all_remotes()
            elif not self.grace or self.grace.done():    # the same server: their Zois wait a moment for us
                self.grace = asyncio.create_task(self.drop_after_grace())
            try:
                await asyncio.wait_for(self.wake.wait(), backoff)
                backoff = 2                              # another server was picked: go now
            except asyncio.TimeoutError:
                backoff = min(backoff * 2, 30)

    async def keep_running(self, loop):
        """Run a long-running loop; if it ever stops with an unexpected error, log it and start it again."""
        while True:
            try:
                return await loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:                       # noqa: BLE001
                loop_error(loop.__name__, e)
            await asyncio.sleep(5)

    def drop_all_remotes(self):
        if self.grace and not self.grace.done():
            self.grace.cancel()
        for r in list(self.remotes.values()):
            self.drop_remote(r)
        self.remotes.clear()

    async def drop_after_grace(self, wait: float = 10):
        """Connection lost: friends' Zois stay for a few seconds (a quick Wi-Fi blip shouldn't rebuild them)."""
        await asyncio.sleep(wait)
        if not (self.writer and self.me_id):
            log.info("still not connected: friends' Zois leave your world")
            self.drop_all_remotes()

    async def session(self):
        host, port, password = self.address()
        log.info("connecting to %s:%d as %s", host, port, self.cfg["name"])
        reader, self.writer = await asyncio.wait_for(asyncio.open_connection(host, port, limit=32 * 1024 * 1024), 20)
        self.connected_at = time.time()
        self.sent_profile = None
        await self.send({"t": "hello", "protocol": PROTOCOL, "key": self.cfg["key"], "name": self.cfg["name"],
                         "password": password, "host": bool(self.hosting and self.play_local), "version": VERSION})
        while True:
            line = await asyncio.wait_for(reader.readline(), 45)     # the server pings every 15 s: silence = gone
            if not line:
                raise ConnectionError("server closed the connection")
            try:
                m = json.loads(line)
            except ValueError:
                log.warning("server sent something unreadable: %r", line[:200])
                continue
            if not isinstance(m, dict):
                continue
            try:
                await self.on_message(m)
            except (OSError, ConnectionError, asyncio.TimeoutError):
                raise                                # (the connection itself, or a fatal error from the server)
            except Exception as e:                   # noqa: BLE001 - one odd message isn't worth the connection
                loop_error(f"server message {m.get('t')!r}", e)

    async def on_message(self, m: dict):
        t = m.get("t")
        if t == "welcome":
            self.me_id = m["id"]
            self.leader, self.leader_world, self.world_synced = m.get("leader"), m.get("world"), 0.0
            self.notice, self.notice_code = "", None
            if self.grace and not self.grace.done():
                self.grace.cancel()                      # back in time: friends' Zois stay
            log.info("joined as %s; %d other player(s) online%s", m["id"], len(m["players"]),
                     f" - {m['motd']}" if m.get("motd") else "")
            here = {p.get("id") for p in m["players"]}
            for r in [r for pid, r in self.remotes.items() if pid not in here]:
                self.remotes.pop(r.pid, None)            # (left while we were away)
                self.drop_remote(r)
            updater.mark_healthy()                       # (joined a server: this version works)
            self.check_version("the server", m.get("version") or m.get("server_version"))
            for p in m["players"]:
                self.update_remote(p)
                self.check_version(p.get("name") or "a friend", p.get("version"))
            self.houses = {h["owner"]: h for h in m.get("houses") or []}
            self.modset = m.get("modset")
            self.check_mods()
            self.world_meta = m.get("world_save")
            self.adopt_server(m.get("server"), m.get("admin"))
            self.check_world()
        elif t == "server_info":
            self.adopt_server(m.get("server"), m.get("admin"))
        elif t == "admin":
            self.on_admin_answer(m)
        elif t == "world_save":
            self.world_meta = {k: v for k, v in m.items() if k != "t"}
            self.system_line(f"{m.get('by') or 'Someone'} saved the shared world")
            self.check_world()
        elif t in ("world_save_offer", "world_save_put"):
            fut = self.world_waiters.pop(t, None)
            if fut and not fut.done():
                fut.set_result(m)
        elif t == "restart_soon":
            asyncio.get_running_loop().create_task(self.on_restart_soon(m))
        elif t == "modset":
            self.modset = {k: v for k, v in m.items() if k != "t"}
            self.check_mods()
        elif t == "joined":
            log.info("%s joined", m["player"]["name"])
            self.update_remote(m["player"])
            self.system_line(f"{m['player']['name']} joined")
            self.check_version(m["player"]["name"], m["player"].get("version"))
        elif t == "rejoined":                        # the same player reconnected: their puppet stays
            self.update_remote(m["player"])
            self.check_version(m["player"].get("name") or "a friend", m["player"].get("version"))
        elif t == "left":
            log.info("%s left", m.get("name"))
            r = self.remotes.pop(m["id"], None)
            self.refresh_version_notice()
            if r:
                self.drop_remote(r)
            self.system_line(f"{m.get('name')} left")
        elif t == "profile":
            r = self.remotes.get(m["id"])
            if r:
                self.set_profile(r, m["profile"])
        elif t == "state":
            r = self.remotes.get(m["id"])
            if r:
                self.set_state(r, m["state"])
        elif t == "chat":
            await self.on_chat(m)
        elif t == "leader":
            self.leader = m.get("id")
        elif t == "house_updated":
            meta = m.get("meta") or {}
            self.houses[meta.get("owner")] = meta
            if meta.get("owner") != self.me_id:
                self.system_line(f"{meta.get('by') or 'Someone'} shared a house: {meta.get('name')}")
        elif t == "house":
            fut = self.house_waiters.pop(m.get("owner"), None)
            if fut and not fut.done():
                fut.set_result(m)
        elif t == "world":
            self.leader_world = m.get("world")
        elif t == "event":
            if m.get("kind") == "couple":
                self.on_couple_event(m)
            elif m.get("kind") == "presence":
                self.on_presence(m)
        elif t == "ping":
            await self.send({"t": "pong"})
        elif t == "kicked":
            log.warning("kicked: %s", m.get("reason"))
            if m.get("by_admin"):
                self.notice = f"You were {m.get('reason')}"
                self.system_line(self.notice)
        elif t == "error":
            log.error("server: %s%s", m.get("error"), f" ({m['code']})" if m.get("code") else "")
            self.notice, self.notice_code = f"Server: {m.get('error')}", m.get("code")     # (the page translates codes)
            if m.get("fatal"):
                err, code = str(m.get("error")), str(m.get("code") or "")
                if m.get("banned") or "password" in err or "version mismatch" in err \
                        or code in ("wrong_password", "version_mismatch", "bad_key", "banned"):
                    self.connect_block = f"{self.joining or self.address()[0]}: {err}"      # retrying won't help
                raise ConnectionError(m.get("error"))

    # ------------------------------------------------------------------ chat
    def system_line(self, text: str):
        self.chat.append({"name": "", "text": text, "time": time.time(), "system": True})

    def check_version(self, who: str, version):
        """Different versions may not understand each other fully: say so (a missing version says nothing)."""
        if version and str(version) != VERSION:
            log.warning("versions differ: you have %s, %s has %s", VERSION, who, version)
            self.notice = f"Versions differ: you have {VERSION}, {who} has {version}. Update both to the same version."
            self.notice_code = "version_differs"
        else:
            self.refresh_version_notice()

    def refresh_version_notice(self):
        """The "versions differ" notice goes once nobody online has another version (they updated, or left)."""
        if self.notice_code == "version_differs" and all(
                str(getattr(r, "version", None) or VERSION) == VERSION for r in self.remotes.values()):
            self.notice, self.notice_code = "", None

    async def on_chat(self, m: dict):
        mine = m.get("id") == self.me_id
        self.chat.append({"name": m.get("name", "?"), "text": m.get("text", ""), "time": m.get("time", time.time()),
                          "mine": mine})
        log.info("[chat] %s: %s", m.get("name"), m.get("text"))
        who = self.my_entity if mine else self.puppet_ids.get(m.get("id"))
        if who:
            try:
                await self.g(self.game.bubble, who, m.get("text", ""))
            except GameError as e:
                log.debug("bubble: %s", e)

    # ------------------------------------------------------------------ remote players -> puppets
    def update_remote(self, p: dict):
        r = self.remotes.get(p["id"]) or Remote(p["id"], p["name"])
        r.name = p["name"]
        r.version = p.get("version") or getattr(r, "version", None)
        if not r.version and p["id"] not in self.remotes:          # before 0.8 no version was sent at all
            log.warning("%s runs an old inZOI Multiplayer (0.7 or earlier): they won't show up in your game", r.name)
            self.notice = f"{r.name} is using an old version of inZOI Multiplayer. Send them the new one."
            self.notice_code = "version_differs"
        if p["id"] in self.puppet_ids and p["id"] not in self.despawning:
            r.spawned = True                 # the controller already has their puppet
        self.remotes[p["id"]] = r
        if p.get("profile"):
            self.set_profile(r, p["profile"])
        if p.get("state"):
            self.set_state(r, p["state"])

    def set_profile(self, r: Remote, profile: dict):
        h = profile_hash(profile)
        if h in (r.profile_hash, r.building):
            return
        r.building = h
        asyncio.get_running_loop().create_task(self.build_member(r, profile, h))

    async def build_member(self, r: Remote, profile: dict, h: str):
        """Turn a friend's profile into the puppet description the controller builds from. It asks the game about
        struct layouts (off the event loop): while the game isn't up or is loading, it tries again a bit later."""
        wait = 3
        while r.building == h and self.remotes.get(r.pid) is r:
            def build():
                with self.registry_lock:
                    return member_lua(profile, self.registry)
            try:
                lua, skipped = await asyncio.to_thread(build)
                break
            except GameError as e:
                log.debug("building %s's Zoi waits for the game: %s", r.name, e)
            except Exception as e:                       # noqa: BLE001 - a profile we can't read: say so, skip it
                log.warning("couldn't use %s's Zoi: %r", r.name, e, exc_info=e)
                r.building = None
                return
            await asyncio.sleep(wait)
            wait = min(wait * 2, 30)
        else:
            return                                       # (a newer profile came, or they left)
        if skipped:
            log.debug("profile of %s: skipped %s", r.name, skipped[:5])
        first = r.profile_hash is None
        r.profile, r.profile_hash, r.building = profile, h, None
        self.mail.set_member(r.pid, lua)
        if r.spawned and not first:      # their Zoi really changed (new look, name...): rebuild the puppet
            self.despawn(r.pid)
            r.spawned = False
        log.info("got %s's Zoi: %s %s (%s)", r.name, profile.get("first_name"), profile.get("last_name"),
                 profile.get("gender"))

    def despawn(self, key: str):
        """Ask the controller to take a puppet out (heartbeats that still list it are stale until it says it did)."""
        self.mail.op("despawn", key=key)
        self.despawning[key] = time.time()
        self.puppet_ids.pop(key, None)

    def set_state(self, r: Remote, st: dict):
        r.state, r.state_at = st, time.time()
        if not r.spawned:
            return
        if "n" in st:
            self.mail.add_point(r.pid, st["n"], st["x"], st["y"], st["z"], st.get("yaw", 0), st.get("ts"))
        if r.pid not in self.puppet_ids:
            return                       # puppet not confirmed yet; actions wait for it
        act = st.get("act")
        sig = json.dumps(act, sort_keys=True) if act else None
        if sig != r.act_sig:
            r.act_sig, r.anim = sig, None
            if act is None:
                self.mail.op("act_stop", key=r.pid)
            elif not act.get("npc"):
                op = {"bag": act["bag"], "x": st["x"], "y": st["y"]}
                if act.get("player"):
                    target = self.my_entity if act["player"] == self.me_id else self.puppet_ids.get(act["player"])
                    op["target"] = target    # None: with someone who isn't here, nothing to join in on
                elif act.get("obj"):
                    op.update({k: act[k] for k in ("obj", "ox", "oy", "oz")})
                    if st.get("anim"):
                        op["anim"] = st["anim"]
                self.learn_bag(act)              # (couple actions: the game's own interaction ids)
                if not act.get("player") or op["target"]:
                    if act.get("player") == self.me_id and not self.mirror_ok(r, act["bag"]):
                        self.ask_local(r, act["bag"], op)    # (couple.py: a popup first, then it plays here)
                    else:
                        self.mail.op("act", key=r.pid, **op)
        if act and act.get("npc") and st.get("anim") and st["anim"] != r.anim:
            r.anim = st["anim"]              # busy with someone only they have: at least show the motion
            self.mail.op("anim", key=r.pid, anim=st["anim"])
        if st.get("outfit") and st["outfit"] != r.outfit:
            r.outfit = st["outfit"]
            self.mail.op("outfit", key=r.pid, category=st["outfit"])

    def drop_remote(self, r: Remote):
        r.building = None
        if r.spawned or r.pid in self.puppet_ids:
            self.despawn(r.pid)
            r.spawned = False
        self.puppet_ids.pop(r.pid, None)
        self.mail.trails.pop(r.pid, None)

    async def manage_puppets(self):
        """Spawn puppets for players in my city, hide those who aren't."""
        if self.mod["state"] != "running":
            return                           # wait for the controller's heartbeat: it tells us which puppets exist
        for r in self.remotes.values():
            if not r.profile or not r.state:
                continue
            here = r.state.get("city") == self.my_city
            # Their Zoi may live in this save as a member of my family: that one is driven (adopt), never a copy.
            fam = await self.g(self.family_zoi, r) if here else None
            if fam and r.spawned and self.puppet_ids.get(r.pid) not in (None, fam):
                log.info("%s's Zoi lives in your family: using them instead of a copy", r.name)
                self.despawn(r.pid)                      # (the copy goes; next round the family member is adopted)
                r.spawned, r.spawn_requested = False, 0.0
                continue
            if r.spawned and here and not r.__dict__.get("house_asked") and time.time() - r.spawn_requested > 20:
                r.house_asked = True                     # (once a session: compare our home with theirs)
                asyncio.get_running_loop().create_task(self.request_house(r))
            if fam and not r.spawned and time.time() - r.spawn_requested > 8:
                st = r.state
                extra = {"home_door": self.home["door"], "home_site": self.home["site"]} if self.home else {}
                self.mail.op("adopt", key=r.pid, entity=fam, x=st["x"], y=st["y"], z=st["z"], **extra)
                r.spawned, r.spawn_requested, r.seen_at, r.hidden, r.adopted = True, time.time(), 0.0, False, True
                r.act_sig = r.anim = r.outfit = None
                log.info("%s is here: their Zoi in your family follows them now", r.name)
                continue
            if r.adopted and r.spawned and here == r.hidden:
                r.hidden = not here                      # (online elsewhere: out of sight, still only theirs)
                self.mail.op("hide", key=r.pid, on=r.hidden)
                continue
            if not r.spawned and here and time.time() - r.spawn_requested > 8:
                r.adopted = False
                tpl = await self.g(self.game.template_for, r.profile["gender"], r.profile["growth_step"])
                if tpl is None:
                    log.warning("no local %s %s Zoi to build %s's puppet on", r.profile["gender"],
                                r.profile["growth_step"], r.name)
                    continue
                extra = {"home_door": self.home["door"], "home_site": self.home["site"]} if self.home else {}
                self.mail.op("spawn", key=r.pid, template=tpl, near=self.my_entity,
                             x=r.state["x"], y=r.state["y"], z=r.state["z"], **extra)
                r.spawned, r.spawn_requested, r.seen_at = True, time.time(), 0.0
                r.act_sig = r.anim = r.outfit = None
                log.info("bringing %s's Zoi into your world", r.name)
            elif r.spawned and here == r.hidden:
                r.hidden = not here
                self.mail.op("hide", key=r.pid, on=r.hidden)

    async def sync_world(self):
        """Time is shared, but pausing isn't: only your own game stops. Everyone runs at the same speed (keep_speed),
        and a player whose clock is behind (they paused, or came in later) catches up with whoever is furthest ahead.
        Season and weather follow the leader (the host; on a Raspberry Pi, the first player online). Each save keeps
        its own day count: jumping whole days would age Zois."""
        call = self.game.call
        slow = time.time() - self.world_synced > 60           # season and weather: once a minute is plenty
        if slow:
            self.world_synced = time.time()
        leader = self.leader == self.me_id
        if leader and (slow or not self.my_weather):     # only season and weather are the leader's to give
            env = await self.g(call, "environment.get_state")
            self.my_weather = {"season": env.get("season"), "weather": env.get("weather")}
            await self.send({"t": "world", "world": self.my_weather})
        # clock_mode "catchup": the clock is everyone's, the leader's too: whoever is behind moves forward to whoever is
        # furthest ahead. Only ever forward and only within 12 h, so two players can't push each other back and forth.
        # clock_mode "host" (the default): the leader's clock is the one (follow_leader_clock); the leader never moves.
        if not self.cfg.get("sync_time", True) or self.world != "play" or not self.my_clock:
            return
        if self.clock_mode() == "host":
            if not leader:
                await self.follow_leader_clock()
        else:
            mine = self.my_clock[0] * 60 + self.my_clock[1]
            ahead = [(c[0] * 60 + c[1] - mine) % 1440 for c in self.remote_clocks()]
            behind = max([d for d in ahead if d < 720], default=0)  # more than 12 h "ahead" means behind me
            if behind > 10:
                log.info("catching up with your friends' time: %d minutes", behind)
                await self.g(call, "environment.time_skip", minutes=behind)
        w = self.leader_world
        if slow and w and not leader:
            env = await self.g(call, "environment.get_state")
            if w.get("season") and env.get("season") != w["season"]:
                log.info("matching the host's season: %s", w["season"])
                await self.g(call, "environment.change_season", season=w["season"])
            if w.get("weather") and env.get("weather") != w["weather"]:
                log.info("matching the host's weather: %s", w["weather"])
                await self.g(call, "environment.override_weather", weather=w["weather"], step=0)

    def remote_clocks(self) -> list:
        """[hour, minute] of every friend who's in the game right now (their state is fresh)."""
        return [r.state["clock"] for r in self.remotes.values()
                if r.state and r.state.get("clock") and time.time() - r.state_at < 10]

    async def keep_speed(self):
        """Game speed is everyone's: normal for all, except while every player's Zoi is asleep, when everyone's game
        runs fast until somebody wakes. Pausing stays each player's own. Alone on the server, your speed is yours.
        clock_mode "host": the others take the leader's speed instead (follow_leader_speed)."""
        if self.clock_mode() == "host" and self.leader and self.leader != self.me_id:
            return await self.follow_leader_speed()
        others = [r for r in self.remotes.values() if r.state and time.time() - r.state_at < 10]
        clock = self.my_clock
        if others and clock and clock[3] and self.cfg.get("never_pause", True)                 and not self.__dict__.get("in_build_mode"):
            # coop-casal: time never stops while the partner is on (a pause on one side breaks the sync for both);
            # build mode pauses the game by itself and is left alone
            if time.time() - self.__dict__.get("_unpaused_at", 0) > 5:
                self._unpaused_at = time.time()
                if not self.__dict__.get("_unpause_said"):
                    self._unpause_said = True
                    self.system_line("Time keeps running while your partner is online")
                log.info("the game was paused: time runs again (your partner is online)")
                await self.g(self.game.call, "environment.time_speed", speed=1)
            return
        if not others or not clock or clock[3]:              # nobody to keep in step with / I'm paused
            return
        asleep = self.sleeping and all(r.state.get("sleep") for r in others)
        want = SLEEP_SPEED if asleep else 1
        if abs(clock[2] - want) > 0.01:
            # (the game speeds itself up while its Zois sleep: set it back at most every 20 s, not a tug of war)
            if time.time() - self.__dict__.get("_speed_set_at", 0) < 20:
                return
            self._speed_set_at = time.time()
            if self.__dict__.get("_speed_said") != want:
                self._speed_said = want
                log.info("everyone's asleep: time runs fast" if asleep else
                         "game speed is normal while anyone's awake (everyone's game runs at the same speed)")
            await self.g(self.game.call, "environment.time_speed", speed=want)

    async def check_home(self):
        """Know my home's front door, so friends' puppets can come in like invited guests (also after a move)."""
        home = await self.g(self.game.home_entry, self.my_entity)
        if not home:
            site = await self.g(self.game.home_site, self.my_entity)
            known = self.cfg.get("homes", {}).get(str(site))
            home = {"site": site, "door": known} if site and known else None
        if home and home != self.home:
            self.home = home
            self.cfg.setdefault("homes", {})[str(home["site"])] = home["door"]
            save_config(self.cfg)
            log.info("your home: lot %s, front door %s", home["site"], home["door"])
            for r in self.remotes.values():
                if r.spawned:
                    self.mail.op("admit", key=r.pid, door=home["door"], site=home["site"])

    # ------------------------------------------------------------------ the mod in the game
    async def ensure_mod(self):
        """Install the mod and make sure its in-game controller is running (heartbeat seen recently)."""
        changed = await self.g(install.install)
        alive = time.time() - self.mod["beat"] < 6
        if alive and not changed:
            return
        if alive:
            log.info("mod updated: reloading it in the game")
            before = self.mod.get("instance")
            await self.g(install.reload, self.game.call)
            for _ in range(16):                       # a reload restarts the controller (a new instance reports)
                await asyncio.sleep(0.5)
                if self.mod.get("instance") != before:
                    return
            if self.world == "play":                  # (paused, nothing runs: the quiet check mounts it later)
                log.info("the controller didn't restart after the reload: mounting it again")
                self.mod["beat"] = 0.0
                await self.ensure_mod()
            return
        else:
            self.mod["state"] = "loading"
            log.info("loading the multiplayer mod into the game: %s", await self.g(install.mount, self.game.call))
            for _ in range(16):
                await asyncio.sleep(0.5)
                if time.time() - self.mod["beat"] < 3:
                    return
            log.info("no answer from the mod yet: reloading Lua")
            await self.g(self.game.call, "luamod.reload")
            for _ in range(16):
                await asyncio.sleep(0.5)
                if time.time() - self.mod["beat"] < 3:
                    return
            self.mod["state"] = "not responding"
            log.warning("the mod isn't responding in the game")
        if changed and not alive:
            await self.g(self.game.call, "uimod.remount", mod_id=install.MOD_ID)    # pick up new panel files

    def on_report(self, kind: str, text: str):
        self.mod["beat"] = time.time()       # any report means the controller is there (on_begin runs even paused)
        if kind == "restart":
            self.mod["instance"] = text
            self.mod["state"] = "starting"   # its first heartbeat says which puppets it found
            log.info("in-game controller started (%s)", text)
            # a fresh controller re-reads the profiles; ops sent before it started were missed
            for r in self.remotes.values():
                r.act_sig = r.anim = r.outfit = None
        elif kind == "up":
            now = self.mod["beat"] = time.time()
            self.mod["state"] = "running"
            for key, at in list(self.despawning.items()):
                if now - at > 10:
                    del self.despawning[key]     # (it never said so: maybe the op was missed; cleanup below retries)
            ids = {}
            for pair in filter(None, text.split(",")):
                key, _, eid = pair.partition("=")
                if eid.isdigit() and key not in self.despawning:     # (listed before our despawn ran: stale)
                    ids[key] = int(eid)
            self.puppet_ids = ids
            for key in list(ids):            # puppets the controller already has (e.g. after a companion restart)
                r = self.remotes.get(key)
                if r:
                    r.seen_at = now
                    if not r.spawned:
                        r.spawned, r.hidden = True, False
            for r in self.remotes.values():  # asked for, but it never showed up (the op ran out, the game was busy)
                if r.spawned and r.pid not in ids and now - max(r.spawn_requested, r.seen_at) > 10 \
                        and not self.mail.pending(r.pid, "spawn"):
                    log.info("%s's Zoi didn't show up in your world: trying again", r.name)
                    r.spawned, r.spawn_requested = False, 0.0
            # puppets nobody is playing any more (left while we were away, or saved into the game): remove
            if self.writer and self.me_id and time.time() - self.connected_at > 20:
                for key in list(ids):            # (despawn takes it out of puppet_ids, which is ids)
                    r = self.remotes.get(key)
                    if ids[key] == self.my_entity:
                        continue                 # never the Zoi being played (a household the program created)
                    if (r is None or not r.spawned) and time.time() - self.cleaning.get(key, 0) > 15:
                        log.info("removing a leftover puppet (%s)", key)
                        self.cleaning[key] = time.time()
                        self.despawn(key)
        elif kind == "spawned":
            key, _, eid = text.partition(" ")
            self.despawning.pop(key, None)       # (ops run in order: any despawn before it is done)
            if eid.isdigit():
                self.puppet_ids[key] = int(eid)
                r = self.remotes.get(key)
                if r:
                    r.seen_at = time.time()
                    if not r.adopted:                # (a family member keeps the game's own relationship)
                        asyncio.get_running_loop().create_task(self.g(self.bond_puppet, int(eid)))   # (couple.py)
        elif kind == "despawned":
            key = text.split(" ", 1)[0]
            self.puppet_ids.pop(key, None)
            r = self.remotes.get(key)
            if self.despawning.pop(key, None) is None and r and r.spawned:
                log.info("%s's Zoi was taken out of your world: bringing it back", r.name)
                r.spawned, r.spawn_requested = False, time.time() - 5        # (the controller lost it)
        elif kind == "spawn_failed":
            key, _, why = text.partition(" ")
            self.puppet_ids.pop(key, None)
            r = self.remotes.get(key)
            log.warning("bringing %s's Zoi in failed: %s", r.name if r else key, why or "no reason given")
            if r:
                r.spawned, r.spawn_requested = False, time.time() - 5        # again in ~3 s (manage_puppets waits 8)
        elif kind == "social":
            self.on_social_report(text)
        elif kind == "me":
            self.on_my_position(text)
        elif kind == "log":
            log.info("[game] %s", text)

    # ------------------------------------------------------------------ my Zoi -> server
    def describe_act(self, a: dict) -> dict | None:
        return describe_act(self.game, a, self.my_entity, self.puppet_owner, self.objects)

    async def game_loop(self):
        was_in_world, tick, last_profile_check, last_me = False, 0, 0.0, 0.0
        act, anim, outfit = None, None, None
        while True:
            try:
                world = await self.g(self.game.world)
                gs = getattr(self.game, "last_state", None) or {}
                if world == "out" and was_in_world and not gs.get("is_lobby") and not gs.get("loading") \
                        and self.__dict__.get("_out_seen", 0) < 2:
                    self._out_seen = self.__dict__.get("_out_seen", 0) + 1      # one odd answer (the game busy for
                    await asyncio.sleep(0.5)                                     # a moment) isn't leaving the world
                    continue
                self._out_seen = 0
                world = self.world = world
                self.mail.paused = world in ("paused", "stopped")      # (no controller ticks: ops wait)
                if world in ("paused", "stopped"):
                    self.mod["beat"] = max(self.mod["beat"], time.time() - 3)   # no ticks while paused: not dead
                await self.send_presence(world)
                if world == "out":
                    if was_in_world:
                        log.info("left the world (menu / loading)")
                        self.mod["state"] = "waiting for the game"
                    was_in_world = False
                    await self.at_main_menu()
                    await asyncio.sleep(2)
                    continue
                if not was_in_world:
                    was_in_world = True
                    await self.ensure_mod()
                    await self.watch_save(entered=True)
                tick += 1
                if tick % 5 == 0:
                    await self.watch_save(entered=False)
                active = self.mp_active()
                if tick % 25 == 7 and active and self.writer and not self.world_busy:
                    asyncio.get_running_loop().create_task(self.maybe_upload_world())   # after the game saved
                if time.time() - last_me > 1:
                    self.my_entity, last_me = await self.g(self.game.possessed), time.time()
                fast = self.fast_pos
                if fast and time.time() - fast["at"] < 1 and tick % 10 and self.last_full_state:
                    st = {**self.last_full_state, **{k: fast[k] for k in ("x", "y", "z", "yaw", "site")}}
                else:
                    st = await self.g(self.game.state_of, self.my_entity)
                    self.last_full_state = st
                self.my_city = st["city"]
                act, anim = self.cur_act, self.cur_anim                  # (act_loop keeps them fresh)
                if tick % 10 == 1:
                    try:
                        outfit = await self.g(self.game.outfit, self.my_entity)
                    except GameError:
                        pass
                if tick % 2 == 0:
                    self.my_clock = await self.g(self.game.clock)
                    self.sleeping = bool(SLEEPING.search(f"{(act or {}).get('bag', '')} {anim or ''}"))
                    if world in ("play", "stopped") and self.writer and self.me_id and active:
                        await self.keep_speed()
                if tick % 5 == 0 and time.time() - self.mod["beat"] > 10 \
                        and time.time() - self.mod.get("tried", 0) > 20:
                    self.mod["tried"] = time.time()
                    log.info("the mod went quiet: loading it again")
                    await self.ensure_mod()
                if not active and any(r.spawned for r in self.remotes.values()):
                    self.despawn_all()                     # not the multiplayer save: friends' Zois leave it
                    for r in self.remotes.values():
                        r.spawned = False
                if self.writer and self.me_id and active:
                    if self.sent_profile is None or time.time() - last_profile_check > 30:
                        last_profile_check = time.time()
                        profile = await self.g(read_profile, self.game.call, self.my_entity)
                        self.my_gender = profile.get("gender")
                        h = profile_hash(profile)
                        if h != self.sent_profile:
                            await self.send({"t": "profile", "profile": profile})
                            self.sent_profile = h
                            log.info("shared your Zoi %s %s", profile["first_name"], profile["last_name"])
                        await self.check_home()
                    if tick % 15 == 3 and world == "play":
                        await self.sync_world()
                    self.n += 1
                    clock = list(self.my_clock[:2]) if self.my_clock and world == "play" else None
                    self.state_base = {"act": act, "anim": anim, "outfit": outfit, "clock": clock,
                                       "sleep": self.sleeping, **self.extra_state(st)}
                    self.sending_state = True
                    await self.send({"t": "state", "state": {"n": self.n, **st, "ts": round(time.time(), 3),
                                                              **self.state_base}})
                    await self.manage_puppets()
            except GameError as e:
                log.debug("game: %s", e)
                await asyncio.sleep(1)
            except (OSError, ConnectionError) as e:
                log.debug("send: %s", e)
            except Exception as e:                       # noqa: BLE001 - my Zoi must keep being sent
                loop_error("game loop", e)
                await asyncio.sleep(1)
            await asyncio.sleep(0.2)

    # ------------------------------------------------------------------ realtime
    def on_my_position(self, text: str):
        """The controller read my Zoi's position from the engine (10 times a second): it goes out right away, with the
        rest of my latest state."""
        try:
            x, y, z, yaw, site = text.split()[:5]
            pos = {"x": round(float(x), 1), "y": round(float(y), 1), "z": round(float(z), 1),
                   "yaw": round(float(yaw), 1), "site": int(float(site)), "at": time.time()}
        except ValueError:
            return
        self.fast_pos = pos
        base = self.last_full_state
        if not (self.sending_state and base and self.writer and self.me_id and self.world == "play" and self.mp_active()):
            return
        self.n += 1
        st = {"n": self.n, **base, **{k: pos[k] for k in ("x", "y", "z", "yaw", "site")},
              "ts": round(pos["at"], 3), **self.state_base}
        asyncio.get_running_loop().create_task(self.send_quiet({"t": "state", "state": st}))

    async def send_quiet(self, msg: dict):
        try:
            await self.send(msg)
        except (OSError, ConnectionError):
            pass

    async def act_loop(self):
        """What my Zoi is doing, 4 times a second, apart from the slower loop: a change goes out right away."""
        while True:
            try:
                self._house_n = self.__dict__.get("_house_n", 0) + 1
                if self._house_n % 4 == 0 and self.world in ("play", "stopped") and self.mp_active()                         and self.cfg.get("sync_time", True) and self.clock_mode() == "host"                         and self.leader and self.leader != self.me_id and self.my_clock:
                    await self.follow_leader_clock()             # (every second: the clocks stay within minutes)
                    await self.follow_leader_speed()
                if self._house_n % 8 == 0 and self.world != "out" and self.my_entity:
                    await self.house_tick()                  # (every 2 s, paused too: build mode pauses the game)
                if self.world == "play" and self.my_entity:
                    a = await self.g(self.game.activity, self.my_entity)
                    act = await self.g(self.describe_act, a)
                    anim = a["anim"]["id"] if a.get("anim") else None
                    if act != self.cur_act or anim != self.cur_anim:
                        self.cur_act, self.cur_anim = act, anim
                        self.learn_bag(act)
                        self.state_base = {**self.state_base, "act": act, "anim": anim}
                        pos = self.fast_pos
                        if pos and self.sending_state and self.last_full_state and self.writer and self.me_id:
                            self.n += 1
                            await self.send_quiet({"t": "state", "state": {
                                "n": self.n, **self.last_full_state, **{k: pos[k] for k in ("x", "y", "z", "yaw", "site")},
                                "ts": round(time.time(), 3), **self.state_base}})
            except GameError:
                await asyncio.sleep(0.5)
            except Exception as e:                       # noqa: BLE001
                loop_error("action loop", e)
                await asyncio.sleep(1)
            await asyncio.sleep(0.25)

    async def mailbox_loop(self):
        inject = install.MAILBOX_DIR / "inject.json"       # debugging: extra ops dropped in by tools
        while True:
            try:
                for kind, text in await asyncio.to_thread(self.reports.poll):
                    try:
                        self.on_report(kind, text)
                    except Exception as e:               # noqa: BLE001 - one odd report, not the whole channel
                        loop_error(f"report {kind}", e)
                if inject.exists():
                    for op in json.loads(inject.read_text("utf-8")):
                        name = op.pop("op")
                        self.mail.op(name, **op)
                    inject.unlink()
                await asyncio.to_thread(self.mail.flush)     # (a file held open just waits for the next round)
            except PermissionError:
                pass                     # the game was reading the mailbox; next round
            except (OSError, ValueError) as e:
                log.warning("mailbox: %s", e)
            except Exception as e:                       # noqa: BLE001
                loop_error("mailbox loop", e)
            await asyncio.sleep(0.03)              # (realtime: positions come in and go to the game at ~30 Hz)

    # ------------------------------------------------------------------ the multiplayer save
    # Multiplayer plays in a save of its own (cfg["mp_save"]), so a player's normal saves stay untouched. At the main
    # menu the program loads it; in any other save, multiplayer pauses (no friends' Zois, no shared clock). A save the
    # game loaded without me is identified by the next save it writes (game.list_saves), or by asking the player.
    async def send_presence(self, world: str):
        """While my Zoi isn't being sent (menu, paused, another save, a question on screen), tell the others why,
        every few seconds: their panel says it instead of a bare "waiting"."""
        if not (self.writer and self.me_id and self.remotes) or time.time() - self.__dict__.get("_presence_at", 0) < 4:
            return
        if world == "play" and self.mp_active():
            return                                       # (my state says it all)
        self._presence_at = time.time()
        box = (self.save_box or {}).get("key")
        await self.send({"t": "event", "kind": "presence",
                         "data": {"world": world, "box": box, "busy": bool(self.save_busy or self.world_busy),
                                  "mp": self.save_session.get("mp")}})

    def on_presence(self, m: dict):
        r = self.remotes.get(m.get("from"))
        if r and isinstance(m.get("data"), dict):
            r.presence = {**m["data"], "at": time.time()}

    def mp_active(self) -> bool:
        return self.save_session["mp"] is True

    def mp_name(self) -> str:
        return (self.cfg.get("mp_save") or {}).get("name") or "your multiplayer save"

    async def watch_save(self, entered: bool):
        """Called about once a second while in a world; entered = a world has just been loaded."""
        mp = self.cfg.get("mp_save")
        if entered:
            folder, self.loading_save = self.loading_save, None
            lobby, self.game.saw_lobby = getattr(self.game, "saw_lobby", True), False
            prev, gs = self.save_session, getattr(self.game, "last_state", {}) or {}
            where = (gs.get("possessed_entity_id"), gs.get("level_name"))
            game_id = await self.g(servers.netinfo.process_id, "inZOI-Win64-Shipping.exe") if where[0] else None
            last = self.cfg.get("last_world") or {}
            if not folder and not prev.get("started") and game_id and last.get("id") == [game_id, *where] \
                    and last.get("mp") is not None:
                # this program restarted (an update, a crash) with the game still in the same world: same save
                log.info("still in the same world as before this program restarted: %s",
                         "your multiplayer save" if last["mp"] else "not your multiplayer save")
                self.save_session = {"started": time.time(), "folder": last.get("folder"), "checked": time.time(),
                                     "where": where, "game": game_id, "mp": last["mp"]}
                self.save_box = None
                if last["mp"] is False:
                    self.show_paused()
                return
            if not folder and not lobby and prev.get("started") and prev.get("where") == where and where[0]:
                # a loading screen without the main menu (moving to another lot, "go to my partner"): same save
                log.info("back in the world after a loading screen: still the same save")
                self.save_session = {**prev, "started": time.time(), "checked": time.time()}
                if prev.get("mp") is False:
                    self.show_paused()
                return
            self.save_session = {"started": time.time(), "folder": folder, "checked": 0.0, "where": where,
                                 "game": game_id,
                                 "mp": (folder == mp["folder"]) if folder and mp and mp.get("folder") else None}
            self.save_box = None
            if self.save_session["mp"] is False:
                self.show_paused()
        s = self.save_session
        if s["folder"] is None and time.time() - s.get("checked", 0) > 60:
            s["checked"] = time.time()
            newest = (await self.g(self.game.saves, 1) or [None])[0]
            if newest and save_time(newest) > s["started"]:        # written by this session, after it loaded
                s["folder"] = newest["folder"]
                if mp and s["mp"] is True and mp.get("folder") != s["folder"]:
                    mp["folder"] = s["folder"]             # they said this is the one (e.g. a Save As in the game)
                    save_config(self.cfg)
                elif mp and s["mp"] is None:
                    s["mp"] = mp.get("folder") == s["folder"]
                    if s["mp"] is False:
                        self.show_paused()
        if s["mp"] is None and not self.save_box and not self.save_busy:
            if mp:
                self.ask_confirm()
            elif not self.world_meta or not self.shared_world_on():
                self.ask_setup()                          # each plays their own save (the usual way)
            elif not self.world_busy and time.time() - self.__dict__.get("_world_tried", 0) > 30:
                self._world_tried = time.time()           # living together: the shared world becomes it (again,
                self.check_world()                        # if a download failed)
        if self.writer and not self.world_meta and not self.world_asked and (mp or {}).get("folder"):
            self.world_asked = True
            self.check_world()                            # no shared world on this server yet: offer ours
        self.remember_world()

    def remember_world(self):
        """Keep which world this is (the game's process, the Zoi played, the map) and whether it's the multiplayer
        save, so a restart of this program with the game still open picks up where it was (see watch_save)."""
        s = self.save_session
        if not s.get("game") or not s.get("where") or s.get("mp") is None:
            return
        rec = {"id": [s["game"], *s["where"]], "mp": s["mp"], "folder": s.get("folder")}
        if self.cfg.get("last_world") != rec:
            self.cfg["last_world"] = rec
            save_config(self.cfg)

    def set_box(self, key: str, text: str, buttons: list, question: str | None = None, on_text=None):
        self.save_box = {"key": key, "text": text, "buttons": [{"id": b, "label": label} for b, label in buttons]}
        if question:
            self.console_ask(question, lambda a: self.save_box and self.save_box["key"] == key and on_text(a))

    def ask_setup(self):
        self.set_box(
            "setup", "Multiplayer plays in a save of its own, so your normal saves stay untouched. Make a multiplayer "
            "copy of this save? The game saves, goes to the main menu for a moment and loads the copy.",
            [("copy", "Make a multiplayer copy"), ("use", "Use this save"), ("later", "Not now")],
            "\n  ------------------------------------------------------------------------------\n"
            "  Multiplayer plays in a save of its own, so your normal saves stay untouched.\n"
            "    c = make a multiplayer copy of this save (recommended: the game saves, goes to the\n"
            "        main menu for a moment and loads the copy)\n"
            "    u = use this save for multiplayer\n"
            "    n = not now (multiplayer stays off in this save)\n"
            "  Type c, u or n and press Enter [c]: ",
            lambda a: self.save_action("setup", {"u": "use", "n": "later"}.get(a[:1], "copy")))

    def ask_confirm(self):
        self.set_box(
            "confirm", f"Is this your multiplayer save ({self.mp_name()})? Multiplayer waits until you say.",
            [("yes", "Yes"), ("no", "No")],
            f"\n  Is this your multiplayer save ({self.mp_name()})? Type y (yes) or n (no) and press Enter [y]: ",
            lambda a: self.save_action("confirm", "no" if a.startswith("n") else "yes"))

    def show_paused(self):
        log.info("this isn't your multiplayer save: multiplayer is paused here")
        self.set_box("paused", f"This isn't {self.mp_name()}: multiplayer is paused here, so this save stays "
                     "untouched.", [("switch", "Switch to my multiplayer save")])

    def save_action(self, key: str, action: str):
        """An answer from the panel or the console."""
        if not self.save_box or self.save_box["key"] != key or self.save_busy:
            return
        if self.asking and key != "paused":
            print("\n  (Answered in the game.)\n", flush=True)
        self.save_box = None
        s = self.save_session
        if action == "copy":
            asyncio.get_running_loop().create_task(self.make_mp_save())
        elif action == "use":
            self.cfg["mp_save"] = {"folder": s["folder"], "name": "the save you were playing"}
            save_config(self.cfg)
            s["mp"] = True
            log.info("this save is your multiplayer save now")
            asyncio.get_running_loop().create_task(self.name_mp_save())
        elif action == "yes":
            s["mp"] = True
            if s["folder"] and self.cfg["mp_save"].get("folder") != s["folder"]:
                self.cfg["mp_save"]["folder"] = s["folder"]
                save_config(self.cfg)
        elif action in ("no", "later"):
            s["mp"] = False
            if action == "no":
                self.show_paused()
        elif action == "switch":
            asyncio.get_running_loop().create_task(self.switch_to_mp_save())

    async def name_mp_save(self):
        """Give the chosen save a name for the panel, once the game has written it (its newest entry)."""
        for _ in range(30):
            folder = self.save_session["folder"]
            if folder:
                entry = next((e for e in await self.g(self.game.saves, 20) if e["folder"] == folder), None)
                if entry:
                    self.cfg["mp_save"]["name"] = save_label(entry)
                    save_config(self.cfg)
                return
            await asyncio.sleep(60)

    async def to_main_menu(self):
        """Save the game and go to the main menu (friends' Zois leave first, so they aren't saved in it)."""
        self.despawn_all()
        for r in self.remotes.values():
            r.spawned = False
        await asyncio.sleep(2)
        await self.g(self.game.call, "game.goto_lobby", save=True)
        await self.wait_game(lambda st: st.get("is_lobby"), 240)

    async def wait_game(self, ok, timeout: float) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            try:
                st = await self.g(self.game.call, "game.get_state")
                if ok(st):
                    return st
            except GameError:
                pass
            await asyncio.sleep(1)
        raise TimeoutError("the game took too long")

    async def make_mp_save(self):
        self.save_busy = True
        self.set_box("working", "Saving your game and making your multiplayer copy...", [])
        try:
            await self.to_main_menu()
            newest = (await self.g(self.game.saves, 1) or [None])[0]
            if not newest:
                raise ValueError("the game didn't list the save it just made")
            new = await self.g(saves.copy_save, newest["folder"], newest["file"])
            entry = next((e for e in await self.g(self.game.saves, 20, True, True) if e["folder"] == new), None)
            if not entry or entry.get("is_broken"):
                raise ValueError("the game doesn't accept the copy")
            self.cfg["mp_save"] = {"folder": new, "name": save_label(entry, "multiplayer copy"),
                                   "copied_from": newest["folder"]}
            save_config(self.cfg)
            log.info("made your multiplayer save (a copy of %s): loading it", newest["folder"])
            await self.load_mp_save()
            self.save_box = None
        except (GameError, OSError, ValueError, TimeoutError) as e:
            log.warning("making the multiplayer save failed: %s", e)
            self.set_box("error", f"Making the multiplayer copy failed: {e}. Load your save from the main menu to "
                         "carry on.", [])
        finally:
            self.save_busy = False

    async def switch_to_mp_save(self):
        self.save_busy = True
        self.set_box("working", f"Saving this game and loading {self.mp_name()}...", [])
        try:
            await self.to_main_menu()
            await self.load_mp_save()
            self.save_box = None
        except (GameError, OSError, TimeoutError) as e:
            self.set_box("error", f"Switching saves failed: {e}", [])
        finally:
            self.save_busy = False

    async def load_mp_save(self):
        mp = self.cfg["mp_save"]
        await self.g(self.backup_save, mp.get("folder"), "before-load")     # (never fails: see couple.py)
        self.loading_save, self.auto_loaded = mp["folder"], True
        newest = await self.g(self.newest_slot_file, mp["folder"])
        log.info("loading %s: its newest slot %s", mp["folder"], newest or "(the game's choice)")
        if newest:
            await self.g(self.game.call, "game.load", folder=mp["folder"], file=newest)
        else:
            await self.g(self.game.call, "game.load", folder=mp["folder"])

    def newest_slot_file(self, folder: str) -> str | None:
        """The newest slot of a save folder (an autosave is often newer than the main slot, and game.load without a
        file opens the main slot): the file name to load, or None if the game doesn't list it."""
        try:
            slots = [s for s in self.game.saves(limit=200, autosave=True, refresh=True)
                     if s.get("folder") == folder and s.get("file") and not s.get("is_broken")]
        except (GameError, OSError, ValueError):
            slots = []
        if slots:
            return max(slots, key=lambda s: str(s.get("saved_time") or ""))["file"]
        return None

    async def at_main_menu(self):
        """At the game's main menu: load the multiplayer save (once per run)."""
        mp = self.cfg.get("mp_save")
        if not mp or not mp.get("folder") or self.auto_loaded or self.save_busy or self.world_busy:
            return
        if self.shared_world_on() and self.world_meta and self.world_meta["version"] > self.local_world().get("version", 0):
            return                                        # a newer shared world is on its way: load that one
        try:
            st = await self.g(self.game.call, "game.get_state")
        except GameError:
            return
        if not st.get("is_lobby") or st.get("loading"):
            return
        log.info("loading %s", self.mp_name())
        try:
            await self.load_mp_save()
        except GameError as e:
            log.warning("couldn't load the multiplayer save: %s", e)

    # ------------------------------------------------------------------ the shared world
    # The server keeps one world save that every player plays (docs/shared-world.md). This PC's copy of it is the
    # multiplayer save; cfg["world"] = {version, folder, mtime} says which version it is. Whoever is alone in the
    # world uploads it after each game save; with others in it too, the last one left does. Before a server restart
    # everyone saves, the keeper uploads, and everyone reloads after.
    def local_world(self) -> dict:
        return self.cfg.get("world") or {}

    def shared_world_on(self) -> bool:
        """coop-casal: each player plays their own save (their multiplayer copy) and they meet on the same map. The
        shared world (one save for everyone) is off for good; only the tests switch it on."""
        return bool(os.environ.get("INZOI_MP_SHARED_WORLD"))

    def check_world(self):
        meta, mine = self.world_meta, self.local_world()
        if self.world_busy or not self.writer or not self.shared_world_on():
            return
        if not meta:
            if (self.cfg.get("mp_save") or {}).get("folder") and not self.world_box:
                self.ask_make_world()
            return
        if meta["version"] <= mine.get("version", 0):
            return
        if self.world != "out" and self.mp_active():
            self.world_box = {"key": f"reload{meta['version']}", "text":
                              f"{meta.get('by') or 'Someone'} saved a newer version of the shared world. Load it now? "
                              "(What you did since your last save stays only in your copy.)",
                              "buttons": [{"id": "reload", "label": "Load it"}, {"id": "later", "label": "Later"}]}
        else:
            asyncio.get_running_loop().create_task(self.download_world(meta))

    def ask_make_world(self):
        key = "make"
        text = (f"This server has no shared world yet. Make your multiplayer save ({self.mp_name()}) everyone's "
                "world? Friends then play in it too, with you.")
        self.world_box = {"key": key, "text": text,
                          "buttons": [{"id": "make", "label": "Make it everyone's world"}, {"id": "later", "label": "Not now"}]}
        self.console_ask(f"\n  ------------------------------------------------------------------------------\n  {text}\n"
                         "  Type y (yes) or n (not now) and press Enter [y]: ",
                         lambda a: self.world_action(key, "later" if a.startswith("n") else "make"))

    def world_action(self, key: str, action: str):
        box = self.world_box
        if not box or box["key"] != key or self.world_busy:
            return
        self.world_box = None
        loop = asyncio.get_running_loop()
        if action == "make":
            mp = self.cfg.get("mp_save") or {}
            loop.create_task(self.upload_world(mp.get("folder"), base=0))
        elif action == "reload":
            loop.create_task(self.reload_world())

    async def download_world(self, meta: dict):
        """Fetch the server's world and make it this PC's multiplayer save (it loads at the game's main menu)."""
        self.world_busy = True
        stage = mods.DATA / "downloads"
        files = {f["h"]: f["s"] for f in meta["files"]}
        job = self.world_job = {"what": "Downloading the shared world", "done": 0, "total": sum(files.values())}
        try:
            todo = [h for h in files if not (stage / h).exists()]
            job["done"] = job["total"] - sum(files[h] for h in todo)
            bar = ProgressBar("Downloading the shared world", job["total"])
            bar.done = job["done"]

            def progress(n):
                job["done"] += n
                bar.add(n)
            async with mods.Transfer(self.address(), self.cfg["key"], self.cfg["name"]) as tr:
                for h in todo:
                    why = await tr.download(h, stage / h, progress)
                    if why:
                        raise ConnectionError(f"the server is missing part of the world ({why})")
            bar.finish()
            folder = (self.local_world().get("folder") or (self.cfg.get("mp_save") or {}).get("folder"))
            await self.g(self.backup_save, folder, f"before-v{meta['version']}")
            folder = await self.g(world.install, meta["files"], lambda h: stage / h, folder)
            slot = world.newest_slot(folder)
            self.cfg["world"] = {"version": meta["version"], "folder": folder, "mtime": slot.stat().st_mtime}
            self.cfg["mp_save"] = {"folder": folder, "name": f"{meta.get('name') or 'the shared world'} (shared world)"}
            save_config(self.cfg)
            for h in files:
                (stage / h).unlink(missing_ok=True)
            self.auto_loaded = False                       # load it at the main menu
            log.info("the shared world (version %d, saved by %s) is your multiplayer save now",
                     meta["version"], meta.get("by"))
            if self.world != "out" and not self.mp_active() and not self.save_busy:
                self.save_session["mp"] = False            # in another save: offer to switch to the shared world
                self.show_paused()
        except (OSError, ConnectionError, ValueError, asyncio.TimeoutError) as e:
            log.warning("downloading the shared world failed: %s", e)
        finally:
            self.world_busy, self.world_job = False, None

    async def world_request(self, msg: dict, reply: str, timeout: float = 30) -> dict:
        fut = self.world_waiters[reply] = asyncio.get_running_loop().create_future()
        await self.send(msg)
        return await asyncio.wait_for(fut, timeout)

    async def upload_world(self, folder: str | None, base: int | None = None):
        """Send this PC's copy of the world to the server as its next version."""
        if not folder or self.world_busy or not self.writer:
            return
        self.world_busy = True
        base = self.local_world().get("version", 0) if base is None else base
        try:
            try:                                         # (never an autosave the game is still writing)
                files, paths, mtime = await self.g(world.pack_stable, folder, self.world_hashes)
            except world.StillWriting as e:
                self.upload_wait = time.time()
                log.info("not uploading the world yet: %s", e)
                return
            self.upload_wait = None
            offer = await self.world_request({"t": "world_save_offer", "files": files}, "world_save_offer")
            if offer.get("error"):
                raise ValueError(offer["error"])
            need = offer.get("need", [])
            job = self.world_job = {"what": "Uploading the shared world", "done": 0,
                                    "total": sum(paths[h].stat().st_size for h in need)}
            if need:
                bar = ProgressBar("Uploading the shared world", job["total"])

                def progress(n):
                    job["done"] += n
                    bar.add(n)
                async with mods.Transfer(self.address(), self.cfg["key"], self.cfg["name"]) as tr:
                    for h in need:
                        await tr.upload(paths[h], h, progress)
                bar.finish()
            put = await self.world_request({"t": "world_save_put", "files": files, "base": base,
                                            "name": (self.cfg.get("mp_save") or {}).get("name", "").replace(
                                                " (shared world)", "")}, "world_save_put")
            if not put.get("ok"):
                log.warning("the shared world wasn't saved to the server: %s", put.get("error"))
                return
            self.cfg["world"] = {"version": put["version"], "folder": folder, "mtime": mtime}
            save_config(self.cfg)
            log.info("saved the shared world to the server (version %d)", put["version"])
        except (OSError, ConnectionError, ValueError, asyncio.TimeoutError) as e:
            log.warning("uploading the shared world failed: %s", e)
        finally:
            self.world_busy, self.world_job = False, None

    def world_status_box(self) -> dict | None:
        job = self.world_job
        if job:
            pct = int(100 * job["done"] / max(1, job["total"]))
            return {"key": "job", "buttons": [],
                    "text": f"{job['what']}: {size_text(job['done'])} of {size_text(job['total'])} ({pct}%)"}
        return self.world_box

    def alone_in_world(self) -> bool:
        return not any(r.state and time.time() - r.state_at < 30 for r in self.remotes.values())

    async def maybe_upload_world(self):
        """After the game saves the world (autosave or by hand), upload it if nobody else is in it."""
        mine = self.local_world()
        folder = mine.get("folder")
        if not folder or not self.world_meta or self.world_busy or not self.alone_in_world() \
                or not self.shared_world_on():
            return
        if self.save_session.get("folder") not in (None, folder):
            return
        slot = world.newest_slot(folder)
        if slot and slot.stat().st_mtime > mine.get("mtime", 0) + 1:
            await self.upload_world(folder)

    async def reload_world(self):
        """Leave this game without saving and load the newer shared world (downloaded first)."""
        self.despawn_all()
        await asyncio.sleep(1.5)
        await self.g(self.game.call, "game.goto_lobby", save=False)
        await self.wait_game(lambda st: st.get("is_lobby"), 240)
        if self.world_meta:
            await self.download_world(self.world_meta)

    async def on_restart_soon(self, m: dict):
        """The server restarts in a moment: save the game (and, as the world keeper, upload the world); everything
        reloads once the server is back."""
        log.info("the server restarts in %s s: saving your game", m.get("in"))
        if self.world == "out" or not self.mp_active() or self.save_busy:
            return
        self.save_busy = True
        try:
            await self.to_main_menu()
            folder = self.local_world().get("folder")
            if folder and self.shared_world_on() and (m.get("keeper") == self.me_id or self.alone_in_world()):
                await self.upload_world(folder)
            self.auto_loaded = False                       # load the world again once the server is back
        except (GameError, OSError, TimeoutError) as e:
            log.warning("saving before the restart failed: %s", e)
        finally:
            self.save_busy = False

    # ------------------------------------------------------------------ updates
    async def update_loop(self):
        """Look for a newer release on GitHub at the start and every couple of hours. A new one is announced in the
        game (a notice, and the Multiplayer page offers it); "Update" there downloads it and restarts into it."""
        was = self.cfg.get("last_version")
        if was != VERSION:
            self.cfg["last_version"] = VERSION
            save_config(self.cfg)
            if was:
                asyncio.get_running_loop().create_task(self.announce(f"inZOI Multiplayer is updated to {VERSION}."))
        await asyncio.sleep(20)
        while True:
            if not self.update or self.update["state"] in ("error", "available", "later"):
                try:
                    await self.check_update()
                except (OSError, ValueError) as e:
                    log.debug("update check: %s", e)
                except Exception as e:                   # noqa: BLE001
                    loop_error("update check", e)
            await asyncio.sleep(15 * 60)               # (coop-casal: often, so both PCs stay on the same version)

    async def announce(self, text: str, wait: float = 1800):
        """A notice in the game once it's up (on its main menu or in a save)."""
        end = time.time() + wait
        while not self.ingame.game_running():
            if time.time() > end:
                return
            await asyncio.sleep(3)
        await asyncio.sleep(3)
        await self.ingame_toast(text)

    async def check_update(self):
        info = await self.g(updater.latest, VERSION)
        if not info or (self.update and self.update["version"] == info["version"]):
            return
        self.update = {**info, "state": "available", "done": 0, "total": info["size"], "path": None, "error": None}
        log.info("inZOI Multiplayer %s is out (this is %s)", info["version"], VERSION)
        if updater.frozen() and not self.cfg.get("ask_before_update") and not os.environ.get("INZOI_MP_ASK_UPDATE"):
            # coop-casal: always the newest version, without a question nobody sees (the panel starts closed): it
            # downloads and restarts the program by itself; the game keeps running.
            self.update_action(info["version"], "update")
            return
        self.console_ask(f"\n  ------------------------------------------------------------------------------\n"
                         f"  {self.update_text()}\n  Type y (yes) or n (later) and press Enter [y]: ",
                         lambda a: self.update_action(info["version"], "later" if a.startswith("n") else "update"))
        asyncio.get_running_loop().create_task(self.announce(
            f"inZOI Multiplayer {info['version']} is out: update it on the Multiplayer page."))

    async def update_now(self) -> dict:
        """The "Update now" button (or the partner asking): look for a newer version right away; if there is one,
        download it and restart into it (the game keeps running)."""
        try:
            info = await self.g(updater.latest, VERSION)
        except (OSError, ValueError) as e:
            return {"error": f"couldn't reach GitHub: {e}", "code": "update_check_failed"}
        if not info:
            return {"ok": True, "latest": True, "version": VERSION}
        if not self.update or self.update.get("version") != info["version"]:
            self.update = {**info, "state": "available", "done": 0, "total": info["size"], "path": None, "error": None}
        log.info("update now: %s", info["version"])
        self.update_action(info["version"], "update")
        return {"ok": True, "latest": False, "version": info["version"]}

    def update_text(self) -> str:
        u = self.update
        return (f"inZOI Multiplayer {u['version']} is out ({u['title']}). Update now? It downloads "
                f"({size_text(u['total'])}) and restarts the program: your game keeps running, and friends' Zois "
                "leave it for a few seconds.")

    def update_view(self) -> dict | None:
        """The update for the pages: {version, title, state, done, total, error, text}."""
        u = self.update
        if not u:
            return None
        out = {k: u.get(k) for k in ("version", "title", "state", "done", "total", "error")}
        out["text"] = self.update_text()
        return out

    def update_box_view(self) -> dict | None:
        """The update in the in-save panel: the question, the download's progress, or what went wrong."""
        u = self.update
        if not u or u["state"] == "later":
            return None
        key = u["version"]
        if u["state"] == "available":
            return {"key": key, "text": self.update_text(), "buttons": [{"id": "update", "label": "Update now"},
                                                                        {"id": "later", "label": "Later"}]}
        if u["state"] == "error":
            return {"key": key, "text": f"The update didn't work: {u['error']}",
                    "buttons": [{"id": "update", "label": "Try again"}, {"id": "later", "label": "Later"}]}
        pct = int(100 * u["done"] / max(1, u["total"]))
        text = f"Downloading inZOI Multiplayer {key}... {pct}%" if u["state"] == "downloading" else \
            f"Restarting into inZOI Multiplayer {key}..."
        return {"key": key, "text": text, "buttons": []}

    def update_action(self, key: str, action: str):
        """Update (download it, then restart into it) or later. Answered on a page or in the console."""
        u = self.update
        if not u or u["version"] != key:
            return
        if action == "later":
            if u["state"] in ("available", "error"):
                u["state"] = "later"
                log.info("the update waits (the Multiplayer page still offers it)")
            return
        if u["state"] in ("available", "later", "error"):
            u.update(state="downloading", done=0, error=None)
            asyncio.get_running_loop().create_task(self.download_update())

    async def download_update(self):
        u = self.update
        if not updater.frozen():
            u.update(state="error", error="this copy runs from source: get the new version from GitHub")
            return
        log.info("downloading inZOI Multiplayer %s", u["version"])
        bar = ProgressBar(f"Downloading update {u['version']}", u["total"])

        def progress(n):
            u["done"] += n
            bar.add(n)
        try:
            u["path"] = await self.g(updater.download, u, progress)
        except (OSError, ValueError) as e:
            u.update(state="error", error=str(e))
            log.warning("downloading the update failed: %s", e)
            return
        bar.finish()
        u["done"] = u["total"]
        await self.restart_into_update()

    async def restart_into_update(self):
        u = self.update
        u["state"] = "restarting"
        log.info("restarting into inZOI Multiplayer %s", u["version"])
        if self.writer:
            if self.hosting:
                try:
                    await self.send({"t": "chat", "text": "(The host is updating inZOI Multiplayer: back in a moment.)"})
                except (OSError, ConnectionError):
                    pass
        self.despawn_all()
        await asyncio.sleep(1.5)                       # let the game take friends' Zois out first
        try:
            await self.g(updater.apply, u["path"], self.restart_args)
        except OSError as e:
            u.update(state="error", error=f"couldn't put the new version in place ({e})")
            log.warning("couldn't apply the update: %s", e)
            return
        logging.shutdown()
        os._exit(0)

    # ------------------------------------------------------------------ mods
    async def mods_loop(self):
        """Tell the server which mods this PC has (again whenever they change), share their files when friends get
        their mods from me, and finish an install that had to wait for the game to close."""
        hashes = mods.Hashes()
        sent_for, sent, last_scan = None, None, 0.0
        while True:
            try:
                if self.writer and self.me_id and (sent_for != self.connected_at or time.time() - last_scan > 120):
                    self.mods_local = await self.g(mods.scan, mods.MODS_DIR, hashes)
                    last_scan = time.time()
                    mine = [{k: v for k, v in m.items() if k != "enabled"} for m in self.mods_local if m["enabled"]]
                    sig = sorted((m["id"], sorted(mods.fingerprint(m))) for m in mine)    # content, not manifests
                    if sent_for != self.connected_at or sig != sent:
                        await self.send({"t": "mods", "mods": mine})
                        sent_for, sent = self.connected_at, sig
                        log.info("your mods: %d", len(mine))
                    self.check_mods()
                    await self.share_mods(mine)
                o = self.mod_offer
                if o and o["state"] == "close_game" and self.world == "out":
                    await self.install_mods(o)
            except (OSError, ConnectionError, ValueError, asyncio.TimeoutError) as e:
                log.warning("mods: %s", e)
                self.mod_share = None
            except Exception as e:                       # noqa: BLE001
                loop_error("mods loop", e)
                self.mod_share = None
            await asyncio.sleep(5)

    async def share_mods(self, mine: list):
        """Upload the files of my mods the server doesn't have yet, when friends get their mods from me: always on
        a stand-alone server, only the host's when someone hosts and plays."""
        ms = self.modset or {}
        if not mine or (ms.get("source") == "host" and ms.get("host") != self.me_id):
            return
        files = {f["h"]: (mods.MODS_DIR / m["path"] / f["p"], f["s"]) for m in mine for f in m["files"]}
        async with mods.Transfer(self.address(), self.cfg["key"], self.cfg["name"]) as tr:
            need = await tr.not_on_server(list(files))
            if not need:
                return
            share = self.mod_share = {"done": 0, "total": sum(files[h][1] for h in need)}
            log.info("sharing your mods with friends: %s to upload", size_text(share["total"]))
            bar = ProgressBar("Sharing your mods", share["total"])

            def progress(n):
                share["done"] += n
                bar.add(n)
            for h in need:
                await tr.upload(files[h][0], h, progress)
            bar.finish()
            log.info("your mods are shared")
            self.mod_share = None

    def check_mods(self):
        """Compare this PC's mods with the server's set and offer the missing ones (asked once per set)."""
        ms, o = self.modset, self.mod_offer
        if ms is None or self.mods_local is None or (o and o["state"] in ("downloading", "waiting", "installing")):
            return
        need = mods.missing(ms.get("mods", []), self.mods_local)
        if not need:
            if o and o["state"] != "done":
                self.mod_offer = None
            return
        key = mods.offer_key(need)
        if o and o["key"] == key:
            return
        o = self.mod_offer = {
            "key": key, "mods": need, "size": sum(m.get("size", 0) for m in need), "done": 0, "message": "",
            "from": sorted({n for m in need for n in m.get("by", []) if n}), "source": ms.get("source"),
            "state": "declined" if self.cfg.get("mods_declined") == key else "ask"}
        o["total"] = o["size"]
        log.info("%s %d mod(s) you don't have (%s): %s", self.mods_who(o), len(need), size_text(o["size"]),
                 ", ".join(m["name"] for m in need))
        if o["state"] == "ask":
            self.ask_console(o)

    def mods_who(self, o: dict) -> str:
        names = ", ".join(o["from"]) or "someone"
        return f"The host ({names}) uses" if o["source"] == "host" else f"Your friends ({names}) use"

    def console_ask(self, question: str, on_answer):
        """Ask in the program's window, one question at a time (the in-game panel asks too; whichever answers first
        counts, and a late answer to a question that's no longer open is ignored)."""
        if not interactive():
            return
        self.console_queue.append((question, on_answer))
        if not self.asking:
            self.next_question()

    def next_question(self):
        if not self.console_queue:
            self.asking = False
            return
        question, on_answer = self.console_queue.pop(0)
        self.asking = True
        loop = asyncio.get_running_loop()

        def run():
            text = ask(question).strip().lower()
            loop.call_soon_threadsafe(self.console_answered, on_answer, text)
        threading.Thread(target=run, daemon=True).start()

    def console_answered(self, on_answer, text: str):
        try:
            on_answer(text)
        finally:
            self.next_question()

    def ask_console(self, o: dict):
        names = ", ".join(m["name"] for m in o["mods"][:12]) + (" ..." if len(o["mods"]) > 12 else "")
        self.console_ask(
            f"\n  ------------------------------------------------------------------------------\n"
            f"  {self.mods_who(o)} {len(o['mods'])} mod(s) you don't have ({size_text(o['size'])}):\n"
            f"    {names}\n"
            f"  Without them, some of their clothes or things won't show up for you.\n"
            f"  Download them now? Type y (yes) or n (no) and press Enter [y]: ",
            lambda text: self.answer_mods(o["key"], not text.startswith("n"), True))

    def answer_mods(self, key: str, yes: bool, console: bool = False):
        o = self.mod_offer
        if not o or o["key"] != key or o["state"] not in ("ask", "declined", "error"):
            if console and o and o["state"] == "ask":
                self.ask_console(o)                  # the question changed while they were answering: ask again
            return
        if not console and self.asking:
            print("\n  (Answered in the game.)\n", flush=True)
        if yes:
            o["state"], o["message"] = "downloading", ""
            self.cfg.pop("mods_declined", None)
            save_config(self.cfg)
            asyncio.get_running_loop().create_task(self.download_mods(o))
        else:
            o["state"] = "declined"
            self.cfg["mods_declined"] = key
            save_config(self.cfg)
            log.info("not downloading the mods (the in-game panel's Mods tab can still get them)")

    async def download_mods(self, o: dict):
        stage = mods.DATA / "downloads"
        files = {f["h"]: f["s"] for m in o["mods"] for f in m["files"]}
        try:
            while self.mod_offer is o:
                todo = [h for h in files if not (stage / h).exists()]
                o["done"] = sum(s for h, s in files.items() if h not in todo)
                o["total"] = sum(files.values())
                log.info("downloading %d mod(s): %s", len(o["mods"]), size_text(o["total"] - o["done"]))
                waiting = False
                bar = ProgressBar("Downloading mods", o["total"])
                bar.done = o["done"]

                def progress(n):
                    o["done"] += n
                    bar.add(n)
                async with mods.Transfer(self.address(), self.cfg["key"], self.cfg["name"]) as tr:
                    for h in todo:
                        if await tr.download(h, stage / h, progress):
                            waiting = True                    # not uploaded yet: its owner is still sharing it
                bar.finish()
                if not waiting:
                    break
                o["state"], o["message"] = "waiting", f"waiting for {', '.join(o['from'])} to finish sharing them"
                await asyncio.sleep(10)
                o["state"] = "downloading"
            else:
                return
            await self.install_mods(o)
        except (OSError, ConnectionError, ValueError, asyncio.TimeoutError) as e:
            o["state"], o["message"] = "error", str(e)
            log.warning("downloading the mods failed: %s", e)

    async def install_mods(self, o: dict):
        stage = mods.DATA / "downloads"
        o["state"] = "installing"
        for m in list(o["mods"]):
            try:
                where = await self.g(mods.install_mod, m, lambda h: stage / h, self.mods_local or [])
            except mods.Busy:
                o["state"] = "close_game"
                o["message"] = "Close inZOI to finish: some of these mods are in use. They install by themselves then."
                log.info("some mods are in use by the game: they'll be installed when you close inZOI")
                return
            except (OSError, ValueError) as e:
                o["state"], o["message"] = "error", f"couldn't install {m['name']}: {e}"
                log.warning("installing %s failed: %s", m["name"], e)
                return
            log.info("installed mod %s -> %s", m["name"], where)
            o["mods"].remove(m)
        for h in list(stage.glob("*")):
            h.unlink(missing_ok=True)
        o["state"] = "done"
        o["message"] = "Restart inZOI to load them." if self.world != "out" else "They load when you start inZOI."
        log.info("mods installed. %s", o["message"])
        self.mods_local = await self.g(mods.scan)

    def mods_status(self) -> dict:
        ms, o = self.modset or {}, self.mod_offer
        have = {m["id"]: mods.fingerprint(m) for m in self.mods_local or [] if m["enabled"]}
        out = {"source": ms.get("source"), "count": len(ms.get("mods", [])), "sharing": self.mod_share,
               "list": [{"name": m["name"], "have": have.get(m["id"]) == mods.fingerprint(m)}
                        for m in ms.get("mods", [])[:40]]}
        if o:
            out["offer"] = {**{k: o.get(k) for k in ("key", "state", "size", "done", "total", "from", "message")},
                            "names": [m["name"] for m in o["mods"]]}
        return out

    def despawn_all(self):
        """On the way out: take every puppet out of the world so none gets saved into the game."""
        for key in list(self.puppet_ids):
            self.despawn(key)
        self.mail.flush()                                # (never raises: a held file is just skipped)

    # ------------------------------------------------------------------ support: clock mode, partner cards, self-test,
    # diagnostics (the panel's /selftest and /diag; cards in /status)
    @property
    def notice(self) -> str:
        return self.__dict__.get("_notice", "")

    @notice.setter
    def notice(self, text):
        if text and text != self.__dict__.get("_notice"):            # (kept for the diagnostics zip)
            RECENT.append({"time": time.time(), "kind": "notice", "text": str(text)[:500]})
        self.__dict__["_notice"] = text

    def clock_mode(self) -> str:
        m = self.cfg.get("clock_mode") or "host"
        return m if m in CLOCK_MODES else "host"

    def extra_state(self, st: dict) -> dict:
        """What my state carries besides position and action: my lot's name (looked up once per lot), my game's time
        and speed. clock_mode "host": only the leader shares "clock" (the one others follow); "time" is for showing."""
        site, lots = st.get("site"), self.__dict__.setdefault("_lot_names", {})
        if site not in (None, -1) and site not in lots:
            lots[site] = ""
            asyncio.get_running_loop().create_task(self.lookup_lot(site))
        c = self.my_clock
        out = {"lot": lots.get(site, ""), "speed": c[2] if c else None, "time": list(c[:2]) if c else None}
        if self.clock_mode() == "host" and self.leader and self.leader != self.me_id:
            out["clock"] = None                          # never pushes anyone's clock
        return out

    async def lookup_lot(self, site):
        lots = self._lot_names
        try:
            lots[site] = await self.g(self.game.site_name, site) or ""
        except GameError:
            asyncio.get_running_loop().call_later(30, lots.pop, site, None)      # (loading: ask again later)

    def leader_state(self) -> dict | None:
        r = self.remotes.get(self.leader or "")
        return r.state if r and r.state and time.time() - r.state_at < 10 else None

    async def follow_leader_clock(self):
        """clock_mode "host", not the leader: behind the leader's clock, jump forward to it; ahead of it on the same
        day (the leader paused), go back to it. Never across midnight backwards (that would be a day earlier): wait."""
        lc = (self.leader_state() or {}).get("clock")
        if not lc:
            return                                       # the leader is paused, away or not in the game
        mine, theirs = self.my_clock[0] * 60 + self.my_clock[1], lc[0] * 60 + lc[1]
        d = (theirs - mine) % 1440
        if CLOCK_SLACK <= d < 720:
            log.info("following the host's time: %d minutes forward", d)
            await self.g(self.game.call, "environment.time_skip", minutes=d)
        elif d >= 720 and mine - theirs >= CLOCK_SLACK and not self.__dict__.get("_no_set_time"):
            log.info("following the host's time: back to %02d:%02d", lc[0], lc[1])
            try:
                await self.g(self.game.call, "environment.set_time", hour=int(lc[0]), minute=int(lc[1]))
            except GameError as e:
                if "isn't reachable" not in str(e):
                    self._no_set_time = True             # (this game has no way back: wait for the host instead)
                    log.info("this game can't turn its clock back (%s): waiting for the host's time", e)

    async def follow_leader_speed(self):
        st, clock = self.leader_state(), self.my_clock
        if not st or not st.get("clock") or not clock or clock[3]:
            return                                       # the leader is paused or away / I'm paused: my own speed
        want = st.get("speed")
        if isinstance(want, (int, float)) and 0 < want <= 10 and abs(clock[2] - want) > 0.01:
            if time.time() - self.__dict__.get("_speed_set_at", 0) < 2:
                return                                   # (as in keep_speed: no tug of war with the game)
            self._speed_set_at = time.time()
            if self.__dict__.get("_speed_said") != want:
                self._speed_said = want
                log.info("following the host's game speed: %s", want)
            await self.g(self.game.call, "environment.time_speed", speed=want)

    def partner_cards(self) -> list[dict]:
        """Where each friend is and what they're doing, from their latest state (the panel's partner card)."""
        out, now = [], time.time()
        for r in self.remotes.values():
            st = r.state or {}
            act = st.get("act") or {}
            bag = act.get("bag") or ""
            with_ = act.get("player")
            out.append({
                "id": r.pid, "name": r.name, "version": getattr(r, "version", None), "leader": r.pid == self.leader,
                "zoi": f"{r.profile['first_name']} {r.profile['last_name']}".strip() if r.profile else "",
                "city": st.get("city") or "", "lot": st.get("lot") or "", "site": st.get("site"),
                "action": bag or None, "action_text": re.sub(r"(?<=[a-z])(?=[A-Z])|_+", " ", bag or st.get("anim") or "").strip(),
                "with": None if not with_ else "you" if with_ == self.me_id else
                getattr(self.remotes.get(with_), "name", None), "npc": bool(act.get("npc")), "anim": st.get("anim"),
                "outfit": st.get("outfit"), "sleep": bool(st.get("sleep")), "time": st.get("time") or st.get("clock"),
                "speed": st.get("speed"), "mood": st.get("mood"), "needs": st.get("needs"),
                "here": bool(r.pid in self.puppet_ids and not r.hidden),
                "away": bool(st and st.get("city") != self.my_city),
                "online": bool(r.state_at and now - r.state_at < 15),
                "presence": {k: r.presence[k] for k in ("world", "box", "busy", "mp")}
                if r.presence and now - r.presence["at"] < 15 and not (r.state_at and now - r.state_at < 5) else None,
                "last_seen_s": int(now - r.state_at) if r.state_at else None})
        return out

    def support_status(self) -> dict:
        return {"partners": self.partner_cards(), "clock_mode": self.clock_mode(),
                "via_lan": servers.address_text(*self.lan_override) if self.lan_override else None}

    async def self_test(self) -> list[dict]:
        """Checks run side by side, each within a few seconds: [{id, ok (true/false, null = doesn't apply), detail,
        hint_code (what the panel suggests when it failed), data}]."""
        host, port, _ = self.address()
        tailnet = False
        try:
            import ipaddress
            tailnet = ipaddress.ip_address(host) in servers.netinfo.TAILNET
        except ValueError:
            tailnet = host.endswith(".ts.net")

        def res(cid, ok, detail, hint=None, **data):
            return {"id": cid, "ok": ok, "detail": detail, "hint_code": None if ok or ok is None else hint, "data": data}

        async def game_api():
            url = self.game.game_url()
            mcp_port = int(url.rsplit(":", 1)[1].split("/")[0]) if url.count(":") >= 2 else None
            try:
                st = await asyncio.wait_for(self.g(self.game.call, "game.get_state"), 4)
                return res("game_api", True, f"inZOI answers on {url} ({st.get('phase') or 'up'})", port=mcp_port)
            except (GameError, asyncio.TimeoutError) as e:
                return res("game_api", False, f"inZOI doesn't answer on {url}: {e or 'no answer'}", "start_game",
                           port=mcp_port)

        async def mod_loaded():
            age = time.time() - self.mod["beat"] if self.mod["beat"] else None
            if age is not None and age < 15:
                return res("mod_loaded", True, f"the in-game mod is {self.mod['state']} (heard {age:.0f} s ago)",
                           age_s=round(age, 1), puppets=len(self.puppet_ids))
            if self.world == "out":
                return res("mod_loaded", False, "the mod runs once a save is loaded", "load_save", age_s=age)
            return res("mod_loaded", False, f"the in-game mod is quiet ({self.mod['state']})", "reload_mod", age_s=age)

        async def server_reachable():
            if not host:
                return res("server_reachable", False, "no server picked yet", "pick_server")
            start = time.monotonic()
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(host, port), 4)
                w.close()
                return res("server_reachable", True, f"{servers.address_text(host, port)} answers",
                           address=servers.address_text(host, port), ms=int((time.monotonic() - start) * 1000),
                           via_lan=bool(self.lan_override))
            except (OSError, asyncio.TimeoutError) as e:
                hint = "restart_program" if self.on_own_server() else "check_tailscale" if tailnet else "check_server"
                return res("server_reachable", False, f"{servers.address_text(host, port)}: {servers.why(e)}", hint,
                           address=servers.address_text(host, port))

        async def logged_in():
            if self.writer and self.me_id:
                return res("logged_in", True, f"on {(self.server_info or {}).get('name') or host} as {self.cfg['name']}",
                           players=len(self.remotes))
            hint = {"wrong_password": "check_password", "version_mismatch": "update_partner", "banned": "banned",
                    "full": "server_full"}.get(self.notice_code or "", "wait_connect" if host else "pick_server")
            return res("logged_in", False, self.connect_block or self.notice or "not connected", hint,
                       code=self.notice_code)

        async def versions_match():
            theirs = {r.name: r.version for r in self.remotes.values() if getattr(r, "version", None)}
            if (self.server_info or {}).get("version"):
                theirs["server"] = self.server_info["version"]
            if not theirs:
                return res("versions_match", None, "nobody to compare with yet", mine=VERSION)
            diff = {k: v for k, v in theirs.items() if str(v) != VERSION}
            if not diff:
                return res("versions_match", True, f"everyone has {VERSION}", mine=VERSION, theirs=theirs)

            def key(v):
                return tuple(int(x) if x.isdigit() else 0 for x in str(v).split("."))
            older = any(key(v) > key(VERSION) for v in diff.values())
            return res("versions_match", False, f"you have {VERSION}; " + ", ".join(f"{k} has {v}" for k, v in diff.items()),
                       "update_self" if older else "update_partner", mine=VERSION, theirs=theirs)

        async def tailscale():
            ts = await asyncio.to_thread(servers.netinfo.tailscale_status)
            need = tailnet or bool(self.hosting)         # (the partner reaches a host over Tailscale)
            if not ts["installed"]:
                return res("tailscale", False if need else None, "Tailscale isn't installed", "install_tailscale")
            if ts["running"] and ts["online"]:
                return res("tailscale", True, f"Tailscale is on ({ts['ip']})", ip=ts["ip"])
            return res("tailscale", False if need else None, f"Tailscale is {ts.get('state') or 'off'}",
                       "check_tailscale", state=ts.get("state"))

        async def nordvpn_running():
            procs = await asyncio.to_thread(servers.netinfo.running_processes, ("nordvpn.exe", "nordvpn-service.exe"))
            if "nordvpn.exe" in procs:
                return res("nordvpn_running", False, "NordVPN is open: it can hide the other PC (LAN invisibility)",
                           "disable_nordvpn", processes=sorted(procs))
            return res("nordvpn_running", True, "NordVPN isn't open" + (" (only its service runs)" if procs else ""),
                       processes=sorted(procs))

        async def partner_here():
            """The point of it all: the partner's Zoi in my world (and why not, when it isn't)."""
            if not (self.writer and self.me_id) or not self.remotes:
                return res("partner_here", None, "nobody else on the server yet")
            if not self.mp_active():
                return res("partner_here", False, "multiplayer waits: this isn't your multiplayer save (or a question "
                           "about it is open)", "answer_save")
            if self.world in ("paused", "stopped"):
                return res("partner_here", False, "your game is paused: nothing comes in until it runs", "unpause")
            cards = self.partner_cards()
            here = [c["name"] for c in cards if c["here"]]
            if here:
                return res("partner_here", True, ", ".join(here) + " in your world", names=here)
            c = cards[0]
            pr = c.get("presence") or {}
            if c.get("away"):
                return res("partner_here", False, f"{c['name']} is in another city ({c.get('city')})", "same_city",
                           name=c["name"])
            if not c["online"]:
                why = ("at the main menu or loading" if pr.get("world") == "out" else "paused" if pr.get("world") in
                       ("paused", "stopped") else "in another save or answering a question" if pr else "not in the game")
                return res("partner_here", False, f"{c['name']}'s game isn't sending yet ({why})", "partner_game",
                           name=c["name"])
            return res("partner_here", False, f"{c['name']}'s Zoi is on its way into your world", "wait_puppet",
                       name=c["name"])

        checks = (game_api, mod_loaded, server_reachable, logged_in, versions_match, partner_here, tailscale,
                  nordvpn_running)

        async def guarded(fn):
            try:
                return await asyncio.wait_for(fn(), 8)
            except Exception as e:                       # noqa: BLE001 - a failed check is a result too
                return res(fn.__name__, False, f"the check failed: {e!r}")
        return list(await asyncio.gather(*(guarded(fn) for fn in checks)))

    def diag_status(self, tests: list | None = None) -> dict:
        host, port, _ = self.address()
        hosting = self.hosting_state() if self.hosted else None
        if hosting:
            hosting = {k: v for k, v in hosting.items() if k != "admin_code"}
        import platform
        return {
            "time": datetime.datetime.now().isoformat(timespec="seconds"), "version": VERSION, "python": sys.version,
            "platform": platform.platform(), "frozen": updater.frozen(), "name": self.cfg.get("name"),
            "mode": self.cfg.get("mode"), "clock_mode": self.clock_mode(), "sync_time": self.cfg.get("sync_time", True),
            "connection": {"connected": bool(self.writer and self.me_id), "address": servers.address_text(host, port)
                           if host else None, "primary": self.cfg.get("server"), "port": self.cfg.get("port"),
                           "via_lan": bool(self.lan_override), "me": self.me_id, "leader": self.leader,
                           "connected_at": self.connected_at, "server": self.server_info, "notice": self.notice,
                           "notice_code": self.notice_code, "blocked": self.connect_block},
            "hosting": hosting, "lan": self.lan_view(),
            "game": {"world": self.world, "url": self.game.game_url(), "mod": self.mod, "city": self.my_city,
                     "entity": self.my_entity, "clock": self.my_clock, "save": self.mp_name(),
                     "save_active": self.mp_active()},
            "partners": self.partner_cards(),
            "puppets": {"ids": self.puppet_ids, "despawning": list(self.despawning)},
            "mods": self.mods_status(), "update": self.update_view(), "selftest": tests, "recent": list(RECENT)}

    async def diagnostics(self) -> dict:
        tests = await self.self_test()
        extra = {}
        mp = sys.modules.get("inzoi_mp_server")
        if self.hosted and mp is not None:
            extra["server_recent.log"] = "\n".join(map(str, mp.RECENT))
        path = await asyncio.to_thread(write_diagnostics, self.diag_status(tests), dict(self.cfg), extra)
        log.info("diagnostics saved: %s", path)
        return {"ok": True, "path": str(path)}

    def support_routes(self) -> dict:
        async def selftest(_):
            return {"ok": True, "checks": await self.self_test()}

        async def diag(_):
            return await self.diagnostics()
        return {"/selftest": selftest, "/diag": diag}

    # ------------------------------------------------------------------ in-game panel
    def ui_routes(self) -> dict:
        async def status(_):
            players = []
            for r in self.remotes.values():
                st = r.state or {}
                act = st.get("act") or {}
                players.append({"id": r.pid, "name": r.name,
                                "zoi": f"{r.profile['first_name']} {r.profile['last_name']}".strip() if r.profile else "",
                                "here": bool(r.pid in self.puppet_ids and not r.hidden),
                                "away": bool(st and st.get("city") != self.my_city),
                                "doing": act.get("bag", "")})
            return {"version": VERSION, "connected": bool(self.writer and self.me_id),
                    "server": "this PC" if self.hosting and self.play_local else (self.server_info or {}).get("name")
                    or self.cfg.get("server") or "no server",
                    "name": self.cfg["name"], "mod": self.mod["state"], "notice": self.notice,
                    "notice_code": self.notice_code,
                    "paused": self.world in ("paused", "stopped"), "game": self.world, "port": self.address()[1],
                    "host": self.leader == self.me_id, "sync_time": bool(self.cfg.get("sync_time", True)),
                    "players": players, "chat": list(self.chat)[-30:], "mods": self.mods_status(),
                    "save": {"name": self.mp_name() if self.cfg.get("mp_save") else None,
                             "active": self.mp_active(), "box": self.save_box},
                    "update": {**{k: (self.update or {}).get(k) for k in ("version", "state", "done", "total")},
                               "box": self.update_box_view()},
                    "world": {"server": (self.world_meta or {}).get("version"), "local": self.local_world().get("version"),
                              "by": (self.world_meta or {}).get("by"), "box": self.world_status_box()},
                    "household": self.household_view(), **self.couple_view(), **self.support_status()}

        async def chat(body):
            text = str(body.get("text", "")).strip()[:500]
            if not text:
                return {"ok": False}
            if not self.writer:
                return {"error": "not connected to the server"}
            await self.send({"t": "chat", "text": text})
            return {"ok": True}

        async def join(body):
            eid = self.puppet_ids.get(body.get("id"))
            if not eid:
                return {"error": "they're not in your world right now"}
            await self.g(self.game.go_to, eid)
            return {"ok": True}

        async def settings(body):
            changed = False
            if self.hosting and self.play_local:  # the panel shows "this PC": keep the friend's server for next time
                body = {k: v for k, v in body.items() if k not in ("server", "port", "password")}
            for k in ("name", "server", "password"):
                if k in body and (str(body[k]).strip() or k == "password") and body[k] != self.cfg.get(k):
                    self.cfg[k] = str(body[k]).strip()
                    changed = True
            if "sync_time" in body:
                self.cfg["sync_time"] = bool(body["sync_time"])
                self.world_synced = 0.0
                save_config(self.cfg)
            if body.get("clock_mode") in CLOCK_MODES:    # "host": the leader's clock is everyone's; "catchup"
                self.cfg["clock_mode"] = body["clock_mode"]
                self.world_synced = 0.0
                save_config(self.cfg)
            if "port" in body and str(body["port"]).isdigit():
                changed = changed or int(body["port"]) != int(self.cfg["port"])
                self.cfg["port"] = int(body["port"])
            if changed:
                save_config(self.cfg)
                if self.cfg.get("server"):
                    self.remember(servers.address_text(self.cfg["server"], self.cfg["port"]), self.cfg["password"])
                self.switch_server()             # reconnect with the new settings
            return {"ok": True, "name": self.cfg["name"],
                    "server": "this PC" if self.hosting and self.play_local else self.cfg.get("server"),
                    "port": self.address()[1], "sync_time": bool(self.cfg.get("sync_time", True)),
                    "clock_mode": self.clock_mode()}

        async def house_list(_):
            added = self.cfg.get("added_houses", {})
            shared = [{"owner": o, "name": h.get("name"), "by": h.get("by") or o, "lot": h.get("lot") or "",
                       "added": added.get(o) == h.get("updated")}
                      for o, h in self.houses.items() if o != self.me_id]
            mine = self.houses.get(self.me_id)
            return {"library": await self.g(houses.my_houses), "shared": shared,
                    "sharing": mine.get("name") if mine else None}

        async def share(body):
            if not self.writer:
                return {"error": "not connected to the server"}
            house = await self.g(houses.pack, str(body.get("id", "")))
            house["by"] = self.cfg["name"]
            if self.home:
                house["lot"] = await self.g(self.game.site_name, self.home["site"])
            await self.send({"t": "house_put", "house": house})
            log.info("shared your house %s", house["name"])
            return {"ok": True, "name": house["name"]}

        async def add_house(body):
            owner = str(body.get("owner", ""))
            if owner not in self.houses or not self.writer:
                return {"error": "that house isn't shared any more"}
            fut = self.house_waiters[owner] = asyncio.get_running_loop().create_future()
            await self.send({"t": "house_get", "owner": owner})
            m = await asyncio.wait_for(fut, 30)
            if not m.get("house"):
                return {"error": "that house isn't shared any more"}
            meta = m.get("meta") or {}
            title = await self.g(houses.unpack, m["house"], meta.get("by") or owner)
            self.cfg.setdefault("added_houses", {})[owner] = meta.get("updated")
            save_config(self.cfg)
            log.info("added %s to your library", title)
            return {"ok": True, "title": title}

        async def mods_answer(body):
            self.answer_mods(str(body.get("key", "")), body.get("answer") == "yes")
            return self.mods_status()

        async def save_answer(body):
            self.save_action(str(body.get("key", "")), str(body.get("action", "")))
            return {"ok": True, "box": self.save_box}

        async def update_answer(body):
            self.update_action(str(body.get("key", "")), str(body.get("action", "")))
            return {"ok": True, "box": self.update_box_view(), "update": self.update_view()}

        async def update_now(_):
            return await self.update_now()

        async def world_answer(body):
            self.world_action(str(body.get("key", "")), str(body.get("action", "")))
            return {"ok": True, "box": self.world_box}

        return {"/status": status, "/chat": chat, "/join": join, "/settings": settings,
                "/houses": house_list, "/share": share, "/add_house": add_house, "/mods": mods_answer,
                "/save": save_answer, "/update": update_answer, "/update/now": update_now, "/world": world_answer, **self.server_routes(),
                **self.couple_routes(), **self.support_routes()}


def setup_logging(verbose: bool):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    out = logging.StreamHandler()
    out.setFormatter(fmt)
    root.addHandler(out)
    f = RotatingFileHandler(CONFIG_DIR / "companion.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    f.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(f)


def on_console_close(fn):
    """Run fn when the console window is closed with its X (or Windows logs off / shuts down), not only on Ctrl+C.
    Windows allows a few seconds before it ends the process."""
    if os.name != "nt":
        return
    import ctypes

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
    def handler(event):
        if event in (2, 5, 6):                   # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
            fn()
            time.sleep(0.5)
            return 1
        return 0                                 # Ctrl+C: Python's own handler raises KeyboardInterrupt
    on_console_close.handler = handler           # keep the callback alive
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)


def main():
    ap = argparse.ArgumentParser(description="inZOI Multiplayer companion")
    ap.add_argument("--server")
    ap.add_argument("--port", type=int)
    ap.add_argument("--name")
    ap.add_argument("--password")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    setup_logging(args.verbose)
    cfg = load_config(args)
    log.info("inZOI Multiplayer %s - server %s:%s, your name %s", VERSION, cfg["server"], cfg["port"], cfg["name"])
    comp = Companion(cfg)
    on_console_close(comp.shutdown)
    try:
        asyncio.run(comp.run())
    except KeyboardInterrupt:
        comp.shutdown()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
