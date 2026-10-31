"""Telemetry: everything that goes between the two games and what each game makes of it, in one file per PC.

CONFIG_DIR/telemetry.jsonl, one JSON object per line, "t" = this PC's clock (s), "k" = kind:
  out  {st}            a state this player sent: position, yaw, site, act (bag/target/obj), anim, outfit, clock, ts
  in   {p, st}         a state received from player p
  pup  {p, e, x, y, z, yaw, site, bag, action, target, anim, err}
                       the partner's Zoi as it is in this game (sampled twice a second), err = cm from where the
                       partner's newest state says they are
  lua  {kind, text}    what the in-game controller reported (an act done, an object not found, a Zoi placed...)
  note {text}          anything else worth seeing on the timeline

The partner's file comes over with /partner/telemetry (the last minutes, compressed); tools/telemetry_report.py lines
the two up: what one player was really doing next to what the other one's game showed.
"""
import base64
import json
import os
import threading
import time
import zlib
from pathlib import Path

FILE = Path(os.environ.get("APPDATA", Path.home())) / "inzoi-mp" / "telemetry.jsonl"
MAX_BYTES = 40 << 20


class Telemetry:
    def __init__(self, path: Path = FILE):
        self.path = path
        self.lock = threading.Lock()
        self.f = None

    def rec(self, kind: str, **data):
        line = json.dumps({"t": round(time.time(), 3), "k": kind, **data}, ensure_ascii=False, default=str)
        with self.lock:
            try:
                if self.f is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                        old = self.path.with_suffix(".old.jsonl")
                        old.unlink(missing_ok=True)
                        self.path.rename(old)
                    self.f = open(self.path, "a", encoding="utf-8")
                self.f.write(line + "\n")
                self.f.flush()
            except OSError:
                self.f = None

    def recent(self, minutes: float = 15) -> list[str]:
        """The lines of the last `minutes` (this file and the rotated one)."""
        since = time.time() - minutes * 60
        out = []
        for p in (self.path.with_suffix(".old.jsonl"), self.path):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        try:
                            if json.loads(line).get("t", 0) >= since:
                                out.append(line.rstrip("\n"))
                        except ValueError:
                            pass
            except OSError:
                pass
        return out


def pack(lines: list[str]) -> str:
    return base64.b64encode(zlib.compress("\n".join(lines).encode("utf-8"), 6)).decode()


def unpack(data: str) -> list[str]:
    return zlib.decompress(base64.b64decode(data)).decode("utf-8", errors="replace").splitlines()
