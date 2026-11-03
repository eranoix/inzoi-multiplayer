"""The state of the home's objects (mixed into the Companion; the in-game half is read_states/set_states in
lua/B2/Main.lua): on/off, open/closed, the TV's channel... whatever the game keeps in an object's state component.

Every few seconds the controller reads the states of every object on our home lot. An object whose states changed here
(and not because the partner's change was just applied to it) goes to the server as the shared key
obj:<lot>:<object key> = {"t": table id, "p": [x, y, z], "s": ["group:state", ...], "ch": channel}; the server hands it
to everyone, and each PC puts that object in its game (the nearest of the same kind, within a metre: the rule set_states
uses) in those states, through the game's own state change (its lights, screens and sounds follow). The Slot groups
(who sits where) aren't synced: the skeletons already show that.

The server's states are the truth. The first read of the lot in a world (after loading or reloading it, or a restart
of the program or of the in-game controller) is only the starting point: nothing is sent from it, and every object is
brought to the server's state, whoever set it - a reloaded save has old states. When several of the server's keys are
the same object here (a moved object, or the two saves' positions a little apart), the newest one counts. A change of
mine isn't done until the server's broadcast of it comes back: one lost with a connection, or that the server didn't
take, is sent again (from the second try with the version it was made on, so a partner's change made meanwhile wins
instead of being overwritten); and a partner's change that the server ordered before mine isn't applied while mine is
on its way back - mine replaces it on both PCs.

For a moment after a partner's state is put on an object, what's read on it isn't sent: it can be the game's own
reaction to the state (which must not go back to the partner), and that can't be told from the player's change. When
that moment is over, the object is compared with the server's state once (settle_objects), so the two PCs don't stay
apart: a change made here after the partner's state took is sent (the last change wins); anything else - the game
didn't take the state, the save put an old one back after a reload - is put back to the server's state. A game that
keeps putting an object back is settled once and then left, so the PCs can't flip it to and fro: what a settle sends
says so ("settle": true in the value), and a settle answering it is the one left. A change made here after the
partner's own change took is always sent, however soon after the last settle.

An object is compared with its own last read (the game's entity id), not with whatever stood at that spot: the house
sync's remove + place (the partner turned or moved something) and the exact house rebuilding the lot put new objects in
their default states, which nobody chose - such a new object is brought to the server's state like one that showed up
(nothing sent), and when the server has none for it, it's put back in the states the one it replaces had. An object
moved here (the same entity at another spot) takes its state along: when the server's state for its new spot isn't
that, it's sent for the new spot (the partner's game places it anew, in its default states), and the server's keys for
where it was are removed once that's confirmed. A change of mine still unconfirmed when the world is left (or
multiplayer paused) isn't forgotten: at the next first read it goes again if the game still has the object so and the
server got nothing newer for it meanwhile. A change made before anything of the server was known in this run (the
program started while it couldn't be reached) wins when the connection comes - it goes on the server's version then -
unless the server's state of that object was set after it (the server's "at", against when it was made here: the last
change wins, as live).
"""
import asyncio
import hashlib
import logging
import time

log = logging.getLogger("objsync")

READ_EVERY = 3.0        # s between reads of the lot's object states
QUIET_AFTER = 15.0      # s after applying the partner's state to an object during which its own changes aren't sent
#                         (then it's compared with the server's state once: settle_objects)
NEAR = 100.0            # cm: the same object in both saves
RETRY_FIRST = 3.0       # s before a change the server didn't confirm goes again (doubling each time...)
RETRY_MAX = 60.0        # (...up to this)
TRIES = 6               # sends of one change; then the server's state is taken instead (both PCs end the same)
GONE_FOR = 120.0        # s an object that vanished is remembered (one placed again at its spot gets its states back)
KEEP_MAX = 256          # unconfirmed changes of mine kept through leaving the world (the oldest go first)


def obj_key(table: str, x: float, y: float, z: float) -> str:
    raw = f"{table}|{round(x / 50)}|{round(y / 50)}|{round(z / 50)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def parse_states(text: str):
    """The controller's "states" report: "<lot>;entity|table|x|y|z|g:s,g:s|channel;..." -> (lot, {key: obj})."""
    parts = text.split(";")
    try:
        site = int(parts[0])
    except (ValueError, IndexError):
        return None, {}
    objs = {}
    for line in parts[1:]:
        f = line.split("|")
        if len(f) != 7:
            continue
        try:
            x, y, z = float(f[2]), float(f[3]), float(f[4])
        except ValueError:
            continue
        states = sorted(s for s in f[5].split(",") if s and not s.startswith("Slot"))
        ch = f[6] if f[6] and f[6] != "None" else ""
        # (e: the game's entity id - which object this is, never sent: value_of)
        objs[obj_key(f[1], x, y, z)] = {"t": f[1], "p": [x, y, z], "s": states, "ch": ch, "e": f[0]}
    return site, objs


def value_of(o: dict) -> dict:
    """An object as it's shared: {"t", "p", "s", "ch"} (no entity id: that's this game's own)."""
    return {"t": o["t"], "p": list(o["p"]), "s": list(o.get("s") or []), "ch": o.get("ch") or ""}


def with_sig(o: dict, want: str) -> dict:
    """The object in the states of that sig."""
    states, _, ch = want.rpartition("|")
    return dict(o, s=[s for s in states.split(",") if s], ch=ch)


def sig(o: dict) -> str:
    return ",".join(o.get("s") or []) + "|" + (o.get("ch") or "")


def nearest(objs: dict, table: str, x: float, y: float, z: float):
    """The key of the object of that kind nearest to (x, y, z), within NEAR, or None: set_states' own rule (strictly
    nearer wins, so of two at the same distance the first in the game's order, which is the read's order)."""
    best, bd = None, NEAR ** 2
    for k, h in objs.items():
        if h.get("t") != table:
            continue
        p = h["p"]
        d = (p[0] - x) ** 2 + (p[1] - y) ** 2 + (p[2] - z) ** 2
        if d < bd:
            best, bd = k, d
    return best


def holds(o: dict, want: str) -> bool:
    """The object (as read) is in the states of that sig: every one of them, and on its channel if it has one.
    (set_states only changes the groups it's given: one the partner's object doesn't have stays as it is here.)"""
    states, _, ch = want.rpartition("|")
    have = set(o.get("s") or [])
    return all(s in have for s in states.split(",") if s) and (not ch or ch == (o.get("ch") or ""))


def settle_span() -> float:
    """An object is settled (settle_objects) at most once in this long: longer than it takes the partner's answer to a
    settling (their quiet time and read) to come back and be settled in turn (mine)."""
    return 3 * (QUIET_AFTER + READ_EVERY)


def version(item) -> int:
    try:
        return int((item or {}).get("v") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def _at(item) -> float:
    """When the server set a shared item (its clock; 0: not said)."""
    try:
        return float((item or {}).get("at") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


class ObjSync:
    def objsync_init(self):
        self.obj_site = None          # the lot the last read was of (None: the next read is a first read)
        self.obj_instance = None      # the in-game controller that read it (a new one: the world was (re)loaded)
        self.obj_last: dict[str, dict] = {}       # key -> the object as last read
        # (when, table, x, y, z, sig, key here, live): states put on objects here (live: a partner's change as it came,
        # not the lot brought to the server's states)
        self.obj_applied: list[tuple] = []
        self.obj_landed: set[str] = set()         # keys here whose read showed the state last put on them (it took)
        self.obj_settled: dict[str, float] = {}   # key here -> when it was last settled (settle_objects)
        self.obj_pending: dict[str, dict] = {}    # shared key -> a change of mine the server hasn't confirmed yet
        # shared key -> a change of mine still unconfirmed when the world was left (revive_kept, at the next first read)
        self.obj_kept: dict[str, dict] = {}
        self.obj_gone: dict[str, tuple] = {}      # key here -> (when, the object as last read): it vanished since
        self.obj_full_due = False     # bringing every object to the server's states couldn't be done yet (paused...)
        self.obj_asked = 0.0
        self.obj_counts = {"sent": 0, "applied": 0}

    def objsync_reset(self):
        """The world was left or reloaded, or multiplayer paused: what was read is history (a reload puts the save's
        own states back). The next read is a first read again: brought to the server's states, nothing sent from it -
        except my changes the server never confirmed, which are kept for it (revive_kept)."""
        if self.obj_site is not None:
            log.info("object states of lot %s: read again from scratch next time", self.obj_site)
        for k, p in self.obj_pending.items():
            self.obj_kept.pop(k, None)
            self.obj_kept[k] = p                         # (at the end: the oldest go first when there are too many)
        while len(self.obj_kept) > KEEP_MAX:
            self.obj_kept.pop(next(iter(self.obj_kept)))
        self.obj_site, self.obj_instance, self.obj_last, self.obj_applied = None, None, {}, []
        self.obj_pending, self.obj_full_due, self.obj_landed, self.obj_settled = {}, False, set(), {}
        self.obj_gone = {}

    def obj_scope(self):
        """The server and the life of its shared state a version number belongs to."""
        return (self.cfg.get("server_id"), getattr(self, "shared_epoch", None))

    def revive_kept(self, site, objs: dict) -> int:
        """A first read: my changes the server never confirmed before the world was left go again if the game still
        has the object so (the save kept it) and the server got nothing newer for it since (else that counts, as for
        any first read). -> how many go again."""
        prefix, groups, n = f"obj:{site}:", None, 0
        for k, p in list(self.obj_kept.items()):
            if not k.startswith(prefix):
                continue                                 # (another lot: kept for when it's read)
            del self.obj_kept[k]
            here = objs.get(k[len(prefix):])
            t = (p.get("o") or {}).get("t")
            if here is None or sig(here) != p["sig"] or p.get("scope") != self.obj_scope():
                log.info("my change of %s never reached the server and the game doesn't have it so now: dropped", t)
                continue
            if groups is None:
                groups = self.obj_groups(site, objs)
            if any(v > p.get("seq", 0) for v, _k, _i in groups.get(k[len(prefix):]) or []):
                log.info("my change of %s never reached the server, which has a newer state for it: that counts", t)
                continue
            log.info("my change of %s that never reached the server is still so in the game: sent again", t)
            self.obj_pending[k] = dict(p, o=value_of(here), tries=0, next=0.0, busy=False, use_base=True)
            n += 1
        return n

    def mod_instance(self):
        return (getattr(self, "mod", None) or {}).get("instance")

    def objects_live(self, site) -> bool:
        """States can be put on this lot's objects now: it's been read in this world and the game is running."""
        return (self.obj_site is not None and str(self.obj_site) == str(site)
                and self.obj_instance == self.mod_instance() and self.world == "play"
                and (getattr(self, "mod", None) or {}).get("state") == "running")

    # ---------------------------------------------------------------- reading
    async def objsync_loop(self):
        while True:
            await asyncio.sleep(READ_EVERY)
            try:
                if self.world == "out" or not self.mp_active():
                    if self.obj_site is not None or self.obj_pending:
                        self.objsync_reset()
                    continue
                self.retry_objects()
                # (read while the server can't be reached too: a change made meanwhile is sent once it can be,
                # instead of the server's older state being put over it when we're back)
                if not (self.world == "play" and self.mod.get("state") == "running"):
                    continue
                home = await self.home_site_cached()
                if home:
                    self.obj_asked = time.time()
                    self.mail.op("read_states", site=int(home))
            except Exception as e:                       # noqa: BLE001
                log.debug("object states: %s", e)

    def on_states_report(self, text: str):
        site, objs = parse_states(text)
        if site is None:
            return
        if site != self.obj_site or self.obj_instance != self.mod_instance():
            # first read of this lot in this world: what the save has is only the starting point
            self.objsync_reset()
            self.obj_site, self.obj_instance, self.obj_last = site, self.mod_instance(), objs
            log.info("object states of lot %d: %d objects with a state", site, len(objs))
            revived = self.revive_kept(site, objs)
            self.reconcile_objects(site, full=True)
            if revived:
                self.retry_objects()
            return
        now = time.time()
        ended = [a for a in self.obj_applied if now - a[0] >= QUIET_AFTER]
        self.obj_applied = [a for a in self.obj_applied if now - a[0] < QUIET_AFTER]
        present = {line.split("|", 1)[0] for line in text.split(";")[1:]}     # (every object read, by entity id)
        was_at = {b["e"]: k for k, b in self.obj_last.items() if b.get("e")}
        fresh, groups, moved, placed = set(), None, [], {}
        for key, o in objs.items():
            before, e = self.obj_last.get(key), o.get("e")
            if before is None or (e and before.get("e") and before["e"] != e):
                # not the object read here last time: it moved here (the same entity elsewhere), or it's a new one
                if e and was_at.get(e, key) != key and self.obj_last[was_at[e]]["t"] == o["t"]:
                    moved.append((was_at[e], key))
                    continue
                fresh.add(key)                           # (showed up since: brought to the server's state, not sent)
                old = before if before is not None else (self.obj_gone.get(key) or (0, None))[1]
                if old is not None and e and old.get("e") and old["e"] != e and old["e"] not in present:
                    placed[key] = old                    # (the one that was here is gone: placed again, default states)
                continue
            if sig(before) == sig(o) or self.just_applied(key, o, objs):
                continue
            k = f"obj:{site}:{key}"
            if groups is None:
                groups = self.obj_groups(site, objs)
            best = (groups.get(key) or [None])[-1]
            if best and sig(best[2].get("value") or {}) == sig(o) and not self.superseded(k):
                # what the server has already (applied late, or changed back before mine went): nothing to send - but
                # with a set of mine for it still on its way, this goes after it, or mine would end up the server's
                self.obj_pending.pop(k, None)
                continue
            self.push_object(site, key, before, o)
        if moved:
            if groups is None:
                groups = self.obj_groups(site, objs)
            ended = self.objects_moved(site, moved, objs, groups, ended, fresh)
        for k, b in self.obj_last.items():
            if k not in objs and b.get("e") not in present:
                self.obj_gone[k] = (now, b)              # (vanished - not moved: a moved one is elsewhere in this read)
        self.obj_gone = {k: g for k, g in self.obj_gone.items() if k not in objs and now - g[0] < GONE_FOR}
        self.obj_last = objs
        for a in {a[6]: a for a in self.obj_applied}.values():      # (the newest state put on each object)
            if a[6] in objs and holds(objs[a[6]], a[5]):
                self.obj_landed.add(a[6])
        if placed:
            if groups is None:
                groups = self.obj_groups(site, objs)
            ended = self.objects_placed(site, placed, objs, groups, ended)
        if ended:
            # (what was read on those objects meanwhile was taken in above without being sent: they're compared with
            # the server's states now, or a change made in that time would never reach the partner nor be undone)
            self.settle_objects(site, ended)
        self.reconcile_objects(site, full=self.obj_full_due, fresh=fresh)

    def objects_moved(self, site: int, moved: list, objs: dict, groups: dict, ended: list, fresh: set) -> list:
        """Objects of this lot moved since the last read ((old key, new key): the same entity). The state goes with the
        object: the partner's game places it anew (the house sync's remove + place) in its default states, and finds
        the server's states by where the object is - so unless the server's state for its new spot is already the
        one it has, that's sent for the new spot. What was put on it a moment ago follows it too. -> `ended` with
        those objects' entries following them."""
        old_groups = self.obj_groups(site, self.obj_last)
        prefix = f"obj:{site}:"
        for src, key in moved:
            o, k = objs[key], prefix + key
            want = self.follow_sig(site, src, o, old_groups)
            carried = any(a[6] == src for a in self.obj_applied)

            def follow(a, src=src, key=key, p=o["p"]):
                return (a[0], a[1], p[0], p[1], p[2], a[5], key, a[7]) if a[6] == src else a
            self.obj_applied, ended = [follow(a) for a in self.obj_applied], [follow(a) for a in ended]
            if src in self.obj_landed:
                self.obj_landed.discard(src)
                self.obj_landed.add(key)
            if src in self.obj_settled:
                self.obj_settled[key] = self.obj_settled.pop(src)
            fresh.add(key)                               # (anything else the server has for it: reconcile_objects)
            if k in self.obj_pending or self.superseded(k):
                continue                                 # (a change of mine for that spot on its way decides it)
            best = (groups.get(key) or [None])[-1]
            if best and sig(best[2].get("value") or {}) == want:
                continue                                 # (the server's state there is its own already: a short move)
            log.info("%s moved here: its state goes with it (%s)", o["t"], want.strip("|") or "normal")
            if not carried and not holds(o, want):
                self.put_states(site, key, o, want)      # (a newer state of it that hadn't been put on it yet)
            gone = [e[1] for e in old_groups.get(src) or []]
            self.push_object(site, key, self.obj_last[src], with_sig(o, want),
                             moved=gone + [prefix + src] if prefix + src not in gone else gone)
        return ended

    def follow_sig(self, site: int, src: str, o: dict, old_groups: dict) -> str:
        """The states a moved object has (or is about to have): the server's newest for it where it was if that hasn't
        been put on it yet (and no change of mine for it is on its way), else what was put on it a moment ago, else
        as read now."""
        g = old_groups.get(src)
        if g and f"obj:{site}:{src}" not in self.obj_pending:
            v, key, item = g[-1]
            if self.seen(key) < v and not self.superseded(key):
                return sig(item.get("value") or {})
        for a in reversed(self.obj_applied):
            if a[6] == src:
                return a[5]
        return sig(o)

    def objects_placed(self, site: int, placed: dict, objs: dict, groups: dict, ended: list) -> list:
        """New objects where others stood a moment ago, now gone (the house sync's remove + place for a partner's
        turn or small move, the exact house rebuilding the lot): the game made them in their default states, which
        nobody chose - nothing is sent. The server's state for them is put on them (reconcile_objects: they're
        fresh); with none (nobody ever changed that object) they get the states the one they replace had - or my
        own change of it still on its way. What was put on the one that's gone is history. -> `ended` without it."""
        prefix = f"obj:{site}:"
        for key, old in placed.items():
            prior = [a[5] for a in self.obj_applied if a[6] == key]
            self.obj_applied = [a for a in self.obj_applied if a[6] != key]
            self.obj_landed.discard(key)
            p = self.obj_pending.get(prefix + key)
            if p is None and groups.get(key):
                continue
            want = p["sig"] if p else (prior[-1] if prior else sig(old))
            if not holds(objs[key], want):
                log.info("%s was placed again here (in its default states): put back in %s", objs[key]["t"],
                         want.strip("|") or "normal")
                self.put_states(site, key, objs[key], want)
        return [a for a in ended if a[6] not in placed]

    def put_states(self, site: int, hk: str, here: dict, want: str):
        """Put the object here in the states of that sig (no server version of it: what it should be anyway). Like
        any state put on an object, what its read shows for a moment after isn't sent (QUIET_AFTER)."""
        if not self.objects_live(site):
            return
        hx, hy, hz = here["p"]
        states, _, ch = want.rpartition("|")
        self.obj_applied.append((time.time(), here["t"], hx, hy, hz, want, hk, False))
        self.obj_landed.discard(hk)
        self.mail.op("set_states", site=int(site), table=here["t"], x=hx, y=hy, z=hz,
                     states=[s for s in states.split(",") if s], channel=ch)
        self.obj_counts["applied"] += 1

    def just_applied(self, key: str, o: dict, objs: dict) -> bool:
        """The change is (or follows) the partner's state put on this very object a moment ago (the one set_states
        hit: the nearest of that kind to where it was put), so it isn't sent back. Another object of the same kind
        close by is still sent."""
        for _, t, ax, ay, az, *_ in self.obj_applied:
            if t == o["t"] and nearest(objs, t, ax, ay, az) == key:
                return True
        return False

    def expected_sig(self, here_key: str):
        """The object's states as they are or are about to be: what was put on it a moment ago, else the read."""
        now = time.time()
        for when, _t, _x, _y, _z, s, k, *_ in reversed(self.obj_applied):
            if k == here_key and now - when < QUIET_AFTER:
                return s
        here = self.obj_last.get(here_key)
        return sig(here) if here is not None else None

    def settle_objects(self, site: int, ended: list):
        """The quiet time of states put on these objects is over: each should be in the server's newest state for it
        now. What was read on it meanwhile wasn't sent (the game's reaction can't be told from the player's change),
        so if it still isn't:
        - it was put in a partner's change as that came, and a read showed it took: what it is now was done here after
          that, and is sent (the last change wins, on both PCs);
        - otherwise (the game didn't take it; or it was this game being brought to the server's states - the first
          read, a snapshot... - when the save's own old states don't count): the server's state is put on it again.
        Once an object at most every settle_span(): a game that keeps putting an object back is left after that,
        instead of the two PCs turning it to and fro (what the partner's settle sent, answered here). A change made
        here after a partner's own change took ("genuine": not sent by a settle) is sent whenever: left, the two PCs
        would stay apart for good."""
        now, span = time.time(), settle_span()
        self.obj_settled = {k: t for k, t in self.obj_settled.items() if now - t < span}
        prefix, groups = f"obj:{site}:", None
        for hk, a in {a[6]: a for a in ended}.items():         # (the newest of each object's ended ones)
            if any(b[6] == hk for b in self.obj_applied):
                continue                                 # (a newer state put on it is still in its quiet time)
            landed = hk in self.obj_landed
            self.obj_landed.discard(hk)
            o, mine = self.obj_last.get(hk), prefix + hk
            if o is None or mine in self.obj_pending or self.superseded(mine):
                continue                                 # (gone; or a change of mine on its way decides it)
            if groups is None:
                groups = self.obj_groups(site, self.obj_last)
            if not groups.get(hk):
                continue                                 # (the server has nothing for it any more)
            v, key, item = groups[hk][-1]
            want = item.get("value") or {}
            if holds(o, sig(want)) or self.superseded(key) or self.seen(key) < v:
                continue                # (as the server has it; or a newer state not put on it yet: reconcile_objects)
            if hk in self.obj_settled and not (a[7] == "genuine" and landed):
                # (a settle answering a settle, or a state the game won't keep: the to and fro stops here. A partner's
                # own change that took, changed here after, is always a change of mine: never left apart)
                log.info("%s keeps going back to %s here after the server's state is put on it: left as it is",
                         o["t"], sig(o) or "-")
                continue
            self.obj_settled[hk] = now
            if a[7] and landed:
                log.info("%s changed here right after the partner's state was put on it: sent now", o["t"])
                self.push_object(site, hk, want, o, settle=True)
            elif self.objects_live(site):
                log.info("%s isn't in the server's state after it was put on it (%s): put on it again", o["t"],
                         sig(o) or "-")
                self.apply_object(key, item, hk)

    # ---------------------------------------------------------------- sending
    def push_object(self, site: int, key: str, before: dict, o: dict, moved=(), settle: bool = False):
        """Send the object's states as my change (moved: the server's keys for where it stood before it moved here,
        removed once this is confirmed; settle: sent by settle_objects, which the value says - "settle": true - so the
        partner's settle doesn't answer it in turn more than once: sig() and 0.9.37 companions ignore it)."""
        k = f"obj:{site}:{key}"
        log.info("%s here changed: %s -> %s", o["t"], sig(before) or "-", sig(o) or "-")
        self.obj_counts["sent"] += 1
        self.tele.rec("note", text=f"object {o['t']} {sig(before)} -> {sig(o)}")
        old = self.obj_pending.get(k)
        connected = bool(self.writer and self.me_id)
        p = self.obj_pending[k] = {
            "o": dict(value_of(o), settle=True) if settle else value_of(o), "sig": sig(o), "tries": 0, "next": 0.0,
            "busy": False,
            # the version this change was made on (an older unconfirmed change of mine never made it: its base stays)
            "base": old["base"] if old else version(self.shared.get(k)),
            # the first send simply wins (the server orders it); a resend, or a change made while the server couldn't
            # be reached, goes on that version only, so a partner's change made meanwhile isn't overwritten
            "use_base": not connected or bool(old and old["use_base"]),
            # the newest version I'd seen, of that server's state, when it was made (revive_kept: newer ones count)
            "seq": int(getattr(self, "shared_seq", 0) or 0), "scope": self.obj_scope(),
            "moved": list(moved) + [m for m in (old or {}).get("moved") or [] if m not in moved],
            # made before anything of the server was known in this run (no snapshot yet: the program started while it
            # couldn't be reached): base, seq and scope above mean nothing; the first snapshot gives them
            # (on_objects_snapshot) and this change wins, as one made live would - unless the server's state of that
            # object came after it ("made": when, by this PC's clock; blind_beaten)
            "blind": getattr(self, "shared_conn", None) is None, "made": time.time()}
        if connected:
            p["busy"] = True
            self.shared_task(self.send_object(k, p))

    async def send_object(self, k: str, p: dict):
        p["busy"] = True
        p["tries"] += 1
        try:
            out = await self.share_ex(k, p["o"], base=p["base"] if p["use_base"] else None)
        finally:
            p["busy"] = False
        if self.obj_pending.get(k) is not p:
            # (changed again since, or the world was left: kept for the next first read - unless it's done with)
            if out in ("ok", "rejected") and self.obj_kept.get(k) is p:
                del self.obj_kept[k]
            return
        if out == "ok":
            del self.obj_pending[k]
            self.drop_aliases(k, p.get("moved") or ())
            return
        if out == "refused":
            cur = self.shared.get(k)
            if cur is None or cur.get("by") == self.me_id:
                # the server has nothing there (a new or started-over server), or a change of mine I didn't know had
                # landed: mine goes on what it has
                p["base"], p["next"] = version(cur), 0.0
                return
            log.info("%s: the partner changed it meanwhile, theirs counts", p["o"].get("t"))
            del self.obj_pending[k]
            self.recheck_object(k)
            return
        if out == "rejected":
            del self.obj_pending[k]
            self.recheck_object(k)
            return
        p["use_base"] = True
        if out == "offline":
            p["tries"] -= 1
            p["next"] = time.time() + READ_EVERY
        else:
            p["next"] = time.time() + min(RETRY_FIRST * 2 ** (p["tries"] - 1), RETRY_MAX)
            log.info("the server didn't confirm %s's change (try %d): sending it again", p["o"].get("t"), p["tries"])

    def retry_objects(self):
        """Changes of mine the server hasn't confirmed: sent again (after a reconnect, or when their time comes)."""
        if not (self.writer and self.me_id):
            return
        now = time.time()
        for k, p in list(self.obj_pending.items()):
            if p["busy"] or now < p["next"]:
                continue
            cur = self.shared.get(k)
            if cur and cur.get("by") == self.me_id and sig(cur.get("value") or {}) == p["sig"]:
                del self.obj_pending[k]                  # (the server has it: its word went down with a connection)
                continue
            hk = k.split(":", 2)[2]
            # (as it is, or is about to be: a moved or re-placed object of mine may not show it yet)
            if self.obj_last.get(hk) is None or self.expected_sig(hk) != p["sig"] or p["tries"] >= TRIES:
                if p["tries"] >= TRIES:
                    log.warning("the server never confirmed %s's change: its state counts", p["o"].get("t"))
                del self.obj_pending[k]                  # (or it isn't like that here any more: nothing to send)
                self.recheck_object(k)
                continue
            p["busy"] = True                             # (from now: one send of it at a time)
            self.shared_task(self.send_object(k, p))

    def drop_aliases(self, k: str, moved=()):
        """My change of an object is the server's now: other keys of the server that are this same object here (it
        moved, or the partner's save has it a little apart) are older states of it - removed, so they don't pile up.
        Each removal is made on the version it saw: one changed meanwhile stays. moved: the keys of where it stood
        before it moved here, removed too if they're no object here any more."""
        site, hk = k.split(":")[1], k.split(":", 2)[2]
        if str(self.obj_site) != site or hk not in self.obj_last:
            return
        mine = version(self.shared.get(k))
        groups = self.obj_groups(int(site), self.obj_last)
        old = list(groups.get(hk) or [])
        old += [e for g, es in groups.items() if isinstance(g, tuple) for e in es if e[1] in moved]
        for v, key, _item in old:
            if key != k and v < mine and key not in self.obj_pending:
                log.debug("%s is %s too (older): removed", key, k)
                self.shared_task(self.share(key, None, base=v))

    # ---------------------------------------------------------------- applying
    def on_shared_object(self, key: str, item: dict | None, snapshot: bool):
        if not item or snapshot:
            return              # (a removed key changes nothing here; a snapshot is applied whole: on_objects_snapshot)
        v = version(item)
        if self.superseded(key, item):
            if item.get("by") == self.me_id:
                self.mark_seen(key, v)                   # (an older one of mine: the newer one counts)
            return
        if self.seen(key) >= v:
            return
        site = key.split(":")[1]
        if not self.objects_live(site):
            return                                       # (applied once this lot is read in a running game)
        if item.get("by") != self.me_id:
            # a change of mine to that object that never reached the server (waiting to go again) is older than this
            # one, which did: this one counts
            o = item.get("value") or {}
            try:
                hk = nearest(self.obj_last, str(o["t"]), *(float(c) for c in o["p"]))
            except (KeyError, TypeError, ValueError):
                hk = None
            mine = f"obj:{site}:{hk}"
            if hk is not None and mine in self.obj_pending and not self.superseded(mine):
                del self.obj_pending[mine]
        # The newest version of the key, mine included: if a partner's older one was put on the object after I sent
        # mine, this puts mine back (nothing happens when the object is like that already). live: whether it's another
        # PC's settle (settle_objects answers that at most once a span) or a player's own change
        self.apply_object(key, item, live="settle" if (item.get("value") or {}).get("settle") else "genuine")

    def on_objects_snapshot(self):
        """The whole shared state came (joining, reconnecting): the lot's objects are brought to it at once (the newest
        item of each object, not every item in turn), and my unconfirmed changes go again on the version they saw.
        One made before anything of the server was known in this run ("blind") goes on the server's current version -
        it wins, as a change made live would - unless the server's newest state of that object came after it
        (blind_beaten): then that one counts, as it would have live, and mine is dropped."""
        groups = self.obj_groups(self.obj_site, self.obj_last) if self.obj_site is not None else {}
        ahead = self.server_ahead()
        for store in (self.obj_pending, self.obj_kept):
            for k, p in list(store.items()):
                if p.get("blind"):
                    if self.blind_beaten(k, p, groups, ahead):
                        del store[k]
                        log.info("my change of %s made before the server could be reached is older than the server's "
                                 "state of it: that counts", (p.get("o") or {}).get("t"))
                        continue
                    p.update(base=version(self.shared.get(k)), seq=int(getattr(self, "shared_seq", 0) or 0),
                             scope=self.obj_scope(), blind=False)
                p["use_base"] = True
        if self.obj_site is not None:
            self.reconcile_objects(self.obj_site, full=True)

    def server_ahead(self) -> float:
        """How far the server's clock is at least ahead of this PC's, by what it has (s; 0: not known to be ahead).
        Every item it holds was set before now by its clock, so one stamped later than now by mine shows the gap."""
        stamps = [_at(i) for i in self.shared.values() if isinstance(i, dict)]
        return max([0.0] + [s - time.time() for s in stamps])

    def blind_beaten(self, k: str, p: dict, groups: dict, ahead: float) -> bool:
        """The server's newest state of the object a blind change of mine is about (by the server's order: the newest
        version among its keys for that object - groups, or for one kept through leaving the world, the keys of that
        kind within NEAR of where it was) is a partner's set after my change was made: its "at" (the server's clock)
        is later than when I made mine (this PC's clock, moved by what the server is at least ahead: server_ahead).
        A PC clock running fast still favours mine; an item without a time doesn't beat it."""
        site, hk = k.split(":")[1], k.split(":", 2)[2]
        if self.obj_site is not None and str(self.obj_site) == site and hk in self.obj_last:
            items = [e[2] for e in groups.get(hk) or []]
        else:
            o, items = p.get("o") or {}, []
            try:
                x, y, z = (float(c) for c in o["p"])
            except (KeyError, TypeError, ValueError):
                return False
            for key, item in self.shared.items():
                v = item.get("value") if isinstance(item, dict) else None
                if not key.startswith(f"obj:{site}:") or not isinstance(v, dict) or v.get("t") != o.get("t"):
                    continue
                try:
                    if sum((float(a) - b) ** 2 for a, b in zip(v["p"], (x, y, z))) < NEAR ** 2:
                        items.append(item)
                except (KeyError, TypeError, ValueError):
                    continue
        if self.shared.get(k):
            items.append(self.shared[k])
        newest = max(items, key=version, default=None)
        me = self.me_id
        if not newest or (me and newest.get("by") == me):
            return False
        return _at(newest) > float(p.get("made") or 0) + ahead

    def recheck_object(self, k: str):
        site = k.split(":")[1]
        if self.objects_live(site):
            self.reconcile_objects(int(site), only={k.split(":", 2)[2]})

    def obj_groups(self, site, objs: dict) -> dict:
        """The server's items for this lot by the object here each one is (set_states' rule: the nearest of that kind
        within a metre) -> [(v, key, item)] oldest first; one that's no object here under ("none", key)."""
        prefix = f"obj:{site}:"
        kinds: dict[str, dict] = {}
        for k, h in objs.items():
            kinds.setdefault(h["t"], {})[k] = h
        groups: dict = {}
        for key, item in list(self.shared.items()):
            if not key.startswith(prefix) or not isinstance(item, dict):
                continue
            o = item.get("value") or {}
            try:
                x, y, z = (float(c) for c in o["p"])
                t = str(o["t"])
            except (KeyError, TypeError, ValueError):
                self.mark_seen(key, version(item))       # (nothing that can be applied)
                continue
            hk = nearest(kinds.get(t) or {}, t, x, y, z)
            groups.setdefault(hk if hk is not None else ("none", key), []).append((version(item), key, item))
        for g in groups.values():
            g.sort(key=lambda e: e[0])
        return groups

    def reconcile_objects(self, site, full: bool = False, fresh=(), only=None):
        """Bring objects here to the server's states. full: every object (a first read, a new snapshot: the save's
        states, or what was applied before, don't count); otherwise objects with a version not applied yet, objects
        that showed up since the last read (fresh), and `only` these. An object with a change of mine on its way is
        left to that change."""
        if not self.objects_live(site):
            if full and self.obj_site is not None and str(self.obj_site) == str(site):
                self.obj_full_due = True                 # (the next read in a running game does it)
            return
        if full:
            self.obj_full_due = False
        prefix = f"obj:{site}:"
        if not (full or fresh or only) and not any(k.startswith(prefix) and self.seen(k) < version(i)
                                                   for k, i in self.shared.items()):
            return
        todo = []
        for hk, entries in self.obj_groups(site, self.obj_last).items():
            if isinstance(hk, tuple):
                continue        # (no such object here, or it hasn't streamed in yet: tried again when it shows up)
            if only is not None and hk not in only:
                continue
            v, key, item = entries[-1]
            mine = prefix + hk
            if mine in self.obj_pending or self.superseded(mine) or self.superseded(key):
                continue
            for ev, ek, _ in entries[:-1]:
                self.mark_seen(ek, ev)                   # (older states of the same object: done with)
            if full or hk in fresh or only is not None or self.seen(key) < v:
                todo.append((v, key, item, hk))
        for v, key, item, hk in sorted(todo, key=lambda e: e[0]):
            self.apply_object(key, item, hk)

    def apply_object(self, key: str, item: dict, here_key: str | None = None, live: bool = False) -> bool:
        """Put the object here in the item's states (nothing when it's like that already, or about to be). False: no
        such object here (yet). live: a partner's change as it came (settle_objects: a change made here after it is
        sent, not undone)."""
        o, v = item.get("value") or {}, version(item)
        try:
            x, y, z = (float(c) for c in o["p"])
            table = str(o["t"])
        except (KeyError, TypeError, ValueError):
            self.mark_seen(key, v)
            return True
        hk = here_key if here_key is not None else nearest(self.obj_last, table, x, y, z)
        here = self.obj_last.get(hk) if hk is not None else None
        if here is None:
            return False
        want = sig(o)
        if self.expected_sig(hk) == want:
            self.mark_seen(key, v)
            return True
        hx, hy, hz = here["p"]          # (where it is here: set_states then picks exactly this one)
        log.info("%s's %s: %s", item.get("by_name") or "partner", table, want.strip("|") or "normal")
        self.obj_applied.append((time.time(), table, hx, hy, hz, want, hk, live))
        self.obj_landed.discard(hk)                      # (whether this one takes: the next reads tell)
        self.mail.op("set_states", site=int(key.split(":")[1]), table=table, x=hx, y=hy, z=hz,
                     states=list(o.get("s") or []), channel=o.get("ch") or "")
        self.obj_counts["applied"] += 1
        self.tele.rec("note", text=f"object {table} set to {want} (from {item.get('by_name')})")
        self.mark_seen(key, v)
        return True
