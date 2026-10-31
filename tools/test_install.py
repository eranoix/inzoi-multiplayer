"""Installing the mod (every update does) keeps the panel's own state: where it was left and its size
(ui/mp/saved/window.cfg); files a newer version dropped still go.

    python tools/test_install.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["INZOI_MP_DOCUMENTS"] = str(Path(tmp) / "Documents")
        import install
        install.INSTALL_DIR = Path(tmp) / "Mods" / "InZOIMultiplayer"
        install.MAILBOX_DIR = install.INSTALL_DIR / "lua" / "B2"
        assert install.install() is True
        saved = install.INSTALL_DIR / "ui" / "mp" / "saved" / "window.cfg"
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text('{"x":1734,"y":15,"minimized":false}', "utf-8")
        old = install.INSTALL_DIR / "ui" / "mp" / "old_panel.js"
        old.write_text("x", "utf-8")
        install.install()
        assert saved.exists() and "1734" in saved.read_text("utf-8"), "the panel's place is kept"
        assert not old.exists(), "a dropped file still goes"
    print("install tests passed")


if __name__ == "__main__":
    main()
