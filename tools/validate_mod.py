"""Check the mod's Lua with the ModKit's own analyzer (the same one the game uses), then optionally install it.

Usage:  python tools/validate_mod.py [--install]

Needs the ModKit editor open (Lua) and Node.js (panel scripts). Each script is copied into a scratch ModKit project's Lua workspace and validated there.
--install then copies the mod into the game's Mods folder and remounts it (client/install.py).
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "client"))
from gamecli import MODKIT_MCP, call  # noqa: E402

SCRATCH = "MPGateTest_5F7FDFC6"
LUA = ROOT / "mod" / "InZOIMultiplayer" / "lua"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true")
    a = ap.parse_args()
    bad = False
    for f in sorted(LUA.rglob("*.lua")):
        if f.name in ("MPCmd.lua", "MPBulk.lua", "MPSeq.lua"):
            continue
        slot = f.relative_to(LUA).as_posix()
        w = call("modkit_write_lua_script", _url=MODKIT_MCP, plugin=SCRATCH, path=slot, content=f.read_text("utf-8"))
        if not w.get("success"):
            sys.exit(f"{slot}: write failed: {w}")
        v = call("modkit_validate_lua_script", _url=MODKIT_MCP, plugin=SCRATCH, path=slot)
        diags = v.get("diagnostics", [])
        errors = [d for d in diags if d.get("severity") == "error"]
        print(f"{slot}: {len(errors)} error(s), {len(diags) - len(errors)} other")
        for d in diags:
            print(f"  [{d['severity']}] line {d.get('line')}: {d.get('message')}")
        bad = bad or bool(errors)
    for f in sorted((LUA.parent / "ui").rglob("*.js")):         # the panel: a syntax error leaves it blank in game
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        print(f"ui/{f.relative_to(LUA.parent / 'ui').as_posix()}: {'ok' if r.returncode == 0 else 'ERROR'}")
        if r.returncode:
            for line in r.stderr.strip().splitlines()[:5]:
                print("  " + line)
            bad = True
    if bad:
        sys.exit("validation errors")
    if a.install:
        from game import Game
        from install import INSTALL_DIR, install, mount, reload
        changed = install()
        print("installed" if changed else "unchanged", "->", INSTALL_DIR)
        print(mount(Game().call))
        if changed:
            print(reload(Game().call))


if __name__ == "__main__":
    main()
