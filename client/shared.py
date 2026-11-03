"""The server's shared state, the companion's side (mixed into the Companion; the server keeps it: inzoi_mp_server.py
on_set).

What's true for everyone lives on the server under a key, with a version: nobody applies a change that didn't come
back from the server, so both PCs apply the same values in the same order, and whoever joins (or reconnects, or
restarts the game) gets the whole state in the server's welcome and brings their game to it.

    house:<lot>:live   the couple's home as the game's commands read it (objects, walls, storey heights)
    house:<lot>:exact  the same home as the game's own saved property (paint, floors, roofs, customization: lotsync.py)
    obj:<lot>:<key>    one object's states there (on/off, the TV's channel...: objsync.py)
    family             {"together": true, "head": <player id>, "home_site": <lot>, "members": [ids]}

Each value carries who set it. A PC remembers the last version it applied of each key (cfg["shared_seen"]) so a
restart doesn't apply it again. Those versions are the server's: they belong to that server and to that life of its
shared state (the server's "epoch", new when its data is new), so another server, or the same one started over, never
has its changes ignored because an old number was bigger (cfg["shared_seen_scope"]; other servers' are kept in
cfg["shared_seen_by"] for when we're back). Object states are only remembered for this run: the first read of the lot
brings the game to the server's states anyway (objsync.py).

Every set carries a "ref"; the server's broadcast of it (to everyone, the sender too) carries it back, which is the only
proof the server has it: share(wait=True) / share_ex() wait for that, so a set lost with a dropped connection or
refused by the server is known and sent again. Changes made while the server couldn't be reached are kept as "dirty"
(cfg["house_dirty"]) and pushed when it can, instead of being overwritten by the server's older version.
"""
import asyncio
import hashlib
import json
import logging
import secrets
import time

log = logging.getLogger("shared")

RECONCILE_EVERY = 5.0
CONFIRM_WITHIN = 10.0       # s: a set the server hasn't confirmed by then is taken as lost (plus the time a big value
SLOW_LINK = 50_000          # takes up and back at this many bytes/s)
KEEP_SCOPES = 8             # other servers' seen versions kept (for when we're back there)
PERMANENT = ("too_big", "bad_key", "bad_request")     # errors that sending the same set again can't fix

FAMILY_RETRY = 120.0        # s before trying again to make this save match the shared family (doubling each time...)
FAMILY_RETRY_MAX = 1800.0   # (...up to this)
FAMILY_TRIES = 5            # tries for one version of the shared family; then left alone until it changes
FAMILY_RECHECK = 300.0      # s between checks once this save matches it (each one pages the game's households)


def version(item) -> int:
    try:
        return int((item or {}).get("v") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def digest(value) -> str:
    """The same for a value and what the server hands back for it (JSON both ways: dict keys become strings...)."""
    return hashlib.sha1(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SharedState:
    def shared_init(self):
        self.shared: dict[str, dict] = {}         # key -> item {key, value, v, by, by_name, at}, in version order
        self.shared_seq = 0
        self.shared_epoch = None
        self.shared_conn = None                   # the connection the server's snapshot came on (None: none yet)
        self.shared_out: dict[str, dict] = {}     # ref -> one of my sets the server hasn't answered yet
        self.shared_inflight: dict[str, str] = {}     # key -> ref of my newest set of it, not answered yet
        self.shared_seen_mem: dict[str, int] = {}     # obj:* versions applied (this run only)
        self.family_state: dict[str, dict] = {}   # partner pid -> {"ver", "next", "tries"}: making this save match

    # ---------------------------------------------------------------- the wire
    def on_shared_snapshot(self, items: dict | None, seq: int = 0, epoch: str | None = None):
        """The whole shared state (the welcome, or shared_all), applied oldest first: the newest of anything last."""
        items = {str(k): v for k, v in (items or {}).items() if isinstance(v, dict)} if isinstance(items, dict) else {}
        seq = int(seq or 0)
        self.shared_scope(epoch, seq)
        self.shared = dict(sorted(items.items(), key=lambda kv: version(kv[1])))
        self.shared_seq, self.shared_epoch, self.shared_conn = seq, epoch or None, self.writer
        # My sets sent on an earlier connection never get their answer: the server has them if this shows them
        # (the confirmation went down with the connection), else they went down with it and are sent again.
        for ref, s in list(self.shared_out.items()):
            if s["conn"] is self.writer:
                continue
            cur = self.shared.get(s["key"])
            got = cur is None if s["deleting"] else bool(
                cur and cur.get("by") == self.me_id and digest(cur.get("value")) == s["digest"])
            self._share_done(ref, "ok" if got else "lost", recheck=False)
        objects = sum(k.startswith("obj:") for k in self.shared)
        named = [f"{k} v{v.get('v')}" for k, v in self.shared.items() if not k.startswith("obj:")]
        named += [f"{objects} object states"] if objects else []
        log.info("shared state from the server: %s", ", ".join(named) or "nothing yet")
        for key, item in list(self.shared.items()):
            self.shared_changed(key, item, snapshot=True)
        hook = getattr(self, "on_objects_snapshot", None)            # (objsync.py: the lot's objects, all at once)
        if hook:
            try:
                hook()
            except Exception as e:                       # noqa: BLE001
                log.warning("applying the shared object states: %s", e)

    def on_shared_msg(self, m: dict):
        key, item = str(m.get("key") or ""), m.get("item")
        if not key:
            return
        epoch = m.get("epoch")
        if epoch and self.shared_epoch and epoch != self.shared_epoch:
            # the server's shared state was started over under us: take it whole again rather than mix the two
            log.info("the server's shared state is a new one: asking for all of it")
            self.shared_task(self.ask_shared_all())
            return
        item = item if isinstance(item, dict) else None
        self.shared.pop(key, None)
        if item:
            self.shared[key] = item                      # (at the end: the dict stays in version order)
        try:
            self.shared_seq = max(self.shared_seq, int(m.get("v") or 0))
        except (TypeError, ValueError):
            pass
        ref = m.get("ref")
        if ref and m.get("ref_by") == self.me_id:
            # mine, confirmed (before applying it: whether a newer set of mine is still on its way decides what counts)
            self._share_done(ref, "ok")
        self.tele.rec("note", text=f"shared {key} v{m.get('v')} by {(item or {}).get('by_name')}"
                                   f"{' (removed)' if not item else ''}")
        self.shared_changed(key, item, snapshot=False)

    def on_shared_refused(self, m: dict):
        """{"t":"set_refused","key","ref","current"}: my change was made on an older version; the server keeps the
        current one, which is applied here. (An "error" about one of my sets can come here too: on_shared_error.)"""
        if m.get("t") == "error":
            self.on_shared_error(m)
            return
        key, cur = str(m.get("key") or ""), m.get("current")
        cur = cur if isinstance(cur, dict) else None
        log.info("the server kept %s v%s (my change was made on an older version)", key, (cur or {}).get("v"))
        if key:
            if cur and version(cur) >= version(self.shared.get(key)):
                self.shared.pop(key, None)
                self.shared[key] = cur
            elif not cur:
                self.shared.pop(key, None)               # (it has nothing under that key: a new or started-over server)
        self._share_done(m.get("ref"), "refused", recheck=False)      # (first: then nothing of mine holds it back)
        if key and cur and self.shared.get(key) is cur:
            self.shared_changed(key, cur, snapshot=False)

    def on_shared_error(self, m: dict):
        """{"t":"error","code","ref"} about one of my sets: too big or a bad key can't be fixed by sending it again
        ("rejected"); anything else (the server couldn't keep it) is a lost set, sent again."""
        s = self.shared_out.get(m.get("ref"))
        if not s:
            return
        log.warning("the server didn't take %s: %s", s["key"], m.get("error") or m.get("code"))
        self._share_done(m.get("ref"), "rejected" if m.get("code") in PERMANENT else "lost")

    async def ask_shared_all(self):
        try:
            await self.send({"t": "shared_get"})
        except (OSError, ConnectionError, RuntimeError):
            pass

    async def share(self, key: str, value, base: int | None = None, wait: bool = False) -> bool:
        """Ask the server to set a shared key (value None removes it). base: the version this change was made on (the
        server refuses it if that's no longer the current one). Without wait: True once sent. With wait: True only once
        the server's broadcast of this very set came back; False if not connected, refused, or no word within ~10 s.
        (Never wait from inside the handling of a server message: the answer is read by that same loop.)"""
        return await self._shared_set(key, value, base, wait) in ("ok", "sent")

    async def share_ex(self, key: str, value, base: int | None = None) -> str:
        """share(wait=True), saying why: "ok" (the server has it), "refused" (made on an older version: the server's
        current one has been applied here), "rejected" (too big, a bad key: sending it again won't help), "lost" (no
        word in time, or the connection went: send it again), "offline" (not connected: nothing was sent)."""
        return await self._shared_set(key, value, base, True)

    async def _shared_set(self, key: str, value, base: int | None, wait: bool) -> str:
        conn = self.writer
        if not (conn and self.me_id) or self.shared_conn is not conn:
            # (no snapshot on this connection: not joined yet, or a server without the shared state, which would
            # never answer)
            return "offline"
        blob = json.dumps(value, sort_keys=True, separators=(",", ":"))
        ref = secrets.token_hex(6)
        loop = asyncio.get_running_loop()
        s = self.shared_out[ref] = {"key": key, "digest": hashlib.sha1(blob.encode()).hexdigest(),
                                    "deleting": value is None, "conn": conn, "fut": loop.create_future(),
                                    "timer": None}
        self.shared_inflight[key] = ref
        msg = {"t": "set", "key": key, "value": value, "ref": ref}
        if base is not None:
            msg["base"] = int(base)
        try:
            await self.send(msg)
        except (OSError, ConnectionError, RuntimeError) as e:
            log.info("%s didn't reach the server: %s", key, e)
            self._share_done(ref, "lost")
            return "lost"
        if ref in self.shared_out:                       # (the answer can be here already)
            s["timer"] = loop.call_later(CONFIRM_WITHIN + 2 * len(blob) / SLOW_LINK, self._share_done, ref, "lost")
        if not wait:
            return "sent"
        return await asyncio.shield(s["fut"])

    def _share_done(self, ref, outcome: str, recheck: bool = True):
        """The server's word on one of my sets (or none in time). A partner's version of that key that arrived
        while mine was on its way was held back for mine (superseded); if mine didn't make it, theirs is applied now."""
        s = self.shared_out.pop(ref, None) if ref else None
        if not s:
            return
        if s["timer"]:
            s["timer"].cancel()
        if not s["fut"].done():
            s["fut"].set_result(outcome)
        key = s["key"]
        if self.shared_inflight.get(key) == ref:
            del self.shared_inflight[key]
            item = self.shared.get(key)
            if outcome != "ok" and recheck and item and item.get("by") != self.me_id:
                self.shared_changed(key, item, snapshot=False)

    def superseded(self, key: str, item: dict | None = None) -> bool:
        """A set of mine for this key is on its way to the server and back: anything that arrives for the key before
        it was ordered before it by the server (it sends to each player in order), so mine replaces it: not applied."""
        return key in self.shared_inflight

    def shared_task(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        keep = self.__dict__.setdefault("_shared_tasks", set())     # (a task nobody holds can be collected mid-way)
        keep.add(task)
        task.add_done_callback(keep.discard)
        return task

    # ---------------------------------------------------------------- what's been applied
    def shared_scope(self, epoch, seq: int):
        """Tie the seen versions to this server and this life of its shared state; keep the others' for later."""
        scope = f"{self.cfg.get('server_id') or '?'}:{epoch or ''}"
        old = self.cfg.get("shared_seen_scope")
        seen = self.cfg.get("shared_seen")
        seen = {k: v for k, v in seen.items() if isinstance(v, int) and not k.startswith("obj:")} \
            if isinstance(seen, dict) else {}
        changed = old != scope
        if old is None and seen:
            # (a config from before the seen versions were kept per server: one list for every server; which server's
            # numbers they were isn't known, and keeping them could ignore this one's changes: it all gets compared
            # once more, which changes nothing that already matches)
            log.info("seen versions from before they were kept per server: compared again")
            seen = {}
        elif changed:
            by = self.cfg.get("shared_seen_by") if isinstance(self.cfg.get("shared_seen_by"), dict) else {}
            if old and seen:
                by.pop(old, None)
                by[old] = seen
            seen = by.pop(scope, None) or {}
            while len(by) > KEEP_SCOPES:
                by.pop(next(iter(by)))
            self.cfg["shared_seen_by"] = by
            log.info("shared state of %s: %d version(s) already applied", scope, len(seen))
        if changed:
            self.shared_seen_mem = {}
        if seen and seq < max(seen.values()):
            log.info("the server's versions went back (%d, I had seen %d): its shared state is compared again",
                     seq, max(seen.values()))
            seen, changed = {}, True
        if self.shared_seen_mem and seq < max(self.shared_seen_mem.values()):
            self.shared_seen_mem = {}
        if changed or seen != self.cfg.get("shared_seen"):
            self.cfg["shared_seen"], self.cfg["shared_seen_scope"] = seen, scope
            self.save_cfg()

    def seen(self, key: str) -> int:
        if key.startswith("obj:"):
            return int(self.shared_seen_mem.get(key) or 0)
        return int((self.cfg.get("shared_seen") or {}).get(key) or 0)

    def mark_seen(self, key: str, v: int):
        """Only ever up: a slow apply of an older version finishing late doesn't make it look unapplied again."""
        v = int(v or 0)
        if key.startswith("obj:"):
            if v > self.shared_seen_mem.get(key, 0):
                self.shared_seen_mem[key] = v
            return
        seen = self.cfg.setdefault("shared_seen", {})
        if v > int(seen.get(key) or 0):
            seen[key] = v
            self.save_cfg()

    def forget_seen(self, key: str):
        """Compare this key with the game again (the panel's "get my partner's house"...)."""
        self.shared_seen_mem.pop(key, None)
        if (self.cfg.get("shared_seen") or {}).pop(key, None) is not None:
            self.save_cfg()

    def shared_changed(self, key: str, item: dict | None, snapshot: bool):
        try:
            if key.startswith("house:") and key.endswith(":live"):
                self.on_shared_house_live(key, item, snapshot)
            elif key.startswith("house:") and key.endswith(":exact"):
                self.on_shared_house_exact(key, item, snapshot)
            elif key == "family":
                self.on_shared_family(item, snapshot)
            elif key.startswith("obj:"):
                self.on_shared_object(key, item, snapshot)
        except Exception as e:                           # noqa: BLE001
            log.warning("applying shared %s: %s", key, e)

    # ---------------------------------------------------------------- family
    def on_shared_family(self, item: dict | None, snapshot: bool):
        if item:
            log.info("family (shared v%s): %s", item.get("v"), item.get("value"))

    async def share_family(self, home_site, members: list):
        await self.share("family", {"together": True, "head": self.leader or self.me_id, "home_site": home_site,
                                    "members": sorted(set(members))})

    async def reconcile_family(self):
        """The shared family says we live together: make it so in this save too (the partner's Zoi in my household,
        my family in the head's home), whatever happened to this save meanwhile. What can't be done isn't tried
        forever: a few tries, each after twice the wait, then nothing until the shared family changes."""
        item = self.shared.get("family") or {}
        fam = item.get("value") or {}
        if not fam.get("together") or self.world == "out" or not self.my_entity or not self.mp_active():
            return
        home = None if fam.get("head") == self.me_id else fam.get("home_site")
        try:
            home = int(home) if home is not None else None
        except (TypeError, ValueError):
            home = None
        ver = (self.shared_epoch, version(item), fam.get("head"), home)
        for pid in fam.get("members") or []:
            r = self.remotes.get(pid)
            if pid == self.me_id or not r or pid not in self.puppet_ids or r.hidden:
                continue
            st = self.family_state.get(pid)
            if not st or st["ver"] != ver:
                st = self.family_state[pid] = {"ver": ver, "next": 0.0, "tries": 0}
            if time.time() < st["next"]:
                continue
            st["next"] = time.time() + FAMILY_RETRY      # (if the game doesn't answer below: not every 5 s)
            together = await self.g(self.family_together, r, fam)
            if together:
                st["next"], st["tries"] = time.time() + FAMILY_RECHECK, 0
                continue
            if st["tries"] >= FAMILY_TRIES:
                log.info("this save still doesn't match the shared family with %s after %d tries: left as it is until "
                         "the family changes", r.name, st["tries"])
                self.tele.rec("note", text=f"family: gave up making this save match (with {r.name})")
                st["next"] = float("inf")
                continue
            st["tries"] += 1
            st["next"] = time.time() + min(FAMILY_RETRY * 2 ** (st["tries"] - 1), FAMILY_RETRY_MAX)
            log.info("the shared family says %s and I live together, but this save doesn't: making it so (try %d)",
                     r.name, st["tries"])
            self.tele.rec("note", text=f"family: making this save match the shared family (with {r.name})")
            try:
                res = await self.g(self.family_apply, "movein", pid, home) or {}
                log.info("family made to match: %s", res)
                if res.get("code") == "asked_game":      # (the game asks the player on screen: not again soon)
                    st["next"] = time.time() + FAMILY_RETRY_MAX
            except Exception as e:                       # noqa: BLE001
                log.warning("couldn't make the family match: %s", e)

    def family_together(self, r, fam: dict) -> bool:
        """In this save: their Zoi is in my household and (if the head isn't me) we live on the head's lot - or, when
        another family lives on that lot in this save, as together as family_apply makes it (it never moves us into
        strangers' home: their Zoi joins my household instead)."""
        if not self.family_zoi(r):
            return False
        site = fam.get("home_site")
        if fam.get("head") == self.me_id or not site:
            return True
        if str(self.my_home_site()) == str(site):
            return True
        try:
            there = self.household_at(int(site))
        except (TypeError, ValueError):
            return True
        mine = self.my_household().get("household_id")
        return bool(there) and there != mine and not self.has_member(there, r)

    # ---------------------------------------------------------------- the loop
    async def shared_loop(self):
        while True:
            await asyncio.sleep(RECONCILE_EVERY)
            try:
                if self.writer and self.me_id:
                    await self.reconcile_family()
                    await self.push_dirty_house()
            except Exception as e:                       # noqa: BLE001
                log.debug("shared reconcile: %s", e)
