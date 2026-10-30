"""A player's Zoi as data: read from the local game, sent over the network, rebuilt on the other side.

read_profile()   -> plain JSON-able dict (what goes to the server and to other players)
member_lua()     -> Lua literal of an FB1CustomizedCharacterInfo for the B2 controller's spawn op
"""
import hashlib
import json

from lua_data import Converter, Registry


def read_profile(call, entity_id: int) -> dict:
    # Names/gender/growth come from the official CLI: entity.get_character_info entity_id
    # (https://mod-docs.playinzoi.com/docs/modkit-docs/wiki/cli/entity/entity-get-character-info).
    # Traits, voice and the full look stay on entity.reflect: the CLI catalog (inZOI 0.11) has no getter for traits or
    # voice, and its appearance category (https://mod-docs.playinzoi.com/docs/modkit-docs/wiki/cli/appearance/) only
    # gets/sets single parts (hair, skin, garments, body sliders...), with no whole-look export/import. Rebuilding a
    # puppet needs the whole FB1CustomizedCharacterInfo, so the reflected struct is still the only complete source.
    info = call("entity.get_character_info", entity_id=entity_id)
    trait = call("entity.reflect", entity_id=entity_id, component="FB2TraitComponent")
    bio = call("entity.reflect", entity_id=entity_id, component="FB2BioComponent")
    look = call("entity.reflect", entity_id=entity_id, component="FB2AppearanceComponent")["appearanceSetting"]
    traits = trait.get("traitIdList") or []
    return {
        "first_name": info.get("first_name") or "",
        "last_name": info.get("last_name") or "",
        "gender": info.get("gender"),
        "growth_step": info.get("growth_step"),
        "personality": traits[0] if traits else "None",
        "characteristics": trait.get("characteristics") or [],
        "voice": bio.get("voice") or {},
        "gender_identity": bio.get("genderIdentitySetting") or {},
        "look": look,
    }


TRANSIENT_LOOK = ("activeStyleList", "baseStyleCategory")      # what's worn right now: synced live as the outfit
TRANSIENT_TOP = ("appearanceAge", "growthRatio")                 # creeps up every day; growth_step covers real change


def profile_hash(profile: dict) -> str:
    """Fingerprint of what makes a Zoi look and act like itself; changes here mean the puppet must be rebuilt."""
    look = {k: v for k, v in profile["look"].items() if k not in TRANSIENT_TOP}
    look["outfit"] = {k: v for k, v in look.get("outfit", {}).items() if k not in TRANSIENT_LOOK}
    stable = {**profile, "look": look}
    return hashlib.sha1(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def member_lua(profile: dict, registry: Registry, first_name: str | None = None):
    """Returns (lua_literal, skipped_fields)."""
    member = {
        "firstName": first_name or profile["first_name"],
        "lastName": profile["last_name"],
        "personality": profile["personality"],
        "characteristics": profile["characteristics"],
        "voice": profile["voice"],
        "genderIdentitySetting": profile["gender_identity"],
        "appearanceSetting": profile["look"],
    }
    conv = Converter(registry)
    lua = conv.struct(member, "FB1CustomizedCharacterInfo", "Member")
    return lua, conv.skipped
