#!/usr/bin/env python3
"""inZOI Multiplayer server.

A small relay + storage server. Each player's companion app connects here; the server keeps
the list of who is online, relays live Zoi state between players, stores each player's Zoi
profile and house snapshots, and forwards chat, visit invites and interactions. All game logic
lives in the companion apps, so the server never needs updating when a new synced feature is added
(new features ride on the generic "event" message).

Runs anywhere Python 3.9+ runs (Raspberry Pi OS, Linux, Windows, macOS). No extra packages.

    python3 inzoi_mp_server.py                     # port 24242, data in ./data
    python3 inzoi_mp_server.py --port 24242 --password secret --data /var/lib/inzoi-mp
    python3 inzoi_mp_server.py --directory         # also keep the list of public servers (see below)
    python3 inzoi_mp_server.py --admin-code        # print the code that makes a player an admin
    python3 inzoi_mp_server.py --make-admin Ana    # make a player (by name or id) an admin of the running server

The server's settings (name, description, password, public or private, admins, bans...) live in data/server.json and
are changed from the admin console on the in-game servers page; --password, --motd, --max-players and --name override
them at start. A public server is listed by "directories": servers started with --directory that keep the list of
public servers, check each one can be reached, and hand the list to anyone who asks.

Wire format: one JSON object per line over TCP, UTF-8. Every message has "t" (its type).
See PROTOCOL.md next to this file.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import signal
import socket
import sys
import time
from pathlib import Path

VERSION = "0.8.8"
PROTOCOL = 1
MAX_LINE = 32 * 1024 * 1024        # house snapshots can be large (only after a successful hello)
MAX_LINE_BEFORE_HELLO = 64 * 1024  # hello, info, directory messages: small; a stranger can't make us buffer 32 MB
MAX_CONNS = 64                     # open connections in all (each player has 2: game + mod files)
MAX_CONNS_PER_IP = 12              # from one address (a couple behind one router: 2 players x 2 + servers page probes)
MAX_PIECE = 8 * 1024 * 1024        # blob_put: the most bytes one piece may carry (the companion sends 512 KB)
CLAIM_TRIES = 5                    # admin_claim attempts per address per minute
PING_EVERY = 15                    # seconds between keepalives
TIMEOUT = 45                       # drop a client that sent nothing for this long
FILES_TIMEOUT = 600                # a file connection gets no pings (and can't answer them): it may sit idle longer
CHUNK = 512 * 1024                 # mod file transfer: bytes per blob_get answer
SHA = re.compile(r"[0-9a-f]{64}")
FILE_MESSAGES = {"blob_check", "blob_put", "blob_get", "ping", "pong"}
BEFORE_HELLO = {"info", "ping", "pong", "dir_announce", "dir_list", "dir_remove"}   # allowed without joining
PART_TTL = 86400                   # an unfinished upload (.part) older than this is deleted
LINGER = 10                        # seconds a player who dropped without "bye" stays (a blip, a restart): no Zoi flicker
BLOB_GRACE = 3600                  # an unreferenced blob younger than this is kept (it may be part of an upload)
HISTORY = 20                       # world versions kept (their files stay on disk: an admin can go back to one)
ANNOUNCE_EVERY = 60                # seconds between a public server's announcements to its directories
LISTING_TTL = 180                  # a directory drops a server it hasn't heard from for this long
RECHECK = 300                      # seconds before a directory checks again that a listed server can be reached
RESTART_EXIT = 75                  # exit code after an admin's restart (systemd's Restart=always starts it again)
DISCOVER_ASK = b"INZOI-MP?"         # LAN discovery: a UDP datagram to the server's port (broadcast) gets a "discover"
LAN_NETS = [ipaddress.ip_network("100.64.0.0/10")]   # (Tailscale's range isn't "private" to ipaddress; answered too)
SETTINGS = {"name": "", "description": "", "motd": "", "public": False, "password": "", "max_players": 16,
            "public_address": "", "directories": [], "admins": [], "bans": {}}

log = logging.getLogger("inzoi-mp")
RECENT: collections.deque = collections.deque(maxlen=300)     # the latest log lines, for the admin console


class _Recent(logging.Handler):
    def emit(self, record):
        RECENT.append(f"{time.strftime('%H:%M:%S', time.localtime(record.created))} {record.getMessage()}")


log.addHandler(_Recent())


def now() -> float:
    return time.time()


def mod_fp(mod: dict) -> str:
    """A short fingerprint of a mod's content (not its mod_manifest.json): equal on two PCs when they have the very same
    mod. The companion computes the same (mods.fp) to tell which of a server's mods it has."""
    return hashlib.sha256("\n".join(sorted(f"{p}:{h}" for p, h in content(mod))).encode()).hexdigest()[:16]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def world_summary(meta: dict | None) -> dict | None:
    return meta and {k: meta.get(k) for k in ("version", "name", "by", "time", "size")}


def split_address(address: str, default_port: int = 24242) -> tuple[str, int]:
    host, _, port = str(address).strip().rpartition(":") if ":" in str(address) else (str(address).strip(), "", "")
    return (host or str(address).strip()).strip("[]"), int(port) if str(port).isdigit() else default_port


async def request(address: str, msg: dict, reply: str, timeout: float = 6) -> dict:
    """One question to another server (before any hello): send msg, return the first answer of type `reply`."""
    host, port = split_address(address)
    r, w = await asyncio.wait_for(asyncio.open_connection(host, port, limit=MAX_LINE), timeout)
    try:
        w.write((json.dumps(msg) + "\n").encode())
        await w.drain()
        while True:
            line = await asyncio.wait_for(r.readline(), timeout)
            if not line:
                raise ConnectionError("no answer")
            m = json.loads(line)
            if m.get("t") in (reply, "error"):
                return m
    finally:
        w.close()


class Store:
    """Everything that survives a restart: known players, their Zoi profiles and house snapshots."""

    def __init__(self, root: Path):
        self.root = root
        for sub in ("houses", "mods", "blobs", "world_history"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        self.players_file = root / "players.json"
        self.players: dict[str, dict] = self._load(self.players_file, {})
        self.settings_file = root / "server.json"
        self.settings: dict = {**SETTINGS, **self._load(self.settings_file, {})}
        self.settings.setdefault("id", secrets.token_hex(8))                  # who this server is, whatever its address
        self.settings.setdefault("admin_code", secrets.token_hex(4))          # typed in the admin console: admin
        self.settings.setdefault("listing_secret", secrets.token_hex(16))     # proves to directories it's this server
        self.save_settings()

    def save_settings(self) -> None:
        self._save(self.settings_file, self.settings)

    @staticmethod
    def _load(path: Path, default):
        """A saved file; if it's damaged (power cut mid-write...), the copy before it (.bak), loudly. Never the default
        in silence: that would drop the password and everyone's profile."""
        bak = path.with_name(path.name + ".bak")
        try:
            return json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            if not bak.exists():
                return default                       # never saved yet: normal
            log.warning("%s is missing: using its backup %s", path, bak.name)
        except (OSError, ValueError) as e:
            log.warning("%s is damaged (%s): trying its backup %s", path, e, bak.name)
            try:
                path.replace(path.with_name(f"{path.name}.corrupt-{int(now())}"))     # kept for a human to look at
            except OSError:
                pass
        try:
            return json.loads(bak.read_text("utf-8"))
        except (OSError, ValueError) as e:
            log.warning("the backup %s can't be read either (%s): %s starts over from defaults", bak, e, path.name)
            return default

    @staticmethod
    def _save(path: Path, data) -> None:
        """Written to a .tmp, flushed to disk, then swapped in; the previous file stays as .bak."""
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            os.replace(path, path.with_name(path.name + ".bak"))
        os.replace(tmp, path)

    def player_for_key(self, key: str, name: str) -> dict:
        """Players are identified by a secret key their companion generates once; the id others see is derived."""
        for pid, p in self.players.items():
            if secrets.compare_digest(p["key"], key):
                p["name"] = name
                p["last_seen"] = now()
                self.save_players()
                return {"id": pid, **p}
        pid = "p" + secrets.token_hex(4)
        self.players[pid] = {"key": key, "name": name, "first_seen": now(), "last_seen": now(), "profile": None}
        self.save_players()
        return {"id": pid, **self.players[pid]}

    def save_players(self) -> None:
        self._save(self.players_file, self.players)

    def set_profile(self, pid: str, profile) -> None:
        self.players[pid]["profile"] = profile
        self.save_players()

    def house_path(self, pid: str) -> Path:
        return self.root / "houses" / f"{pid}.json"

    def put_house(self, pid: str, house) -> dict:
        house = house or {}
        meta = {"owner": pid, "updated": now(), "name": house.get("name", ""), "by": house.get("by", ""),
                "lot": house.get("lot", "")}
        self._save(self.house_path(pid), {"meta": meta, "house": house})
        return meta

    def get_house(self, pid: str):
        return self._load(self.house_path(pid), None)

    def list_houses(self) -> list[dict]:
        out = []
        for f in (self.root / "houses").glob("p*.json"):
            d = self._load(f, None)
            if d:
                out.append(d["meta"])
        return out

    # Mods: each player's list of enabled mods (with a sha256 per file), and the files themselves as blobs named by
    # their sha256, so a file several players have is stored once.
    mods_changed = 0                   # bumped on every set_mods, so the server knows its cached mod set is stale

    def set_mods(self, pid: str, name: str, mods: list) -> None:
        self._save(self.root / "mods" / f"{pid}.json", {"name": name, "updated": now(), "mods": mods})
        self.mods_changed += 1

    def all_mods(self) -> dict[str, dict]:
        out = {}
        for f in (self.root / "mods").glob("p*.json"):
            d = self._load(f, None)
            if d:
                out[f.stem] = d
        return out

    def blob(self, sha: str) -> Path:
        return self.root / "blobs" / sha

    # The shared world: one game save everyone plays, as files (blobs) plus a version that goes up with each upload.
    _world: tuple | None = None        # (meta, {sha}): the current world, read once (blob_get asks per 512 KB)

    def world_save(self) -> dict | None:
        if self._world is None:
            meta = self._load(self.root / "world_save.json", None)
            self._world = (meta, {f["h"] for f in (meta or {}).get("files", [])})
        return self._world[0]

    def world_blobs(self) -> set:
        self.world_save()
        return self._world[1]

    def set_world_save(self, meta: dict) -> None:
        """The new current world; the last HISTORY versions are kept so an admin can go back to one."""
        self._save(self.root / "world_save.json", meta)
        self._world = None
        self._save(self.root / "world_history" / f"{meta['version']}.json", meta)
        for old in sorted(self.root.glob("world_history/*.json"), key=lambda f: int(f.stem) if f.stem.isdigit() else 0)[
                :-HISTORY]:
            old.unlink(missing_ok=True)
            old.with_name(old.name + ".bak").unlink(missing_ok=True)

    def gc_blobs(self, keep: set = frozenset()) -> None:
        """Deletes blobs nothing uses any more: not a file of anyone's mods (all stored lists, not just the current
        mod set), of the current world or a kept world version, nor in `keep` (uploads under way); and .part files
        of uploads abandoned over a day ago. Mod files and world files share the folder, hence the full list."""
        used = set(keep) | self.world_blobs()
        for d in self.all_mods().values():
            used |= {f["h"] for m in d.get("mods", []) for f in m.get("files", [])}
        for m in self.world_history():
            used |= {f["h"] for f in m.get("files", [])}
        freed, t = 0, now()
        for f in (self.root / "blobs").iterdir():
            try:
                st = f.stat()
                if (f.name.endswith(".part") and t - st.st_mtime > PART_TTL) or \
                        (SHA.fullmatch(f.name) and f.name not in used and t - st.st_mtime > BLOB_GRACE):
                    f.unlink()
                    freed += st.st_size
            except OSError:
                pass
        if freed:
            log.info("deleted %d MB of files nothing uses any more", freed >> 20)

    def world_history(self) -> list[dict]:
        out = [self._load(f, None) for f in self.root.glob("world_history/*.json")]
        return sorted((m for m in out if m), key=lambda m: -m["version"])

    def world_version(self, version: int) -> dict | None:
        return self._load(self.root / "world_history" / f"{int(version)}.json", None)


def clean_files(raw, limit: int = 5000) -> list[dict]:
    """[{p, h, s}] from a player: relative path, sha256, size. Malformed entries are dropped."""
    return [{"p": str(f["p"])[:400], "h": f["h"], "s": int(f["s"])} for f in (raw if isinstance(raw, list) else [])[:limit]
            if isinstance(f, dict) and SHA.fullmatch(str(f.get("h", ""))) and isinstance(f.get("s"), int)
            and 0 <= f["s"] < 1 << 32 and f.get("p")]


def content(mod: dict) -> set:
    """A mod's files, leaving out mod_manifest.json: the game rewrites it on each PC (load order, enabled...)."""
    return {(f["p"], f["h"]) for f in mod["files"] if f["p"] != "mod_manifest.json"}


def clean_mods(raw) -> list[dict]:
    """A player's mod list, keeping only well-formed entries (they are stored and passed on to other players)."""
    out = []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict) or not isinstance(m.get("files"), list) or not str(m.get("id", "")).strip():
            continue
        files = clean_files(m["files"])
        if files:
            out.append({"id": str(m["id"])[:200], "name": str(m.get("name") or m["id"])[:200],
                        "version": str(m.get("version", ""))[:100], "built": str(m.get("built", ""))[:40],
                        "path": str(m.get("path", ""))[:300],
                        "size": sum(f["s"] for f in files), "files": files})
    return out[:500]


class Client:
    def __init__(self, server: "Server", reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.server = server
        self.reader = reader
        self.writer = writer
        self.addr = writer.get_extra_info("peername")
        self.id: str | None = None
        self.name = "?"
        self.state: dict | None = None     # last live state (position, animation, ...)
        self.last_heard = now()
        self.closed = False
        self.lock = asyncio.Lock()
        self.files = False                 # a second connection of a player, only for mod file transfers
        self.joined = 0.0
        self.version = ""                  # the player's companion app version (others are warned on a mismatch)

    def ip(self) -> str:
        return str((self.addr or ("",))[0])

    def local(self) -> bool:
        return self.ip() in ("127.0.0.1", "::1")

    def public(self) -> dict:
        p = self.server.store.players.get(self.id or "", {})
        return {"id": self.id, "name": self.name, "profile": p.get("profile"), "state": self.state,
                "leader": self.server.leader is self, "version": self.version}

    async def send(self, msg: dict) -> None:
        if self.closed:
            return
        data = (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            async with self.lock:
                self.writer.write(data)
                await self.writer.drain()
        except (ConnectionError, OSError):
            await self.close()

    async def fail(self, code: str, error: str, **extra) -> None:
        """A fatal error (code: machine-readable, for the UI to translate; error: English), then hang up."""
        await self.send({"t": "error", "fatal": True, "code": code, "error": error, **extra})
        await self.close()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.writer.close()
        except Exception:
            pass


class Discovery(asyncio.DatagramProtocol):
    """LAN discovery on UDP (the same port number as TCP): b"INZOI-MP?" from the home network, broadcast or not, gets
    {"t": "discover", name, port, version, protocol, id, password (bool), players, max_players, hosted}. Only private,
    link-local, loopback and Tailscale addresses are answered, each at most 5 times a second."""

    def __init__(self, server: "Server"):
        self.server, self.transport, self.last = server, None, {}

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if data.strip() != DISCOVER_ASK:
            return
        try:
            ip = ipaddress.ip_address(addr[0])
        except ValueError:
            return
        if not (ip.is_private or ip.is_loopback or ip.is_link_local or any(ip in n for n in LAN_NETS)):
            return
        t = now()
        if t - self.last.get(addr[0], 0) < 0.2:
            return
        if len(self.last) > 1000:
            self.last.clear()
        self.last[addr[0]] = t
        try:
            self.transport.sendto(json.dumps(self.server.discover_reply()).encode(), addr)
        except OSError:
            pass

    def error_received(self, exc) -> None:          # (Windows reports an unreachable asker here: nothing to do)
        pass


class Server:
    def __init__(self, store: Store, password: str | None = None, max_players: int | None = None,
                 motd: str | None = None, hosted: bool = False, name: str | None = None, directory: bool = False):
        self.store = store
        for k, v in (("password", password), ("max_players", max_players), ("motd", motd), ("name", name)):
            if v is not None:                    # given at start (command line, the program's host settings): wins
                store.settings[k] = v
        store.save_settings()
        self.started = now()
        self.port = 24242                        # set by start(): where players reach this server
        self.restartable = False                 # a stand-alone server (main()) can be restarted by an admin
        self.stop_event: asyncio.Event | None = None
        self.directory: dict[str, dict] | None = {} if directory else None    # public servers this one lists
        self.public_status: dict[str, dict] = {}  # directory address -> the answer to our last announcement
        self.announce_now: asyncio.Event | None = None
        self.clients: dict[str, Client] = {}
        self.leader: Client | None = None      # whose world clock/weather everyone follows
        self.world: dict | None = None         # latest world state from the leader
        # hosted: the server runs inside a player's program ("Host and play"). That player (the one connecting from
        # this same PC and saying so) is the host: their mods are the ones everyone gets, their clock everyone follows.
        # Otherwise (a Raspberry Pi, "Host only") everyone's mods are pooled and the first player online sets the clock.
        self.hosted = hosted
        self.host_id: str | None = None
        self.modset_sent: str | None = None
        self.conns: set[Client] = set()        # every open connection, players' and file transfers'
        self.world_offers: dict[str, dict] = {}   # player id -> {sha: size} of the world files they're uploading
        self.restart_requested = False
        self.claims: dict[str, collections.deque] = {}   # address -> times of its recent admin_claim attempts
        self.udp = None                          # the LAN discovery endpoint (start())
        store.gc_blobs()

    @property
    def password(self) -> str:
        return self.store.settings["password"]

    @property
    def max_players(self) -> int:
        return int(self.store.settings["max_players"])

    @property
    def motd(self) -> str:
        return self.store.settings["motd"]

    def name(self) -> str:
        host = self.clients.get(self.host_id or "")
        return self.store.settings["name"] or (f"{host.name}'s server" if host else "") \
            or f"{socket.gethostname()} (inZOI Multiplayer)"

    def summary(self) -> dict:
        """What everyone sees about this server (the welcome and server_info messages)."""
        s = self.store.settings
        return {"id": s["id"], "name": self.name(), "description": s["description"], "motd": s["motd"],
                "public": bool(s["public"]), "password": bool(s["password"]), "max_players": self.max_players,
                "version": VERSION, "hosted": self.hosted, "directory": self.directory is not None}

    def is_admin(self, c: Client) -> bool:
        return bool(c.id) and (c.id in self.store.settings["admins"] or (self.hosted and c.id == self.host_id))

    # ------------------------------------------------------------------ plumbing
    async def broadcast(self, msg: dict, exclude: Client | None = None) -> None:
        await asyncio.gather(*(c.send(msg) for c in list(self.clients.values()) if c is not exclude))

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        c = Client(self, reader, writer)
        same_ip = sum(1 for o in self.conns if o.ip() == c.ip())
        if len(self.conns) >= MAX_CONNS or (same_ip >= MAX_CONNS_PER_IP and not c.local()):
            log.warning("refused a connection from %s: too many open", c.addr)
            await c.fail("busy", "too many connections, try again in a moment")
            return
        self.conns.add(c)
        log.info("connection from %s", c.addr)
        reader._limit = MAX_LINE_BEFORE_HELLO        # StreamReader's line limit: back to MAX_LINE after a hello
        try:
            while not c.closed:
                try:
                    line = await asyncio.wait_for(reader.readline(), FILES_TIMEOUT if c.files else TIMEOUT)
                except asyncio.TimeoutError:
                    log.info("%s timed out", c.name)
                    break
                except (ValueError, asyncio.LimitOverrunError):
                    await c.fail("too_large", "message too large")
                    break
                if not line:
                    break
                c.last_heard = now()
                try:
                    msg = json.loads(line)
                    if not isinstance(msg, dict):
                        raise ValueError
                except ValueError:
                    await c.send({"t": "error", "code": "bad_json", "error": "bad json"})
                    continue
                try:
                    await self.dispatch(c, msg)
                except Exception as e:               # a malformed field, a bug: answer it and keep the connection
                    log.exception("%s: %r message failed", c.name, msg.get("t"))
                    await c.send({"t": "error", "code": "bad_request", "error": f"bad {msg.get('t')!r} message: {e}",
                                  "ref": msg.get("ref")})
                if c.id is not None:
                    reader._limit = MAX_LINE
        except (ConnectionError, OSError):
            pass
        finally:
            self.conns.discard(c)
            await c.close()
            await self.drop(c, linger=not getattr(c, "said_bye", False))

    async def gc(self) -> None:
        """Deletes blobs nothing uses any more (keeping the files of world uploads under way), off the event loop."""
        keep = {h for offer in self.world_offers.values() for h in offer}
        await asyncio.to_thread(self.store.gc_blobs, keep)

    async def drop(self, c: Client, reelect: bool = True, linger: bool = False) -> None:
        """A player is gone. linger: the connection just broke (no "bye"): they stay listed LINGER s first, so a
        quick reconnect (a Wi-Fi blip, their program restarting for an update) is a silent rejoin, not leave+join."""
        if linger and c.id and self.clients.get(c.id) is c:
            async def later():
                await asyncio.sleep(LINGER)
                await self.drop(c, reelect)
            asyncio.get_running_loop().create_task(later())
            return
        if c.id and self.clients.get(c.id) is c:
            del self.clients[c.id]
            log.info("%s (%s) left", c.name, c.id)
            await self.broadcast({"t": "left", "id": c.id, "name": c.name})
            if self.leader is c and reelect:
                self.leader = next(iter(self.clients.values()), None)
                if self.leader:
                    await self.broadcast({"t": "leader", "id": self.leader.id})

    # ------------------------------------------------------------------ messages
    async def dispatch(self, c: Client, msg: dict) -> None:
        t = msg.get("t")
        if c.id is None and c.local() and t in ("announce_restart", "make_admin"):     # from this machine only
            if t == "announce_restart":
                await self.announce_restart(max(5, min(600, int(msg.get("in") or 60))))
                await c.send({"t": "ok"})
            else:
                await c.send({"t": "ok", **await self.admin_op("admin_add", {"id": msg.get("who")}, "this machine")})
            return
        if c.id is None:
            if t in BEFORE_HELLO:
                await getattr(self, "on_" + t)(c, msg)
            elif t != "hello":
                await c.send({"t": "error", "code": "hello_first", "error": "say hello first"})
            else:
                await self.on_hello(c, msg)
            return
        if c.files != (t in FILE_MESSAGES - {"ping", "pong"}) and t not in ("ping", "pong"):
            await c.send({"t": "error", "code": "wrong_connection", "error": f"{t!r} doesn't belong on this connection"})
            return
        fn = getattr(self, "on_" + str(t), None) if re.fullmatch(r"[a-z_]{1,32}", str(t)) and t != "hello" else None
        if fn is None:
            await c.send({"t": "error", "code": "unknown_type", "error": f"unknown message type {t!r}"})
            return
        await fn(c, msg)

    async def on_bye(self, c: Client, msg: dict) -> None:
        c.said_bye = True                            # leaving on purpose: told at once (see drop)

    async def on_hello(self, c: Client, msg: dict) -> None:
        if msg.get("protocol") != PROTOCOL:
            await c.fail("version_mismatch", f"version mismatch: server speaks protocol {PROTOCOL}", protocol=PROTOCOL,
                         server_version=VERSION)
            return
        given = str(msg.get("password") or "").encode("utf-8")        # bytes: compare_digest refuses non-ASCII str
        if self.password and not secrets.compare_digest(given, self.password.encode("utf-8")):
            await c.fail("wrong_password", "wrong server password")
            return
        key = str(msg.get("key", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
            await c.fail("bad_key", "missing or malformed player key")
            return
        name = str(msg.get("name", "")).strip()[:40] or "Player"
        p = self.store.player_for_key(key, name)
        ban = self.store.settings["bans"].get(p["id"])
        if ban:
            await c.fail("banned", "you're banned from this server" + (f" ({ban['reason']})" if ban.get("reason") else ""),
                         banned=True)
            return
        version = str(msg.get("version") or "")[:20]
        if msg.get("files"):                         # the player's file transfer connection: not a second player
            c.id, c.name, c.files, c.version = p["id"], name, True, version
            await c.send({"t": "files_ok"})
            return
        old = self.clients.get(p["id"])
        if old is None and len(self.clients) >= self.max_players:
            await c.fail("full", "server is full")
            return
        c.id, c.name, c.joined, c.version = p["id"], name, now(), version
        if old:
            # Same player reconnecting (a Wi-Fi blip): swap the session in place. Nobody is told they left, so their
            # Zoi stays in everyone's world; the others only get "rejoined" (older companions ignore it).
            c.state, c.joined = old.state, old.joined
            self.clients[c.id] = c
            if self.leader is old:
                self.leader = c
            await old.send({"t": "kicked", "reason": "you connected from somewhere else"})
            await old.close()
            log.info("%s (%s) reconnected from %s", c.name, c.id, c.addr)
            await c.send(self.welcome(c))
            await self.broadcast({"t": "rejoined", "player": c.public()}, exclude=c)
            return
        self.clients[c.id] = c
        is_host = self.hosted and bool(msg.get("host")) and c.local()
        if is_host:
            self.host_id = c.id
        before = self.leader.id if self.leader else None
        if self.leader is None or is_host:
            self.leader = c
        log.info("%s (%s) joined from %s%s", c.name, c.id, c.addr, " - the host" if is_host else "")
        modset = self.modset()
        await c.send(self.welcome(c, modset))
        await self.broadcast({"t": "joined", "player": c.public()}, exclude=c)
        if before not in (None, self.leader.id):                    # the clock changed hands
            await self.broadcast({"t": "leader", "id": self.leader.id}, exclude=c)
        await self.send_modset(modset, exclude=c)        # it's in the welcome already

    def welcome(self, c: Client, modset: dict | None = None) -> dict:
        return {
            "t": "welcome", "id": c.id, "server_version": VERSION, "protocol": PROTOCOL, "motd": self.motd,
            "leader": self.leader.id if self.leader else None, "world": self.world, "host": self.host_id,
            "players": [o.public() for o in self.clients.values() if o is not c],
            "houses": self.store.list_houses(), "modset": modset or self.modset(), "world_save": self.store.world_save(),
            "server": self.summary(), "admin": self.is_admin(c),
        }

    async def on_ping(self, c: Client, msg: dict) -> None:
        await c.send({"t": "pong", "time": now(), "echo": msg.get("echo")})

    async def on_pong(self, c: Client, msg: dict) -> None:
        pass

    async def on_state(self, c: Client, msg: dict) -> None:
        """Live Zoi state (position, facing, animation, current interaction, site...). Sent ~5-10x/s."""
        c.state = msg.get("state")
        await self.broadcast({"t": "state", "id": c.id, "state": c.state}, exclude=c)

    async def on_profile(self, c: Client, msg: dict) -> None:
        """The player's Zoi: name, appearance, traits. Stored so people who join later can build the Zoi."""
        self.store.set_profile(c.id, msg.get("profile"))
        await self.broadcast({"t": "profile", "id": c.id, "profile": msg.get("profile")}, exclude=c)

    async def on_world(self, c: Client, msg: dict) -> None:
        """Clock, weather and season. Only the leader's world is shared, everyone else follows it."""
        if c is not self.leader:
            return
        self.world = msg.get("world")
        await self.broadcast({"t": "world", "world": self.world}, exclude=c)

    async def on_take_leader(self, c: Client, msg: dict) -> None:
        if self.hosted and self.host_id in self.clients and c.id != self.host_id:     # the host keeps their clock
            await c.send({"t": "leader", "id": self.leader.id if self.leader else None})
            return
        self.leader = c
        await self.broadcast({"t": "leader", "id": c.id})

    async def on_chat(self, c: Client, msg: dict) -> None:
        text = str(msg.get("text", "")).strip()[:500]
        if text:
            log.info("[chat] %s: %s", c.name, text)
            await self.broadcast({"t": "chat", "id": c.id, "name": c.name, "text": text, "time": now()})

    async def on_event(self, c: Client, msg: dict) -> None:
        """Generic relay: {"t":"event","kind":"...","to":<player id or omitted for everyone>,"data":{...}}.
        Interactions, visit invites, gifts... anything new the companions agree on."""
        out = {"t": "event", "from": c.id, "kind": str(msg.get("kind", "")), "data": msg.get("data")}
        to = msg.get("to")
        if to:
            target = self.clients.get(str(to))
            if target:
                await target.send(out)
            else:
                await c.send({"t": "error", "code": "not_online", "error": f"{to} is not online", "ref": msg.get("ref")})
        else:
            await self.broadcast(out, exclude=c)

    async def on_house_put(self, c: Client, msg: dict) -> None:
        meta = self.store.put_house(c.id, msg.get("house"))
        log.info("%s uploaded their house (%s)", c.name, meta.get("name") or "unnamed")
        await self.broadcast({"t": "house_updated", "meta": meta})

    async def on_house_get(self, c: Client, msg: dict) -> None:
        owner = str(msg.get("owner", ""))
        d = self.store.get_house(owner)
        await c.send({"t": "house", "owner": owner, "meta": d and d["meta"], "house": d and d["house"]})

    async def on_house_list(self, c: Client, msg: dict) -> None:
        await c.send({"t": "house_list", "houses": self.store.list_houses()})

    async def on_who(self, c: Client, msg: dict) -> None:
        await c.send({"t": "who", "players": [o.public() for o in self.clients.values()]})

    # ------------------------------------------------------------------ mods
    def modset(self) -> dict:
        """The mods everyone should have. Hosted: the host's. Otherwise: everyone's together (for a mod two players
        have in different versions, the newest build). Each mod lists who has it."""
        key = (self.host_id, self.store.mods_changed)
        if getattr(self, "_modset", (None, None))[0] != key:
            self._modset = (key, self._compute_modset())
        return self._modset[1]

    def _compute_modset(self) -> dict:
        lists = self.store.all_mods()
        if self.hosted:
            d = lists.get(self.host_id or "", {})
            mods = [{**m, "by": [d.get("name", "")]} for m in d.get("mods", [])]
            return {"source": "host", "host": self.host_id, "mods": mods}
        merged: dict[str, dict] = {}
        for pid, d in sorted(lists.items(), key=lambda kv: kv[1].get("updated", 0)):
            for m in d.get("mods", []):
                same, name = merged.get(m["id"]), d.get("name", pid)
                if same and content(same) == content(m):            # the same mod, whatever each PC's manifest says
                    same["by"].append(name)
                elif not same or m.get("built", "") >= same.get("built", ""):     # an older build never wins
                    merged[m["id"]] = {**m, "by": [name]}
        return {"source": "everyone", "host": None, "mods": sorted(merged.values(), key=lambda m: m["name"].lower())}

    async def send_modset(self, modset: dict | None = None, exclude: Client | None = None) -> None:
        modset = modset or self.modset()
        text = json.dumps(modset, sort_keys=True)
        if text != self.modset_sent:
            self.modset_sent = text
            await self.broadcast({"t": "modset", **modset}, exclude=exclude)

    def shared_blobs(self) -> set[str]:
        return {f["h"] for m in self.modset()["mods"] for f in m["files"]}

    async def on_mods(self, c: Client, msg: dict) -> None:
        """A player's enabled mods: [{id, name, version, path, files: [{p, h, s}]}]."""
        mods = clean_mods(msg.get("mods"))
        self.store.set_mods(c.id, c.name, mods)
        log.info("%s has %d mod(s)", c.name, len(mods))
        await self.send_modset()

    async def on_blob_check(self, c: Client, msg: dict) -> None:
        """Which of these files (sha256) the server doesn't have yet: the client uploads those."""
        want = [h for h in msg.get("h", []) if isinstance(h, str) and SHA.fullmatch(h)]
        await c.send({"t": "blob_missing", "h": [h for h in want if not self.store.blob(h).exists()]})

    async def on_blob_put(self, c: Client, msg: dict) -> None:
        """Upload a file of one of your own mods, in order: {h, o (offset), d (base64), end}."""
        h, offset = str(msg.get("h", "")), msg.get("o")
        if getattr(c, "mine", (None,))[0] != self.store.mods_changed:
            c.mine = (self.store.mods_changed, {f["h"]: f["s"] for m in self.store.all_mods().get(c.id, {}).get("mods", [])
                                                for f in m["files"]})
        mine = {**c.mine[1], **self.world_offers.get(c.id, {})}
        if h not in mine:
            await c.send({"t": "blob_bad", "h": h, "error": "not a file of your mods or your world upload"})
            return
        part = self.store.blob(h).with_name(f"{h}.{c.id}.part")
        size = part.stat().st_size if part.exists() else 0
        if type(offset) is not int or offset != size:
            await c.send({"t": "blob_bad", "h": h, "error": f"expected offset {size}", "o": size})
            return
        d = msg.get("d", "")
        try:
            data = base64.b64decode(d, validate=True) if isinstance(d, str) and len(d) <= MAX_PIECE * 4 // 3 + 4 else None
        except ValueError:
            data = None
        if data is None or size + len(data) > mine[h]:
            part.unlink(missing_ok=True)
            await c.send({"t": "blob_bad", "h": h, "o": 0,
                          "error": "bad piece (not base64, too big or past the end of the file): start again"})
            return
        with part.open("ab") as f:
            f.write(data)
        if not msg.get("end"):
            return
        if part.stat().st_size != mine[h] or await asyncio.to_thread(sha256_file, part) != h:   # off the event loop
            part.unlink()
            await c.send({"t": "blob_bad", "h": h, "error": "the file didn't arrive intact"})
            return
        os.replace(part, self.store.blob(h))
        await c.send({"t": "blob_ok", "h": h})

    async def on_blob_get(self, c: Client, msg: dict) -> None:
        """Download a piece of a file that's part of the shared mods: {h, o} -> blob {h, o, d, end}."""
        h, offset = str(msg.get("h", "")), int(msg.get("o") or 0)
        path = self.store.blob(h)
        if offset < 0:
            raise ValueError("negative offset")
        if not SHA.fullmatch(h) or (h not in self.store.world_blobs() and h not in self.shared_blobs()):
            await c.send({"t": "blob", "h": h, "error": "not shared"})
            return
        if not path.exists():
            await c.send({"t": "blob", "h": h, "error": "not uploaded yet"})
            return
        with path.open("rb") as f:
            f.seek(offset)
            data = f.read(CHUNK)
        await c.send({"t": "blob", "h": h, "o": offset, "d": base64.b64encode(data).decode(),
                      "end": offset + len(data) >= path.stat().st_size})

    # ------------------------------------------------------------------ the shared world
    async def on_world_save_offer(self, c: Client, msg: dict) -> None:
        """About to upload the world: {files: [{p, h, s}]}. Answers which files the server still needs."""
        files = clean_files(msg.get("files"), 2000)
        if not any(f["p"].endswith(".usvx") for f in files):
            await c.send({"t": "world_save_offer", "error": "no save file in the upload"})
            return
        self.world_offers[c.id] = {f["h"]: f["s"] for f in files}
        need = sorted({f["h"] for f in files if not self.store.blob(f["h"]).exists()})
        await c.send({"t": "world_save_offer", "need": need})

    async def on_world_save_put(self, c: Client, msg: dict) -> None:
        """The uploaded world becomes the new version, if it was based on the current one:
        {files, base (the version the player had loaded), name}."""
        files = clean_files(msg.get("files"), 2000)
        current = self.store.world_save() or {}
        version = current.get("version", 0)
        if msg.get("base") != version:
            await c.send({"t": "world_save_put", "ok": False, "version": version,
                          "error": f"the world changed since you loaded it (it's at version {version} now)"})
            return
        missing = [f["p"] for f in files if not self.store.blob(f["h"]).exists()]
        if missing or not files:
            await c.send({"t": "world_save_put", "ok": False, "version": version,
                          "error": f"files not uploaded yet: {', '.join(missing[:5])}"})
            return
        meta = {"version": version + 1, "by": c.name, "pid": c.id, "time": now(), "files": files,
                "name": str(msg.get("name", ""))[:100] or current.get("name", ""), "size": sum(f["s"] for f in files)}
        self.store.set_world_save(meta)
        self.world_offers.pop(c.id, None)
        await self.gc()
        log.info("%s saved the shared world: version %d (%d MB)", c.name, meta["version"], meta["size"] >> 20)
        await c.send({"t": "world_save_put", "ok": True, "version": meta["version"]})
        await self.broadcast({"t": "world_save", **meta}, exclude=c)

    async def announce_restart(self, seconds: int) -> None:
        """Before the server restarts: everyone saves their game (the world keeper also uploads it)."""
        keeper = self.leader.id if self.leader else None
        log.info("restart in %d s: asking everyone to save (world keeper %s)", seconds, keeper)
        await self.broadcast({"t": "restart_soon", "in": seconds, "keeper": keeper})
        await self.broadcast({"t": "chat", "id": None, "name": "Server", "time": now(),
                              "text": f"The server restarts in {seconds} seconds: your game is saved first."})

    # ------------------------------------------------------------------ the servers page: info before joining
    def info(self) -> dict:
        """Everything the servers page shows about this server before joining it, and what its mods check needs."""
        ms = self.modset()
        return {"t": "info", **self.summary(), "protocol": PROTOCOL, "players": len(self.clients),
                "names": [c.name for c in self.clients.values()][:16],
                "host": self.clients[self.host_id].name if self.host_id in self.clients else None,
                "world": world_summary(self.store.world_save()), "uptime": int(now() - self.started),
                "mods": {"source": ms["source"],
                         "list": [{"id": m["id"], "name": m["name"], "version": m.get("version", ""),
                                   "built": m.get("built", ""), "size": m.get("size", 0), "fp": mod_fp(m)}
                                  for m in ms["mods"]]}}

    async def on_info(self, c: Client, msg: dict) -> None:
        await c.send(self.info())

    def discover_reply(self) -> dict:
        """The answer to a LAN discovery datagram (small: one UDP packet)."""
        s = self.store.settings
        return {"t": "discover", "name": self.name(), "port": self.port, "version": VERSION, "protocol": PROTOCOL,
                "id": s["id"], "password": bool(s["password"]), "players": len(self.clients),
                "max_players": self.max_players, "hosted": self.hosted}

    async def hold_udp(self) -> None:
        """Keeps the LAN discovery endpoint open for as long as the server's background tasks run (every way of
        stopping a server cancels them)."""
        try:
            await asyncio.Event().wait()
        finally:
            if self.udp:
                self.udp.close()
                self.udp = None

    # ------------------------------------------------------------------ admin console
    async def on_admin(self, c: Client, msg: dict) -> None:
        """{op, ref, ...}: answered with {t: admin, ref, op, ok, error?, ...}. Only for the server's admins."""
        op = str(msg.get("op", ""))
        if not self.is_admin(c):
            out = {"ok": False, "error": "you're not an admin of this server"}
        else:
            try:
                out = await self.admin_op(op, msg, c.name, c)
            except (ValueError, TypeError, KeyError) as e:
                out = {"ok": False, "error": str(e)}
        await c.send({"t": "admin", "ref": msg.get("ref"), "op": op, **out})

    async def on_admin_claim(self, c: Client, msg: dict) -> None:
        """Become an admin with the server's admin code (shown to admins, and by --admin-code on the server's PC)."""
        tries = self.claims.setdefault(c.ip(), collections.deque())
        while tries and now() - tries[0] > 60:
            tries.popleft()
        if len(tries) >= CLAIM_TRIES:                # someone guessing the code
            await c.send({"t": "admin", "ref": msg.get("ref"), "op": "claim", "ok": False, "code": "rate_limited",
                          "error": "too many tries: wait a minute"})
            return
        tries.append(now())
        code = str(msg.get("code", "")).strip().lower()
        ok = bool(code) and secrets.compare_digest(code.encode("utf-8"), self.store.settings["admin_code"].encode("utf-8"))
        if ok and c.id not in self.store.settings["admins"]:
            self.store.settings["admins"].append(c.id)
            self.store.save_settings()
            log.info("%s (%s) is an admin now (admin code)", c.name, c.id)
        await c.send({"t": "admin", "ref": msg.get("ref"), "op": "claim", "ok": ok,
                      **({} if ok else {"error": "that's not this server's admin code"})})

    def player_name(self, pid: str) -> str:
        return (self.store.players.get(pid) or {}).get("name") or pid

    def status(self) -> dict:
        s = self.store.settings
        ws = self.store.world_save()
        ms = self.modset()
        banned = s["bans"]
        return {
            "ok": True, "settings": {k: s[k] for k in ("name", "description", "motd", "public", "password",
                                                       "max_players", "public_address", "directories")},
            "id": s["id"], "admin_code": s["admin_code"], "version": VERSION, "uptime": int(now() - self.started),
            "hosted": self.hosted, "restartable": self.restartable, "shown_name": self.name(), "port": self.port,
            "players": [{"id": c.id, "name": c.name, "ip": c.ip(), "since": c.joined, "admin": self.is_admin(c),
                         "version": c.version,
                         "leader": c is self.leader, "host": c.id == self.host_id} for c in self.clients.values()],
            "known": [{"id": pid, "name": p.get("name"), "last_seen": p.get("last_seen"), "banned": pid in banned,
                       "admin": pid in s["admins"], "online": pid in self.clients}
                      for pid, p in sorted(self.store.players.items(), key=lambda kv: -(kv[1].get("last_seen") or 0))
                      ][:100],
            "bans": [{"id": pid, "name": b.get("name") or self.player_name(pid), **b} for pid, b in banned.items()],
            "admins": [{"id": pid, "name": self.player_name(pid)} for pid in s["admins"]],
            "world": {**(world_summary(ws) or {}), "history": [world_summary(m) for m in self.store.world_history()]}
            if ws else None,
            "mods": {"source": ms["source"], "count": len(ms["mods"]), "size": sum(m.get("size", 0) for m in ms["mods"])},
            "public_status": self.public_status,
            "directory": {"listed": len(self.directory)} if self.directory is not None else None,
            "log": list(RECENT)[-120:],
        }

    async def admin_op(self, op: str, msg: dict, by: str, c: Client | None = None) -> dict:
        s = self.store.settings
        target = str(msg.get("id") or "")
        if op == "status":
            return self.status()
        if op == "set":
            changed = []
            for k, cast, limit in (("name", str, 60), ("description", str, 300), ("motd", str, 300),
                                   ("password", str, 100), ("public_address", str, 200)):
                if k in msg and str(msg[k]).strip() != s[k]:
                    s[k] = cast(msg[k]).strip()[:limit]
                    changed.append(k)
            if "max_players" in msg and int(msg["max_players"]) != s["max_players"]:
                s["max_players"] = max(1, min(64, int(msg["max_players"])))
                changed.append("max_players")
            if "public" in msg and bool(msg["public"]) != s["public"]:
                s["public"] = bool(msg["public"])
                changed.append("public")
            if "directories" in msg and isinstance(msg["directories"], list):
                s["directories"] = [str(d).strip()[:200] for d in msg["directories"] if str(d).strip()][:10]
                changed.append("directories")
            self.store.save_settings()
            if changed:
                log.info("%s changed the server's %s", by, ", ".join(changed))
                await self.broadcast({"t": "server_info", "server": self.summary()})
                if {"public", "name", "description", "directories", "public_address", "password"} & set(changed):
                    if "public" in changed and not s["public"]:
                        await self.unlist()
                    if self.announce_now:
                        self.announce_now.set()
            return {"ok": True, "changed": changed, "settings": self.status()["settings"]}
        if op in ("kick", "ban"):
            if c is not None and target == c.id:
                raise ValueError("that's you")
            if target == self.host_id and self.hosted:
                raise ValueError("the host can't be removed from their own server")
            reason = str(msg.get("reason", "")).strip()[:200]
            if op == "ban":
                if target not in self.store.players:
                    raise ValueError("no such player")
                s["bans"][target] = {"name": self.player_name(target), "reason": reason, "time": now(), "by": by}
                if target in s["admins"]:
                    s["admins"].remove(target)
                self.store.save_settings()
                log.info("%s banned %s%s", by, self.player_name(target), f" ({reason})" if reason else "")
            who = self.clients.get(target)
            if who:
                await who.send({"t": "kicked", "reason": ("banned" if op == "ban" else "kicked") + f" by {by}"
                                + (f": {reason}" if reason else ""), "by_admin": True})
                await who.close()
                await self.drop(who)
                if op == "kick":
                    log.info("%s kicked %s%s", by, who.name, f" ({reason})" if reason else "")
            elif op == "kick":
                raise ValueError("they're not online")
            return {"ok": True}
        if op == "unban":
            if s["bans"].pop(target, None) is None:
                raise ValueError("they're not banned")
            self.store.save_settings()
            log.info("%s unbanned %s", by, self.player_name(target))
            return {"ok": True}
        if op == "admin_add":
            if target not in self.store.players:
                target = next((pid for pid, p in self.store.players.items() if p.get("name") == target), target)
            if target not in self.store.players:
                raise ValueError("no such player (they need to have joined once)")
            if target not in s["admins"]:
                s["admins"].append(target)
                self.store.save_settings()
                log.info("%s made %s an admin", by, self.player_name(target))
            if target in self.clients:
                await self.clients[target].send({"t": "server_info", "server": self.summary(), "admin": True})
            return {"ok": True, "id": target, "name": self.player_name(target)}
        if op == "admin_remove":
            if target not in s["admins"]:
                raise ValueError("they're not an admin")
            s["admins"].remove(target)
            self.store.save_settings()
            log.info("%s removed %s as an admin", by, self.player_name(target))
            if target in self.clients:
                await self.clients[target].send({"t": "server_info", "server": self.summary(), "admin": False})
            return {"ok": True}
        if op == "say":
            text = str(msg.get("text", "")).strip()[:500]
            if not text:
                raise ValueError("nothing to say")
            await self.broadcast({"t": "chat", "id": None, "name": "Server", "text": text, "time": now()})
            log.info("[server] %s: %s", by, text)
            return {"ok": True}
        if op == "restart":
            if not self.restartable or self.stop_event is None:
                raise ValueError("this server runs inside a player's program: restart that program instead")
            seconds = max(5, min(600, int(msg.get("in") or 30)))
            log.info("%s restarts the server in %d s", by, seconds)
            await self.announce_restart(seconds)
            self.restart_requested = True
            asyncio.get_running_loop().call_later(seconds + 3, self.stop_event.set)
            return {"ok": True, "in": seconds}
        if op == "world_restore":
            old = self.store.world_version(int(msg.get("version") or 0))
            if not old:
                raise ValueError("that version isn't kept any more")
            missing = [f["p"] for f in old["files"] if not self.store.blob(f["h"]).exists()]
            if missing:
                raise ValueError(f"files of that version are gone: {', '.join(missing[:3])}")
            version = (self.store.world_save() or {}).get("version", 0) + 1
            meta = {**old, "version": version, "by": f"{by} (back to version {old['version']})", "time": now(),
                    "pid": None}
            self.store.set_world_save(meta)
            await self.gc()
            log.info("%s made world version %d current again (as version %d)", by, old["version"], version)
            await self.broadcast({"t": "world_save", **meta})
            return {"ok": True, "version": version}
        raise ValueError(f"unknown admin action {op!r}")

    # ------------------------------------------------------------------ public servers: the directory
    async def on_dir_announce(self, c: Client, msg: dict) -> None:
        """A public server says it's there: {id, secret, port, address?, name, ...}. It's listed (and checked to be
        reachable) until it goes quiet for LISTING_TTL seconds or says dir_remove."""
        if self.directory is None:
            await c.send({"t": "dir_announce", "ok": False, "error": "this server doesn't keep a list of servers"})
            return
        sid, secret = str(msg.get("id", ""))[:32], str(msg.get("secret", ""))[:64]
        old = self.directory.get(sid)
        if not re.fullmatch(r"[0-9a-f]{8,32}", sid) or len(secret) < 16 or (old and old["secret"] != secret):
            await c.send({"t": "dir_announce", "ok": False, "error": "not accepted"})
            return
        port = int(msg.get("port") or 24242)
        address = str(msg.get("address") or "").strip()[:200] or f"{c.ip()}:{port}"
        entry = {"id": sid, "secret": secret, "address": address, "local": not msg.get("address") and c.local(),
                 "port": port, "seen": now(), "checked": (old or {}).get("checked", 0.0),
                 "reachable": (old or {}).get("reachable", False), "error": (old or {}).get("error", "")}
        for k, limit in (("name", 60), ("description", 300), ("version", 20)):
            entry[k] = str(msg.get(k) or "")[:limit]
        for k in ("players", "max_players", "protocol", "mods_count"):
            entry[k] = int(msg.get(k) or 0)
        entry["password"], entry["names"] = bool(msg.get("password")), [str(n)[:40] for n in (msg.get("names") or [])][:16]
        entry["world"] = world_summary(msg.get("world")) if isinstance(msg.get("world"), dict) else None
        if not old or old["address"] != address or now() - entry["checked"] > RECHECK:
            entry["checked"] = now()
            try:
                answer = await request(address, {"t": "info"}, "info", timeout=5)
                entry["reachable"] = answer.get("id") == sid
                entry["error"] = "" if entry["reachable"] else "a different server answers at that address"
            except (OSError, ValueError, asyncio.TimeoutError, ConnectionError) as e:
                entry["reachable"], entry["error"] = False, f"can't be reached at {address} ({e or 'timed out'})"
        if not old:
            log.info("listing public server %s at %s%s", entry["name"], address,
                     "" if entry["reachable"] else f" - {entry['error']}")
        self.directory[sid] = entry
        await c.send({"t": "dir_announce", "ok": True, "address": address, "reachable": entry["reachable"],
                      "error": entry["error"]})

    async def on_dir_remove(self, c: Client, msg: dict) -> None:
        entry = (self.directory or {}).get(str(msg.get("id", "")))
        if entry and secrets.compare_digest(entry["secret"], str(msg.get("secret", ""))):
            del self.directory[entry["id"]]
            log.info("public server %s went private", entry["name"])
        await c.send({"t": "dir_remove", "ok": True})

    async def on_dir_list(self, c: Client, msg: dict) -> None:
        """The public servers that can be reached. A server on this same machine is given with the address the
        asker reached this one at (a Raspberry Pi listing itself is reachable at whatever address its players use)."""
        mine = c.writer.get_extra_info("sockname")
        out = []
        for e in list((self.directory or {}).values()):
            if now() - e["seen"] > LISTING_TTL:
                self.directory.pop(e["id"], None)
                continue
            if not e["reachable"]:
                continue
            address = f"{mine[0]}:{e['port']}" if e["local"] and mine else e["address"]
            out.append({k: v for k, v in e.items() if k not in ("secret", "local", "checked", "error")}
                       | {"address": address})
        await c.send({"t": "dir_list", "servers": out, "error": None if self.directory is not None
                      else "this server doesn't keep a list of servers"})

    def listing(self) -> dict:
        s = self.store.settings
        return {"t": "dir_announce", "id": s["id"], "secret": s["listing_secret"], "port": self.port,
                "address": s["public_address"], "name": self.name(), "description": s["description"],
                "version": VERSION, "protocol": PROTOCOL, "players": len(self.clients), "max_players": self.max_players,
                "password": bool(s["password"]), "names": [c.name for c in self.clients.values()][:16],
                "mods_count": len(self.modset()["mods"]), "world": world_summary(self.store.world_save())}

    def directories(self) -> list[str]:
        out = list(self.store.settings["directories"])
        if self.directory is not None:
            out.insert(0, f"127.0.0.1:{self.port}")          # a directory lists itself too
        return out

    async def announcer(self) -> None:
        """While public: tell each directory about this server every minute (and right after a settings change)."""
        self.announce_now = asyncio.Event()
        while True:
            if self.store.settings["public"]:
                for d in self.directories():
                    try:
                        answer = await request(d, self.listing(), "dir_announce", timeout=10)
                        self.public_status[d] = {k: answer.get(k) for k in ("ok", "address", "reachable", "error")} \
                            | {"time": now()}
                    except (OSError, ValueError, asyncio.TimeoutError, ConnectionError) as e:
                        self.public_status[d] = {"ok": False, "error": f"can't reach the directory ({e or 'timeout'})",
                                                 "time": now()}
            try:
                await asyncio.wait_for(self.announce_now.wait(), ANNOUNCE_EVERY)
            except asyncio.TimeoutError:
                pass
            self.announce_now.clear()

    async def unlist(self) -> None:
        s = self.store.settings
        for d in self.directories():
            try:
                await request(d, {"t": "dir_remove", "id": s["id"], "secret": s["listing_secret"]}, "dir_remove", 5)
            except (OSError, ValueError, asyncio.TimeoutError, ConnectionError):
                pass
        self.public_status.clear()

    # ------------------------------------------------------------------ keepalive
    async def keepalive(self) -> None:
        while True:
            await asyncio.sleep(PING_EVERY)
            await self.broadcast({"t": "ping", "time": now()})


async def start(args):
    """Start serving and return once listening: (asyncio server, background tasks). The all-in-one app
    (client/launcher.py, client/companion.py) calls this to host inside the same program as the companion."""
    store = Store(Path(args.data).expanduser().resolve())
    server = Server(store, password=args.password, max_players=args.max_players, motd=args.motd,
                    hosted=getattr(args, "hosted", False), name=getattr(args, "name", None),
                    directory=getattr(args, "directory", False))
    if getattr(args, "directories", None) is not None:
        store.settings["directories"] = list(args.directories)
    if getattr(args, "public", None) is not None:
        store.settings["public"] = bool(args.public)
    store.save_settings()
    srv = await asyncio.start_server(server.handle, args.host, args.port, limit=MAX_LINE)
    server.port = srv.sockets[0].getsockname()[1]
    addrs = ", ".join(str(s.getsockname()[:2]) for s in srv.sockets)
    log.info("inZOI Multiplayer server %s \"%s\" listening on %s (data: %s)%s%s%s", VERSION, server.name(), addrs,
             store.root, " - password required" if server.password else "",
             " - public" if store.settings["public"] else "", " - keeps the public server list" if server.directory
             is not None else "")
    srv.mp_server = server
    try:                                         # LAN discovery (UDP, same port): every way of hosting gets it
        server.udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: Discovery(server), local_addr=(args.host or "0.0.0.0", server.port))
    except OSError as e:
        log.warning("LAN discovery is off (UDP port %d: %s)", server.port, e.strerror or e)
    return srv, asyncio.gather(server.keepalive(), server.announcer(), server.hold_udp())


async def main_async(args) -> None:
    srv, ka = await start(args)
    stop = asyncio.Event()
    srv.mp_server.stop_event, srv.mp_server.restartable = stop, True
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):     # Windows: Ctrl+C raises KeyboardInterrupt instead
            pass
    await stop.wait()
    log.info("shutting down")
    await srv.mp_server.broadcast({"t": "chat", "id": None, "name": "Server", "time": now(),
                                   "text": "The server is restarting. You'll be reconnected in a moment."})
    await asyncio.sleep(0.5)
    srv.close()
    for c in list(srv.mp_server.conns):              # don't wait for players to hang up: a restart is instant
        await c.close()
    ka.cancel()
    try:
        await asyncio.wait_for(srv.wait_closed(), 5)
    except asyncio.TimeoutError:
        pass
    return srv.mp_server.restart_requested


def main() -> None:
    env = os.environ.get
    ap = argparse.ArgumentParser(description="inZOI Multiplayer server")
    ap.add_argument("--host", default=env("INZOI_MP_HOST", "0.0.0.0"), help="address to listen on (default all)")
    ap.add_argument("--port", type=int, default=int(env("INZOI_MP_PORT", 24242)))
    ap.add_argument("--password", default=env("INZOI_MP_PASSWORD"), help="server password (saved; \"\" for none)")
    ap.add_argument("--data", default=env("INZOI_MP_DATA", str(Path(__file__).resolve().parent / "data")))
    ap.add_argument("--max-players", type=int, default=int(env("INZOI_MP_MAX_PLAYERS") or 0) or None)
    ap.add_argument("--motd", default=env("INZOI_MP_MOTD"), help="message shown to players when they join (saved)")
    ap.add_argument("--name", default=env("INZOI_MP_NAME"), help="the server's name on the servers page (saved)")
    ap.add_argument("--directory", action="store_true", default=bool(env("INZOI_MP_DIRECTORY")),
                    help="also keep the list of public servers that players' servers pages show")
    ap.add_argument("--public", action="store_true", default=None, help="list this server publicly (saved)")
    ap.add_argument("--list-on", action="append", metavar="ADDRESS", dest="directories",
                    help="a server that keeps the public list, to be listed on (saved; repeat for more)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--announce-restart", type=int, metavar="SECONDS",
                    help="tell the server running on this machine to have everyone save, then exit (restart it after)")
    ap.add_argument("--admin-code", action="store_true", help="print the code that makes a player an admin")
    ap.add_argument("--make-admin", metavar="NAME_OR_ID", help="make a player an admin of the server running here")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    if args.admin_code:
        print(Store(Path(args.data).expanduser().resolve()).settings["admin_code"])
        return
    if args.announce_restart or args.make_admin:
        async def tell():
            r, w = await asyncio.open_connection("127.0.0.1", args.port)
            msg = {"t": "announce_restart", "in": args.announce_restart} if args.announce_restart else \
                {"t": "make_admin", "who": args.make_admin}
            w.write((json.dumps(msg) + "\n").encode())
            await w.drain()
            print(json.loads(await asyncio.wait_for(r.readline(), 10)))
            w.close()
        asyncio.run(tell())
        return
    restart = False
    try:
        restart = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        log.info("shutting down")
    if restart:
        sys.exit(RESTART_EXIT)


if __name__ == "__main__":
    main()
