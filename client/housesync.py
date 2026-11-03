"""The family home, the same in both saves (mixed into the Companion, like couple.Couple).

Each player plays their own save, so what one of them builds or rearranges in the shared home only exists in their
save. This keeps the two in step: the walls (segments per floor, and each storey's wall height) and the objects
(furniture, decoration, doors and windows). Wall and floor paint/material can't be read through the game's commands
(only written), so those stay as each save has them:

- While a player is in build mode on their home lot, the lot is read every few seconds (build.scan_objects: table
  id, site-relative position, yaw, floor; build.scan_walls: segment ends per floor; build.scan_floors: heights).
  When they leave build mode, the last reading goes to the partner:
  {"t":"event","kind":"couple","data":{"type":"house","site","site_id","objects":[...],"walls":[...],"heights":{}}}.
- The partner's program compares it with the same lot in its own save (same map, same lot id): objects of the same
  kind within 10 cm and 5 degrees are the same object; the rest is new (to place) or gone (to remove). A wall is
  there when every point along it lies on a wall of the other save (so one long wall and the two short ones that make
  it up are the same); walls not covered are deleted or built. A popup asks "<name> changed the house: N new,
  M removed - apply?" (or "always yes": key "house").
- Applying enters build mode on that lot (build.enter_mode, the game pauses), removes the objects that are gone,
  deletes and builds walls (building.construct_wall, is_delete for deleting), sets storey heights, reads the lot again
  and places what's still missing (building.place_object; doors and windows snap onto the new walls), and leaves build
  mode (which saves the lot). Leaving build mode after applying doesn't send the house back (it's the partner's
  version already).
- Outside build mode the scans read the lot my Zoi is on, so the home is only read while my Zoi is on it. The last
  reading is kept (home-lot.json: from build mode, after applying, and every few minutes while my Zoi is at home), so
  a partner who asks while my Zoi is out still gets it, and my side compares with it when I'm out. Each side asks
  again when its Zoi gets home.
- The exact house (lotsync.py): what the game's commands can't read (paint, materials, floors, roofs, stairs, object
  customization) goes as the game's own saved property. After leaving build mode on the home lot, the lot is saved as
  a property (build mode reopens for a few seconds) and sent: {"type":"lot","site","site_id","hash","files"}. The
  partner, once they said yes to that house, writes it as a property and the game applies it the next time it enters
  the world (the game reads properties only when it starts), or right away with "apply now" (saves and restarts the
  game). The same house hashes the same in both saves, so an unchanged one isn't asked about again.
"""
import asyncio
import json
import logging
import math
import os
import secrets
import time
from pathlib import Path

import lotsync

log = logging.getLogger("housesync")

SCAN_EVERY = 4            # s between readings while in build mode
SAME_CM, SAME_DEG = 10.0, 5.0
MAX_OBJECTS = 2000
MAX_WALLS = 2000
ON_WALL_CM = 4.0          # a point this close to a wall's centre line is on it (centre lines sit on 25*k+12.5)
WALL_STEP = 12.5          # cm between the points checked along a wall
LOT_FILE = Path(os.environ.get("APPDATA", Path.home())) / "inzoi-mp" / "home-lot.json"
REFRESH_EVERY = 300       # s between readings of the home while my Zoi is there (things bought in play mode)
ASK_AGAIN = 120           # s at least between asking the partner again on getting home
PRESET_FILE = Path(os.environ.get("APPDATA", Path.home())) / "inzoi-mp" / "home-lot-preset.json"
LOT_ACCEPT_FOR = 600      # s after saying yes to a partner's house in which their exact house is taken without asking


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
        self.lot_file = LOT_FILE
        self.was_home = None          # my Zoi was on the home lot at the last tick
        self.asked_at = 0.0
        self.preset_file = PRESET_FILE
        self.lot_busy = False         # saving/applying the exact house (build mode opens and closes by itself)
        self.lot_tried = False        # the pending exact house was tried in this world session
        self.house_yes = {}           # partner pid -> when I last said yes to their house
        try:
            pending = (self.cfg.get("pending_lot") or {}).get("folder")
            lotsync.cleanup(keep={pending} if pending else ())
        except Exception as e:                           # noqa: BLE001
            log.debug("cleaning old properties: %s", e)

    def saved_lot(self, site) -> dict | None:
        """The last reading of the home (if it's lot `site`)."""
        try:
            lot = json.loads(self.lot_file.read_text("utf-8"))
            return lot if site and int(lot.get("site") or -1) == int(site) else None
        except (OSError, ValueError, TypeError):
            return None

    def keep_lot(self, lot: dict):
        try:
            self.lot_file.parent.mkdir(parents=True, exist_ok=True)
            self.lot_file.write_text(json.dumps(dict(lot, at=time.time())), "utf-8")
        except OSError as e:
            log.debug("keeping the house: %s", e)

    async def home_site_cached(self):
        cached = self.__dict__.get("_home_cache")
        if not cached or time.time() - cached[1] > 30:
            cached = self._home_cache = (await self.g(self.my_home_site), time.time())
        return cached[0]

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
        if self.world == "out":
            self.lot_tried = False                       # (a new world session: the game may have restarted)
        if self.world == "out" or not self.my_entity or self.lot_busy:
            return
        try:
            st = await self.g(self.game.call, "build.get_mode_state") or {}
        except Exception:                                # noqa: BLE001
            return
        self.in_build_mode = bool(st.get("in_build_mode"))
        building = self.in_build_mode and st.get("phase") == "ready"
        if building:
            site = st.get("site_entity_id")
            home = await self.home_site_cached()
            if site and home and int(site) == int(home) and not self.applying_house:
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
            self.keep_lot(b)
            await self.publish_house(b)
            if self.cfg.get("exact_house", True):
                asyncio.get_running_loop().create_task(self.capture_lot(b))
        if not st.get("in_build_mode"):
            self.applying_house = False
            await self.home_tick()
            await self.maybe_apply_pending()

    # ------------------------------------------------------------------ the exact house (lotsync.py)
    def saved_preset(self, site=None) -> dict | None:
        try:
            p = json.loads(self.preset_file.read_text("utf-8"))
            return p if site is None or int(p.get("site") or -1) == int(site) else None
        except (OSError, ValueError, TypeError):
            return None

    async def capture_lot(self, b: dict):
        """Save my home as the game's property (build mode reopens for a few seconds) and send it to my partner."""
        if self.lot_busy or not b.get("site_id"):
            return
        self.lot_busy = True
        try:
            res = await self.g(lotsync.save_lot, self.game.call, b["site_id"], lotsync.new_title(self.cfg.get("name")
                                                                                                 or "home"))
            preset = {"site": b["site"], "site_id": b["site_id"], "hash": res["hash"], "files": res["files"],
                      "at": time.time()}
            self.preset_file.parent.mkdir(parents=True, exist_ok=True)
            self.preset_file.write_text(json.dumps(preset), "utf-8")
            self.cfg["lot_hash"] = res["hash"]
            self.save_cfg()
            log.info("saved the exact house (%s)", res["hash"])
            await self.publish_exact(preset)
        except lotsync.LotError as e:
            log.warning("couldn't save the exact house: %s", e)
        except Exception as e:                           # noqa: BLE001
            log.warning("saving the exact house failed: %s", e)
        finally:
            self.lot_busy = False

    # ------------------------------------------------------------------ the server's shared state (shared.py)
    async def publish_house(self, b: dict):
        """My home changed (I left build mode on it): its new version goes to the server, which hands it to everyone.
        Not connected: kept as changed here, pushed when the server can be reached (not overwritten by its version)."""
        objs = [{k: o[k] for k in ("table_id", "x", "y", "z", "yaw", "floor")} for o in b["objects"]]
        value = {"site": b["site"], "site_id": b.get("site_id"), "objects": objs, "walls": b.get("walls") or [],
                 "heights": b.get("heights") or {}}
        if await self.share(f"house:{b['site']}:live", value):
            self.set_dirty(b["site"], "live", False)
            log.info("my house went to the server (%d objects, %d walls)", len(objs), len(value["walls"]))
        else:
            self.set_dirty(b["site"], "live", True)

    async def publish_exact(self, preset: dict):
        value = {k: preset[k] for k in ("site", "site_id", "hash", "files")}
        if await self.share(f"house:{preset['site']}:exact", value):
            self.set_dirty(preset["site"], "exact", False)
            log.info("my exact house went to the server (%s)", preset["hash"])
        else:
            self.set_dirty(preset["site"], "exact", True)

    def set_dirty(self, site, part: str, on: bool):
        d = self.cfg.setdefault("house_dirty", {})
        k = f"{site}:{part}"
        if bool(d.get(k)) != on:
            if on:
                d[k] = True
            else:
                d.pop(k, None)
            self.save_cfg()

    def dirty(self, site, part: str) -> bool:
        return bool((self.cfg.get("house_dirty") or {}).get(f"{site}:{part}"))

    async def push_dirty_house(self):
        """Changes made while the server couldn't be reached: pushed now (they're newer than its version)."""
        d = self.cfg.get("house_dirty") or {}
        if not d or not (self.writer and self.me_id) or self.world == "out" or self.lot_busy:
            return
        home = await self.home_site_cached()
        if home and d.get(f"{home}:live") and await self.g(self.at_home_now, home):
            info = await self.g(self.game.call, "build.get_site_info") or {}
            lot = dict(await self.g(self.scan_lot), site=int(home), site_id=info.get("site_id"))
            self.keep_lot(lot)
            await self.publish_house(lot)
        preset = self.saved_preset(home) if home else None
        if preset and d.get(f"{home}:exact"):
            await self.publish_exact(preset)

    def on_shared_house_live(self, key: str, item: dict | None, snapshot: bool):
        if not item:
            return
        v, site = int(item.get("v") or 0), key.split(":")[1]
        if item.get("by") == self.me_id:
            self.mark_seen(key, v)                       # (my own version, confirmed: nothing to apply)
            return
        if self.seen(key) >= v:
            return
        if self.dirty(site, "live"):
            log.info("the server's house is v%d, but mine changed while it couldn't be reached: mine goes up", v)
            return
        log.info("house v%d from %s (the server's): bringing mine to it", v, item.get("by_name"))
        self.on_house(item.get("by"), item.get("value") or {}, name=item.get("by_name"), auto=True, shared=(key, v))

    def on_shared_house_exact(self, key: str, item: dict | None, snapshot: bool):
        if not item:
            return
        v, val = int(item.get("v") or 0), item.get("value") or {}
        if item.get("by") == self.me_id:
            if val.get("hash"):
                self.cfg["lot_hash"] = val["hash"]
            self.mark_seen(key, v)
            return
        if self.seen(key) >= v or self.dirty(key.split(":")[1], "exact"):
            return
        if val.get("hash") in (self.cfg.get("lot_hash"), (self.cfg.get("pending_lot") or {}).get("hash")):
            self.mark_seen(key, v)
            return
        asyncio.get_running_loop().create_task(self.take_shared_exact(key, v, item.get("by_name") or "Your partner", val))

    async def take_shared_exact(self, key: str, v: int, name: str, val: dict):
        home = await self.g(self.my_home_site)
        if not home or int(home) != int(val.get("site") or -1):
            log.info("%s's exact house is lot %s, mine is %s: not taken", name, val.get("site"), home)
            return
        self.stash_lot(name, val)
        self.mark_seen(key, v)

    def reapply_shared_house(self) -> bool:
        """Compare my home with the server's version again (the panel's "get my partner's house", getting home)."""
        keys = [k for k in self.shared if k.startswith("house:")]
        for k in keys:
            seen = self.cfg.setdefault("shared_seen", {})
            seen.pop(k, None)
            self.shared_changed(k, self.shared[k], snapshot=False)
        return bool(keys)

    async def send_lot(self, preset: dict):
        r = self.partner(None) if hasattr(self, "partner") else None
        if not preset or not r or not (self.writer and self.me_id):
            return
        await self.send({"t": "event", "kind": "couple", "to": r.pid,
                         "data": {"type": "lot", "id": secrets.token_hex(6), "site": preset["site"],
                                  "site_id": preset["site_id"], "hash": preset["hash"], "files": preset["files"]}})
        log.info("sent the exact house to %s", r.name)

    def on_lot(self, frm: str, data: dict):
        """My partner's exact house: the same as mine -> nothing; else kept to apply once I said yes to their house."""
        r = self.remotes.get(frm)
        name = r.name if r else "Your partner"
        if not isinstance(data.get("files"), dict) or not data.get("hash"):
            return
        if data["hash"] in (self.cfg.get("lot_hash"), (self.cfg.get("pending_lot") or {}).get("hash")):
            log.info("%s's exact house is the same as mine", name)
            return
        asyncio.get_running_loop().create_task(self.take_lot(frm, name, data))

    async def take_lot(self, frm: str, name: str, data: dict):
        home = await self.g(self.my_home_site)
        if not home or int(home) != int(data.get("site") or -1):
            log.info("%s's exact house is lot %s, mine is %s: ignored", name, data.get("site"), home)
            return
        if time.time() - self.house_yes.get(frm, 0) < LOT_ACCEPT_FOR or self.always_yes("house", None):
            self.stash_lot(name, data)
            return
        for q in self.requests.values():                 # (their house popup is still open: it carries this too)
            if q.get("action") == "house" and q.get("from_id") == frm:
                q["house"]["lot"] = data
                return
        rid = secrets.token_hex(6)
        self.requests[rid] = {"id": rid, "from": name, "from_id": frm, "action": "house", "at": time.time(),
                              "local": True, "label": "finishes",
                              "house": {"site": data.get("site"), "site_id": data.get("site_id"), "objects": None,
                                        "walls": [], "heights": {}, "lot": data}}
        self.system_line(f"{name} changed the house: finishes")

    def stash_lot(self, name: str, data: dict):
        """Write their exact house as a property; it's applied when the game next enters the world."""
        old = self.cfg.get("pending_lot") or {}
        title = f"{lotsync.TITLE_PREFIX} {name} {data['hash'][:6]}"
        try:
            folder = lotsync.write_pending(data["files"], title)
        except Exception as e:                           # noqa: BLE001
            log.warning("couldn't keep %s's exact house: %s", name, e)
            return
        if old.get("folder") and old["folder"] != folder:
            lotsync.cleanup(keep={folder})
        self.cfg["pending_lot"] = {"title": title, "folder": folder, "site_id": data.get("site_id"),
                                   "hash": data["hash"], "from": name, "at": time.time()}
        self.save_cfg()
        self.lot_tried = False
        log.info("kept %s's exact house: applied when the game reopens", name)
        self.system_line(f"{name}'s exact house (paint, floors, roof) comes in when you reopen the game")

    async def maybe_apply_pending(self):
        p = self.cfg.get("pending_lot")
        if not p or self.lot_tried or self.lot_busy or self.world != "play" or self.in_build_mode:
            return
        self.lot_tried = True
        self.lot_busy = True
        try:
            await self.g(lotsync.apply_by_title, self.game.call, p["site_id"], p["title"])
            self.cfg["lot_hash"] = p["hash"]
            self.cfg.pop("pending_lot", None)
            self.save_cfg()
            lotsync.cleanup()
            log.info("applied %s's exact house", p["from"])
            self.system_line(f"House now exactly like {p['from']}'s")
        except lotsync.NotListed:
            log.info("%s's exact house was kept while the game ran: it comes in after the game restarts", p["from"])
        except Exception as e:                           # noqa: BLE001
            log.warning("applying the exact house failed: %s", e)
        finally:
            self.lot_busy = False

    async def apply_lot_now(self) -> dict:
        """Panel: save, restart the game; the pending exact house is applied when the world loads again."""
        if not self.cfg.get("pending_lot"):
            return {"error": "no house waiting to be applied", "code": "no_pending"}
        if self.world == "out":
            return {"error": "you're not in the game's world", "code": "not_in_game"}
        self.lot_busy = True
        try:
            await self.g(lotsync.restart_game, self.game.call)
        except lotsync.LotError as e:
            return {"error": str(e), "code": "restart"}
        finally:
            self.lot_busy = False
        self.lot_tried = False
        return {"ok": True}

    async def home_tick(self):
        """Play mode: keep the reading of the home fresh while my Zoi is there, and ask the partner for theirs on
        getting home (their answer is compared while my Zoi is on the lot)."""
        home = await self.home_site_cached()
        here = (self.last_full_state or {}).get("site")
        at_home = bool(home and here is not None and int(here) == int(home))
        arrived = at_home and self.was_home is False
        self.was_home = at_home
        if not at_home:
            return
        lot = self.saved_lot(home)
        if not lot or time.time() - lot.get("at", 0) > REFRESH_EVERY:
            if await self.g(self.at_home_now, home):
                try:
                    info = await self.g(self.game.call, "build.get_site_info") or {}
                    self.keep_lot(dict(await self.g(self.scan_lot), site=int(home), site_id=info.get("site_id")))
                except Exception as e:                   # noqa: BLE001
                    log.debug("reading the house: %s", e)
        r = self.partner(None) if hasattr(self, "partner") else None
        if arrived and r and time.time() - self.asked_at > ASK_AGAIN:
            self.asked_at = time.time()
            await self.request_house(r)

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
        """Ask the partner's program for their home as it is now (they needn't be in build mode). With the server's
        shared state: compare with its version (the truth) instead."""
        if not (self.writer and self.me_id):
            return {"error": "not connected to the server", "code": "not_connected"}
        if self.reapply_shared_house():
            log.info("comparing my house with the server's version")
            return {"ok": True}
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
                lot = self.saved_lot(home)
                if lot:
                    log.info("asked for the house while your Zoi is out: sending the last reading of it")
                    await self.send_house(lot)
                    await self.send_lot(self.saved_preset(home))
                else:
                    log.info("asked for the house, but your Zoi isn't at home (and it was never read): not sent")
                return
            site = home
            info = await self.g(self.game.call, "build.get_site_info") or {}     # (the lot my Zoi is on)
            lot = await self.g(self.scan_lot)
        except Exception as e:                           # noqa: BLE001
            log.info("couldn't read the house for the partner: %s", e)
            return
        lot = dict(lot, site=int(site), site_id=info.get("site_id") or st.get("site_id"))
        self.keep_lot(lot)
        await self.send_house(lot)
        await self.send_lot(self.saved_preset(site))

    def on_house(self, frm: str, data: dict, name: str | None = None, auto: bool = False, shared=None):
        """The partner left build mode on our home: compare, and ask before changing anything here (auto: the server's
        version, the truth: applied without asking; shared: (key, version) marked applied once done)."""
        r = self.remotes.get(frm)
        name = name or (r.name if r else "Your partner")
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
                              "label": None, "auto": auto, "shared": shared}
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
        if await self.g(self.at_home_now, home):         # (elsewhere the scan would read another lot: kept reading)
            try:
                mine = await self.g(self.scan_lot)
            except Exception as e:                       # noqa: BLE001
                log.debug("reading my house: %s", e)
        if mine is None:
            mine = self.saved_lot(home)
        if mine is not None:
            place, remove = diff(h["objects"], mine["objects"])
            build, delete = wall_diff(h["walls"], mine["walls"]) if h["walls"] else ([], [])
            storeys = height_diff(h["heights"], mine["heights"], h["walls"])
            if not place and not remove and not build and not delete and not storeys:
                self.requests.pop(rid, None)
                log.info("%s's house is the same as mine", q["from"])
                if q.get("shared"):
                    self.mark_seen(*q["shared"])
                return
            q["label"] = house_label(place, remove, len(build) + len(delete))
        self.system_line(f"{q['from']} changed the house" + (f": {q['label']}" if q["label"] else ""))
        if self.always_yes("house", None) or q.get("auto"):
            out = await self.couple_respond(rid, True)
            if q.get("shared") and ((out or {}).get("result") or {}).get("state") == "done":
                self.mark_seen(*q["shared"])

    async def apply_house(self, q: dict) -> dict:
        """Enter build mode on the lot, remove and place what differs, leave build mode (it saves the lot)."""
        h = q["house"]
        if h.get("lot"):
            self.stash_lot(q["from"], h["lot"])
        if h.get("objects") is None:                     # (only their exact house: it comes in on reopen)
            return {"state": "done", "placed": 0, "removed": 0, "walls": 0, "failed": 0, "exact": "on_reopen"}
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
            try:
                self.keep_lot(dict(await self.g(self.scan_lot), site=int(h["site"]), site_id=h.get("site_id")))
            except Exception as e:                       # noqa: BLE001
                log.debug("reading the house after applying: %s", e)
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
