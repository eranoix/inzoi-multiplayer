"""The state of the home's objects (mixed into the Companion; the in-game half is read_states/set_states in
lua/B2/Main.lua): on/off, open/closed, the TV's channel... whatever the game keeps in an object's state component.

Every few seconds the controller reads the states of every object on our home lot. An object whose states changed here
(and not because the partner's change was just applied to it) goes to the server as the shared key
obj:<lot>:<object key> = {"t": table id, "p": [x, y, z], "s": ["group:state", ...], "ch": channel}; the server hands it
to everyone, and each PC puts that object in its game (same kind, within a metre) in those states, through the game's
own state change (its lights, screens and sounds follow). The Slot groups (who sits where) aren't synced: the
skeletons already show that.
"""
import asyncio
import hashlib
import logging
import time

log = logging.getLogger("objsync")

READ_EVERY = 3.0        # s between reads of the lot's object states
QUIET_AFTER = 15.0      # s after applying the partner's state to an object during which its own changes aren't sent
NEAR = 100.0            # cm: the same object in both saves


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
        objs[obj_key(f[1], x, y, z)] = {"t": f[1], "p": [x, y, z], "s": states, "ch": ch}
    return site, objs


def sig(o: dict) -> str:
    return ",".join(o.get("s") or []) + "|" + (o.get("ch") or "")


class ObjSync:
    def objsync_init(self):
        self.obj_site = None          # the lot the last read was of
        self.obj_last: dict[str, dict] = {}       # key -> the object as last read
        self.obj_applied: list[tuple] = []        # (when, table, x, y, z, sig): the partner's states put on objects here
        self.obj_asked = 0.0
        self.obj_counts = {"sent": 0, "applied": 0}

    # ---------------------------------------------------------------- reading
    async def objsync_loop(self):
        while True:
            await asyncio.sleep(READ_EVERY)
            try:
                if not (self.writer and self.me_id and self.world == "play" and self.mod.get("state") == "running"
                        and self.mp_active()):
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
        if site != self.obj_site:                     # first read of this lot: what it is now is the starting point
            self.obj_site, self.obj_last = site, objs
            log.info("object states of lot %d: %d objects with a state", site, len(objs))
            self.apply_shared_objects(site)
            return
        now = time.time()
        self.obj_applied = [a for a in self.obj_applied if now - a[0] < QUIET_AFTER]
        for key, o in objs.items():
            before = self.obj_last.get(key)
            if before is None or sig(before) == sig(o):
                continue
            if self.just_applied(o):
                continue
            item = self.shared.get(f"obj:{site}:{key}")
            if item and sig(item.get("value") or {}) == sig(o):
                continue
            log.info("%s here changed: %s -> %s", o["t"], sig(before) or "-", sig(o) or "-")
            self.obj_counts["sent"] += 1
            self.tele.rec("note", text=f"object {o['t']} {sig(before)} -> {sig(o)}")
            asyncio.get_running_loop().create_task(self.share(f"obj:{site}:{key}", o))
        self.obj_last = objs

    def just_applied(self, o: dict) -> bool:
        """The change is (or follows) the partner's state put on this object a moment ago: not sent back."""
        x, y, z = o["p"]
        for _, t, ax, ay, az, _s in self.obj_applied:
            if t == o["t"] and (ax - x) ** 2 + (ay - y) ** 2 + (az - z) ** 2 < NEAR ** 2:
                return True
        return False

    # ---------------------------------------------------------------- applying
    def on_shared_object(self, key: str, item: dict | None, snapshot: bool):
        if not item:
            return
        v = int(item.get("v") or 0)
        if item.get("by") == self.me_id:
            self.mark_seen(key, v)
            return
        if self.seen(key) >= v:
            return
        site = key.split(":")[1]
        if self.obj_site is None or str(self.obj_site) != site:
            return                                   # (applied once this lot has been read: apply_shared_objects)
        self.apply_object(key, item)

    def apply_shared_objects(self, site: int):
        for key, item in list(self.shared.items()):
            if key.startswith(f"obj:{site}:") and item.get("by") != self.me_id and self.seen(key) < int(item.get("v") or 0):
                self.apply_object(key, item)

    def apply_object(self, key: str, item: dict):
        o = item.get("value") or {}
        try:
            x, y, z = (float(c) for c in o["p"])
            table = str(o["t"])
        except (KeyError, TypeError, ValueError):
            self.mark_seen(key, int(item.get("v") or 0))
            return
        here = next((h for h in self.obj_last.values() if h["t"] == table and
                     (h["p"][0] - x) ** 2 + (h["p"][1] - y) ** 2 + (h["p"][2] - z) ** 2 < NEAR ** 2), None)
        if here is not None and sig(here) == sig(o):
            self.mark_seen(key, int(item.get("v") or 0))
            return
        log.info("%s's %s: %s", item.get("by_name") or "partner", table, sig(o) or "normal")
        self.obj_applied.append((time.time(), table, x, y, z, sig(o)))
        self.mail.op("set_states", site=int(key.split(":")[1]), table=table, x=x, y=y, z=z,
                     states=list(o.get("s") or []), channel=o.get("ch") or "")
        self.obj_counts["applied"] += 1
        self.tele.rec("note", text=f"object {table} set to {sig(o)} (from {item.get('by_name')})")
        if here is not None:
            here["s"], here["ch"] = list(o.get("s") or []), o.get("ch") or ""
        self.mark_seen(key, int(item.get("v") or 0))
