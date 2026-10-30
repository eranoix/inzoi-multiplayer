"""coop-casal: let a friend reach the server this PC hosts, through Windows Firewall.

One inbound rule per protocol (TCP for the game connection, UDP for finding the host on the home network), each tied
to THIS program and THIS port, on private networks only, from the home network and Tailscale (100.64.0.0/10) only.
Tied to the program means the port is only open while the program runs and listens: once it's closed (or stops
hosting because inZOI closed), nothing answers there and no other program can use the rule.

Writing firewall rules needs an administrator, so Windows asks once ("allow this app to make changes?"). The rules
stay, so it isn't asked again, unless the program moved (a new Python, the exe elsewhere). A "no" is remembered.
The same step removes "block" rules Windows made for this program if someone once pressed Cancel on its prompt.
"""
import base64
import logging
import os
import subprocess
import sys

NAMES = ("inZOI Co-op (TCP)", "inZOI Co-op (UDP)")
REMOTE = "LocalSubnet,100.64.0.0/10"
log = logging.getLogger("firewall")
_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def program() -> str:
    return sys.executable


def _rule_ok(name: str, prog: str, port: int) -> bool:
    """netsh can read rules without an administrator; its labels are in the Windows language, so only look for the
    values (the program's path and the port)."""
    try:
        out = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}", "verbose"],
                             capture_output=True, text=True, errors="replace", timeout=20, creationflags=_flags)
    except (OSError, subprocess.SubprocessError):
        return False
    text = out.stdout.lower()
    return out.returncode == 0 and prog.lower() in text and str(port) in text


def is_open(port: int) -> bool:
    prog = program()
    return all(_rule_ok(n, prog, port) for n in NAMES)


def _script(prog: str, port: int) -> str:
    p = prog.replace("'", "''")
    lines = [f"$p = '{p}'",
             "Get-NetFirewallApplicationFilter -Program $p -ErrorAction SilentlyContinue | Get-NetFirewallRule |"
             " Where-Object Action -eq 'Block' | Remove-NetFirewallRule",
             f"Remove-NetFirewallRule -DisplayName '{NAMES[0]}','{NAMES[1]}' -ErrorAction SilentlyContinue"]
    for name, proto in zip(NAMES, ("TCP", "UDP")):
        lines.append(f"New-NetFirewallRule -DisplayName '{name}' -Direction Inbound -Action Allow -Program $p "
                     f"-Protocol {proto} -LocalPort {int(port)} -Profile Private "
                     f"-RemoteAddress {REMOTE} | Out-Null")
    return "\n".join(lines)


def ensure(port: int, cfg: dict | None = None) -> str:
    """Make sure the rules exist for this program and port. Returns "open", "declined", "unsupported" or "failed".
    cfg (the companion's config): remembers a "no" (firewall_declined) so Windows doesn't ask every time."""
    if os.name != "nt" or any(os.environ.get(k) for k in ("INZOI_MP_NO_FIREWALL", "INZOI_MP_TEST", "INZOI_MP_GAME_URL")):
        return "unsupported"                             # (the tests: never a Windows prompt)
    prog = program()
    if is_open(port):
        return "open"
    if cfg is not None and cfg.get("firewall_declined") == f"{prog}|{port}":
        return "declined"
    encoded = base64.b64encode(_script(prog, port).encode("utf-16-le")).decode()
    launch = ("try { Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
              f"-ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','{encoded}'; exit 0 }} catch {{ exit 5 }}")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", launch], capture_output=True,
                           timeout=300, creationflags=_flags)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("couldn't open the firewall: %s", e)
        return "failed"
    if r.returncode == 5:
        log.warning("opening the firewall for port %d was declined: friends may not reach this server", port)
        if cfg is not None:
            cfg["firewall_declined"] = f"{prog}|{port}"
        return "declined"
    if is_open(port):
        log.info("firewall open for %s on port %d (TCP+UDP, private networks)", prog, port)
        if cfg is not None:
            cfg.pop("firewall_declined", None)
        return "open"
    log.warning("the firewall rules didn't take (exit %s)", r.returncode)
    return "failed"
