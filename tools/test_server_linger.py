"""A player whose connection breaks (no "bye") lingers a few seconds: a quick reconnect is a silent rejoin (their Zoi
doesn't flicker out and back for the others); leaving with "bye" is told at once; not coming back is told after.

    python tools/test_server_linger.py
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import inzoi_mp_server as mp  # noqa: E402


async def join(port, key, name):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write((json.dumps({"t": "hello", "protocol": mp.PROTOCOL, "key": key, "name": name}) + "\n").encode())
    await w.drain()
    welcome = json.loads(await r.readline())
    assert welcome["t"] == "welcome", welcome
    return r, w


async def next_of(r, kinds, timeout):
    """The next message whose type is in kinds (others skipped), or None after timeout."""
    end = asyncio.get_running_loop().time() + timeout
    while True:
        left = end - asyncio.get_running_loop().time()
        if left <= 0:
            return None
        try:
            line = await asyncio.wait_for(r.readline(), left)
        except asyncio.TimeoutError:
            return None
        m = json.loads(line)
        if m.get("t") in kinds:
            return m


async def main():
    mp.LINGER = 1.5
    with tempfile.TemporaryDirectory() as tmp:
        server = mp.Server(mp.Store(Path(tmp)))
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        ra, wa = await join(port, "a" * 20, "Ana")
        rb, wb = await join(port, "b" * 20, "Ben")
        assert (await next_of(ra, {"joined"}, 2))["player"]["name"] == "Ben"

        wb.close()                                           # Ben's program is killed: no "bye"
        assert await next_of(ra, {"left"}, 0.8) is None, "not told at once"
        rb, wb = await join(port, "b" * 20, "Ben")           # ...and comes straight back
        m = await next_of(ra, {"left", "rejoined", "joined"}, 3)
        assert m and m["t"] == "rejoined", m
        print("  ok: a quick reconnect is a silent rejoin")

        wb.write(b'{"t":"bye"}\n')                           # Ben quits on purpose
        await wb.drain()
        wb.close()
        m = await next_of(ra, {"left"}, 1.0)
        assert m and m["name"] == "Ben", m
        print("  ok: leaving with bye is told at once")

        rb, wb = await join(port, "b" * 20, "Ben")
        await next_of(ra, {"joined"}, 2)
        wb.close()                                           # gone for good, no bye
        assert await next_of(ra, {"left"}, 0.8) is None
        m = await next_of(ra, {"left"}, 3)
        assert m and m["name"] == "Ben", m
        print("  ok: not coming back is told after the linger")
        wa.close()
        srv.close()
    print("server linger tests passed")


if __name__ == "__main__":
    asyncio.run(main())
