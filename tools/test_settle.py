"""The program puts itself on the Desktop as "inZOI Co-op.exe" and deletes the other copies (a stand-in exe, Desktop,
Downloads and home in a temporary folder; nothing is started):
- started from Downloads: copied to the Desktop, that one started, this one closes
- started from the Desktop: the other copies and shortcuts go, other files stay
- installed in %LOCALAPPDATA%\\Programs (with a shortcut): left as it is

    python tools/test_settle.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        desk, down = home / "Desktop", home / "Downloads"
        for d in (desk, down, down / "Taildrop", home / "AppData" / "Local" / "Programs" / "inZOI Co-op"):
            d.mkdir(parents=True)
        os.environ.update(USERPROFILE=str(home), HOME=str(home), INZOI_MP_DESKTOP=str(desk),
                          LOCALAPPDATA=str(home / "AppData" / "Local"), APPDATA=str(home / "AppData" / "Roaming"))
        import launcher
        started = []
        launcher.subprocess.Popen = lambda cmd, **kw: started.append(cmd)
        launcher.updater.frozen = lambda: True

        sent = down / "inZOI-Coop-0.8.8.exe"
        sent.write_bytes(b"exe 0.9.3")
        for f in (down / "inZOI-Coop (1).exe", down / "Taildrop" / "inZOI-Multiplayer.exe",
                  down / "inZOI-Multiplayer-0.9.1.zip", desk / "inZOI Co-op.lnk"):
            f.write_bytes(b"old")
        keep = [down / "outro-programa.exe", desk / "foto.png"]
        for f in keep:
            f.write_bytes(b"keep")
        old = home / "inZOI-Coop"
        (old / "client").mkdir(parents=True)
        (old / "entrar.bat").write_text("x")
        (old / "client" / "companion.py").write_text("x")

        # Started from Downloads: moves to the Desktop.
        launcher.sys.executable = str(sent)
        assert launcher.settle_on_desktop(["--play"]) is True
        exe = desk / "inZOI Co-op.exe"
        assert exe.read_bytes() == b"exe 0.9.3" and started[-1] == [str(exe), "--play", "--restarted"], started
        print("  ok: from Downloads it copies itself to the Desktop and starts from there")

        # The Desktop one cleans up.
        launcher.sys.executable = str(exe)
        assert launcher.settle_on_desktop([]) is False
        left = sorted(str(p.relative_to(home)) for p in home.rglob("*") if p.is_file())
        assert left == sorted(str(p.relative_to(home)) for p in [exe, *keep]), left
        assert not old.exists() and len(started) == 1
        print("  ok: on the Desktop, the other copies, shortcuts and the old install are deleted; other files stay")

        # Installed with a shortcut (Programs): left alone.
        inst = home / "AppData" / "Local" / "Programs" / "inZOI Co-op" / "inZOI-Multiplayer.exe"
        inst.write_bytes(b"x")
        (down / "inZOI-Coop.exe").write_bytes(b"x")
        launcher.sys.executable = str(inst)
        assert launcher.settle_on_desktop([]) is False and (down / "inZOI-Coop.exe").exists() and len(started) == 1
        print("  ok: an install in Programs (the host's PC) is left as it is")
    print("settle tests passed")


if __name__ == "__main__":
    main()
