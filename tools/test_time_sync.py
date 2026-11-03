"""Test of the time rules with a stand-in for the game (records the commands the companion sends):
- whoever is behind catches up with whoever is furthest ahead; nobody is ever pushed back
- pausing is your own: a paused player isn't caught up and doesn't hold anyone back
- speed is normal while anyone's awake, fast while everyone's asleep; alone on the server, speed is yours
- season and weather follow the leader; the leader's clock catches up like everyone's

    python tools/test_time_sync.py
"""
import asyncio
import sys
import time
from pathlib import Path

import os  # noqa: E402
import tempfile  # noqa: E402
os.environ["INZOI_MP_DOCUMENTS"] = tempfile.mkdtemp()                 # never the real game's mod folder
os.environ["INZOI_MP_GAME_URL"] = "http://127.0.0.1:9/mcp"
os.environ["APPDATA"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import companion  # noqa: E402


class StandIn:
    def __init__(self, hour, minute, speed=1.0, paused=False, season="Fall", weather="Cloud"):
        self.t = {"hour": hour, "minute": minute, "day": 10, "time_scale": speed, "paused": paused}
        self.env = {"season": season, "weather": weather}
        self.sent = []

    def call(self, name, **kw):
        self.sent.append((name, kw))
        if name == "environment.get_time":
            return dict(self.t)
        if name == "environment.get_state":
            return dict(self.env)
        return {}


def friend(comp, pid, clock, sleep=False):
    r = companion.Remote(pid, pid)
    r.state, r.state_at = {"clock": clock, "sleep": sleep}, time.time()
    comp.remotes[pid] = r


def make(game, leader=False):
    comp = companion.Companion({"key": "k" * 20, "name": "me", "server": "x", "port": 1, "password": "",
                                "clock_mode": "catchup"})       # (tools/test_support.py: "host")
    comp.game, comp.me_id, comp.world = game, "me", "play"
    comp.leader = "me" if leader else "host"
    t = game.t
    comp.my_clock = (t["hour"], t["minute"], t["time_scale"], t["paused"])
    sent = []

    async def send(m):
        sent.append(m)
    comp.send = send
    return comp, sent


def commands(game, name):
    return [kw for n, kw in game.sent if n == name]


async def main():
    # Behind a friend by 30 minutes: catch up. Ahead of another: ignored.
    g = StandIn(9, 0)
    comp, _ = make(g)
    friend(comp, "a", [9, 30])
    friend(comp, "b", [8, 0])
    await comp.sync_world()
    assert commands(g, "environment.time_skip") == [{"minutes": 30}], g.sent

    # Ahead of everyone: never pushed back.
    g = StandIn(12, 0)
    comp, _ = make(g)
    friend(comp, "a", [9, 30])
    await comp.sync_world()
    assert not commands(g, "environment.time_skip") and not commands(g, "environment.set_time")

    # Across midnight: 23:50 vs 00:20 is 30 minutes behind, not 23 hours ahead.
    g = StandIn(23, 50)
    comp, _ = make(g)
    friend(comp, "a", [0, 20])
    await comp.sync_world()
    assert commands(g, "environment.time_skip") == [{"minutes": 30}]

    # Paused (time controls) or in build mode: left alone.
    for world in ("stopped", "paused"):
        g = StandIn(9, 0)
        comp, _ = make(g)
        comp.world = world
        friend(comp, "a", [10, 0])
        await comp.sync_world()
        assert not commands(g, "environment.time_skip")

    # Season and weather follow the leader.
    g = StandIn(9, 0, season="Fall", weather="Cloud")
    comp, _ = make(g)
    comp.leader_world = {"season": "Winter", "weather": "Snow"}
    await comp.sync_world()
    assert commands(g, "environment.change_season") == [{"season": "Winter"}]
    assert commands(g, "environment.override_weather") == [{"weather": "Snow", "step": 0}]

    # The leader shares its season and weather.
    g = StandIn(9, 0, season="Summer", weather="Sunny")
    comp, sent = make(g, leader=True)
    await comp.sync_world()
    assert sent == [{"t": "world", "world": {"season": "Summer", "weather": "Sunny"}}]

    # The leader (host) catches up too, when a friend is ahead (it paused); its season/weather still go out.
    g = StandIn(9, 0, season="Summer", weather="Sunny")
    comp, sent = make(g, leader=True)
    friend(comp, "a", [9, 40])
    comp.leader_world = {"season": "Winter", "weather": "Snow"}     # (never followed by the leader itself)
    await comp.sync_world()
    assert commands(g, "environment.time_skip") == [{"minutes": 40}], g.sent
    assert not commands(g, "environment.change_season") and sent[0]["t"] == "world"

    # Speed: a friend awake -> back to normal; everyone asleep -> fast; alone or paused -> left alone.
    g = StandIn(9, 0, speed=3)
    comp, _ = make(g)
    friend(comp, "a", [9, 0], sleep=False)
    comp.sleeping = True
    await comp.keep_speed()
    assert commands(g, "environment.time_speed") == [{"speed": 1}]

    g = StandIn(23, 0, speed=1)
    comp, _ = make(g)
    friend(comp, "a", [23, 0], sleep=True)
    comp.sleeping = True
    await comp.keep_speed()
    assert commands(g, "environment.time_speed") == [{"speed": companion.SLEEP_SPEED}]

    g = StandIn(9, 0, speed=3)
    comp, _ = make(g)
    await comp.keep_speed()                                   # alone
    assert not commands(g, "environment.time_speed")

    g = StandIn(9, 0, speed=3, paused=True)
    comp, _ = make(g)
    friend(comp, "a", [9, 0])
    await comp.keep_speed()                                   # paused with the partner on: time runs again
    assert commands(g, "environment.time_speed") == [{"speed": 1}]
    g = StandIn(9, 0, speed=3, paused=True)
    comp, _ = make(g)
    friend(comp, "a", [9, 0])
    comp.in_build_mode = True
    await comp.keep_speed()                                   # build mode pauses by itself: left alone
    assert not commands(g, "environment.time_speed")
    g = StandIn(9, 0, speed=3, paused=True)
    comp, _ = make(g)
    friend(comp, "a", [9, 0])
    comp.cfg["never_pause"] = False
    await comp.keep_speed()                                   # (switched off: pausing is each one's own)
    assert not commands(g, "environment.time_speed")

    # Following the host's clock ("host" mode): a game that loaded its save paused runs again while the host's runs
    # (it stayed paused for good, the mod with it: seen in the real game), also out of the game's own pause; a host
    # whose time stopped (build mode, paused: no clock in its state) stops mine too - left alone.
    g = StandIn(9, 0, speed=0, paused=True)
    g.call = (lambda orig: lambda name, **kw: {"is_paused": True} if name == "environment.get_simulation"
              else orig(name, **kw))(g.call)
    comp, _ = make(g)
    comp.cfg["clock_mode"] = "host"
    friend(comp, "host", [9, 0])
    comp.remotes["host"].state["speed"] = 1.0
    await comp.keep_speed()
    assert commands(g, "environment.time_speed") == [{"speed": 1}] and commands(g, "environment.resume"), g.sent
    g = StandIn(9, 0, speed=0, paused=True)
    comp, _ = make(g)
    comp.cfg["clock_mode"] = "host"
    friend(comp, "host", None)                                # (the host's own time is stopped)
    await comp.keep_speed()
    assert not commands(g, "environment.time_speed") and not commands(g, "environment.resume"), g.sent

    # Following the host's clock with the day (seen: a game a whole day behind, at about the same hour, was taken for
    # one 17 minutes ahead and set back, staying a day behind for good): the whole difference counts.
    def follower(hour, minute, day, host_clock):
        g = StandIn(hour, minute)
        comp, _ = make(g)
        comp.cfg["clock_mode"] = "host"
        comp.my_clock = (hour, minute, 1.0, False, day)
        friend(comp, "host", host_clock)
        return g, comp
    g, comp = follower(18, 2, 1046, [17, 45, 1047])           # a day behind (17 min "ahead" on the dial)
    await comp.follow_leader_clock()
    assert commands(g, "environment.time_skip") == [{"minutes": 1440 - 17}] and not commands(g, "environment.set_time")
    # ahead: back to the host's time exactly, on this save's day, rewinding (an hour alone moves FORWARD a day: seen)
    g, comp = follower(18, 2, 1047, [17, 45, 1047])
    await comp.follow_leader_clock()
    assert commands(g, "environment.set_time") == [{"Day": 1047, "Hour": 17, "Minute": 45, "allow_rewind": True}], g.sent
    assert not commands(g, "environment.time_skip")
    g, comp = follower(10, 0, 1048, [17, 45, 1047])           # a day ahead: back to the host's day too
    await comp.follow_leader_clock()
    assert commands(g, "environment.set_time") == [{"Day": 1047, "Hour": 17, "Minute": 45, "allow_rewind": True}], g.sent
    await comp.follow_leader_clock()                          # (asked again before the clock is read: not set twice)
    assert len(commands(g, "environment.set_time")) == 1
    g, comp = follower(17, 44, 1047, [17, 45, 1047])          # within the slack: left alone
    await comp.follow_leader_clock()
    assert not g.sent
    # Each save counts its own days (seen: 1018 on one PC, 1060 on the other, both Thursday): only the weekday is shared
    g, comp = follower(9, 39, 1060, [9, 53, 1018])            # same weekday, 14 min behind: forward 14, not "42 days ahead"
    await comp.follow_leader_clock()
    assert commands(g, "environment.time_skip") == [{"minutes": 14}], g.sent
    g, comp = follower(18, 2, 1059, [17, 45, 1018])           # a weekday behind on its own count: forward a day
    await comp.follow_leader_clock()
    assert commands(g, "environment.time_skip") == [{"minutes": 1440 - 17}], g.sent
    g, comp = follower(18, 2, 1060, [17, 45, 1018])           # ahead on the same weekday: back, on this save's day
    await comp.follow_leader_clock()
    assert commands(g, "environment.set_time") == [{"Day": 1060, "Hour": 17, "Minute": 45, "allow_rewind": True}], g.sent
    g, comp = follower(9, 0, 1047, [9, 40])                   # a host that doesn't send the day (older program)
    await comp.follow_leader_clock()
    assert commands(g, "environment.time_skip") == [{"minutes": 40}]

    # A friend whose game isn't running (no fresh state) doesn't count.
    g = StandIn(9, 0, speed=3)
    comp, _ = make(g)
    friend(comp, "a", [9, 0])
    comp.remotes["a"].state_at = time.time() - 60
    await comp.keep_speed()
    assert not commands(g, "environment.time_speed")

    # Sleep detection from what the Zoi is doing.
    assert companion.SLEEPING.search("Object_Bed_Sleep_Start") and companion.SLEEPING.search("Self_Nap")
    assert not companion.SLEEPING.search("Use_BarBell_Row")
    print("time sync tests passed")


if __name__ == "__main__":
    asyncio.run(main())
