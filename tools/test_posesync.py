"""The skeleton's send rate (client/posesync.py pose_rate, Unreal-style relevancy): full rate (30 a second) while a
partner's Zoi is near mine, a few frames a second once every partner is far; two distances so it doesn't flap at the
edge; unknown positions -> full rate.

    python tools/test_posesync.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["APPDATA"] = tempfile.mkdtemp()                 # (never the real config)
os.environ["INZOI_MP_DOCUMENTS"] = tempfile.mkdtemp()      # (never the real game's Mods folder)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import posesync  # noqa: E402


class R:
    def __init__(self, x, y, age=0.0):
        self.state, self.state_at = {"x": x, "y": y}, time.time() - age


class P(posesync.PoseSync):
    pass


def main():
    p = P()
    assert p.pose_rate([R(0, 0)]) == posesync.FULL_RATE           # (my own position unknown yet: full)
    p.fast_pos = {"x": 0.0, "y": 0.0}
    assert p.pose_rate([R(1000, 0)]) == posesync.FULL_RATE       # 10 m
    assert p.pose_rate([R(6000, 0)]) == posesync.FULL_RATE       # 60 m: between the two, stays as it was (near)
    assert p.pose_rate([R(8000, 0)]) == posesync.FAR_RATE        # 80 m: far
    assert p.pose_rate([R(6000, 0)]) == posesync.FAR_RATE        # 60 m again: stays far (no flapping)
    assert p.pose_rate([R(4000, 0)]) == posesync.FULL_RATE       # 40 m: near again
    assert p.pose_rate([R(8000, 0), R(100, 100)]) == posesync.FULL_RATE   # any one partner near
    p.pose_near = False
    assert p.pose_rate([R(100, 0, age=60)]) == posesync.FULL_RATE  # (their position is old: unknown -> full)
    assert p.pose_rate([]) == posesync.FULL_RATE
    print("skeleton rate: full while a partner is near, a few frames when all are far, no flapping: ok")

    # frames compressed on the wire: "z:" + base64(zlib), read back exactly; an old partner gets them plain
    text = "12|100,200,300,9000|A|" + ",".join(f"{b},{b * 10},0,0,0,0,0,10000" for b in range(1260))
    z = posesync.pack(text)
    assert z.startswith("z:") and len(z) < len(text) / 3 and posesync.unpack(z) == text, (len(z), len(text))
    assert posesync.unpack(text) == text and posesync.unpack("z:not base64!") is None
    old, new = R(0, 0), R(0, 0)
    old.version, new.version = "0.9.60", "0.9.61"
    assert p.pose_zip_ok([new]) and not p.pose_zip_ok([new, old]) and not p.pose_zip_ok([R(0, 0)])
    # receiving one: written to in/ as plain text (the B1 script reads it)
    p.pose_init()
    p.puppet_ids, p.remotes, p.mail = {}, {}, None
    p.tele = type("T", (), {"rec": lambda *a, **k: None})()
    p.on_pose({"from": "p1", "data": {"d": z}})
    import install
    got = (install.INSTALL_DIR / "in" / "posed_p1.cfg").read_text("utf-8")
    assert got == text, got[:80]
    print(f"frames compressed on the wire ({len(text)} -> {len(z)} chars), plain for an older partner: ok")

    # the partner card's network line: frames a second over the last 3 s, the newest one's age, my send rate
    p.pose_times = {"p2": [time.time() - 2.5 + i * 0.1 for i in range(25)]}
    p.pose_from["p2"] = time.time() - 0.04
    net = p.pose_net("p2")
    assert 7 <= net["frames_s"] <= 9 and 30 <= net["frame_ms"] <= 200 and net["send_s"] in (2, 30), net
    print("network line: frames a second, the newest frame's age, my send rate: ok")
    print("posesync tests passed")


main()
