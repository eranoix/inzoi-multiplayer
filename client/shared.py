"""The server's shared state, the companion's side (mixed into the Companion; the server keeps it: inzoi_mp_server.py
on_set).

What's true for everyone lives on the server under a key, with a version: nobody applies a change that didn't come
back from the server, so both PCs apply the same values in the same order, and whoever joins (or reconnects, or
restarts the game) gets the whole state in the server's welcome and brings their game to it.

    house:<lot>:live   the couple's home as the game's commands read it (objects, walls, storey heights)
    house:<lot>:exact  the same home as the game's own saved property (paint, floors, roofs, customization: lotsync.py)
    family             {"together": true, "head": <player id>, "home_site": <lot>, "members": [ids]}

Each value carries who set it; a PC never re-applies its own change, and remembers the last version it applied of each
key (cfg["shared_seen"]) so a restart doesn't apply it again. Changes made while the server couldn't be reached are kept
as "dirty" (cfg["house_dirty"]) and pushed when it can, instead of being overwritten by the server's older version.
"""
import asyncio
import logging
import time

log = logging.getLogger("shared")

RECONCILE_EVERY = 5.0


class SharedState:
    def shared_init(self):
        self.shared: dict[str, dict] = {}
        self.shared_seq = 0
        self.family_tries: dict[str, float] = {}

    # ---------------------------------------------------------------- the wire
    def on_shared_snapshot(self, items: dict | None, seq: int = 0):
        self.shared = {k: v for k, v in (items or {}).items() if isinstance(v, dict)}
        self.shared_seq = seq
        log.info("shared state from the server: %s", ", ".join(f"{k} v{v.get('v')}" for k, v in self.shared.items())
                 or "nothing yet")
        for key, item in list(self.shared.items()):
            self.shared_changed(key, item, snapshot=True)

    def on_shared_msg(self, m: dict):
        key, item = str(m.get("key") or ""), m.get("item")
        if not key:
            return
        if item:
            self.shared[key] = item
        else:
            self.shared.pop(key, None)
        self.shared_seq = max(self.shared_seq, int(m.get("v") or 0))
        self.tele.rec("note", text=f"shared {key} v{m.get('v')} by {(item or {}).get('by_name')}"
                                   f"{' (removed)' if not item else ''}")
        self.shared_changed(key, item, snapshot=False)

    def on_shared_refused(self, m: dict):
        cur = m.get("current") or {}
        log.info("the server kept %s v%s (my change was made on an older version)", m.get("key"), cur.get("v"))
        if cur:
            self.shared[m["key"]] = cur
            self.shared_changed(m["key"], cur, snapshot=False)

    async def share(self, key: str, value, base: int | None = None) -> bool:
        """Ask the server to set a shared key. False: not connected (the caller keeps it to push later)."""
        if not (self.writer and self.me_id):
            return False
        msg = {"t": "set", "key": key, "value": value}
        if base is not None:
            msg["base"] = base
        try:
            await self.send(msg)
        except (OSError, ConnectionError):
            return False
        return True

    def seen(self, key: str) -> int:
        return int((self.cfg.get("shared_seen") or {}).get(key) or 0)

    def mark_seen(self, key: str, v: int):
        seen = self.cfg.setdefault("shared_seen", {})
        if int(seen.get(key) or 0) != int(v or 0):
            seen[key] = int(v or 0)
            self.save_cfg()

    def shared_changed(self, key: str, item: dict | None, snapshot: bool):
        try:
            if key.startswith("house:") and key.endswith(":live"):
                self.on_shared_house_live(key, item, snapshot)
            elif key.startswith("house:") and key.endswith(":exact"):
                self.on_shared_house_exact(key, item, snapshot)
            elif key == "family":
                self.on_shared_family(item, snapshot)
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
        my family in the head's home), whatever happened to this save meanwhile."""
        item = self.shared.get("family") or {}
        fam = item.get("value") or {}
        if not fam.get("together") or self.world == "out" or not self.my_entity or not self.mp_active():
            return
        for pid in fam.get("members") or []:
            r = self.remotes.get(pid)
            if pid == self.me_id or not r or pid not in self.puppet_ids or r.hidden:
                continue
            if time.time() - self.family_tries.get(pid, 0) < 120:
                continue
            together = await self.g(self.family_together, r, fam)
            if together:
                continue
            self.family_tries[pid] = time.time()
            home = None if fam.get("head") == self.me_id else fam.get("home_site")
            log.info("the shared family says %s and I live together, but this save doesn't: making it so", r.name)
            self.tele.rec("note", text=f"family: making this save match the shared family (with {r.name})")
            try:
                res = await self.g(self.family_apply, "movein", pid, home)
                log.info("family made to match: %s", res)
            except Exception as e:                       # noqa: BLE001
                log.warning("couldn't make the family match: %s", e)

    def family_together(self, r, fam: dict) -> bool:
        """In this save: their Zoi is in my household and (if the head isn't me) we live on the head's lot."""
        if not self.family_zoi(r):
            return False
        if fam.get("head") != self.me_id and fam.get("home_site"):
            return str(self.my_home_site()) == str(fam["home_site"])
        return True

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
