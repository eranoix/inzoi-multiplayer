"""Every detail of the shared home that the game lets us read and write, kept on the server (mixed into the Companion;
the in-game half is read_dirt / set_dirt in lua/B2/Main.lua; colours go through the game's build.* commands):

    dirt:<lot>          the home's dust and wear: {"o": {key: [table, x, y, z, dust (1/1000), wear grade]}}
    look:<lot>:<key>    one object's colours and materials: {"t": table, "p": [x, y, z], "m": {part: {field: value}}}

(key: the object's kind and place to 10 cm - detail_key: the same object in both saves, which the house sync keeps
in the same places; objsync's 50 cm took two pieces of lawn 45 cm apart for one, and only one was ever synced.)

The server's values are the truth: each PC brings its game to them - at the first read of the lot in a world (a save
loaded, the program or the game started again) whoever set them, and later whenever a partner's change comes.

Dust and wear grow with game time in both games: only the host's growth counts - otherwise the home got dirty twice as
fast, each game adding its own. The host's readings go up as they are (grown, cleaned, repaired); the other PC's go up
only where they went down (cleaned, repaired), and what grew there by itself is put back to the server's value. A
cleaning of mine that the server answered with an older value (the host's reading crossed it) is sent again.

Colours and materials change in build mode, on any PC: a change read here (an object's look other than when it was last
synced) goes up and is put on that object in the other game, part by part (build.set_material; a part the server has
as default is reset). The first time a lot's looks are shared, the host's home is the starting point. Right after a
look is put on an object here, the game's own rendering of it (a field it fills in) isn't taken for a change.
"""
import asyncio
import hashlib
import logging
import time

from housesync import TRANSIENT
from shared import version

log = logging.getLogger("detailsync")

DIRT_EVERY = 10.0        # s between reads of the home's dust and wear
LOOK_EVERY = 60.0        # s between reads of its colours (every object, one command each: ~6 s for 215)
DUST_STEP = 50           # 1/1000: a dust difference that counts (the game moves it 75 at a time)
MINE_FOR = 90.0          # s a cleaning of mine is sent again when the server answers it with an older value
SET_BATCH = 60           # objects in one set_dirt op
COLOR_FIELDS = ("color", "emissive_color", "pattern_color")
NUMBER_FIELDS = ("emissive_multi", "roughness", "metallic", "specular", "opacity", "pattern_scale")
INT_FIELDS = ("material_group", "material_index")
SET_PARAM = {"color": "Color", "roughness": "Roughness", "metallic": "Metallic", "specular": "Specular",
             "opacity": "Opacity", "pattern_color": "pattern_color", "pattern_scale": "pattern_scale",
             "emissive_color": "emissive_color", "emissive_multi": "emissive_multi",
             "material_group": "material_group", "material_index": "material_index"}


def detail_key(table: str, x: float, y: float, z: float) -> str:
    raw = f"{table}|{round(x / 10)}|{round(y / 10)}|{round(z / 10)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def parse_dirt(text: str):
    """The controller's "dirt" report: "<lot>;entity|table|x|y|z|dust|grade;..." -> (lot, {key: obj})."""
    parts = str(text or "").split(";")
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
            x, y, z, d, w = float(f[2]), float(f[3]), float(f[4]), int(f[5]), int(f[6])
        except ValueError:
            continue
        objs[detail_key(f[1], x, y, z)] = {"t": f[1], "p": [round(x), round(y), round(z)], "d": d, "w": w, "e": f[0]}
    return site, objs


def dirt_differs(a, b) -> bool:
    """[dust, grade] a and b apart enough to count (a side the object doesn't have, -1, never does)."""
    if a[0] >= 0 and b[0] >= 0 and abs(a[0] - b[0]) >= DUST_STEP:
        return True
    return a[1] >= 0 and b[1] >= 0 and a[1] != b[1]


def color_of(c) -> list | None:
    """A colour as the game reads it: linear [r, g, b, a], 4 decimals (8 bits lose the dark tones' precision)."""
    if not isinstance(c, dict):
        return None
    try:
        return [round(float(c.get(k, 1.0 if k == "a" else 0.0)), 4) for k in "rgba"]
    except (TypeError, ValueError):
        return None


def srgb_hex(rgba) -> str:
    """A colour as the game reads it (linear) -> as build.set_material takes it (sRGB hex): put as read, a brown
    #5E3C2D came out #1D0B07."""
    def conv(v: float) -> int:
        v = max(0.0, min(1.0, float(v)))
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return max(0, min(255, round(v * 255)))
    rgb = [conv(v) for v in rgba[:3]]
    a = max(0, min(255, round(float(rgba[3]) * 255))) if len(rgba) > 3 else 255
    return "#" + "".join(f"{v:02X}" for v in rgb) + (f"{a:02X}" if a < 255 else "")


def same_look(a: dict, b: dict) -> bool:
    """Two looks alike to the eye: colours within 0.004 a channel, numbers within 0.002 (put through the game, a
    colour comes back a hair off - 0.368 read as 0.366 - and would be put again at every load)."""
    if set(a) != set(b):
        return False
    for part, fa in a.items():
        fb = b[part]
        if set(fa) != set(fb):
            return False
        for k, va in fa.items():
            vb = fb[k]
            if isinstance(va, list) and isinstance(vb, list):
                if len(va) != len(vb) or any(abs(x - y) > 0.004 for x, y in zip(va, vb)):
                    return False
            elif isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                if abs(va - vb) > 0.002:
                    return False
            elif va != vb:
                return False
    return True


def normalize_look(info) -> dict:
    """build.get_customize_info -> {part: {field: value}}: only customised parts, only fields that can be put back
    (colours linear, numbers rounded) - the same look reads the same in both games."""
    out = {}
    for p in (info or {}).get("parts") or []:
        if not isinstance(p, dict) or "index" not in p:
            continue
        fields = {}
        for k in COLOR_FIELDS:
            c = color_of(p.get(k))
            if c:
                fields[k] = c
        for k in NUMBER_FIELDS:
            if isinstance(p.get(k), (int, float)):
                fields[k] = round(float(p[k]), 3)
        for k in INT_FIELDS:
            if isinstance(p.get(k), (int, float)):
                fields[k] = int(p[k])
        if fields:
            out[str(p["index"])] = fields
    return out


class DetailSync:
    def detail_init(self):
        self.dirt_site = self.dirt_inst = None
        self.dirt_last: dict[str, dict] = {}       # key -> the last reading (every object of the lot, with its entity)
        self.dirt_base: dict[str, list] = {}       # key -> [dust, grade] as last synced (applied or sent)
        self.dirt_mine: dict[str, tuple] = {}      # key -> ([dust, grade], at): a cleaning of mine on its way
        self.look_site = self.look_inst = None
        self.look_base: dict[str, dict] = {}       # key -> my game's look as last synced
        self.look_srv: dict[str, dict] = {}        # key -> the server's look it was synced with
        self.look_put: dict[str, float] = {}       # key -> when a look was put on it here (its next read is the game's)
        self.look_busy = False

    def is_host(self) -> bool:
        return bool(self.leader) and self.leader == self.me_id

    def detail_live(self) -> bool:
        return self.world == "play" and self.mp_active() and (getattr(self, "mod", None) or {}).get("state") == "running"

    async def detail_loop(self):
        looked = 0.0
        while True:
            await asyncio.sleep(DIRT_EVERY)
            try:
                if not self.detail_live():
                    continue
                home = await self.home_site_cached()
                if not home:
                    continue
                self.mail.op("read_dirt", site=int(home))
                if time.time() - looked >= LOOK_EVERY and not self.look_busy and self.dirt_site == int(home):
                    looked = time.time()
                    self.shared_task(self.look_scan(int(home)))
            except Exception as e:                       # noqa: BLE001 - never worth stopping the loop
                log.debug("details: %s", e)

    # ---------------------------------------------------------------- dust and wear
    def on_dirt_report(self, text: str):
        site, objs = parse_dirt(text)
        if site is None:
            return
        first = site != self.dirt_site or self.dirt_inst != self.mod_instance()
        if first:
            self.dirt_site, self.dirt_inst, self.dirt_base = site, self.mod_instance(), {}
            log.info("dust and wear of lot %d: %d objects", site,
                     sum(1 for o in objs.values() if o["d"] >= 0 or o["w"] >= 0))
        self.dirt_last = objs
        self.reconcile_dirt(site, first)

    def reconcile_dirt(self, site: int, first: bool = False):
        key = f"dirt:{site}"
        if key in self.shared_inflight:
            return                                       # (mine is on its way: its answer decides)
        item = self.shared.get(key)
        srv = ((item or {}).get("value") or {}).get("o") or {}
        host, now = self.is_host(), time.time()
        send, fix = {}, []
        for k, o in self.dirt_last.items():
            here = [o["d"], o["w"]]
            if here[0] < 0 and here[1] < 0:
                continue
            s = srv.get(k)
            if not isinstance(s, list) or len(s) < 6:
                if host:
                    send[k] = here                       # (the server doesn't know it yet: the host's counts)
                continue
            sv = [int(s[4]), int(s[5])]
            base = self.dirt_base.get(k)
            mine = self.dirt_mine.get(k)
            if mine and (not dirt_differs(sv, mine[0]) or now - mine[1] > MINE_FOR):
                self.dirt_mine.pop(k, None)              # (it got there - or it's too old to insist on)
                mine = None
            if first or base is None or sv != base:
                if mine and item.get("by") != self.me_id and not dirt_differs(here, mine[0]):
                    send[k] = mine[0]                    # (my cleaning, answered with an older value: again)
                    continue
                if dirt_differs(here, sv):
                    fix.append((o, sv))
                self.dirt_base[k] = sv
                continue
            if not dirt_differs(here, base):
                continue
            if host:
                send[k] = here
                continue
            d = here[0] if here[0] >= 0 and here[0] <= base[0] - DUST_STEP else base[0]
            w = here[1] if 0 <= here[1] < base[1] else base[1]
            if [d, w] != base:
                send[k] = [d, w]                         # (cleaned or repaired here)
                self.dirt_mine[k] = ([d, w], now)
            if dirt_differs(here, [d, w]):
                fix.append((o, [d, w]))                  # (grew here by itself: the host's growth counts)
        if fix:
            self.put_dirt(site, fix)
        if send:
            # (the host's: only what's in the home now - keys from an older program or of objects gone are dropped)
            value = {"o": {k: v for k, v in srv.items() if not host or k in self.dirt_last}}
            for k, v in send.items():
                o = self.dirt_last[k]
                value["o"][k] = [o["t"], *o["p"], int(v[0]), int(v[1])]
                self.dirt_base[k] = [int(v[0]), int(v[1])]
            self.shared_task(self.share(key, value, base=version(item) if item else None))

    def put_dirt(self, site: int, fix: list):
        for i in range(0, len(fix), SET_BATCH):
            items = [{"table": o["t"], "x": o["p"][0], "y": o["p"][1], "z": o["p"][2], "d": int(v[0]), "w": int(v[1])}
                     for o, v in fix[i:i + SET_BATCH]]
            self.mail.op("set_dirt", site=int(site), items=items)
        log.info("dust and wear brought to the shared home's on %d object(s)", len(fix))

    # ---------------------------------------------------------------- colours and materials
    async def look_scan(self, site: int):
        self.look_busy = True
        try:
            objs = {k: o for k, o in self.dirt_last.items() if o.get("e") and not TRANSIENT.match(o["t"])}
            looks = await asyncio.to_thread(self.read_looks, objs)
            if looks:
                await self.reconcile_looks(site, looks, objs)
        except Exception as e:                           # noqa: BLE001
            log.debug("colours: %s", e)
        finally:
            self.look_busy = False

    def read_looks(self, objs: dict) -> dict:
        out = {}
        for k, o in objs.items():
            try:
                out[k] = normalize_look(self.game.call("build.get_customize_info", entity_id=str(o["e"])))
            except Exception:                            # noqa: BLE001 - (one object the game won't describe)
                continue
        return out

    async def reconcile_looks(self, site: int, looks: dict, objs: dict):
        first = site != self.look_site or self.look_inst != self.mod_instance()
        if first:
            self.look_site, self.look_inst, self.look_base, self.look_srv = site, self.mod_instance(), {}, {}
        host, now, put = self.is_host(), time.time(), []
        for k, m in looks.items():
            key = f"look:{site}:{k}"
            if key in self.shared_inflight:
                continue
            item = self.shared.get(key)
            base = self.look_base.get(k)
            if self.look_put.pop(k, 0) > now - 3 * LOOK_EVERY:
                self.look_base[k] = m                    # (the game's own rendering of what was put: not a change)
                continue
            if item is None:
                if base is None:
                    self.look_base[k] = m
                    if host:                             # (the server has no look for it yet: the host's is it)
                        self.look_srv[k] = m
                        self.shared_task(self.share(key, self.look_value(objs[k], m)))
                elif not same_look(m, base):
                    self.look_base[k] = self.look_srv[k] = m
                    self.shared_task(self.share(key, self.look_value(objs[k], m)))
                continue
            sv = (item.get("value") or {}).get("m") or {}
            if first or base is None or sv != self.look_srv.get(k):
                self.look_srv[k] = sv                    # (the server's changed - or this is the first look at it)
                if not same_look(m, sv):
                    put.append((k, objs[k], sv, m))
                else:
                    self.look_base[k] = m
                continue
            if not same_look(m, base):                   # (changed here)
                self.look_base[k] = self.look_srv[k] = m
                self.shared_task(self.share(key, self.look_value(objs[k], m), base=version(item)))
        if host:                                         # (keys of objects not in the home: dropped)
            prefix = f"look:{site}:"
            for key in [k for k in self.shared if k.startswith(prefix) and k[len(prefix):] not in objs]:
                if key not in self.shared_inflight:
                    self.shared_task(self.share(key, None))
        if put:
            await asyncio.to_thread(self.put_looks, put)

    @staticmethod
    def look_value(o: dict, m: dict) -> dict:
        return {"t": o["t"], "p": list(o["p"]), "m": m}

    def put_looks(self, put: list):
        for k, o, want, have in put:
            e = str(o["e"])
            try:
                for part in sorted(set(have) - set(want), key=int):
                    self.game.call("build.reset_material", entity_id=e, part_index=int(part))
                for part, fields in sorted(want.items(), key=lambda kv: int(kv[0])):
                    if part in have and same_look({part: have[part]}, {part: fields}):
                        continue
                    args = {SET_PARAM[f]: (srgb_hex(v) if f in COLOR_FIELDS else v)
                            for f, v in fields.items() if f in SET_PARAM}
                    self.game.call("build.set_material", entity_id=e, part_index=int(part), **args)
                self.look_put[k] = time.time()
            except Exception as ex:                      # noqa: BLE001
                log.debug("colours of %s: %s", o.get("t"), ex)
        log.info("colours brought to the shared home's on %d object(s)", len(put))

    # ---------------------------------------------------------------- the server's word
    def on_shared_detail(self, key: str, item: dict | None, snapshot: bool):
        """A partner's dust or colours (or my own, confirmed): brought into my game at the next read (dirt) or now
        (one object's look)."""
        if not item or snapshot or item.get("by") == self.me_id:
            return                                       # (a snapshot: the next read and scan bring the game to it)
        parts = key.split(":")
        try:
            site = int(parts[1])
        except (IndexError, ValueError):
            return
        if parts[0] == "dirt":
            if self.dirt_site == site and self.dirt_inst == self.mod_instance() and self.detail_live():
                self.reconcile_dirt(site)
            return
        if parts[0] == "look" and len(parts) == 3 and self.look_site == site and self.detail_live():
            k = parts[2]
            o = self.dirt_last.get(k)
            want = (item.get("value") or {}).get("m") or {}
            if o and o.get("e") and self.look_srv.get(k) != want:
                self.shared_task(self.put_one_look(k, o, want))

    async def put_one_look(self, k: str, o: dict, want: dict):
        try:
            have = await asyncio.to_thread(lambda: normalize_look(self.game.call(
                "build.get_customize_info", entity_id=str(o["e"]))))
        except Exception:                                # noqa: BLE001
            return
        self.look_srv[k] = want
        if not same_look(have, want):
            await asyncio.to_thread(self.put_looks, [(k, o, want, have)])
        else:
            self.look_base[k] = have
