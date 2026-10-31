"""This PC's addresses, to tell friends where to find a server hosted here; finding servers on the home network (LAN
discovery: a UDP broadcast the relay server answers); Tailscale's and other programs' state, for the self-test."""
import ipaddress
import json
import os
import select
import shutil
import socket
import subprocess
import time

TAILNET = ipaddress.ip_network("100.64.0.0/10")
DISCOVER_ASK = b"INZOI-MP?"          # (server/inzoi_mp_server.py answers it on UDP, on its TCP port number)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _tailscale_exe() -> str:
    return shutil.which("tailscale") or (r"C:\Program Files\Tailscale\tailscale.exe" if os.name == "nt" else "tailscale")


def lan_address() -> str | None:
    """This PC's address on the home network (the interface that leads to the internet; nothing is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") or ipaddress.ip_address(ip) in TAILNET else ip
    except OSError:
        return None
    finally:
        s.close()


def tailscale_self() -> tuple[str | None, str | None]:
    """(MagicDNS name, Tailscale IPv4) of this PC, if it's on Tailscale."""
    exe = shutil.which("tailscale") or (r"C:\Program Files\Tailscale\tailscale.exe" if os.name == "nt" else "tailscale")
    try:
        out = subprocess.run([exe, "status", "--json", "--peers=false"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        me = json.loads(out.stdout).get("Self") or {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None
    ips = [ip for ip in me.get("TailscaleIPs") or [] if "." in ip]
    return (me.get("DNSName") or "").rstrip(".") or None, ips[0] if ips else None


def addresses(port: int) -> list[dict]:
    """Where friends can reach a server on this PC: [{address, how}]. The Tailscale IP, not its name: names change."""
    at = "" if port == 24242 else f":{port}"
    out = []
    _, ts_ip = tailscale_self()
    if ts_ip:
        out.append({"address": ts_ip + at, "how": "friends anywhere, over Tailscale (share this PC with them first)"})
    lan = lan_address()
    if lan:
        out.append({"address": lan + at, "how": "friends in your home (same Wi-Fi or network)"})
    return out


def tailscale_status() -> dict:
    """{installed, running, online, state, ip, error?}: Tailscale on this PC (running = logged in and connected)."""
    exe = _tailscale_exe()
    if not (shutil.which("tailscale") or os.path.exists(exe)):
        return {"installed": False, "running": False, "online": False, "state": None, "ip": None}
    try:
        out = subprocess.run([exe, "status", "--json", "--peers=false"], capture_output=True, text=True, timeout=5,
                             creationflags=NO_WINDOW)
        st = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {"installed": True, "running": False, "online": False, "state": None, "ip": None, "error": str(e)}
    me = st.get("Self") or {}
    ips = [ip for ip in me.get("TailscaleIPs") or [] if "." in ip]
    state = st.get("BackendState")
    return {"installed": True, "running": state == "Running", "online": bool(me.get("Online", state == "Running")),
            "state": state, "ip": ips[0] if ips else None}


def running_processes(names) -> set[str]:
    """Which of these program names (e.g. "nordvpn.exe", any case) are running now. Windows only (else empty)."""
    if os.name != "nt":
        return set()
    want = {n.lower() for n in names}
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=8,
                             creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {n for n in (line.split(",")[0].strip('"').lower() for line in out.splitlines()) if n in want}


def process_id(name: str) -> int | None:
    """The process id of a running program (e.g. "inZOI-Win64-Shipping.exe"), or None. Windows only."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=8, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) > 1 and cols[0].lower() == name.lower() and cols[1].isdigit():
            return int(cols[1])
    return None


def local_ipv4() -> list[str]:
    """This PC's IPv4 addresses (not loopback or link-local)."""
    ips = set()
    try:
        ips.update(i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    lan = lan_address()
    if lan:
        ips.add(lan)
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


def broadcast_addresses() -> list[tuple[str, str]]:
    """(this PC's address, its network's broadcast address) for each home-network interface. The netmask isn't known
    without extra packages: /24 is assumed (what home routers use), and 255.255.255.255 goes out from each address too.
    Tailscale carries no broadcasts: skipped."""
    return [(ip, str(ipaddress.ip_network(ip + "/24", strict=False).broadcast_address))
            for ip in local_ipv4() if ipaddress.ip_address(ip) not in TAILNET]


def discover(port: int = 24242, timeout: float = 1.5, targets: list[str] | None = None) -> list[dict]:
    """Servers on the home network: broadcast DISCOVER_ASK on every interface (or send it to `targets`) and collect
    the answers for `timeout` seconds. Each is the server's discover answer + {ip, address} (address as the servers
    page writes it). One entry per server id."""
    plan = [("", t) for t in targets] if targets else [("", "255.255.255.255")] + [
        (src, dst) for ip, b in broadcast_addresses() for src, dst in ((ip, b), (ip, "255.255.255.255"))]
    socks = {}
    for src, dst in plan:
        s = socks.get(src)
        if s is None:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.bind((src, 0))
                s.setblocking(False)
            except OSError:
                continue
            socks[src] = s
        try:
            s.sendto(DISCOVER_ASK, (dst, port))
        except OSError:
            pass
    found = {}
    end = time.monotonic() + timeout
    try:
        while socks and (left := end - time.monotonic()) > 0:
            ready, _, _ = select.select(list(socks.values()), [], [], left)
            for s in ready:
                try:
                    data, (ip, _) = s.recvfrom(4096)
                    info = json.loads(data)
                except (OSError, ValueError):
                    continue
                if not isinstance(info, dict) or info.get("t") != "discover":
                    continue
                p = int(info.get("port") or port)
                entry = {**info, "ip": ip, "address": ip if p == 24242 else f"{ip}:{p}"}
                key = info.get("id") or entry["address"]
                old = found.get(key)
                if old is None or (ipaddress.ip_address(old["ip"]) in TAILNET and ipaddress.ip_address(ip) not in TAILNET):
                    found[key] = entry               # (the home network's address rather than the Tailscale one)
    finally:
        for s in socks.values():
            s.close()
    return list(found.values())
