"""Puts the inZOI Multiplayer mod where the game looks for it and loads it into the running game.

The mod ships next to the companion (mod/InZOIMultiplayer in the source tree, or mod/ in a release). It is copied to
Documents/inZOI/Mods/InGame/InZOIMultiplayer and mounted with the game's own `uimod.mount` / `luamod.mount`
commands, so no Mod Kit is needed. The B2 mailbox files the companion writes live inside that folder too.

    python client/install.py            install/update the mod and mount it in the running game
"""
import json
import os
import shutil
import sys
from pathlib import Path

MOD_ID = "InZOIMultiplayer"
MAILBOX_FILES = {"MPCmd.lua", "MPBulk.lua", "MPSeq.lua", "MPCmd.tmp", "MPBulk.tmp", "MPSeq.tmp", "inject.json"}


def _documents() -> Path:
    """The real Documents folder (it can be redirected, e.g. into OneDrive). Tests set INZOI_MP_DOCUMENTS to a
    folder of their own, so nothing they do reaches the real game: its mod, mailbox, mods and saves all live here."""
    if os.environ.get("INZOI_MP_DOCUMENTS"):
        return Path(os.environ["INZOI_MP_DOCUMENTS"])
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:      # CSIDL_PERSONAL
                return Path(buf.value)
        except (OSError, AttributeError):
            pass
    return Path.home() / "Documents"


INSTALL_DIR = _documents() / "inZOI" / "Mods" / "InGame" / MOD_ID
MAILBOX_DIR = INSTALL_DIR / "lua" / "B2"


def source_dir() -> Path:
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for cand in (here / "mod" / MOD_ID, here.parent / "mod" / MOD_ID, here / "mod", here.parent / "mod"):
        if (cand / "lua" / "B2" / "Main.lua").exists():
            return cand
    raise FileNotFoundError("can't find the mod files (mod/InZOIMultiplayer) next to the companion")


def _manifest(files: list[str], version: str) -> dict:
    return {
        "ProjectName": MOD_ID,
        "FriendlyName": "inZOI Multiplayer",
        "Description": "Play inZOI together: see your friends' Zois in your world and live side by side.",
        "Author": "",
        "Version": version,
        "Url": "",
        "Dependencies": [],
        "Assets": [],
        "Maps": [],
        "NonAssets": files,
        "FileChecksums": [],
        "bEnable": True,
        "LoadOrder": 3,
    }


def install(src: Path | None = None) -> bool:
    """Copy the mod into the game's Mods folder. Returns True if anything changed."""
    src = src or source_dir()
    changed = False
    files = []
    for f in sorted(src.rglob("*")):
        if not f.is_file() or f.name in MAILBOX_FILES or f.name == "mod_manifest.json":
            continue
        rel = f.relative_to(src)
        files.append("/" + rel.as_posix())
        dst = INSTALL_DIR / rel
        if not dst.exists() or dst.read_bytes() != f.read_bytes():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dst)
            changed = True
    shipped = {x.lstrip("/") for x in files}
    for sub in ("lua", "ui"):                           # files a newer version dropped
        for f in list((INSTALL_DIR / sub).rglob("*")) if (INSTALL_DIR / sub).exists() else []:
            rel = f.relative_to(INSTALL_DIR).as_posix()
            if "/saved/" in "/" + rel:                    # the panel's own state (where it was left, its size)
                continue
            if f.is_file() and f.name not in MAILBOX_FILES and rel not in shipped:
                f.unlink()
                changed = True
    try:
        version = json.loads((src / f"{MOD_ID}.uplugin").read_text("utf-8")).get("VersionName", "0.1.0")
    except (OSError, ValueError):
        version = "0.1.0"
    text = json.dumps(_manifest([x for x in files if not x.endswith(".uplugin")], version), indent="\t")
    mf = INSTALL_DIR / "mod_manifest.json"
    if not mf.exists() or mf.read_text("utf-8") != text:
        mf.write_text(text, "utf-8")
        changed = True
    MAILBOX_DIR.mkdir(parents=True, exist_ok=True)
    return changed


def _run(call, cmds) -> str:
    out = []
    for cmd, args in cmds:
        try:
            r = call(cmd, **args)
            out.append(str(r.get("_text", r) if isinstance(r, dict) else r))
        except Exception as e:                          # noqa: BLE001 - report every failure, keep going
            out.append(f"{cmd}: {e}")
    return " | ".join(out)


def mount(call) -> str:
    """Load the mod's panel and in-game scripts into the running game. `call` is Game.call."""
    return _run(call, [("uimod.mount", {"mod_id": MOD_ID}), ("luamod.mount", {"mod_id": MOD_ID})])


def reload(call) -> str:
    """Pick up changed files: luamod.reload refreshes both Lua sides (a remount leaves per-Zoi B1 scripts stale)."""
    return _run(call, [("luamod.reload", {}), ("uimod.remount", {"mod_id": MOD_ID})])


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from game import Game
    changed = install()
    print("installed" if changed else "already up to date", "->", INSTALL_DIR)
    print(mount(Game().call))
    if changed:
        print(reload(Game().call))
