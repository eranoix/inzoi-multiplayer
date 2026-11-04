"""Installing the mod (every update does) keeps the panel's own state: where it was left and its size
(ui/mp/saved/window.cfg); files a newer version dropped still go.

    python tools/test_install.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["INZOI_MP_DOCUMENTS"] = str(Path(tmp) / "Documents")
        import install
        install.INSTALL_DIR = Path(tmp) / "Mods" / "InZOIMultiplayer"
        install.MAILBOX_DIR = install.INSTALL_DIR / "lua" / "B2"
        assert install.install() is True
        saved = install.INSTALL_DIR / "ui" / "mp" / "saved" / "window.cfg"
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text('{"x":1734,"y":15,"minimized":false}', "utf-8")
        old = install.INSTALL_DIR / "ui" / "mp" / "old_panel.js"
        old.write_text("x", "utf-8")
        install.install()
        assert saved.exists() and "1734" in saved.read_text("utf-8"), "the panel's place is kept"
        assert not old.exists(), "a dropped file still goes"
    print("install tests passed")


# Unreal types the game's mod check is known to accept ("accessed via UE" must be in its ue_global_table.json, else the
# game refuses the whole mod when it starts: EBoneSpaces was refused). A new one needs checking in the game first.
# (The game's own list ships as BlueClient/Content/Data/Lua/luals-defs/globals/UE.lua - EBoneSpaces isn't in it;
# UUserWidget, UWidgetBlueprintLibrary and UActorComponent are: the family portraits, 0.9.81-0.9.83.)
UE_ALLOWED = {"FVector", "FRotator", "FTransform", "FQuat", "TArray", "AActor", "UGameplayStatics",
              "USkinnedMeshComponent", "UPoseableMeshComponent", "UUserWidget", "UWidgetBlueprintLibrary",
              "UActorComponent"}


def ue_types_allowed():
    import re
    lua = Path(__file__).resolve().parents[1] / "mod" / "InZOIMultiplayer" / "lua"
    bad = []
    for f in lua.rglob("*.lua"):
        for n, line in enumerate(f.read_text("utf-8").splitlines(), 1):
            code = line.split("--", 1)[0]
            for t in re.findall(r"\bUE\s*(?:\.\s*|\[\s*[\"'])([A-Za-z_][A-Za-z0-9_]*)", code):
                if t not in UE_ALLOWED:
                    bad.append(f"{f.relative_to(lua)}:{n}: UE.{t}")
    assert not bad, "Unreal types the game's mod check may refuse:\n" + "\n".join(bad)
    print("Unreal types in the mod: all known to pass the game's mod check")


if __name__ == "__main__":
    main()
    ue_types_allowed()
