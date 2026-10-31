"""Screenshot the in-game panel outside the game: headless Edge, talking to the companion running on this PC.

    python tools/panel_preview.py [--tab people|houses|mods|settings|help] [--lang pt-BR|en] [--out shot.png]
        [--width 520] [--height 960] [--api http://127.0.0.1:24243]

The game's own scripts (cohtml.js, inzoi-sdk.js) and fonts aren't there: small stand-ins replace them, and a
sans-serif font stands in for the game's. Good for layout, wording and states; the game itself has the last say.
"""
import argparse
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "mod" / "InZOIMultiplayer" / "ui" / "mp"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

STUBS = """<style>:root { --inzoi-font-ui: 'Segoe UI', Arial, sans-serif; --inzoi-font-mono: Consolas, monospace; }
html, body { background: #3b4a3a !important; }</style>
<script>
window.engine = { on: function () {}, off: function () {}, trigger: function () {}, call: function () {
    return { then: function () {} }; } };
window.inzoi = { view: { hover: function () {}, setHover: function () {} }, cli: { execute: function () {} },
    saveState: function () {}, loadState: function (k, cb) { if (cb) { cb(null); } return null; } };
try { localStorage.setItem('mp.lang', '%LANG%'); } catch (e) {}
</script>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tab", default="people")
    ap.add_argument("--lang", default="pt-BR")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "panel.png"))
    ap.add_argument("--width", type=int, default=520)
    ap.add_argument("--height", type=int, default=960)
    a = ap.parse_args()
    html = (UI / "index.html").read_text("utf-8")
    html = re.sub(r'<link rel="stylesheet" href="coui://[^"]+">', "", html)
    html = re.sub(r'<script src="coui://[^"]+"></script>', "", html)
    html = html.replace("<head>", "<head>" + STUBS.replace("%LANG%", a.lang), 1)
    click = ("<script>setTimeout(function(){var b=document.querySelector('[data-tab=\"%s\"]');"
             "if(b){b.click();}var r=document.getElementById('app-root');if(r){r.style.display='flex';}"
             "document.body.style.visibility='visible';},400);</script>" % a.tab)
    html = html.replace("</body>", click + "</body>", 1)
    page = UI / "_preview.html"
    page.write_text(html, "utf-8")
    try:
        out = Path(a.out).resolve()
        with tempfile.TemporaryDirectory() as prof:
            subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", f"--user-data-dir={prof}",
                            "--hide-scrollbars", f"--window-size={a.width},{a.height}",
                            "--virtual-time-budget=6000", "--allow-file-access-from-files",
                            "--disable-web-security", f"--screenshot={out}", page.as_uri()],
                           capture_output=True, timeout=90)
        time.sleep(0.5)
        print(out if out.exists() else "no screenshot", flush=True)
    finally:
        page.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
