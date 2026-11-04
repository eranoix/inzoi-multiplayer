"""The companion -> in-game controller channel: Lua modules the B2 controller re-reads a few times a second.

MPCmd.lua  small: one-shot ops (kept for a few seconds of running game, the controller runs each id once) and the
           newest trail of every remote player. Rewritten only when something in it changed.
MPSeq.lua  tiny, `return {seq = N}`: written right after each MPCmd.lua, so the controller can poll it cheaply and
           only reload MPCmd.lua when the number moves (without MPSeq it reads MPCmd.lua every time, as before).
MPBulk.lua big, rewritten only when a profile arrives: the rebuilt identities of remote players.
All are written atomically (temp file + rename) so the game never reads half a file. A file the game (or an
antivirus, OneDrive...) holds open just waits for the next flush: nothing here raises because of it.
"""
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger("mailbox")
MAX_OP_AGE = 120                 # seconds: even with the game paused, an op older than this is dropped


def lua(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(lua(x) for x in v) + "}"
    if isinstance(v, dict):
        return "{" + ", ".join(f"[{lua(k)}] = {lua(x)}" for k, x in v.items()) + "}"
    if isinstance(v, RawLua):
        return v.code
    raise TypeError(type(v))


class RawLua:
    def __init__(self, code: str):
        self.code = code


def write_atomic(path: Path, text: str):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:          # the game may be reading it right now
            time.sleep(0.02)
    raise PermissionError(path)


class Mailbox:
    def __init__(self, folder: Path, op_ttl: float = 4.0):
        self.cmd = folder / "MPCmd.lua"
        self.seqf = folder / "MPSeq.lua"
        self.bulk = folder / "MPBulk.lua"
        self.ttl = op_ttl
        self.seq = int(time.time() * 10)
        self.bulk_seq = int(time.time() * 10)      # (from the clock too: a restart never goes back to a number seen)
        self.bulk_written = None                   # the bulk seq actually on disk (what MPCmd points at)
        self.ops = []            # [(expires_at, created_at, op)], time.monotonic()
        self.members = {}        # key -> Lua literal of FB1CustomizedCharacterInfo
        self.trails = {}         # key -> list of [n, x, y, z, yaw]
        self.point_n = int(time.time() * 100)      # the count the controller gets: rising, whatever the source
        self.point_src = {}      # key -> the sender's count of the last point taken (a repeat is dropped)
        self.paused = False      # the world is paused (no controller ticks): ops don't run out meanwhile
        self.written = None      # the body of the last MPCmd.lua written (None: write next flush)
        self.seq_written = None  # the seq in MPSeq.lua
        self.last_flush = time.monotonic()
        self.lock = threading.Lock()               # state (ops, members, trails): op() runs on the event loop,
        self.wlock = threading.Lock()              # flush() in a thread; one writer at a time

    def op(self, name: str, **fields):
        fields = {"id": uuid.uuid4().hex[:12], "op": name, **fields}
        now = time.monotonic()
        with self.lock:
            self.ops.append((now + self.ttl, now, fields))
        return fields["id"]

    def pending(self, key: str, name: str) -> bool:
        """An op `name` for `key` is still in the mailbox (not expired)."""
        with self.lock:
            return any(o.get("key") == key and o.get("op") == name for _, _, o in self.ops)

    def set_member(self, key: str, member_lua: str | None):
        """Change a member; MPBulk.lua is written by the next flush (before the MPCmd.lua that points at it)."""
        with self.lock:
            if member_lua is None:
                self.members.pop(key, None)
            else:
                self.members[key] = member_lua
            self.bulk_seq += 1

    def add_point(self, key: str, n: int, x, y, z, yaw, ts=None):
        """A point of where a partner's Zoi is. n: the sender's own count, only for dropping a repeat; the controller
        gets this program's count instead, one rising count for every source. (Points come from their states - small
        counts - and from their skeleton frames - counted from 10 000 000 - one or the other as frames stop and start,
        at a loading screen: mixed in one batch the controller took each drop for "their program restarted", dropped
        the trail and teleported the Zoi back and forth, 10 times in half a second.) Started from the clock, the
        count is higher after a restart of this program too.
        ts: the sender's clock when the point was read (the controller works out their speed from it)."""
        with self.lock:
            if self.point_src.get(key) == n:
                return
            self.point_src[key] = n
            self.point_n += 1
            t = self.trails.setdefault(key, [])
            t.append([self.point_n, x, y, z, yaw] + ([round(ts % 100000, 3)] if isinstance(ts, (int, float)) else []))
            del t[:-10]                  # the controller dedups by n; resending the last few covers missed polls

    def flush(self) -> bool:
        """Write what changed. False: a file was held open (by the game, an antivirus...): tried again next time."""
        with self.wlock:
            now = time.monotonic()
            with self.lock:
                dt, self.last_flush = now - self.last_flush, now
                if self.paused:              # no controller ticks while paused: the ops' few seconds wait too
                    self.ops = [(exp + dt, made, o) for exp, made, o in self.ops]
                self.ops = [(exp, made, o) for exp, made, o in self.ops if exp > now and now - made < MAX_OP_AGE]
                bulk = None
                if self.bulk_written != self.bulk_seq:
                    body = ", ".join(f"[{lua(k)}] = {v}" for k, v in self.members.items())
                    bulk = (self.bulk_seq, f"return {{ seq = {self.bulk_seq}, members = {{ {body} }} }}\n")
                ops = [o for _, _, o in self.ops]
                ops += [{"op": "trail", "key": k, "pts": pts} for k, pts in self.trails.items() if pts]
            try:
                if bulk:
                    write_atomic(self.bulk, bulk[1])
                    self.bulk_written = bulk[0]
                body = f"bulk = {self.bulk_written or 0}, ops = {lua(ops)}"
                if body != self.written:     # (unchanged: the controller keeps what it has)
                    self.seq += 1
                    write_atomic(self.cmd, f"return {{ seq = {self.seq}, {body} }}\n")
                    self.written = body
                if self.seq_written != self.seq:
                    write_atomic(self.seqf, f"return {{seq = {self.seq}}}\n")    # after MPCmd: it announces it
                    self.seq_written = self.seq
                return True
            except OSError as e:             # (PermissionError: held open; try again next flush)
                log.debug("mailbox write: %s", e)
                return False
