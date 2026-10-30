"""Turn the game's reflected JSON (entity.reflect) into a typed Lua table literal that B2 Lua can rebuild
into real engine structs (see build() in the puppet controller).

Type information comes from the game's own Lua binding metadata (lua_api_lookup), cached per user in
%APPDATA%/inzoi-mp/struct_cache.json (seeded from client/struct_cache.json, which ships with the companion). Reflected JSON uses camelCase names; the real properties are PascalCase, and
names are matched case-insensitively (UE property names and FNames are case-insensitive).

Encoding consumed by the Lua builder:
    struct   {__s = "FB1HairDescription", Field = <value>, ...}
    array    {__a = "<element ctype>", i = {<value>, ...}}
    map      {__m = "<value ctype>", __k = "<key ctype>", i = {{<key>, <value>}, ...}}
    enum     {__e = "EB1GenderType", v = "Male"}            (resolved by name, case-insensitively, in Lua)
    scalars  plain Lua numbers / strings / booleans
TOptional and TSet values are skipped when empty; non-empty optionals are emitted as {__o = <value>}.
"""
import json
import os
import re
import sys
from pathlib import Path

SEED = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "struct_cache.json"
CACHE = Path(os.environ.get("APPDATA", Path.home())) / "inzoi-mp" / "struct_cache.json"

SCALARS_NUM = {"float", "double", "int32", "int64", "uint8", "uint16", "uint32", "int8", "int16", "uint64", "int"}
# Engine (UE) structs aren't in the game's Lua binding metadata.
ENGINE_STRUCTS = {
    "FLinearColor": {"R": "float", "G": "float", "B": "float", "A": "float"},
    "FColor": {"R": "uint8", "G": "uint8", "B": "uint8", "A": "uint8"},
    "FVector": {"X": "double", "Y": "double", "Z": "double"},
    "FVector2D": {"X": "double", "Y": "double"},
    "FVector4": {"X": "double", "Y": "double", "Z": "double", "W": "double"},
    "FRotator": {"Pitch": "double", "Yaw": "double", "Roll": "double"},
    "FQuat": {"X": "double", "Y": "double", "Z": "double", "W": "double"},
    "FDateTime": {"Ticks": "int64"},
    "FTimespan": {"Ticks": "int64"},
}
SCALARS_STR = {"FName", "FString", "FText", "FUtf8String"}


def is_soft(ctype):
    return ctype.startswith(("TSoftObjectPtr", "TSoftClassPtr", "FSoftObjectPath", "FSoftClassPath"))


def split_args(s):
    """'FName,TMap<FName,float>' -> ['FName', 'TMap<FName,float>']"""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def generic(ctype):
    m = re.fullmatch(r"(\w+)<(.*)>", ctype.strip())
    return (m.group(1), split_args(m.group(2))) if m else (ctype.strip(), [])


class Registry:
    """Struct name -> {lowercased field name: (RealName, ctype)} using lua_api_lookup, cached on disk."""

    def __init__(self, lookup):
        self.lookup = lookup
        self.cache = {}
        for f in (SEED, CACHE):                 # the user's own cache wins (it reflects their game version)
            try:
                self.cache.update({k: v for k, v in json.loads(f.read_text("utf-8")).items() if v})  # (no empties)
            except (OSError, ValueError, AttributeError):
                pass

    def fields(self, struct):
        if struct in ENGINE_STRUCTS:
            return {k.lower(): [k, t] for k, t in ENGINE_STRUCTS[struct].items()}
        if struct not in self.cache:
            text = self.lookup(struct)
            props = {}
            if "Properties (" in text:
                body = text.split("Properties (", 1)[1].split(")", 1)[1]
                for line in body.splitlines():
                    line = line.strip()
                    if line.startswith(("Functions (", "Override points")):
                        break
                    m = re.match(r"(\w+)\s*:\s*(.+)$", line)
                    if m:
                        props[m.group(1).lower()] = [m.group(1), m.group(2).strip()]
            if not props:                       # (a loading screen answers empty): ask again next time, never keep it
                return props
            self.cache[struct] = props
            try:
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                tmp = CACHE.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.cache, indent=0), "utf-8")
                os.replace(tmp, CACHE)
            except OSError:
                pass                            # (only a cache: it's written again with the next struct)
        return self.cache[struct]


def lua_str(s):
    return json.dumps(str(s), ensure_ascii=False)


class Converter:
    def __init__(self, registry):
        self.reg = registry
        self.skipped = []

    def value(self, v, ctype, path):
        base, args = generic(ctype.replace("const ", "").replace("&", "").replace("*", "").strip())
        if base in ("TArray",):
            items = [self.value(x, args[0], f"{path}[{i}]") for i, x in enumerate(v or [])]
            items = [x for x in items if x is not None]
            return "{__a = %s, i = {%s}}" % (lua_str(args[0]), ", ".join(items))
        if base == "TMap" and is_soft(args[1]):
            return None            # asset references can't be set from Lua; the template already carries them
        if base == "TMap":
            pairs = []
            for k, x in (v or {}).items():
                kv = self.key(k, args[0])
                xv = self.value(x, args[1], f"{path}.{k}")
                if kv is not None and xv is not None:
                    pairs.append("{%s, %s}" % (kv, xv))
            return "{__m = %s, __k = %s, i = {%s}}" % (lua_str(args[1]), lua_str(args[0]), ", ".join(pairs))
        if base == "TSet":
            items = [self.value(x, args[0], f"{path}[{i}]") for i, x in enumerate(v or [])]
            return "{__set = %s, i = {%s}}" % (lua_str(args[0]), ", ".join(x for x in items if x is not None))
        if base == "TOptional":
            if v in (None, "()", "", {}):
                return None
            inner = self.value(v, args[0], path)
            return None if inner is None else "{__o = %s}" % inner
        if base in SCALARS_NUM:
            return repr(float(v)) if base in ("float", "double") else str(int(v))
        if base == "bool":
            return "true" if v else "false"
        if is_soft(base):
            return None
        if base in SCALARS_STR:
            return lua_str(v if isinstance(v, str) else "")
        if base.startswith("E"):
            return "{__e = %s, v = %s}" % (lua_str(base), lua_str(v))
        if base.startswith("F") and isinstance(v, dict):
            return self.struct(v, base, path)
        self.skipped.append(f"{path} ({ctype})")
        return None

    def key(self, k, ctype):
        base, _ = generic(ctype)
        if base.startswith("E"):
            return "{__e = %s, v = %s}" % (lua_str(base), lua_str(k))
        if base in SCALARS_NUM:
            return str(int(k)) if base != "float" else repr(float(k))
        return lua_str(k)

    def struct(self, obj, sname, path=""):
        fields = self.reg.fields(sname)
        parts = ["__s = %s" % lua_str(sname)]
        for k, v in obj.items():
            if k.startswith("_"):
                continue
            f = fields.get(k.lower())
            if not f:
                self.skipped.append(f"{path}.{k} (no field on {sname})")
                continue
            real, ctype = f
            lv = self.value(v, ctype, f"{path}.{real}")
            if lv is not None:
                parts.append(f"{real} = {lv}")
        return "{" + ", ".join(parts) + "}"
