"""The program lives as long as inZOI (client/launcher.py follow_game): it must never close under a running game.
- the game's window not found for a long while (away, recreated, or its process unreadable) while inZOI's process runs:
  the program carries on
- the process gone: "inZOI closed" after ~30 s, as before
- a failed process-name lookup isn't kept (client/menu.py _exe_name): the window is recognized once it can be read

    python tools/test_follow_game.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["APPDATA"] = tempfile.mkdtemp()                 # (never the real config)
os.environ["INZOI_MP_DOCUMENTS"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import launcher  # noqa: E402
import menu  # noqa: E402


async def run(windows, processes, max_steps=200):
    """Feed follow_game a window/process answer per 3 s step; -> (its result or None, steps taken)."""
    steps = {"n": 0}
    real_sleep = asyncio.sleep

    async def fast(_s):
        steps["n"] += 1
        if steps["n"] > max_steps:
            raise TimeoutError
        await real_sleep(0)
    win, proc = iter(windows), iter(processes)
    launcher.asyncio.sleep = fast
    menu.game_window = lambda: next(win, None)
    menu.game_running = lambda: next(proc, False)
    launcher.testing = lambda: False
    launcher.KEEP_HOSTING = False
    try:
        return await launcher.follow_game(keep=False), steps["n"]
    except TimeoutError:
        return None, steps["n"]
    finally:
        launcher.asyncio.sleep = real_sleep


async def main():
    if os.name != "nt":
        print("follow_game tests: Windows only (skipped)")
        return
    # the window is there, then can't be found for 5 minutes while the game runs: never "closed"
    res, n = await run([1] + [None] * 150, [True] * 150, max_steps=120)
    assert res is None, f"the program closed under a running game after {n} steps: {res}"
    print("no window found for minutes while inZOI runs: the program carries on: ok")

    # the game really closes: window and process gone -> closed after ~30 s (10 steps of 3 s)
    res, n = await run([1, 1] + [None] * 50, [False] * 50)
    assert res == "inZOI closed" and 10 <= n <= 14, (res, n)
    print("inZOI's process gone: the program closes after ~30 s: ok")

    # the process list can't be read (None): not taken as closed either
    res, n = await run([1] + [None] * 150, [None] * 150, max_steps=120)
    assert res is None, (res, n)
    print("the process list unreadable: not taken as closed: ok")

    # started while inZOI already runs, its window never found (seen on Chey's PC: closed after half an hour, "inZOI
    # didn't start"): the running process counts as started
    real_never = launcher.NEVER_STARTED
    launcher.NEVER_STARTED = 0
    try:
        res, n = await run([None] * 150, [True] * 150, max_steps=60)
        assert res is None, f"closed under a running game whose window wasn't found: {res}"
        res, n = await run([None] * 150, [True] * 5 + [False] * 150)
        assert res == "inZOI closed", (res, n)
        res, n = await run([None] * 150, [False] * 150, max_steps=60)
        assert res == "inZOI didn't start", (res, n)
    finally:
        launcher.NEVER_STARTED = real_never
    print("started under a running inZOI whose window isn't found: carries on; its process gone: closes: ok")

    # a failed name lookup isn't cached
    menu._names.clear()
    calls = []
    real_open = menu.kernel32.OpenProcess

    first = {"done": False}

    def flaky_open(access, inherit, pid):
        calls.append(pid)
        if not first["done"]:
            first["done"] = True
            return 0                                          # (access denied, once)
        return real_open(access, inherit, pid)
    menu.kernel32.OpenProcess = flaky_open
    try:
        me = os.getpid()
        assert menu._exe_name(me) == ""
        assert menu._exe_name(me).endswith(".exe"), "a failed lookup was kept"
    finally:
        menu.kernel32.OpenProcess = real_open
    print("a process name that couldn't be read once is read again: ok")
    print("follow_game tests passed")


asyncio.run(main())
