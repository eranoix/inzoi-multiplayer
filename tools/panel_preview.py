"""Screenshot the in-game panel outside the game: headless Edge, talking to the companion running on this PC.

    python tools/panel_preview.py [--tab people|houses|mods|settings|help] [--lang pt-BR|en] [--out shot.png]
        [--width 520] [--height 960] [--mock status.json]

--mock: answer the panel from that file (its /status) instead of the companion, to see states that are hard to
cause in the game (a request coming in, a question on screen, disconnected...).

The game's own scripts (cohtml.js, inzoi-sdk.js) and fonts aren't there: small stand-ins replace them, and a
sans-serif font stands in for the game's. Good for layout, wording and states; the game itself has the last say.
"""
import argparse
import http.server
import json
import re
import threading
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
    ap.add_argument("--mock", help="a /status JSON file to answer with (no companion needed)")
    a = ap.parse_args()
    stubs = STUBS.replace("%LANG%", a.lang)
    if a.mock:
        status = Path(a.mock).read_bytes()

        class Mock(http.server.BaseHTTPRequestHandler):
            def answer(self):
                body = status if self.path.startswith("/status") else b"{}"
                self.send_response(200)
                for k, v in (("Content-Type", "application/json"), ("Access-Control-Allow-Origin", "*"),
                             ("Access-Control-Allow-Headers", "Content-Type")):
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
            do_GET = do_POST = do_OPTIONS = answer

            def log_message(self, *args):
                pass
        mock = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=mock.serve_forever, daemon=True).start()
        port = mock.server_address[1]
        stubs += ("<script>(function(){var o=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open="
                  "function(m,u){u=String(u).replace('127.0.0.1:24243','127.0.0.1:%d');"
                  "return o.apply(this,[m,u].concat([].slice.call(arguments,2)));};})();</script>" % port)
    html = (UI / "index.html").read_text("utf-8")
    html = re.sub(r'<link rel="stylesheet" href="coui://[^"]+">', "", html)
    html = re.sub(r'<script src="coui://[^"]+"></script>', "", html)
    html = html.replace("<head>", "<head>" + stubs, 1)
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
