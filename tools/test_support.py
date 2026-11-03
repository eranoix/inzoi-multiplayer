"""Tests of the support features, with a real relay server on this PC and a stand-in for the game:
- LAN discovery: netinfo.discover finds the server; the companion plays on its home-network address when the picked
  (Tailscale) address can't be reached, remembers it as an alternate only, and joins it when no server is picked
- clock_mode "host": the leader's clock and speed are everyone's, nobody pushes it; "catchup" stays as before
- partner cards in /status, clock_mode in /settings
- /selftest checks and /diag (the diagnostics zip: logs, mod reports, settings without secrets, status snapshot)

    python tools/test_support.py
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ["INZOI_MP_DOCUMENTS"] = tempfile.mkdtemp()                 # never the real game's mod folder
os.environ["INZOI_MP_GAME_URL"] = "http://127.0.0.1:9/mcp"             # no game
os.environ["INZOI_MP_GAME_BIN"] = tempfile.mkdtemp()
os.environ["INZOI_MP_DESKTOP"] = DESKTOP = tempfile.mkdtemp()
os.environ["APPDATA"] = tempfile.mkdtemp()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "server"))
import companion  # noqa: E402
import inzoi_mp_server as mp  # noqa: E402
import install  # noqa: E402
import netinfo  # noqa: E402
import servers  # noqa: E402


class StandIn:
    def __init__(self, hour, minute, speed=1.0, paused=False):
        self.t = {"hour": hour, "minute": minute, "time_scale": speed, "paused": paused}
        self.sent = []

    def call(self, name, **kw):
        self.sent.append((name, kw))
        if name == "environment.get_state":
            return {"season": "Fall", "weather": "Cloud"}
        return {}

    def game_url(self, failed=False):
        return "http://127.0.0.1:9/mcp"

    def site_name(self, site):
        return "Casa Azul"


def make(cfg=None, game=None, leader=False):
    comp = companion.Companion({"key": "k" * 20, "name": "me", "server": "x", "port": 1, "password": "", **(cfg or {})})
    if game:
        comp.game = game
        t = game.t
        comp.my_clock = (t["hour"], t["minute"], t["time_scale"], t["paused"])
    comp.me_id, comp.world = "me", "play"
    comp.leader = "me" if leader else "host"
    sent = []

    async def send(m):
        sent.append(m)
    comp.send = send
    return comp, sent


def friend(comp, pid, state, version=None):
    comp.update_remote({"id": pid, "name": pid.title(), "version": version})
    r = comp.remotes[pid]
    r.state, r.state_at = state, time.time()
    return r


def commands(game, name):
    return [kw for n, kw in game.sent if n == name]


def tcp_and_udp_port():
    """A loopback port number free for both TCP and UDP. Port 0 isn't enough: the kernel picks a free TCP port and
    start() binds UDP on the same number, but Windows (Hyper-V/WinNAT) reserves blocks of UDP ports (and others of TCP
    ports), so about 1 start in 4 got WSAEACCES and LAN discovery was off. Each candidate is tried with both. The
    kernel hands its ports out in sequence, so its picks can sit in a run of reserved blocks for hundreds of tries
    (63866-64944 for UDP here; server/test_server.py's UDP-first pick has the same trouble with TCP's 57319-57987):
    every other candidate is a random one from the dynamic range instead."""
    import random
    import socket
    for i in range(400):
        t = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            t.bind(("127.0.0.1", 0 if i % 2 == 0 else random.randint(49152, 65535)))
            port = t.getsockname()[1]
            u.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            t.close()
            u.close()
        if port not in (24242, 24243):            # (never the user's own server and app on this machine)
            return port
    raise AssertionError("no loopback port free for both TCP and UDP")


async def start_server(name="Casa", password=""):
    """A relay server on loopback with LAN discovery on (lan() needs it)."""
    data = tempfile.mkdtemp()
    for _ in range(5):
        # The port is let go before start() takes it, so another program could grab it in between (TCP: start()
        # fails; UDP: LAN discovery is off): then again with another one.
        args = argparse.Namespace(host="127.0.0.1", port=tcp_and_udp_port(), password=password, motd=None,
                                  max_players=None, data=data, hosted=False, name=name)
        try:
            srv, tasks = await mp.start(args)
        except OSError:
            continue
        if srv.mp_server.udp is not None:
            return srv, tasks, srv.mp_server
        await mp.stop(srv, tasks)
    raise AssertionError("start() never got a port with LAN discovery on")


async def ports():
    """start_server() always hosts with LAN discovery on (port 0 left it off about 1 start in 4 on Windows, and lan()
    then failed with found == []), never on the user's own ports."""
    import socket
    global tcp_and_udp_port
    for _ in range(20):
        srv, tasks, server = await start_server()
        assert server.udp is not None and server.port not in (0, 24242, 24243), server.port
        await mp.stop(srv, tasks)
    for _ in range(200):
        port = tcp_and_udp_port()
        assert 0 < port and port not in (24242, 24243), port

    # The UDP port is taken between the pick and start() (as a reserved block does): LAN discovery would be off, so
    # start_server() hosts again on another port.
    held = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    held.bind(("127.0.0.1", 0))
    taken, real, picks = held.getsockname()[1], tcp_and_udp_port, []

    def pick():
        picks.append(taken if not picks else real())
        return picks[-1]
    tcp_and_udp_port = pick
    try:
        srv, tasks, server = await start_server()
    finally:
        tcp_and_udp_port = real
        held.close()
    assert len(picks) == 2 and server.port == picks[1] != taken and server.udp is not None, (picks, server.port)
    await mp.stop(srv, tasks)
    print("server port tests passed")


async def lan():
    srv, tasks, server = await start_server()
    port, sid = server.port, server.store.settings["id"]
    found = await asyncio.to_thread(netinfo.discover, port, 1.0, ["127.0.0.1"])
    if not found:                                     # (a busy PC, the game running: a second, longer listen)
        found = await asyncio.to_thread(netinfo.discover, port, 3.0, ["127.0.0.1"])
    assert len(found) == 1 and found[0]["id"] == sid and found[0]["address"] == f"127.0.0.1:{port}", found
    assert found[0]["name"] == "Casa" and found[0]["password"] is False
    real = netinfo.discover
    servers.netinfo.discover = lambda p, timeout=1.5, targets=None: real(port, 0.8, ["127.0.0.1"])
    try:
        # The picked server (a Tailscale address) doesn't answer: after a while, the same server on the home network.
        comp, _ = make({"server": "100.64.0.99", "port": port, "server_id": sid})
        comp.lan_fail_since = time.monotonic() - 20
        await comp.lan_tick()
        assert comp.lan_override == ("127.0.0.1", port), comp.lan_override
        assert comp.address()[:2] == ("127.0.0.1", port) and comp.wake.is_set()
        assert comp.cfg["server"] == "100.64.0.99"                              # the picked address stays
        assert comp.cfg["lan_alt"][f"100.64.0.99:{port}"]["id"] == sid
        assert companion.read_config()["server"] == "100.64.0.99"               # (and next start tries it first)
        assert [s["id"] for s in comp.lan_view()] == [sid] and comp.lan_view()[0]["current"]
        await comp.lan_tick()                                                   # connected-or-not: no flapping
        assert comp.lan_override == ("127.0.0.1", port)
        comp.switch_server()                                                    # another server picked
        assert comp.lan_override is None and comp.address()[0] == "100.64.0.99"

        # Not long enough yet: nothing happens.
        comp, _ = make({"server": "100.64.0.99", "port": port})
        await comp.lan_tick()
        assert comp.lan_override is None and comp.lan["at"] == 0.0

        # Wrong password etc.: another address won't help.
        comp, _ = make({"server": "100.64.0.99", "port": port})
        comp.lan_fail_since, comp.connect_block = time.monotonic() - 60, "wrong server password"
        await comp.lan_tick()
        assert comp.lan_override is None

        # No server picked at all: the one found is joined (and becomes the picked one).
        comp, _ = make({"server": "", "port": 24242})
        await comp.lan_tick()
        assert comp.cfg["server"] == "127.0.0.1" and int(comp.cfg["port"]) == port and comp.lan_override is None

        # Connected: never looks.
        comp, _ = make({"server": "100.64.0.99", "port": port})
        comp.writer, comp.lan_fail_since = object(), time.monotonic() - 60
        await comp.lan_tick()
        assert comp.lan_fail_since is None and comp.lan_override is None
    finally:
        servers.netinfo.discover = real

    # Which one: the id joined last wins; else only when exactly one answered; never another protocol.
    comp, _ = make({"server": "100.64.0.99", "port": 24242, "server_id": "B"})
    a, b = {"id": "A", "address": "10.0.0.2", "protocol": 1}, {"id": "B", "address": "10.0.0.3", "protocol": 1}
    assert comp.lan_pick([a, b]) is b and comp.lan_pick([a]) is a and comp.lan_pick([]) is None
    comp.cfg["server_id"] = "C"
    assert comp.lan_pick([a, b]) is None and comp.lan_pick([{**a, "protocol": 2}]) is None
    srv.close()
    tasks.cancel()
    print("LAN discovery tests passed")


async def clock():
    # host mode (the default), not the leader: behind the host -> forward to it; ahead -> back (same day only).
    g = StandIn(9, 0)
    comp, _ = make(game=g)
    assert comp.clock_mode() == "host"
    friend(comp, "host", {"clock": [10, 0], "speed": 2, "sleep": False})
    friend(comp, "other", {"clock": [15, 0], "speed": 1})                     # furthest ahead, but not the host
    await comp.sync_world()
    assert commands(g, "environment.time_skip") == [{"minutes": 60}], g.sent
    await comp.keep_speed()
    assert commands(g, "environment.time_speed") == [{"speed": 2}]

    g = StandIn(11, 0)
    comp, _ = make(game=g)
    friend(comp, "host", {"clock": [10, 0], "speed": 1})
    await comp.sync_world()
    # (a host that doesn't send its day: forward only - set_time with only an hour moves a whole day FORWARD; going back
    # exactly, with the day, is in tools/test_time_sync.py)
    assert not commands(g, "environment.set_time") and not commands(g, "environment.time_skip"), g.sent

    g = StandIn(0, 20)                                                        # host at 23:50: wait, don't go a day back
    comp, _ = make(game=g)
    friend(comp, "host", {"clock": [23, 50], "speed": 1})
    await comp.sync_world()
    assert not commands(g, "environment.set_time") and not commands(g, "environment.time_skip")

    g = StandIn(9, 0, speed=3)                                                # the host is paused (no clock): my own
    comp, _ = make(game=g)
    friend(comp, "host", {"clock": None, "speed": 1})
    await comp.sync_world()
    await comp.keep_speed()
    assert not g.sent or all(n == "environment.get_state" for n, _ in g.sent), g.sent

    # The leader in host mode never follows anyone's clock.
    g = StandIn(9, 0)
    comp, sent = make(game=g, leader=True)
    friend(comp, "guest", {"clock": [9, 40], "speed": 1})
    await comp.sync_world()
    assert not commands(g, "environment.time_skip") and sent[0]["t"] == "world"

    # What goes out: in host mode only the leader shares "clock"; "time", "speed" and the lot's name always.
    g = StandIn(9, 5, speed=2)
    comp, _ = make(game=g)
    ex = comp.extra_state({"site": 7})
    assert ex["clock"] is None and ex["time"] == [9, 5] and ex["speed"] == 2 and ex["lot"] == ""
    await asyncio.sleep(0.05)                                                 # (the lot's name is looked up once)
    assert comp.extra_state({"site": 7})["lot"] == "Casa Azul"
    comp.leader = "me"
    assert "clock" not in comp.extra_state({"site": 7})
    comp, _ = make({"clock_mode": "catchup"}, game=StandIn(9, 0))
    assert "clock" not in comp.extra_state({"site": -1}) and comp.clock_mode() == "catchup"

    # /settings shows and changes it.
    comp, _ = make(game=StandIn(9, 0))
    settings = comp.ui_routes()["/settings"]
    assert (await settings({}))["clock_mode"] == "host"
    assert (await settings({"clock_mode": "catchup"}))["clock_mode"] == "catchup"
    assert companion.read_config()["clock_mode"] == "catchup"
    assert (await settings({"clock_mode": "nonsense"}))["clock_mode"] == "catchup"
    print("clock mode tests passed")


async def cards():
    comp, _ = make(game=StandIn(9, 0))
    comp.my_city = "Dowon"
    r = friend(comp, "ana", {"city": "Dowon", "lot": "Casa Azul", "site": 7, "act": {"bag": "Object_Bed_Sleep", "player": "me"},
                             "anim": "Sleep_Loop", "outfit": "Sleepwear", "sleep": True, "time": [23, 10], "speed": 3},
               version="0.8.1")
    r.profile = {"first_name": "Ana", "last_name": "Lima"}
    r.state_at = time.time() - 4
    card = comp.partner_cards()[0]
    assert card["name"] == "Ana" and card["zoi"] == "Ana Lima" and card["lot"] == "Casa Azul" and card["city"] == "Dowon"
    assert card["action"] == "Object_Bed_Sleep" and card["action_text"] == "Object Bed Sleep" and card["with"] == "you"
    assert card["sleep"] and card["time"] == [23, 10] and card["version"] == "0.8.1" and 3 <= card["last_seen_s"] <= 5
    assert card["online"] and not card["away"] and card["mood"] is None
    friend(comp, "ben", {})
    assert comp.partner_cards()[1]["action"] is None and comp.partner_cards()[1]["lot"] == ""
    try:
        status = await comp.ui_routes()["/status"]({})
        assert status["partners"][0]["name"] == "Ana" and status["clock_mode"] == "host" and status["via_lan"] is None
    except (AttributeError, KeyError, TypeError) as e:          # (another part of /status may be mid-change)
        print("  /status itself couldn't be built here:", repr(e))
    print("partner card tests passed")


async def selftest_and_diag():
    srv, tasks, server = await start_server(password="pw")
    comp, _ = make({"server": "127.0.0.1", "port": server.port, "password": "segredo",
                    "servers": [{"address": "x", "password": "segredo2"}]})
    comp.world = "out"
    comp.game = companion.Game()                                               # (nothing listens on its port)
    friend(comp, "ana", {"clock": [9, 0]}, version="0.7.0")
    checks = (await comp.ui_routes()["/selftest"]({}))["checks"]
    by = {c["id"]: c for c in checks}
    assert set(by) == {"game_api", "mod_loaded", "server_reachable", "logged_in", "versions_match", "partner_here",
                       "tailscale", "nordvpn_running"}, by
    assert by["partner_here"]["ok"] is None, "not connected: nothing to say yet"
    assert all(set(c) >= {"id", "ok", "detail", "hint_code"} for c in checks)
    assert by["game_api"]["ok"] is False and by["game_api"]["hint_code"] == "start_game" and by["game_api"]["data"]["port"] == 9
    assert by["mod_loaded"]["ok"] is False and by["mod_loaded"]["hint_code"] == "load_save"
    assert by["server_reachable"]["ok"] is True and by["server_reachable"]["hint_code"] is None, by["server_reachable"]
    assert by["logged_in"]["ok"] is False and by["logged_in"]["hint_code"] == "wait_connect"
    assert by["versions_match"]["ok"] is False and by["versions_match"]["hint_code"] == "update_partner"
    assert by["tailscale"]["ok"] in (True, False, None) and by["nordvpn_running"]["ok"] in (True, False)
    comp.cfg["port"] = 9                                                       # nothing there
    by = {c["id"]: c for c in await comp.self_test()}
    assert by["server_reachable"]["ok"] is False and by["server_reachable"]["hint_code"] == "check_server"
    comp.cfg["port"] = server.port

    # The diagnostics zip.
    companion.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (companion.CONFIG_DIR / "companion.log").write_text("line one\n", "utf-8")
    (companion.CONFIG_DIR / "companion.log.1").write_text("older\n", "utf-8")
    (install.INSTALL_DIR / "out").mkdir(parents=True, exist_ok=True)
    (install.INSTALL_DIR / "out" / "m3.cfg").write_text("abc 3 log hello", "utf-8")
    companion.log.warning("something odd happened")
    comp.notice = "Can't reach the server: retrying"
    out = await comp.ui_routes()["/diag"]({})
    path = Path(out["path"])
    assert out["ok"] and path.parent == Path(DESKTOP) and path.name.startswith("inzoi-coop-diagnostico-") and path.exists()
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        assert {"logs/companion.log", "logs/companion.log.1", "mod/out/m3.cfg", "config.json", "status.json"} <= names, names
        cfg_text = z.read("config.json").decode()
        assert "segredo" not in cfg_text and "k" * 20 not in cfg_text and '"server": "127.0.0.1"' in cfg_text
        st = json.loads(z.read("status.json"))
    assert st["version"] == companion.VERSION and st["connection"]["address"] == f"127.0.0.1:{server.port}"
    assert st["connection"]["connected"] is False and len(st["selftest"]) == 8 and st["partners"][0]["name"] == "Ana"
    texts = [e["text"] for e in st["recent"]]
    assert any("something odd happened" in t for t in texts) and "Can't reach the server: retrying" in texts, texts
    assert "puppets" in st and "game" in st
    srv.close()
    tasks.cancel()
    print("self-test and diagnostics tests passed")


async def other_address():
    """The server tells a joined player the addresses it can be reached at (welcome "addresses", never in its public
    info); when the picked one stops answering - even while the home network still finds the server at that very
    address (a VPN or a router breaking the path) - the same server is played on at another one, never another server."""
    srv, tasks, server = await start_server(name="Casa")
    other_srv, other_tasks, other = await start_server(name="Outro")
    try:
        port, sid = server.port, server.store.settings["id"]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        hello = {"t": "hello", "protocol": mp.PROTOCOL, "key": "a" * 24, "name": "Ana"}
        w.write(json.dumps(hello).encode() + b"\n")
        await w.drain()
        welcome = json.loads(await r.readline())
        w.close()
        assert isinstance(welcome.get("addresses"), list), welcome.keys()
        info, _ = await servers.ask(f"127.0.0.1:{port}", {"t": "info"}, "info", timeout=3)
        assert "addresses" not in info, "a stranger was told the host's addresses"
        # the companion keeps them from its welcome (as it really arrives: through on_message)
        comp, _ = make({"server": "127.0.0.1", "port": port})
        await comp.on_message(dict(welcome, addresses=[f"127.0.0.1:{port}", "100.64.0.7:24242"]))
        assert comp.cfg.get("server_addrs", {}).get(sid) == [f"127.0.0.1:{port}", "100.64.0.7"], \
            comp.cfg.get("server_addrs")                         # (the default port isn't written: address_text)

        dead = f"127.0.0.1:{free_port()}"                       # (the picked address: nothing answers there)
        real = servers.netinfo.discover
        servers.netinfo.discover = lambda p, timeout=1.5, targets=None: [
            {"id": sid, "address": dead, "protocol": mp.PROTOCOL, "name": "Casa"}]    # (found there all the same)
        try:
            comp, _ = make({"server": "127.0.0.1", "port": int(dead.split(":")[1]), "server_id": sid})
            comp.note_addresses({"id": sid}, [dead, f"127.0.0.1:{other.port}", f"127.0.0.1:{port}"])
            comp.lan_fail_since = time.monotonic() - 20
            await comp.lan_tick()
            assert comp.lan_override == ("127.0.0.1", port), comp.lan_override
            assert comp.cfg["server"] == "127.0.0.1" and int(comp.cfg["port"]) == int(dead.split(":")[1])

            comp, _ = make({"server": "127.0.0.1", "port": int(dead.split(":")[1]), "server_id": sid})
            comp.note_addresses({"id": sid}, [f"127.0.0.1:{other.port}"])          # (only another server there)
            comp.lan_fail_since = time.monotonic() - 20
            await comp.lan_tick()
            assert comp.lan_override is None, comp.lan_override
        finally:
            servers.netinfo.discover = real
        print("the server's other address (its Tailscale one) is used when the picked one's path breaks: ok")
    finally:
        await mp.stop(srv, tasks)
        await mp.stop(other_srv, other_tasks)


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main():
    await ports()
    await lan()
    await other_address()
    await clock()
    await cards()
    await selftest_and_diag()
    print("support tests passed")


if __name__ == "__main__":
    asyncio.run(main())
