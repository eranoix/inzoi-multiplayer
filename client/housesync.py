"""The family home, the same in both saves (mixed into the Companion, like couple.Couple).

Each player plays their own save, so what one of them builds or rearranges in the shared home only exists in their
save. This keeps the two in step: the walls (segments per floor, and each storey's wall height) and the objects
(furniture, decoration, doors and windows). Wall and floor paint/material can't be read through the game's commands
(only written), so those stay as each save has them:

- While a player is in build mode on their home lot, the lot is read every few seconds (build.scan_objects: table
  id, site-relative position, yaw, floor; build.scan_walls: segment ends per floor; build.scan_floors: heights). When they leave build mode, the last reading goes
  to the partner: {"t":"event","kind":"couple","data":{"type":"house","site","site_name","objects":[...]}}.
- The partner's program compares it with the same lot in its own save (same map, same lot id): objects of the same
  kind within 10 cm and 5 degrees are the same object; the rest is new (to place) or gone (to remove). A wall is
  there when every point along it lies on a wall of the other save (so one long wall and two short ones that make it
  up are the same); walls not covered are deleted or built. A popup asks
  "<name> changed the house: N new, M removed - apply?" (or "always yes": key "house").
- Applying enters build mode on that lot (build.enter_mode, the game pauses), removes the objects that are gone,
  deletes and builds walls (building.construct_wall, is_delete for deleting), sets storey heights, reads the lot again
  and places what's still missing (building.place_object; doors and windows snap onto the new walls), and leaves build
  mode (which saves the lot). Leaving build mode after applying doesn't send the house back (it's the partner's version already).
"""
import asyncio
import json
import logging
import math
import secrets
import time

log = logging.getLogger("housesync")

SCAN_EVERY = 4            # s between readings while in build mode
SAME_CM, SAME_DEG = 10.0, 5.0
MAX_OBJECTS = 2000
MAX_WALLS = 2000
ON_WALL_CM = 4.0          # a point this close to a wall's centre line is on it (centre lines sit on 25*k+12.5)
WALL_STEP = 12.5          # cm between the points checked along a wall


def object_key(o: dict) -> dict:
    loc = o.get("location") or {}
    return {"table_id": str(o.get("table_id") or ""), "x": round(float(loc.get("x") or 0), 1),
            "y": round(float(loc.get("y") or 0), 1), "z": round(float(loc.get("z") or 0), 1),
            "yaw": round(float(o.get("yaw") or 0), 1), "floor": int(o.get("floor_level") or 0)}


def same(a: dict, b: dict) -> bool:
    d_yaw = abs((a["yaw"] - b["yaw"] + 180) % 360 - 180)
    return (a["table_id"] == b["table_id"] and a["floor"] == b["floor"] and d_yaw <= SAME_DEG
            and math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= SAME_CM and abs(a["z"] - b["z"]) <= SAME_CM)


def wall_key(w: dict) -> dict:
    a, b = w.get("left") or {}, w.get("right") or {}
    return {"floor": int(w.get("floor_level") or 0),
            "a": [round(float(a.get("x") or 0), 1), round(float(a.get("y") or 0), 1)],
            "b": [round(float(b.get("x") or 0), 1), round(float(b.get("y") or 0), 1)]}


def _near(p, w) -> bool:
    (ax, ay), (bx, by) = w["a"], w["b"]
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    k = 0.0 if ll == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / ll))
    return math.hypot(p[0] - ax - dx * k, p[1] - ay - dy * k) <= ON_WALL_CM


def covered(w: dict, walls: list[dict]) -> bool:
    """Every point along w (every WALL_STEP cm) lies on one of `walls` on the same floor."""
    same_floor = [o for o in walls if o["floor"] == w["floor"]]
    (ax, ay), (bx, by) = w["a"], w["b"]
    n = max(1, int(math.hypot(bx - ax, by - ay) / WALL_STEP))
    return all(any(_near((ax + (bx - ax) * i / n, ay + (by - ay) * i / n), o) for o in same_floor)
               for i in range(n + 1))


def wall_diff(theirs: list[dict], mine: list[dict]) -> tuple[list[dict], list[dict]]:
    """-> (to build: their walls missing here, to delete: my walls they don't have). A wall of mine only partly theirs
    is deleted whole; what of it they do have is then built again."""
    delete = [m for m in mine if not covered(m, theirs)]
    kept = [m for m in mine if m not in delete]
    return [t for t in theirs if not covered(t, kept)], delete


def height_diff(theirs: dict, mine: dict, walls: list[dict]) -> list[tuple[int, float, list]]:
    """Storeys whose wall height differs: [(floor, their height, a point on one of their walls there)]."""
    out = []
    for f, h in (theirs or {}).items():
        mh = (mine or {}).get(str(f))
        w = next((w for w in walls if str(w["floor"]) == str(f)), None)
        if mh and w and h.get("wall_height") and abs(float(h["wall_height"]) - float(mh.get("wall_height") or 0)) > 1:
            out.append((int(f), float(h["wall_height"]), w["a"]))
    return out


def house_label(place, remove, walls: int) -> str:
    return f"{len(place)} new, {len(remove)} removed" + (f", {walls} walls" if walls else "")


def _payload(out) -> dict:
    """A scan's answer: JSON, or the game's "Truncated: ..." line followed by the JSON."""
    if isinstance(out, dict) and "_text" in out:
        text = out["_text"]
        return json.loads(text[text.index("{"):]) if "{" in text else {}
    return out or {}


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

    def scan_walls(self) -> list[dict]:
        out = _payload(self.game.call("build.scan_walls", Limit=0))
        return [wall_key(w) for w in (out.get("walls") or [])[:MAX_WALLS] if isinstance(w, dict)]

    def scan_heights(self) -> dict:
        """{floor (str): {"wall_height", "floor_height", "base_height"}} of each storey that has walls."""
        out = _payload(self.game.call("build.scan_floors", Limit=0))
        res = {}
        for f in out.get("floors") or []:
            hs = f.get("heights") or []
            if hs:
                res[str(int(f.get("floor_level") or 0))] = {k: hs[0].get(k) for k in ("wall_height", "floor_height",
                                                                                      "base_height")}
        return res

    def at_home_now(self, home) -> bool:
        """My Zoi is on the home lot right now. Outside build mode the scans read the lot my Zoi is on, and
        build.get_mode_state keeps naming the last lot it was in (seen: 958 while walking on 839), so this asks where
        my Zoi is."""
        try:
            return bool(home and self.my_entity and int(self.game.state_of(self.my_entity)["site"]) == int(home))
        except Exception:                                # noqa: BLE001
            return False

    def scan_lot(self) -> dict:
        """The lot as it is: objects, walls, heights (walls/heights empty if this game can't read them)."""
        lot = {"objects": self.scan_site(), "walls": [], "heights": {}}
        try:
            lot["walls"], lot["heights"] = self.scan_walls(), self.scan_heights()
        except Exception as e:                           # noqa: BLE001
            log.debug("reading the walls: %s", e)
        return lot

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
                        lot = await self.g(self.scan_lot)
                        self.build_site = dict(lot, site_id=st.get("site_id"), site=int(site), at=time.time())
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
                                  "site_id": b["site_id"], "objects": objs, "walls": b.get("walls") or [],
                                  "heights": b.get("heights") or {}}})
        log.info("sent the house to %s (%d objects, %d walls)", r.name, len(objs), len(b.get("walls") or []))

    async def request_house(self, r) -> dict:
        """Ask the partner's program for their home as it is now (they needn't be in build mode)."""
        if not (self.writer and self.me_id):
            return {"error": "not connected to the server", "code": "not_connected"}
        await self.send({"t": "event", "kind": "couple", "to": r.pid,
                         "data": {"type": "house_request", "id": secrets.token_hex(6)}})
        log.info("asked %s for their house", r.name)
        return {"ok": True}

    async def on_house_request(self, frm: str):
        """The partner wants our home as it is now: read it (my Zoi must be on the home lot) and send it."""
        try:
            st = await self.g(self.game.call, "build.get_mode_state") or {}
            home = await self.g(self.my_home_site)
            if not await self.g(self.at_home_now, home):
                log.info("asked for the house, but your Zoi isn't at home: not sent")
                return
            site = home
            info = await self.g(self.game.call, "build.get_site_info") or {}     # (the lot my Zoi is on)
            lot = await self.g(self.scan_lot)
        except Exception as e:                           # noqa: BLE001
            log.info("couldn't read the house for the partner: %s", e)
            return
        await self.send_house(dict(lot, site=int(site), site_id=info.get("site_id") or st.get("site_id")))

    def on_house(self, frm: str, data: dict):
        """The partner left build mode on our home: compare, and ask before changing anything here."""
        r = self.remotes.get(frm)
        name = r.name if r else "Your partner"
        objs = [o for o in (data.get("objects") or [])[:MAX_OBJECTS] if isinstance(o, dict) and o.get("table_id")]
        walls = []
        for w in (data.get("walls") or [])[:MAX_WALLS]:
            try:
                walls.append({"floor": int(w["floor"]), "a": [float(v) for v in w["a"][:2]],
                              "b": [float(v) for v in w["b"][:2]]})
            except (KeyError, TypeError, ValueError, IndexError):
                pass
        heights = data.get("heights") if isinstance(data.get("heights"), dict) else {}
        rid = secrets.token_hex(6)
        self.prune_requests()
        for q in [q for q in self.requests.values() if q.get("action") == "house"]:
            self.requests.pop(q["id"], None)             # (a newer version of the house replaces an older one)
        self.requests[rid] = {"id": rid, "from": name, "from_id": frm, "action": "house", "at": time.time(),
                              "local": True, "house": {"site": data.get("site"), "site_id": data.get("site_id"),
                                                       "objects": objs, "walls": walls, "heights": heights},
                              "label": None}
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
        mine = None
        if await self.g(self.at_home_now, home):         # (elsewhere the scan would read another lot: compare later)
            try:
                mine = await self.g(self.scan_lot)
            except Exception as e:                       # noqa: BLE001
                log.debug("reading my house: %s", e)
        if mine is not None:
            place, remove = diff(h["objects"], mine["objects"])
            build, delete = wall_diff(h["walls"], mine["walls"]) if h["walls"] else ([], [])
            storeys = height_diff(h["heights"], mine["heights"], h["walls"])
            if not place and not remove and not build and not delete and not storeys:
                self.requests.pop(rid, None)
                log.info("%s's house is the same as mine", q["from"])
                return
            q["label"] = house_label(place, remove, len(build) + len(delete))
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
            if int(st.get("site_entity_id") or -1) != int(h.get("site") or -2):    # (never edit another lot)
                await self.g(call, "build.exit_mode")
                log.warning("build mode opened lot %s, not the home (%s): nothing changed", st.get("site_entity_id"),
                            h.get("site"))
                return {"state": "failed", "message": "build mode opened another lot"}
            mine = await self.g(self.scan_lot)
            place, remove = diff(h["objects"], mine["objects"])
            build, delete = wall_diff(h["walls"], mine["walls"]) if h["walls"] else ([], [])
            failed = 0
            for o in remove:
                try:
                    await self.g(call, "build.remove_object", entity_id=str(o["entity_id"]))
                except Exception as e:                   # noqa: BLE001
                    failed += 1
                    log.debug("couldn't remove %s: %s", o["table_id"], e)
            for w in delete:                             # (walls: the objects that go with them are gone by now)
                try:
                    await self.g(call, "building.construct_wall", start_x=w["a"][0], start_y=w["a"][1],
                                 end_x=w["b"][0], end_y=w["b"][1], floor_level=w["floor"], is_delete=True)
                except Exception as e:                   # noqa: BLE001
                    failed += 1
                    log.debug("couldn't delete a wall: %s", e)
            for w in sorted(build, key=lambda w: w["floor"]):
                hh = h["heights"].get(str(w["floor"])) or {}
                kw = {"wall_height": float(hh["wall_height"])} if hh.get("wall_height") else {}
                if w["floor"] == 0 and hh.get("base_height"):
                    kw["base_height"] = float(hh["base_height"])
                try:
                    await self.g(call, "building.construct_wall", start_x=w["a"][0], start_y=w["a"][1],
                                 end_x=w["b"][0], end_y=w["b"][1], floor_level=w["floor"], **kw)
                except Exception as e:                   # noqa: BLE001
                    failed += 1
                    log.debug("couldn't build a wall: %s", e)
            if h["walls"]:
                try:
                    for f, height, at in height_diff(h["heights"], await self.g(self.scan_heights), h["walls"]):
                        await self.g(call, "building.set_storey_wall_height", floor_level=f, target_height=height,
                                     X=at[0], Y=at[1])
                except Exception as e:                   # noqa: BLE001
                    failed += 1
                    log.debug("couldn't set a storey's height: %s", e)
            if build or delete:                          # (doors/windows on deleted walls went too: read again)
                place, _ = diff(h["objects"], await self.g(self.scan_site))
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
            walls = len(build) + len(delete)
            log.info("applied %s's house: %d placed, %d removed, %d walls built, %d deleted, %d failed", q["from"],
                     len(place), len(remove), len(build), len(delete), failed)
            self.system_line("House updated: " + house_label(place, remove, walls) +
                             (f" ({failed} couldn't be changed)" if failed else ""))
            return {"state": "done", "placed": len(place), "removed": len(remove), "walls": walls, "failed": failed}
        except Exception as e:                           # noqa: BLE001
            log.warning("applying the house failed: %s", e)
            return {"state": "failed", "message": str(e)}
