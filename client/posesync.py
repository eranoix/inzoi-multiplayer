"""Skeleton mirroring, the companion's half (mixed into the Companion). The in-game half is lua/B1/Main.lua.

What one PC shows of a player's Zoi is exactly what that player's own game computed, bone by bone: nothing is
re-enacted (no interaction queued, no AI deciding which side of the bed). The B1 script writes this player's frames to
<mod>/out/posek.cfg (a keyframe, every second) and <mod>/out/posed.cfg (the bones that differ from it, 60 times a
second); this module sends each new one to every partner as {"t":"event","kind":"pose","data":{"k"|"d": text}} and
writes what partners send to <mod>/in/posek_<pid>.cfg / posed_<pid>.cfg, plus <mod>/in/posemap.cfg ("pid=entity.v;":
which Zoi here is whose, v changes when their outfit does, so the rig is rebuilt on the new clothes).

While a partner's frames keep coming (pose_live), the older re-enactment is off for them: no actions queued on their
Zoi (except the couple's own interactions with this player), no montage copying, no pose pinning.
"""
import asyncio
import base64
import logging
import os
import time
import zlib

import install

log = logging.getLogger("posesync")

LIVE_FOR = 2.0            # s without a frame from a partner before the re-enactment takes over again
SEND_EVERY = 1 / 250      # s between looks at the B1 script's files (it writes 60 frames a second; timer_1ms)
MAP_EVERY = 0.5
# How often my skeleton goes out (Unreal's relevancy and update rate): FULL_RATE frames a second while a partner's Zoi
# is within NEAR_IN cm of mine (drawn on their screen, mine on theirs), FAR_RATE once every partner is past NEAR_OUT
# (not drawn there: the frames would only cost both games time; the far side doesn't even read them). The two
# distances keep it from flapping at the edge.
FULL_RATE, FAR_RATE = 60, 2
NEAR_IN, NEAR_OUT = 5000, 7000
# Frames go out compressed (Gaffer's snapshot compression, the simple way): "z:" + base64(zlib(text)), a third of the
# size (the numbers repeat a lot), to partners whose program reads it (ZIP_FROM on); the server only relays and keeps
# the newest one of each kind for latecomers, as it is.
ZIP_FROM, ZIP_OVER = (0, 9, 61), 512


def timer_1ms() -> None:
    """Windows wakes a sleeping program every 15.6 ms by default: polling the B1 script's files for 60 frames a second
    (one every 16.7 ms) then misses some. Asking for 1 ms (timeBeginPeriod, as games and media players do) - for this
    program only on Windows 10 2004 and later."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.WinDLL("winmm").timeBeginPeriod(1)
    except (OSError, AttributeError) as e:
        log.debug("1 ms timer: %s", e)


def pack(text: str) -> str:
    return "z:" + base64.b64encode(zlib.compress(text.encode("utf-8"), 6)).decode("ascii")


def unpack(text: str) -> str | None:
    if not text.startswith("z:"):
        return text
    try:
        return zlib.decompress(base64.b64decode(text[2:]), bufsize=1 << 20).decode("utf-8")
    except (ValueError, zlib.error, UnicodeDecodeError):
        return None


def write_atomic(path, text: str):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, path)


class PoseSync:
    def pose_init(self):
        self.pose_from = {}           # pid -> when their last frame came
        self.pose_mtimes = {"k": None, "d": None}
        self.pose_counts = {"sent": 0, "got": 0}
        self.pose_map_text = None

    def pose_rate(self, partners) -> int:
        """FULL_RATE while any partner is near my Zoi, FAR_RATE when all are far (positions from their latest states and
        my own; unknown -> full)."""
        me = self.__dict__.get("fast_pos")
        near = self.__dict__.get("pose_near", True)
        if not me:
            near = True
        else:
            dists = []
            for r in partners:
                st = r.state or {}
                if isinstance(st.get("x"), (int, float)) and isinstance(st.get("y"), (int, float))                         and time.time() - getattr(r, "state_at", 0) < 10:
                    dists.append(((st["x"] - me["x"]) ** 2 + (st["y"] - me["y"]) ** 2) ** 0.5)
            if not dists:
                near = True
            elif near:
                near = min(dists) <= NEAR_OUT
            else:
                near = min(dists) < NEAR_IN
        if near != self.__dict__.get("pose_near", True):
            log.info("skeleton: %s", "a partner is near: full rate" if near else "every partner is far: a few frames a second")
        self.pose_near = near
        return FULL_RATE if near else FAR_RATE

    def pose_net(self, pid: str) -> dict:
        """The partner card's network line (like a game's net graph): their frames a second over the last 3 s, how old
        the newest one is, and the rate mine go out at."""
        now = time.time()
        times = [t for t in self.__dict__.get("pose_times", {}).get(pid, []) if now - t < 3]
        last = self.pose_from.get(pid) if hasattr(self, "pose_from") else None
        return {"frames_s": round(len(times) / 3, 1), "frame_ms": int((now - last) * 1000) if last else None,
                "send_s": FULL_RATE if self.__dict__.get("pose_near", True) else FAR_RATE}

    def pose_zip_ok(self, partners) -> bool:
        """Every partner's program reads compressed frames (a version not known yet: not yet)."""
        import updater
        return all(updater.version_tuple(getattr(r, "version", None) or "0") >= ZIP_FROM for r in partners)

    def pose_live(self, r) -> bool:
        return time.time() - self.pose_from.get(r.pid, 0) < LIVE_FOR

    def on_pose(self, m: dict):
        frm, data = str(m.get("from") or ""), m.get("data") or {}
        if not frm or not isinstance(data, dict) or not frm.replace("_", "").replace("-", "").isalnum():
            return
        folder = install.INSTALL_DIR / "in"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for kind in ("k", "d"):
                text = data.get(kind)
                if isinstance(text, str) and len(text) < 400000:
                    text = unpack(text)
                    if text is not None and len(text) < 1_000_000:
                        write_atomic(folder / f"pose{kind}_{frm}.cfg", text)
                        data = {**data, kind: text}
        except OSError as e:
            log.debug("writing a pose: %s", e)
            return
        first = time.time() - self.pose_from.get(frm, 0) > LIVE_FOR
        self.pose_from[frm] = time.time()
        times = self.__dict__.setdefault("pose_times", {}).setdefault(frm, [])
        times.append(time.time())
        del times[:-120]
        self.skel_point(frm, data.get("d") or data.get("k"))
        self.pose_counts["got"] += 1
        if first:
            r = self.remotes.get(frm)
            name = r.name if r else frm
            log.info("%s's skeleton is coming in: their Zoi here shows exactly what their game shows", name)
            self.tele.rec("note", text=f"{name}: skeleton frames coming in (exact mirroring on)")
            if r:
                self.leave_reenactment(r)

    def skel_point(self, frm: str, text):
        """Where their Zoi is drawn in their game (the frame's head: x,y,z x10, yaw x100) goes to the controller as the
        puppet's trail point while their skeleton comes in: the bones are relative to that place - their simulation's
        position, in the state, can be off it (lying in bed: 77 cm), which left the body beside the bed."""
        if not isinstance(text, str) or frm not in self.puppet_ids:
            return
        parts = text.split("|", 4)
        # (a frame "kid|seq|head|..." or a keyframe "kid|head|..."; the head x,y,z,yaw - and since 0.9.70 the time it
        # was read, which the B1 script plays the frames back on)
        head = parts[2] if len(parts) > 3 and parts[2].count(",") in (3, 4) else (parts[1] if len(parts) > 1 else "")
        try:
            x, y, z, yaw = (int(v) for v in head.split(",")[:4])
        except ValueError:
            return
        n = self.__dict__.setdefault("skel_n", {}).get(frm, 10 ** 7) + 1        # (above any state's count: one trail)
        self.skel_n[frm] = n
        self.mail.add_point(frm, n, x / 10, y / 10, z / 10, yaw / 100, time.time())

    def leave_reenactment(self, r):
        """Their frames drive their Zoi now: stop what the re-enactment had going on it."""
        if r.pid in self.puppet_ids:
            self.mail.op("act_stop", key=r.pid)
            self.mail.op("pose", key=r.pid, on=False)
        r.act_sig, r.pose_on = None, False

    def tell_skel(self):
        """The B2 controller puts a puppet where its player is every tick while their skeleton comes in, instead of
        walking it there ("skel" op): told when that starts or stops, and again for a fresh controller (a reload)."""
        inst = self.mod.get("instance")
        sent = self.__dict__.setdefault("skel_sent", {})
        for r in self.remotes.values():
            want = bool(self.puppet_ids.get(r.pid) and r.spawned and self.pose_live(r))
            if sent.get(r.pid) != (want, inst):
                if r.pid in self.puppet_ids:
                    self.mail.op("skel", key=r.pid, on=want)
                sent[r.pid] = (want, inst)

    async def pose_loop(self):
        out_dir = install.INSTALL_DIR / "out"
        in_dir = install.INSTALL_DIR / "in"
        map_at = 0.0
        timer_1ms()                                      # (a look every 4 ms, really)
        while True:
            try:
                partners = [r for r in self.remotes.values() if getattr(r, "pid", None)]
                if self.writer and self.me_id and partners and self.world != "out" and self.mp_active():
                    for kind in ("k", "d"):                      # (keyframe first: a frame needs its keyframe)
                        f = out_dir / f"pose{kind}.cfg"
                        try:
                            mt = f.stat().st_mtime_ns
                        except OSError:
                            continue
                        if mt == self.pose_mtimes[kind]:
                            continue
                        self.pose_mtimes[kind] = mt
                        try:
                            text = f.read_text("utf-8", errors="replace").strip()
                        except OSError:
                            continue
                        if not text:
                            continue
                        # (to everyone, through the server: it keeps the latest frames for whoever joins later). Only
                        # the newest frame of each kind waits for room on a slow link, never a backlog of old ones; a
                        # keyframe is only ever replaced by a newer keyframe, and goes out before the delta
                        # (send_latest; pose_counts["sent"] counts what really went out)
                        if len(text) > ZIP_OVER and self.pose_zip_ok(partners):
                            text = pack(text)
                        self.send_latest("pose:" + kind, {"t": "event", "kind": "pose", "data": {kind: text}})
                if time.time() - map_at > MAP_EVERY:
                    map_at = time.time()
                    me = str(self.my_entity or "") if self.world != "out" and self.mp_active() and partners else ""
                    if me != self.__dict__.get("pose_me_text"):
                        in_dir.mkdir(parents=True, exist_ok=True)
                        write_atomic(in_dir / "me.cfg", me)          # (the B1 script sends this Zoi's skeleton)
                        self.pose_me_text = me
                    items = []
                    for r in self.remotes.values():
                        e = self.puppet_ids.get(r.pid)
                        if e and r.spawned and not r.hidden and self.pose_live(r):
                            items.append(f"{r.pid}={e}.{r.__dict__.get('outfit_ver', 0)}")
                    text = ";".join(items)
                    rate = str(self.pose_rate(partners))
                    if rate != self.__dict__.get("pose_rate_text"):
                        in_dir.mkdir(parents=True, exist_ok=True)
                        write_atomic(in_dir / "poserate.cfg", rate)    # (the B1 script sends at this rate)
                        self.pose_rate_text = rate
                    self.tell_skel()
                    if text != self.pose_map_text:
                        in_dir.mkdir(parents=True, exist_ok=True)
                        write_atomic(in_dir / "posemap.cfg", text)
                        self.pose_map_text = text
            except (OSError, ConnectionError):
                pass
            except Exception as e:                       # noqa: BLE001
                log.debug("pose loop: %s", e)
            await asyncio.sleep(SEND_EVERY)
