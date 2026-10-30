"""Rolling back a broken update (client/updater.py startup_check / mark_healthy), with a pretend packaged exe.

    python tools/test_rollback.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import updater  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        app = tmp / "app" / "inZOI Multiplayer.exe"
        app.parent.mkdir()
        updater.DIR = tmp / "update"
        updater.HEALTH = updater.DIR / "health.json"
        updater.frozen = lambda: True
        updater.signed_by_us = lambda p: True
        started = []
        updater.subprocess.Popen = lambda cmd, **kw: started.append(cmd)
        sys.executable = str(app)
        prev = app.with_name(app.stem + ".prev.exe")

        app.write_text("v0.8.2")
        prev.write_text("v0.8.1")
        assert not updater.startup_check("0.8.2", [])                     # 1st start of the new version
        assert not updater.startup_check("0.8.2", [])                     # 2nd: still never healthy
        assert updater.startup_check("0.8.2", ["--play"])                 # 3rd: roll back
        assert app.read_text() == "v0.8.1" and not prev.exists(), "the previous exe is back in place"
        assert app.with_name(app.stem + ".bad.exe").read_text() == "v0.8.2"
        assert started and started[-1][0] == str(app) and "--play" in started[-1] and "--restarted" in started[-1]
        assert updater.blocked() == ["0.8.2"]
        print("  ok: two failed starts -> the third goes back to the previous version, which is started")

        assert not updater.startup_check("0.8.1", [])
        updater.mark_healthy()
        for _ in range(5):
            assert not updater.startup_check("0.8.1", []), "no .prev left: nothing to roll back to"
        print("  ok: no previous exe -> keeps going")

        updater.DIR.mkdir(exist_ok=True)
        (updater.DIR / "inZOI-Multiplayer-0.8.2.exe").write_text("x")
        assert updater.pending("0.8.1") is None, "a rolled-back version isn't applied again"
        (updater.DIR / "inZOI-Multiplayer-0.8.3.exe").write_text("x")
        assert updater.pending("0.8.1").name == "inZOI-Multiplayer-0.8.3.exe"
        print("  ok: the blocked version is skipped, a newer one is taken")

        app.write_text("v0.8.3")
        prev.write_text("v0.8.1")
        updater.HEALTH.unlink()
        assert not updater.startup_check("0.8.3", [])
        updater.mark_healthy()                                           # ran fine once
        assert not updater.startup_check("0.8.3", [])
        assert not updater.startup_check("0.8.3", [])
        assert app.read_text() == "v0.8.3", "a healthy run resets the count"
        print("  ok: a healthy run resets the count")
    print("rollback tests passed")


if __name__ == "__main__":
    main()
