"""The family home, the same in both saves (mixed into the Companion, like couple.Couple).

Each player plays their own save, so what one of them builds or rearranges in the shared home only exists in their
save. This keeps the two in step, object by object (furniture and decoration; walls and floors come later):

- While a player is in build mode on their home lot, the objects there are read every few seconds
  (build.scan_objects: table id, site-relative position, yaw, floor). When they leave build mode, the last reading goes
  to the partner: {"t":"event","kind":"couple","data":{"type":"house","site","site_name","objects":[...]}}.
- The partner's program compares it with the same lot in its own save (same map, same lot id): objects of the same
  kind within 10 cm and 5 degrees are the same object; the rest is new (to place) or gone (to remove). A popup asks
  "<name> changed the house: N new, M removed - apply?" (or "always yes": key "house").
- Applying enters build mode on that lot (build.enter_mode, the game pauses), removes and places (building.place_object
  in the same site coordinates, snapping chairs to tables like the game's magnet), and leaves build mode (which saves
  the lot). Leaving build mode after applying doesn't send the house back (it's the partner's version already).
"""
import asyncio
import logging
import math
import secrets
import time

log = logging.getLogger("housesync")

SCAN_EVERY = 4            # s between readings while in build mode
SAME_CM, SAME_DEG = 10.0, 5.0
MAX_OBJECTS = 2000


def object_key(o: dict) -> dict:
    loc = o.get("location") or {}
    return {"table_id": str(o.get("table_id") or ""), "x": round(float(loc.get("x") or 0), 1),
            "y": round(float(loc.get("y") or 0), 1), "z": round(float(loc.get("z") or 0), 1),
            "yaw": round(float(o.get("yaw") or 0), 1), "floor": int(o.get("floor_level") or 0)}


def same(a: dict, b: dict) -> bool:
    d_yaw = abs((a["yaw"] - b["yaw"] + 180) % 360 - 180)
    return (a["table_id"] == b["table_id"] and a["floor"] == b["floor"] and d_yaw <= SAME_DEG
            and math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= SAME_CM and abs(a["z"] - b["z"]) <= SAME_CM)


def diff(theirs: list[dict], mine: list[dict]) -> tuple[list[dict], list[dict]]:
    """-> (to place: their objects missing here, to remove: my objects (with entity_id) they don't have)."""
    left = list(mine)
    place = []
    for o in theirs:
        hit = next((m for m in left if same(o, m)), None)
        if hit:
            left.remove(hit)
        else:
            place.append(o)
    return place, left


class HouseSync:
    def house_init(self):
        self.build_site = None        # {"site_id", "site": entity, "objects": [...]} while in build mode
        self.applying_house = False   # (leaving build mode after applying the partner's house: not sent back)

    def scan_site(self) -> list[dict]:
        out, offset = [], 0
        while offset < MAX_OBJECTS:
            page = self.game.call("build.scan_objects", Limit=500, Offset=offset) or {}
            objs = page.get("objects") or []
            for o in objs:
                k = object_key(o)
                k["entity_id"] = o.get("entity_id")
                if k["table_id"]:
                    out.append(k)
            offset += len(objs)
            if len(objs) < 500:
                break
        return out

    async def house_tick(self):
        """Called every couple of seconds while in the world: follows build mode on my home lot."""
        if self.world == "out" or not self.my_entity:
            return
        try:
            st = await self.g(self.game.call, "build.get_mode_state") or {}
        except Exception:                                # noqa: BLE001
            return
        self.in_build_mode = bool(st.get("in_build_mode"))
        building = self.in_build_mode and st.get("phase") == "ready"
        if building:
            site = st.get("site_entity_id")
            cached = self.__dict__.get("_home_cache")
            if not cached or time.time() - cached[1] > 30:
                cached = self._home_cache = (await self.g(self.my_home_site), time.time())
            home = cached[0]
            if site and home and int(site) == int(home):
                b = self.build_site
                if not b or time.time() - b.get("at", 0) > SCAN_EVERY:
                    try:
                        objs = await self.g(self.scan_site)
                        self.build_site = {"site_id": st.get("site_id"), "site": int(site), "objects": objs,
                                           "at": time.time()}
                    except Exception as e:                # noqa: BLE001
                        log.debug("reading the house: %s", e)
            return
        b, self.build_site = self.build_site, None
        if b and not self.applying_house and st.get("phase") in ("none", None):
            await self.send_house(b)
        if not st.get("in_build_mode"):
            self.applying_house = False

    async def send_house(self, b: dict):
        r = self.partner(None) if hasattr(self, "partner") else None
        if not r or not (self.writer and self.me_id):
            return
        objs = [{k: o[k] for k in ("table_id", "x", "y", "z", "yaw", "floor")} for o in b["objects"]]
        await self.send({"t": "event", "kind": "couple", "to": r.pid,
                         "data": {"type": "house", "id": secrets.token_hex(6), "site": b["site"],
                                  "site_id": b["site_id"], "objects": objs}})
        log.info("sent the house to %s (%d objects)", r.name, len(objs))

    def on_house(self, frm: str, data: dict):
        """The partner left build mode on our home: compare, and ask before changing anything here."""
        r = self.remotes.get(frm)
        name = r.name if r else "Your partner"
        objs = [o for o in (data.get("objects") or [])[:MAX_OBJECTS] if isinstance(o, dict) and o.get("table_id")]
        rid = secrets.token_hex(6)
        self.prune_requests()
        for q in [q for q in self.requests.values() if q.get("action") == "house"]:
            self.requests.pop(q["id"], None)             # (a newer version of the house replaces an older one)
        self.requests[rid] = {"id": rid, "from": name, "from_id": frm, "action": "house", "at": time.time(),
                              "local": True, "house": {"site": data.get("site"), "site_id": data.get("site_id"),
                                                       "objects": objs}, "label": None}
        asyncio.get_running_loop().create_task(self.prepare_house(rid))

    async def prepare_house(self, rid: str):
        """Work out what differs (needs the lot: my home must be that lot) and put it in the popup's text."""
        q = self.requests.get(rid)
        if not q:
            return
        h = q["house"]
        home = await self.g(self.my_home_site)
        if not home or int(home) != int(h.get("site") or -1):
            self.requests.pop(rid, None)
            log.info("%s's house is lot %s, mine is %s: not the same home, nothing to do", q["from"], h.get("site"), home)
            return
        try:
            mine = await self.g(self.scan_site)
        except Exception as e:                           # noqa: BLE001 (not inside the house: compare when applying)
            log.debug("reading my house: %s", e)
            mine = None
        if mine is not None:
            place, remove = diff(h["objects"], mine)
            if not place and not remove:
                self.requests.pop(rid, None)
                log.info("%s's house is the same as mine", q["from"])
                return
            q["label"] = f"{len(place)} new, {len(remove)} removed"
        self.system_line(f"{q['from']} changed the house" + (f": {q['label']}" if q["label"] else ""))
        if self.always_yes("house", None):
            await self.couple_respond(rid, True)

    async def apply_house(self, q: dict) -> dict:
        """Enter build mode on the lot, remove and place what differs, leave build mode (it saves the lot)."""
        h = q["house"]
        call = self.game.call
        self.applying_house = True
        try:
            await self.g(call, "build.enter_mode", site_id=str(h.get("site_id") or ""))
            for _ in range(120):
                await asyncio.sleep(0.5)
                st = await self.g(call, "build.get_mode_state") or {}
                if st.get("phase") == "ready":
                    break
            else:
                return {"error": "build mode didn't open", "code": "build_mode"}
            mine = await self.g(self.scan_site)
            place, remove = diff(h["objects"], mine)
            failed = 0
            for o in remove:
                try:
                    await self.g(call, "build.remove_object", entity_id=str(o["entity_id"]))
                except Exception as e:                   # noqa: BLE001
                    failed += 1
                    log.debug("couldn't remove %s: %s", o["table_id"], e)
            for o in place:
                try:
                    await self.g(call, "building.place_object", object_table_id=o["table_id"], X=o["x"], Y=o["y"],
                                 Z=o["z"], floor_level=o["floor"], Yaw=o["yaw"])
                except Exception as e:                   # noqa: BLE001
                    failed += 1
                    log.debug("couldn't place %s: %s", o["table_id"], e)
            await self.g(call, "build.exit_mode")
            for _ in range(60):
                await asyncio.sleep(0.5)
                st = await self.g(call, "build.get_mode_state") or {}
                if st.get("phase") == "none":
                    break
            log.info("applied %s's house: %d placed, %d removed, %d failed", q["from"], len(place), len(remove), failed)
            self.system_line(f"House updated: {len(place)} new, {len(remove)} removed" +
                             (f" ({failed} couldn't be changed)" if failed else ""))
            return {"state": "done", "placed": len(place), "removed": len(remove), "failed": failed}
        except Exception as e:                           # noqa: BLE001
            log.warning("applying the house failed: %s", e)
            return {"state": "failed", "message": str(e)}
