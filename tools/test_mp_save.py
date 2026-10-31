"""Tests of the multiplayer save, with a stand-in for the game and a temporary save folder:
- a copied save gets its own folder name in its header, and both checksums stay valid
- first time in a world: the player is asked; "copy" saves, goes to the menu, copies and loads the copy
- at the main menu the multiplayer save is loaded; loaded that way, multiplayer is on
- in another save (found out from the save it writes), multiplayer pauses; "switch" goes back
- a save the game loaded without the program is asked about ("Is this your multiplayer save?")

    python tools/test_mp_save.py
"""
import asyncio
import json
import os
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))


def fake_save(folder: str, file: str, slot: str = "Sawyer") -> bytes:
    """A minimal save file laid out like the game's: IZMS header (checksum over the file with that field zeroed),
    then an IZTB block holding the JSON header with its own checksum, then some data."""
    js = json.dumps({"SaveType": "Save", "FolderName": folder, "FileName": file, "SlotName": slot},
                    indent="\t").encode()
    first = 44
    block = b"IZTB" + struct.pack("<III", len(js) + 8, len(js), zlib.crc32(js)) + js
    rest = b"DLCD" + os.urandom(200)
    body = bytearray(b"IZMS" + struct.pack("<II", 17, 1) + b"\0\0\0\0" + struct.pack("<II", 0, 2)
                     + struct.pack("<II", first, first + len(block)) + b"\0" * 12 + block + rest)
    body[16:20] = struct.pack("<I", len(body))
    body[12:16] = struct.pack("<I", zlib.crc32(bytes(body)))
    return bytes(body)


def check_file(data: bytes, folder: str):
    import saves
    z = bytearray(data)
    z[12:16] = b"\0\0\0\0"
    assert struct.unpack("<I", data[12:16])[0] == zlib.crc32(bytes(z)), "file checksum"
    first = struct.unpack("<I", data[24:28])[0]
    _, jlen, crc = struct.unpack("<III", data[first + 4:first + 16])
    js = data[first + 16:first + 16 + jlen]
    assert zlib.crc32(js) == crc, "header checksum"
    h = json.loads(js)
    assert h["FolderName"] == h["FileName"] == folder and h["SaveType"] == "Save", h
    return h


class StandIn:
    """The game: a main menu, saves on disk (in saves.SAVES), loading, and the world the player is in."""

    def __init__(self, saves_mod):
        self.saves_mod, self.phase, self.loaded, self.calls = saves_mod, "in_game", None, []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "game.get_state":
            return {"phase": self.phase, "is_lobby": self.phase == "lobby", "loading": False}
        if name == "game.goto_lobby":
            self.write(self.loaded)                                # it saves first
            self.phase = "lobby"
            return {}
        if name == "game.load":
            self.loaded, self.phase = kw["folder"], "in_game"
            return {}
        return {}

    def write(self, folder):
        d = self.saves_mod.SAVES / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{folder}.usvx").write_bytes(fake_save(folder, folder))
        os.utime(d / f"{folder}.usvx")

    def saves(self, limit=10, autosave=True, refresh=False):
        out = []
        for d in self.saves_mod.SAVES.iterdir():
            for f in d.glob("*.usvx"):
                h = json.loads(f.read_bytes()[60:60 + struct.unpack("<I", f.read_bytes()[52:56])[0]])
                out.append({"folder": h["FolderName"], "file": h["FileName"], "slot": h["SlotName"],
                            "first_name": "Bella", "last_name": "Sawyer", "is_broken": False,
                            "saved_time": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(f.stat().st_mtime))})
        return sorted(out, key=lambda e: e["saved_time"], reverse=True)[:limit]

    def world(self):
        return "play" if self.phase == "in_game" else "out"


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["APPDATA"] = str(Path(tmp) / "appdata")
        os.environ["INZOI_MP_DOCUMENTS"] = str(Path(tmp) / "Documents")
        os.environ["INZOI_MP_GAME_URL"] = "http://127.0.0.1:9/mcp"
        import companion
        import saves
        saves.SAVES, saves.MODSAVE = Path(tmp) / "SaveGames", Path(tmp) / "ModSave"

        # The header rewrite on its own.
        out = saves.retitle(fake_save("20261010-214421", "20261010-214421_AutoSave_3", "Sawyer_AutoSave_3"),
                            "20261011-020304", "20261011-020304")
        assert check_file(out, "20261011-020304")["SlotName"] == "Sawyer"

        game = StandIn(saves)
        game.write("20260101-000000")                              # the player's normal save, loaded
        game.loaded = "20260101-000000"
        comp = companion.Companion({"key": "k" * 20, "name": "me", "server": "x", "port": 1, "password": ""})
        comp.game = game

        # First time in a world: asked, multiplayer waits.
        await comp.watch_save(entered=True)
        await comp.watch_save(entered=False)
        assert comp.save_box["key"] == "setup" and not comp.mp_active()

        # "copy": saved, at the menu, copied, the copy loaded.
        comp.save_action("setup", "copy")
        for _ in range(50):
            await asyncio.sleep(0.05)
            if not comp.save_busy:
                break
        mp = comp.cfg["mp_save"]
        assert mp["folder"] != "20260101-000000" and game.loaded == mp["folder"], (mp, game.loaded)
        check_file((saves.SAVES / mp["folder"] / f"{mp['folder']}.usvx").read_bytes(), mp["folder"])
        assert (saves.SAVES / "20260101-000000" / "20260101-000000.usvx").exists(), "the original stays"
        await comp.watch_save(entered=True)
        assert comp.mp_active() and comp.save_box is None

        # Back at the main menu next time: the multiplayer save is loaded by itself.
        comp.auto_loaded, game.phase, game.loaded = False, "lobby", None
        await comp.at_main_menu()
        assert game.loaded == mp["folder"]
        await comp.watch_save(entered=True)
        assert comp.mp_active()

        # The player loads their normal save themselves: asked, and once the game saves there, it's known.
        time.sleep(1.1)
        game.loaded = "20260101-000000"
        await comp.watch_save(entered=True)
        await comp.watch_save(entered=False)
        assert not comp.mp_active() and comp.save_box["key"] == "confirm"
        comp.save_box = None
        comp.save_session["checked"] = 0
        time.sleep(1.1)
        game.write("20260101-000000")
        await comp.watch_save(entered=False)
        assert comp.save_session["folder"] == "20260101-000000" and comp.save_session["mp"] is False
        assert comp.save_box["key"] == "paused"

        # "switch": back to the multiplayer save.
        comp.save_action("paused", "switch")
        for _ in range(50):
            await asyncio.sleep(0.05)
            if not comp.save_busy:
                break
        assert game.loaded == mp["folder"]
        await comp.watch_save(entered=True)
        assert comp.mp_active()

        # Saying "yes, this is it" in a save made with the game's own Save As: it becomes the multiplayer save.
        game.loaded = "20260202-020202"
        await comp.watch_save(entered=True)
        comp.save_action("confirm", "yes")
        assert comp.mp_active()
        time.sleep(1.1)
        game.write("20260202-020202")
        comp.save_session["checked"] = 0
        await comp.watch_save(entered=False)
        assert comp.cfg["mp_save"]["folder"] == "20260202-020202"

        # Moving to another lot ("go to my partner"): a loading screen, no main menu, same Zoi and map: same save.
        game.saw_lobby, game.last_state = True, {"possessed_entity_id": 7, "level_name": "RedCity_Map"}
        comp.loading_save = "20260202-020202"
        await comp.watch_save(entered=True)
        assert comp.mp_active()
        game.saw_lobby = False
        await comp.watch_save(entered=True)                       # (nothing loaded by the program)
        assert comp.mp_active() and comp.save_box is None, "a lot change keeps multiplayer on"
        # Another save loaded from the game's own menu (no main menu either), another Zoi: not taken for it.
        game.saw_lobby, game.last_state = False, {"possessed_entity_id": 99, "level_name": "RedCity_Map"}
        await comp.watch_save(entered=True)
        assert not comp.mp_active(), "a different Zoi means another save: multiplayer waits"
        print("  ok: a lot change keeps the save; another save loaded in the game doesn't pass for it")

        # A server that still keeps a shared world (from "living together", now gone): a friend without a
        # multiplayer save is still asked to make their own copy (it used to be a silent dead end).
        os.environ["APPDATA"] = str(Path(tmp) / "appdata2")
        friend = companion.Companion({"key": "f" * 20, "name": "friend", "server": "x", "port": 1, "password": ""})
        friend.game = game
        friend.world_meta = {"version": 3, "by": "host", "files": [], "name": "Host's world"}
        game.loaded = "20260101-000000"
        await friend.watch_save(entered=True)
        await friend.watch_save(entered=False)
        assert friend.save_box and friend.save_box["key"] == "setup", friend.save_box
        print("  ok: a stale shared world on the server doesn't block a friend's own save")
    print("multiplayer save tests passed")


if __name__ == "__main__":
    asyncio.run(main())
