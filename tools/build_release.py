"""Build what friends download: one program, "inZOI Multiplayer.exe" (play, host, or both; the game mod is inside it),
and a zip with that exe, the README and the server script for a Raspberry Pi / Linux box.

    python tools/build_release.py
    python tools/build_release.py --publish notes.md     # and put it on GitHub as release v<version>

Every player's program updates itself from the latest GitHub release (client/updater.py), so publishing is how an
update reaches everyone. notes.md: its first line (without "#") is the release title, the rest its description.

The first run creates build/venv with PyInstaller in it (needs the internet once). Output in dist/:
    inZOI-Multiplayer.exe               the program on its own
    inZOI-Multiplayer-<version>.zip     inZOI Multiplayer.exe, README.txt, server for Raspberry Pi or Linux/
"""
import base64
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
VENV_PY = BUILD / "venv" / "Scripts" / "python.exe"
EXE_NAME = "inZOI Multiplayer"
LINUX_SERVER = ["inzoi_mp_server.py", "PROTOCOL.md", "inzoi-mp.service"]
SKIP_MOD = {"MPCmd.lua", "MPBulk.lua", "MPSeq.lua", "inject.json", "__pycache__", "out", "signals_dir.txt", "version.txt"}
# UE4SS (github.com/UE4SS-RE/RE-UE4SS, MIT), the script loader behind the main menu's Multiplayer button: the very
# build that was tested with inZOI, checked by its sha256 (the "experimental-latest" release changes every day).
UE4SS_ZIP = "UE4SS_v3.0.1-1165-g12ec45a0.zip"
UE4SS_SHA256 = "d51512869bde8c32a1ebd6732a01fbb1343e6b3e0fb9cd0d8a9b1841b68cba97"
UE4SS_URL = "https://github.com/UE4SS-RE/RE-UE4SS/releases/download/experimental-latest/" + UE4SS_ZIP

BG, BLUE, MINT = (22, 34, 56), (90, 168, 240), (127, 209, 185)      # the in-game panel's colours


def pyinstaller() -> Path:
    if not VENV_PY.exists():
        subprocess.run([sys.executable, "-m", "venv", str(BUILD / "venv")], check=True)
        subprocess.run([str(VENV_PY), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "pyinstaller"],
                       check=True)
    # Pillow draws the "Multiplayer" item on the game's main menu (client/menu.py)
    subprocess.run([str(VENV_PY), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "pillow"], check=True)
    return VENV_PY


# ---------------------------------------------------------------------------------------------------- icon
def _person(x, y, hx, hy, head, body, grow=0.0) -> bool:
    """A bust: round head at (hx, hy), shoulders below it (cut off by the icon's edge)."""
    if (x - hx) ** 2 + (y - hy) ** 2 <= (head + grow) ** 2:
        return True
    bx, by = hx, hy + head + 22 + body
    return ((x - bx) / (body + 8 + grow)) ** 2 + ((y - by) / (body + grow)) ** 2 <= 1


def _letter_i(x, y, cx, top, w, h, grow=0.0) -> bool:
    """A lowercase "i" (round dot over a rounded stem) - also a person: head and body."""
    r = w / 2 + grow
    if (x - cx) ** 2 + (y - (top + w / 2)) ** 2 <= (w * 0.58 + grow) ** 2:      # the dot
        return True
    y0, y1 = top + w * 1.55 + w / 2, top + h - w / 2                           # the stem: a capsule
    py = min(max(y, y0), y1)
    return (x - cx) ** 2 + (y - py) ** 2 <= r * r


def _shade(x, y):
    """Colour at a point of the 256x256 icon (None = transparent): two lowercase i's side by side, two players."""
    r = 56                                                      # rounded square, 8 px margin
    cx, cy = min(max(x, 8 + r), 248 - r), min(max(y, 8 + r), 248 - r)
    if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
        return None
    if _letter_i(x, y, 152, 46, 48, 168):
        return MINT
    if _letter_i(x, y, 152, 46, 48, 168, grow=11):             # a gap between the two
        return BG
    if _letter_i(x, y, 96, 70, 40, 140):
        return BLUE
    return BG


def _png(size: int, rgba: bytes) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def make_icon(path: Path):
    images = []
    for size, ss in ((256, 2), (64, 3), (48, 4), (32, 4), (16, 4)):
        px = bytearray()
        for y in range(size):
            for x in range(size):
                r = g = b = n = 0
                for j in range(ss):
                    for i in range(ss):
                        c = _shade((x + (i + 0.5) / ss) * 256 / size, (y + (j + 0.5) / ss) * 256 / size)
                        if c:
                            r, g, b, n = r + c[0], g + c[1], b + c[2], n + 1
                px += bytes((r // n, g // n, b // n, 255 * n // (ss * ss))) if n else b"\0\0\0\0"
        images.append((size, _png(size, bytes(px))))
    path.with_suffix(".png").write_bytes(images[0][1])
    entries, blobs, offset = b"", b"", 6 + 16 * len(images)
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    path.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + entries + blobs)


# ---------------------------------------------------------------------------------------------------- exe
VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=(@T@, 0), prodvers=(@T@, 0), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'inZOI Multiplayer (fan-made)'),
      StringStruct('FileDescription', 'inZOI Multiplayer'),
      StringStruct('FileVersion', '@V@'),
      StringStruct('InternalName', 'inZOI Multiplayer'),
      StringStruct('LegalCopyright', 'Fan-made mod, not affiliated with KRAFTON'),
      StringStruct('OriginalFilename', 'inZOI Multiplayer.exe'),
      StringStruct('ProductName', 'inZOI Multiplayer'),
      StringStruct('ProductVersion', '@V@')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def sync_versions(version: str):
    """One version number everywhere: the program's (client/companion.py VERSION) goes into the game mod (its .uplugin,
    which also becomes the mod_manifest Version the game records in saves), the panel's manifest and the server."""
    nums = [int(x) for x in (version.split(".") + ["0", "0"])[:3]]
    mod = ROOT / "mod" / "InZOIMultiplayer"
    plugin = mod / "InZOIMultiplayer.uplugin"
    data = json.loads(plugin.read_text("utf-8"))
    data.update(VersionName=version, Version=nums[0] * 10000 + nums[1] * 100 + nums[2])
    plugin.write_text(json.dumps(data, indent="\t") + "\n", "utf-8")
    panel = mod / "ui" / "mp" / "uimod_manifest.json"
    data = json.loads(panel.read_text("utf-8"))
    data["version"] = version
    panel.write_text(json.dumps(data, indent="\t") + "\n", "utf-8")
    server = ROOT / "server" / "inzoi_mp_server.py"
    text = server.read_text("utf-8")
    server.write_text(re.sub(r'^VERSION = "[^"]*"', f'VERSION = "{version}"', text, count=1, flags=re.M), "utf-8")


def ue4ss_runtime() -> Path:
    """UE4SS's files for the exe (build/ue4ss_runtime), from the pinned zip (build/cache, downloaded once)."""
    import hashlib
    import urllib.request
    out = BUILD / "ue4ss_runtime"
    zpath = BUILD / "cache" / UE4SS_ZIP
    if not zpath.exists():
        zpath.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {UE4SS_URL}")
        try:
            urllib.request.urlretrieve(UE4SS_URL, zpath)
        except OSError as e:            # coop-casal: the pinned build left "experimental-latest"; build without it
            print(f"UE4SS not available ({e}): building without it (the menu item is drawn over the game instead)")
            return None
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    if digest != UE4SS_SHA256:
        raise SystemExit(f"{zpath} isn't the tested UE4SS build (sha256 {digest}); put the right one in build/cache")
    if out.exists():
        shutil.rmtree(out)
    with zipfile.ZipFile(zpath) as z:
        for name, dest in (("dwmapi.dll", "dwmapi.dll"), ("ue4ss/UE4SS.dll", "ue4ss/UE4SS.dll"),
                           ("ue4ss/UE4SS-settings.ini", "ue4ss/UE4SS-settings.ini"), ("ue4ss/LICENSE", "ue4ss/LICENSE")):
            (out / dest).parent.mkdir(parents=True, exist_ok=True)
            (out / dest).write_bytes(z.read(name))
    return out


def build_exe(version: str) -> Path:
    stage = BUILD / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(ROOT / "mod" / "InZOIMultiplayer", stage / "mod" / "InZOIMultiplayer",
                    ignore=lambda d, names: [n for n in names if n in SKIP_MOD])
    shutil.copytree(ROOT / "mod" / "ue4ss", stage / "ue4ss_mod",
                    ignore=lambda d, names: [n for n in names if n in SKIP_MOD])
    runtime = ue4ss_runtime()
    icon, vfile = ROOT / "release" / "icon.ico", BUILD / "version_info.txt"
    if not icon.exists():                                   # the chosen icon; without it, draw the built-in one
        icon = BUILD / "icon.ico"
        make_icon(icon)
    nums = ", ".join((version.split(".") + ["0", "0"])[:3])
    vfile.write_text(VERSION_INFO.replace("@T@", nums).replace("@V@", version), "utf-8")
    subprocess.run([
        str(pyinstaller()), "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
        "--onefile", "--windowed", "--name", EXE_NAME, "--icon", str(icon), "--version-file", str(vfile),
        "--paths", str(ROOT / "client"), "--paths", str(ROOT / "server"),
        "--add-data", f"{stage / 'mod'}{os.pathsep}mod",
        "--add-data", f"{ROOT / 'client' / 'struct_cache.json'}{os.pathsep}.",
        "--add-data", f"{ROOT / 'release' / 'coop-cert.cer'}{os.pathsep}.",     # the exe trusts its own publisher
        "--add-data", f"{ROOT / 'client' / 'page'}{os.pathsep}page",       # the servers page, with its font
        *(["--add-data", f"{runtime}{os.pathsep}ue4ss_runtime"] if runtime else []),   # UE4SS, for the menu button
        "--add-data", f"{stage / 'ue4ss_mod'}{os.pathsep}ue4ss_mod",        # our script for it
        "--exclude-module", "tkinter", "--exclude-module", "unittest", "--exclude-module", "pydoc",
        "--distpath", str(BUILD / "exe"), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
        str(ROOT / "client" / "launcher.py")], check=True)
    return BUILD / "exe" / f"{EXE_NAME}.exe"


def sign(exe: Path):
    """coop-casal: Authenticode-sign the exe with our certificate (tools/criar-certificado.ps1), the one the updater
    insists on (client/updater.py SIGNER). The private key never leaves this PC's certificate store."""
    signer = re.search(r'^SIGNER = "([0-9A-Fa-f]*)"', (ROOT / "client" / "updater.py").read_text("utf-8"), re.M).group(1)
    if not signer:
        raise SystemExit("no certificate yet: run  powershell -ExecutionPolicy Bypass -File tools\\criar-certificado.ps1")
    ps = (f"$c = Get-Item Cert:\\CurrentUser\\My\\{signer} -ErrorAction Stop; "
          "$s = Set-AuthenticodeSignature -LiteralPath $env:SIGN_EXE -Certificate $c -HashAlgorithm SHA256 "
          "-TimestampServer http://timestamp.digicert.com; "
          "if ($s.Status -eq 'UnknownError' -or $s.Status -eq 'Valid') { 'signed' } else { throw $s.StatusMessage }")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                         text=True, env={**os.environ, "SIGN_EXE": str(exe)})
    if "signed" not in out.stdout:
        raise SystemExit(f"couldn't sign {exe.name}: {out.stderr.strip() or out.stdout.strip()}")
    print(f"signed with {signer}")


def trust_script(dest: Path):
    """confiar-certificado.bat: run once on a friend's PC, so Windows knows the exe's publisher (our certificate,
    carried inside the script). Goes in the current user's stores only: no administrator needed."""
    b64 = base64.b64encode((ROOT / "release" / "coop-cert.cer").read_bytes()).decode()
    dest.write_text(
        "@echo off\r\n"
        "rem Roda uma vez: faz o Windows confiar no certificado que assina o inZOI-Multiplayer.exe do co-op.\r\n"
        "rem O Windows vai perguntar se deseja instalar o certificado: clique em Sim.\r\n"
        "set CER=%TEMP%\\inzoi-coop-cert.cer\r\n"
        f"powershell -NoProfile -Command \"[IO.File]::WriteAllBytes($env:CER, "
        f"[Convert]::FromBase64String('{b64}'))\"\r\n"
        "certutil -user -addstore Root \"%CER%\"\r\n"
        "certutil -user -addstore TrustedPublisher \"%CER%\"\r\n"
        "del \"%CER%\"\r\n"
        "echo.\r\necho Pronto! Agora e so abrir o inZOI-Multiplayer.exe.\r\npause\r\n", "ascii")


def main():
    version = re.search(r'VERSION = "([^"]+)"', (ROOT / "client" / "companion.py").read_text("utf-8")).group(1)
    sync_versions(version)
    exe = build_exe(version)
    sign(exe)
    name = f"inZOI-Multiplayer-{version}"
    out = DIST / name
    if out.exists():
        shutil.rmtree(out)
    linux = out / "server for Raspberry Pi or Linux"
    linux.mkdir(parents=True)
    shutil.copy2(exe, out / exe.name)
    shutil.copy2(ROOT / "release" / "README.txt", out / "README.txt")
    for f in LINUX_SERVER:
        shutil.copy2(ROOT / "server" / f, linux / f)
    zpath = DIST / f"{name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                z.write(f, Path(name) / f.relative_to(out))
    shutil.copy2(exe, DIST / "inZOI-Multiplayer.exe")
    trust_script(DIST / "confiar-certificado.bat")
    print(f"{DIST / 'inZOI-Multiplayer.exe'}  ({exe.stat().st_size // 1024} KB)")
    print(f"{zpath}  ({zpath.stat().st_size // 1024} KB)")
    return version, zpath


def publish(version: str, zpath: Path, notes: Path):
    """GitHub release v<version> with the exe (what programs update to) and the zip."""
    text = notes.read_text("utf-8").strip()
    title, _, body = text.partition("\n")
    subprocess.run(["gh", "release", "create", f"v{version}", str(DIST / "inZOI-Multiplayer.exe"), str(zpath),
                    "--title", title.lstrip("#").strip(), "--notes", body.strip()], check=True, cwd=ROOT)
    print(f"published v{version}: every player's program offers it within a couple of hours, or at its next start")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", type=Path, metavar="NOTES", help="also create the GitHub release, with these notes")
    a = ap.parse_args()
    v, z = main()
    if a.publish:
        publish(v, z, a.publish)
