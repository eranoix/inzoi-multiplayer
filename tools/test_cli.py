"""Test of the official-CLI layer in client/game.py against a stand-in for the game's MCP control server:
- the catalog (cli.list + tools/list) is read once and cached; cli() refuses commands the build doesn't list
- every migrated helper (possessed, clock, go_to, environment, time speed/skip, season, weather) uses the CLI when
  listed, and falls back to the legacy call when the command is missing, errors or answers oddly
- INZOI_MP_USE_CLI=0 forces the legacy paths (no catalog read at all)
- an unreachable game isn't retried the legacy way

    python tools/test_cli.py
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import game  # noqa: E402


class Fake:
    """What the fake game knows and what it was asked."""
    def __init__(self):
        self.listing = {"commands": [{"name": "entity.get_possessed"}, {"name": "environment.get_time"},
                                     {"name": "environment.get_simulation"}, {"name": "movement.teleport_to"},
                                     {"name": "environment.get_state"}, {"name": "environment.time_speed"},
                                     {"name": "environment.time_skip"}, {"name": "environment.change_season"},
                                     {"name": "environment.override_weather"}, {"name": "game.get_state"}]}
        self.tools = None           # tools/list names (None: method not supported)
        self.answers = {}           # tool -> result (dict) or Exception-ish "error"
        self.calls = []             # (method, tool, args)


FAKE = Fake()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method, params = req["method"], req.get("params") or {}
        name = params.get("name")
        FAKE.calls.append((method, name, params.get("arguments")))
        if method == "tools/list":
            if FAKE.tools is None:
                body = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no"}}
            else:
                body = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": t} for t in FAKE.tools]}}
        elif name == "cli_list":
            if FAKE.listing is None:
                body = {"jsonrpc": "2.0", "id": 1, "result": {"isError": True,
                                                              "content": [{"type": "text", "text": "unknown"}]}}
            else:
                body = {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": FAKE.listing}}
        else:
            a = FAKE.answers.get(name, {})
            if a == "error":
                body = {"jsonrpc": "2.0", "id": 1, "result": {"isError": True,
                                                              "content": [{"type": "text", "text": "boom"}]}}
            else:
                body = {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": a}}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def tools_called(name):
    return [args for m, n, args in FAKE.calls if m == "tools/call" and n == name]


def reset(**answers):
    FAKE.calls.clear()
    FAKE.answers = dict(answers)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/mcp"
    os.environ.pop("INZOI_MP_USE_CLI", None)

    # Catalog parsing copes with the shapes cli.list might take.
    assert game.cli_names({"commands": [{"name": "a.b"}, "c.d_e"]}) == {"a_b", "c_d_e"}
    assert game.cli_names({"_text": "entity.get_possessed - Returns...\nenvironment.get_time"}) == \
        {"entity_get_possessed", "environment_get_time"}
    assert game.cli_names({"categories": {"zoi": {"zoi.destroy": "Removes"}}}) == {"zoi_destroy"}

    # --- CLI path: catalog read once, commands go through it
    g = game.Game(url)
    reset(entity_get_possessed={"entity_id": 42},
          environment_get_time={"hour": 7, "minute": 30, "day": 3},
          environment_get_simulation={"time_scale": 3, "is_paused": True},
          environment_get_state={"season": "Fall", "weather": "Rain"})
    assert g.possessed() == 42
    assert g.clock() == (7, 30, 3, True)                    # speed/pause from get_simulation
    assert g.environment()["season"] == "Fall"
    assert len([c for c in FAKE.calls if c[1] == "cli_list"]) == 1      # cached
    assert g.has_cli("environment.time_speed") and not g.has_cli("appearance.export")
    try:
        g.cli("appearance.export")
        raise AssertionError("unlisted command ran")
    except game.CliUnavailable:
        pass
    g.set_time_speed(99)
    assert tools_called("environment_time_speed") == [{"speed": 30.0}]  # documented range 0-30
    g.time_skip(15)
    g.change_season("Winter")
    g.override_weather("Snow")
    g.go_to(7)
    assert tools_called("environment_time_skip") == [{"minutes": 15}]
    assert tools_called("environment_change_season") == [{"season": "Winter"}]
    assert tools_called("environment_override_weather") == [{"weather": "Snow", "step": 0}]
    assert tools_called("movement_teleport_to") == [{"entity_id": 7}]

    # get_time carrying speed/pause itself: no get_simulation needed
    reset(environment_get_time={"hour": 1, "minute": 2, "time_scale": 2, "paused": False})
    assert g.clock() == (1, 2, 2, False) and not tools_called("environment_get_simulation")

    # --- CLI errors / odd answers: legacy path
    reset(entity_get_possessed="error")
    try:
        g.possessed()
        raise AssertionError("expected GameError")
    except game.GameError:
        pass
    assert len(tools_called("entity_get_possessed")) == 2   # CLI try, then the legacy call
    reset(environment_get_time={"hour": "x"})
    try:
        g.clock()
        raise AssertionError("expected GameError")
    except game.GameError:
        pass

    # --- a build without entity.get_possessed: game.get_state, then legacy
    FAKE.listing["commands"] = [c for c in FAKE.listing["commands"] if c["name"] != "entity.get_possessed"]
    g = game.Game(url)
    reset(game_get_state={"possessed_entity_id": 9}, entity_get_possessed={"entity_id": 5})
    assert g.possessed() == 9 and not tools_called("entity_get_possessed")
    reset(game_get_state={"possessed_entity_id": -1}, entity_get_possessed={"entity_id": 5})
    assert g.possessed() == 5                                # nobody possessed per get_state: legacy decides

    # --- no catalog at all (old build): legacy everywhere, catalog retried only after CLI_RELIST
    FAKE.listing = None
    g = game.Game(url)
    reset(entity_get_possessed={"entity_id": 11}, environment_get_time={"hour": 5, "minute": 6, "time_scale": 1})
    assert g.possessed() == 11 and g.clock() == (5, 6, 1, False)
    assert g.cli_list() == set()
    assert len([c for c in FAKE.calls if c[1] == "cli_list"]) == 1
    # ...but tools/list alone is enough to know a command exists
    FAKE.tools = ["environment_time_speed"]
    assert g.cli_list(refresh=True) == {"environment_time_speed"}
    FAKE.tools = None

    # --- INZOI_MP_USE_CLI=0: legacy, never reads the catalog
    os.environ["INZOI_MP_USE_CLI"] = "0"
    FAKE.listing = {"commands": ["entity.get_possessed"]}
    g = game.Game(url)
    reset(entity_get_possessed={"entity_id": 3}, environment_get_state={"season": "Spring"})
    assert g.possessed() == 3 and g.environment() == {"season": "Spring"}
    g.set_time_speed(99)
    assert tools_called("environment_time_speed") == [{"speed": 99}]    # legacy: sent as given
    assert not [c for c in FAKE.calls if c[1] == "cli_list" or c[0] == "tools/list"]
    assert not g.has_cli("entity.get_possessed")
    os.environ.pop("INZOI_MP_USE_CLI")

    # --- unreachable game: one GameUnreachable, no legacy retry
    srv.shutdown()
    srv.server_close()
    g = game.Game(url)
    try:
        g.possessed()
        raise AssertionError("expected GameUnreachable")
    except game.GameUnreachable:
        pass
    print("CLI tests passed")


if __name__ == "__main__":
    main()
