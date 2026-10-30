"""The shared world, this PC's side: packing this PC's copy of the world for the server, and installing the server's
newest world as this PC's multiplayer save.

What an upload holds (paths inside it):
    world.usvx       the newest save of the multiplayer save's folder (its main slot or an autosave)
    pictures/...     that save's Portrait and FamilyPortrait pictures
    ModInfo.json     the mods it was saved with (Documents/inZOI/ModSave/...)

Installing writes the world into the multiplayer save's folder as its main slot (the save's header renamed to that
folder, see saves.retitle), so the game lists and loads it like any save.
"""
import os
import shutil
import time
from pathlib import Path

import mods
import saves


def newest_slot(folder: str) -> Path | None:
    """The newest save file in a save folder: the main slot or an autosave, whichever the game wrote last."""
    d = saves.SAVES / folder
    files = sorted(d.glob("*.usvx"), key=lambda f: f.stat().st_mtime) if d.is_dir() else []
    return files[-1] if files else None


def pack(folder: str, hashes) -> tuple[list[dict], dict, float]:
    """The world as files to upload: ([{p, h, s}], {sha: local path}, the save's modification time)."""
    slot = newest_slot(folder)
    if slot is None:
        raise FileNotFoundError(f"no save in {folder}")
    entries = [("world.usvx", slot)]
    pictures = slot.with_suffix("")                          # SaveGames/<folder>/<file>/
    if pictures.is_dir():
        entries += [("pictures/" + f.relative_to(pictures).as_posix(), f) for f in sorted(pictures.rglob("*"))
                    if f.is_file()]
    modinfo = saves.MODSAVE / folder / slot.stem / "ModInfo.json"
    if modinfo.is_file():
        entries.append(("ModInfo.json", modinfo))
    files, paths = [], {}
    for p, f in entries:
        h = hashes.get(f)
        files.append({"p": p, "h": h, "s": f.stat().st_size})
        paths[h] = f
    hashes.save()
    return files, paths, slot.stat().st_mtime


def snapshot(folder: str) -> dict:
    """{path: (size, mtime_ns)} of every file of a save folder (SaveGames/<folder>/ and ModSave/<folder>/): it
    changes while the game writes a save (temp files come and go, the slot grows, pictures are rewritten)."""
    out = {}
    for root in (saves.SAVES / folder, saves.MODSAVE / folder):
        for f in root.rglob("*") if root.is_dir() else []:
            try:
                st = f.stat()
            except OSError:                                  # (gone between listing and stat: still being written)
                out[str(f)] = (-1, 0)
                continue
            if f.is_file():
                out[str(f)] = (st.st_size, st.st_mtime_ns)
    return out


def wait_stable(folder: str, settle: float = 3.0, timeout: float = 90.0, poll: float = 0.5,
                clock=time.monotonic, sleep=time.sleep) -> bool:
    """Wait until the save folder's files stay the same (sizes and times) for `settle` seconds: the game has
    finished writing. False: still changing after `timeout` (try again later)."""
    end = clock() + timeout
    last, since = snapshot(folder), clock()
    while clock() < end:
        if clock() - since >= settle:
            return True
        sleep(poll)
        now = snapshot(folder)
        if now != last:
            last, since = now, clock()
    return False


class StillWriting(OSError):
    """The game is (still) writing the save: nothing was packed."""


def pack_stable(folder: str, hashes, settle: float = 3.0, timeout: float = 90.0, **kw):
    """pack(), but only from a save the game has finished writing: waits for the files to settle first, and checks
    nothing changed while they were read. Raises StillWriting otherwise."""
    if not wait_stable(folder, settle, timeout, **kw):
        raise StillWriting(f"the game is still writing {folder}")
    before = snapshot(folder)
    out = pack(folder, hashes)
    if snapshot(folder) != before:
        raise StillWriting(f"the game wrote {folder} while it was being read")
    return out


def install(files: list[dict], blob, folder: str | None) -> str:
    """Make a downloaded world this PC's multiplayer save: written into folder (its main slot replaced) or, without
    one, a new save folder. blob(sha) -> Path of that verified file. Returns the folder."""
    folder = folder or saves.new_folder_name()
    by_path = {f["p"]: f for f in files}
    if "world.usvx" not in by_path:
        raise ValueError("the shared world has no save file")
    data = saves.retitle(blob(by_path["world.usvx"]["h"]).read_bytes(), folder, folder)
    d = saves.SAVES / folder
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{folder}.usvx.mp-new"
    tmp.write_bytes(data)
    os.replace(tmp, d / f"{folder}.usvx")
    new_pictures, pictures = d / f"{folder}.mp-new", d / folder
    if new_pictures.exists():
        shutil.rmtree(new_pictures)
    for f in files:
        rel = mods.safe_rel(f["p"][len("pictures/"):]) if f["p"].startswith("pictures/") else None
        if rel:
            (new_pictures / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(blob(f["h"]), new_pictures / rel)
    if pictures.exists():
        shutil.rmtree(pictures)                              # the previous version's pictures of this same save
    if new_pictures.exists():
        new_pictures.rename(pictures)
    if "ModInfo.json" in by_path:
        mi = saves.MODSAVE / folder / folder
        mi.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(blob(by_path["ModInfo.json"]["h"]), mi / "ModInfo.json")
    return folder
