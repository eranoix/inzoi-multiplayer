"""Updates itself from GitHub: every release of github.com/eranoix/inzoi-multiplayer carries inZOI-Multiplayer.exe.

latest() asks GitHub for the latest release; the game's Multiplayer page offers a newer one, and "Update" downloads it
into %APPDATA%/inzoi-mp/update, checks it against the sha256 GitHub lists for it and against our own code-signing
certificate (signed_by_us: only exes built and signed on Arthur's PC are taken), and apply() swaps the running exe for
the new one (Windows lets a running exe be renamed, not overwritten: the old one becomes "<name>.old.exe" and is deleted
by the next start) and starts it.

Only the packaged exe updates itself. Run from source (python client/launcher.py), it just says a new version exists.
"""
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

REPO = "eranoix/inzoi-multiplayer"         # coop-casal: our fork's releases, not upstream's
ENABLED = True
SIGNER = "71F550645942BC38BD8353A6FB3FAEB6DCDA1A14"   # thumbprint of our code-signing certificate (tools/criar-certificado.ps1)
API = os.environ.get("INZOI_MP_UPDATE_API") or f"https://api.github.com/repos/{REPO}/releases/latest"   # (tests: other)
ASSET = "inZOI-Multiplayer.exe"
DIR = Path(os.environ.get("APPDATA", Path.home())) / "inzoi-mp" / "update"
HEALTH = DIR / "health.json"                # coop-casal: {version, fails, blocked} for rolling back a broken update
FAILS_TO_ROLL_BACK = 3                      # this start is the 3rd in a row of a version that never ran healthy
log = logging.getLogger("update")


def version_tuple(v: str) -> tuple:
    out = []
    for part in str(v).lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def latest(current: str) -> dict | None:
    """The latest release if it's newer than current: {version, title, url, size, sha256}."""
    if not ENABLED:
        return None
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"inZOI-Multiplayer/{current}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rel = json.loads(r.read().decode("utf-8"))
    version = str(rel.get("tag_name", "")).lstrip("vV")
    if not version or version_tuple(version) <= version_tuple(current) or rel.get("draft") or rel.get("prerelease"):
        return None
    if version in blocked():
        return None                                      # it was rolled back: wait for a newer one
    asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET), None)
    if not asset:
        return None
    digest = str(asset.get("digest") or "")
    return {"version": version, "title": rel.get("name") or f"inZOI Multiplayer {version}",
            "url": asset["browser_download_url"], "size": int(asset.get("size") or 0),
            "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else None}


def ready_file(version: str) -> Path:
    return DIR / f"inZOI-Multiplayer-{version}.exe"


def download(info: dict, progress=None) -> Path:
    """Fetch the new exe (resumes nothing: it's ~11 MB) and check it. Returns its path."""
    dest = ready_file(info["version"])
    if dest.exists() and (not info["sha256"] or sha256(dest) == info["sha256"]) and signed_by_us(dest):
        return dest
    DIR.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")
    digest = hashlib.sha256()
    req = urllib.request.Request(info["url"], headers={"User-Agent": "inZOI-Multiplayer"})
    with urllib.request.urlopen(req, timeout=60) as r, part.open("wb") as f:
        while block := r.read(256 * 1024):
            f.write(block)
            digest.update(block)
            if progress:
                progress(len(block))
    if info["sha256"] and digest.hexdigest() != info["sha256"]:
        part.unlink()
        raise ValueError("the downloaded update doesn't match GitHub's fingerprint")
    if not signed_by_us(part):
        part.unlink()
        raise ValueError("the downloaded update isn't signed with our certificate")
    os.replace(part, dest)
    return dest


def signed_by_us(path: Path) -> bool:
    """The exe carries an Authenticode signature by SIGNER (our own certificate: tools/criar-certificado.ps1).
    GitHub's sha256 only proves the download wasn't damaged; this proves the exe was built on Arthur's PC.
    NotTrusted/UnknownError only mean this PC hasn't trusted the self-signed certificate (confiar-certificado.bat);
    a tampered file is HashMismatch."""
    if not SIGNER:
        return False
    ps = ("$s = Get-AuthenticodeSignature -LiteralPath $env:INZOI_MP_SIGNED; "
          "\"$($s.Status)|$($s.SignerCertificate.Thumbprint)\"")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                             text=True, timeout=60, env={**os.environ, "INZOI_MP_SIGNED": str(path)},
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("couldn't check the update's signature: %s", e)
        return False
    status, _, thumb = out.partition("|")
    ok = status in ("Valid", "NotTrusted", "UnknownError") and thumb.upper() == SIGNER.upper()
    if not ok:
        log.warning("update %s rejected: signature %s by %s", path.name, status or "?", thumb or "nobody")
    return ok


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pending(current: str) -> Path | None:
    """A downloaded update newer than this version, waiting to be applied."""
    best = None
    for f in DIR.glob("inZOI-Multiplayer-*.exe") if DIR.exists() else []:
        v = f.stem.split("inZOI-Multiplayer-", 1)[1]
        if v in blocked():
            continue
        if version_tuple(v) > version_tuple(current) and (best is None or version_tuple(v) > version_tuple(best[0])):
            best = (v, f)
    return best[1] if best and signed_by_us(best[1]) else None


def apply(new_exe: Path, args: list[str]):
    """Put the new exe where this one is and start it (in a window of its own). The caller exits right after."""
    exe = Path(sys.executable)
    old = exe.with_name(exe.stem + ".prev.exe")         # kept (not deleted) so a broken update can be rolled back
    try:
        old.unlink()
    except FileNotFoundError:
        pass
    exe.rename(old)
    try:
        os.replace(new_exe, exe)
    except OSError:
        old.rename(exe)                                  # put things back as they were
        raise
    # A one-file exe started from another one would otherwise inherit its unpacked files (gone once this one exits).
    # --restarted: it waits for this one to close.
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    subprocess.Popen([str(exe), *args, "--restarted"], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                     close_fds=True, env=env)


def clean_old():
    """Delete the exes left behind while they were running: a pre-0.8.2 update's ".old" and a rolled-back ".bad".
    The ".prev" one stays: it's what a broken update rolls back to."""
    if frozen():
        exe = Path(sys.executable)
        for tail in (".old.exe", ".bad.exe"):
            try:
                exe.with_name(exe.stem + tail).unlink()
            except OSError:
                pass
    for f in DIR.glob("*.part") if DIR.exists() else []:
        f.unlink(missing_ok=True)
    if frozen():
        threading.Thread(target=trust_self, daemon=True).start()


def trust_self():
    """coop-casal: the exe trusts its own publisher. The first time it runs, our certificate (carried inside the exe)
    goes into this user's Trusted Root and Trusted Publishers, so Windows shows "inZOI Co-op (eranoix)" instead of an
    unknown publisher. Windows asks once ("install this certificate?"); a "no" is remembered and never asked again.
    The current user's stores only: no administrator needed. Updates don't depend on it (signed_by_us checks the
    thumbprint either way)."""
    cer = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent / "release")) / "coop-cert.cer"
    mark = DIR.parent / "cert_trusted.txt"
    if not SIGNER or not cer.exists() or mark.exists():
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        for store in ("TrustedPublisher", "Root"):                     # Root last: that's the one Windows asks about
            have = subprocess.run(["certutil", "-user", "-verifystore", store, SIGNER], capture_output=True,
                                  creationflags=flags, timeout=30).returncode == 0
            if not have:
                r = subprocess.run(["certutil", "-user", "-f", "-addstore", store, str(cer)], capture_output=True,
                                   creationflags=flags, timeout=600)
                log.info("certificate -> %s: %s", store, "ok" if r.returncode == 0 else f"declined ({r.returncode})")
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(SIGNER, "utf-8")
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("couldn't trust our certificate: %s", e)


# ---------------------------------------------------------------------------------------------- rolling back
def _health() -> dict:
    try:
        h = json.loads(HEALTH.read_text("utf-8"))
        return h if isinstance(h, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_health(h: dict):
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = HEALTH.with_suffix(".tmp")
        tmp.write_text(json.dumps(h), "utf-8")
        os.replace(tmp, HEALTH)
    except OSError as e:
        log.warning("couldn't save the update health: %s", e)


def blocked() -> list:
    """Versions that were rolled back: never offered or applied again (a newer release is)."""
    return [str(v) for v in _health().get("blocked", [])]


def startup_check(current: str, args: list[str]) -> bool:
    """coop-casal: count this start; a version that didn't run healthy (mark_healthy) for FAILS_TO_ROLL_BACK starts
    in a row is swapped back for the exe it replaced (".prev", if it carries our signature), blocked, and that one
    is started instead. True: rolled back, the caller exits right away."""
    if not frozen():
        return False
    h = _health()
    if h.get("version") != current:
        h.update(version=current, fails=0)
    h["fails"] = int(h.get("fails") or 0) + 1
    exe = Path(sys.executable)
    prev = exe.with_name(exe.stem + ".prev.exe")
    if h["fails"] < FAILS_TO_ROLL_BACK or not prev.exists() or not signed_by_us(prev):
        _save_health(h)
        return False
    log.warning("version %s failed to start %d times in a row: going back to the previous one", current, h["fails"] - 1)
    bad = exe.with_name(exe.stem + ".bad.exe")
    try:
        bad.unlink(missing_ok=True)
        exe.rename(bad)
        try:
            os.replace(prev, exe)
        except OSError:
            bad.rename(exe)
            raise
    except OSError as e:
        log.warning("couldn't roll back: %s", e)
        _save_health(h)
        return False
    h["blocked"] = sorted({*blocked(), current}, key=version_tuple)
    h.update(version=None, fails=0)
    _save_health(h)
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    subprocess.Popen([str(exe), *args, "--restarted"], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                     close_fds=True, env=env)
    return True


def mark_healthy():
    """This version runs fine (a couple of minutes up, or a normal exit): no rollback for it."""
    if frozen():
        h = _health()
        if h.get("fails"):
            h["fails"] = 0
            _save_health(h)
