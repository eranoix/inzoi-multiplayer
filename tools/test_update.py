"""End-to-end test of self-updating, with the real built exe (dist/inZOI-Multiplayer.exe) and nothing real touched:
a stand-in "GitHub" offers version 9.9.9 (a copy of the same exe), the game address leads nowhere, the settings live
in a temporary folder, a stand-in game folder gets the main menu's button, and the panel API uses its own port. The
exe must find the update and offer it; on "Update" it downloads and checks it, swaps itself and comes back as a new
process from the same path (no window, no inZOI started: INZOI_MP_TEST).

    python tools/build_release.py && python tools/test_update.py
"""
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = 24343


def post(path, body=None, timeout=3):
    req = urllib.request.Request(f"http://127.0.0.1:{PANEL}{path}", json.dumps(body or {}).encode(), method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def wait(check, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = check()
            if r:
                return r
        except OSError:
            pass
        time.sleep(1)
    raise AssertionError(f"timed out waiting for {what}")


def procs_under(folder: Path) -> list[int]:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '" + str(folder) +
                          "\\*' } | ForEach-Object { $_.ProcessId }"], capture_output=True, text=True).stdout
    return [int(x) for x in out.split()]


def main():
    exe = ROOT / "dist" / "inZOI-Multiplayer.exe"
    keep = os.environ.get("KEEP_TEST_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(keep) if keep else Path(tmp)
        if keep:
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
        app, web, data, game = tmp / "app", tmp / "web", tmp / "appdata", tmp / "game"
        for d in (app, web, data, game):
            d.mkdir()
        (game / "inZOI-Win64-Shipping.exe").write_bytes(b"")
        shutil.copy2(exe, app / "inZOI Multiplayer.exe")
        shutil.copy2(exe, web / "inZOI-Multiplayer.exe")
        digest = hashlib.sha256((web / "inZOI-Multiplayer.exe").read_bytes()).hexdigest()

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(web), **kw)

            def log_message(self, *a):
                pass
        site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = site.server_address[1]
        (web / "latest.json").write_text(json.dumps({"tag_name": "v9.9.9", "name": "Test update", "assets": [
            {"name": "inZOI-Multiplayer.exe", "size": (web / "inZOI-Multiplayer.exe").stat().st_size,
             "digest": f"sha256:{digest}", "browser_download_url": f"http://127.0.0.1:{port}/inZOI-Multiplayer.exe"}]}))
        threading.Thread(target=site.serve_forever, daemon=True).start()

        env = {**os.environ, "APPDATA": str(data), "INZOI_MP_GAME_URL": "http://127.0.0.1:9/mcp",
               "INZOI_MP_DOCUMENTS": str(tmp / "Documents"),          # never the real game's mod folder
               "INZOI_MP_PANEL_PORT": str(PANEL), "INZOI_MP_UPDATE_API": f"http://127.0.0.1:{port}/latest.json",
               "INZOI_MP_GAME_BIN": str(game), "INZOI_MP_TEST": "1", "INZOI_MP_ASK_UPDATE": "1"}
        server = subprocess.Popen([sys.executable, str(ROOT / "server" / "inzoi_mp_server.py"), "--port", "24390",
                                   "--data", str(tmp / "server")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            subprocess.Popen([str(app / "inZOI Multiplayer.exe"), "--play", "--server", "127.0.0.1", "--port", "24390",
                              "--name", "Tester"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NEW_CONSOLE)
            first = wait(lambda: post("/status"), 30, "the program")
            print("running:", first["version"], "connected:", first["connected"])
            if os.environ.get("INZOI_MP_NO_UE4SS"):      # (built without UE4SS: the antivirus took dwmapi.dll)
                print("(built without UE4SS: the main menu's button isn't checked)")
            else:
                assert (game / "dwmapi.dll").exists(), "the main menu's button goes in by itself"
            offer = wait(lambda: (lambda s: s if s["update"]["state"] == "available" else None)(post("/status")), 90,
                         "the update to be found")
            print("update offered:", offer["update"]["box"]["text"][:70], "...")
            page = post("/servers/state")["update"]
            assert page["version"] == "9.9.9" and page["state"] == "available", page
            old_pids = procs_under(app)
            post("/update", {"key": "9.9.9", "action": "update"})
            wait(lambda: not set(old_pids) & set(procs_under(app)), 30, "the old program to exit")
            after = wait(lambda: post("/status"), 40, "the updated program to start")
            print("came back:", after["version"], "connected:", after["connected"])
            new_pids = procs_under(app)
            assert new_pids and not set(new_pids) & set(old_pids), "a new process should be running"
            assert (app / "inZOI Multiplayer.exe").exists()
            log = (data / "inzoi-mp" / "companion.log").read_text("utf-8")
            assert "restarting into inZOI Multiplayer 9.9.9" in log, log[-2000:]
            print("update test passed")
        finally:
            for pid in procs_under(app):
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            server.terminate()
            site.shutdown()
            time.sleep(1)


if __name__ == "__main__":
    main()
