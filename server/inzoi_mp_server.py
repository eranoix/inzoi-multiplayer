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
import shutil
import signal
import socket
import sys
import time
from pathlib import Path

VERSION = "0.9.42"
PROTOCOL = 1
MAX_LINE = 32 * 1024 * 1024        # house snapshots can be large (only after a successful hello)
MAX_LINE_BEFORE_HELLO = 64 * 1024  # hello, info, directory messages: small; a stranger can't make us buffer 32 MB
MAX_CONNS = 64                     # open connections in all (each player has 2: game + mod files)
MAX_CONNS_PER_IP = 12              # from one address (a couple behind one router: 2 players x 2 + servers page probes)
MAX_PRE_HELLO = 24                 # connections from other machines that haven't joined yet: strangers can't hold
MAX_PRE_HELLO_PER_IP = 6           # every slot (this machine, the host's own program, is never refused)
HELLO_WITHIN = 20                  # seconds after connecting a connection must have said hello by (pings don't count)
MAX_FILE_CONNS = 3                 # file connections one player may have open (a companion runs at most 3 transfers at
                                   # once: its mods up, others' mods down, the world up or down)
MAX_PIECE = 8 * 1024 * 1024        # blob_put: the most bytes one piece may carry (the companion sends 512 KB)
CLAIM_TRIES = 5                    # admin_claim attempts per address per minute
HELLO_FAILS = 10                   # wrong passwords per address per minute before it has to wait (not this machine)
NEW_PLAYERS_PER_IP = 5             # new player records per address per 10 minutes (not this machine)
PING_EVERY = 15                    # seconds between keepalives
TIMEOUT = 45                       # drop a client that sent nothing (not a byte) for this long
FILES_TIMEOUT = 600                # a file connection gets no pings (and can't answer them): it may sit idle longer
SEND_TIMEOUT = 20                  # a peer that takes none of what it's sent for this long is gone: cut off
WRITE_PIECE = 64 * 1024            # big messages go to the socket in pieces: SEND_TIMEOUT measures progress, not size
MAX_BACKLOG = 40 * 1024 * 1024     # bytes queued for one player behind what's being sent (a whole MAX_LINE fits)
MAX_BACKLOG_BEFORE_HELLO = 1024 * 1024
CLOSE_GRACE = 3                    # a connection being closed gets this long to send its last messages (error, kicked)
CHUNK = 512 * 1024                 # mod file transfer: bytes per blob_get answer
SHA = re.compile(r"[0-9a-f]{64}")
PLAYER_ID = re.compile(r"p[0-9a-f]{8}")   # "p" + secrets.token_hex(4): every player id this server hands out
FILE_MESSAGES = {"blob_check", "blob_put", "blob_get", "ping", "pong"}
BEFORE_HELLO = {"info", "ping", "pong", "dir_announce", "dir_list", "dir_remove"}   # allowed without joining
PART_TTL = 86400                   # an unfinished upload (.part) older than this is deleted
SHARED_KEY = re.compile(r"[a-z0-9_.:-]{1,96}")
MAX_SHARED = 4 * 1024 * 1024       # bytes of JSON one shared value may take (a house with its exact copy fits)
MAX_SHARED_TOTAL = 16 * 1024 * 1024    # all shared values together: every welcome carries them all, in one line
MAX_SHARED_KEYS = 4096
# Every welcome carries the players online, the shared houses, the mod set and the shared state in one line, and a
# companion reads at most MAX_LINE in one line: what a player can store or relay into it is capped (bytes of JSON).
MAX_PROFILE = 2 * 1024 * 1024      # a player's Zoi (a real one: tens of KB)
MAX_STATE = 64 * 1024              # a live state (real ones: under 2 KB)
MAX_WORLD = 64 * 1024              # the leader's clock and weather (real: under 1 KB)
MAX_MODS = 2 * 1024 * 1024         # one player's mod list (a few hundred bytes a mod): past it, the mods at its end go
MAX_MODSET = 8 * 1024 * 1024       # the mod set everyone gets: past it, the mods of the lists updated longest ago go
MAX_HOUSES = 200                   # shared houses listed (welcome, house_list): the players' online, then the newest
WELCOME_ROOM = MAX_LINE - 256 * 1024   # a welcome past this sends the Zois, states and mod set as lines after it
LOT_IN_USE = 600                  # a lot whose shared keys changed this recently is never cleared for room (make_room)
MAX_DEPTH = 100                    # nesting levels a message may have (real ones: under 10; json.dumps breaks far deeper)
INT_DIGITS = 4300                  # digits a JSON integer may have: Python 3.11+ (every companion) reads no longer one
MAX_REF = 200                      # a "ref" longer than this isn't echoed (it would go to everyone with each "shared")
LINGER = 10                        # seconds a player who dropped without "bye" stays (a blip, a restart): no Zoi flicker
PENDING_MAX = 64                   # events held for a player who is reconnecting (delivered when they're back)...
PENDING_BYTES = 1024 * 1024        # ...and their size in all
BLOB_GRACE = 3600                  # an unreferenced blob younger than this is kept (it may be part of an upload)
HISTORY = 20                       # world versions kept (their files stay on disk: an admin can go back to one)
PLAYERS_FLUSH = 60                 # seconds between saves of players.json when only "last seen" times changed
ANNOUNCE_EVERY = 60                # seconds between a public server's announcements to its directories
LISTING_TTL = 180                  # a directory drops a server it hasn't heard from for this long
RECHECK = 300                      # seconds before a directory checks again that a listed server can be reached
MAX_LISTED = 500                   # servers a directory lists at most...
MAX_LISTED_PER_IP = 4              # ...from one address (not this machine)
NEW_LISTINGS_PER_IP = 5            # new listings per address per 10 minutes
PROBE_EVERY = 10                   # seconds between a directory's reachability checks asked for by one address
DIR_LIST_MAX = 200                 # servers in one dir_list answer (newest first)
RESTART_EXIT = 75                  # exit code after an admin's restart (systemd's Restart=always starts it again)
DISCOVER_ASK = b"INZOI-MP?"         # LAN discovery: a UDP datagram to the server's port (broadcast) gets a "discover"
LAN_NETS = [ipaddress.ip_network("100.64.0.0/10")]   # (Tailscale's range isn't "private" to ipaddress; answered too)
SURROGATE_ESCAPE = re.compile(r"\\u[dD][89a-fA-F]")  # the only way a lone UTF-16 surrogate can reach a parsed message
SETTINGS = {"name": "", "description": "", "motd": "", "public": False, "password": "", "max_players": 16,
            "public_address": "", "directories": [], "admins": [], "bans": {}}

log = logging.getLogger("inzoi-mp")
RECENT: collections.deque = collections.deque(maxlen=300)     # the latest log lines, for the admin console


class _Recent(logging.Handler):
    def emit(self, record):
        RECENT.append(f"{time.strftime('%H:%M:%S', time.localtime(record.created))} {record.getMessage()}")


log.addHandler(_Recent())


def now() -> float:
    """Wall-clock time: only for times that are stored or shown (a chat line's, "last seen", a shared value's)."""
    return time.time()


def mono() -> float:
    """A clock that only goes forward, for every interval (timeouts, the keepalive, rate limits, uptime): a step of the
    wall clock (a Raspberry Pi without a clock battery setting its time from the network after players joined, a
    manual fix) must neither cut everyone off nor keep a rate limit or a timeout from ever ending. Its zero is
    arbitrary (a Pi's is its boot): never compare it with 0."""
    return time.monotonic()


def json_size(value) -> int:
    """Bytes of JSON a value takes on the wire (encode: compact, ASCII)."""
    return len(json.dumps(value, separators=(",", ":")))


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


def fsync_file(path: Path) -> None:
    """path's content flushed to disk: a file renamed to a new name after this can't come back empty or cut short after
    a power cut (ext4, a Pi's SD card, writes a new file's data out up to 30 s after its rename)."""
    with path.open("r+b") as f:                      # (Windows flushes only a handle that may write)
        os.fsync(f.fileno())


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def world_summary(meta: dict | None) -> dict | None:
    return meta and {k: meta.get(k) for k in ("version", "name", "by", "time", "size")}


def split_address(address: str, default_port: int = 24242) -> tuple[str, int]:
    host, _, port = str(address).strip().rpartition(":") if ":" in str(address) else (str(address).strip(), "", "")
    return (host or str(address).strip()).strip("[]"), int(port) if str(port).isdigit() else default_port


def norm_ip(text: str):
    """An address as ipaddress sees it (an IPv4 address reached over IPv6, ::ffff:a.b.c.d, as plain IPv4), or None."""
    try:
        ip = ipaddress.ip_address(str(text).split("%")[0])
    except ValueError:
        return None
    return ip.ipv4_mapped or ip if ip.version == 6 else ip


def encode(msg: dict) -> bytes | None:
    """One message as a wire line. ASCII (\\u escapes, the default), so no string a client managed to get past the
    checks can make it fail; anything else that can't be written is logged and the message skipped, never raised
    into a handler (one odd value must not break a broadcast or someone's welcome)."""
    try:
        return (json.dumps(msg, separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, RecursionError) as e:
        log.warning("couldn't write a %r message: %s", str(msg.get("t"))[:40], e)
        return None


def coalesce_key(msg: dict):
    """Messages that only matter as the latest of their kind (a newer one replaces them): a player's live state and
    skeleton frames, the world clock, the keepalive. A client that's behind gets the newest instead of all of them."""
    t = msg.get("t")
    if t == "state":
        return ("state", msg.get("id"))
    if t == "event" and msg.get("kind") == "pose" and isinstance(msg.get("data"), dict):
        return ("pose", msg.get("from"), "k" if "k" in msg["data"] else "d")
    if t in ("ping", "world"):
        return (t,)
    return None


def too_deep(obj, limit: int = MAX_DEPTH) -> bool:
    """Nested deeper than `limit` (checked without recursion, so the check itself can't overflow)."""
    stack = [(obj, 1)]
    while stack:
        o, d = stack.pop()
        if isinstance(o, dict):
            items = o.values()
        elif isinstance(o, list):
            items = o
        else:
            continue
        if d > limit:
            return True
        stack.extend((v, d + 1) for v in items)
    return False


def short_int(text: str) -> int:
    """json.loads' parse_int: an integer of at most INT_DIGITS digits, else ValueError. A server interpreter without
    Python's own limit (CPython before 3.9.14 / 3.10.7, or run with it off) would otherwise store one that every
    companion then fails to read, in each welcome; and wouldn't spend seconds converting a huge one either."""
    if len(text) - text.startswith("-") > INT_DIGITS:
        raise ValueError(f"an integer longer than {INT_DIGITS} digits")
    return int(text)


def small_int(value) -> int:
    """int(value) for a number a message gave, raising ValueError/TypeError like int() does, but never converting a
    long digit string: on an interpreter without Python's int-digit limit (see short_int) that takes quadratic time,
    and one such message would hold up the whole server for minutes."""
    if isinstance(value, (str, bytes)) and len(value) > 20:
        raise ValueError("not a number")
    return int(value)


def loads_ints_limited(text: str):
    """json.loads, refusing (ValueError) an integer longer than INT_DIGITS digits. Python's own limit does that already
    when it's on and no higher (3.11+, 3.10.7+, 3.9.14+ by default): then without the slower parse_int callback."""
    get = getattr(sys, "get_int_max_str_digits", None)
    if get is not None and 0 < get() <= INT_DIGITS:
        return json.loads(text)
    return json.loads(text, parse_int=short_int)


def parse(line: bytes) -> dict | None:
    """A received line as a message, or None if it isn't one we can safely keep and pass on: not strict UTF-8 (CESU-8
    surrogates included), not a JSON object, nested too deep (json.dumps would fail on it later, after it was
    stored), holding a lone UTF-16 surrogate (an escape json.loads accepts but no UTF-8 text can hold), or an integer
    longer than a companion reads (INT_DIGITS)."""
    try:
        text = line.decode("utf-8")
        msg = loads_ints_limited(text)
        if not isinstance(msg, dict):
            return None
        if text.count("{") + text.count("[") > MAX_DEPTH and too_deep(msg):
            return None
        if SURROGATE_ESCAPE.search(text):                # (emoji arrive as escaped pairs: those are fine)
            json.dumps(msg, ensure_ascii=False).encode("utf-8")
        return msg
    except (ValueError, RecursionError):                 # UnicodeError and JSONDecodeError are ValueErrors
        return None


def clean_ref(ref):
    """A message's "ref" as it may be echoed back: a short string or a number; anything else isn't echoed."""
    if ref is None or isinstance(ref, (bool, int, float)):
        return ref
    if isinstance(ref, str) and len(ref) <= MAX_REF:
        return ref
    return None


def tune_socket(writer: asyncio.StreamWriter) -> None:
    """TCP keepalive (and on Linux a limit on unacknowledged data): a peer that vanished without a word (Wi-Fi gone,
    PC asleep) is noticed in about a minute instead of the system's default of a quarter of an hour."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except (OSError, AttributeError, ValueError):
        return
    for name, value in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3), ("TCP_USER_TIMEOUT", 60000)):
        opt = getattr(socket, name, None)
        if opt is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, opt, value)
            except (OSError, ValueError):
                pass


class Tries:
    """Recent attempts per address, for rate limits: the times within the last `window` seconds."""

    def __init__(self, window: float):
        self.window, self.by = window, {}

    def count(self, key: str) -> int:
        q = self.by.get(key)
        if q is None:
            return 0
        t = mono()
        while q and t - q[0] > self.window:
            q.popleft()
        if not q:
            del self.by[key]
        return len(q)

    def add(self, key: str) -> None:
        self.by.setdefault(key, collections.deque()).append(mono())

    def clear(self, key: str) -> None:
        self.by.pop(key, None)

    def prune(self) -> None:
        for key in list(self.by):
            self.count(key)                          # (drops the ones whose attempts are all old)
        if len(self.by) > 10000:                     # a flood from many addresses: forget rather than grow
            self.by.clear()


async def request(address: str, msg: dict, reply: str, timeout: float = 6, limit: int = 1 << 20) -> dict:
    """One question to another server (before any hello): send msg, return the first answer of type `reply`. One
    deadline for the whole exchange (connecting, sending, every line read), and a cap on the size of an answer."""
    host, port = split_address(address)
    if not 0 < port < 65536:
        raise ValueError(f"bad port in {address!r}")

    async def exchange() -> dict:
        r, w = await asyncio.open_connection(host, port, limit=limit)
        try:
            w.write((json.dumps(msg) + "\n").encode())
            await w.drain()
            for _ in range(50):                      # a few unrelated lines (a ping) at most
                line = await r.readline()
                if not line:
                    raise ConnectionError("no answer")
                try:
                    m = json.loads(line)
                except RecursionError:
                    raise ValueError("the answer is nested too deep") from None
                if not isinstance(m, dict):
                    raise ValueError("the answer isn't a JSON object")
                if m.get("t") in (reply, "error"):
                    return m
            raise ConnectionError("no answer")
        finally:
            w.close()
    return await asyncio.wait_for(exchange(), timeout)


def _retry(fn, *args) -> None:
    """A file operation, tried again a few times on PermissionError: on Windows an antivirus, the search indexer or a
    backup tool keeps a file open for a moment, and a replace or delete fails while it does."""
    for pause in (0.01, 0.03, 0.06, 0.1, 0.2, None):
        try:
            return fn(*args)
        except PermissionError:
            if pause is None:
                raise
            time.sleep(pause)


def _unlink(path: Path) -> None:
    try:
        _retry(os.unlink, path)
    except FileNotFoundError:
        pass


class Store:
    """Everything that survives a restart: known players, their Zoi profiles and house snapshots."""

    def __init__(self, root: Path):
        self.root = Path(root)
        for sub in ("houses", "mods", "blobs", "world_history"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self.root_real = self.root.resolve()
        self.players_file = self.root / "players.json"
        players = self._load(self.players_file, {})
        self.players: dict[str, dict] = {pid: p for pid, p in (players.items() if isinstance(players, dict) else ())
                                         if isinstance(p, dict) and isinstance(p.get("key"), str)}
        self.key_index: dict[str, str] = {}      # sha256(key) -> player id: a hello finds its player at once
        for pid, p in self.players.items():
            self.key_index.setdefault(key_hash(p["key"]), pid)
        self.players_dirty = False               # changed in memory (a "last seen" time, a failed save) but not saved
        self.players_failing = False
        self.players_saved = mono()
        self.settings_file = self.root / "server.json"
        settings = self._load(self.settings_file, {})
        self.settings: dict = {**SETTINGS, **(settings if isinstance(settings, dict) else {})}
        self.settings.setdefault("id", secrets.token_hex(8))                  # who this server is, whatever its address
        self.settings.setdefault("admin_code", secrets.token_hex(4))          # typed in the admin console: admin
        self.settings.setdefault("listing_secret", secrets.token_hex(16))     # proves to directories it's this server
        self.shared_epoch: str | None = None     # set by load_shared
        self._houses: dict[str, dict] | None = None   # player id -> their house's meta (read once, then kept up to date)
        self.save_settings()

    def save_settings(self) -> None:
        self._save(self.settings_file, self.settings)

    def _load(self, path: Path, default):
        """A saved file. If it's missing or damaged (power cut mid-write...), what's left of its last save: the .tmp
        that was being swapped in (complete or json.loads refuses it), then the copy before it (.bak), loudly; and the
        file is written back so the next start finds it. Never the default in silence: that would drop the password
        and everyone's profile. A damaged file is set aside as .corrupt-<time> (only inside the data folder)."""
        tmp, bak = path.with_name(path.name + ".tmp"), path.with_name(path.name + ".bak")
        restore = True
        try:
            return json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            if not tmp.exists() and not bak.exists():
                return default                       # never saved yet: normal
            log.warning("%s is missing: using what's left of its last save", path)
        except (ValueError, RecursionError) as e:
            log.warning("%s is damaged (%s): trying its backup", path, str(e)[:200])
            self._set_aside(path)
        except OSError as e:                         # can't be read right now (locked): leave it alone, read a copy
            log.warning("%s can't be read (%s): trying its backup", path, e)
            restore = False
        for copy in (tmp, bak):
            try:
                data = json.loads(copy.read_text("utf-8"))
            except FileNotFoundError:
                continue
            except (ValueError, RecursionError) as e:
                log.warning("%s can't be used either (%s)", copy.name, str(e)[:200])
                continue
            except OSError as e:
                # Locked right now (an antivirus): it may well be the newest copy, so nothing is written back over it
                # (writing back from the .bak would go through that same .tmp). The next start tries it again.
                log.warning("%s can't be read either (%s)", copy.name, e)
                restore = False
                continue
            if restore:
                self._restore(path, copy, data)
            return data
        log.warning("no usable copy of %s: it starts over from defaults", path.name)
        return default

    def _restore(self, path: Path, copy: Path, data) -> None:
        """path back from copy (its .tmp or .bak, whose content is data), so the next start finds it. A .tmp is renamed
        into place, never written again: it's the only complete copy of the newest content, and _save writes that same
        .tmp and deletes it when its final replace fails (a lock that outlasts the retries), which would leave only the
        older .bak for the next start. A .bak is written back through _save and stays the backup."""
        try:
            if copy.name.endswith(".tmp"):
                _retry(os.replace, copy, path)
                self._sync_folder(path)
            elif path.exists():
                # the damaged file couldn't be set aside: _save would make it the backup, over the only good copy
                raise OSError(f"the damaged {path.name} couldn't be moved aside")
            else:
                self._save(path, data)
        except (OSError, ValueError, TypeError, RecursionError) as e:
            log.warning("couldn't write %s back from %s (it's tried again at the next start): %s", path.name,
                        copy.name, e)

    def _set_aside(self, path: Path) -> None:
        try:
            if path.resolve().is_relative_to(self.root_real):      # never rename anything outside the data folder
                _retry(os.replace, path, path.with_name(f"{path.name}.corrupt-{int(now())}"))
        except (OSError, ValueError):
            pass

    @staticmethod
    def _save(path: Path, data) -> None:
        """Written to a .tmp and flushed to disk, then swapped in with one atomic replace: the file always exists under
        its own name, holding either the old or the new content. The old content stays as .bak."""
        text = json.dumps(data)                      # ASCII (\u escapes): no character can make the write fail
        tmp = path.with_name(path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            if path.exists():
                Store._keep_backup(path)
            _retry(os.replace, tmp, path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        Store._sync_folder(path)

    @staticmethod
    def _sync_folder(path: Path) -> None:
        """A rename into path survives a power cut (a Pi's SD card): the folder is flushed too."""
        if os.name == "posix":
            try:
                fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                pass

    @staticmethod
    def _keep_backup(path: Path) -> None:
        """path.bak becomes the current content of path, without path ever going away: a second link to the same
        data (no copying) where the file system allows it, a copy otherwise. Only a convenience: a failure is logged."""
        bak, btmp = path.with_name(path.name + ".bak"), path.with_name(path.name + ".bak.tmp")
        try:
            _unlink(btmp)
            try:
                os.link(path, btmp)
            except (OSError, NotImplementedError, AttributeError):
                shutil.copyfile(path, btmp)
            _retry(os.replace, btmp, bak)
        except OSError as e:
            log.warning("couldn't refresh the backup of %s: %s", path.name, e)

    @staticmethod
    def _scan(folder: Path, pattern: str) -> list[Path]:
        """The files matching pattern, including those of which only a .bak or .tmp is left (a crash mid-save): _load
        recovers them."""
        names = {f.name for f in folder.glob(pattern)}
        for suffix in (".bak", ".tmp"):
            names |= {f.name[:-len(suffix)] for f in folder.glob(pattern + suffix)}
        return [folder / n for n in sorted(names)]

    # Players: a hello finds its record by key (find_player); a new key gets a record only once the join is allowed.
    def find_player(self, key: str) -> str | None:
        return self.key_index.get(key_hash(key))

    def register(self, key: str, name: str) -> str:
        """Players are identified by a secret key their companion generates once; the id others see is derived."""
        pid = "p" + secrets.token_hex(4)
        while pid in self.players:
            pid = "p" + secrets.token_hex(4)
        t = now()
        self.players[pid] = {"key": key, "name": name, "first_seen": t, "last_seen": t, "profile": None}
        self.key_index[key_hash(key)] = pid
        self.save_players()
        return pid

    def seen(self, pid: str, name: str) -> None:
        """A known player joined: their name is saved at once if it changed; "last seen" goes with the next save."""
        p = self.players[pid]
        renamed = p.get("name") != name
        p["name"], p["last_seen"] = name, now()
        if renamed:
            self.save_players()
        else:
            self.players_dirty = True

    def player_for_key(self, key: str, name: str) -> dict:
        pid = self.find_player(key)
        if pid is None:
            pid = self.register(key, name)
        else:
            self.seen(pid, name)
        return {"id": pid, **self.players[pid]}

    def save_players(self) -> bool:
        """Never raises: a disk that can't be written (full, read-only, a file locked for a moment) must not refuse a
        hello or a profile. What's in memory is right, and is saved again shortly (flush_players)."""
        try:
            self._save(self.players_file, self.players)
        except (OSError, ValueError, TypeError, RecursionError) as e:
            if not self.players_failing:
                log.error("couldn't save players.json (%s): kept in memory, trying again shortly", e)
            self.players_dirty = self.players_failing = True
            return False
        if self.players_failing:
            log.info("players.json saved again")
        self.players_dirty = self.players_failing = False
        self.players_saved = mono()
        return True

    def flush_players(self, force: bool = False) -> None:
        if self.players_dirty and (force or self.players_failing or mono() - self.players_saved > PLAYERS_FLUSH):
            self.save_players()

    # The shared state: what's true for everyone (the couple's house, their family, ...). Each key holds the latest
    # value with the version (a server-wide sequence number) it got when it was set, who set it and when. The epoch
    # names this data folder's shared state: a new or wiped folder gets a new one, so a client knows the versions it
    # remembers are from another state.
    def load_shared(self) -> tuple[int, dict]:
        folder = self.root / "shared"
        folder.mkdir(exist_ok=True)
        meta = self._load(folder / "_seq.json", {})
        meta = meta if isinstance(meta, dict) else {}
        try:
            seq = int(meta.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        epoch = meta.get("epoch")
        items = {}
        for f in self._scan(folder, "*.json"):
            if f.name == "_seq.json":
                continue
            item = self._load(f, None)
            if not (isinstance(item, dict) and SHARED_KEY.fullmatch(str(item.get("key", "")))):
                continue
            try:
                v = int(item.get("v") or 0)
                loads_ints_limited(json.dumps(item))  # (one that can't be written, or read back, breaks every welcome)
            except (TypeError, ValueError, RecursionError):
                log.warning("shared %s can't be used: left out", f.name)
                continue
            items[item["key"]] = item
            seq = max(seq, v)
        if not (isinstance(epoch, str) and re.fullmatch(r"[0-9a-f]{8,64}", epoch)):
            epoch = secrets.token_hex(8)
            try:
                self._save(folder / "_seq.json", {"seq": seq, "epoch": epoch})
            except OSError as e:
                log.warning("couldn't save the shared state's epoch: %s", e)
        self.shared_epoch = epoch
        return seq, items

    def save_seq(self, seq: int) -> None:
        self._save(self.root / "shared" / "_seq.json", {"seq": seq, "epoch": self.shared_epoch})

    def shared_path(self, key: str) -> Path:
        return self.root / "shared" / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")

    def save_shared(self, seq: int, item: dict | None, key: str, seq_saved: bool = False) -> None:
        """Raises OSError if it couldn't be saved (the caller then changes nothing). The sequence goes first, so the
        number on disk is never behind a version handed out, and a restart never hands the same version out twice
        (seq_saved: the caller saved one at least this high already). A removal is on disk for good when this returns,
        like a set: the folder is flushed after the deletes (on Linux a delete survives a power cut only then)."""
        if not seq_saved:
            self.save_seq(seq)
        if item is None:
            self.drop_shared(key)
            self.sync_shared()
        else:
            self._save(self.shared_path(key), item)

    def drop_shared(self, key: str) -> None:
        """A key's files deleted (the copies first: none of them can bring the key back). Raises OSError if one can't
        be. Lasts through a power cut only once the folder is flushed (sync_shared): a batch flushes it once."""
        path = self.shared_path(key)
        for p in (path.with_name(path.name + ".tmp"), path.with_name(path.name + ".bak"), path):
            _unlink(p)

    def sync_shared(self) -> None:
        self._sync_folder(self.root / "shared" / "_seq.json")

    def set_profile(self, pid: str, profile) -> None:
        self.players[pid]["profile"] = profile
        self.save_players()

    def house_path(self, pid: str) -> Path:
        if not PLAYER_ID.fullmatch(str(pid)):        # a player id, never a path: no ../ out of the data folder
            raise ValueError(f"bad player id {str(pid)[:20]!r}")
        return self.root / "houses" / f"{pid}.json"

    def put_house(self, pid: str, house) -> dict:
        house = house or {}
        meta = house_meta(pid, house, now())
        self._save(self.house_path(pid), {"meta": meta, "house": house})
        if self._houses is not None:
            self._houses[pid] = meta
        return meta

    def get_house(self, pid: str):
        if not PLAYER_ID.fullmatch(str(pid)):
            return None
        d = self._load(self.house_path(pid), None)
        if not (isinstance(d, dict) and isinstance(d.get("meta"), dict)):
            return None
        return {**d, "meta": house_meta(pid, d["meta"], d["meta"].get("updated"))}

    def list_houses(self) -> list[dict]:
        if self._houses is None:
            houses = {}
            for f in self._scan(self.root / "houses", "p*.json"):
                if PLAYER_ID.fullmatch(f.stem):
                    d = self._load(f, None)
                    if isinstance(d, dict) and isinstance(d.get("meta"), dict):
                        houses[f.stem] = house_meta(f.stem, d["meta"], d["meta"].get("updated"))
            self._houses = houses
        return list(self._houses.values())

    # Mods: each player's list of enabled mods (with a sha256 per file), and the files themselves as blobs named by
    # their sha256, so a file several players have is stored once.
    mods_changed = 0                   # bumped on every set_mods, so the server knows its cached mod set is stale

    def set_mods(self, pid: str, name: str, mods: list) -> None:
        self._save(self.root / "mods" / f"{pid}.json", {"name": name, "updated": now(), "mods": mods})
        self.mods_changed += 1

    def all_mods(self) -> dict[str, dict]:
        out = {}
        for f in self._scan(self.root / "mods", "p*.json"):
            d = self._load(f, None)
            if isinstance(d, dict):
                out[f.stem] = d
        return out

    def blob(self, sha: str) -> Path:
        return self.root / "blobs" / sha

    # The shared world: one game save everyone plays, as files (blobs) plus a version that goes up with each upload.
    _world: tuple | None = None        # (meta, {sha}): the current world, read once (blob_get asks per 512 KB)

    def world_save(self) -> dict | None:
        if self._world is None:
            meta = self._load(self.root / "world_save.json", None)
            meta = meta if isinstance(meta, dict) else None
            self._world = (meta, {f["h"] for f in (meta or {}).get("files", [])})
        return self._world[0]

    def world_blobs(self) -> set:
        self.world_save()
        return self._world[1]

    def set_world_save(self, meta: dict) -> None:
        """The new current world; the last HISTORY versions are kept so an admin can go back to one."""
        self._save(self.root / "world_save.json", meta)
        self._world = None
        folder = self.root / "world_history"
        self._save(folder / f"{meta['version']}.json", meta)
        for old in sorted(self._scan(folder, "*.json"), key=lambda f: int(f.stem) if f.stem.isdigit() else 0)[:-HISTORY]:
            for p in (old.with_name(old.name + ".tmp"), old.with_name(old.name + ".bak"), old):
                _unlink(p)

    def blobs_in_use(self, keep: set = frozenset()) -> set:
        """Every blob something uses: a file of anyone's mods (all stored lists, not just the current mod set), of the
        current world or a kept world version, or in `keep` (uploads under way)."""
        used = set(keep) | self.world_blobs()
        for d in self.all_mods().values():
            used |= {f["h"] for m in d.get("mods", []) for f in m.get("files", [])}
        for m in self.world_history():
            used |= {f["h"] for f in m.get("files", [])}
        return used

    def gc_blobs(self, keep: set = frozenset(), used: set | None = None) -> None:
        """Deletes blobs nothing uses any more (blobs_in_use; `used` when the caller worked it out already) once over
        an hour old, and .part files of uploads abandoned over a day ago. Mod files and world files share the folder,
        hence the full list."""
        used = self.blobs_in_use(keep) if used is None else used
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
        out = [self._load(f, None) for f in self._scan(self.root / "world_history", "*.json")]
        return sorted((m for m in out if isinstance(m, dict) and isinstance(m.get("version"), int)),
                      key=lambda m: -m["version"])

    def world_version(self, version: int) -> dict | None:
        m = self._load(self.root / "world_history" / f"{int(version)}.json", None)
        return m if isinstance(m, dict) else None


def house_meta(pid: str, src: dict, updated) -> dict:
    """What's listed about a shared house (welcome, house_list, house_updated): short fields only, from what was sent
    or what's on disk (a file of a server that kept "lot" as it came, any size: every welcome lists every house)."""
    lot = src.get("lot", "")
    if not (isinstance(lot, int) and not isinstance(lot, bool) and abs(lot) < 1 << 53):
        lot = str("" if lot is None else lot)[:200]
    if not isinstance(updated, (int, float)) or isinstance(updated, bool):
        updated = 0.0
    return {"owner": pid, "updated": updated, "name": str(src.get("name", ""))[:200],
            "by": str(src.get("by", ""))[:200], "lot": lot}


def fit_mods(mods: list, limit: int, rank=None) -> list:
    """mods within `limit` bytes of JSON: the first ones (in order, or by `rank`, a sort key on their index) up to the
    first that doesn't fit, in their order. (A player's own list loses its end only: the companion uploads its mods'
    files in the list's order, and the server takes only files of the mods it kept.)"""
    sizes = [json_size(m) + 1 for m in mods]
    if sum(sizes) <= limit:
        return mods
    keep, total = set(), 0
    for i in sorted(range(len(mods)), key=rank) if rank else range(len(mods)):
        total += sizes[i]
        if total > limit:
            break
        keep.add(i)
    return [m for i, m in enumerate(mods) if i in keep]


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
    """One connection. What's sent to it goes through its own queue, written by its own task (only while there's
    something to write), so nothing anywhere ever waits for this peer: a player whose PC froze, whose Wi-Fi died
    without a word, or who is downloading a big welcome over a weak link holds up nobody else, not even the keepalive.
    A peer that takes nothing for SEND_TIMEOUT, or falls hopelessly behind (MAX_BACKLOG), is cut off (transport.abort:
    close() alone would wait forever for a peer that doesn't read); it reconnects and gets a fresh welcome."""

    def __init__(self, server: "Server", reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.server = server
        self.reader = reader
        self.writer = writer
        self.addr = writer.get_extra_info("peername")
        self.id: str | None = None
        self.name = "?"
        self.state: dict | None = None     # last live state (position, animation, ...)
        self.opened = self.last_heard = self.last_rx = mono()
        self.last_progress = float("-inf")   # when the peer last took a buffer's worth of what we sent (it's alive)
        self.closed = False                # nothing more is sent (a graceful close may still be flushing the queue)
        self.aborted = False               # the connection is cut
        self.files = False                 # a second connection of a player, only for mod file transfers
        self.joined = 0.0
        self.version = ""                  # the player's companion app version (others are warned on a mismatch)
        self.said_bye = False
        self.mine = (None, {})             # (mods_changed, {sha: size}) of this player's own mod files (blob_put)
        self.out: collections.deque = collections.deque()   # waiting to be written: [bytes, coalesce key or None]
        self.out_bytes = 0
        self.latest: dict = {}             # coalesce key -> its entry still waiting in self.out
        self.stale = 0                     # entries emptied by a newer one (skipped when their turn comes)
        self.pump: asyncio.Task | None = None
        self.warned: set = set()           # what this connection sent too big (Server.too_big: logged once)

    def ip(self) -> str:
        return str((self.addr or ("",))[0])

    def local(self) -> bool:
        return self.ip() in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def public(self) -> dict:
        p = self.server.store.players.get(self.id or "", {})
        return {"id": self.id, "name": self.name, "profile": p.get("profile"), "state": self.state,
                "leader": self.server.leader is self, "version": self.version}

    def busy(self) -> bool:
        """Still has something to send."""
        if self.aborted:
            return False
        try:
            buffered = self.writer.transport.get_write_buffer_size()
        except Exception:                            # noqa: BLE001
            buffered = 0
        return bool(self.out) or self.pump is not None or buffered > 0

    async def send(self, msg: dict) -> None:
        """Queues msg and returns at once (kept a coroutine: handlers await it)."""
        self.send_now(msg)

    def send_now(self, msg: dict) -> None:
        data = encode(msg)
        if data is not None:
            self.push(data, coalesce_key(msg))

    def push(self, data: bytes, key=None) -> None:
        """Queues a wire line. key: a coalesce key (coalesce_key): a newer line with the same key replaces this one
        if it hasn't gone out yet, and such lines are skipped rather than cut a client off when it's far behind."""
        if self.closed or self.aborted:
            return
        transport = self.writer.transport
        if transport.is_closing():
            return
        if self.pump is None and not self.out and len(data) <= WRITE_PIECE \
                and transport.get_write_buffer_size() <= WRITE_PIECE:
            try:                                     # nothing waiting and the socket keeps up: straight out
                self.writer.write(data)
            except (ConnectionError, OSError, RuntimeError):
                self.abort()
            return
        old = self.latest.get(key) if key is not None else None
        limit = MAX_BACKLOG if self.id else MAX_BACKLOG_BEFORE_HELLO
        waiting = self.out_bytes - (len(old[0]) if old is not None else 0) - (len(self.out[0][0]) if self.out else 0)
        if self.out and waiting + len(data) > limit:     # (what's behind the message being sent)
            if key is not None:
                return                               # a frame: the one already waiting goes instead
            log.warning("%s isn't taking what's sent to it (%d MB waiting): disconnecting", self.name or self.addr,
                        self.out_bytes >> 20)
            self.abort()
            return
        if old is not None:                          # latest wins: the older frame still waiting is skipped
            self.out_bytes -= len(old[0])
            old[0] = b""
            self.stale += 1
        entry = [data, key]
        self.out.append(entry)
        self.out_bytes += len(data)
        if key is not None:
            self.latest[key] = entry
        if self.stale > 512 and self.stale * 2 > len(self.out):
            self.out = collections.deque(e for e in self.out if e[0])
            self.stale = 0
        if self.pump is None:
            self.pump = asyncio.get_running_loop().create_task(self._pump())

    async def _pump(self) -> None:
        w = self.writer
        try:
            while self.out and not self.aborted:
                entry = self.out.popleft()
                data, key = entry
                if key is not None and self.latest.get(key) is entry:
                    del self.latest[key]
                if not data:
                    self.stale = max(0, self.stale - 1)
                    continue
                self.out_bytes -= len(data)
                for i in range(0, len(data), WRITE_PIECE):
                    if w.transport.is_closing():
                        raise ConnectionResetError("the connection is closing")
                    w.write(data[i:i + WRITE_PIECE] if len(data) > WRITE_PIECE else data)
                    if w.transport.get_write_buffer_size() > WRITE_PIECE:
                        await asyncio.wait_for(w.drain(), SEND_TIMEOUT)
                        self.last_progress = mono()  # (it took most of a buffer: it's there and reading)
            if self.closed and not self.aborted:
                w.close()                            # a graceful close: the last messages are out
        except asyncio.TimeoutError:
            log.info("%s took nothing for %d s: disconnecting", self.name if self.id else self.addr, SEND_TIMEOUT)
            self.abort()
        except (ConnectionError, OSError, RuntimeError):
            self.abort()
        finally:
            if self.pump is asyncio.current_task():
                self.pump = None

    def abort(self) -> None:
        """Cuts the connection now (whatever is still queued is dropped). Wakes anything waiting on the socket."""
        self.closed = True
        if self.aborted:
            return
        self.aborted = True
        self.out.clear()
        self.latest.clear()
        self.out_bytes = self.stale = 0
        try:
            self.writer.transport.abort()
        except Exception:                            # noqa: BLE001
            pass
        task = self.pump
        if task is not None:
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if task is not current:
                task.cancel()

    def shut(self) -> None:
        """Closes gracefully but never waits: nothing more is queued, what was queued (a fatal error, "kicked") goes
        out, then the socket closes; a peer that doesn't take it within CLOSE_GRACE is cut off."""
        if self.closed:
            return
        self.closed = True
        if self.aborted:
            return
        if self.pump is None and not self.out:
            try:
                self.writer.close()
            except Exception:                        # noqa: BLE001
                pass
        try:
            asyncio.get_running_loop().call_later(CLOSE_GRACE, self.abort)
        except RuntimeError:
            self.abort()

    async def close(self) -> None:
        self.shut()

    async def fail(self, code: str, error: str, **extra) -> None:
        """A fatal error (code: machine-readable, for the UI to translate; error: English), then hang up."""
        self.send_now({"t": "error", "fatal": True, "code": code, "error": error, **extra})
        self.shut()


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
        t, last = mono(), self.last.get(addr[0])
        if last is not None and t - last < 0.2:
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
        self.started = mono()                    # (for the uptime)
        self.port = 24242                        # set by start(): where players reach this server
        self.restartable = False                 # a stand-alone server (main()) can be restarted by an admin
        self.stop_event: asyncio.Event | None = None
        self.stopping = False                    # shutting down: no new connections, no lingering
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
        self.claims = Tries(60)                  # admin_claim attempts per address
        self.hello_fails = Tries(60)             # wrong passwords per address
        self.new_players = Tries(600)            # new player records per address
        self.dir_new = Tries(600)                # new public listings per address (a directory)
        self.dir_probed: dict[str, float] = {}   # address -> when a directory last checked a server for it
        self.probe_slots: asyncio.Semaphore | None = None
        self.linger_tasks: set = set()
        self.pending: dict[str, collections.deque] = {}   # player id -> events held while they reconnect
        self.udp = None                          # the LAN discovery endpoint (start())
        self.seq, self.shared = store.load_shared()   # the shared state (on_set): what's true for every player
        self.shared_size = {k: len(json.dumps(item.get("value"))) + 200 for k, item in self.shared.items()}
        self.shared_total = sum(self.shared_size.values())
        self.last_pose: dict[str, dict] = {}     # player id -> {"k": keyframe, "d": latest frame} (for latecomers)
        self.trim_shared()
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
    def broadcast_now(self, msg: dict, exclude: Client | None = None) -> None:
        """To every player (encoded once). Only queues: never waits for anyone."""
        data = encode(msg)
        if data is None:
            return
        key = coalesce_key(msg)
        for c in list(self.clients.values()):
            if c is not exclude:
                c.push(data, key)

    async def broadcast(self, msg: dict, exclude: Client | None = None) -> None:
        self.broadcast_now(msg, exclude)

    def refusal(self, c: Client) -> str | None:
        """Why a new connection can't be taken, or None. This machine (the host's own program) is never refused."""
        if self.stopping:
            return "the server is stopping"
        if c.local():
            return None
        ip = c.ip()
        if len(self.conns) >= MAX_CONNS:
            return "too many open"
        if sum(1 for o in self.conns if o.ip() == ip) >= MAX_CONNS_PER_IP:
            return "too many open from that address"
        waiting = [o for o in self.conns if o.id is None and not o.local()]
        if len(waiting) >= MAX_PRE_HELLO or sum(1 for o in waiting if o.ip() == ip) >= MAX_PRE_HELLO_PER_IP:
            return "too many connections that haven't joined"
        return None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        c = Client(self, reader, writer)
        why = self.refusal(c)
        if why:
            log.warning("refused a connection from %s: %s", c.addr, why)
            await c.fail("busy", "too many connections, try again in a moment")
            return
        tune_socket(writer)
        self.conns.add(c)
        log.info("connection from %s", c.addr)
        reader._limit = MAX_LINE_BEFORE_HELLO        # StreamReader's line limit: back to MAX_LINE after a hello
        cancelled = False
        try:
            while not c.closed:
                try:
                    line = await self.read_line(c)
                except asyncio.TimeoutError:
                    if c.id is None:
                        log.info("%s never said hello", c.addr)
                    else:
                        log.info("%s timed out", c.name)
                    break
                except (ValueError, asyncio.LimitOverrunError):
                    await c.fail("too_large", "message too large")
                    break
                if not line or c.closed:                 # closed while the line was read: kicked, banned, replaced
                    break                                # by a reconnect, failed. What it still sent isn't handled
                c.last_heard = c.last_rx = mono()
                msg = parse(line)
                if msg is None:
                    c.send_now({"t": "error", "code": "bad_json", "error": "bad json"})
                    continue
                try:
                    await self.dispatch(c, msg)
                except Exception as e:               # a malformed field, a bug: answer it and keep the connection
                    self.report(c, msg, e)
                if c.id is not None:
                    reader._limit = MAX_LINE
        except (ConnectionError, OSError):
            pass
        except asyncio.CancelledError:               # the event loop is going away: no goodbyes, no lingering
            cancelled = True
            raise
        finally:
            self.conns.discard(c)
            if cancelled:
                c.abort()
                await self.drop(c, announce=False)
            else:
                c.shut()
                await self.drop(c, linger=not c.said_bye)

    async def read_line(self, c: Client) -> bytes:
        """The next line. Before hello, within HELLO_WITHIN of connecting (whole lines: a stranger can't stay by
        sending a byte now and then). After, a player is gone only once nothing at all came for TIMEOUT: a long line
        still arriving, or a peer still taking what we send (a big welcome on a slow link), is alive."""
        r = c.reader
        if c.id is None:
            left = HELLO_WITHIN - (mono() - c.opened)
            if left <= 0:
                raise asyncio.TimeoutError
            return await asyncio.wait_for(r.readline(), left)
        idle = FILES_TIMEOUT if c.files else TIMEOUT
        while True:
            seen = len(getattr(r, "_buffer", b""))
            try:
                return await asyncio.wait_for(r.readline(), idle)   # (cancelled, readline keeps what it buffered)
            except asyncio.TimeoutError:
                t = mono()
                if len(getattr(r, "_buffer", b"")) > seen:
                    c.last_rx = t
                    continue
                if t - c.last_progress < idle:
                    continue
                raise

    def report(self, c: Client, msg: dict, e: Exception) -> None:
        """A message whose handling failed: logged (a traceback only for what looks like a bug, not a malformed
        field), answered with bad_request, the connection kept."""
        try:
            t = str(msg.get("t"))[:40]
            if isinstance(e, (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError)):
                log.warning("%s: bad %r message (%s: %s)", c.name, t, type(e).__name__, str(e)[:200])
            else:
                log.exception("%s: %r message failed", c.name, t)
            c.send_now({"t": "error", "code": "bad_request", "error": f"bad {t!r} message: {str(e)[:200]}",
                        "ref": clean_ref(msg.get("ref"))})
        except Exception:                            # noqa: BLE001 - reporting an error must never end the connection
            pass

    async def gc(self) -> None:
        """Deletes blobs nothing uses any more (keeping the files of world uploads under way). What's in use is worked
        out here, on the event loop (so the mod lists aren't read while they're being rewritten); the deleting runs
        in a thread."""
        keep = {h for offer in self.world_offers.values() for h in offer}
        used = self.store.blobs_in_use(keep)
        await asyncio.to_thread(self.store.gc_blobs, keep, used)

    async def drop(self, c: Client, reelect: bool = True, linger: bool = False, announce: bool = True) -> None:
        """A player is gone. linger: the connection just broke (no "bye"): they stay listed LINGER s first, so a
        quick reconnect (a Wi-Fi blip, their program restarting for an update) is a silent rejoin, not leave+join."""
        if linger and not self.stopping and c.id and self.clients.get(c.id) is c:
            async def later():
                await asyncio.sleep(LINGER)
                await self.drop(c, reelect)
            task = asyncio.get_running_loop().create_task(later())
            self.linger_tasks.add(task)
            task.add_done_callback(self.linger_tasks.discard)
            return
        if c.id and self.clients.get(c.id) is c:
            del self.clients[c.id]
            self.last_pose.pop(c.id, None)
            # Their file connections from other machines go too: those are only for a player who is on the server
            # (on_hello), so one can't stay behind, idle for FILES_TIMEOUT, holding a slot for someone who left.
            for o in list(self.conns):
                if o.files and o.id == c.id and not o.local():
                    o.shut()
            held = self.pending.pop(c.id, ())
            if not announce:
                return
            log.info("%s (%s) left", c.name, c.id)
            for _, sender, _, kind, eid, ref in held:      # events held for them: their senders learn they're gone
                s = self.clients.get(sender)
                if s is not None:
                    s.send_now({"t": "error", "code": "not_online", "error": f"{c.name} went offline", "ref": ref,
                                "to": c.id, "kind": kind, "id": eid})
            self.broadcast_now({"t": "left", "id": c.id, "name": c.name})
            if self.leader is c and reelect:
                self.leader = next(iter(self.clients.values()), None)
                if self.leader:
                    self.broadcast_now({"t": "leader", "id": self.leader.id})

    def cancel_lingers(self) -> None:
        for task in list(self.linger_tasks):
            task.cancel()

    async def shutdown(self, goodbye: str | None = None, grace: float = 1.0) -> None:
        """Every connection goes, without waiting on any of them: a goodbye chat line, at most `grace` s for it to go
        out, then each connection is cut. players.json is saved."""
        self.stopping = True
        self.cancel_lingers()
        if goodbye:
            self.broadcast_now({"t": "chat", "id": None, "name": "Server", "time": now(), "text": goodbye})
        loop = asyncio.get_running_loop()
        end = loop.time() + grace
        while loop.time() < end and any(c.busy() for c in self.conns):
            await asyncio.sleep(0.05)
        for c in list(self.conns):
            c.abort()
        self.store.flush_players(force=True)

    # ------------------------------------------------------------------ messages
    async def dispatch(self, c: Client, msg: dict) -> None:
        t = msg.get("t")
        if c.id is None and c.local() and t in ("announce_restart", "make_admin"):     # from this machine only
            if t == "announce_restart":
                seconds = max(5, min(600, small_int(msg.get("in") or 60)))
                self.announce_restart(seconds)
                c.send_now({"t": "ok"})
            else:
                c.send_now({"t": "ok", **await self.admin_op("admin_add", {"id": msg.get("who")}, "this machine")})
            return
        if c.id is None:
            if t in BEFORE_HELLO:
                await getattr(self, "on_" + t)(c, msg)
            elif t != "hello":
                c.send_now({"t": "error", "code": "hello_first", "error": "say hello first"})
            else:
                await self.on_hello(c, msg)
            return
        if c.files != (t in FILE_MESSAGES - {"ping", "pong"}) and t not in ("ping", "pong"):
            c.send_now({"t": "error", "code": "wrong_connection",
                        "error": f"{str(t)[:40]!r} doesn't belong on this connection"})
            return
        fn = getattr(self, "on_" + str(t), None) if re.fullmatch(r"[a-z_]{1,32}", str(t)) and t != "hello" else None
        if fn is None:
            c.send_now({"t": "error", "code": "unknown_type", "error": f"unknown message type {str(t)[:40]!r}"})
            return
        await fn(c, msg)

    async def on_bye(self, c: Client, msg: dict) -> None:
        c.said_bye = True                            # leaving on purpose: told at once (see drop)

    async def on_hello(self, c: Client, msg: dict) -> None:
        if msg.get("protocol") != PROTOCOL:
            await c.fail("version_mismatch", f"version mismatch: server speaks protocol {PROTOCOL}", protocol=PROTOCOL,
                         server_version=VERSION)
            return
        ip, local = c.ip(), c.local()
        if self.password:
            if not local and self.hello_fails.count(ip) >= HELLO_FAILS:      # someone guessing the password
                # (no "password" in the text: companions, 0.9.37 included, take a fatal error that says "password"
                # as one retrying can't fix and stop reconnecting for good; this one passes in a minute. The check
                # comes before the compare on purpose: otherwise guessing would go on, only with this answer.)
                await c.fail("rate_limited", "too many failed joins from your address: wait a minute and try again")
                return
            given = str(msg.get("password") or "").encode("utf-8")    # bytes: compare_digest refuses non-ASCII str
            if not secrets.compare_digest(given, self.password.encode("utf-8")):
                self.hello_fails.add(ip)
                await c.fail("wrong_password", "wrong server password")
                return
        key = str(msg.get("key", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
            await c.fail("bad_key", "missing or malformed player key")
            return
        name = str(msg.get("name", "")).strip()[:40] or "Player"
        pid = self.store.find_player(key)
        ban = self.store.settings["bans"].get(pid) if pid else None
        if ban:
            await c.fail("banned", "you're banned from this server" + (f" ({ban['reason']})" if ban.get("reason") else ""),
                         banned=True)
            return
        version = str(msg.get("version") or "")[:20]
        files = bool(msg.get("files"))
        old = self.clients.get(pid) if pid else None
        if not files and old is None and len(self.clients) >= self.max_players:
            await c.fail("full", "server is full")
            return
        if files and old is None and not local:
            # A file connection serves a player who is on the server (the companion, 0.9.37's too, opens one only after
            # its welcome; one opened in the linger after a broken connection still counts). Otherwise a stranger on a
            # server without a password could hold every slot with file connections, which may sit idle for
            # FILES_TIMEOUT, without ever joining. Refused before a player record is made, like a full server.
            await c.fail("not_joined", "join the server first: file connections are only for players on it")
            return
        if pid is None:                              # a new player: only now, once the join is allowed, a record
            if not local and self.new_players.count(ip) >= NEW_PLAYERS_PER_IP:
                await c.fail("rate_limited", "too many new players from your address: wait a few minutes")
                return
            pid = self.store.register(key, name)
            if not local:
                self.new_players.add(ip)
        elif not files:
            self.store.seen(pid, name)
        self.hello_fails.clear(ip)
        if files:                                    # the player's file transfer connection: not a second player
            c.id, c.name, c.files, c.version = pid, name, True, version
            # At most MAX_FILE_CONNS per player: past that, the ones that have been quiet longest go (a transfer whose
            # connection died without a word, which the server would otherwise keep for FILES_TIMEOUT).
            mine = sorted((o for o in self.conns if o.files and o.id == pid and o is not c and not o.closed),
                          key=lambda o: max(o.last_rx, o.last_progress))
            for o in mine[:max(0, len(mine) + 1 - MAX_FILE_CONNS)]:
                log.info("%s opened another file connection: closing a quiet one from %s", name, o.addr)
                o.shut()
            c.send_now({"t": "files_ok"})
            return
        c.id, c.name, c.joined, c.version = pid, name, now(), version
        # The welcome is built and queued in the same step that adds the player (no await in between), so it's the
        # first line on the new connection and nothing said meanwhile is missed or comes before it.
        if old:
            # Same player reconnecting (a Wi-Fi blip): swap the session in place. Nobody is told they left, so their
            # Zoi stays in everyone's world; the others only get "rejoined" (older companions ignore it). Nothing here
            # waits on the old connection: if it's dead, its own timers cut it.
            c.state, c.joined = old.state, old.joined
            leader = self.leader
            self.clients[c.id] = c
            if self.leader is old:
                self.leader = c
            lines = self.welcome_lines(c)
            if lines is None:
                self.clients[c.id], self.leader = old, leader
                await c.fail("bad_request", "the server couldn't put your welcome together")
                return
            for data, key in lines:
                c.push(data, key)
            old.send_now({"t": "kicked", "reason": "you connected from somewhere else"})
            old.shut()
            log.info("%s (%s) reconnected from %s", c.name, c.id, c.addr)
            self.send_poses(c)
            for held in self.pending.pop(c.id, ()):     # events sent to them while they were away
                c.push(held[2])
            self.broadcast_now({"t": "rejoined", "player": c.public()}, exclude=c)
            return
        is_host = self.hosted and bool(msg.get("host")) and local
        before, host_before = self.leader, self.host_id
        self.clients[c.id] = c
        self.pending.pop(c.id, None)
        if is_host:
            self.host_id = c.id
        if self.leader is None or is_host:
            self.leader = c
        lines = self.welcome_lines(c)
        if lines is None:
            del self.clients[c.id]
            self.leader, self.host_id = before, host_before
            await c.fail("bad_request", "the server couldn't put your welcome together")
            return
        for data, key in lines:
            c.push(data, key)
        log.info("%s (%s) joined from %s%s", c.name, c.id, c.addr, " - the host" if is_host else "")
        self.send_poses(c)
        self.broadcast_now({"t": "joined", "player": c.public()}, exclude=c)
        if before is not None and before is not self.leader:        # the clock changed hands
            self.broadcast_now({"t": "leader", "id": self.leader.id}, exclude=c)
        await self.send_modset(exclude=c)                # it's in the welcome already

    def welcome_lines(self, c: Client) -> list[tuple] | None:
        """c's welcome, ready to send: [(wire line, coalesce key)]. One line, unless that's past what a companion reads
        in one line (WELCOME_ROOM, under MAX_LINE: many players online, each Zoi up to MAX_PROFILE): then the welcome
        leaves out the players' Zois and states and the mod set, which follow it at once as the messages that would
        bring their changes ("profile", "state", "modset": companions, 0.9.37's too, take them). None if it can't be
        put together, or still wouldn't fit (logged: then the hello is undone, not half done)."""
        try:
            msg = self.welcome(c)
            data = encode(msg)
            if data is None or len(data) <= WELCOME_ROOM:
                return None if data is None else [(data, None)]
            after = [{"t": "modset", **msg["modset"]}]
            for p in msg["players"]:
                after += [{"t": k, "id": p["id"], k: p[k]} for k in ("profile", "state") if p.get(k) is not None]
            slim = {**msg, "modset": None, "players": [{**p, "profile": None, "state": None} for p in msg["players"]]}
            lines = [(encode(m), coalesce_key(m)) for m in [slim] + after]
            if any(line is None for line, _ in lines):
                return None
            size = max(len(line) for line, _ in lines)
            if size > WELCOME_ROOM:
                log.error("%s can't join: even without the Zois and the mod set, a line of their welcome would be %d "
                          "MB, more than a companion reads in one line (%d MB)", c.name, size >> 20, MAX_LINE >> 20)
                return None
            log.warning("%s's welcome would be %d MB: the Zois, states and mod set follow it on their own lines",
                        c.name, len(data) >> 20)
            return lines
        except Exception:                            # noqa: BLE001 - a damaged file on disk, a bug
            log.exception("couldn't put %s's welcome together", c.name)
            return None

    def houses(self) -> list[dict]:
        """The shared houses listed (welcome, house_list): the players' online first, then the newest, MAX_HOUSES at
        most (each new player may share one: the list would grow without end)."""
        houses = sorted(self.store.list_houses(), key=lambda h: (h["owner"] not in self.clients, -h["updated"]))
        return houses[:MAX_HOUSES]

    def welcome(self, c: Client, modset: dict | None = None) -> dict:
        return {
            "t": "welcome", "id": c.id, "server_version": VERSION, "protocol": PROTOCOL, "motd": self.motd,
            "leader": self.leader.id if self.leader else None, "world": self.world, "host": self.host_id,
            "players": [o.public() for o in self.clients.values() if o is not c],
            "houses": self.houses(), "modset": modset or self.modset(), "world_save": self.store.world_save(),
            "server": self.summary(), "admin": self.is_admin(c),
            "shared": self.shared, "seq": self.seq, "epoch": self.store.shared_epoch,
        }

    def send_poses(self, c: Client) -> None:
        """A newcomer gets everyone's latest skeleton frames at once (they're only sent again when they change)."""
        for pid, frames in list(self.last_pose.items()):
            if pid != c.id and pid in self.clients:
                for kind in ("k", "d"):
                    if frames.get(kind):
                        c.send_now({"t": "event", "from": pid, "kind": "pose", "data": {kind: frames[kind]}})

    async def on_ping(self, c: Client, msg: dict) -> None:
        c.send_now({"t": "pong", "time": now(), "echo": msg.get("echo")})

    async def on_pong(self, c: Client, msg: dict) -> None:
        pass

    def too_big(self, c: Client, what: str, value, limit: int) -> bool:
        """value (a player's state, Zoi or world, which every welcome carries) is past `limit` bytes of JSON: logged
        once per connection, and the caller keeps what it had."""
        size = json_size(value)
        if size <= limit:
            return False
        if what not in c.warned:
            c.warned.add(what)
            log.warning("%s sent a %s of %d KB (at most %d KB): ignored", c.name, what, size >> 10, limit >> 10)
        return True

    async def on_state(self, c: Client, msg: dict) -> None:
        """Live Zoi state (position, facing, animation, current interaction, site...). Sent ~5-10x/s."""
        if self.too_big(c, "state", msg.get("state"), MAX_STATE):
            return
        c.state = msg.get("state")
        self.broadcast_now({"t": "state", "id": c.id, "state": c.state}, exclude=c)

    async def on_profile(self, c: Client, msg: dict) -> None:
        """The player's Zoi: name, appearance, traits. Stored so people who join later can build the Zoi."""
        if self.too_big(c, "Zoi", msg.get("profile"), MAX_PROFILE):
            c.send_now({"t": "error", "code": "too_big", "error": "your Zoi is too big to share: friends can't see it"})
            return
        self.store.set_profile(c.id, msg.get("profile"))
        self.broadcast_now({"t": "profile", "id": c.id, "profile": msg.get("profile")}, exclude=c)

    async def on_world(self, c: Client, msg: dict) -> None:
        """Clock, weather and season. Only the leader's world is shared, everyone else follows it."""
        if c is not self.leader or self.too_big(c, "world", msg.get("world"), MAX_WORLD):
            return
        self.world = msg.get("world")
        self.broadcast_now({"t": "world", "world": self.world}, exclude=c)

    async def on_take_leader(self, c: Client, msg: dict) -> None:
        if self.hosted and self.host_id in self.clients and c.id != self.host_id:     # the host keeps their clock
            c.send_now({"t": "leader", "id": self.leader.id if self.leader else None})
            return
        self.leader = c
        self.broadcast_now({"t": "leader", "id": c.id})

    async def on_chat(self, c: Client, msg: dict) -> None:
        text = str(msg.get("text", "")).strip()[:500]
        if text:
            log.info("[chat] %s: %s", c.name, text)
            self.broadcast_now({"t": "chat", "id": c.id, "name": c.name, "text": text, "time": now()})

    async def on_event(self, c: Client, msg: dict) -> None:
        """Generic relay: {"t":"event","kind":"...","to":<player id or omitted for everyone>,"data":{...}}.
        Interactions, visit invites, gifts... anything new the companions agree on. One sent to a player who is
        reconnecting (their connection broke a moment ago) is held and delivered when they're back; if they don't
        come back, its sender gets not_online then."""
        out = {"t": "event", "from": c.id, "kind": str(msg.get("kind", ""))[:64], "data": msg.get("data")}
        if out["kind"] == "pose" and isinstance(out["data"], dict) and self.clients.get(c.id) is c:
            frames = self.last_pose.setdefault(c.id, {})    # (only a player on the server: drop() clears it)
            for kind in ("k", "d"):
                if isinstance(out["data"].get(kind), str):
                    frames[kind] = out["data"][kind]
        to = msg.get("to")
        if not to:
            self.broadcast_now(out, exclude=c)
            return
        ref, target = clean_ref(msg.get("ref")), self.clients.get(str(to))
        eid = out["data"].get("id") if isinstance(out["data"], dict) else None
        eid = eid if isinstance(eid, (str, int)) and len(str(eid)) <= MAX_REF else None
        if target is not None and not target.closed:
            target.send_now(out)
        elif target is not None and out["kind"] != "pose" and self.hold(target, c, out, ref, eid):
            pass
        else:
            c.send_now({"t": "error", "code": "not_online", "error": f"{str(to)[:40]} is not online", "ref": ref,
                        "to": str(to)[:40], "kind": out["kind"], "id": eid,
                        **({"lingering": True} if target is not None else {})})

    def hold(self, target: Client, sender: Client, out: dict, ref, eid) -> bool:
        """Keeps an event for a player who is reconnecting, if there's room (PENDING_MAX, PENDING_BYTES)."""
        data = encode(out)
        if data is None:
            return False
        q = self.pending.setdefault(target.id, collections.deque())
        if len(q) >= PENDING_MAX or sum(len(h[2]) for h in q) + len(data) > PENDING_BYTES:
            return False
        q.append((now(), sender.id, data, out["kind"], eid, ref))
        return True

    async def on_set(self, c: Client, msg: dict) -> None:
        """{"t":"set","key","value","base"?,"ref"?}: the shared state changes. The new value gets the next version and
        goes to everyone, the sender too (that's its confirmation: every player applies the same values in the same
        order). With "base" (the version the sender's change was made on) a change made on an older version is refused
        ("set_refused" with the current one): nobody overwrites what they haven't seen. value null removes the key.
        It's saved before anything changes: if it can't be, the sender gets storage_failed and nothing changed."""
        key, ref = str(msg.get("key", "")), clean_ref(msg.get("ref"))
        if not SHARED_KEY.fullmatch(key):
            c.send_now({"t": "error", "code": "bad_key", "error": f"bad shared key {key[:100]!r}", "key": key[:100],
                        "ref": ref})
            return
        value, base = msg.get("value"), msg.get("base")
        if base is not None and type(base) is not int:   # (no int() of a string: see small_int)
            c.send_now({"t": "error", "code": "bad_request", "error": "base must be a version number", "key": key,
                        "ref": ref})
            return
        cur = self.shared.get(key)
        if base is not None and base != int((cur or {}).get("v") or 0):
            c.send_now({"t": "set_refused", "key": key, "ref": ref, "current": cur})
            return
        size = 0
        if value is not None:
            size = len(json.dumps(value))
            if size > MAX_SHARED:
                c.send_now({"t": "error", "code": "too_big", "error": f"{key} is too big", "key": key, "ref": ref})
                return
            size += 200
            try:
                room = self.make_room(key, size - self.shared_size.get(key, 0), cur is None, value)
            except OSError as e:
                log.error("couldn't save the shared state while making room for %s (%s): refused", key, e)
                c.send_now({"t": "error", "code": "storage_failed", "key": key, "ref": ref,
                            "error": "the server couldn't save that: try again"})
                return
            if not room:
                log.warning("shared state full: %s by %s refused (%d MB in %d keys)", key, c.name,
                            self.shared_total >> 20, len(self.shared))
                c.send_now({"t": "error", "code": "too_big", "key": key, "ref": ref,
                            "error": "the server's shared state is full: old houses must be cleared first"})
                return
        try:
            self.commit_shared(key, value, size, c, ref)
        except (OSError, ValueError, TypeError) as e:
            log.error("couldn't save shared %s (%s): refused, nothing changed", key, e)
            c.send_now({"t": "error", "code": "storage_failed", "key": key, "ref": ref,
                        "error": "the server couldn't save that: try again"})

    def commit_shared(self, key: str, value, size: int = 0, c: Client | None = None, ref=None) -> None:
        """The change, saved first (raises if it can't be: then nothing changed), then made and sent to everyone."""
        v = self.seq + 1
        item = None if value is None else {"key": key, "value": value, "v": v, "by": c.id if c else None,
                                           "by_name": c.name if c else "Server", "at": now()}
        self.store.save_shared(v, item, key)
        log.info("shared %s v%d by %s%s", key, v, c.name if c else "the server", " (removed)" if item is None else "")
        self.apply_shared(key, item, v, size, c, ref)

    def apply_shared(self, key: str, item: dict | None, v: int, size: int = 0, c: Client | None = None,
                     ref=None) -> None:
        """A change that's on disk already (commit_shared, remove_shared): made, and sent to everyone."""
        self.seq = v
        self.shared_total -= self.shared_size.pop(key, 0)
        if item is None:
            self.shared.pop(key, None)
        else:
            self.shared[key] = item
            self.shared_size[key] = size or len(json.dumps(item["value"])) + 200
            self.shared_total += self.shared_size[key]
        self.broadcast_now({"t": "shared", "key": key, "item": item, "v": v, "ref_by": c.id if c else None,
                            "ref": ref})

    def remove_shared(self, keys: list) -> int:
        """These keys removed, as sets with value null would (everyone gets item null), with the sequence saved and the
        folder flushed once for all of them: a whole lot's object keys one by one would flush the disk twice per key,
        holding everyone up. Every file goes first, then the flush, and only then is anyone told: a removal players
        saw is never undone by a power cut. Raises OSError if a key's file can't be removed: the keys before it are
        gone (and said), it and the rest stay. (The caller says it in the log once.)"""
        keys = [k for k in dict.fromkeys(keys) if k in self.shared]
        if not keys:
            return 0
        self.store.save_seq(self.seq + len(keys))
        gone = []
        try:
            for k in keys:
                self.store.drop_shared(k)
                gone.append(k)
        finally:
            if gone:
                self.store.sync_shared()
                for k in gone:
                    self.apply_shared(k, None, self.seq + 1)
        return len(keys)

    @staticmethod
    def lot_of(key: str) -> str | None:
        """The lot of a house:<lot>:... or obj:<lot>:... key (client/housesync.py, client/objsync.py); None for any
        other key (family)."""
        parts = key.split(":", 2)
        return parts[1] if len(parts) == 3 and parts[0] in ("house", "obj") and parts[1] else None

    @staticmethod
    def home_of(family) -> str | None:
        """The home lot (home_site) of a "family" value, if it has one."""
        site = family.get("home_site") if isinstance(family, dict) else None
        return None if site is None or site == "" else str(site)

    def home_lot(self) -> str | None:
        """The family's home lot (the stored "family" key's home_site), if there's one."""
        return self.home_of((self.shared.get("family") or {}).get("value"))

    def lots_by_age(self, keep: set) -> tuple[list[str], dict[str, list[str]], dict[str, float]]:
        """The lots that have shared keys (house:<lot>:*, obj:<lot>:*), but those in keep, the one changed longest ago
        first; each lot's keys; and when each lot last changed (its newest key's "at")."""
        lots: dict[str, list[str]] = {}
        for k in self.shared:
            lot = self.lot_of(k)
            if lot is not None and lot not in keep:
                lots.setdefault(lot, []).append(k)

        def changed(k: str) -> float:
            try:
                at = float(self.shared[k].get("at") or 0)
            except (TypeError, ValueError, OverflowError):
                return 0.0
            return at if abs(at) < 1e12 else 0.0     # (also NaN: a damaged "at" on disk counts as long ago)
        last = {lot: max(changed(k) for k in ks) for lot, ks in lots.items()}
        return sorted(lots, key=lambda lot: last[lot]), lots, last

    def clear_lot(self, lot: str, keys: list, last: float, why: str) -> None:
        """Removes a whole lot's shared keys (remove_shared: everyone gets item null), with one warning in the log."""
        try:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last))
        except (OverflowError, OSError, ValueError):                 # (a damaged "at" on disk)
            when = "?"
        log.warning("shared state full: lot %s cleared %s (%d keys, %d KB, last changed %s)", lot, why, len(keys),
                    sum(self.shared_size.get(k, 0) for k in keys) >> 10, when)
        self.remove_shared(keys)

    def make_room(self, key: str, grows: int, new: bool, value=None) -> bool:
        """Room for a set of key (to value) that makes the shared state grow by `grows` bytes (and adds a key, if
        `new`). Nothing else ever removes shared keys: every lot the couple has lived on keeps its house:<lot>:* (an
        exact house is up to 4 MB) and obj:<lot>:* keys, so the state only grows until it's full, and then the home's
        own changes would be refused for good. So, when full, old lots go: whole lots, the one changed longest ago
        first, as removals everyone gets (item null), until the set fits. Never the lot of the key being set, nor the
        family's home, nor a lot changed in the last LOT_IN_USE seconds (two lots in use never push each other out),
        nor a key that isn't a lot's (family). A "family" set that moves the home (moving in with the partner) keeps
        the new home - their new home's house must not be what makes room for the move - and the old one goes only
        if nothing else is enough (after this set it's no longer the home). Nothing goes unless that makes the set
        fit. False: it doesn't fit (too_big). Raises OSError if a removal couldn't be saved (the removals before it
        stand)."""
        def fits(total: int, count: int) -> bool:
            return (grows <= 0 or total + grows <= MAX_SHARED_TOTAL) and (not new or count < MAX_SHARED_KEYS)

        if fits(self.shared_total, len(self.shared)):
            return True
        home = self.home_lot()
        moved = key == "family" and self.home_of(value) != home
        keep = {self.lot_of(key), self.home_of(value) if key == "family" else None} | (set() if moved else {home})
        order, lots, last = self.lots_by_age(keep)
        t = now()
        old = sorted((lot for lot in order if t - last[lot] >= LOT_IN_USE), key=lambda lot: lot == home)
        freed = sum(self.shared_size.get(k, 0) for lot in old for k in lots[lot])
        if not fits(self.shared_total - freed, len(self.shared) - sum(len(lots[lot]) for lot in old)):
            return False
        for lot in old:
            if fits(self.shared_total, len(self.shared)):
                break
            self.clear_lot(lot, lots[lot], last[lot], f"to make room for {key}")
        return fits(self.shared_total, len(self.shared))

    def trim_shared(self) -> None:
        """At start: a shared state over the cap (MAX_SHARED_TOTAL, MAX_SHARED_KEYS) is brought under it before anyone
        joins. Only a data folder from a server without the cap can be over it (on_set never lets it grow past), and
        there nothing would ever trim it: every welcome carries the whole state in one line, so past what a companion
        reads in one line (32 MB) nobody could join - not even an admin, to clear it - and so no set would ever come
        to make room. make_room's rules: whole lots, the one changed longest ago first, never the family's home nor a
        key that isn't a lot's; lots changed in the last LOT_IN_USE seconds (in use before a restart) only if the others
        weren't enough, as nobody is on yet. Nobody is connected, so the removals are only saved. Never raises: a
        removal that can't be saved stops the trimming (what's left is served; the next start tries again)."""
        def over() -> bool:
            return self.shared_total > MAX_SHARED_TOTAL or len(self.shared) > MAX_SHARED_KEYS

        if not over():
            return
        log.warning("the shared state is over the cap (%d MB in %d keys; at most %d MB in %d keys): clearing old lots",
                    self.shared_total >> 20, len(self.shared), MAX_SHARED_TOTAL >> 20, MAX_SHARED_KEYS)
        order, lots, last = self.lots_by_age({self.home_lot()})
        t = now()
        order = [lot for lot in order if t - last[lot] >= LOT_IN_USE] + \
                [lot for lot in order if t - last[lot] < LOT_IN_USE]
        for lot in order:
            if not over():
                break
            try:
                self.clear_lot(lot, lots[lot], last[lot], "at start (over the cap)")
            except (OSError, ValueError, TypeError) as e:
                log.error("couldn't clear lot %s from the shared state (%s): it stays for now", lot, e)
                return
        if over():
            log.warning("the shared state is still over the cap (%d MB in %d keys): what's left is the family's home "
                        "and keys that aren't a lot's", self.shared_total >> 20, len(self.shared))

    async def on_shared_get(self, c: Client, msg: dict) -> None:
        c.send_now({"t": "shared_all", "shared": self.shared, "seq": self.seq, "epoch": self.store.shared_epoch})

    async def on_house_put(self, c: Client, msg: dict) -> None:
        house = msg.get("house")
        if house is not None and not isinstance(house, dict):
            raise ValueError("house must be an object")
        meta = self.store.put_house(c.id, house)
        log.info("%s uploaded their house (%s)", c.name, meta.get("name") or "unnamed")
        self.broadcast_now({"t": "house_updated", "meta": meta})

    async def on_house_get(self, c: Client, msg: dict) -> None:
        owner = str(msg.get("owner", ""))
        d = self.store.get_house(owner)              # None for anything that isn't a player id
        c.send_now({"t": "house", "owner": owner[:40], "meta": d and d["meta"], "house": d and d.get("house")})

    async def on_house_list(self, c: Client, msg: dict) -> None:
        c.send_now({"t": "house_list", "houses": self.houses()})

    async def on_who(self, c: Client, msg: dict) -> None:
        c.send_now({"t": "who", "players": [o.public() for o in self.clients.values()]})

    # ------------------------------------------------------------------ mods
    def modset(self) -> dict:
        """The mods everyone should have. Hosted: the host's. Otherwise: everyone's together (for a mod two players
        have in different versions, the newest build). Each mod lists who has it."""
        key = (self.host_id, self.store.mods_changed)
        if getattr(self, "_modset", (None, None))[0] != key:
            self._modset = (key, self._compute_modset())
        return self._modset[1]

    def _compute_modset(self) -> dict:
        """At most MAX_MODSET (it rides in every welcome; the stored lists may be from before MAX_MODS, and pooled
        there's one per player who ever joined): past it, the host's last mods, or the mods of the lists updated
        longest ago, are left out."""
        lists = self.store.all_mods()
        if self.hosted:
            d = lists.get(self.host_id or "", {})
            mods = [{**m, "by": [d.get("name", "")]} for m in d.get("mods", [])]
            return {"source": "host", "host": self.host_id, "mods": self.fit_modset(mods)}
        merged: dict[str, dict] = {}
        latest: dict[str, float] = {}                # mod id -> when the newest list holding it was updated
        for pid, d in sorted(lists.items(), key=lambda kv: kv[1].get("updated", 0)):
            for m in d.get("mods", []):
                same, name = merged.get(m["id"]), d.get("name", pid)
                if same and content(same) == content(m):            # the same mod, whatever each PC's manifest says
                    same["by"].append(name)
                elif not same or m.get("built", "") >= same.get("built", ""):     # an older build never wins
                    merged[m["id"]] = {**m, "by": [name]}
                latest[m["id"]] = d.get("updated", 0)
        mods = sorted(merged.values(), key=lambda m: m["name"].lower())
        return {"source": "everyone", "host": None, "mods": self.fit_modset(mods, lambda i: -latest[mods[i]["id"]])}

    @staticmethod
    def fit_modset(mods: list, rank=None) -> list:
        kept = fit_mods(mods, MAX_MODSET, rank)
        if len(kept) < len(mods):
            log.warning("the mod set is too big: %d of %d mods kept (the rest aren't offered to anyone)",
                        len(kept), len(mods))
        return kept

    async def send_modset(self, modset: dict | None = None, exclude: Client | None = None) -> None:
        modset = modset or self.modset()
        text = json.dumps(modset, sort_keys=True)
        if text != self.modset_sent:
            self.modset_sent = text
            self.broadcast_now({"t": "modset", **modset}, exclude=exclude)

    def shared_blobs(self) -> set[str]:
        return {f["h"] for m in self.modset()["mods"] for f in m["files"]}

    async def on_mods(self, c: Client, msg: dict) -> None:
        """A player's enabled mods: [{id, name, version, path, files: [{p, h, s}]}]. At most MAX_MODS of them (the
        mod set rides in every welcome): past it, the ones at the end are left out, and the player told."""
        mods = clean_mods(msg.get("mods"))
        kept = fit_mods(mods, MAX_MODS)
        if len(kept) < len(mods):
            log.warning("%s's mod list is too big: %d of %d mods kept", c.name, len(kept), len(mods))
            c.send_now({"t": "error", "code": "too_big",
                        "error": f"your mod list is too big for the server: only {len(kept)} of {len(mods)} mods shared"})
        self.store.set_mods(c.id, c.name, kept)
        log.info("%s has %d mod(s)", c.name, len(kept))
        await self.send_modset()

    def own_files(self, c: Client) -> dict[str, int]:
        """{sha256: size} of the files c may upload: those of its own mods (its stored list) and of its world upload."""
        if c.mine[0] != self.store.mods_changed:
            c.mine = (self.store.mods_changed, {f["h"]: f["s"] for m in self.store.all_mods().get(c.id, {}).get("mods", [])
                                                for f in m["files"]})
        return {**c.mine[1], **self.world_offers.get(c.id, {})}

    async def missing_blobs(self, want: list, sizes: dict) -> list:
        """The files of want (sha256s) the server doesn't have whole. One stored with another size than sizes lists
        for it, whose content isn't its sha256 either, was cut short (a power cut soon after its upload, before uploads
        were flushed to disk): it's deleted, so it's uploaded again, instead of being served damaged for good."""
        gone = set()
        for h in dict.fromkeys(want):
            path = self.store.blob(h)
            try:
                st = path.stat()
            except OSError:
                gone.add(h)
                continue
            if h not in sizes or st.st_size == sizes[h]:
                continue
            try:
                intact = await asyncio.to_thread(sha256_file, path) == h   # (the list could be the wrong one)
            except OSError:                          # gone, or locked right now: asked for again, never deleted
                gone.add(h)                          # (an upload replaces it with a checked copy)
                continue
            if intact:
                continue
            gone.add(h)
            try:
                now_st = path.stat()                 # (not one uploaded again while it was being read)
            except OSError:
                continue
            if (now_st.st_size, now_st.st_mtime_ns, now_st.st_ino) == (st.st_size, st.st_mtime_ns, st.st_ino):
                log.warning("blobs/%s is damaged (%d bytes, %d expected): deleted, to be uploaded again", h[:12],
                            st.st_size, sizes[h])
                try:
                    _unlink(path)
                except OSError as e:
                    log.warning("couldn't delete the damaged %s: %s", h[:12], e)
            else:
                gone.discard(h)
        return [h for h in want if h in gone]

    async def on_blob_check(self, c: Client, msg: dict) -> None:
        """Which of these files (sha256) the server doesn't have yet (or has damaged): the client uploads those."""
        raw = msg.get("h")
        want = [h for h in (raw if isinstance(raw, list) else [])[:20000] if isinstance(h, str) and SHA.fullmatch(h)]
        c.send_now({"t": "blob_missing", "h": await self.missing_blobs(want, self.own_files(c))})

    async def on_blob_put(self, c: Client, msg: dict) -> None:
        """Upload a file of one of your own mods, in order: {h, o (offset), d (base64), end}."""
        h, offset = str(msg.get("h", "")), msg.get("o")
        mine = self.own_files(c)
        if h not in mine:
            c.send_now({"t": "blob_bad", "h": h[:64], "error": "not a file of your mods or your world upload"})
            return
        part = self.store.blob(h).with_name(f"{h}.{c.id}.part")
        size = part.stat().st_size if part.exists() else 0
        if type(offset) is not int or offset != size:
            c.send_now({"t": "blob_bad", "h": h, "error": f"expected offset {size}", "o": size})
            return
        d = msg.get("d", "")
        try:
            data = base64.b64decode(d, validate=True) if isinstance(d, str) and len(d) <= MAX_PIECE * 4 // 3 + 4 else None
        except ValueError:
            data = None
        if data is None or size + len(data) > mine[h]:
            part.unlink(missing_ok=True)
            c.send_now({"t": "blob_bad", "h": h, "o": 0,
                        "error": "bad piece (not base64, too big or past the end of the file): start again"})
            return
        with part.open("ab") as f:
            f.write(data)
        if not msg.get("end"):
            return
        if part.stat().st_size != mine[h] or await asyncio.to_thread(sha256_file, part) != h:   # off the event loop
            part.unlink()
            c.send_now({"t": "blob_bad", "h": h, "error": "the file didn't arrive intact"})
            return
        # On disk for good before blob_ok: from then on the uploader never sends it again (blob_check). Its content
        # first, then its name (the folder): ext4 can otherwise lose the data of a renamed file to a power cut.
        await asyncio.to_thread(fsync_file, part)
        blob = self.store.blob(h)
        _retry(os.replace, part, blob)
        Store._sync_folder(blob)
        c.send_now({"t": "blob_ok", "h": h})

    async def on_blob_get(self, c: Client, msg: dict) -> None:
        """Download a piece of a file that's part of the shared mods: {h, o} -> blob {h, o, d, end}."""
        h, offset = str(msg.get("h", "")), msg.get("o") or 0
        if type(offset) is not int or offset < 0:        # (no int() of a string: see small_int)
            raise ValueError("the offset must be a whole number, not negative")
        if not SHA.fullmatch(h) or (h not in self.store.world_blobs() and h not in self.shared_blobs()):
            c.send_now({"t": "blob", "h": h[:64], "error": "not shared"})
            return
        path = self.store.blob(h)
        if not path.exists():
            c.send_now({"t": "blob", "h": h, "error": "not uploaded yet"})
            return
        with path.open("rb") as f:
            f.seek(offset)
            data = f.read(CHUNK)
        c.send_now({"t": "blob", "h": h, "o": offset, "d": base64.b64encode(data).decode(),
                    "end": offset + len(data) >= path.stat().st_size})

    # ------------------------------------------------------------------ the shared world
    async def on_world_save_offer(self, c: Client, msg: dict) -> None:
        """About to upload the world: {files: [{p, h, s}]}. Answers which files the server still needs."""
        files = clean_files(msg.get("files"), 2000)
        if not any(f["p"].endswith(".usvx") for f in files):
            c.send_now({"t": "world_save_offer", "error": "no save file in the upload"})
            return
        offer = self.world_offers[c.id] = {f["h"]: f["s"] for f in files}
        need = sorted(await self.missing_blobs(list(offer), offer))
        c.send_now({"t": "world_save_offer", "need": need})

    async def on_world_save_put(self, c: Client, msg: dict) -> None:
        """The uploaded world becomes the new version, if it was based on the current one:
        {files, base (the version the player had loaded), name}."""
        files = clean_files(msg.get("files"), 2000)
        current = self.store.world_save() or {}
        version = current.get("version", 0)
        if msg.get("base") != version:
            c.send_now({"t": "world_save_put", "ok": False, "version": version,
                        "error": f"the world changed since you loaded it (it's at version {version} now)"})
            return
        missing = [f["p"] for f in files if not self.store.blob(f["h"]).exists()]
        if missing or not files:
            c.send_now({"t": "world_save_put", "ok": False, "version": version,
                        "error": f"files not uploaded yet: {', '.join(missing[:5])}"})
            return
        meta = {"version": version + 1, "by": c.name, "pid": c.id, "time": now(), "files": files,
                "name": str(msg.get("name", ""))[:100] or current.get("name", ""), "size": sum(f["s"] for f in files)}
        self.store.set_world_save(meta)
        self.world_offers.pop(c.id, None)
        await self.gc()
        log.info("%s saved the shared world: version %d (%d MB)", c.name, meta["version"], meta["size"] >> 20)
        c.send_now({"t": "world_save_put", "ok": True, "version": meta["version"]})
        self.broadcast_now({"t": "world_save", **meta}, exclude=c)

    def announce_restart(self, seconds: int) -> None:
        """Before the server restarts: everyone saves their game (the world keeper also uploads it)."""
        keeper = self.leader.id if self.leader else None
        log.info("restart in %d s: asking everyone to save (world keeper %s)", seconds, keeper)
        self.broadcast_now({"t": "restart_soon", "in": seconds, "keeper": keeper})
        self.broadcast_now({"t": "chat", "id": None, "name": "Server", "time": now(),
                            "text": f"The server restarts in {seconds} seconds: your game is saved first."})

    # ------------------------------------------------------------------ the servers page: info before joining
    def info(self) -> dict:
        """Everything the servers page shows about this server before joining it, and what its mods check needs."""
        ms = self.modset()
        return {"t": "info", **self.summary(), "protocol": PROTOCOL, "players": len(self.clients),
                "names": [c.name for c in self.clients.values()][:16],
                "host": self.clients[self.host_id].name if self.host_id in self.clients else None,
                "world": world_summary(self.store.world_save()), "uptime": int(mono() - self.started),
                "mods": {"source": ms["source"],
                         "list": [{"id": m["id"], "name": m["name"], "version": m.get("version", ""),
                                   "built": m.get("built", ""), "size": m.get("size", 0), "fp": mod_fp(m)}
                                  for m in ms["mods"]]}}

    async def on_info(self, c: Client, msg: dict) -> None:
        c.send_now(self.info())

    def discover_reply(self) -> dict:
        """The answer to a LAN discovery datagram (small: one UDP packet)."""
        s = self.store.settings
        return {"t": "discover", "name": self.name(), "port": self.port, "version": VERSION, "protocol": PROTOCOL,
                "id": s["id"], "password": bool(s["password"]), "players": len(self.clients),
                "max_players": self.max_players, "hosted": self.hosted}

    async def hold_udp(self) -> None:
        """Keeps the LAN discovery endpoint open for as long as the server's background tasks run (every way of
        stopping a server cancels them). Their end is the server's end: no lingering players after it."""
        try:
            await asyncio.Event().wait()
        finally:
            self.stopping = True
            self.cancel_lingers()
            self.store.flush_players(force=True)     # ("last seen" times not saved yet)
            if self.udp:
                self.udp.close()
                self.udp = None

    # ------------------------------------------------------------------ admin console
    async def on_admin(self, c: Client, msg: dict) -> None:
        """{op, ref, ...}: answered with {t: admin, ref, op, ok, error?, ...}. Only for the server's admins."""
        op = str(msg.get("op", ""))[:40]
        if not self.is_admin(c):
            out = {"ok": False, "error": "you're not an admin of this server"}
        else:
            try:
                out = await self.admin_op(op, msg, c.name, c)
            except (ValueError, TypeError, KeyError) as e:
                out = {"ok": False, "error": str(e)}
        c.send_now({"t": "admin", "ref": clean_ref(msg.get("ref")), "op": op, **out})

    async def on_admin_claim(self, c: Client, msg: dict) -> None:
        """Become an admin with the server's admin code (shown to admins, and by --admin-code on the server's PC)."""
        ref = clean_ref(msg.get("ref"))
        if self.claims.count(c.ip()) >= CLAIM_TRIES:     # someone guessing the code
            c.send_now({"t": "admin", "ref": ref, "op": "claim", "ok": False, "code": "rate_limited",
                        "error": "too many tries: wait a minute"})
            return
        self.claims.add(c.ip())
        code = str(msg.get("code", "")).strip().lower()
        ok = bool(code) and secrets.compare_digest(code.encode("utf-8"), self.store.settings["admin_code"].encode("utf-8"))
        if ok and c.id not in self.store.settings["admins"]:
            self.store.settings["admins"].append(c.id)
            try:
                self.store.save_settings()
            except OSError as e:                     # not saved: not an admin (memory and disk never disagree)
                self.store.settings["admins"].remove(c.id)
                log.error("couldn't save %s as an admin: %s", c.name, e)
                c.send_now({"t": "admin", "ref": ref, "op": "claim", "ok": False,
                            "error": "the server couldn't save that: try again"})
                return
            log.info("%s (%s) is an admin now (admin code)", c.name, c.id)
        c.send_now({"t": "admin", "ref": ref, "op": "claim", "ok": ok,
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
            "id": s["id"], "admin_code": s["admin_code"], "version": VERSION, "uptime": int(mono() - self.started),
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
            "shared": {"keys": len(self.shared), "size": self.shared_total, "max_size": MAX_SHARED_TOTAL,
                       "seq": self.seq},
            "public_status": self.public_status,
            "directory": {"listed": len(self.directory)} if self.directory is not None else None,
            "log": list(RECENT)[-120:],
        }

    async def admin_op(self, op: str, msg: dict, by: str, c: Client | None = None) -> dict:
        """One admin action. All or nothing for the settings: if they can't be saved, they're put back as they were
        (memory and disk never disagree) and the admin is told."""
        before = json.loads(json.dumps(self.store.settings))
        try:
            return await self._admin_op(op, msg, by, c)
        except OSError as e:
            self.store.settings.clear()
            self.store.settings.update(before)
            log.error("%s's %s couldn't be saved: %s", by, op, e)
            raise ValueError(f"the server couldn't save that ({e.strerror or e})") from None

    async def _admin_op(self, op: str, msg: dict, by: str, c: Client | None = None) -> dict:
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
            if "max_players" in msg and small_int(msg["max_players"]) != s["max_players"]:
                s["max_players"] = max(1, min(64, small_int(msg["max_players"])))
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
                self.broadcast_now({"t": "server_info", "server": self.summary()})
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
                who.send_now({"t": "kicked", "reason": ("banned" if op == "ban" else "kicked") + f" by {by}"
                              + (f": {reason}" if reason else ""), "by_admin": True})
                who.shut()
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
                self.clients[target].send_now({"t": "server_info", "server": self.summary(), "admin": True})
            return {"ok": True, "id": target, "name": self.player_name(target)}
        if op == "admin_remove":
            if target not in s["admins"]:
                raise ValueError("they're not an admin")
            s["admins"].remove(target)
            self.store.save_settings()
            log.info("%s removed %s as an admin", by, self.player_name(target))
            if target in self.clients:
                self.clients[target].send_now({"t": "server_info", "server": self.summary(), "admin": False})
            return {"ok": True}
        if op == "say":
            text = str(msg.get("text", "")).strip()[:500]
            if not text:
                raise ValueError("nothing to say")
            self.broadcast_now({"t": "chat", "id": None, "name": "Server", "text": text, "time": now()})
            log.info("[server] %s: %s", by, text)
            return {"ok": True}
        if op == "restart":
            if not self.restartable or self.stop_event is None:
                raise ValueError("this server runs inside a player's program: restart that program instead")
            seconds = max(5, min(600, small_int(msg.get("in") or 30)))
            log.info("%s restarts the server in %d s", by, seconds)
            self.restart_requested = True                       # scheduled first: telling players can't hold it up
            asyncio.get_running_loop().call_later(seconds + 3, self.stop_event.set)
            self.announce_restart(seconds)
            return {"ok": True, "in": seconds}
        if op == "shared_remove":
            key, prefix = str(msg.get("key") or ""), str(msg.get("prefix") or "")
            if not key and not prefix:
                raise ValueError("give the key (or a prefix) to remove")
            keys = [k for k in list(self.shared) if (k == key if key else k.startswith(prefix))]
            before = len(self.shared)
            try:
                self.remove_shared(keys)
            except OSError as e:
                log.info("%s removed %d shared key(s) (%s)", by, before - len(self.shared), key or prefix + "*")
                raise ValueError(f"couldn't save the change: {e}") from None
            log.info("%s removed %d shared key(s) (%s)", by, len(keys), key or prefix + "*")
            return {"ok": True, "removed": len(keys)}
        if op == "world_restore":
            old = self.store.world_version(small_int(msg.get("version") or 0))
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
            self.broadcast_now({"t": "world_save", **meta})
            return {"ok": True, "version": version}
        raise ValueError(f"unknown admin action {op!r}")

    # ------------------------------------------------------------------ public servers: the directory
    def prune_directory(self) -> None:
        t = mono()
        for e in list((self.directory or {}).values()):
            if t - e["heard"] > LISTING_TTL:
                self.directory.pop(e["id"], None)

    async def points_at(self, address: str, ip: str) -> bool:
        """address (host:port) leads to the machine at ip: so a directory only ever checks the announcer itself."""
        host, port = split_address(address)
        want = norm_ip(ip)
        if want is None:
            return False
        given = norm_ip(host)
        if given is not None:
            return given == want
        try:
            infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM),
                                           3)
        except (OSError, ValueError, asyncio.TimeoutError):          # (UnicodeError is a ValueError)
            return False
        return any(norm_ip(ai[4][0]) == want for ai in infos)

    async def on_dir_announce(self, c: Client, msg: dict) -> None:
        """A public server says it's there: {id, secret, port, address?, name, ...}. It's listed (and checked to be
        reachable) until it goes quiet for LISTING_TTL seconds or says dir_remove. Before any hello, so it's bounded:
        MAX_LISTED in all, MAX_LISTED_PER_IP and NEW_LISTINGS_PER_IP per announcer, a check at most every PROBE_EVERY
        per announcer, and only of an address that leads to the announcer itself."""
        if self.directory is None:
            c.send_now({"t": "dir_announce", "ok": False, "error": "this server doesn't keep a list of servers"})
            return
        self.prune_directory()
        sid, secret = str(msg.get("id", ""))[:32], str(msg.get("secret", ""))[:64]
        old = self.directory.get(sid)
        if not re.fullmatch(r"[0-9a-f]{8,32}", sid) or len(secret) < 16 or \
                (old and not secrets.compare_digest(old["secret"].encode("utf-8"), secret.encode("utf-8"))):
            c.send_now({"t": "dir_announce", "ok": False, "error": "not accepted"})
            return
        try:
            port = small_int(msg.get("port") or 24242)
        except (TypeError, ValueError, OverflowError):
            port = 0
        if not 0 < port < 65536:
            c.send_now({"t": "dir_announce", "ok": False, "error": "bad port"})
            return
        ip, local = c.ip(), c.local()
        if not old:
            mine = sum(1 for e in self.directory.values() if e.get("ip") == ip)
            if len(self.directory) >= MAX_LISTED:
                c.send_now({"t": "dir_announce", "ok": False, "error": "the list is full"})
                return
            if not local and (mine >= MAX_LISTED_PER_IP or self.dir_new.count(ip) >= NEW_LISTINGS_PER_IP):
                c.send_now({"t": "dir_announce", "ok": False, "error": "too many servers from your address"})
                return
            if not local:
                self.dir_new.add(ip)
        given = str(msg.get("address") or "").strip()[:200]
        address = given or f"{ip}:{port}"
        if given and not local and not await self.points_at(given, ip):
            address = f"{ip}:{port}"                 # (an address elsewhere: listed where it actually announced from)
        entry = {"id": sid, "secret": secret, "address": address, "local": not given and local, "ip": ip,
                 "port": port, "seen": now(), "heard": mono(), "checked": (old or {}).get("checked"),
                 "reachable": (old or {}).get("reachable", False), "error": (old or {}).get("error", "")}
        for k, limit in (("name", 60), ("description", 300), ("version", 20)):
            entry[k] = str(msg.get(k) or "")[:limit]
        for k in ("players", "max_players", "protocol", "mods_count"):
            try:
                entry[k] = small_int(msg.get(k) or 0)
            except (TypeError, ValueError, OverflowError):
                entry[k] = 0
        names = msg.get("names")
        entry["password"], entry["names"] = bool(msg.get("password")), \
            [str(n)[:40] for n in (names if isinstance(names, list) else [])][:16]
        entry["world"] = world_summary(msg.get("world")) if isinstance(msg.get("world"), dict) else None
        t, probed = mono(), self.dir_probed.get(ip)
        due = not old or old["address"] != address or entry["checked"] is None or t - entry["checked"] > RECHECK
        if due and (local or probed is None or t - probed >= PROBE_EVERY):
            entry["checked"] = self.dir_probed[ip] = t
            if len(self.dir_probed) > 10000:
                self.dir_probed.clear()
            if self.probe_slots is None:
                self.probe_slots = asyncio.Semaphore(4)
            try:
                async with self.probe_slots:
                    answer = await request(address, {"t": "info"}, "info", timeout=5)
                entry["reachable"] = answer.get("id") == sid
                entry["error"] = "" if entry["reachable"] else "a different server answers at that address"
            except Exception as e:                   # noqa: BLE001 - whatever is (or isn't) there
                log.debug("checking %s: %r", address, e)
                entry["reachable"], entry["error"] = False, f"can't be reached at {address}"
        if not old:
            log.info("listing public server %s at %s%s", entry["name"], address,
                     "" if entry["reachable"] else f" - {entry['error']}")
        self.directory[sid] = entry
        c.send_now({"t": "dir_announce", "ok": True, "address": address, "reachable": entry["reachable"],
                    "error": entry["error"]})

    async def on_dir_remove(self, c: Client, msg: dict) -> None:
        entry = (self.directory or {}).get(str(msg.get("id", ""))[:32])
        if entry and secrets.compare_digest(entry["secret"].encode("utf-8"), str(msg.get("secret", "")).encode("utf-8")):
            del self.directory[entry["id"]]
            log.info("public server %s went private", entry["name"])
        c.send_now({"t": "dir_remove", "ok": True})

    async def on_dir_list(self, c: Client, msg: dict) -> None:
        """The public servers that can be reached (the DIR_LIST_MAX heard from last). A server on this same machine is
        given with the address the asker reached this one at (a Raspberry Pi listing itself is reachable at whatever
        address its players use)."""
        self.prune_directory()
        mine = c.writer.get_extra_info("sockname")
        out = []
        for e in sorted((self.directory or {}).values(), key=lambda e: -e["seen"]):
            if not e["reachable"]:
                continue
            address = f"{mine[0]}:{e['port']}" if e["local"] and mine else e["address"]
            out.append({k: v for k, v in e.items() if k not in ("secret", "local", "checked", "heard", "error", "ip")}
                       | {"address": address})
            if len(out) >= DIR_LIST_MAX:
                break
        c.send_now({"t": "dir_list", "servers": out, "error": None if self.directory is not None
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
        """While public: tell each directory about this server every minute (and right after a settings change).
        Nothing a directory answers (or fails to) can stop it."""
        self.announce_now = asyncio.Event()
        while True:
            try:
                if self.store.settings["public"]:
                    for d in self.directories():
                        try:
                            answer = await request(d, self.listing(), "dir_announce", timeout=10)
                            self.public_status[d] = {k: answer.get(k) for k in ("ok", "address", "reachable", "error")} \
                                | {"time": now()}
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:       # noqa: BLE001 - a directory down, or answering nonsense
                            self.public_status[d] = {"ok": False, "time": now(),
                                                     "error": f"can't reach the directory ({str(e)[:200] or 'timeout'})"}
            except asyncio.CancelledError:
                raise
            except Exception:                        # noqa: BLE001 - a bug: logged, and the announcing goes on
                log.exception("announcing to the directories failed")
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
            except asyncio.CancelledError:
                raise
            except Exception as e:                   # noqa: BLE001
                log.debug("unlisting from %s: %r", d, e)
        self.public_status.clear()

    # ------------------------------------------------------------------ keepalive
    async def keepalive(self) -> None:
        while True:
            await asyncio.sleep(PING_EVERY)
            try:
                self.tick()
            except Exception:                        # noqa: BLE001 - the keepalive must keep going
                log.exception("keepalive")

    def tick(self) -> None:
        """Every PING_EVERY: a ping to every player (queued: never waits on anyone), connections silent for far too
        long cut off (a backstop: the handlers time out first), players.json saved if it's due, old rate-limit entries
        forgotten."""
        self.broadcast_now({"t": "ping", "time": now()})
        t = mono()
        for c in list(self.conns):
            limit = 3 * (HELLO_WITHIN if c.id is None else FILES_TIMEOUT if c.files else TIMEOUT)
            if not c.aborted and t - max(c.last_heard, c.last_rx, c.last_progress) > limit:
                log.info("%s has been silent for %d s: disconnecting", c.name if c.id else c.addr, limit)
                c.abort()
        self.store.flush_players()
        for tries in (self.claims, self.hello_fails, self.new_players, self.dir_new):
            tries.prune()
        for ip in [ip for ip, at in self.dir_probed.items() if t - at > PROBE_EVERY]:
            del self.dir_probed[ip]


async def start(args):
    """Start serving and return once listening: (asyncio server, background tasks). The all-in-one app
    (client/launcher.py, client/companion.py) calls this to host inside the same program as the companion. Stop it
    with stop() (or: srv.close(), srv.mp_server.shutdown(), tasks.cancel())."""
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
    # return_exceptions: one task failing doesn't end the gather, so cancel() still reaches the others (the UDP
    # endpoint is closed by hold_udp); the done callback reads the outcome, so none is ever logged as "never retrieved".
    tasks = asyncio.gather(server.keepalive(), server.announcer(), server.hold_udp(), return_exceptions=True)
    tasks.add_done_callback(lambda f: f.cancelled() or f.exception())
    return srv, tasks


async def stop(srv, tasks, goodbye: str | None = None) -> None:
    """Stops what start() started, never waiting on a player's connection (a frozen PC can't hold it up): no more
    connections, a goodbye to everyone (a moment to go out), every connection cut, the background tasks ended."""
    server = srv.mp_server
    srv.close()
    await server.shutdown(goodbye)
    tasks.cancel()
    try:
        await tasks
    except asyncio.CancelledError:
        pass
    try:
        await asyncio.wait_for(srv.wait_closed(), 5)
    except asyncio.TimeoutError:
        pass
    await asyncio.sleep(0)                       # (the connections' handlers finish: nothing left pending)


async def main_async(args) -> bool:
    srv, tasks = await start(args)
    stop_event = asyncio.Event()
    srv.mp_server.stop_event, srv.mp_server.restartable = stop_event, True
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):     # Windows: Ctrl+C raises KeyboardInterrupt instead
            pass
    await stop_event.wait()
    log.info("shutting down")
    await stop(srv, tasks, "The server is restarting. You'll be reconnected in a moment.")
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
