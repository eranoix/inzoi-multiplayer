"""The multiplayer save: each player plays multiplayer in a save of its own, so their normal saves stay untouched by
it (friends' Zois coming and going, the shared clock, friends' houses).

A save slot is Documents/inZOI/SaveGames/<folder>/<file>.usvx with its pictures in <folder>/<file>/, plus
Documents/inZOI/ModSave/<folder>/<file>/ModInfo.json (the mods it was saved with). The main slot's file is named like
its folder (a timestamp, YYYYMMDD-HHMMSS); autosaves add _AutoSave_<n>.

The game has no "save as" command, so the multiplayer copy is made on disk: the .usvx starts with a small header
(a JSON block, "IZTB", holding FolderName, FileName and SlotName) that must name the copy's own folder, or the game
would treat the copy as the original. The header and the whole file each carry a CRC32 (the file's is taken with its
own field zeroed); both are recomputed. New names are padded with spaces outside the JSON strings, so no length or
offset in the file changes.
"""
import json
import os
import re
import shutil
import struct
import time
import zipfile
import zlib
from pathlib import Path

import install

INZOI = install.INSTALL_DIR.parents[2]                 # Documents/inZOI
SAVES = INZOI / "SaveGames"
MODSAVE = INZOI / "ModSave"


def _set(js: bytes, key: str, value: str) -> bytes:
    """Replace "key": "<old>" with the new value, padding with spaces so the block keeps its length."""
    m = re.search(rb'"' + key.encode() + rb'": "([^"]*)"', js)
    if not m:
        raise ValueError(f"save header has no {key}")
    new = f'"{key}": "{value}"'.encode()
    old = m.group(0)
    if len(new) > len(old):
        raise ValueError(f"new {key} is longer than the old one")
    return js[:m.start()] + new + b" " * (len(old) - len(new)) + js[m.end():]


def retitle(data: bytes, folder: str, file: str) -> bytes:
    """The save file renamed to belong to folder/file, as a manual save (not an autosave)."""
    if data[:4] != b"IZMS":
        raise ValueError("not an inZOI save file")
    first = struct.unpack("<I", data[24:28])[0]                # offset of the first block: the header
    if data[first:first + 4] != b"IZTB":
        raise ValueError("unexpected save file layout")
    size, jlen, crc = struct.unpack("<III", data[first + 4:first + 16])
    start = first + 16
    js = data[start:start + jlen]
    if zlib.crc32(js) != crc:
        raise ValueError("the save file's header is damaged")
    slot = re.sub(r"_AutoSave_\d+$", "", json.loads(js).get("SlotName", ""))
    js = _set(_set(_set(js, "FolderName", folder), "FileName", file), "SaveType", "Save")
    if slot:
        js = _set(js, "SlotName", slot)
    out = bytearray(data[:first + 12] + struct.pack("<I", zlib.crc32(js)) + js + data[start + jlen:])
    out[12:16] = b"\0\0\0\0"
    out[12:16] = struct.pack("<I", zlib.crc32(bytes(out)))
    return bytes(out)


def header(path: Path) -> dict:
    data = path.read_bytes()[:4096]
    first = struct.unpack("<I", data[24:28])[0]
    _, jlen, _ = struct.unpack("<III", data[first + 4:first + 16])
    return json.loads(data[first + 16:first + 16 + jlen])


def new_folder_name() -> str:
    t = time.time()
    while True:
        name = time.strftime("%Y%m%d-%H%M%S", time.localtime(t))
        if not (SAVES / name).exists() and not (MODSAVE / name).exists():
            return name
        t += 1


def copy_save(folder: str, file: str) -> str:
    """Copy one save slot into a new save of its own. Returns the new folder name."""
    src = SAVES / folder / f"{file}.usvx"
    new = new_folder_name()
    data = retitle(src.read_bytes(), new, new)
    dst = SAVES / new
    dst.mkdir(parents=True)
    try:
        (dst / f"{new}.usvx").write_bytes(data)
        if (SAVES / folder / file).is_dir():
            shutil.copytree(SAVES / folder / file, dst / new)
        if (MODSAVE / folder / file).is_dir():
            shutil.copytree(MODSAVE / folder / file, MODSAVE / new / new)
    except OSError:
        shutil.rmtree(dst, ignore_errors=True)
        shutil.rmtree(MODSAVE / new, ignore_errors=True)
        raise
    return new


# ---------------------------------------------------------------------- local backups of the multiplayer save
# Before each session's load and before a downloaded world replaces it, the multiplayer save's folders
# (SaveGames/<folder>/ and ModSave/<folder>/) are zipped into %APPDATA%/inzoi-mp/backups; the newest KEEP stay.
# Each zip carries mp-backup.json: {folder, reason, created, files: [{p, s, m}]} (path inside the zip, size, mtime),
# so a restore puts the files' times back and a backup of an unchanged save is skipped.
BACKUPS = Path(os.environ.get("APPDATA", Path.home())) / "inzoi-mp" / "backups"
KEEP = 10
_MANIFEST = "mp-backup.json"


def _save_files(folder: str) -> list[tuple[str, Path]]:
    """(path in the zip, file) of every file of a save folder: SaveGames/... and ModSave/..."""
    out = []
    for prefix, root in (("SaveGames", SAVES / folder), ("ModSave", MODSAVE / folder)):
        if root.is_dir():
            out += [(f"{prefix}/{f.relative_to(root).as_posix()}", f) for f in sorted(root.rglob("*")) if f.is_file()]
    return out


def _listing(files) -> list[dict]:
    out = []
    for p, f in files:
        st = f.stat()
        out.append({"p": p, "s": st.st_size, "m": st.st_mtime})
    return out


def list_backups(folder: str | None = None) -> list[dict]:
    """Backups, newest first: {name, path, folder, reason, created, size}."""
    out = []
    for z in BACKUPS.glob("*.zip") if BACKUPS.is_dir() else []:
        try:
            with zipfile.ZipFile(z) as zf:
                man = json.loads(zf.read(_MANIFEST))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue
        if folder and man.get("folder") != folder:
            continue
        out.append({"name": z.name, "path": z, "folder": man.get("folder"), "reason": man.get("reason", ""),
                    "created": man.get("created", z.stat().st_mtime), "size": z.stat().st_size,
                    "files": man.get("files", [])})
    return sorted(out, key=lambda b: -b["created"])


def prune_backups(keep: int = KEEP):
    for b in list_backups()[keep:]:
        b["path"].unlink(missing_ok=True)


def backup(folder: str, reason: str = "manual", keep: int = KEEP) -> Path | None:
    """Zip the save folder. None: nothing to back up, or the newest backup of it already has these very files."""
    files = _save_files(folder)
    if not files:
        return None
    listing = _listing(files)
    newest = next(iter(list_backups(folder)), None)
    if newest and sorted((f["p"], f["s"], round(f["m"], 3)) for f in newest["files"]) == \
            sorted((f["p"], f["s"], round(f["m"], 3)) for f in listing):
        return None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    created = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(created))
    tag = re.sub(r"[^A-Za-z0-9-]", "", reason)[:20] or "backup"
    path = BACKUPS / f"{folder}_{stamp}_{tag}.zip"
    n = 1
    while path.exists():
        n += 1
        path = BACKUPS / f"{folder}_{stamp}_{tag}-{n}.zip"
    tmp = path.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for p, f in files:
                zf.write(f, p)
            zf.writestr(_MANIFEST, json.dumps({"folder": folder, "reason": reason, "created": created,
                                               "files": listing}))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    prune_backups(keep)
    return path


def _safe_member(name: str) -> tuple[str, Path] | None:
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if len(parts) < 2 or parts[0] not in ("SaveGames", "ModSave") or any(p == ".." or ":" in p for p in parts):
        return None
    return parts[0], Path(*parts[1:])


def restore(path: Path) -> str:
    """Put a backup back as its save folder (whatever is there now is replaced: back it up first). Returns the
    folder. Extracted next to the target first, then swapped in, so a failure leaves the save as it was."""
    with zipfile.ZipFile(path) as zf:
        man = json.loads(zf.read(_MANIFEST))
        folder = str(man.get("folder") or "")
        if not re.fullmatch(r"[\w.-]+", folder):
            raise ValueError("the backup names no save folder")
        times = {f["p"]: f["m"] for f in man.get("files", [])}
        roots = {"SaveGames": SAVES / folder, "ModSave": MODSAVE / folder}
        new = {k: r.with_name(r.name + ".mp-restore") for k, r in roots.items()}
        for d in new.values():
            if d.exists():
                shutil.rmtree(d)
        for info in zf.infolist():
            m = _safe_member(info.filename)
            if not m or info.is_dir():
                continue
            dst = new[m[0]] / m[1]
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
            if info.filename in times:
                os.utime(dst, (times[info.filename], times[info.filename]))
    for k, root in roots.items():
        old = root.with_name(root.name + ".mp-old")
        if old.exists():
            shutil.rmtree(old)
        if root.exists():
            root.rename(old)
        if new[k].exists():
            new[k].rename(root)
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
    return folder
