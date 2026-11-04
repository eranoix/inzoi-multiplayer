"""The puppet controller (mod/InZOIMultiplayer/lua/B2/Main.lua) lets a partner's Zoi answer a social done to it here
(answering): the receiving half the game gives it ("..._FeedBack"), the turn to face first (MoveRotate), couple socials
(Co_, Coop, Social). Everything else it starts on its own is still stopped (eating, the toilet, wandering off...).
Seen before: every one of those answers was cancelled as "its own doing", so no social between the players worked.

    python tools/test_b2_social.py
"""
import re
import sys
from pathlib import Path

try:
    from lupa import LuaRuntime
except ImportError:
    print("B2 social tests: lupa isn't installed (pip install lupa): skipped")
    sys.exit(0)

MAIN = Path(__file__).resolve().parents[1] / "mod" / "InZOIMultiplayer" / "lua" / "B2" / "Main.lua"


def main():
    src = MAIN.read_text("utf-8")
    m = re.search(r"^local function answering\(bag\)\n.*?^end\n", src, re.M | re.S)
    assert m, "answering() not found in B2/Main.lua"
    lua = LuaRuntime()
    answering = lua.execute(m.group(0).replace("local function answering", "function answering") +
                            "\nreturn answering")
    # (the receiving halves cancelled in one evening, from both PCs' logs)
    for bag in ("Friendship_G10_2_FeedBack", "friendship_R09_FeedBack", "Skill_Logic_03_Feedback",
                "Friend_TakePic_MaleToFemale_Friendship_FeedBack", "HandShake_MaleToFemale_Business_FeedBack",
                "Dance_Together_MaleToFemale_Friendship_FeedBack", "HighFIve_FemaleToMale_Friendship_FeedBack",
                "Business_G04_Start4_FeedBack", "MoveRotate", "Co_DeepKiss_MaleToFemale_Success", "Coop_Dance",
                "Social_Talk"):
        assert answering(bag) is True, f"{bag}: stopped as the Zoi's own doing"
    for bag in ("Toilet01_Use", "Eat_Food", "SelfMove", "Stand", "Object_Bed_Sleep_Start", "Computer01_Play", None):
        assert answering(bag) is False, f"{bag}: let through"
    print("a partner's Zoi answers the socials done to it here; what it starts on its own is still stopped: ok")

    # the controller: answering keeps the stop away, and the skeleton doesn't force its place meanwhile
    assert "not p.answering then" in src and "not p.hidden and not p.answering and #p.trail > 0" in src
    print("B2 social tests passed")


main()
