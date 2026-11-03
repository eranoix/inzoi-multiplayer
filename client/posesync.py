"""Skeleton mirroring, the companion's half (mixed into the Companion). The in-game half is lua/B1/Main.lua.

What one PC shows of a player's Zoi is exactly what that player's own game computed, bone by bone: nothing is
re-enacted (no interaction queued, no AI deciding which side of the bed). The B1 script writes this player's frames to
<mod>/out/posek.cfg (a keyframe, every second) and <mod>/out/posed.cfg (the bones that differ from it, 15 times a
second); this module sends each new one to every partner as {"t":"event","kind":"pose","data":{"k"|"d": text}} and
writes what partners send to <mod>/in/posek_<pid>.cfg / posed_<pid>.cfg, plus <mod>/in/posemap.cfg ("pid=entity.v;":
which Zoi here is whose, v changes when their outfit does, so the rig is rebuilt on the new clothes).

While a partner's frames keep coming (pose_live), the older re-enactment is off for them: no actions queued on their
Zoi (except the couple's own interactions with this player), no montage copying, no pose pinning.
"""
import asyncio
import logging
import os
import time

import install

log = logging.getLogger("posesync")

LIVE_FOR = 2.0            # s without a frame from a partner before the re-enactment takes over again
SEND_EVERY = 1 / 30       # s between looks at the B1 script's files
MAP_EVERY = 0.5


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
                    write_atomic(folder / f"pose{kind}_{frm}.cfg", text)
        except OSError as e:
            log.debug("writing a pose: %s", e)
            return
        first = time.time() - self.pose_from.get(frm, 0) > LIVE_FOR
        self.pose_from[frm] = time.time()
        self.pose_counts["got"] += 1
        if first:
            r = self.remotes.get(frm)
            name = r.name if r else frm
            log.info("%s's skeleton is coming in: their Zoi here shows exactly what their game shows", name)
            self.tele.rec("note", text=f"{name}: skeleton frames coming in (exact mirroring on)")
            if r:
                self.leave_reenactment(r)

    def leave_reenactment(self, r):
        """Their frames drive their Zoi now: stop what the re-enactment had going on it."""
        if r.pid in self.puppet_ids:
            self.mail.op("act_stop", key=r.pid)
            self.mail.op("pose", key=r.pid, on=False)
        r.act_sig, r.pose_on = None, False

    async def pose_loop(self):
        out_dir = install.INSTALL_DIR / "out"
        in_dir = install.INSTALL_DIR / "in"
        map_at = 0.0
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
                        # (to everyone, through the server: it keeps the latest frames for whoever joins later)
                        await self.send({"t": "event", "kind": "pose", "data": {kind: text}})
                        self.pose_counts["sent"] += 1
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
                    if text != self.pose_map_text:
                        in_dir.mkdir(parents=True, exist_ok=True)
                        write_atomic(in_dir / "posemap.cfg", text)
                        self.pose_map_text = text
            except (OSError, ConnectionError):
                pass
            except Exception as e:                       # noqa: BLE001
                log.debug("pose loop: %s", e)
            await asyncio.sleep(SEND_EVERY)
