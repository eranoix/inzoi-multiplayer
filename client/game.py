"""Everything the companion asks the running game, through its built-in control server (http://127.0.0.1:11212/mcp)."""
import http.client
import json
import math
import os
import re
import subprocess
import threading
import time
import urllib.request

GAME_MCP = os.environ.get("INZOI_MP_GAME_URL") or "http://127.0.0.1:11212/mcp"     # (tests point it elsewhere)
MCP_PORT = 11212                                                                    # unless inZOI ran with -climcpport


# Interactions that are just getting somewhere: a puppet's own follow logic covers them.
MOVE_BAGS = ("ClickMove", "SelfMove", "Move_", "WalkTo", "Run_", "Sprint", "MoveRotate", "Rotate", "Self_GoHome",
             "GoHome", "GoTo")

_ESCAPE = re.compile(r'\\(["\\/bfnrtu]?)')


def loads(text: str):
    """json.loads, but forgiving one thing inZOI does: a lone backslash in a string (a Windows path in a lot or
    object name), which makes the whole answer unreadable. Lone ones are doubled and the answer read again."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        if "escape" not in e.msg:
            raise
        return json.loads(_ESCAPE.sub(lambda m: m.group(0) if m.group(1) else "\\\\", text))


class GameError(Exception):
    pass


class GameUnreachable(GameError):
    """Nothing answered at all (game closed, wrong port): no point trying another route right away."""


class CliUnavailable(GameError):
    """The CLI command isn't in this game build's catalog (cli.list), or INZOI_MP_USE_CLI=0."""


# The official CLI (inZOI 0.11+) is one catalog of documented commands, reachable from UI-mod JS (inzoi.cli.execute),
# Lua and this MCP server, where every command is its own tool (dots sent as underscores). Catalog and per-command
# pages: CLI_DOCS + "<category>/<category>-<command>" (underscores as dashes), e.g. CLI_DOCS + "entity/entity-get-possessed".
CLI_DOCS = "https://mod-docs.playinzoi.com/docs/modkit-docs/wiki/cli/"
CLI_RELIST = 30         # s between catalog attempts while it can't be read


def use_cli_flag() -> bool:
    """INZOI_MP_USE_CLI=0 forces the legacy (pre-CLI-catalog) paths everywhere."""
    return os.environ.get("INZOI_MP_USE_CLI", "1").strip().lower() not in ("0", "false", "no", "off")


def _tool(name) -> str:
    return str(name).strip().replace(".", "_")


def cli_names(listing) -> set:
    """Command names out of a cli.list answer, whatever its shape (list of names or of {name|id|command: ...},
    maybe nested under "commands"/"categories", or plain text), as MCP tool names (dots -> underscores)."""
    out = set()

    def walk(v, depth=0):
        if depth > 6:
            return
        if isinstance(v, str):
            out.update(_tool(m) for m in re.findall(r"\b[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\b", v))
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1)
        elif isinstance(v, dict):
            for k in ("name", "id", "command", "command_id"):
                if isinstance(v.get(k), str) and "." in v[k]:
                    out.add(_tool(v[k]))
            for k, x in v.items():
                if isinstance(x, (list, dict)) or k == "_text":
                    walk(x, depth + 1)
                elif isinstance(k, str) and re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", k):
                    out.add(_tool(k))            # {"entity.get_possessed": "description", ...}
    walk(listing)
    return out


def mcp_port_flag() -> int | None:
    """-climcpport=N from the running game's command line (None: not given, no game running, or not Windows)."""
    if os.name != "nt":
        return None
    q = "(Get-CimInstance Win32_Process -Filter \"Name like 'inZOI%'\").CommandLine"
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", q], capture_output=True,
                             text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"-climcpport[=\s]+(\d+)", out or "", re.I)
    return int(m.group(1)) if m else None


def describe_act(game, a: dict, me: int, owner_of, cache: dict) -> dict | None:
    """What other players need to copy an action: the interaction bag plus what it's done with.
    -> None (nothing worth copying: idle, walking, or the reply half of someone else's social)
       {"bag"}                         on its own (dance, phone...)
       {"bag", "player": id}           with another player's Zoi (owner_of maps puppet entity -> player id)
       {"bag", "obj", "ox", "oy", "oz"} with an object (found again by table id near that spot)
       {"bag", "npc": True}            with a character only this world has"""
    bag = a.get("bag")
    if not bag or bag == "None" or bag.startswith(MOVE_BAGS) or bag.endswith("_FeedBack") or bag == "Self_CantAction":
        return None
    d = {"bag": bag}
    t = a.get("target") or -1
    if t > 0 and t != me:
        pid = owner_of(t)
        if pid:
            d["player"] = pid
        else:
            if t not in cache:
                cache[t] = game.object_at(t)
            if cache[t]:
                d.update(cache[t])
            else:
                d["npc"] = True
    return d


class Game:
    def __init__(self, url: str | None = None):
        self.fixed = url or os.environ.get("INZOI_MP_GAME_URL")    # else the game's own port (game_url)
        self.url = self.fixed or GAME_MCP
        self._port_at = None            # when the game's command line was last looked at (None: not yet)
        self._port_lock = threading.Lock()
        self._templates = None          # (gender, growth) -> entity id of a local Zoi to build puppets on
        self.use_cli = use_cli_flag()
        self._cli = None                # names of the CLI commands this game build has (None: not read yet)
        self._cli_at = 0.0              # when the catalog was last tried (0: never)
        self._cli_lock = threading.Lock()
        self.saw_lobby = True           # set at the main menu; the companion clears it when a world is entered
        self.last_state = {}            # the latest game.get_state (world() reads it)

    # ------------------------------------------------------------------ transport
    def game_url(self, failed: bool = False) -> str:
        """The control server's address: INZOI_MP_GAME_URL, else 127.0.0.1 on the game's -climcpport (11212 if not
        given). The command line is read once (game calls run in threads, off the event loop), and again at most
        every 30 s while the game can't be reached (it may have started later, or restarted on another port)."""
        if self.fixed:
            return self.fixed
        with self._port_lock:
            if self._port_at is None or (failed and time.monotonic() - self._port_at > 30):
                self._port_at = time.monotonic()
                self.url = f"http://127.0.0.1:{mcp_port_flag() or MCP_PORT}/mcp"
        return self.url

    def _post(self, method: str, params: dict) -> str:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.game_url(), body, {"Content-Type": "application/json",
                                                             "Accept": "application/json, text/event-stream"})
        try:
            raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
        except (OSError, http.client.HTTPException) as e:      # (IncompleteRead, BadStatusLine... are HTTPExceptions)
            if isinstance(getattr(e, "reason", e), ConnectionRefusedError):     # (nothing on that port)
                self.game_url(failed=True)
                self._cli = None        # the game may come back as another build: read its catalog again
            raise GameUnreachable(f"inZOI isn't reachable ({e})") from None
        if raw.startswith(("event:", "data:")):
            raw = "\n".join(l[5:].strip() for l in raw.splitlines() if l.startswith("data:"))
        return raw

    def call(self, tool: str, **args):
        raw = self._post("tools/call", {"name": tool.replace(".", "_"), "arguments": args})
        try:
            res = loads(raw).get("result") or {}
            if res.get("structuredContent") is not None:
                out = res["structuredContent"]
            else:
                text = " ".join(c.get("text", "") for c in res.get("content") or [] if isinstance(c, dict))
                try:
                    out = loads(text)
                except ValueError:
                    out = {"_text": text}
        except (ValueError, KeyError, TypeError, AttributeError) as e:     # empty / half / odd answer (loading...)
            raise GameError(f"odd answer from inZOI to {tool}: {e!r}") from None
        if res.get("isError") or (isinstance(out, dict) and "_error" in out):
            raise GameError(str(out))
        return out

    # ------------------------------------------------------------------ official CLI catalog
    def cli_list(self, refresh: bool = False) -> set:
        """Names (as MCP tool names, dots -> underscores) of the CLI commands this game build has: cli.list
        (CLI_DOCS + "cli/cli-list") plus the server's tools/list. Read once and cached; while it can't be read (game
        not up yet), tried again at most every CLI_RELIST s. Always empty with INZOI_MP_USE_CLI=0."""
        if not self.use_cli:
            return set()
        with self._cli_lock:
            if self._cli is not None and not refresh:
                return self._cli
            if not refresh and self._cli_at and time.monotonic() - self._cli_at < CLI_RELIST:
                return set()
            self._cli_at = time.monotonic()
            names = set()
            try:
                names |= cli_names(self.call("cli.list"))
            except GameError:
                pass
            try:
                tools = (json.loads(self._post("tools/list", {})).get("result") or {}).get("tools") or []
                names |= {_tool(t["name"]) for t in tools if isinstance(t, dict) and t.get("name")}
            except (GameError, ValueError, AttributeError, TypeError):
                pass
            self._cli = names or None       # (nothing read: try again later)
            return names

    def has_cli(self, command: str) -> bool:
        return self.use_cli and _tool(command) in self.cli_list()

    def cli(self, command: str, **params):
        """Run an official CLI command (e.g. "environment.get_time"). Raises CliUnavailable when this build doesn't
        list it (or INZOI_MP_USE_CLI=0) and GameError when it fails: every caller keeps a legacy path for both."""
        if not self.has_cli(command):
            raise CliUnavailable(f"CLI command {command} not available")
        return self.call(command, **params)

    def _cli_or(self, legacy, attempt):
        """attempt() through the CLI; on a missing command, an error or an odd answer, legacy() (the pre-CLI code,
        unchanged). An unreachable game isn't retried the legacy way: that would only wait out a second timeout."""
        if self.use_cli:
            try:
                return attempt()
            except GameUnreachable:
                raise
            except (GameError, KeyError, TypeError, ValueError, AttributeError):
                pass
        return legacy()

    def lookup(self, struct: str) -> str:
        # Stays on lua_api_lookup (an MCP tool outside the CLI catalog): entity.schema (CLI_DOCS + "entity/entity-schema")
        # documents names/types/enum values but not the C++ container/struct types lua_data.Converter needs.
        r = self.call("lua_api_lookup", type=struct)
        return r.get("_text", "") if isinstance(r, dict) else str(r)

    # ------------------------------------------------------------------ queries
    def in_world(self) -> bool:
        return self.world() != "out"

    def world(self) -> str:
        """'play' (the world is running), 'paused' (in the world, but held by build mode, menus, the map...),
        'stopped' (in the world, clock paused with the time controls), 'out'."""
        try:
            s = self.call("game.get_state")
        except GameError:
            return "out"
        self.last_state = s
        if s.get("is_lobby"):
            self.saw_lobby = True               # the main menu: whatever loads next may be another save
        if s.get("phase") not in ("in_game", "map_view") or (s.get("possessed_entity_id") or -1) <= 0:
            return "out"
        if s.get("phase") != "in_game" or (s.get("blocking") or {}).get("pause_reasons"):
            return "paused"
        try:
            if self.call("environment.get_simulation").get("is_paused"):
                return "stopped"                # the time controls are on pause: the mod's per-frame code stops too
        except GameError:
            pass
        return "play"

    def saves(self, limit: int = 10, autosave: bool = True, refresh: bool = False) -> list[dict]:
        """Save slots, newest first: {folder, file, slot, first_name, last_name, map, saved_time, is_broken, ...}."""
        r = self.call("game.list_saves", limit=limit, include_autosave=autosave, refresh=refresh)
        _, _, body = str(r.get("_text", "") if isinstance(r, dict) else r).partition("\n")
        return json.loads(body) if body.strip().startswith("[") else []

    def possessed(self) -> int:
        # CLI: entity.get_possessed, no params -> entity_id (CLI_DOCS + "entity/entity-get-possessed"); on a build
        # without it, game.get_state's possessed_entity_id (CLI_DOCS + "game/").
        def attempt():
            if self.has_cli("entity.get_possessed"):
                eid = int(self.cli("entity.get_possessed")["entity_id"])
            else:
                eid = int(self.cli("game.get_state")["possessed_entity_id"])
            if eid <= 0:
                raise ValueError(eid)
            return eid
        return self._cli_or(self._possessed_legacy, attempt)

    def _possessed_legacy(self) -> int:
        try:
            return int(self.call("entity.get_possessed")["entity_id"])
        except (KeyError, TypeError, ValueError) as e:
            raise GameError(f"no possessed Zoi ({e!r})") from None

    def clock(self) -> tuple:
        """(hour, minute, speed, paused) of the game's clock."""
        # CLI: environment.get_time -> hour, minute, day (CLI_DOCS + "environment/environment-get-time"). Speed and
        # pause are documented on environment.get_simulation (CLI_DOCS + "environment/environment-get-simulation"):
        # read from there when get_time doesn't carry them.
        def attempt():
            c = self.cli("environment.get_time")
            h, m = int(c["hour"]), int(c["minute"])
            speed, paused = c.get("time_scale"), c.get("paused")
            if (speed is None or paused is None) and self.has_cli("environment.get_simulation"):
                sim = self.cli("environment.get_simulation")
                if speed is None:
                    speed = next((sim[k] for k in ("time_scale", "time_speed", "speed") if k in sim), None)
                if paused is None:
                    paused = next((sim[k] for k in ("paused", "is_paused") if k in sim), None)
            return h, m, 1 if speed is None else speed, bool(paused)
        return self._cli_or(self._clock_legacy, attempt)

    def _clock_legacy(self) -> tuple:
        c = self.call("environment.get_time")
        try:
            return int(c["hour"]), int(c["minute"]), c.get("time_scale", 1), bool(c.get("paused"))
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise GameError(f"odd clock ({e!r})") from None

    def state_of(self, entity_id: int) -> dict:
        """Position, facing and whereabouts of a character: what other players need to mirror it."""
        r = self.call("entity.reflect", entity_id=entity_id)
        try:
            w = r["worldTransform"]
            t, q = w["translation"], w["rotation"]
            yaw = math.degrees(math.atan2(2 * (q["w"] * q["z"] + q["x"] * q["y"]), 1 - 2 * (q["y"] ** 2 + q["z"] ** 2)))
            return {"x": round(t["x"], 1), "y": round(t["y"], 1), "z": round(t["z"], 1), "yaw": round(yaw, 1),
                    "city": r.get("locatedCityName") or "", "site": (r.get("locatedSiteId") or {}).get("value", -1)}
        except (KeyError, TypeError, AttributeError) as e:          # (mid-loading or teleport: no transform yet)
            raise GameError(f"no transform for {entity_id} ({e!r})") from None

    def activity(self, entity_id: int) -> dict:
        """What a character is doing right now: interaction bag, its state/action type, target, and the motion."""
        cur = self.call("interaction.get_current", entity_id=entity_id)
        out = {"bag": cur.get("interaction_bag_id") if cur.get("playing") else None,
               "action": cur.get("action_type"), "target": cur.get("target_entity_id") or -1}
        try:
            m = self.call("motion.get_current", entity_id=entity_id)
            ms = [x for x in (m.get("motions") or []) if isinstance(x, dict) and x.get("anim_id")]
            mo = ms[0] if ms else None
            out["anim"] = mo and {"id": mo["anim_id"], "pos": mo.get("position", 0)}
            # every montage playing (body posture, arms, face...): what the other game plays on this Zoi's copy
            out["motions"] = [{"id": x["anim_id"], "g": x.get("group") or "", "pos": round(float(x.get("position") or 0), 2),
                               "len": round(float(x.get("total_length") or 0), 2)} for x in ms]
        except (GameError, KeyError, TypeError, AttributeError, ValueError):
            out["anim"], out["motions"] = None, []
        return out

    def motion_sections(self, anim: str, entity_id: int, cache: dict) -> list:
        """[(name, start, is_loop)] of a montage (cached per animation)."""
        if anim not in cache:
            try:
                info = self.call("motion.get_info", anim_id=anim, entity_id=entity_id) or {}
                cache[anim] = [(s.get("name") or "Default", float(s.get("start_time") or 0), bool(s.get("is_loop")))
                               for s in info.get("sections") or []] or [("Default", 0.0, False)]
            except (GameError, TypeError, ValueError):
                cache[anim] = [("Default", 0.0, False)]
        return cache[anim]

    def object_at(self, entity_id: int) -> dict | None:
        """Table id + world position of a game object, or None if it isn't one (a character, nothing)."""
        try:
            info = self.call("entity.get_object_info", entity_id=entity_id)
            t = self.call("entity.reflect", entity_id=entity_id)["worldTransform"]["translation"]
        except (GameError, KeyError):
            return None
        table = info.get("object_data_table_id")
        return table and {"obj": table, "ox": round(t["x"], 1), "oy": round(t["y"], 1), "oz": round(t["z"], 1)}

    def outfit(self, entity_id: int) -> str | None:
        """The outfit category a character is wearing now (situational overlays, like gym clothes, win)."""
        r = self.call("appearance.get_outfit_category", entity_id=entity_id)
        overlays = [o["category"] for o in r.get("active_overlays") or [] if o.get("category") not in (None, "None")]
        base = r.get("base_category")
        return overlays[-1] if overlays else (base if base not in (None, "None") else None)

    def bubble(self, entity_id: int, text: str, seconds: float = 6):
        self.call("entity.show_speech_bubble", entity_id=entity_id, text=text[:120], duration=seconds)

    def go_to(self, entity_id: int):
        """Teleport the player's Zoi next to another character."""
        # CLI: movement.teleport_to entity_id (required) (CLI_DOCS + "movement/movement-teleport-to")
        self._cli_or(lambda: self.call("movement.teleport_to", entity_id=entity_id),
                     lambda: self.cli("movement.teleport_to", entity_id=int(entity_id)))

    # ------------------------------------------------------------------ environment (season, weather, time)
    def environment(self) -> dict:
        """{"season", "weather", ...} of the world now."""
        # CLI: environment.get_state, no params -> time, season, weather... (CLI_DOCS + "environment/environment-get-state")
        return self._cli_or(lambda: self.call("environment.get_state"), lambda: self.cli("environment.get_state"))

    def set_time_speed(self, speed: float):
        # CLI: environment.time_speed speed (float, required, 0-30) (CLI_DOCS + "environment/environment-time-speed")
        self._cli_or(lambda: self.call("environment.time_speed", speed=speed),
                     lambda: self.cli("environment.time_speed", speed=max(0.0, min(30.0, float(speed)))))

    def time_skip(self, minutes: int):
        # CLI: environment.time_skip days/hours/minutes (int, optional) (CLI_DOCS + "environment/environment-time-skip")
        self._cli_or(lambda: self.call("environment.time_skip", minutes=minutes),
                     lambda: self.cli("environment.time_skip", minutes=int(minutes)))

    def change_season(self, season: str):
        # CLI: environment.change_season season (string, required) (CLI_DOCS + "environment/environment-change-season")
        self._cli_or(lambda: self.call("environment.change_season", season=season),
                     lambda: self.cli("environment.change_season", season=str(season)))

    def override_weather(self, weather: str, step: int = 0):
        # CLI: environment.override_weather weather (name), step (int), both required
        # (CLI_DOCS + "environment/environment-override-weather")
        self._cli_or(lambda: self.call("environment.override_weather", weather=weather, step=step),
                     lambda: self.cli("environment.override_weather", weather=str(weather), step=int(step)))

    def template_for(self, gender: str, growth: str, exclude=()) -> int | None:
        """A local Zoi with the same gender and age: puppets borrow its body meshes and skeleton."""
        if self._templates is None:
            self._templates = {}
            for c in self.call("world.list_characters", limit=1000).get("characters", []):
                self._templates.setdefault((c.get("gender"), c.get("age_group")), []).append(c["entity_id"])
        for eid in self._templates.get((gender, growth), []):
            if eid not in exclude:
                return eid
        return None

    def home_site(self, entity_id: int) -> int | None:
        """Entity id of the lot a character lives on (None if homeless)."""
        hid = self.call("entity.get_character_info", entity_id=entity_id).get("home_site_id")
        if not hid or hid == "None":
            return None
        for s in self.call("world.get_loaded_sites").get("sites", []):
            if s.get("id") == hid:
                return s.get("entity_id")
        return None

    def site_name(self, site_entity: int) -> str:
        for s in self.call("world.get_loaded_sites").get("sites", []):
            if s.get("entity_id") == site_entity:
                return s.get("l10n") or s.get("id") or ""
        return ""

    def home_entry(self, entity_id: int) -> dict | None:
        """Front door + lot of a character's home, so visiting puppets can be let in like guests. The door can only
        be found while the character is at home (build.scan_objects scans the current lot): None until then."""
        site = self.home_site(entity_id)
        if site is None or self.state_of(entity_id)["site"] != site:
            return None
        try:
            objs = (self.call("build.scan_objects", table_id_prefix="Door", limit=0).get("objects", []))
        except GameError:
            return None
        doors = [o for o in objs if str(o.get("table_id", "")).startswith("Door") and "Bell" not in o["table_id"]]
        bells = [o for o in objs if "DoorBell" in str(o.get("table_id", ""))]
        if not doors:
            return None

        def d(a, b):
            la, lb = a["location"], b["location"]
            return math.hypot(la["x"] - lb["x"], la["y"] - lb["y"])
        front = min(doors, key=lambda o: min((d(o, b) for b in bells), default=0))
        return {"door": front["entity_id"], "site": site}
