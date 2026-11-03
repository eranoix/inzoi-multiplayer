"""The servers page's side of the companion (mixed into Companion): servers you've saved and the one you host
("Private servers"), public servers from the directories ("Public servers"), checking a server's version, mods and
room before joining, joining (switching from one server to another), hosting from the page, and the admin console.

The page itself is client/page/ (servers.html, .css, .js), served by the companion at http://127.0.0.1:24243/servers
and opened full screen over the game from the "Multiplayer" item on the main menu (client/menu.py) or the in-game
panel.

Each server keeps its own multiplayer save: cfg["by_server"][server id] = {mp_save, world}, swapped in when the program
joins that server (adopt_server), so coming back to a server brings back the save played there.
"""
import argparse
import asyncio
import importlib.util
import json
import logging
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

import firewall
import ingame
import menu
import mods
import netinfo
from game import GameError

log = logging.getLogger("servers")
PROTOCOL = 1
DIRECTORY_URL = os.environ.get("INZOI_MP_DIRECTORY_URL",
                               "https://raw.githubusercontent.com/Darker117/inzoi-multiplayer/main/directory.json")
FRESH = 20                       # seconds a server's info stays fresh on the page
LAN_AFTER = 15                   # seconds without reaching the picked server before looking for it on the home network
LOCAL = ("127.0.0.1", "localhost", "::1")


def split_address(text: str, default_port: int = 24242) -> tuple[str, int]:
    text = str(text or "").strip()
    host, sep, port = text.rpartition(":") if text.count(":") == 1 or text.startswith("[") else ("", "", "")
    if not sep:
        return text, default_port
    return host.strip("[]") or text, int(port) if port.isdigit() else default_port


def address_text(host: str, port: int) -> str:
    return host if int(port) == 24242 else f"{host}:{port}"


def same_server(a: str, b: str) -> bool:
    (ha, pa), (hb, pb) = split_address(a), split_address(b)
    return ha.lower() == hb.lower() and pa == pb


async def ask(address: str, msg: dict, reply: str, timeout: float = 5) -> tuple[dict, int]:
    """One question to a server without joining it: (its answer, round trip in ms)."""
    host, port = split_address(address)
    start = time.monotonic()
    r, w = await asyncio.wait_for(asyncio.open_connection(host, port, limit=8 * 1024 * 1024), timeout)
    try:
        w.write((json.dumps(msg) + "\n").encode())
        await w.drain()
        while True:
            line = await asyncio.wait_for(r.readline(), timeout)
            if not line:
                raise ConnectionError("the server closed the connection")
            m = json.loads(line)
            if m.get("t") in (reply, "error"):
                return m, int((time.monotonic() - start) * 1000)
    finally:
        w.close()


def hang_up(w, grace: float = 2.0):
    """Leave a server connection on purpose: what's written (a "bye") still goes out to a healthy server, but a stuck
    one can't keep the connection, nor every task waiting in drain() on it, alive: it's aborted after grace s (which
    also ends the session's reader, so the program moves on at once instead of waiting for silence)."""
    try:
        w.close()
        asyncio.get_running_loop().call_later(grace, w.transport.abort)
    except Exception:                                  # noqa: BLE001 - (no event loop: the program is closing)
        pass


def why(e: BaseException) -> str:
    """A connection problem in words a player understands."""
    if isinstance(e, ConnectionRefusedError):
        return "nothing answers there: is the server running, and is the address right?"
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return "no answer: the address may be wrong, or that PC isn't shared with you on Tailscale"
    if isinstance(e, OSError) and getattr(e, "errno", None) in (11001, -2, -3, 8):
        return "that address doesn't exist"
    return str(e) or e.__class__.__name__


def compat(info: dict, local: list[dict]) -> dict:
    """Can this PC join that server, and what will it need? {verdict, line, problems, mods: [...], missing, size}.
    verdict: ready, download (mods to get), full, incompatible, old (a server too old to say)."""
    if info.get("old"):
        return {"verdict": "old", "line": "Older server: it can't be checked before joining", "problems": [],
                "mods": [], "missing": 0, "size": 0}
    problems = []
    if info.get("protocol") != PROTOCOL:
        problems.append(f"It runs inZOI Multiplayer {info.get('version')}, which can't play with yours "
                        f"({'update yours' if (info.get('protocol') or 0) > PROTOCOL else 'the server needs updating'}).")
    have = {m["id"]: m for m in local if m.get("enabled", True)}
    rows = []
    for m in (info.get("mods") or {}).get("list", []):
        mine = have.get(m["id"])
        state = "ok" if mine and mods.fp(mine) == m["fp"] else "different" if mine else "missing"
        rows.append({"name": m["name"], "version": m.get("version", ""), "size": m.get("size", 0), "state": state,
                     "mine": (mine or {}).get("version", "")})
    need = [r for r in rows if r["state"] != "ok"]
    size = sum(r["size"] for r in need)
    theirs = {m["id"] for m in (info.get("mods") or {}).get("list", [])}
    extra = sorted(m["name"] for i, m in have.items() if i not in theirs and i != "InZOIMultiplayer")
    full = (info.get("players") or 0) >= (info.get("max_players") or 1)
    if problems:
        verdict, line = "incompatible", "Can't join: " + problems[0]
    elif full:
        verdict, line = "full", f"Full ({info.get('players')} of {info.get('max_players')} players)"
    elif need:
        verdict = "download"
        line = f"Join and get {len(need)} mod{'s' if len(need) != 1 else ''} ({size / 1048576:.1f} MB)"
    else:
        verdict, line = "ready", "Ready to join: you have all its mods" if rows else "Ready to join"
    if info.get("password") and verdict in ("ready", "download"):
        line += " (it needs a password)"
    return {"verdict": verdict, "line": line, "problems": problems, "mods": rows, "missing": len(need), "size": size,
            "extra": extra[:30], "source": (info.get("mods") or {}).get("source")}


class Servers:
    """The servers page's side of Companion. Uses self.cfg, self.game, self.send(), self.writer and friends."""

    def servers_init(self, server_data: Path, page_profile: Path):
        self.server_data = server_data                 # where a server hosted by this program keeps its data
        self.play_local = True                         # when hosting: play on that server (else on cfg["server"])
        self.hosted = None                             # (asyncio server, its background tasks) while hosting
        self.server_info = None                        # the joined server's summary (welcome / server_info)
        self.is_admin = False
        self.connect_block = None                      # why the program stopped retrying (wrong password, banned...)
        self.wake = asyncio.Event()                    # connect again now (a server was picked)
        self.probes = {}                               # address -> {at, info, error}
        self.public = {"at": 0.0, "list": [], "errors": {}, "busy": False}
        self.admin_waiters = {}
        self.admin_ref = 0
        self.joining = None                            # the server being joined, by name, for messages
        self.local_mods = (0.0, [])
        self.lan = {"at": 0.0, "list": [], "busy": False, "tried": -1e9}  # servers found on the home network (UDP)
        self.lan_override = None                       # (host, port): the picked server's home-network address, for now
        self.lan_fail_since = None                     # since when the picked server can't be reached (monotonic)
        self.page = menu.Page(page_profile)
        self.menu_item = None
        # the Multiplayer page inside the game (UE4SS script): fed through files next to the page's browser profile
        self.ingame = ingame.InGame(page_profile.parent / "ingame", self.ingame_api, self.ingame_open_mods,
                                    self.ingame_toast)
        if not self.cfg.get("servers") and self.cfg.get("server") and self.cfg["server"] not in LOCAL:
            self.remember(address_text(self.cfg["server"], self.cfg.get("port", 24242)), self.cfg.get("password", ""))

    # ------------------------------------------------------------------ saved servers ("Private servers")
    def saved(self) -> list[dict]:
        return self.cfg.setdefault("servers", [])

    def remember(self, address: str, password: str | None = None, name: str | None = None, sid: str | None = None,
                 joined: bool = False, server_name: str | None = None, only_known: bool = False) -> dict | None:
        """Save a server in Private Servers (or update it). name: what the player calls it; server_name: its own."""
        host, port = split_address(address)
        address = address_text(host, port)
        entry = next((s for s in self.saved() if same_server(s["address"], address)), None)
        if entry is None:
            if only_known:
                return None
            entry = {"address": address, "password": "", "name": "", "id": "", "added": time.time(), "last": 0}
            self.saved().append(entry)
        for k, v in (("password", password), ("name", name), ("id", sid), ("server_name", server_name)):
            if v is not None:
                entry[k] = v
        if joined:
            entry["last"] = time.time()
        self.save_cfg()
        return entry

    def forget(self, address: str):
        self.cfg["servers"] = [s for s in self.saved() if not same_server(s["address"], address)]
        self.save_cfg()

    def on_own_server(self) -> bool:
        return bool(self.hosting) and self.play_local

    def current_address(self) -> str:
        if self.on_own_server():
            return address_text("127.0.0.1", self.hosting[0])
        return address_text(self.cfg.get("server") or "", self.cfg.get("port", 24242)) if self.cfg.get("server") else ""

    # ------------------------------------------------------------------ joining
    async def join(self, address: str, password: str = "", name: str | None = None):
        host, port = split_address(address)
        if not host:
            raise ValueError("type the server's address")
        if self.hosting and host in LOCAL and port == self.hosting[0]:
            self.play_local = True
        else:
            self.play_local = False
            self.cfg["server"], self.cfg["port"], self.cfg["password"] = host, port, password
            self.remember(address_text(host, port), password, joined=True, only_known=True)
        self.joining = name or address_text(host, port)
        log.info("joining %s", address_text(host, port))
        self.switch_server()

    def switch_server(self):
        """Drop the server connection and connect again at once to whatever address() says now."""
        self.connect_block = None
        self.lan_override, self.lan_fail_since = None, None     # another server picked: its own address first
        self.server_info, self.is_admin = None, False
        self.modset, self.world_meta, self.houses, self.leader = None, None, {}, None
        if self.mod_offer and self.mod_offer["state"] not in ("downloading", "installing", "waiting"):
            self.mod_offer = None
        self.public["at"] = 0.0                        # the page's lists show where we are: look again
        self.save_cfg()
        if self.writer:
            self.say_bye()
            hang_up(self.writer)
        self.wake.set()

    def say_bye(self):
        """Leaving the server on purpose: say so, so friends see it at once (a dropped connection lingers). (Never in
        the middle of a message going out in pieces: then it would only follow that one, if the connection lasts.)"""
        try:
            if self.writer and self.me_id:
                self.write_soon(self.writer, b'{"t":"bye"}\n')
        except Exception:                              # noqa: BLE001 - best effort
            pass

    def adopt_server(self, summary: dict | None, admin: bool | None):
        """Joined a server (or its settings changed): know who it is, and switch to its multiplayer save."""
        if not isinstance(summary, dict) or not summary or self.wake.is_set():
            return                                     # (another server was picked: this one's id isn't filed under
                                                       # the new address, nor its save swapped in)
        self.server_info = summary
        if admin is not None:
            self.is_admin = bool(admin)
        sid, old = summary.get("id"), self.cfg.get("server_id")
        if not self.on_own_server():
            entry = self.saved_entry(self.current_address())
            if entry or not summary.get("public"):         # a private server you joined goes in your list
                self.remember(self.current_address(), password=self.cfg.get("password", ""), sid=sid,
                              server_name=summary.get("name") or "", joined=True)
        if not sid or sid == old:
            return
        by = self.cfg.setdefault("by_server", {})
        if old:                                        # leave the other server's save behind, for when we're back
            by[old] = {"mp_save": self.cfg.get("mp_save"), "world": self.cfg.get("world")}
        if old or sid in by:
            mine = by.get(sid, {})
            self.cfg["mp_save"], self.cfg["world"] = mine.get("mp_save"), mine.get("world")
            s, mp = self.save_session, self.cfg.get("mp_save") or {}
            s["mp"] = (s["folder"] == mp.get("folder")) if s.get("folder") and mp.get("folder") else None
            self.save_box, self.auto_loaded, self.world_asked = None, False, False
            log.info("this server's multiplayer save: %s", self.mp_name() if mp else "none yet")
            if s["mp"] is False and self.world != "out":
                self.show_paused()
        self.cfg["server_id"] = sid                    # (the first server: the save played so far is its save)
        self.save_cfg()

    # ------------------------------------------------------------------ looking at servers before joining
    async def check(self, address: str, force: bool = False) -> dict:
        key = address_text(*split_address(address))
        p = self.probes.get(key)
        if p and not force and time.time() - p["at"] < FRESH:
            return p
        try:
            info, ping = await ask(key, {"t": "info"}, "info", timeout=4)
            if info.get("t") == "error":                       # a server from before 0.6: it only talks after hello
                info = {"old": True, "name": "", "error": info.get("error")}
            info["ping"] = ping
            p = {"at": time.time(), "info": info, "error": None}
        except (OSError, ValueError, asyncio.TimeoutError, ConnectionError) as e:
            p = {"at": time.time(), "info": None, "error": why(e)}
        self.probes[key] = p
        return p

    async def my_mods(self) -> list[dict]:
        if self.mods_local is not None:
            return self.mods_local
        at, found = self.local_mods
        if time.time() - at > 60:
            found = await asyncio.to_thread(mods.scan)
            self.local_mods = (time.time(), found)
        return found

    async def row(self, address: str, entry: dict | None = None, listing: dict | None = None,
                  force: bool = False) -> dict:
        """One server as the page lists it."""
        p = await self.check(address, force)
        info = p["info"] or {}
        out = {"address": address_text(*split_address(address)), "saved": entry is not None,
               "name": (entry or {}).get("name") or info.get("name") or (entry or {}).get("server_name")
               or (listing or {}).get("name") or address_text(*split_address(address)),
               "server_name": info.get("name") or (entry or {}).get("server_name") or (listing or {}).get("name"),
               "online": p["info"] is not None, "error": p["error"], "ping": info.get("ping"),
               "current": bool(self.current_address()) and same_server(address, self.current_address()),
               "connected": False, "here": bool(self.hosting) and split_address(address)[0] in LOCAL
               and split_address(address)[1] == self.hosting[0], "password_saved": bool((entry or {}).get("password"))}
        out["connected"] = out["current"] and bool(self.writer and self.me_id)
        for k in ("id", "description", "motd", "players", "max_players", "names", "password", "public", "version",
                  "world", "host", "hosted", "uptime", "directory", "old"):
            out[k] = info.get(k, (listing or {}).get(k))
        out["mods_count"] = len((info.get("mods") or {}).get("list", [])) if info.get("mods") else \
            (listing or {}).get("mods_count")
        out["check"] = compat(info, await self.my_mods()) if p["info"] else None
        return out

    def directories(self) -> list[str]:
        """Where to ask for public servers: the server we're on and any saved one that keeps a list, the ones in
        settings, and the shared list from GitHub."""
        out = list(self.cfg.get("directories") or []) + list(self.cfg.get("directory_cache") or [])
        if self.server_info and self.server_info.get("directory") and self.current_address():
            out.insert(0, self.current_address())
        for s in self.saved():
            info = (self.probes.get(address_text(*split_address(s["address"]))) or {}).get("info") or {}
            if info.get("directory"):
                out.append(s["address"])
        unique = []
        for d in out:
            if d and not any(same_server(d, u) for u in unique):
                unique.append(d)
        return unique

    async def refresh_public(self):
        if self.public["busy"]:
            return
        self.public["busy"] = True
        try:
            found, errors = {}, {}
            for d in self.directories():
                try:
                    answer, _ = await ask(d, {"t": "dir_list"}, "dir_list", timeout=5)
                    if answer.get("t") == "error" or answer.get("error"):
                        errors[d] = answer.get("error") or "doesn't keep a list"
                    for s in answer.get("servers") or []:
                        found.setdefault(s["id"], s)
                except (OSError, ValueError, asyncio.TimeoutError, ConnectionError) as e:
                    errors[d] = why(e)
            rows = await asyncio.gather(*(self.row(s["address"], self.saved_entry(s["address"]), s) for s in found.values()))
            self.public.update(at=time.time(), list=list(rows), errors=errors)
        finally:
            self.public["busy"] = False

    def saved_entry(self, address: str) -> dict | None:
        return next((s for s in self.saved() if same_server(s["address"], address)), None)

    async def directory_loop(self):
        """The shared list of directories, kept on GitHub (so it can change without a new version)."""
        while True:
            try:
                raw = await asyncio.to_thread(lambda: urllib.request.urlopen(DIRECTORY_URL, timeout=15).read())
                dirs = [str(d) for d in json.loads(raw).get("directories", []) if str(d).strip()][:20]
                if dirs != self.cfg.get("directory_cache"):
                    self.cfg["directory_cache"] = dirs
                    self.save_cfg()
            except (OSError, ValueError) as e:
                log.debug("directory list: %s", e)
            await asyncio.sleep(6 * 3600)

    # ------------------------------------------------------------------ LAN discovery
    # Servers answer a UDP broadcast on their port (b"INZOI-MP?"). When the picked server (usually the host's Tailscale
    # address) can't be reached for LAN_AFTER seconds, or none is picked, the program looks for it on the home network
    # and plays there for now (lan_override): the server id it joined last wins, else the only server that answered. The
    # home-network address is remembered as an alternate (cfg["lan_alt"]), never in place of the picked address: the
    # next start tries that one first again.
    async def discover_lan(self) -> list[dict]:
        if self.lan["busy"]:
            return self.lan["list"]
        self.lan["busy"] = True
        try:
            found = await asyncio.to_thread(netinfo.discover, int(self.cfg.get("port") or 24242))
        except OSError as e:
            log.debug("LAN discovery: %s", e)
            found = []
        finally:
            self.lan["busy"] = False
        mine = self.hosted[0].mp_server.store.settings.get("id") if self.hosted else None
        keep = ("address", "name", "id", "version", "protocol", "password", "players", "max_players", "hosted", "ip",
                "port")
        self.lan.update(at=time.time(), list=[{k: s.get(k) for k in keep} for s in found
                                              if not mine or s.get("id") != mine])
        return self.lan["list"]

    def lan_view(self) -> list[dict]:
        cur = address_text(*self.lan_override) if self.lan_override else None
        return [{**s, "current": bool(cur) and same_server(s["address"], cur),
                 "saved": self.saved_entry(s["address"]) is not None} for s in self.lan["list"]]

    def lan_pick(self, found: list[dict]) -> dict | None:
        """The server to play on from what answered: the one joined last (by id), else the only one."""
        primary = self.cfg.get("server") and address_text(self.cfg["server"], self.cfg.get("port", 24242))
        ids = {self.cfg.get("server_id"), (self.saved_entry(primary) or {}).get("id") if primary else None} - {None, ""}
        ok = [s for s in found if s.get("protocol", PROTOCOL) == PROTOCOL]
        return next((s for s in ok if s.get("id") in ids), None) or (ok[0] if len(ok) == 1 else None)

    async def lan_loop(self):
        while True:
            await asyncio.sleep(5)
            try:
                await self.lan_tick()
            except Exception as e:                         # noqa: BLE001 - never worth stopping the program
                log.debug("LAN discovery: %r", e, exc_info=e)

    async def lan_tick(self, after: float = LAN_AFTER):
        if self.on_own_server() or (self.writer and self.me_id):
            self.lan_fail_since = None
            return
        primary = self.cfg.get("server") and address_text(self.cfg["server"], self.cfg.get("port", 24242))
        if primary and self.connect_block:
            return                                     # wrong password, banned...: another address won't help
        now = time.monotonic()
        self.lan_fail_since = self.lan_fail_since or now
        if (primary and now - self.lan_fail_since < after) or now - self.lan["tried"] < after:
            return
        self.lan["tried"] = now
        pick = self.lan_pick(await self.discover_lan())
        alt = (self.cfg.get("lan_alt") or {}).get(primary or "")
        if not pick and alt and not self.lan_override:  # nothing answered the broadcast: the address it had last time?
            try:
                info, _ = await ask(alt["address"], {"t": "info"}, "info", timeout=3)
                if info.get("id") and info["id"] == alt.get("id"):
                    pick = {**info, "address": alt["address"]}
            except (OSError, ValueError, asyncio.TimeoutError, ConnectionError):
                pass
        if not pick:
            if self.lan_override:                      # not there either: back to the picked address
                log.info("the server isn't on your home network either: trying %s again", primary)
                self.lan_override = None
                self.wake.set()
            return
        if not primary:                                # no server picked yet: this one is it
            log.info("found a server on your home network: %s (%s)", pick.get("name"), pick["address"])
            await self.join(pick["address"], "", pick.get("name"))
            return
        host, port = split_address(pick["address"])
        if self.lan_override == (host, port) or same_server(pick["address"], primary):
            return
        log.info("can't reach %s: playing on the same server on your home network (%s)", primary, pick["address"])
        self.lan_override = (host, port)
        self.cfg.setdefault("lan_alt", {})[primary] = {"address": pick["address"], "id": pick.get("id"),
                                                       "at": time.time()}
        self.save_cfg()
        self.connect_block = None
        if self.writer:
            hang_up(self.writer)
        self.wake.set()

    # ------------------------------------------------------------------ hosting from the page
    async def start_hosting(self, play: bool = True, **settings) -> dict:
        """Run a server in this program (Host and play). settings: port, password, motd, name, public, max_players."""
        if self.hosted:
            return self.hosting_state()
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
        import inzoi_mp_server as mpserver
        for k in ("port", "password", "motd", "name"):
            if settings.get(k) is not None:
                self.cfg[f"host_{k}"] = int(settings[k]) if k == "port" else str(settings[k])
        port = int(self.cfg.get("host_port") or 24242)
        args = argparse.Namespace(host="0.0.0.0", port=port, password=self.cfg.get("host_password", ""),
                                  motd=self.cfg.get("host_motd", ""), name=self.cfg.get("host_name") or None,
                                  max_players=int(settings["max_players"]) if settings.get("max_players") else None,
                                  data=str(self.server_data), hosted=True, directory=False,
                                  directories=[d for d in self.directories() if not same_server(d, f"127.0.0.1:{port}")])
        fw = await asyncio.to_thread(firewall.ensure, port, self.cfg)   # coop-casal: the friend can reach us
        if fw == "declined":
            self.notice, self.notice_code = "Windows Firewall wasn't opened: your partner may not reach you", "firewall_declined"
        try:
            self.hosted = await mpserver.start(args)
        except OSError as e:
            raise ValueError(f"can't host on port {port}: {e.strerror or e}. Is another server running on this PC?") \
                from None
        server = self.hosted[0].mp_server
        if settings.get("public") is not None:
            server.store.settings["public"] = bool(settings["public"])
            server.store.save_settings()
        self.hosting = (port, server.password)
        self.cfg["mode"] = "host"
        self.save_cfg()
        if play:
            self.play_local = True
            self.switch_server()
        return self.hosting_state()

    async def stop_hosting(self):
        if not self.hosted:
            return
        srv, tasks = self.hosted
        server = srv.mp_server
        if server.store.settings["public"]:
            await server.unlist()
        await server.broadcast({"t": "chat", "id": None, "name": "Server", "time": time.time(),
                                "text": "The host closed this server."})
        await asyncio.sleep(0.3)
        srv.close()
        for c in list(server.conns):
            await c.close()
        tasks.cancel()
        try:
            await tasks                                # (retrieved: no "exception was never retrieved" in the log)
        except (asyncio.CancelledError, Exception):     # noqa: BLE001
            pass
        self.hosted, self.hosting = None, None
        self.cfg["mode"] = "play"
        self.save_cfg()
        log.info("stopped hosting")
        if self.play_local:
            self.play_local = False
            self.switch_server()                       # back to the server picked before (if any)
        self.public["at"] = 0.0                        # it's gone from the public list: look again

    def hosting_state(self) -> dict | None:
        if not self.hosted:
            return None
        server = self.hosted[0].mp_server
        port = self.hosting[0]
        return {"port": port, "addresses": self.host_addresses(port), "settings": server.status()["settings"],
                "name": server.name(), "players": len(server.clients), "public_status": server.public_status,
                "playing_here": self.play_local, "admin_code": server.store.settings["admin_code"]}

    def host_addresses(self, port: int) -> list[dict]:
        """netinfo.addresses (it runs `tailscale status`), cached: refreshed in a thread at most every 30 s, so the
        page's polling never holds up the event loop (the first answer may come a moment later)."""
        c = self.__dict__.setdefault("_addr_cache", {"port": None, "at": -1e9, "list": [], "busy": False})
        if (c["port"] != port or time.monotonic() - c["at"] > 30) and not c["busy"]:
            c["busy"] = True

            def refresh():
                try:
                    c["list"], c["port"] = netinfo.addresses(port), port
                except Exception as e:                     # noqa: BLE001
                    log.debug("addresses: %s", e)
                finally:
                    c["at"], c["busy"] = time.monotonic(), False
            threading.Thread(target=refresh, daemon=True).start()
        return c["list"] if c["port"] == port else []

    # ------------------------------------------------------------------ admin console
    async def admin(self, op: str, local: bool = False, **fields) -> dict:
        """An admin action on the server we're on (or, local=True, the one this program hosts: no connection needed)."""
        if local:
            if not self.hosted:
                return {"ok": False, "error": "you're not hosting a server"}
            server = self.hosted[0].mp_server
            try:
                out = await server.admin_op(op, fields, self.cfg.get("name", "the host"))
            except (ValueError, TypeError, KeyError) as e:
                return {"ok": False, "error": str(e)}
            if op == "set":                       # the program starts it with these next time: keep them the same
                s = server.store.settings
                self.cfg.update(host_password=s["password"], host_motd=s["motd"], host_name=s["name"])
                self.hosting = (self.hosting[0], s["password"])
                self.save_cfg()
            return out
        if not (self.writer and self.me_id):
            return {"ok": False, "error": "not connected to a server"}
        self.admin_ref += 1
        ref = self.admin_ref
        fut = self.admin_waiters[ref] = asyncio.get_running_loop().create_future()
        try:
            await self.send({"t": "admin_claim" if op == "claim" else "admin", "op": op, "ref": ref, **fields})
            return await asyncio.wait_for(fut, 15)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "the server didn't answer"}
        except (OSError, ConnectionError):                 # (the connection dropped meanwhile)
            return {"ok": False, "error": "not connected to a server"}
        finally:
            self.admin_waiters.pop(ref, None)

    def on_admin_answer(self, m: dict):
        fut = self.admin_waiters.get(m.get("ref"))
        if fut and not fut.done():
            fut.set_result({k: v for k, v in m.items() if k != "t"})
        if m.get("op") == "claim" and m.get("ok"):
            self.is_admin = True

    # ------------------------------------------------------------------ the main menu item and the page
    async def menu_loop(self):
        """Show "Multiplayer" on the game's main menu while it's up and in front; hide it everywhere else."""
        if os.name != "nt" or os.environ.get("INZOI_MP_NO_MENU"):
            return
        await asyncio.to_thread(self.page.close)              # a page left open by an earlier run
        if importlib.util.find_spec("PIL") is None:              # menu.render draws the text with Pillow
            log.info("the main menu item needs Pillow (it's in the exe); open the servers page from the in-game panel")
            return
        loop = asyncio.get_running_loop()
        self.menu_item = await asyncio.to_thread(menu.MenuItem, lambda: loop.call_soon_threadsafe(self.open_page))
        layout = (None, None, None)                   # (menu window, item spot in UI units, UI size)
        while True:
            spot = None
            try:
                hwnd = await asyncio.to_thread(menu.game_window)                     if not self.page.is_open() and not self.ingame.game_running() else None
                if hwnd and menu.in_front(hwnd, self.menu_item.hwnd):
                    top = await self.g(self.top_window)
                    if top:
                        if layout[0] != top["window"]:
                            layout = (top["window"], *await self.g(self.menu_layout, top["window"]))
                        if layout[1]:
                            x, y, w, h = menu.client_rect(hwnd)
                            scale = w / layout[2][0]
                            spot = (x + layout[1][0] * scale, y + layout[1][1] * scale, scale)
            except (GameError, OSError, ValueError, KeyError, TypeError) as e:
                log.debug("menu item: %s", e)
            except Exception as e:                         # noqa: BLE001 - keep the menu item alive
                log.debug("menu item: %r", e, exc_info=e)
            if spot:
                self.menu_item.place(*spot)
            else:
                self.menu_item.hide()
            await asyncio.sleep(0.25 if spot else 0.6)

    # ------------------------------------------------------------------ the page inside the game (UE4SS script)
    async def ingame_loop(self):
        await self.ingame.run(self.version)

    async def ingame_api(self, path: str, body: dict) -> dict:
        """The in-game page's calls: the very same handlers the browser page's calls go to."""
        routes = getattr(self, "_ingame_routes", None)
        if routes is None:
            routes = self._ingame_routes = self.ui_routes()
        return await routes[path](body)

    async def ingame_open_mods(self):
        """Ask the game to open its Mods page: the script turns it into the servers page as it's built."""
        def click():
            res = self.game.call("ui.list_windows")
            text = res.get("_text", "") if isinstance(res, dict) else ""
            data = json.loads(text[text.index("{"):]) if "{" in text else res
            win = next((w["window"] for w in data.get("windows") or [] if w.get("ui_type") == "MainMenu"), None)
            if not win:
                return False
            self.game.call("ui.click", ref=f"{win}/BW_WBP_Button_Mod/BW_Button", force=True)
            return True
        try:
            if not await self.g(click):
                log.info("the servers page opens from the main menu")
        except (GameError, OSError, ValueError) as e:
            log.warning("couldn't open the servers page in the game: %s", e)

    async def ingame_toast(self, text: str):
        try:
            await self.g(self.game.call, "toast.show", message=text, immediate=True)
        except (GameError, OSError) as e:
            log.debug("toast: %s", e)

    def top_window(self) -> dict | None:
        """The game's main menu, if it's what's on top (no Mods page, popup or loading screen over it)."""
        res = self.game.call("ui.list_windows")
        text = res.get("_text", "") if isinstance(res, dict) else ""
        data = json.loads(text[text.index("{"):]) if "{" in text else res
        if (data.get("blocking") or {}).get("blocked"):
            return None
        wins = data.get("windows") or []
        if wins and wins[0].get("ui_type") == "MainMenu" and not wins[0].get("occluded"):
            return {**wins[0], "screen": data.get("screen") or [1920, 1080]}
        return None

    def menu_layout(self, window: str):
        res = self.game.call("ui.get_tree", window=window)
        text = res.get("_text", "") if isinstance(res, dict) else ""
        tree = json.loads(text[text.index("{"):]) if "{" in text else res
        return menu.item_spot(tree), tree.get("screen") or [1920, 1080]

    def shutdown(self):
        """The program is closing: take friends' Zois out of the game and close the page."""
        self.say_bye()
        self.despawn_all()
        if self.menu_item:
            self.menu_item.hide()
        self.page.close()

    def open_page(self):
        hwnd = menu.game_window()
        if self.menu_item:
            self.menu_item.hide()
        import webui
        self.page.open(f"http://127.0.0.1:{webui.PORT}/servers", menu.monitor_of(hwnd) if hwnd else None)
        log.info("opened the servers page")

    # ------------------------------------------------------------------ the page's calls
    def server_routes(self) -> dict:
        async def state(body):
            self.page.seen()                                   # the page asks every few seconds while it's open
            if time.time() - self.public["at"] > FRESH and not self.public["busy"]:
                asyncio.get_running_loop().create_task(self.refresh_public())
            if time.time() - self.lan["at"] > FRESH and not self.lan["busy"]:
                asyncio.get_running_loop().create_task(self.discover_lan())
            saved = await asyncio.gather(*(self.row(s["address"], s) for s in self.saved()))
            current = self.current_address()
            connected = bool(self.writer and self.me_id)
            for r in self.public["list"]:                  # (refreshed every FRESH seconds; where we are, now)
                r["current"] = bool(current) and same_server(r["address"], current)
                r["connected"] = r["current"] and connected
            if current and not any(same_server(current, s["address"]) for s in saved) and not self.on_own_server()                     and not (self.server_info or {}).get("public"):
                saved.append(await self.row(current))
            return {"name": self.cfg.get("name"), "version": self.version, "current": current,
                    "connected": bool(self.writer and self.me_id), "error": self.connect_block or self.notice,
                    "notice_code": getattr(self, "notice_code", None),
                    "server": self.server_info, "admin": self.is_admin, "players": len(self.remotes),
                    "saved": saved, "public": self.public["list"], "public_errors": self.public["errors"],
                    "public_at": self.public["at"], "directories": self.directories(),
                    "hosting": self.hosting_state(), "mods": self.mods_status(), "in_game": self.world != "out",
                    "save": self.mp_name() if self.cfg.get("mp_save") else None, "update": self.update_view(),
                    "lan": self.lan_view(),
                    "via_lan": address_text(*self.lan_override) if self.lan_override else None}

        async def check(body):
            entry = self.saved_entry(body.get("address", ""))
            return await self.row(str(body.get("address", "")), entry, force=True)

        async def join(body):
            address = str(body.get("address", ""))
            password = str(body.get("password") or (self.saved_entry(address) or {}).get("password", ""))
            await self.join(address, password, body.get("name"))
            return {"ok": True}

        async def save(body):
            address = str(body.get("address", "")).strip()
            if not split_address(address)[0]:
                return {"error": "type the server's address"}
            if body.get("old") and not same_server(body["old"], address):
                self.forget(body["old"])
            self.remember(address, body.get("password"), body.get("name"))
            return {"ok": True, "row": await self.row(address, self.saved_entry(address), force=True)}

        async def remove(body):
            self.forget(str(body.get("address", "")))
            return {"ok": True}

        async def host_start(body):
            return {"ok": True, "hosting": await self.start_hosting(play=body.get("play", True), **{
                k: body.get(k) for k in ("port", "password", "motd", "name", "public", "max_players")})}

        async def host_stop(body):
            await self.stop_hosting()
            return {"ok": True}

        async def admin(body):
            body = dict(body)
            op = str(body.pop("op", ""))
            return await self.admin(op, local=bool(body.pop("local", False)), **body)

        async def page_close(body):
            await asyncio.to_thread(self.page.close)
            return {"ok": True}

        async def page_open(body):
            self.open_page()
            return {"ok": True}

        async def mods_get(body):
            o = self.mod_offer
            if o and o["state"] in ("ask", "declined", "error"):
                self.answer_mods(o["key"], True)
            return self.mods_status()

        return {"/servers/state": state, "/servers/check": check, "/servers/join": join, "/servers/save": save,
                "/servers/remove": remove, "/host/start": host_start, "/host/stop": host_stop, "/admin": admin,
                "/page/close": page_close, "/page/open": page_open, "/servers/get_mods": mods_get}
