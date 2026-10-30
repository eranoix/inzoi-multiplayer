"""Your inZOI mods, and getting the ones your friends have.

A mod is a folder with a mod_manifest.json anywhere under Documents/inZOI/Mods: InGame/<mod> for script and UI mods,
88849/<project>_<file>/<mod> for the ones installed from CurseForge. The companion tells the server which enabled
mods this PC has (id, version, folder, and a sha256 per file). The server answers with the mods everyone should have:
the host's when someone hosts and plays, everyone's together on a stand-alone server (a Raspberry Pi). Files this PC
is missing come over a second connection (Transfer), are checked against their sha256, and only then go into the
Mods folder. A mod that gets replaced is moved aside to %APPDATA%/inzoi-mp/replaced-mods, never deleted. New mods
load when inZOI next starts.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path

import install

MODS_DIR = Path(os.environ.get("INZOI_MP_MODS_DIR") or install.INSTALL_DIR.parents[1])   # Documents/inZOI/Mods
DATA = Path(os.environ.get("APPDATA", Path.home())) / "inzoi-mp"
CHUNK = 512 * 1024
MANIFEST = "mod_manifest.json"
RUNTIME = {".cfg"}               # what mods write while the game runs (uimod.cfg_save): not part of the mod
log = logging.getLogger("mods")


class Busy(Exception):
    """A mod's files are in use (inZOI is running with it loaded): it can be replaced once the game is closed."""


def safe_rel(rel: str) -> Path | None:
    """A folder or file path that came from another player: plain names only (no "..", drives or hidden folders)."""
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") or ":" in p for p in parts):
        return None
    return Path(*parts)


class Hashes:
    """sha256 of each file, remembered by size and modification time, so a rescan only reads files that changed."""

    def __init__(self, path: Path = DATA / "mod_hashes.json"):
        self.path = path
        try:
            self.known = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            self.known = {}
        self.dirty = False

    def get(self, f: Path) -> str:
        st = f.stat()
        key, stamp = str(f), [st.st_size, st.st_mtime_ns]
        if self.known.get(key, [None])[:2] != stamp:
            digest = hashlib.sha256()
            with f.open("rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(block)
            self.known[key] = stamp + [digest.hexdigest()]
            self.dirty = True
        return self.known[key][2]

    def save(self):
        if self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.known), "utf-8")
            self.dirty = False


def scan(root: Path | None = None, hashes: Hashes | None = None) -> list[dict]:
    """Every mod under root: {id, name, version, path, enabled, files: [{p, h, s}]} (except this multiplayer mod)."""
    root, hashes = root or MODS_DIR, hashes or Hashes()
    mods, folders = [], []
    for mf in sorted(root.rglob("mod_manifest.json")) if root.exists() else []:
        folder = mf.parent
        rel = folder.relative_to(root)
        if any(p.startswith(".") or p.endswith(".inzoi-mp-new") for p in rel.parts) \
                or any(f in folder.parents for f in folders):
            continue
        try:
            man = json.loads(mf.read_text("utf-8-sig"))
        except (OSError, ValueError):
            continue
        mid = str(man.get("ProjectName") or folder.name)
        if mid == install.MOD_ID:
            continue
        folders.append(folder)
        files = []
        for f in sorted(folder.rglob("*")):
            if f.is_file() and f.suffix.lower() not in RUNTIME and not any(p.startswith(".") for p in
                                                                           f.relative_to(folder).parts):
                try:
                    files.append({"p": f.relative_to(folder).as_posix(), "h": hashes.get(f), "s": f.stat().st_size})
                except OSError:
                    pass
        mods.append({"id": mid, "name": str(man.get("FriendlyName") or mid), "version": str(man.get("Version", "")),
                     "built": str(man.get("PackageTime") or ""), "path": rel.as_posix(),
                     "enabled": bool(man.get("bEnable", True)), "files": files})
    hashes.save()
    return mods


def fingerprint(mod: dict) -> frozenset:
    """What makes two copies of a mod the same. Not mod_manifest.json: the game rewrites it on every PC (load order,
    enabled, validated game version), so it differs between players who have the very same mod."""
    return frozenset((f["p"], f["h"]) for f in mod["files"] if f["p"] != MANIFEST)


def fp(mod: dict) -> str:
    """fingerprint() as a short text: what a server's info gives for each of its mods (the server's mod_fp)."""
    return hashlib.sha256("\n".join(sorted(f"{p}:{h}" for p, h in fingerprint(mod))).encode()).hexdigest()[:16]


def missing(modset: list[dict], local: list[dict]) -> list[dict]:
    """The mods of the set this PC doesn't have exactly (absent, disabled, or a different version)."""
    have = {m["id"]: fingerprint(m) for m in local if m.get("enabled", True)}
    return [m for m in modset if m.get("id") != install.MOD_ID and have.get(m["id"]) != fingerprint(m)]


def offer_key(mods: list[dict]) -> str:
    return hashlib.sha1(json.dumps(sorted((m["id"], sorted(fingerprint(m))) for m in mods)).encode()).hexdigest()[:16]


def target_of(mod: dict, root: Path | None = None) -> Path:
    root = root or MODS_DIR
    rel = safe_rel(mod.get("path", "")) or safe_rel("InGame/" + mod["id"])
    if rel is None or mod["id"] == install.MOD_ID:
        raise ValueError(f"unsafe mod location for {mod.get('id')!r}")
    target = (root / rel).resolve()
    if root.resolve() not in target.parents:
        raise ValueError(f"unsafe mod location for {mod.get('id')!r}")
    return target


def install_mod(mod: dict, blob, local: list[dict], root: Path | None = None) -> Path:
    """Put a downloaded mod in place. blob(sha) -> Path of that verified file. Older copies of the same mod (any
    folder, enabled or not) are moved to %APPDATA%/inzoi-mp/replaced-mods first. Raises Busy when the game has them
    open."""
    root = root or MODS_DIR
    target = target_of(mod, root)
    olds = sorted({root / m["path"] for m in local if m["id"] == mod["id"]} | ({target} if target.exists() else set()))
    for old in olds:                          # in use by the game? (a folder with open files can't be renamed)
        probe = old.with_name(old.name + ".inzoi-mp-probe")
        try:
            old.rename(probe)
            probe.rename(old)
        except FileNotFoundError:
            pass
        except PermissionError as e:
            raise Busy(str(e)) from None
    new = target.with_name(target.name + ".inzoi-mp-new")
    if new.exists():
        shutil.rmtree(new)
    for f in mod["files"]:
        rel = safe_rel(f["p"])
        if rel is None:
            raise ValueError(f"unsafe file name in {mod['id']}: {f['p']!r}")
        dst = new / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(blob(f["h"]), dst)
    aside = DATA / "replaced-mods" / f"{mod['id']}-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        for i, old in enumerate(olds):
            if old.exists():
                aside.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(aside.with_name(aside.name + (f"-{i}" if i else ""))))
        new.rename(target)
    except PermissionError as e:
        raise Busy(str(e)) from None
    return target


class Transfer:
    """A second connection to the server, only for mod files, so big transfers don't hold up anyone's Zoi."""

    def __init__(self, address: tuple, key: str, name: str):
        self.address, self.key, self.name = address, key, name

    async def __aenter__(self):
        host, port, password = self.address
        self.r, self.w = await asyncio.open_connection(host, port, limit=32 * 1024 * 1024)
        await self.send({"t": "hello", "protocol": 1, "key": self.key, "name": self.name, "password": password,
                         "files": True})
        m = await self.expect("files_ok")
        if m.get("t") != "files_ok":
            raise ConnectionError(m.get("error") or "the server refused the file connection")
        return self

    async def __aexit__(self, *exc):
        self.w.close()

    async def send(self, msg: dict):
        self.w.write((json.dumps(msg, separators=(",", ":")) + "\n").encode())
        await self.w.drain()

    async def expect(self, *types) -> dict:
        while True:
            line = await asyncio.wait_for(self.r.readline(), 60)
            if not line:
                raise ConnectionError("the server closed the file connection")
            m = json.loads(line)
            if m.get("t") in types or m.get("t") == "error":
                return m

    async def not_on_server(self, shas: list[str]) -> list[str]:
        await self.send({"t": "blob_check", "h": shas})
        return (await self.expect("blob_missing"))["h"]

    async def upload(self, path: Path, sha: str, progress=None):
        """Send one file. blob_bad (a bad piece; the server wants it again from offset 0): one more try, from 0."""
        size = path.stat().st_size
        for attempt in range(2):
            offset = 0
            with path.open("rb") as f:
                while True:
                    data = f.read(CHUNK)
                    end = offset + len(data) >= size
                    await self.send({"t": "blob_put", "h": sha, "o": offset, "d": base64.b64encode(data).decode(),
                                     "end": end})
                    offset += len(data)
                    if progress and not attempt:
                        progress(len(data))
                    if end:
                        break
            m = await self.expect("blob_ok", "blob_bad")
            if m.get("t") == "blob_ok":
                return
            if m.get("t") != "blob_bad" or m.get("o", 0) != 0 or attempt:
                break
            log.info("upload of %s: the server asked for it again (%s)", path.name, m.get("error"))
        raise ConnectionError(f"upload of {path.name} failed: {m.get('error')}")

    async def download(self, sha: str, dest: Path, progress=None) -> str | None:
        """Fetch one file into dest (checked against its sha256). Returns None, or why it isn't available yet."""
        part = dest.with_name(dest.name + ".part")
        part.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with part.open("wb") as f:
            offset = 0
            while True:
                await self.send({"t": "blob_get", "h": sha, "o": offset})
                m = await self.expect("blob")
                if m.get("error"):
                    return m["error"]
                data = base64.b64decode(m["d"])
                f.write(data)
                digest.update(data)
                offset += len(data)
                if progress:
                    progress(len(data))
                if m.get("end"):
                    break
        if digest.hexdigest() != sha:
            part.unlink()
            raise ConnectionError("a mod file arrived damaged")
        os.replace(part, dest)
        return None
