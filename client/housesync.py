"""The family home, the same in both saves (mixed into the Companion, like couple.Couple).

Each player plays their own save, so what one of them builds or rearranges in the shared home only exists in their
save. This keeps the two in step: the walls (segments per floor, and each storey's wall height) and the objects
(furniture, decoration, doors and windows). Wall and floor paint/material can't be read through the game's commands
(only written): the exact house (below) carries those.

Reading and comparing:
- The game's commands read the lot: build.scan_objects (table id, site-relative position, yaw, floor), build.scan_walls
  (segment ends per floor), build.scan_floors (storey heights). Outside build mode they read the lot my Zoi is on, so
  the home is read there only while my Zoi is on it; the last reading is kept (home-lot.json) for when it's out.
- Objects of the same kind within 10 cm and 5 degrees are the same object. A wall is there when every point along it
  lies on a wall of the other reading (one long wall and the two short ones that make it up are the same).

The server keeps the home's version for both (shared.py: house:<lot>:live, versioned, compare-and-set):
- What I change goes up as a change, never as my whole lot over theirs. My home as build mode opened on it (the last
  reading of it in this world session; without one, the first reading in build mode) is compared with my home as
  build mode closes; the difference (objects added and removed - a removed and an added one of the same kind are a
  move -, wall pieces built and knocked down, storey heights) is put on top of the server's newest version and sent
  with that version as the base. If the world goes while build mode is open (the game crashes, or goes to the menu from
  build mode), the session ended unseen: its change is only what its own readings show, because my game afterwards may
  hold an older save, whose differences aren't changes I made (nor is an exact house saved of it). If another version got there first the server refuses it, and the change is put on
  top of that one instead and sent again. Where we both changed the same thing the newer version (my partner's) wins:
  an object I moved that they had already moved or removed stays as they left it (my move is dropped, with a chat
  line), a storey height we both changed stays theirs. The rest of my change is kept. The value sent also lists what
  that change removed ("change"), and what all the changes since the house's first version on this server took out of
  it ("history": what's back in it isn't listed; the newest MAX_HISTORY, "complete" while none was dropped), for a PC
  that has no base (below), and the ids of the changes folded into the house ("applied", the newest APPLIED_KEEP,
  carried along by every version): a change of mine that got there with its word lost is known by it, and isn't sent
  again - not even after my partner took back out what it added -, and my game is brought to what came after it
  (confirm_landed).
- Things that appear in my home in play mode (bought, gifts, deliveries) are found by the reading made every few
  minutes while my Zoi is home, and go up the same way; while the server has no version of the home at all (a new
  one, or one whose shared state started over), that reading sends my home whole, as its first version.
  Only additions: something gone from my home in play mode (sold,
  or a save loaded from before) isn't sent as removed, so a rolled-back save never deletes my partner's things. Nor
  does it bring back what was deleted: what was taken out of my home on purpose (by an apply - my partner's removals
  and moves -, or by me in build mode) is remembered ("gone", home-lot-sync.json). When the base's reading (below) may
  be older than the save my game holds (it wasn't made since that save was loaded - a loading screen that keeps the
  save isn't a load; a save loaded from the main menu, the game restarted, this program started, the exact house
  applied may be), the first reading of the home brings my game to the server's version again (what that save lacks
  comes back) and becomes the base; what my game has beyond the server's then goes up as bought unless it's one of
  those taken out before: that came back with an older save, and stays only in my game ("local_only", said in the
  chat), never sent - nor in my exact house: that isn't saved while my home has such a thing (said once in the chat).
- My changes are kept until the server confirmed them (home-lot-sync.json, cfg["house_dirty"]): made while it can't be
  reached, refused, lost with the connection - they're sent again, on top of whatever version is newest by then.
- A server version is brought into my game without asking, as a three-way merge with the base: the version my game was
  last brought to (its contents, and my lot as it was read right then; home-lot-sync.json, valid only while it's the
  version counted as seen on this server - another server or a started-over one has no base):
    placed:  what the new version has and my game hasn't, if it's new since the base (my partner added it) or was in my
             game at the base and is gone now (brought back);
    removed: only my objects that the base had and the new version hasn't: what my partner deliberately removed;
    kept:    everything else in my game. What the server never knew about (bought in play mode, my changes not sent
             yet) is never removed, and is sent up right after - except a move of mine not sent yet of something that
             version moved or removed (as my push would find: fold): my copy goes, theirs is placed, and my move no
             longer waits (said once).
  Walls the same way (knocked down only where the base had them and the new version doesn't), storey heights only
  where my partner changed them since the base. Without a base (the first time, another server, a new one or one whose
  shared state started over): what's missing is placed, and only what that version lists as taken out of the house
  (its "history": my partner's removals and moves - the spot moved from - since its first version, and its own
  change's) is removed; what my game has beyond it (bought before the server had the house...) goes up, so the house
  is both - but for what had been taken out of the home on purpose before (as below: kept in my game only). That needs
  the history whole: without it (a version made by an older program, or more taken out than it lists), what my game
  has beyond the house may be what my partner moved or took out before I had any base - sent up, it would come back
  into both games for good -, so it stays in my game only (said in the chat).
  After my partner's exact house was applied (below: the lot replaced as a whole by their property, saved before the
  live house as it is now), what it brought back that had been taken out of the home on purpose since it was saved (a
  removal or move of mine, or theirs, made while it waited for the game's start) goes again, and storey heights are
  brought to the version's: the live house is newer.
- One apply at a time, always of the newest version (an older one still waiting is dropped, and one a newer version
  overtakes while build mode opens for it is left with nothing done: the newest comes next), and never while I'm in
  build mode, or the exact house is being saved, or another save is loaded: it waits, and when I leave build mode what
  I built goes up first, on top of it, so my build-mode edits are never undone or lost. What an apply works out once
  under way (after the walls) is against the base and my waiting changes as they were when it began: my change that
  lands meanwhile is folded into the base (confirm_landed), and against that the version being put in would read as
  taking it back out. A version counts as brought in
  (cfg["shared_seen"], which never goes down) only once it's applied.
- Only the home's key changes the home (a house:<other lot>:live is left alone).
- What the game won't take (an object it refuses to place, a wall it won't build) is tried again at most MAX_TRIES
  times in all, and only along with other changes (never build mode just for it); then it's left as it is and said once
  in the chat. An apply that fails as a whole (build mode doesn't open...) is tried MAX_TRIES times, then left until a
  newer version (said once). Getting home doesn't apply anything again; the panel's "get my partner's house" compares
  and asks before bringing back what's missing (and tries again what was left).
- Applying enters build mode on that lot (build.enter_mode, the game pauses), removes the objects that go, deletes and
  builds walls (building.construct_wall, is_delete for deleting), sets storey heights, reads the lot again and places
  what's still missing (building.place_object; doors and windows snap onto the new walls), and leaves build mode (which
  saves the lot). Leaving build mode after applying doesn't send anything back - except what the player changed in
  that build mode while it was open on their screen (the reading at the end, less what the apply did): it goes up as a
  change of theirs (apply_edits).

Without the server's shared state (an older server), the partner's program is asked for its home on getting home and
sends it as it is ({"t":"event","kind":"couple","data":{"type":"house","site","site_id","objects","walls","heights"}}),
and a popup asks "<name> changed the house: N new, M removed - apply?" (or "always yes": key "house").

The exact house (lotsync.py): what the game's commands can't read (paint, materials, floors, roofs, stairs, object
customization) goes as the game's own saved property. After leaving build mode on the home lot, the lot is saved as a
property (build mode reopens for a few seconds) and sent (house:<lot>:exact), on the version my game really holds
(exact_held: my own once confirmed, a partner's once applied - not one merely heard of or kept for the next start): a
property can't be merged, so if my partner's newer one got there first theirs is kept and mine dropped, with a chat
line. It's saved only once it can't carry anything wrong into my partner's game (kick_exact): not while my partner's
newer one waits for my game's next start (mine wouldn't have it: theirs wins, said once), nor while the home has
something kept only in my game (above, said once), nor before my game is known to hold the shared house as it is (a
version of it waiting to come in, my change to it on its way up, a save loaded since - it's brought there first). My
changes the server hasn't confirmed (made while it can't be reached) don't hold it back: the save notes the house my
game has with them (the server's version with them put on it), so a session that changed objects or walls offline has
its exact house saved even if the game is closed before the server is back. That's looked at again once the save is
done (a version of my partner's that came during those seconds isn't in it), and before each upload: a save is sent
only while the live house is still the one it was saved on (that version, or the house it noted once my changes are on
the server and nothing else came in; else - my partner's change came meanwhile, or while the server couldn't be
reached - it's saved again once that is in my game). It's megabytes, so one goes up at a time: one asked for
meanwhile waits, then the newest saved one goes
(and one the server has already isn't sent again); one the server didn't confirm (no word, or it couldn't keep it)
goes again only after PUSH_WAIT, longer each time (a newer save goes at once). The partner, once they said yes to that
house, writes it as a property and the game applies it the next time it starts (the game reads properties only when
it starts: tried once per run of the game), or right away with "apply now" (saves and restarts the game); a newer one
that comes while it's being applied waits for the start after. Once mine, saved with theirs in it, is the server's
version, an older one of theirs still waiting for that is dropped, and while mine waits to go up, made on top of
theirs, theirs isn't applied.
The same house hashes the same in both saves, so an unchanged one isn't asked about again.
"""
import asyncio
import json
import logging
import math
import os
import secrets
import time
from pathlib import Path

import lotsync

log = logging.getLogger("housesync")

SCAN_EVERY = 4            # s between readings while in build mode
SAME_CM, SAME_DEG = 10.0, 5.0
MAX_OBJECTS = 2000
MAX_WALLS = 2000
ON_WALL_CM = 4.0          # a point this close to a wall's centre line is on it (centre lines sit on 25*k+12.5)
BESIDE_CM = 100.0         # an object this close to a wall stands in it or by it (apply_edits)
WALL_STEP = 12.5          # cm between the points checked along a wall
LOT_FILE = Path(os.environ.get("APPDATA", Path.home())) / "inzoi-mp" / "home-lot.json"
REFRESH_EVERY = 300       # s between readings of the home while my Zoi is there (things bought in play mode)
ASK_AGAIN = 120           # s at least between asking the partner again on getting home
PRESET_FILE = Path(os.environ.get("APPDATA", Path.home())) / "inzoi-mp" / "home-lot-preset.json"
LOT_ACCEPT_FOR = 600      # s after saying yes to a partner's house in which their exact house is taken without asking
MAX_TRIES = 2             # applies that may try something the game won't take, before it's left as it is
PUSH_TRIES = 4            # refusals in a row (another version got there first) merged on top of in one go
PUSH_WAIT = (5, 30, 60, 300)    # s before sending again after a change the server didn't confirm (while connected)
MAX_PENDING = 40          # changes waiting for the server (past this the two oldest are joined into one)
MAX_LEFTOVER = 200
MAX_GONE = 1000           # objects (and wall pieces) taken out of the home on purpose that are remembered (note_gone)
TICK_GAP = 20             # s without a house tick (every 2 s while in a world) that means the world was left meanwhile
APPLIED_KEEP = 64         # ids of the changes folded into a house version that it carries along ("applied")
MAX_HISTORY = 300         # objects (and wall pieces) taken out of the house that its versions list ("history")
OBJ_FIELDS = ("table_id", "x", "y", "z", "yaw", "floor")
KINDS = ("place", "remove", "build", "delete")
DELTA_PARTS = ("added", "removed", "walls_add", "walls_del", "heights")


def delta_ids(d: dict) -> list[str]:
    """A change's ids (add_pending gives each one; two joined into one - compose - carry both)."""
    ids = d.get("ids") if isinstance(d.get("ids"), list) else [d.get("id")]
    return [str(i) for i in ids if isinstance(i, str) and i]


def applied_ids(value) -> list[str]:
    """The ids of the changes a house version says it has folded in (house_value: "applied"; the newest last)."""
    a = value.get("applied") if isinstance(value, dict) else None
    return [i for i in a if isinstance(i, str) and i][-APPLIED_KEEP:] if isinstance(a, list) else []


def landed(d: dict, applied) -> bool:
    """Change d is in a server version whose "applied" is `applied` (a set): it got there, whatever its word."""
    ids = delta_ids(d)
    return bool(ids) and all(i in applied for i in ids)


def object_key(o: dict) -> dict:
    loc = o.get("location") or {}
    return {"table_id": str(o.get("table_id") or ""), "x": round(float(loc.get("x") or 0), 1),
            "y": round(float(loc.get("y") or 0), 1), "z": round(float(loc.get("z") or 0), 1),
            "yaw": round(float(o.get("yaw") or 0), 1), "floor": int(o.get("floor_level") or 0)}


def same(a: dict, b: dict) -> bool:
    d_yaw = abs((a["yaw"] - b["yaw"] + 180) % 360 - 180)
    return (a["table_id"] == b["table_id"] and a["floor"] == b["floor"] and d_yaw <= SAME_DEG
            and math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= SAME_CM and abs(a["z"] - b["z"]) <= SAME_CM)


def wall_key(w: dict) -> dict:
    a, b = w.get("left") or {}, w.get("right") or {}
    return {"floor": int(w.get("floor_level") or 0),
            "a": [round(float(a.get("x") or 0), 1), round(float(a.get("y") or 0), 1)],
            "b": [round(float(b.get("x") or 0), 1), round(float(b.get("y") or 0), 1)]}


def _near(p, w, cm: float = ON_WALL_CM) -> bool:
    (ax, ay), (bx, by) = w["a"], w["b"]
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    k = 0.0 if ll == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / ll))
    return math.hypot(p[0] - ax - dx * k, p[1] - ay - dy * k) <= cm


def _close(w: dict, walls: list[dict]) -> list[dict]:
    """The walls of `walls` on w's floor that come within ON_WALL_CM of it at all (the only ones a point of w can lie
    on: checking just those keeps comparing two lots of hundreds of walls quick)."""
    (ax, ay), (bx, by) = w["a"], w["b"]
    x0, x1 = min(ax, bx) - ON_WALL_CM, max(ax, bx) + ON_WALL_CM
    y0, y1 = min(ay, by) - ON_WALL_CM, max(ay, by) + ON_WALL_CM
    return [o for o in walls if o["floor"] == w["floor"] and min(o["a"][0], o["b"][0]) <= x1
            and max(o["a"][0], o["b"][0]) >= x0 and min(o["a"][1], o["b"][1]) <= y1 and max(o["a"][1], o["b"][1]) >= y0]


def covered(w: dict, walls: list[dict]) -> bool:
    """Every point along w (every WALL_STEP cm) lies on one of `walls` on the same floor."""
    same_floor = _close(w, walls)
    (ax, ay), (bx, by) = w["a"], w["b"]
    n = max(1, int(math.hypot(bx - ax, by - ay) / WALL_STEP))
    return all(any(_near((ax + (bx - ax) * i / n, ay + (by - ay) * i / n), o) for o in same_floor)
               for i in range(n + 1))


def subtract(w: dict, walls: list[dict]) -> list[dict]:
    """The parts of wall w that don't lie on any of `walls`: [w] itself if none of it does, [] if all of it does."""
    same_floor = _close(w, walls)
    if not same_floor:
        return [w]
    (ax, ay), (bx, by) = w["a"], w["b"]
    n = max(1, round(math.hypot(bx - ax, by - ay) / WALL_STEP))
    free = [not any(_near((ax + (bx - ax) * (i + 0.5) / n, ay + (by - ay) * (i + 0.5) / n), o) for o in same_floor)
            for i in range(n)]
    if all(free):
        return [w]

    def at(i):
        return [round(ax + (bx - ax) * i / n, 1), round(ay + (by - ay) * i / n, 1)]
    out, start = [], None
    for i, f in enumerate(free + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append({"floor": w["floor"], "a": at(start), "b": at(i)})
            start = None
    return out


def overlaps(w: dict, walls: list[dict]) -> bool:
    """Some of wall w lies on one of `walls`."""
    return subtract(w, walls) != [w]


def pieces(walls: list[dict], other: list[dict]) -> list[dict]:
    """The parts of `walls` that aren't on `other`."""
    return [p for w in walls for p in subtract(w, other)]


def wall_diff(theirs: list[dict], mine: list[dict]) -> tuple[list[dict], list[dict]]:
    """-> (to build: their walls missing here, to delete: my walls they don't have). A wall of mine only partly theirs
    is deleted whole; what of it they do have is then built again."""
    delete = [m for m in mine if not covered(m, theirs)]
    kept = [m for m in mine if m not in delete]
    return [t for t in theirs if not covered(t, kept)], delete


def wh(h) -> float:
    """A storey's wall height (0: unknown)."""
    try:
        return float((h or {}).get("wall_height") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def height_diff(theirs: dict, mine: dict, walls: list[dict]) -> list[tuple[int, float, list]]:
    """Storeys whose wall height differs: [(floor, their height, a point on one of their walls there)]."""
    out = []
    for f, h in (theirs or {}).items():
        mh = (mine or {}).get(str(f))
        w = next((w for w in walls if str(w["floor"]) == str(f)), None)
        if mh and w and wh(h) and abs(wh(h) - wh(mh)) > 1:
            out.append((int(f), wh(h), w["a"]))
    return out


def house_label(place, remove, walls: int) -> str:
    return f"{len(place)} new, {len(remove)} removed" + (f", {walls} walls" if walls else "")


def _payload(out) -> dict:
    """A scan's answer: JSON, or the game's "Truncated: ..." line followed by the JSON."""
    if isinstance(out, dict) and "_text" in out:
        text = out["_text"]
        return json.loads(text[text.index("{"):]) if "{" in text else {}
    return out or {}


def diff(theirs: list[dict], mine: list[dict]) -> tuple[list[dict], list[dict]]:
    """-> (to place: their objects missing here, to remove: my objects (with entity_id) they don't have). One for one
    (two chairs of theirs need two of mine), compared only with objects of the same kind on the same floor."""
    kinds: dict[tuple, list] = {}
    for m in mine:
        kinds.setdefault((m["table_id"], m["floor"]), []).append(m)
    place, used = [], set()
    for o in theirs:
        hit = next((m for m in kinds.get((o["table_id"], o["floor"]), ()) if id(m) not in used and same(o, m)), None)
        if hit is None:
            place.append(o)
        else:
            used.add(id(hit))
    return place, [m for m in mine if id(m) not in used]


def has(objs: list[dict], o: dict) -> bool:
    return any(same(o, m) for m in objs)


def matched(gone: list[dict], objs: list[dict]) -> list[dict]:
    """The objects of `objs` that match those in `gone`, one for one."""
    left, out = list(objs), []
    for g in gone:
        hit = next((m for m in left if same(g, m)), None)
        if hit is not None:
            left.remove(hit)
            out.append(hit)
    return out


def clean_obj(o) -> dict | None:
    """An object as it's kept and sent (no entity id), or None if it isn't one."""
    try:
        c = {"table_id": str(o["table_id"]), "x": float(o["x"]), "y": float(o["y"]), "z": float(o["z"]),
             "yaw": float(o["yaw"]), "floor": int(o["floor"])}
    except (KeyError, TypeError, ValueError):
        return None
    return c if c["table_id"] else None


def clean_wall(w) -> dict | None:
    try:
        return {"floor": int(w["floor"]), "a": [float(v) for v in w["a"][:2]], "b": [float(v) for v in w["b"][:2]]}
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def clean_lot(lot: dict) -> dict:
    """A reading or a house as it's kept: objects, walls, storey heights."""
    lot = lot if isinstance(lot, dict) else {}
    return {"objects": [c for c in map(clean_obj, (lot.get("objects") or [])[:MAX_OBJECTS]) if c],
            "walls": [c for c in map(clean_wall, (lot.get("walls") or [])[:MAX_WALLS]) if c],
            "heights": lot.get("heights") if isinstance(lot.get("heights"), dict) else {}}


def house_history(value) -> dict | None:
    """What a house version lists as taken out of the house since its first version on this server ("history": the
    objects and wall pieces its changes took out - a move's spot moved from too -, not what's back in it; "complete":
    none was dropped, MAX_HISTORY). None: it keeps none (made on top of a version an older program made)."""
    h = value.get("history") if isinstance(value, dict) else None
    if not isinstance(h, dict):
        return None
    return {"removed": clean_lot({"objects": h.get("removed")})["objects"],
            "walls_removed": clean_lot({"walls": h.get("walls_removed")})["walls"],
            "complete": h.get("complete") is True}


def grow_history(hist: dict | None, value: dict, removed: list, walls_removed: list) -> dict:
    """The history of a new house version `value`, made on top of one whose history is `hist` (None: it's the first
    version) by changes that took `removed` and `walls_removed` out: what's back in the house isn't listed, the newest
    MAX_HISTORY are kept - past that it isn't complete any more."""
    objs = (diff(hist["removed"], value["objects"])[0] if hist else []) + list(removed)
    walls = (pieces(hist["walls_removed"], value["walls"]) if hist and value["walls"] else
             list(hist["walls_removed"]) if hist else []) + list(walls_removed)
    complete = (hist is None or hist["complete"]) and len(objs) <= MAX_HISTORY and len(walls) <= MAX_HISTORY
    return {"removed": objs[-MAX_HISTORY:], "walls_removed": walls[-MAX_HISTORY:], "complete": complete}


def taken_out(house: dict) -> tuple[list, list]:
    """What a house version lists as taken out of it -> (objects, wall pieces): its history (house_history) and its own
    change's ("change"), each once."""
    ch = house.get("change") if isinstance(house.get("change"), dict) else {}
    h = house.get("history") if isinstance(house.get("history"), dict) else {}
    ho, hw = list(h.get("removed") or []), list(h.get("walls_removed") or [])
    return (ho + diff(ch.get("removed") or [], ho)[0],
            hw + [w for w in ch.get("walls_removed") or [] if not covered(w, hw)])


def plan_size(plan: dict) -> int:
    return sum(len(plan[k]) for k in KINDS) + len(plan["storeys"])


def session_delta(before: dict, after: dict) -> dict | None:
    """What changed between two readings of the home (build mode opening, and closing): {"added", "removed" (objects),
    "walls_add", "walls_del" (wall pieces), "heights" ({floor: {"before", "after"}})}. None: nothing did."""
    bo, ao = before.get("objects") or [], after.get("objects") or []
    if bo and not ao:                                    # (a reading that came back empty isn't "everything removed")
        log.warning("the home's last reading has no objects (it had %d): not taken as a change", len(bo))
        return None
    added, removed = diff(ao, bo)
    bw, aw = before.get("walls") or [], after.get("walls") or []
    walls_add, walls_del = (pieces(aw, bw), pieces(bw, aw)) if aw else ([], [])   # (no walls read: not "all gone")
    bh, ah = before.get("heights") or {}, after.get("heights") or {}
    heights = {str(f): {"before": bh.get(f), "after": h} for f, h in ah.items()
               if wh(h) and abs(wh(h) - wh(bh.get(f))) > 1}
    if not (added or removed or walls_add or walls_del or heights):
        return None
    return {"added": [c for c in map(clean_obj, added) if c], "removed": [c for c in map(clean_obj, removed) if c],
            "walls_add": walls_add, "walls_del": walls_del, "heights": heights, "at": time.time()}


def added_delta(objs: list[dict]) -> dict:
    return {"added": objs, "removed": [], "walls_add": [], "walls_del": [], "heights": {}, "at": time.time()}


def apply_edits(plan: dict, before: dict, after: dict) -> dict | None:
    """The player's own changes made in the build mode an apply had open (the game shows it on their screen for as long
    as the apply takes), as a session_delta: what the lot read at the end (`after`) has beyond `before` (the lot as the
    apply had left it before placing: what it removed gone, its walls and storeys done) and what the apply placed.
    What it placed a little off (a door snapped onto its wall: the nearest of that kind), couldn't place or couldn't
    remove isn't counted; nor walls or storey heights one of the readings lacks; nor something gone from beside a wall
    the apply built or knocked down (a door in it, or something in its way, that the game took away a moment after the
    reading made right after the walls: sent as removed, it would go from my partner's game too). None: nothing."""
    placed = [c for c in map(clean_obj, plan.get("place") or []) if c]
    walls = list(plan.get("build") or []) + list(plan.get("delete") or [])
    exp = {"objects": list(before.get("objects") or []) + placed, "walls": before.get("walls") or [],
           "heights": before.get("heights") or {}}
    end = {"objects": after.get("objects") or [], "walls": (after.get("walls") or []) if exp["walls"] else [],
           "heights": (after.get("heights") or {}) if exp["heights"] else {}}
    d = session_delta(exp, end)
    if not d:
        return None
    off = matched(placed, d["removed"])                  # (placed, but not just where it was asked - or not at all)
    d["removed"] = [r for r in d["removed"] if not any(r is o for o in off)
                    and not any(w["floor"] == r["floor"] and _near((r["x"], r["y"]), w, BESIDE_CM) for w in walls)]
    stuck = matched([c for c in map(clean_obj, plan.get("remove") or []) if c], d["added"])    # (still there)
    d["added"] = [a for a in d["added"] if not any(a is s for s in stuck)]
    for o in off:
        twins = [a for a in d["added"] if a["table_id"] == o["table_id"] and a["floor"] == o["floor"]]
        if twins:
            d["added"].remove(min(twins, key=lambda a: math.hypot(a["x"] - o["x"], a["y"] - o["y"])))
    if not (d["added"] or d["removed"] or d["walls_add"] or d["walls_del"] or d["heights"]):
        return None
    return d


def compose(d0: dict, d1: dict) -> dict:
    """Two changes, one after the other, as one (only when too many wait to be sent)."""
    added, removed = list(d0.get("added") or []), list(d0.get("removed") or [])
    for r in d1.get("removed") or []:
        hit = next((a for a in added if same(r, a)), None)
        if hit is not None:
            added.remove(hit)                            # (added, then removed: neither)
        else:
            removed.append(r)
    heights = dict(d0.get("heights") or {})
    for f, h in (d1.get("heights") or {}).items():
        heights[f] = {"before": (heights.get(f) or h).get("before"), "after": h.get("after")}
    del1 = d1.get("walls_del") or []
    return {"added": added + list(d1.get("added") or []), "removed": removed,
            "walls_add": [w for w in d0.get("walls_add") or [] if not covered(w, del1)]
                         + list(d1.get("walls_add") or []),
            "walls_del": list(d0.get("walls_del") or []) + list(del1), "heights": heights, "at": d1.get("at"),
            "ids": delta_ids(d0) + delta_ids(d1)}


def fold(value: dict, d: dict, pairs: list | None = None) -> tuple[dict, int, list[dict]]:
    """Change d put on top of the house `value` (the server's newest version, maybe newer than the one d was made on)
    -> (the house with it, how many parts of d were dropped because that version had changed the same thing - the
    newer version wins there -, and where my game has those dropped moves: to be taken back to the server's).
    pairs: given a list, each dropped move is added to it as (where it was, where my game has it)."""
    objs, added, dropped, undo = list(value.get("objects") or []), list(d.get("added") or []), 0, []
    for r in d.get("removed") or []:
        twins = [a for a in added if a["table_id"] == r["table_id"] and a["floor"] == r["floor"]]
        moved_to = min(twins, key=lambda a: math.hypot(a["x"] - r["x"], a["y"] - r["y"])) if twins else None
        hit = next((o for o in objs if same(r, o)), None)
        if moved_to is not None:                         # (a removed and an added one of the same kind: a move)
            added.remove(moved_to)
            if hit is None:
                if has(objs, moved_to):                  # (that version has my move already: the same change put
                    continue                             # again after the server took it, its word late or lost)
                dropped += 1                             # (they moved or removed it meanwhile: theirs stays)
                undo.append(moved_to)
                if pairs is not None:
                    pairs.append((r, moved_to))
                continue
            objs.remove(hit)
            if not has(objs, moved_to):
                objs.append(moved_to)
        elif hit is not None:
            objs.remove(hit)
    for a in added:
        if not has(objs, a):
            objs.append(a)
    walls = list(value.get("walls") or [])
    if d.get("walls_del"):
        walls = [p for w in walls for p in subtract(w, d["walls_del"])]
    for p in d.get("walls_add") or []:
        if not covered(p, walls):
            walls.append(p)
    heights = dict(value.get("heights") or {})
    for f, h in (d.get("heights") or {}).items():
        cur = heights.get(f)
        if cur is None or abs(wh(cur) - wh(h.get("before"))) <= 1 or abs(wh(cur) - wh(h.get("after"))) <= 1:
            heights[f] = h.get("after")
        else:
            dropped += 1                                 # (they changed that storey's height too: theirs stays)
    return dict(value, objects=objs, walls=walls, heights=heights), dropped, undo


def same_house(a: dict, b: dict) -> bool:
    """Two houses with the same objects, walls and storey heights, as the comparisons see them."""
    ao, bo = a.get("objects") or [], b.get("objects") or []
    if len(ao) != len(bo) or diff(ao, bo) != ([], []):
        return False
    aw, bw = a.get("walls") or [], b.get("walls") or []
    if pieces(aw, bw) or pieces(bw, aw):
        return False
    ah, bh = a.get("heights") or {}, b.get("heights") or {}
    return all(abs(wh(ah.get(f)) - wh(bh.get(f))) <= 1 for f in set(ah) | set(bh))


def plan_storeys(theirs: dict, mine_heights: dict, mode: str, base: dict | None, keep: dict) -> list:
    """Storeys whose height to set (merge: only those my partner changed since the base; not those I changed and
    haven't sent yet)."""
    th = theirs.get("heights") or {}
    bh = (base["value"].get("heights") or {}) if mode == "merge" and base else None
    return [s for s in height_diff(th, mine_heights or {}, theirs.get("walls") or [])
            if (bh is None or abs(wh(th.get(str(s[0]))) - wh(bh.get(str(s[0])))) > 1)
            and str(s[0]) not in (keep.get("heights") or ())]


def house_plan(theirs: dict, mine: dict, mode: str = "full", base: dict | None = None,
               keep: dict | None = None) -> dict:
    """What to change in my game (read as `mine`) to bring it to `theirs` -> {"place", "remove", "build", "delete",
    "storeys"}. mode:
      "merge": their changes since `base` (the version my game was last brought to: {"value", "lot"}), the rest of my
               game kept (the module's docstring has the rule);
      "add":   no base: what's missing is placed, and only what `theirs` lists as taken out (taken_out) goes;
      "fill":  only what's missing is placed, nothing goes;
      "full":  exactly theirs (what they don't have goes).
    keep: my changes not on the server yet (they're never undone)."""
    T, M = theirs.get("objects") or [], mine.get("objects") or []
    Tw, Mw = theirs.get("walls") or [], mine.get("walls") or []
    keep = keep or {}
    missing = diff(T, M)[0]
    if mode == "merge" and base:
        B, BL = base["value"].get("objects") or [], base["lot"].get("objects") or []
        Bw, BLw = base["value"].get("walls") or [], base["lot"].get("walls") or []
        their_added, their_removed = diff(T, B)
        place = [o for o in missing if has(their_added, o) or has(BL, o)]
        remove = matched(their_removed, M)
        delete = [m for m in Mw if Tw and Bw and covered(m, Bw) and not covered(m, Tw)]
        kept = [m for m in Mw if m not in delete]
        # walls of theirs missing here: new since the base, in my game at the base and gone, or what they kept of a wall
        # of mine that goes (not one the game wouldn't build before: that's in neither my game nor new)
        build = [t for t in Tw if not covered(t, kept)
                 and (not Bw or not covered(t, Bw) or covered(t, BLw) or covered(t, delete))]
    else:
        if mode in ("add", "fill"):
            out_o, out_w = taken_out(theirs) if mode == "add" else ([], [])
            remove = [m for m in matched(out_o, M) if not has(T, m)]
            delete = [m for m in Mw if Tw and overlaps(m, out_w) and not covered(m, Tw)]
        else:
            remove = diff(T, M)[1]
            delete = [m for m in Mw if Tw and not covered(m, Tw)]
        place = missing
        kept = [m for m in Mw if m not in delete]
        build = [t for t in Tw if not covered(t, kept)]
    return {"place": [o for o in place if not has(keep.get("removed") or [], o)],
            "remove": [m for m in remove if not has(keep.get("added") or [], m)],
            "build": [t for t in build if not overlaps(t, keep.get("walls_del") or [])],
            "delete": [m for m in delete if not overlaps(m, keep.get("walls_add") or [])],
            "storeys": plan_storeys(theirs, mine.get("heights") or {}, mode, base, keep)}


def version_of(item) -> int:
    """A shared item's version (0: none)."""
    try:
        return int((item or {}).get("v") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def same_item(kind: str, a: dict, b: dict) -> bool:
    if kind in ("place", "remove"):
        return same(a, b)
    return covered(a, [b]) and covered(b, [a])


class HouseSync:
    def house_init(self):
        self.build_site = None        # while I'm in build mode on my home: its last reading, "entry" (the home as
        self.applying_house = False   # build mode opened) and "session" (the world session it's in: world_session)
        self.in_build_mode = False
        self.house_ticked = 0.0       # when house_tick last ran (it doesn't while the world is out)
        self.lot_file = LOT_FILE
        self.was_home = None          # my Zoi was on the home lot at the last tick
        self.asked_at = 0.0
        self.preset_file = PRESET_FILE
        self.lot_busy = False         # saving/applying the exact house (build mode opens and closes by itself)
        self.lot_applying = None      # the property folder the game is reading while it's applied (stash_lot)
        self.lot_tried = None         # the run of the game (game_run) the pending exact house was tried in
        self.exact_pushing = set()    # exact-house keys whose upload is waiting for the server's word
        self.exact_again = {}         # key -> base: another upload of it was asked for meanwhile (publish_exact)
        self.exact_push_at = {}       # key -> {"hash", "n", "at"}: uploads of that house the server didn't confirm (n
                                      # in a row): not sent again before "at" (exact_failed)
        self.exact_wanted = {}        # site -> {"site", "site_id", "load"}: build mode was left on the home, its exact
                                      # house is saved once my game is known to hold the shared house (kick_exact)
        self.house_yes = {}           # partner pid -> when I last said yes to their house
        self.house_lock = asyncio.Lock()    # what uses build mode (applying, saving the exact house): one at a time
        self.house_next = {}          # key -> the newest server version waiting to be brought into my game
        self.house_worker = None
        self.house_retry_at = 0.0     # (an apply had to wait: not again before then)
        self.house_pushing = set()    # keys whose changes are on their way to the server
        self.house_push_again = set()
        self.house_push_fails = 0
        self.house_push_at = 0.0      # changes the server didn't confirm: sent again from then
        self.house_failed = {}        # key -> {"v", "n"}: failed applies of that version (MAX_TRIES of them: left)
        self.lot_replaced_at = 0.0    # the exact house was applied then: readings of the home from before are stale
        self.exact_applied_at = 0.0   # ... and was applied whole then (not an apply that failed part of the way)
        try:
            pending = (self.cfg.get("pending_lot") or {}).get("folder")
            lotsync.cleanup(keep={pending} if pending else ())
        except Exception as e:                           # noqa: BLE001
            log.debug("cleaning old properties: %s", e)

    # ------------------------------------------------------------------ readings of the home
    def saved_lot(self, site) -> dict | None:
        """The last reading of the home (if it's lot `site`)."""
        try:
            lot = json.loads(self.lot_file.read_text("utf-8"))
            return lot if site and int(lot.get("site") or -1) == int(site) else None
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def session_entry(self, site, first: dict) -> dict:
        """What my home was as build mode opened on it, for the session's change (end_session). The first reading in
        build mode (`first`) comes a few seconds late - the 2 s tick, then the reading itself -, so what the player did
        right away (a lamp deleted at once) would already be in it and never sent, and the next version of my partner's
        would even put that lamp back (it's in the base and in theirs). So it's the last reading of the home from
        before, when there's one that still describes my game: taken in this world session (a save loaded since may be
        an older one: its differences aren't changes I made, and must never go up as removals) and after anything that
        replaced the lot as a whole (the exact house applied). Applies and build sessions keep a reading at their end,
        and play mode one every few minutes; what appeared in play mode since then just goes up with the session. Only
        without one (this program started in build mode, the first visit home in this session...) is it `first`."""
        prior = self.saved_lot(site)
        started = float((getattr(self, "save_session", None) or {}).get("started") or 0)
        if not prior or not started or float(prior.get("at") or 0) < max(started, self.lot_replaced_at):
            return first
        objects = prior.get("objects") or first.get("objects") or []    # (a reading that came back empty: the other)
        walls, heights = ((prior.get("walls"), prior.get("heights") or {}) if prior.get("walls")
                          else (first.get("walls") or [], first.get("heights") or {}))
        log.info("build mode on the home: changes counted from the reading of %d s ago", time.time() - prior["at"])
        return {"objects": objects, "walls": walls, "heights": heights}

    def world_session(self) -> float | None:
        """The world session I'm in (when it began: save_session, see the companion's watch_save), or None while that
        isn't known: the world was just entered from the main menu (game.saw_lobby, cleared by watch_save) and the
        companion hasn't looked at which save it is yet - save_session is still the last world's then."""
        started = float((getattr(self, "save_session", None) or {}).get("started") or 0)
        if not started or getattr(getattr(self, "game", None), "saw_lobby", False):
            return None
        return started

    def game_run(self):
        """Which run of the game this is (its process, kept in save_session; else the world session): the game lists
        properties only when it starts, so the partner's exact house kept for it is tried once per run."""
        return (getattr(self, "save_session", None) or {}).get("game") or self.world_session()

    def load_started(self) -> float | None:
        """When the save my game holds now was loaded (None: the world session isn't known yet). A loading screen that
        keeps the save (moving to another lot) starts a new world session but not a new load: the companion's
        watch_save carries save_session over then ({**prev, ...}), with the mark kept in it here. A save loaded (from
        the main menu, the game started again after a crash) gets a new save_session, so a new mark: what was read of
        the home before it may be another, newer save's than the one loaded - and so may this program having started,
        which can't know what was loaded before."""
        s = getattr(self, "save_session", None)
        if not isinstance(s, dict) or self.world_session() is None:
            return None
        if not s.get("house_load"):
            s["house_load"] = float(s.get("started") or 0)
        return float(s["house_load"])

    def base_current(self, base: dict) -> bool:
        """The base's reading of my lot (reached) is known to describe the save my game holds now: it was made since
        that save was loaded and since anything replaced the lot as a whole (the exact house applied). Else my game is
        brought to the server's version again (note_play_changes), and what it has beyond it that was taken out on
        purpose before (came_back) isn't sent as bought: a save loaded from before brings back what was deleted after it
        was written (by my partner, or by me), and that must never go up as added (house_done)."""
        start = self.load_started()
        return start is not None and float(base.get("read_at") or 0) >= max(start, self.lot_replaced_at)

    def lot_from_exact(self, base: dict | None) -> bool:
        """Since the base's reading, my game's lot was replaced as a whole by my partner's exact house (this program
        applied it, whole, in the save loaded now - the game's next start after it was kept, or "apply now"): what my
        home has is that property, saved before the live house (the server's version) as it is now. What it has
        beyond the live house that was taken out of the home on purpose after it was saved (came_back: a removal or
        move of mine, or of my partner's, made while it waited for the start) is older than the live house, which wins:
        it goes again (plan_for) - not kept as come back with an older save."""
        at, start = float(self.__dict__.get("exact_applied_at") or 0), self.load_started()
        return bool(base and at and start is not None and at >= start and at >= self.lot_replaced_at
                    and float(base.get("read_at") or 0) < at)

    def keep_lot(self, lot: dict):
        try:
            self.lot_file.parent.mkdir(parents=True, exist_ok=True)
            self.lot_file.write_text(json.dumps(dict(lot, at=time.time())), "utf-8")
        except OSError as e:
            log.debug("keeping the house: %s", e)

    async def home_site_cached(self):
        cached = self.__dict__.get("_home_cache")
        if not cached or time.time() - cached[1] > 30:
            cached = self._home_cache = (await self.g(self.my_home_site), time.time())
        return cached[0]

    def scan_site(self) -> list[dict]:
        out, offset = [], 0
        while offset < MAX_OBJECTS:
            page = self.game.call("build.scan_objects", Limit=500, Offset=offset) or {}
            objs = page.get("objects") or []
            for o in objs:
                k = object_key(o)
                k["entity_id"] = o.get("entity_id")
                if k["table_id"]:
                    out.append(k)
            offset += len(objs)
            if len(objs) < 500:
                break
        return out

    def scan_walls(self) -> list[dict]:
        out = _payload(self.game.call("build.scan_walls", Limit=0))
        return [wall_key(w) for w in (out.get("walls") or [])[:MAX_WALLS] if isinstance(w, dict)]

    def scan_heights(self) -> dict:
        """{floor (str): {"wall_height", "floor_height", "base_height"}} of each storey that has walls."""
        out = _payload(self.game.call("build.scan_floors", Limit=0))
        res = {}
        for f in out.get("floors") or []:
            hs = f.get("heights") or []
            if hs:
                res[str(int(f.get("floor_level") or 0))] = {k: hs[0].get(k) for k in ("wall_height", "floor_height",
                                                                                      "base_height")}
        return res

    def at_home_now(self, home) -> bool:
        """My Zoi is on the home lot right now. Outside build mode the scans read the lot my Zoi is on, and
        build.get_mode_state keeps naming the last lot it was in (seen: 958 while walking on 839), so this asks where
        my Zoi is."""
        try:
            return bool(home and self.my_entity and int(self.game.state_of(self.my_entity)["site"]) == int(home))
        except Exception:                                # noqa: BLE001
            return False

    def scan_lot(self) -> dict:
        """The lot as it is: objects, walls, heights (walls/heights empty if this game can't read them)."""
        lot = {"objects": self.scan_site(), "walls": [], "heights": {}}
        try:
            lot["walls"], lot["heights"] = self.scan_walls(), self.scan_heights()
        except Exception as e:                           # noqa: BLE001
            log.debug("reading the walls: %s", e)
        return lot

    async def read_home(self, home) -> dict | None:
        """My home as it is now (read, if my Zoi is on it), else its last reading (None: never read)."""
        if await self.g(self.at_home_now, home):
            try:
                return await self.g(self.scan_lot)
            except Exception as e:                       # noqa: BLE001
                log.debug("reading my house: %s", e)
        return self.saved_lot(home)

    def partner_name(self) -> str:
        r = self.partner(None) if hasattr(self, "partner") else None
        return r.name if r else "Your partner"

    # ------------------------------------------------------------------ what this side keeps (home-lot-sync.json)
    def sync_file(self) -> Path:
        return self.lot_file.with_name(self.lot_file.stem + "-sync.json")

    def hs(self) -> dict:
        """The house sync's own state: "base" (the server version my game was last brought to, and my lot then),
        "pending" (key -> my changes the server hasn't confirmed), "leftover" (what the game wouldn't take)."""
        path = self.sync_file()
        if self.__dict__.get("_hs_path") != path:
            try:
                data = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                data = {}
            self._hs, self._hs_path = data if isinstance(data, dict) else {}, path
        return self._hs

    def save_hs(self):
        path = self.sync_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self.hs()), "utf-8")
            os.replace(tmp, path)
        except OSError as e:
            log.warning("keeping the house's sync state: %s", e)

    def shared_scope_id(self) -> str:
        """Which server, and which life of its shared state, the versions belong to (shared.py)."""
        return f"{self.cfg.get('server_id') or '?'}:{getattr(self, 'shared_epoch', None) or ''}"

    def house_base(self, key) -> dict | None:
        """The version my game was last brought to, while it's still the one counted as seen on this same server (a
        version number of another server, or of one started over, means nothing here: no base then)."""
        b = self.hs().get("base")
        if (key and isinstance(b, dict) and b.get("key") == key and int(b.get("v") or 0) > 0
                and int(b["v"]) == self.seen(key) and b.get("scope") == self.shared_scope_id()
                and isinstance(b.get("value"), dict) and isinstance(b.get("lot"), dict)):
            return b
        return None

    def reached(self, key: str, v: int, value: dict, lot: dict, scope: str | None = None,
                used: list | None = None) -> bool:
        """My game is at version v of the server's house now (lot: read right then): it's the base of the next merge,
        and the version counts as brought in. scope: the server (shared_scope_id) that version came from, when it was
        read before something that takes a while (an apply): if the connection has moved to another server, or this
        one's shared state no longer has that version (started over), the number means nothing here and isn't kept -
        else a slow apply of the old server's v812 would count as seen on a new one whose house is at v5, and that
        house would be ignored. The time the lot was read is kept with it ("read_at": a kept reading's own time, else
        now): see base_current. used: what of the undo (my copies of moves a newer version beat) the apply's plan
        took back - only those are done with: one my push noted while the apply was under way is taken back by the
        next one. -> whether it was kept."""
        v = int(v or 0)
        if v <= 0 or v < self.seen(key) or not self.still_here(key, v, scope):
            return False
        value = dict(clean_lot(value), site=value.get("site"), site_id=value.get("site_id"))
        self.hs()["base"] = {"key": key, "v": v, "scope": self.shared_scope_id(), "value": value,
                             "lot": clean_lot(lot), "at": time.time(), "read_at": float(lot.get("at") or time.time())}
        if used:
            left = list(self.house_undo(key))
            for m in used:
                hit = next((x for x in left if same(x, m)), None)
                if hit is not None:
                    left.remove(hit)
            if left:
                self.hs()["undo"][key] = left
            else:
                (self.hs().get("undo") or {}).pop(key, None)
        self.save_hs()
        self.mark_seen(key, v)
        return True

    def house_undo(self, key: str) -> list:
        """My copies of moves a newer version beat (fold), not taken back out of my game yet."""
        u = self.hs().get("undo")
        u = u.get(key) if isinstance(u, dict) else None
        return [o for o in u if isinstance(o, dict)] if isinstance(u, list) else []

    def still_here(self, key: str, v: int, scope: str | None = None) -> bool:
        """Version v of the key (read on server `scope`) is still what this server's numbers mean: the connection is on
        that same server and life of its shared state, and it has that version or a newer one of the key."""
        if (scope is not None and scope != self.shared_scope_id()) or version_of(self.shared.get(key)) < int(v):
            log.info("%s v%s isn't this server's any more (another server, or its shared state started over): not "
                     "counted", key, v)
            return False
        return True

    def pending(self, key: str) -> dict | None:
        p = (self.hs().get("pending") or {}).get(key)
        return p if isinstance(p, dict) and p.get("deltas") else None

    def pending_keep(self, key: str) -> dict:
        """My changes not on the server yet, all together: an apply doesn't undo them."""
        ds = (self.pending(key) or {}).get("deltas") or []
        return {"added": [o for d in ds for o in d.get("added") or []],
                "removed": [o for d in ds for o in d.get("removed") or []],
                "walls_add": [w for d in ds for w in d.get("walls_add") or []],
                "walls_del": [w for d in ds for w in d.get("walls_del") or []],
                "heights": {f for d in ds for f in (d.get("heights") or {})}}

    def add_pending(self, site, d: dict, lot: dict):
        """A change of mine to the home, kept until the server confirmed it. Each has an id, which the house versions
        it's folded into carry along ("applied"): a change that got there, its word lost, is known by it (confirm_landed)
        - never sent again on top of a newer version that has taken it back out."""
        key = f"house:{site}:live"
        p = self.hs().setdefault("pending", {}).setdefault(key, {"deltas": []})
        if not delta_ids(d):
            d["id"] = secrets.token_hex(6)
        p["deltas"].append(d)
        while len(p["deltas"]) > MAX_PENDING:
            p["deltas"][:2] = [compose(*p["deltas"][:2])]
        p["lot"] = dict(clean_lot(lot), site=int(site),
                        site_id=lot.get("site_id") or (p.get("lot") or {}).get("site_id"))
        self.save_hs()
        self.set_dirty(site, "live", True)

    def drop_pending(self, key: str, gone: list) -> bool:
        """These changes (the very dicts) wait no more. -> whether anything is still waiting for that key."""
        p = self.pending(key)
        if p:
            p["deltas"] = [d for d in p["deltas"] if not any(d is g for g in gone)
                           and any(d.get(k) for k in DELTA_PARTS)]
        if self.pending(key):
            return True
        (self.hs().get("pending") or {}).pop(key, None)
        self.set_dirty(key.split(":")[1], "live", False)
        return False

    def confirm_landed(self, key: str, item: dict | None):
        """The server's version lists changes of mine as folded into it ("applied"): they got there - their word lost
        with the connection, or not back yet - and wait no more. My game counts as brought to them too: they're put on
        the base, so the merge of that version sees what it took back out of them (my partner deleting what I had
        added) as their change, and does it here as well."""
        have = set(applied_ids((item or {}).get("value")))
        p = self.pending(key)
        if not have or not p:
            return
        done = [d for d in p["deltas"] if landed(d, have)]
        if not done:
            return
        log.info("%d of my changes to the house are in the server's v%s already: not sent again", len(done),
                 (item or {}).get("v"))
        b = self.house_base(key)
        if b:
            for d in done:
                b["value"] = fold(b["value"], d)[0]
                b["lot"] = fold(b["lot"], d)[0]
        self.drop_pending(key, done)
        self.save_hs()

    def drop_beaten(self, key: str, pairs: list, after: dict) -> int:
        """My moves waiting to go up that a newer version beat (my partner moved or removed the same thing first), once
        an apply has taken my copy back out of my game (`after`, read after it): they wait no more - sent later, they'd
        be dropped anyway, or worse, put back if my partner put it back where it was. -> how many."""
        p, ao, n = self.pending(key), after.get("objects") or [], 0
        if not p or not pairs or not ao:
            return 0
        for r, m in pairs:
            if has(ao, m):
                continue                                 # (still in my game: my push drops it and takes it back later)
            for d in p["deltas"]:
                hr = next((x for x in d.get("removed") or [] if same(x, r)), None)
                ha = next((x for x in d.get("added") or [] if same(x, m)), None)
                if hr is not None and ha is not None:
                    d["removed"].remove(hr)
                    d["added"].remove(ha)
                    n += 1
                    break
        if n:
            self.drop_pending(key, [])
            self.save_hs()
        return n

    def note_gone(self, key: str, objs: list, walls: list = ()):
        """Objects and wall pieces taken out of my home on purpose: by an apply (my partner's removals and moves, a door
        the game took out with its wall, what I took out in the build mode it had open) or by me in build mode. A save
        loaded from before brings them back; then they're never sent as bought, nor in my exact house (house_done). The
        newest MAX_GONE of each are kept."""
        objs = [c for c in map(clean_obj, objs) if c]
        walls = [c for c in map(clean_wall, walls) if c]
        if not (objs or walls):
            return
        g = self.hs().get("gone")
        if not isinstance(g, dict):
            g = self.hs()["gone"] = {}
        e = g.get(key) if isinstance(g.get(key), dict) else {}
        o, w = list(e.get("objects") or []), list(e.get("walls") or [])
        o += diff(objs, o)[0]                            # (not twice)
        w += [p for p in walls if not covered(p, w)]
        g[key] = {"objects": o[-MAX_GONE:], "walls": w[-MAX_GONE:]}
        self.save_hs()

    def gone(self, key: str) -> dict:
        g = self.hs().get("gone")
        e = g.get(key) if isinstance(g, dict) else None
        return e if isinstance(e, dict) else {}

    def came_back(self, key: str, objs: list, lot: dict, theirs: dict) -> tuple[list, list]:
        """What of my home - `objs` (my objects the server's house `theirs` doesn't have), and my walls in `lot` - was
        taken out of it on purpose before (note_gone) and isn't one of my changes on its way: back with a save loaded
        from before. -> (objects, walls)."""
        g = self.gone(key)
        gone_o, gone_w, tw = g.get("objects") or [], g.get("walls") or [], theirs.get("walls") or []
        back = [o for o in objs if has(gone_o, o)] if gone_o else []
        mine = self.pending_keep(key)["walls_add"]
        back_w = [w for w in lot.get("walls") or [] if not covered(w, tw) and overlaps(w, gone_w)
                  and not overlaps(w, mine)] if gone_w and tw else []
        return back, back_w

    def keep_local_only(self, key: str, objs: list, walls: list):
        """These stay only in my game (came back with a save loaded): remembered, so my exact house isn't saved while my
        home has them (local_only_left)."""
        rec = self.hs().get("local_only")
        if not isinstance(rec, dict) or rec.get("key") != key:
            rec = {}
        o, w = list(rec.get("objects") or []), list(rec.get("walls") or [])
        o += diff([c for c in map(clean_obj, objs) if c], o)[0]
        w += [p for p in (c for c in map(clean_wall, walls) if c) if not covered(p, w)]
        self.hs()["local_only"] = {"key": key, "objects": o[:MAX_OBJECTS], "walls": w[:MAX_WALLS], "told": False}
        self.save_hs()

    def local_only_left(self, key: str, lot: dict) -> tuple[list, list]:
        """What's kept only in my game (keep_local_only) that my home - `lot`, a reading of it - still has, and that
        neither the server's house nor my changes on their way have: my exact house, the game's whole property, would
        bring it back into my partner's game. What's gone (taken out, or the server has it now) is forgotten. ->
        (objects, walls)."""
        rec = self.hs().get("local_only")
        if not isinstance(rec, dict) or rec.get("key") != key:
            return [], []
        cur = (self.shared.get(key) or {}).get("value")
        cur = cur if isinstance(cur, dict) else {}
        keep = self.pending_keep(key)
        lo, lw = lot.get("objects") or [], lot.get("walls") or []          # (a reading that came back empty: kept)
        so, sw = cur.get("objects") or [], cur.get("walls") or []
        ro, rw = rec.get("objects") or [], rec.get("walls") or []
        objs = [o for o in ro if (not lo or has(lo, o)) and not has(so, o) and not has(keep["added"], o)]
        walls = [w for w in rw if (not lw or overlaps(w, lw)) and not (sw and covered(w, sw))
                 and not overlaps(w, keep["walls_add"])]
        if len(objs) != len(ro) or len(walls) != len(rw):
            if objs or walls:
                rec.update(objects=objs, walls=walls)
            else:
                self.hs().pop("local_only", None)
                log.info("nothing kept only in my game is left in the home")
            self.save_hs()
        return objs, walls

    # ------------------------------------------------------------------ build mode on my home
    async def house_tick(self):
        """Called every couple of seconds while in the world (never while it's out): follows build mode on my home
        lot."""
        now = time.time()
        if self.build_site and self.house_ticked and now - self.house_ticked > TICK_GAP:
            self.build_site["stale"] = True              # (no tick for that long: the world was left meanwhile)
        self.house_ticked = now
        if self.me_id:
            self._house_me = self.me_id                  # (exact_behind: who "me" is while not connected)
        if self.world == "out" or not self.my_entity or self.lot_busy or self.applying_house:
            return
        session = self.world_session()
        if session is None:
            return                                       # (just back in a world: which save it is isn't known yet)
        self.load_started()                              # (marked now: the next loading screen carries the mark over)
        b = self.build_site
        if b and (b.get("stale") or b.get("session") != session):
            # build mode was open on the home when the world went (a crash, the menu): that session ended unseen, and
            # what my game has now may be another save's (end_session)
            self.build_site = None
            await self.end_session(b, stale=True)
        if not self.mp_active():                         # (another save: its home isn't the couple's)
            self.build_site = None
            return
        try:
            st = await self.g(self.game.call, "build.get_mode_state") or {}
        except Exception:                                # noqa: BLE001
            return
        if self.applying_house or self.lot_busy:         # (my program opened build mode meanwhile: its own)
            return
        self.in_build_mode = bool(st.get("in_build_mode"))
        if self.in_build_mode and st.get("phase") == "ready":
            site = st.get("site_entity_id")
            home = await self.home_site_cached()
            if site and home and int(site) == int(home):
                b = self.build_site
                if not b or time.time() - b.get("at", 0) > SCAN_EVERY:
                    try:
                        lot = await self.g(self.scan_lot)
                        entry = (b or {}).get("entry") or self.session_entry(int(site), lot)  # (as build mode opened)
                        self.build_site = dict(lot, site_id=st.get("site_id"), site=int(site), at=time.time(),
                                               entry=entry, session=session)
                    except Exception as e:                # noqa: BLE001
                        log.debug("reading the house: %s", e)
            return
        if st.get("phase") not in ("none", None):        # (build mode opening or closing: the next tick sees)
            return
        b, self.build_site = self.build_site, None
        if b:
            await self.end_session(b)
        if not self.in_build_mode:
            await self.home_tick()
            if not b:                                    # (just out of build mode: the exact house of what I built is
                await self.maybe_apply_pending()         # saved first - a partner's older one waiting is then dropped)
        self.kick_house()
        self.kick_exact()                                # (one that waited for my game to have the shared house)

    async def end_session(self, b: dict, stale: bool = False):
        """I left build mode on my home: what I changed there goes to the server, on top of its newest version.
        stale: the world went while build mode was open (the game crashed, or went to the menu from build mode) - the
        session ended unseen, and my game may hold another, older save of the home now. What it has isn't what I left
        (its differences would go up as removals of my partner's newer things, and as my exact house over theirs), so
        the change is only what the session's own readings show (the edits made before the world went): the home isn't
        read again or kept as its last reading, and no exact house is saved of it."""
        after = {k: v for k, v in b.items() if k not in ("entry", "at", "session", "stale")}
        if stale:
            log.info("build mode was open on the home when the world went: only what that session's own readings show "
                     "goes up")
        else:
            try:
                if await self.g(self.at_home_now, b["site"]):    # (the last reading can be seconds old: once more)
                    now = await self.g(self.scan_lot)
                    if now["objects"] or not after.get("objects"):
                        after["objects"] = now["objects"]
                    if now["walls"] or not after.get("walls"):
                        after["walls"], after["heights"] = now["walls"], now["heights"]
            except Exception as e:                       # noqa: BLE001
                log.debug("reading the house after build mode: %s", e)
            self.keep_lot(after)
        d = session_delta(b.get("entry") or after, after)
        key = f"house:{b['site']}:live"
        if d:
            log.info("left build mode on the home: %d added, %d removed, %d wall pieces built, %d knocked down, %d "
                     "storeys", len(d["added"]), len(d["removed"]), len(d["walls_add"]), len(d["walls_del"]),
                     len(d["heights"]))
            self.note_gone(key, d["removed"], d["walls_del"])
            self.add_pending(b["site"], d, after)
            self.house_task(self.push_house(b["site"]))
        else:
            log.info("left build mode on the home: no change to objects, walls or storeys")
        if not stale and self.cfg.get("exact_house", True):
            self.exact_wanted[str(b["site"])] = {"site": b["site"], "site_id": b.get("site_id"),
                                                 "load": self.load_started()}
            self.kick_exact(after)

    def house_task(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        keep = self.__dict__.setdefault("_house_tasks", set())      # (a task nobody holds can be collected mid-way)
        keep.add(task)
        task.add_done_callback(keep.discard)
        return task

    # ------------------------------------------------------------------ my changes to the server
    async def push_house(self, site) -> bool:
        """My changes to the home that the server hasn't confirmed go to it, on top of its newest version (compare-and-
        set on that version: if another one got there first, they're put on top of that one). True: the server has
        them all."""
        key = f"house:{site}:live"
        if key in self.house_pushing:
            self.house_push_again.add(key)               # (one send at a time: that one goes again when done)
            return False
        self.house_pushing.add(key)
        try:
            while True:
                self.house_push_again.discard(key)
                ok = await self._push_house(site, key)
                if key not in self.house_push_again:
                    return ok
        finally:
            self.house_pushing.discard(key)
            self.kick_house()                            # (an apply of that key waited for this)
            self.kick_exact()

    async def house_share(self, key: str, value, base: int) -> str:
        """Set a shared key and wait for the server's word: "ok", "refused" (a newer version is there), "rejected" (too
        big...: sending it again won't help), "lost" (no word), "offline"."""
        if hasattr(self, "share_ex"):
            return await self.share_ex(key, value, base)
        if not (self.writer and self.me_id):
            return "offline"
        if await self.share(key, value, base=base, wait=True):
            return "ok"
        return "refused" if int((self.shared.get(key) or {}).get("v") or 0) != int(base or 0) else "lost"

    async def _push_house(self, site, key: str) -> bool:
        for _ in range(PUSH_TRIES):
            cur = self.shared.get(key)
            self.confirm_landed(key, cur)                # (what the server lists as in it already isn't sent again)
            p = self.pending(key)
            if not p:
                self.set_dirty(site, "live", False)
                return True
            if not (self.writer and self.me_id):
                return False
            if any(not delta_ids(d) for d in p["deltas"]):      # (kept by an older version of this program)
                for d in p["deltas"]:
                    d.setdefault("id", secrets.token_hex(6))
                self.save_hs()
            sent, base_v = list(p["deltas"]), version_of(cur)
            value, dropped, undo = self.house_value(p, cur)
            if cur and same_house(value, clean_lot(cur.get("value"))):
                # (the server's version has all of it already - the same change landed before and its word came late
                # or went with the connection, or all of it was beaten: nothing to send, no new version for nothing)
                outcome = "ok"
                log.info("the server's house (v%d) has my changes already: nothing to send", base_v)
            else:
                outcome = await self.house_share(key, value, base_v)
            if outcome != "ok" and not self.pending(key):
                # (meanwhile the server's version showed them all as in it - confirm_landed: an earlier send of them got
                # there, its word lost)
                self.set_dirty(site, "live", False)
                self.house_push_fails, self.house_push_at = 0, 0.0
                return True
            if outcome == "ok":
                self.drop_pending(key, sent)             # (changes made while it was on its way wait for the next one)
                if undo:                                 # (my copies of moves the newer version beat: taken back by the
                    self.hs().setdefault("undo", {}).setdefault(key, []).extend(undo)     # next apply)
                self.save_hs()
                if undo and self.shared.get(key):
                    # (now: when nothing was sent - all of it beaten - no newer version comes to do it)
                    self.queue_house(key, self.shared[key])
                self.house_push_fails, self.house_push_at = 0, 0.0
                log.info("my change to the house is on the server (on v%d: %d objects, %d walls)", base_v,
                         len(value["objects"]), len(value["walls"]))
                if dropped:
                    who = (cur or {}).get("by_name") or self.partner_name()
                    self.system_line(f"{dropped} of your changes to the house weren't kept: {who} had changed the same "
                                     "things")
                return True
            if outcome == "offline":
                return False
            if outcome == "refused":
                log.info("the server has a newer house (v%s): my change goes on top of it",
                         (self.shared.get(key) or {}).get("v"))
                continue
            if outcome == "rejected":
                self.house_push_fails = len(PUSH_WAIT)
                log.warning("the server won't take the house (too big?): kept here")
            break
        self.house_push_fails += 1
        self.house_push_at = time.time() + PUSH_WAIT[min(self.house_push_fails, len(PUSH_WAIT)) - 1]
        return False

    def house_value(self, p: dict, cur: dict | None) -> tuple[dict, int, list]:
        """What goes to the server: my waiting changes put on top of its newest version (none yet: my home, whole), and
        the ids of the changes folded into it ("applied": that version's own list, carried along - clean_lot doesn't
        keep it -, with mine added; older programs leave it out), and what's been taken out of the house since its
        first version ("history", grown from that version's: one that keeps none - an older program's - has none
        after it either). One that version lists already isn't put on it again. -> (the value, how many of my changes
        the newer version beat, my objects of those to take back)."""
        lot = p.get("lot") or {}
        start = (cur or {}).get("value")
        applied = applied_ids(start)
        have = set(applied)
        todo = [d for d in p["deltas"] if not landed(d, have)]
        applied += [i for d in todo for i in delta_ids(d) if i not in have]
        undo = []
        if isinstance(start, dict):
            start = clean_lot(start)
            value, dropped = start, 0
            for d in todo:
                value, k, u = fold(value, d)
                dropped += k
                undo += u
            removed = diff(value["objects"], start["objects"])[1]
            walls_removed = pieces(start["walls"], value["walls"]) if value["walls"] else []
            site_id = (cur.get("value") or {}).get("site_id") or lot.get("site_id")
            hist = house_history(cur.get("value"))
            history = grow_history(hist, value, removed, walls_removed) if hist is not None else None
        else:                                            # (my home, whole: but never what's kept only in my game)
            value, dropped = clean_lot(lot), 0
            rec = self.hs().get("local_only")
            if isinstance(rec, dict) and rec.get("key") == f"house:{lot.get('site')}:live":
                mine = matched(rec.get("objects") or [], value["objects"])
                value["objects"] = [o for o in value["objects"] if not any(o is m for m in mine)]
                if rec.get("walls"):
                    value["walls"] = pieces(value["walls"], rec["walls"])
            removed = [o for d in p["deltas"] for o in d.get("removed") or []]
            walls_removed = [w for d in p["deltas"] for w in d.get("walls_del") or []]
            site_id = lot.get("site_id")
            history = grow_history(None, value, diff(removed, value["objects"])[0],
                                   pieces(walls_removed, value["walls"]) if value["walls"] else walls_removed)
        out = {"site": lot.get("site"), "site_id": site_id, "objects": value["objects"][:MAX_OBJECTS],
               "walls": value["walls"][:MAX_WALLS], "heights": value["heights"],
               "change": {"removed": removed[:MAX_OBJECTS], "walls_removed": walls_removed[:MAX_WALLS]},
               "applied": applied[-APPLIED_KEEP:]}
        if history is not None:
            out["history"] = history
        return out, dropped, undo

    async def publish_exact(self, preset: dict, base: int | None = None) -> bool:
        """My exact house to the server, on the version of it my game really holds (base; default: exact_held - not
        merely the newest one I've heard of: a partner's kept for the game's next start isn't in it). A property can't
        be merged: if my partner's newer one got there first, theirs is kept and mine dropped, with a chat line. One
        upload of a lot's exact house at a time: it's megabytes, and the shared loop finding it still unconfirmed
        (dirty) while it's on its way would send it again - a second version of the same house for everyone, the link
        full for a minute. One asked for meanwhile (a newer save of the house) waits for that one's answer; then the
        newest saved one goes, on the version the first one asked for was made on. True: the server has it."""
        site, key = preset["site"], f"house:{preset['site']}:exact"
        w = self.exact_push_at.get(key)
        if w and w.get("hash") == preset.get("hash") and time.time() < w["at"] and self.dirty(site, "exact"):
            return False                                 # (that very house wasn't confirmed a moment ago: it goes when
        if base is None:                                 # its wait is over - exact_failed; a newer save goes now)
            base = self.exact_held(key)
        if key in self.exact_pushing:
            self.exact_again.setdefault(key, int(base))
            return False
        self.exact_pushing.add(key)
        try:
            while True:
                ok = await self._publish_exact(preset, int(base))
                if key not in self.exact_again:
                    return ok
                base = self.exact_again.pop(key)
                newer = self.saved_preset(site)
                if not newer or newer.get("hash") == preset.get("hash"):
                    return ok                            # (the same one asked for again: its answer stands)
                preset = newer
        finally:
            self.exact_pushing.discard(key)
            self.exact_again.pop(key, None)

    def shared_online(self) -> bool:
        """Connected, with this connection's shared state (sets get an answer: shared.py)."""
        return bool(self.writer and self.me_id) and getattr(self, "shared_conn", None) is self.writer

    def exact_mark(self, base: int) -> dict:
        """The dirty mark of an exact house not on the server yet: the version it was made on, and whose numbers those
        are (shared_scope_id: another server's, or a started-over one's, mean nothing here)."""
        return {"base": int(base), "scope": self.shared_scope_id()}

    def mark_base(self, mark) -> int:
        """The version a dirty exact house was made on - what my game held when it was saved (exact_held) -, as this
        server counts. One from another server (or a started-over one), or marked by an older version of this program
        (True, or a base without a scope: the newest version it had heard of, which my game may never have taken - and
        may have taken since, after that save), isn't known to have anything of this server's in it: 0. It goes then
        only on top of my own (_publish_exact), never over my partner's."""
        try:
            if isinstance(mark, dict) and mark.get("base") is not None and mark.get("scope") == self.shared_scope_id():
                return int(mark["base"])
        except (TypeError, ValueError):
            pass
        return 0

    def exact_held(self, key: str) -> int:
        """The version of the exact house (key) my game really holds, as this server counts (0: none known): my own once
        the server confirmed it, a partner's once it was applied (maybe_apply_pending), or the server's when it's the
        very house my game last saved or took (cfg["lot_hash"]). Not one kept for the game's next start (stash_lot): the
        game doesn't have that yet, and an exact house of mine saved meanwhile doesn't have it either."""
        rec = self.cfg.get("exact_have")
        v = version_of(rec) if (isinstance(rec, dict) and rec.get("key") == key
                                and rec.get("scope") == self.shared_scope_id()) else 0
        cur = self.shared.get(key)
        h = ((cur or {}).get("value") or {}).get("hash") if isinstance((cur or {}).get("value"), dict) else None
        if h and h == self.cfg.get("lot_hash"):
            v = max(v, version_of(cur))
        return v

    def note_exact_held(self, key: str, v: int, scope: str | None = None):
        """My game holds version v of the exact house now (exact_held)."""
        scope, v = scope or self.shared_scope_id(), int(v or 0)
        rec = self.cfg.get("exact_have")
        if v <= 0 or (isinstance(rec, dict) and rec.get("key") == key and rec.get("scope") == scope
                      and version_of(rec) >= v):
            return
        self.cfg["exact_have"] = {"key": key, "v": v, "scope": scope}
        self.save_cfg()

    def exact_behind(self, key: str) -> dict | None:
        """The server's exact house when it's a partner's newer than the one my game holds (kept for the game's next
        start, or not taken yet): an exact house of mine saved now wouldn't have it, and would put my older paint,
        floors and roof back over theirs - in both games. None: there's no such one."""
        cur = self.shared.get(key)
        if self.me_id:
            self._house_me = self.me_id                  # (between connections the last snapshot stays, my id doesn't:
        me = self.me_id or self.__dict__.get("_house_me")   # it's the same one again - the server keeps it per key)
        if not cur or (me and cur.get("by") == me) or version_of(cur) <= self.exact_held(key):
            return None
        return cur

    def say_exact_behind(self, key: str, cur: dict):
        """Said once for each such version of my partner's (exact_behind)."""
        v = version_of(cur)
        if self.__dict__.get("_exact_behind_said") == (key, v):
            return
        self._exact_behind_said = (key, v)
        name = cur.get("by_name") or self.partner_name()
        p = self.cfg.get("pending_lot") or {}
        later = p.get("key") == key and int(p.get("v") or 0) == v
        log.info("the exact house isn't saved: %s's newer one (v%d) isn't in my game yet", name, v)
        self.system_line(f"Your exact house (paint, floors, roof) isn't shared: {name} changed it first, and theirs is "
                         + ("what your game gets when you reopen it" if later else "kept"))

    def live_busy(self, live: str) -> bool:
        """A version of the shared house (live) waits to come into my game, or a change of mine to it isn't confirmed
        yet (kept: its send may not have started, or comes back refused and goes on top of my partner's newer one):
        what my game holds isn't known to be the shared house."""
        return live in self.house_next or live in self.house_pushing or bool(self.pending(live))

    def preset_stale(self, preset: dict) -> str | None:
        """Why an exact house saved (preset) can't go up as it is (None: it can): the shared house (live) isn't the one
        my game held when it was saved ("live", noted by capture_lot: that version - or, with my changes not confirmed
        yet then, the house they make put on it, "expect": the server's version is fine once it's just that, mine
        landed and nothing else came in) - a change came since (my partner's, or mine put on top of theirs), so the
        property lacks it and would put back what they took out -, or it was saved on another server's house (or by an
        older version of this program, which didn't note it)."""
        rec, live = preset.get("live"), f"house:{preset.get('site')}:live"
        if not isinstance(rec, dict) or rec.get("scope") != self.shared_scope_id():
            return "it wasn't saved on this server's house"
        cur = self.shared.get(live)
        if version_of(cur) != int(rec.get("v") or 0):
            exp, now = rec.get("expect"), (cur or {}).get("value")
            if not (isinstance(exp, dict) and isinstance(now, dict) and same_house(clean_lot(now), clean_lot(exp))):
                return "the shared house changed since it was saved"
        return None

    def exact_hold(self, live: str) -> str | None:
        """Why my exact house can't be saved right now (None: it can): build mode can't be opened for it (as for an
        apply: house_hold), or my game isn't known to hold the shared house (live) as it is - a version of it waits to
        come in, my change to it is on its way up (what comes back may carry my partner's changes), or a save may have
        been loaded since my game was last brought to it (base_current: what that save brought back isn't known yet -
        local_only). The property is the whole lot as my game has it: saved before that, it would carry my partner's
        removals back into their game and my older things over their newer ones. My change not confirmed yet (made
        while the server can't be reached) doesn't hold it: the save notes the house my game has with it, and goes up
        only once the server's is that (capture_lot, preset_stale) - held, it would be lost with the game closed
        before the server is back (a save loaded since drops it: kick_exact)."""
        if self.world == "out" or not self.my_entity or self.load_started() is None:
            return "not in the game's world"
        if not self.mp_active():
            return "not in the multiplayer save"
        if self.lot_busy or self.applying_house:
            return "the house is being saved or applied"
        if self.build_site is not None or self.in_build_mode:
            return "in build mode"
        if live in self.house_next or live in self.house_pushing:
            return "the shared house is on its way in or up"
        if self.shared.get(live):
            base = self.house_base(live)
            if self.seen(live) < version_of(self.shared[live]) or not base:
                return "a version of the shared house isn't in my game yet"
            if not self.base_current(base) or self.house_undo(live):
                return "my game isn't known to be at the shared house"
        return None

    def kick_exact(self, lot: dict | None = None):
        """An exact house wanted (build mode was left on the home: end_session) is saved once it can be (exact_hold);
        not while my partner's newer one waits for the game's next start (exact_behind, said once), nor while the home
        has things kept only in my game (local_only_left, said once) - and a save loaded since drops it (what's in my
        game now isn't what that build mode left). lot: a reading of the home just made."""
        for site, w in list(self.exact_wanted.items()):
            live, key = f"house:{site}:live", f"house:{site}:exact"
            load = self.load_started()
            if not self.cfg.get("exact_house", True) or (load is not None and load != w.get("load")):
                self.exact_wanted.pop(site, None)
                log.info("the exact house wanted isn't saved: %s", "it's off" if not self.cfg.get("exact_house", True)
                         else "another save was loaded since")
                continue
            theirs = self.exact_behind(key)
            if theirs:
                self.exact_wanted.pop(site, None)
                self.say_exact_behind(key, theirs)
                continue
            why = self.exact_hold(live)
            if why:
                log.debug("the exact house waits: %s", why)
                cur, base = self.shared.get(live), self.house_base(live)
                if (cur and live not in self.house_next and live not in self.house_pushing
                        and (not base or not self.base_current(base)) and not self.house_gave_up(live, version_of(cur))):
                    # (a save may have been loaded since my game was last brought to the shared house, and build mode
                    # opened before the first reading of the home that brings it there again - note_play_changes: now)
                    self.queue_house(live, cur)
                continue
            reading = lot if lot is not None and lot.get("objects") else self.saved_lot(site)
            objs, walls = self.local_only_left(live, reading or {})
            self.exact_wanted.pop(site, None)
            if not (objs or walls):
                self.house_task(self.capture_lot(w))
                continue
            # (the property is the whole lot: it would put what came back with an older save - what my partner, or I,
            # had taken out - back into my partner's game)
            n = len(objs) + len(walls)
            log.info("the exact house isn't saved: the home has %d things kept only in my game", n)
            rec = self.hs().get("local_only") or {}
            if not rec.get("told"):
                rec["told"] = True
                self.save_hs()
                self.system_line(f"Your exact house (paint, floors, roof) isn't shared while your home has the {n} "
                                 f"thing{'s' if n != 1 else ''} kept only in your game: take "
                                 f"{'them' if n != 1 else 'it'} out in build mode to share it again")

    async def _publish_exact(self, preset: dict, base: int) -> bool:
        site, key = preset["site"], f"house:{preset['site']}:exact"
        value = {k: preset[k] for k in ("site", "site_id", "hash", "files")}
        for _ in range(3):
            if not self.shared_online():
                self.set_dirty(site, "exact", self.exact_mark(base))    # (sent once the server can be reached)
                return False
            cur = self.shared.get(key)
            now, mine = version_of(cur), bool(cur) and cur.get("by") == self.me_id
            if cur and (cur.get("value") or {}).get("hash") == preset["hash"]:
                # (the server has this very house already - mine, its word late or gone with the connection, or my
                # partner's, the same house in both games: nothing to send, no new version for nothing)
                self.set_dirty(site, "exact", False)
                self.exact_push_at.pop(key, None)
                if mine:
                    self.drop_older_pending_lot(key, now, base)
                self.mark_seen(key, now)                 # (it's the house my game has: my own save of it)
                self.note_exact_held(key, now)
                log.info("the server's exact house (v%d) is this one already: not sent again", now)
                return True
            if now != base:                              # (the server would refuse it: decided here, nothing sent)
                if cur and not mine:
                    self.exact_lost(key, cur)            # (my partner's, one I hadn't seen: theirs is kept)
                    return False
                base = now                               # (gone from the server, or my own newer one: mine on top)
            self.set_dirty(site, "exact", self.exact_mark(base))    # (kept until the server confirms it)
            live = f"house:{site}:live"
            if self.live_busy(live) or self.preset_stale(preset):
                # (my game isn't known to hold the shared house as it is now, or that save doesn't have it: not sent -
                # push_dirty_house looks at it again, and has it saved again once my game has the shared house)
                log.info("the exact house saved isn't sent now: %s", self.preset_stale(preset)
                         or "the shared house is on its way in or up")
                return False
            outcome = await self.house_share(key, value, base)
            if outcome == "ok":
                self.set_dirty(site, "exact", False)
                self.exact_push_at.pop(key, None)
                log.info("my exact house went to the server (%s)", preset["hash"])
                cur = self.shared.get(key)
                if cur and cur.get("by") == self.me_id and (cur.get("value") or {}).get("hash") == preset["hash"]:
                    self.note_exact_held(key, version_of(cur))
                    self.drop_older_pending_lot(key, version_of(cur), base)
                return True
            if outcome == "rejected":
                self.set_dirty(site, "exact", False)
                self.exact_push_at.pop(key, None)
                log.warning("the server won't take the exact house (too big?): not shared")
                self.system_line("Your exact house (paint, floors, roof) is too big for the server: not shared")
                return False
            if outcome == "offline":
                return False                             # (not connected: nothing went, sent once it's back)
            if outcome == "lost" and version_of(self.shared.get(key)) == base:
                self.exact_failed(key, preset["hash"])   # (no word, or the server couldn't keep it: sent again later)
                return False
            # (refused, or a newer version came while no word did: looked at again with the server's current one)
        self.exact_failed(key, preset["hash"])           # (each of those tries went up: not again right away)
        return False

    def exact_failed(self, key: str, h: str):
        """An upload of my exact house the server didn't confirm (no word in time, or it couldn't keep it: a full disk
        answers storage_failed): that same house isn't sent again before PUSH_WAIT, longer each time - it's megabytes,
        and the shared loop finds it unconfirmed every few seconds (push_dirty_house); nor is it when build mode is
        left again with the house unchanged (the same save). A newer save of the house goes at once (capture_lot), and
        the wait starts over with it."""
        w = self.exact_push_at.get(key)
        n = (w["n"] if w and w.get("hash") == h else 0) + 1
        wait = PUSH_WAIT[min(n, len(PUSH_WAIT)) - 1]
        self.exact_push_at[key] = {"hash": h, "n": n, "at": time.time() + wait}
        log.info("my exact house (%s) isn't confirmed by the server: sent again in %d s", h, wait)

    def exact_lost(self, key: str, cur: dict):
        """My partner's exact house got to the server before mine: theirs is kept, mine isn't sent (said in the chat)."""
        self.set_dirty(key.split(":")[1], "exact", False)
        self.exact_push_at.pop(key, None)
        name = cur.get("by_name") or self.partner_name()
        log.info("%s's exact house got to the server first: theirs is kept, mine isn't sent", name)
        self.system_line(f"{name} changed the exact house (paint, floors, roof) at the same time: theirs is kept")
        self.on_shared_house_exact(key, cur, False)     # (it waited while mine was unsent: taken now)

    def set_dirty(self, site, part: str, on):
        """on: True (or a dict: the exact house's {"base", "scope"} - exact_mark), or False when the server has it."""
        d = self.cfg.setdefault("house_dirty", {})
        k = f"{site}:{part}"
        if on:
            val = on if isinstance(on, dict) else (d.get(k) if isinstance(d.get(k), dict) else True)
            if d.get(k) != val:
                d[k] = val
                self.save_cfg()
        elif k in d:
            d.pop(k)
            self.save_cfg()

    def dirty(self, site, part: str) -> bool:
        return bool((self.cfg.get("house_dirty") or {}).get(f"{site}:{part}"))

    async def push_dirty_house(self):
        """Changes the server hasn't confirmed (made while it couldn't be reached, refused, lost): sent again."""
        d = dict(self.cfg.get("house_dirty") or {})
        for key in [k for k in self.hs().get("pending") or {} if self.pending(k)]:
            d.setdefault(f"{key.split(':')[1]}:live", True)     # (kept here even if the mark in the config wasn't)
        if not d or not (self.writer and self.me_id):
            return
        for k, mark in d.items():
            site, _, part = str(k).rpartition(":")
            if part == "live":
                if time.time() < self.house_push_at:     # (the server didn't confirm the last one: PUSH_WAIT)
                    continue
                if self.pending(f"house:{site}:live"):
                    await self.push_house(site)
                else:
                    await self.push_old_dirty(site)
            elif part == "exact" and not self.lot_busy and f"house:{site}:exact" not in self.exact_pushing:
                preset = self.saved_preset(site)         # (one on its way already: its answer decides - publish_exact)
                if not preset:
                    self.set_dirty(site, "exact", False)
                    continue
                if not self.shared_online():
                    continue                             # (this connection's shared state isn't in yet: compared then)
                theirs = self.exact_beaten(f"house:{site}:exact", preset, mark)
                if theirs:
                    self.exact_lost(f"house:{site}:exact", theirs)
                    continue
                if self.live_busy(f"house:{site}:live"):
                    continue                             # (what my game will hold isn't known yet: looked at after)
                why = self.preset_stale(preset)
                if why:
                    self.stale_dirty_exact(site, preset, why)
                    continue
                await self.publish_exact(preset, base=self.mark_base(mark))     # (not before its wait is over)

    def exact_beaten(self, key: str, preset: dict, mark) -> dict | None:
        """The server's exact house (key) when it's my partner's, on another version than the one my unsent one
        (preset, and its dirty mark: mark_base) was saved on: theirs is newer, and kept - as publish_exact decides,
        whatever the live house is doing. None: it isn't so."""
        cur = self.shared.get(key)
        me = self.me_id or self.__dict__.get("_house_me")
        if (cur and cur.get("by") != me and version_of(cur) != self.mark_base(mark)
                and (cur.get("value") or {}).get("hash") != preset.get("hash")):
            return cur
        return None

    def stale_dirty_exact(self, site, preset: dict, why: str):
        """An exact house not on the server yet whose save isn't the shared house any more (preset_stale: a change of
        my partner's came since - made while I was offline, say -, which it would undo in their game): saved again
        once my game holds the shared house (kick_exact), in the multiplayer save's world - till then it stays
        unsent."""
        if not self.cfg.get("exact_house", True):
            self.set_dirty(site, "exact", False)
            log.info("the exact house not sent yet isn't the shared house any more (%s), and it's off: dropped", why)
            return
        load = self.load_started()
        if load is None or not self.mp_active() or str(site) in self.exact_wanted:
            return
        if self.__dict__.get("_stale_exact_said") != preset.get("hash"):
            self._stale_exact_said = preset.get("hash")
            log.info("the exact house not sent yet isn't sent as it is (%s): saved again once my game has the shared "
                     "house", why)
        self.exact_wanted[str(site)] = {"site": preset["site"], "site_id": preset.get("site_id"), "load": load}
        self.kick_exact()

    async def push_old_dirty(self, site):
        """A change marked by an older version of this program (only "my home changed", no change kept): sent as the
        difference from the server's version if that's still the one my game was at; else theirs is newer and kept."""
        key = f"house:{site}:live"
        if self.world == "out" or not self.my_entity or not self.mp_active() or self.lot_busy:
            return
        lot = await self.read_home(site)
        if lot is None:
            return
        cur = self.shared.get(key)
        if cur and self.seen(key) != int(cur.get("v") or 0):
            self.set_dirty(site, "live", False)
            name = cur.get("by_name") or self.partner_name()
            log.info("a change to the house made offline by an older version can't be merged with v%s: theirs is kept",
                     cur.get("v"))
            self.system_line(f"Your offline changes to the house couldn't be merged with {name}'s newer house: theirs "
                             "is kept")
            self.queue_house(key, cur)
            return
        objs = clean_lot(lot)["objects"]
        d = session_delta(clean_lot(cur.get("value")), lot) if cur else (added_delta(objs) if objs else None)
        if not d:
            self.set_dirty(site, "live", False)
            return
        site_id = (lot.get("site_id") or ((cur or {}).get("value") or {}).get("site_id")
                   or (self.saved_lot(site) or {}).get("site_id"))
        self.add_pending(site, d, dict(lot, site_id=site_id))
        await self.push_house(site)

    # ------------------------------------------------------------------ the server's versions into my game
    def on_shared_house_live(self, key: str, item: dict | None, snapshot: bool):
        """A version of a house:<lot>:live (the server's: the welcome, a change, or the answer to a refused set of mine,
        mine included): queued; one apply at a time brings the newest in (house_work)."""
        if not item:
            self.house_next.pop(key, None)
            return
        self.confirm_landed(key, item)
        if self.seen(key) >= int(item.get("v") or 0) and self.house_base(key):
            return
        self.queue_house(key, item)

    def queue_house(self, key: str, item: dict):
        old = self.house_next.get(key)
        if not old or int(item.get("v") or 0) >= int(old.get("v") or 0):
            self.house_next[key] = item
        self.kick_house()

    def house_hold(self) -> str | None:
        """Why the house can't be applied right now (None: it can)."""
        if self.world == "out" or not self.my_entity:
            return "not in the game's world"
        if self.world_session() is None:                 # (save_session, mp_active included, is still the last
            return "just back in a world"                # world's until the companion has looked at this one)
        if not self.mp_active():
            return "not in the multiplayer save"
        if self.lot_busy:
            return "the exact house is being saved or applied"
        if self.build_site is not None or (self.in_build_mode and not self.applying_house):
            return "in build mode"
        if time.time() < self.house_retry_at:
            return "waiting a moment"
        return None

    def kick_house(self):
        if (not any(k not in self.house_pushing for k in self.house_next) or self.house_hold()
                or (self.house_worker and not self.house_worker.done())):
            return
        self.house_worker = self.house_task(self.house_work())

    async def house_work(self):
        """Brings the server's versions into my game, one at a time, each key at its newest."""
        while not self.house_hold():
            ready = [k for k in self.house_next if k not in self.house_pushing]   # (my change to it is on its way:
            if not ready:                                                        # push_house kicks this again)
                return
            key = min(ready, key=lambda k: int(self.house_next[k].get("v") or 0))
            item = self.house_next.pop(key)
            try:
                await self.house_version(key, item)
            except Exception as e:                       # noqa: BLE001
                log.warning("bringing the server's house in: %s", e)
                if self.house_failure(key, int(item.get("v") or 0)):
                    self.house_retry_at = time.time() + 5
                    self.queue_house(key, item)          # (tried again in a moment, a few times)

    def house_failure(self, key: str, v: int) -> bool:
        """An apply of version v failed: counted; False once it's failed MAX_TRIES times (left until a newer version,
        or the panel's "get my partner's house"; said once)."""
        f = self.house_failed.get(key)
        f = self.house_failed[key] = {"v": v, "n": (f["n"] if f and f["v"] == v else 0) + 1}
        if f["n"] < MAX_TRIES:
            return True
        if f["n"] == MAX_TRIES:
            log.warning("bringing %s v%d into the game failed %d times: left until it changes", key, v, f["n"])
            self.system_line("Your partner's changes to the house couldn't be brought into your game (\"get my "
                             "partner's house\" tries again)")
        return False

    def house_gave_up(self, key: str, v: int) -> bool:
        f = self.house_failed.get(key)
        return bool(f and f["v"] == v and f["n"] >= MAX_TRIES)

    async def house_version(self, key: str, item: dict):
        # The server's current version of the key is the one brought in: a newer one may be there already, and one
        # queued before the connection moved to another server (or before this one's shared state started over) is
        # another server's house, whatever its number - never applied here.
        item = self.shared.get(key)
        if not item:
            return
        v, scope = int(item.get("v") or 0), self.shared_scope_id()
        home = await self.home_site_cached()
        if not home or str(home) != key.split(":")[1]:
            return                                       # (another lot: only the home's key changes the home)
        again = False
        if self.seen(key) >= v:
            base = self.house_base(key)
            if self.seen(key) == v and not base:
                mine = await self.read_home(home)        # (brought to it already, by a version that kept no base)
                if mine is not None:
                    self.reached(key, v, item.get("value") or {}, mine, scope)
                return
            if self.seen(key) > v or (self.base_current(base) and not self.house_undo(key)):
                return                                   # (my copies of beaten moves still there: taken back now)
            again = True                                 # (my game was brought to it before a save was loaded, maybe
        if self.house_gave_up(key, v):                   # an older one: brought to it again - note_play_changes)
            return
        by = item.get("by")
        name = item.get("by_name") if by != self.me_id else self.partner_name()
        q = self.house_request(by, item.get("value") or {}, name=name, auto=True, shared=(key, v), item=item,
                               scope=scope)
        q["again"] = again
        await self.prepare_q(q)

    def catch_up_house(self):
        """Server versions of the house not in my game yet (one that had to wait, or never compared): queued again."""
        for k in self.house_keys():
            it = self.shared.get(k) or {}
            if self.seen(k) < int(it.get("v") or 0) or not self.house_base(k):
                self.queue_house(k, it)

    def house_keys(self) -> list[str]:
        return [k for k in self.shared if k.startswith("house:") and k.endswith(":live")]

    def reapply_shared_house(self, force: bool = False) -> bool:
        """The panel's "get my partner's house" (and once a session when the partner's Zoi shows up): a server version
        not in my game yet comes in as any new one; otherwise my home is compared with the server's version and a popup
        offers what's missing (force: all of it - what it doesn't have goes too, and what the game refused before is
        tried again). True: the server has the house."""
        keys = sorted(self.house_keys(), key=lambda k: int((self.shared.get(k) or {}).get("v") or 0))
        for k in keys:
            it = self.shared.get(k) or {}
            self.house_failed.pop(k, None)               # (asked for: what failed before is tried again)
            if not force and (self.seen(k) < int(it.get("v") or 0) or not self.house_base(k)):
                self.queue_house(k, it)
            else:
                self.house_task(self.redo_house(k, it, force, self.shared_scope_id()))
        return bool(keys)

    async def redo_house(self, key: str, item: dict, force: bool, scope: str | None = None):
        home = await self.g(self.my_home_site)
        if not home or str(home) != key.split(":")[1]:
            return
        by = item.get("by")
        name = item.get("by_name") if by != self.me_id else self.partner_name()
        q = self.house_request(by, item.get("value") or {}, name=name, mode="full" if force else "fill",
                               redo=(key, int(item.get("v") or 0)), force=force, item=item, scope=scope)
        await self.prepare_q(q)

    # ------------------------------------------------------------------ the exact house (lotsync.py)
    def on_shared_house_exact(self, key: str, item: dict | None, snapshot: bool):
        if not item:
            return
        v, val = int(item.get("v") or 0), item.get("value") or {}
        if item.get("by") == self.me_id:
            rec = self.cfg.get("exact_have")             # (what my game was known to hold before this word of it -
            held = (version_of(rec) if isinstance(rec, dict) and rec.get("key") == key     # what mine was saved on;
                    and rec.get("scope") == self.shared_scope_id() else None)            # not known: the server's
            if val.get("hash") and self.cfg.get("lot_hash") != val["hash"]:                # compare-and-set says)
                self.cfg["lot_hash"] = val["hash"]
                self.save_cfg()
            self.drop_older_pending_lot(key, v, held)
            self.mark_seen(key, v)
            self.note_exact_held(key, v)
            return
        if val.get("hash") and val["hash"] == self.cfg.get("lot_hash"):
            self.note_exact_held(key, v)                 # (the very house my game has: my partner's is the same)
        if self.seen(key) >= v or self.dirty(key.split(":")[1], "exact"):
            return                                       # (mine waits to go up: publish_exact decides which stays)
        if val.get("hash") in (self.cfg.get("lot_hash"), (self.cfg.get("pending_lot") or {}).get("hash")):
            self.mark_seen(key, v)
            return
        self.house_task(self.take_shared_exact(key, v, item.get("by_name") or "Your partner", val,
                                               self.shared_scope_id()))

    async def take_shared_exact(self, key: str, v: int, name: str, val: dict, scope: str | None = None):
        home = await self.g(self.my_home_site)
        if not home or int(home) != int(val.get("site") or -1):
            log.info("%s's exact house is lot %s, mine is %s: not taken", name, val.get("site"), home)
            return
        if (scope is not None and scope != self.shared_scope_id()) or version_of(self.shared.get(key)) != v:
            log.info("%s's exact house v%d isn't the server's any more: not taken", name, v)
            return                                       # (a newer one came meanwhile - it's taken itself - or the
        if self.stash_lot(name, val, shared=(key, v)):   # connection moved to another server, whose own comes with
            self.mark_seen(key, v)                       # its welcome; one that couldn't be kept is tried again)

    def drop_older_pending_lot(self, key: str, v: int, base: int | None = None):
        """My own exact house is the server's version v of `key` now, saved on top of version `base` (what my game held
        then: exact_held): a partner's one still waiting for the game's next start that it has in it (base at least
        its version, on this same server) would put their older house back over mine on reopening - and my next save
        of the house would then send that over theirs. It goes: the server's version is the house, and that's mine
        (already in my game). One my house doesn't have in it is never dropped: it's theirs, newer than what mine was
        saved on (the server doesn't take mine over it - publish_exact)."""
        p = self.cfg.get("pending_lot") or {}
        if not (p and p.get("key") == key and p.get("v") and int(p["v"]) < int(v)
                and p.get("scope") == self.shared_scope_id()):
            return                                       # (a stash from another server, or from before versions were
        if base is not None and int(p["v"]) > int(base):     # kept with it, isn't known to be older: left)
            log.info("%s's exact house v%s waiting for the next start isn't in mine (saved on v%s): kept",
                     p.get("from"), p.get("v"), base)
            return
        self.cfg.pop("pending_lot", None)
        self.save_cfg()
        if not self.lot_busy:                            # (the game may be reading it right now: then it's removed
            try:                                         # when that's done, or at the next start - house_init)
                lotsync.cleanup()
            except Exception as e:                       # noqa: BLE001
                log.debug("removing the older exact house: %s", e)
        log.info("%s's exact house v%s waiting for the next start is older than mine on the server (v%d): dropped",
                 p.get("from"), p.get("v"), v)

    def saved_preset(self, site=None) -> dict | None:
        try:
            p = json.loads(self.preset_file.read_text("utf-8"))
            return p if site is None or int(p.get("site") or -1) == int(site) else None
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def rewant_exact(self, b: dict, why: str):
        """The exact house (b: an exact_wanted entry) isn't saved or sent now (why): it's saved again once my game is
        known to hold the shared house (kick_exact - after that version is in, or my change confirmed)."""
        log.info("the exact house isn't sent now (%s): saved again once my game has the shared house", why)
        self.exact_wanted.setdefault(str(b["site"]), b)

    async def capture_lot(self, b: dict):
        """Save my home as the game's property (build mode reopens for a few seconds) and send it to my partner. Only
        while my game is known to hold the shared house (exact_hold): looked at again once build mode is mine (an
        apply may have had it meanwhile), and once the save is done - a version of my partner's that came in during
        those seconds isn't in it (it waited: the house was being saved), so that save isn't sent; it's saved again
        after that version is in."""
        if not b.get("site_id"):
            return
        preset, live = None, f"house:{b['site']}:live"
        async with self.house_lock:                      # (never while an apply or another save has build mode open:
            why = self.exact_hold(live)                  # this one waits its turn - if it were dropped, what I built in
            if why:                                      # that session would never reach my partner's exact house)
                self.rewant_exact(b, why)
                self.kick_house()                        # (a version that waits: in once the lock is free)
                return
            cur, p = self.shared.get(live), self.pending(live)
            on = {"v": version_of(cur), "scope": self.shared_scope_id()}
            if p:                                        # (my changes the server hasn't confirmed: in my game, and so
                value, dropped, undo = self.house_value(p, cur)          # in this save - what the server's house is
                if not (dropped or undo):                # once they're on it: preset_stale. One the server's beat: my
                    on["expect"] = {k: value[k] for k in ("objects", "walls", "heights")}   # copy is in the save)
            self.lot_busy = True
            try:
                res = await self.g(lotsync.save_lot, self.game.call, b["site_id"],
                                   lotsync.new_title(self.cfg.get("name") or "home"))
                preset = {"site": b["site"], "site_id": b["site_id"], "hash": res["hash"], "files": res["files"],
                          "at": time.time(), "live": on}  # (the shared house it was saved on: preset_stale)
                self.preset_file.parent.mkdir(parents=True, exist_ok=True)
                self.preset_file.write_text(json.dumps(preset), "utf-8")
                self.cfg["lot_hash"] = res["hash"]
                self.save_cfg()
                log.info("saved the exact house (%s)", res["hash"])
            except lotsync.LotError as e:
                log.warning("couldn't save the exact house: %s", e)
            except Exception as e:                       # noqa: BLE001
                log.warning("saving the exact house failed: %s", e)
            finally:
                self.lot_busy = False
            why = preset and (self.exact_hold(live) or self.preset_stale(preset))
            if why:
                self.rewant_exact(b, why)
                preset = None
        if preset:
            await self.publish_exact(preset)
        else:
            self.kick_house()                            # (a version that waited for the save: in now)

    async def send_lot(self, preset: dict):
        r = self.partner(None) if hasattr(self, "partner") else None
        if not preset or not r or not (self.writer and self.me_id):
            return
        await self.send({"t": "event", "kind": "couple", "to": r.pid,
                         "data": {"type": "lot", "id": secrets.token_hex(6), "site": preset["site"],
                                  "site_id": preset["site_id"], "hash": preset["hash"], "files": preset["files"]}})
        log.info("sent the exact house to %s", r.name)

    def on_lot(self, frm: str, data: dict):
        """My partner's exact house: the same as mine -> nothing; else kept to apply once I said yes to their house."""
        r = self.remotes.get(frm)
        name = r.name if r else "Your partner"
        if not isinstance(data.get("files"), dict) or not data.get("hash"):
            return
        if data["hash"] in (self.cfg.get("lot_hash"), (self.cfg.get("pending_lot") or {}).get("hash")):
            log.info("%s's exact house is the same as mine", name)
            return
        self.house_task(self.take_lot(frm, name, data))

    async def take_lot(self, frm: str, name: str, data: dict):
        home = await self.g(self.my_home_site)
        if not home or int(home) != int(data.get("site") or -1):
            log.info("%s's exact house is lot %s, mine is %s: ignored", name, data.get("site"), home)
            return
        if time.time() - self.house_yes.get(frm, 0) < LOT_ACCEPT_FOR or self.always_yes("house", None):
            self.stash_lot(name, data)
            return
        for q in self.requests.values():                 # (their house popup is still open: it carries this too)
            if q.get("action") == "house" and q.get("from_id") == frm:
                q["house"]["lot"] = data
                return
        rid = secrets.token_hex(6)
        self.requests[rid] = {"id": rid, "from": name, "from_id": frm, "action": "house", "at": time.time(),
                              "local": True, "label": "finishes",
                              "house": {"site": data.get("site"), "site_id": data.get("site_id"), "objects": None,
                                        "walls": [], "heights": {}, "lot": data}}
        self.system_line(f"{name} changed the house: finishes")

    def stash_lot(self, name: str, data: dict, shared: tuple | None = None) -> bool:
        """Write their exact house as a property; it's applied when the game next enters the world. shared: (key,
        version) when it's the server's version (kept with it: a newer one of mine replaces it -
        drop_older_pending_lot). -> whether it was kept."""
        old = self.cfg.get("pending_lot") or {}
        title = f"{lotsync.TITLE_PREFIX} {name} {data['hash'][:6]}"
        try:
            folder = lotsync.write_pending(data["files"], title)
        except Exception as e:                           # noqa: BLE001
            log.warning("couldn't keep %s's exact house: %s", name, e)
            return False
        if old.get("folder") and old["folder"] != folder:
            # (the older one goes - but not one the game is reading right now, being applied: it's removed when that's
            # done - maybe_apply_pending -, or at the next start - house_init)
            applying = self.__dict__.get("lot_applying")
            lotsync.cleanup(keep={folder, applying} if applying else {folder})
        self.cfg["pending_lot"] = {"title": title, "folder": folder, "site_id": data.get("site_id"),
                                   "hash": data["hash"], "from": name, "at": time.time()}
        if shared:
            self.cfg["pending_lot"].update(key=shared[0], v=int(shared[1]), scope=self.shared_scope_id())
        self.save_cfg()
        self.lot_tried = None
        log.info("kept %s's exact house: applied when the game reopens", name)
        self.system_line(f"{name}'s exact house (paint, floors, roof) comes in when you reopen the game")
        return True

    def exact_waits(self, p: dict) -> bool:
        """My own exact house waits to go up (dirty: saved while the server couldn't be reached, or its word isn't
        back), made on top of the partner's one kept in `p` (pending_lot) - its mark's base, what my game held when it
        was saved (exact_held), is that version or newer: applying theirs now would put their older house over what I
        built in my game, and my next save would then send theirs back over mine. The server decides which stays
        (publish_exact): mine confirmed drops theirs (drop_older_pending_lot), theirs kept replaces it. One of mine
        saved on an older version than theirs doesn't have theirs in it: it doesn't hold theirs back (the server keeps
        theirs). Only for one kept from the server's versions, compared on the same server's numbers (one from another
        server, or from before versions were kept with it, isn't known to be older)."""
        key, v, scope = p.get("key"), int(p.get("v") or 0), p.get("scope")
        if not (key and v and scope):
            return False
        mark = (self.cfg.get("house_dirty") or {}).get(f"{str(key).split(':')[1]}:exact")
        return (isinstance(mark, dict) and mark.get("base") is not None and mark.get("scope") == scope
                and int(mark["base"]) >= v)

    async def maybe_apply_pending(self):
        """The partner's exact house kept for the game's next start (stash_lot): tried once per run of the game - the
        game lists properties only when it starts, so in the run it was written in it isn't there (NotListed)."""
        p = self.cfg.get("pending_lot")
        run = self.game_run()
        if (not p or run is None or self.lot_tried == run or self.lot_busy or self.world != "play"
                or self.in_build_mode or self.house_lock.locked()):     # (an apply has build mode: the next tick)
            return
        if self.exact_waits(p):
            if self.__dict__.get("_exact_wait_said") != p.get("hash"):
                self._exact_wait_said = p.get("hash")
                log.info("%s's exact house waits: mine, made on top of it, isn't on the server yet", p.get("from"))
            return
        async with self.house_lock:
            self.lot_tried = run
            self.lot_busy, self.lot_applying = True, p.get("folder")    # (stash_lot leaves that folder alone)
            try:
                await self.g(lotsync.apply_by_title, self.game.call, p["site_id"], p["title"])
                self.lot_replaced_at = time.time()       # (the lot is another one now: readings from before aren't it)
                self.exact_applied_at = self.lot_replaced_at     # (and it's that property as a whole: lot_from_exact)
                self.cfg["lot_hash"] = p["hash"]
                now = self.cfg.get("pending_lot") or {}
                if now.get("folder") == p.get("folder") and now.get("hash") == p.get("hash"):
                    self.cfg.pop("pending_lot", None)
                elif now:                                # (a newer one of theirs came while this one was put in -
                    log.info("%s's newer exact house came while the older one was applied: it waits for the next "
                             "start", now.get("from"))   # stash_lot: kept, and its folder too)
                if p.get("key") and p.get("v") and p.get("scope"):
                    self.note_exact_held(p["key"], p["v"], p["scope"])   # (my game holds that version now)
                self.save_cfg()
                log.info("applied %s's exact house", p["from"])
                self.system_line(f"House now exactly like {p['from']}'s")
            except lotsync.NotListed:
                log.info("%s's exact house was kept while the game ran: it comes in after the game restarts", p["from"])
            except Exception as e:                       # noqa: BLE001
                self.lot_replaced_at = time.time()       # (it may have got part of the way)
                log.warning("applying the exact house failed: %s", e)
            finally:
                self.lot_busy, self.lot_applying = False, None
                left = (self.cfg.get("pending_lot") or {}).get("folder")
                if p.get("folder") != left:              # (applied, or replaced or dropped meanwhile: its property goes,
                    try:                                 # the one still waiting stays)
                        lotsync.cleanup(keep={left} if left else ())
                    except Exception as e:               # noqa: BLE001
                        log.debug("removing the applied exact house: %s", e)

    async def apply_lot_now(self) -> dict:
        """Panel: save, restart the game; the pending exact house is applied when the world loads again."""
        if not self.cfg.get("pending_lot"):
            return {"error": "no house waiting to be applied", "code": "no_pending"}
        if self.world == "out":
            return {"error": "you're not in the game's world", "code": "not_in_game"}
        async with self.house_lock:
            self.lot_busy = True
            try:
                await self.g(lotsync.restart_game, self.game.call)
            except lotsync.LotError as e:
                return {"error": str(e), "code": "restart"}
            finally:
                self.lot_busy = False
        self.lot_tried = None
        return {"ok": True}

    # ------------------------------------------------------------------ play mode at home; asking the partner
    async def home_tick(self):
        """Play mode: keep the reading of the home fresh while my Zoi is there (what appeared there - bought, a gift -
        goes to the server); on getting home, a server version that had to wait comes in (without the server's shared
        state: the partner is asked for their home)."""
        home = await self.home_site_cached()
        here = (self.last_full_state or {}).get("site")
        at_home = bool(home and here is not None and int(here) == int(home))
        arrived = at_home and self.was_home is False
        self.was_home = at_home
        if not at_home:
            return
        lot = self.saved_lot(home)
        started = float((getattr(self, "save_session", None) or {}).get("started") or 0)
        if not lot or time.time() - lot.get("at", 0) > REFRESH_EVERY or lot.get("at", 0) < started:
            if await self.g(self.at_home_now, home):     # (one from before this world session too: build mode
                try:                                     # counts from a reading of this one - session_entry)
                    info = await self.g(self.game.call, "build.get_site_info") or {}
                    lot = dict(await self.g(self.scan_lot), site=int(home), site_id=info.get("site_id"))
                    self.keep_lot(lot)
                    self.note_play_changes(home, lot)
                except Exception as e:                   # noqa: BLE001
                    log.debug("reading the house: %s", e)
        if not arrived:
            return
        if self.house_keys():
            self.catch_up_house()                        # (nothing applied again: only what never came in)
            return
        r = self.partner(None) if hasattr(self, "partner") else None
        if r and time.time() - self.asked_at > ASK_AGAIN:
            self.asked_at = time.time()
            await self.request_house(r)

    def local_additions(self, key: str, lot: dict, value: dict, base: dict, after: dict | None = None,
                        skip: list = ()) -> list[dict]:
        """What my game has (`lot`, read before an apply) that wasn't there when it was last brought to the server's
        version, isn't in `value` (the server's), isn't waiting to be sent already, and is still there `after` the
        apply (not one it took back; what the apply placed, maybe a little off, isn't counted: it wasn't before). Nor
        my copy of a move a newer version beat (the undo, `skip`): it's to go from my game, never up as bought."""
        waiting = self.pending_keep(key)["added"] + self.house_undo(key) + list(skip)
        still = (after or lot).get("objects") or []
        return [c for c in map(clean_obj, diff(lot.get("objects") or [], base["lot"].get("objects") or [])[0])
                if c and not has(value.get("objects") or [], c) and not has(waiting, c) and has(still, c)]

    def local_extras(self, key: str, lot: dict, value: dict, after: dict | None = None, skip: list = ()) -> list[dict]:
        """Without a base (the first version of this server's house my game is brought to - a new server, or one whose
        shared state started over -, merged as "add"): what my game has (`lot`, read before the apply) beyond that
        version, one for one - bought or built before the server had the house, so never compared with any version -,
        still there `after` the apply. Not what that version lists as taken out (taken_out: a removal the game didn't
        do), nor what's waiting to go up already, nor my copy of a move a newer version beat (the undo, `skip`). Sent up
        as bought: the house is then both (what it doesn't list as taken out is never removed) - only with the
        version's history whole (house_done)."""
        rec = self.hs().get("local_only")                # (nor what's kept only in my game: keep_local_only)
        lo = (rec.get("objects") or []) if isinstance(rec, dict) and rec.get("key") == key else []
        waiting = (self.pending_keep(key)["added"] + self.house_undo(key) + list(skip)
                   + taken_out(value)[0] + list(lo))
        still = (after or lot).get("objects") or []
        return [c for c in map(clean_obj, diff(lot.get("objects") or [], value.get("objects") or [])[0])
                if c and not has(waiting, c) and has(still, c)]

    def note_play_changes(self, home, lot: dict):
        """A reading of my home in play mode: what appeared since my game was last brought to the server's version
        (bought, a gift, a delivery) goes up. Only additions (the module's docstring says why). While the server has
        no version of it: my home, whole, as the first one (first_house)."""
        key = f"house:{home}:live"
        cur = self.shared.get(key) or {}
        v = int(cur.get("v") or 0)
        if not v:
            self.first_house(home, key, lot)
            return
        if self.seen(key) != v or key in self.house_next or self.build_site or self.applying_house:
            return                                       # (a version is on its way in: that apply sends them)
        base = self.house_base(key)
        if not base:                                     # (in step with it from before bases were kept: one now)
            self.reached(key, v, cur.get("value") or {}, lot)
            return
        if not self.base_current(base):
            # The first reading of the home since a save may have been loaded (from the main menu, or before this
            # program started) or the lot was replaced as a whole: what it has beyond the base may be what was deleted
            # after that save was written. My game is brought to the server's version again instead (what that save
            # lacks comes back); that apply keeps this reading as the base, and sends what's beyond the server's
            # version as bought - except what was taken out on purpose before (house_done).
            log.info("the home's first reading since a save may have been loaded: brought to the server's house (v%d) "
                     "again", v)
            self.queue_house(key, cur)
            return
        new = self.local_additions(key, lot, cur.get("value") or {}, base)
        if new:
            log.info("%d new things in the home (play mode): they go to the server", len(new))
            self.add_pending(home, added_delta(new), lot)
            self.house_task(self.push_house(home))

    def first_house(self, home, key: str, lot: dict):
        """A reading of my home just made (play mode, the multiplayer save) while the server has no version of it - a
        new server, or one whose shared state started over: my home goes up whole as its first version (house_value
        with none), so what it has (bought before the server had the house...) reaches my partner without anyone
        building. If a first version of theirs gets there first, mine is refused and theirs comes in: what my game has
        beyond it goes up then (house_done)."""
        if (not self.shared_online() or self.pending(key) or key in self.house_pushing or key in self.house_next
                or self.build_site or self.applying_house or not lot.get("objects")):
            return                                       # (my changes waiting go up whole anyway: push_dirty_house)
        log.info("the server has no version of the home yet: mine goes up as the first one")
        self.add_pending(home, added_delta([]), lot)
        self.house_task(self.push_house(home))

    async def send_house(self, b: dict):
        r = self.partner(None) if hasattr(self, "partner") else None
        if not r or not (self.writer and self.me_id):
            return
        objs = [{k: o[k] for k in OBJ_FIELDS} for o in b["objects"]]
        await self.send({"t": "event", "kind": "couple", "to": r.pid,
                         "data": {"type": "house", "id": secrets.token_hex(6), "site": b["site"],
                                  "site_id": b["site_id"], "objects": objs, "walls": b.get("walls") or [],
                                  "heights": b.get("heights") or {}}})
        log.info("sent the house to %s (%d objects, %d walls)", r.name, len(objs), len(b.get("walls") or []))

    async def request_house(self, r, force: bool = False) -> dict:
        """The panel's "get my partner's house" (and once a session when the partner's Zoi shows up). With the server's
        shared state: its version (the truth) - what never came in comes in, else a popup offers what my home lacks
        (reapply_shared_house). Without: the partner's program is asked for their home as it is now."""
        if not (self.writer and self.me_id):
            return {"error": "not connected to the server", "code": "not_connected"}
        if self.reapply_shared_house(force):
            log.info("comparing my house with the server's version")
            return {"ok": True}
        await self.send({"t": "event", "kind": "couple", "to": r.pid,
                         "data": {"type": "house_request", "id": secrets.token_hex(6)}})
        log.info("asked %s for their house", r.name)
        return {"ok": True}

    async def on_house_request(self, frm: str):
        """The partner wants our home as it is now: read it (my Zoi must be on the home lot) and send it."""
        try:
            st = await self.g(self.game.call, "build.get_mode_state") or {}
            home = await self.g(self.my_home_site)
            if not self.mp_active() or not await self.g(self.at_home_now, home):
                lot = self.saved_lot(home)
                if lot:
                    log.info("asked for the house while your Zoi is out: sending the last reading of it")
                    await self.send_house(lot)
                    await self.send_lot(self.saved_preset(home))
                else:
                    log.info("asked for the house, but your Zoi isn't at home (and it was never read): not sent")
                return
            site = home
            info = await self.g(self.game.call, "build.get_site_info") or {}     # (the lot my Zoi is on)
            lot = await self.g(self.scan_lot)
        except Exception as e:                           # noqa: BLE001
            log.info("couldn't read the house for the partner: %s", e)
            return
        lot = dict(lot, site=int(site), site_id=info.get("site_id") or st.get("site_id"))
        self.keep_lot(lot)
        await self.send_house(lot)
        await self.send_lot(self.saved_preset(site))

    # ------------------------------------------------------------------ comparing and applying
    def on_house(self, frm: str, data: dict, name: str | None = None, auto: bool = False, shared=None,
                 **kw) -> str:
        """The partner left build mode on our home (or was asked for it): compare, and ask before changing anything
        here. -> the request's id."""
        q = self.house_request(frm, data, name=name, auto=auto, shared=shared, **kw)
        self.house_task(self.prepare_q(q))
        return q["id"]

    def house_request(self, frm: str, data: dict, name: str | None = None, auto: bool = False, shared=None,
                      mode: str | None = None, redo=None, force: bool = False, item: dict | None = None,
                      scope: str | None = None) -> dict:
        """A house to bring my home to. auto: the server's version (shared: (key, version)), merged without asking;
        else a popup (redo: the server's version my game has already, compared again from the panel). scope: the
        server that version was read from (shared_scope_id; default: the current one)."""
        r = self.remotes.get(frm)
        name = name or (r.name if r else "Your partner")
        house = dict(clean_lot(data), site=data.get("site"), site_id=data.get("site_id"))
        ch = data.get("change")
        if isinstance(ch, dict):
            house["change"] = {"removed": clean_lot({"objects": ch.get("removed")})["objects"],
                               "walls_removed": clean_lot({"walls": ch.get("walls_removed")})["walls"]}
        if applied_ids(data):
            house["applied"] = applied_ids(data)
        hist = house_history(data)
        if hist is not None:
            house["history"] = hist
        self.prune_requests()
        site, carried = str(data.get("site")), None
        for q in [q for q in self.requests.values()
                  if q.get("action") == "house" and str((q.get("house") or {}).get("site")) == site]:
            carried = carried or (q.get("house") or {}).get("lot")      # (their exact house rides along)
            self.requests.pop(q["id"], None)             # (a newer version of this lot's house replaces an older one)
        if carried:
            house["lot"] = carried
        q = {"id": secrets.token_hex(6), "from": name, "from_id": frm, "action": "house", "at": time.time(),
             "local": True, "house": house, "label": None, "auto": auto, "shared": shared, "redo": redo,
             "mode": mode or ("merge" if shared else "full"), "force": force, "item": item,
             "scope": scope or self.shared_scope_id()}    # (whose version number it is: house_stale, house_done)
        if not auto:
            self.requests[q["id"]] = q                   # (the popup; the server's versions don't ask)
        return q

    async def prepare_house(self, rid: str):
        q = self.requests.get(rid)
        if q:
            await self.prepare_q(q)

    def house_stale(self, q: dict) -> bool:
        """A server version that's in my game already, or that a newer one replaces (the newest is the one applied).
        One my game was brought to before a save was loaded isn't in it any more (house_version brings it again)."""
        sh = q.get("shared") or q.get("redo")
        if not sh:
            return False
        key, v = sh
        if q.get("scope") != self.shared_scope_id():
            return True                                  # (another server's version: what this one has comes instead)
        if int((self.shared.get(key) or {}).get("v") or 0) > v:
            return True
        if not q.get("shared") or self.seen(key) < v:
            return False
        base = self.house_base(key)
        return self.seen(key) > v or not base or (self.base_current(base) and not self.house_undo(key))

    def plan_state(self, key: str, live: str) -> dict:
        """What a plan is worked out against, as it is now: the base, my changes waiting to go up (deltas, and all
        together: keep) and my copies of moves a newer version beat (undo). An apply works out its later steps (after
        the walls) against the same: my change that lands meanwhile is folded into the base and waits no more
        (confirm_landed) - against that, the version being put in would read as taking it back out."""
        b = self.house_base(key)
        return {"base": dict(b) if b else None, "deltas": list((self.pending(live) or {}).get("deltas") or []),
                "keep": self.pending_keep(live), "undo": self.house_undo(key)}

    def plan_for(self, q: dict, mine: dict, hold: bool = True, state: dict | None = None) -> tuple[dict, list]:
        """The plan to bring my game to q's house (house_plan), with: what earlier applies couldn't do, tried again;
        and my copies of moves a newer version beat taken back out - noted by my push (undo), or found here among my
        changes still waiting to go up: put on top of this version (fold, as my push would), a move of something my
        partner moved or removed meanwhile is dropped, so my copy goes and theirs is placed (else both stay). With
        hold, the plan whose removals are done: q keeps what of the undo it covered, the beaten moves (house_done) and
        what it was worked out against (plan_state: state, given, is that of an earlier plan of the same apply)."""
        key = (q.get("shared") or q.get("redo") or (None,))[0]
        mode = q.get("mode") or "full"
        h = q["house"]
        live = f"house:{h.get('site')}:live"
        st = state or self.plan_state(key, live)
        base = st["base"] if mode == "merge" else None
        if mode == "merge" and not base:
            mode = "add"                                 # (no base: never remove what it doesn't list as removed)
        plan = house_plan(h, mine, mode, base, st["keep"])
        plan["mode"], plan["base"], plan["keep"] = mode, base, st["keep"]
        T, M, Tw, Mw = h["objects"], mine.get("objects") or [], h["walls"], mine.get("walls") or []
        left = [e for e in self.hs().get("leftover") or [] if e["tries"] < MAX_TRIES]
        if mode == "merge" and left:                     # (what an earlier apply couldn't do: tried again, a few times)
            plan["place"] += [o for o in diff(T, M)[0] if not has(plan["place"], o)
                              and any(e.get("kind") == "place" and same(e["item"], o) for e in left)]
            plan["build"] += [t for t in Tw if Mw and not covered(t, Mw) and t not in plan["build"]
                              and any(e.get("kind") == "build" and same_item("build", e["item"], t) for e in left)]
        undo, pairs = (list(st["undo"]) if mode != "fill" else []), []
        if mode != "fill":
            v = h
            for d in st["deltas"]:
                v, _, u = fold(v, d, pairs)
        copies = list(undo) + [m for _, m in pairs]
        if copies:                                       # (theirs stays, mine goes)
            have = [m for m in M if not any(m is r for r in plan["remove"])]
            plan["remove"] += [m for m in matched(copies, have) if not has(T, m)]
        if mode == "merge" and self.lot_from_exact(base):
            # (my lot is my partner's exact house, older than this version: what it brought back that had been taken out
            # on purpose goes again, and the storeys are brought to this version's heights - not my changes on their way)
            keep = st["keep"]
            extra = [m for m in diff(M, base["lot"].get("objects") or [])[0] if not has(T, m)
                     and not has(keep["added"], m) and not any(m is r for r in plan["remove"])]
            back, back_w = self.came_back(key, extra, mine, h)
            plan["remove"] += back
            plan["delete"] += [w for w in back_w if w not in plan["delete"]]
            plan["storeys"] = plan_storeys(h, mine.get("heights") or {}, "merge", None, keep)
            plan["relive"] = True
            if (back or back_w) and hold:
                log.info("my partner's exact house brought back %d objects and %d walls taken out of the home since it "
                         "was saved: they go again (the shared house is newer)", len(back), len(back_w))
        if hold:
            q["undo_used"], q["beaten"], q["plan_mode"], q["plan_state"] = list(undo), pairs, mode, st
        return self.hold_back(plan, bool(q.get("force"))) if hold else (plan, [])

    def hold_back(self, plan: dict, force: bool) -> tuple[dict, list]:
        """Leave out what earlier applies couldn't do: given up on (MAX_TRIES) unless forced, and tried before unless
        there's other work to do anyway (never build mode just for that). -> (the plan, the leftovers left out)."""
        left = [] if force else (self.hs().get("leftover") or [])
        if not left:
            return plan, []
        out, retry, held = {k: [] for k in KINDS}, {k: [] for k in KINDS}, []
        for kind in KINDS:
            for it in plan[kind]:
                e = next((e for e in left if e.get("kind") == kind and same_item(kind, e["item"], it)), None)
                if e is None:
                    out[kind].append(it)
                elif e["tries"] >= MAX_TRIES:
                    held.append(e)
                else:
                    retry[kind].append((it, e))
        if any(out.values()) or plan["storeys"]:
            for kind in KINDS:
                out[kind] += [it for it, _ in retry[kind]]
        else:
            held += [e for kind in KINDS for _, e in retry[kind]]
        return dict(plan, **out), held

    def given_up(self, kind: str, it: dict) -> bool:
        return any(e.get("kind") == kind and e["tries"] >= MAX_TRIES and same_item(kind, e["item"], it)
                   for e in self.hs().get("leftover") or [])

    async def prepare_q(self, q: dict):
        """Work out what differs (with my home's reading) and put it in the popup's text; the server's versions are
        brought in without asking (merged: the module's docstring has the rule)."""
        h, rid, popup = q["house"], q["id"], not q.get("auto")
        over = self.__dict__.pop("_house_over", None) if q.get("auto") else None    # (house_overtaken)
        try:
            home = await self.g(self.my_home_site)
        except Exception as e:                           # noqa: BLE001
            if not popup:
                raise                                    # (the queue tries it again in a moment)
            log.info("couldn't read which lot is my home: %s", e)
            return
        if not home or int(home) != int(h.get("site") or -1):
            self.requests.pop(rid, None)
            log.info("%s's house is lot %s, mine is %s: not the same home, nothing to do", q["from"], h.get("site"),
                     home)
            return
        async with self.house_lock:
            if popup and self.requests.get(rid) is not q:
                return                                   # (a newer one replaced it meanwhile)
            if self.house_stale(q):
                self.requests.pop(rid, None)
                return
            mine = await self.read_home(home)
            if self.house_stale(q):                      # (overtaken while my home was read: the newest comes next)
                self.requests.pop(rid, None)
                return
            if mine is not None:
                plan, _ = self.plan_for(q, mine)
                if not plan_size(plan):
                    self.requests.pop(rid, None)
                    log.info("%s's house is the same as mine%s", q["from"],
                             " (what's left differs only where my game keeps its own)" if q.get("shared") else "")
                    self.house_done(q, mine, mine)
                    return
                q["label"] = house_label(plan["place"], plan["remove"], len(plan["build"]) + len(plan["delete"]))
        label = f": {q['label']}" if q["label"] else ""
        if q.get("redo"):
            self.system_line(f"Your home differs from the shared one{label}")
        elif q.get("again"):                             # (nobody changed it: a save was loaded - "House updated" says)
            log.info("the save loaded lacks some of the shared house%s: brought back", label)
        elif not q.get("shared") or self.__dict__.get("_house_said") != q["shared"]:
            self._house_said = q.get("shared")
            if over != ((q.get("shared") or (None,))[0], q["label"]):     # (not again for the version that overtook
                q["said"] = True                                           # one said just before: the same change)
                self.system_line(f"{q['from']} changed the house{label}")
        if q.get("auto"):
            await self.apply_house(q)
        elif self.always_yes("house", None):
            await self.couple_respond(rid, True)

    def house_defer(self, q: dict):
        """It can't be applied now (build mode...): the server's version waits in the queue, a popup stays open."""
        self.house_retry_at = time.time() + 2
        if q.get("auto") and q.get("shared") and q.get("item"):
            self.queue_house(q["shared"][0], q["item"])
        elif not q.get("auto"):
            q["at"] = time.time()
            self.requests[q["id"]] = q

    def house_done(self, q: dict, mine: dict, after: dict, edits: dict | None = None):
        """A server version (or the panel's redo of it) is in my game now: it's the base; what the apply took out is
        remembered (note_gone); what my game has that the server doesn't know about (bought in play mode...) goes up,
        and so do the edits the player made in the build mode the apply had open (edits: apply_edits; the base's
        reading, `after`, has them already, so nothing else would ever send them). When a save may have been loaded
        since the base's reading (base_current), what my game has beyond the server's version that had been taken out
        of the home on purpose before came back with that save (came_back): it stays only in my game (keep_local_only,
        said in the chat), never sent. Without a base (merged as "add": the first version of this server's house my game
        is brought to), what my game has beyond it goes up the same way (local_extras), but for what had been taken out
        on purpose before - and only when the version's history is whole: else it may be what my partner moved or took
        out before my game ever had a base, and it stays in my game only (said in the chat)."""
        sh = q.get("shared") or q.get("redo")
        if not sh:
            return
        key, v = sh
        site, new, kept = key.split(":")[1], [], False
        ao, aw = after.get("objects") or [], after.get("walls") or []
        if after is not mine:                            # (no reading, or one that came back empty: nothing went)
            self.note_gone(key, diff(ao, mine.get("objects") or [])[1] if ao else [],
                           pieces(mine.get("walls") or [], aw) if aw else [])
        beaten = q.get("beaten") or []
        n = self.drop_beaten(key, beaten, after)         # (my moves this version beat: my copies are out of my game)
        if n:
            log.info("%d of my moves waiting to go up were beaten by %s's version: dropped", n, q["from"])
            self.system_line(f"{n} of your changes to the house weren't kept: {q['from']} had changed the same things")
        if self.still_here(key, v, q.get("scope")):      # (else the server moved on under the apply: see reached)
            base = self.house_base(key)
            merge = bool(base) and q.get("mode") == "merge"
            add = not merge and q.get("mode") == "merge" and q.get("plan_mode") == "add"
            skip = [m for _, m in beaten]
            if merge:
                new = self.local_additions(key, mine, q["house"], base, after, skip=skip)
            elif add:
                new = self.local_extras(key, mine, q["house"], after, skip=skip)
            if (merge and not self.base_current(base)) or add:
                back, back_w = self.came_back(key, new, after, q["house"])
                new = [o for o in new if not any(o is b for b in back)]
                rec = self.hs().get("local_only")        # (what's kept only in my game already isn't said again)
                if isinstance(rec, dict) and rec.get("key") == key:
                    back = [o for o in back if not has(rec.get("objects") or [], o)]
                    back_w = [w for w in back_w if not covered(w, rec.get("walls") or [])]
                if back or back_w:
                    n = len(back) + len(back_w)
                    why = ("back with the exact house, and the game didn't take them out again"
                           if merge and self.lot_from_exact(base) else "back with an older save?")
                    log.info("%d things in my home that the server's house doesn't have had been taken out of it before "
                             "(%d objects, %d walls): %s - kept in my game only, not sent", n, len(back), len(back_w),
                             why)
                    self.keep_local_only(key, back, back_w)
                    exact = " (and your exact house isn't shared while it has them)" if self.cfg.get("exact_house",
                                                                                                    True) else ""
                    self.system_line(f"Your home has {n} thing{'s' if n != 1 else ''} that had been taken out of the "
                                     f"shared house ({why}): kept in your game only{exact}")
            if add and new and not (q["house"].get("history") or {}).get("complete"):
                # (the version doesn't list all that was taken out of the house since its first version - an older
                # program made one of them, or more was taken out than it lists: what my game has beyond it may be what
                # my partner moved or took out before I had any base - up, it would come back in both games for good)
                n = len(new)
                log.info("%d things in my home aren't in the server's house, whose history isn't known whole: kept in "
                         "my game only, not sent", n)
                self.keep_local_only(key, new, [])
                exact = " (and your exact house isn't shared while it has them)" if self.cfg.get("exact_house",
                                                                                                True) else ""
                self.system_line(f"Your home has {n} thing{'s' if n != 1 else ''} the shared house doesn't have, maybe "
                                 f"moved or taken out by {q['from']} before you joined: kept in your game only{exact}")
                new = []
            kept = self.reached(key, v, q["house"], after, q.get("scope"), used=q.get("undo_used") or [])
        self.local_only_left(key, after)                 # (what's gone from the home is forgotten)
        lot = dict(after, site_id=q["house"].get("site_id"))
        if new:
            log.info("%d things in my home aren't in the server's house (bought, or from before it had the house): "
                     "they go up", len(new))
            self.add_pending(site, added_delta(new), lot)
        if edits:
            log.info("changed in build mode while my partner's house was put in: %d added, %d removed, %d wall pieces "
                     "built, %d knocked down, %d storeys: they go up", len(edits["added"]), len(edits["removed"]),
                     len(edits["walls_add"]), len(edits["walls_del"]), len(edits["heights"]))
            self.add_pending(site, edits, lot)
        if new or edits:
            self.house_task(self.push_house(site))
        if kept and self.house_undo(key) and self.shared.get(key):
            self.queue_house(key, self.shared[key])      # (noted by my push while this apply was under way: next)
        self.kick_exact(after)                           # (an exact house that waited for my game to have this one)

    async def apply_house(self, q: dict) -> dict:
        """Enter build mode on the lot, remove and place what differs, leave build mode (it saves the lot). One at a
        time, and never while I'm in build mode (it waits for that)."""
        h = q["house"]
        if h.get("lot"):
            self.stash_lot(q["from"], h.pop("lot"))
        if h.get("objects") is None:                     # (only their exact house: it comes in on reopen)
            return {"state": "done", "placed": 0, "removed": 0, "walls": 0, "failed": 0, "exact": "on_reopen"}
        async with self.house_lock:
            return await self._apply_house(q)

    async def leave_build_mode(self) -> bool:
        call = self.game.call
        try:
            await self.g(call, "build.exit_mode")
            for _ in range(60):
                await asyncio.sleep(0.5)
                st = await self.g(call, "build.get_mode_state") or {}
                if st.get("phase") == "none":
                    self.in_build_mode = False
                    return True
        except Exception as e:                           # noqa: BLE001
            log.warning("leaving build mode: %s", e)
        return False

    def house_overtaken(self, q: dict):
        """q's version was overtaken before anything of it was done: the chat line said for it ("<name> changed the
        house: ...") isn't said again for the next one, when that's the same (prepare_q)."""
        if q.get("said") and q.get("shared"):
            self._house_over = (q["shared"][0], q.get("label"))

    async def _apply_house(self, q: dict) -> dict:
        call = self.game.call
        if self.house_stale(q):
            self.house_overtaken(q)
            return {"state": "done", "placed": 0, "removed": 0, "walls": 0, "failed": 0, "superseded": True}
        why = self.house_hold()
        if not why:
            try:
                if (await self.g(call, "build.get_mode_state") or {}).get("in_build_mode"):
                    self.in_build_mode, why = True, "in build mode"       # (the player opened it since the last tick)
            except Exception as e:                       # noqa: BLE001
                why = f"the game didn't answer: {e}"
        if why:
            log.info("%s's house waits (%s)", q["from"], why)
            self.house_defer(q)
            return {"state": "deferred", "message": f"it's applied once you can ({why})"}
        run = {"entered": False, "left": False, "mine": None, "after": None, "edits": None}
        self.applying_house = True                       # (house_tick leaves build mode alone: it's mine now)
        try:
            res = await self._apply_in_build_mode(q, run)
        except Exception as e:                           # noqa: BLE001
            log.warning("applying the house failed: %s", e)
            res = {"state": "failed", "message": str(e)}
        finally:
            if run["entered"] and not run["left"]:       # (never leave the player in a paused build mode)
                await self.leave_build_mode()
            self.applying_house = False
        if res.get("superseded"):                        # (overtaken while build mode opened: nothing was done, the
            self.house_overtaken(q)                      # newest comes next - house_work)
            return res
        if res.get("state") == "done":                   # (only then is that version in my game)
            mine, after = run["mine"], run["after"]
            self.house_done(q, mine, after if after is not None else mine, run["edits"])
        elif q.get("auto") and q.get("shared") and self.house_failure(*q["shared"]):
            self.house_retry_at = time.time() + 5        # (tried again in a moment, a few times)
            self.queue_house(q["shared"][0], q["item"])
        return res

    async def _apply_in_build_mode(self, q: dict, run: dict) -> dict:
        h, call = q["house"], self.game.call
        await self.g(call, "build.enter_mode", site_id=str(h.get("site_id") or ""))
        run["entered"] = True
        for _ in range(120):
            await asyncio.sleep(0.5)
            st = await self.g(call, "build.get_mode_state") or {}
            if st.get("phase") == "ready":
                break
        else:
            return {"error": "build mode didn't open", "code": "build_mode"}
        if int(st.get("site_entity_id") or -1) != int(h.get("site") or -2):    # (never edit another lot)
            log.warning("build mode opened lot %s, not the home (%s): nothing changed", st.get("site_entity_id"),
                        h.get("site"))
            return {"state": "failed", "message": "build mode opened another lot"}
        mine = run["mine"] = await self.g(self.scan_lot)
        if self.house_stale(q):
            # (a newer version came while build mode opened - my own change going up, say: confirm_landed has put it on
            # the base, and worked out against that base this older version would take it back out of my game)
            log.info("%s's house was overtaken while build mode opened: nothing done, the newest comes next", q["from"])
            return {"state": "done", "placed": 0, "removed": 0, "walls": 0, "failed": 0, "superseded": True}
        plan, held = self.plan_for(q, mine)
        failed = 0
        for o in plan["remove"]:
            try:
                await self.g(call, "build.remove_object", entity_id=str(o["entity_id"]))
            except Exception as e:                   # noqa: BLE001
                failed += 1
                log.debug("couldn't remove %s: %s", o["table_id"], e)
        for w in plan["delete"]:                     # (walls: the objects that go with them are gone by now)
            try:
                await self.g(call, "building.construct_wall", start_x=w["a"][0], start_y=w["a"][1],
                             end_x=w["b"][0], end_y=w["b"][1], floor_level=w["floor"], is_delete=True)
            except Exception as e:                   # noqa: BLE001
                failed += 1
                log.debug("couldn't delete a wall: %s", e)
        for w in sorted(plan["build"], key=lambda w: w["floor"]):
            hh = h["heights"].get(str(w["floor"])) or {}
            kw = {"wall_height": float(hh["wall_height"])} if hh.get("wall_height") else {}
            if w["floor"] == 0 and hh.get("base_height"):
                kw["base_height"] = float(hh["base_height"])
            try:
                await self.g(call, "building.construct_wall", start_x=w["a"][0], start_y=w["a"][1],
                             end_x=w["b"][0], end_y=w["b"][1], floor_level=w["floor"], **kw)
            except Exception as e:                   # noqa: BLE001
                failed += 1
                log.debug("couldn't build a wall: %s", e)
        storeys = 0
        if h["walls"]:
            try:                                     # (new storeys have their heights only once their walls stand)
                keep = plan["keep"]                      # (as the plan was worked out: plan_state)
                for f, height, at in plan_storeys(h, await self.g(self.scan_heights), plan["mode"],
                                                  None if plan.get("relive") else plan["base"], keep):
                    storeys += 1                     # (counted first: one that fails may have got part of the way)
                    await self.g(call, "building.set_storey_wall_height", floor_level=f, target_height=height,
                                 X=at[0], Y=at[1])
            except Exception as e:                   # noqa: BLE001
                failed += 1
                log.debug("couldn't set a storey's height: %s", e)
        place, mid = plan["place"], None
        if plan["build"] or plan["delete"] or storeys:   # (doors/windows on deleted walls went too, and a storey's
            mid = await self.g(self.scan_lot)            # new height moves what's above it: read again)
            again, _ = self.plan_for(q, dict(mine, objects=mid["objects"]), hold=False, state=q.get("plan_state"))
            place = [o for o in again["place"] if not self.given_up("place", o)]
            plan["place"] = place
        for o in place:
            try:
                await self.g(call, "building.place_object", object_table_id=o["table_id"], X=o["x"], Y=o["y"],
                             Z=o["z"], floor_level=o["floor"], Yaw=o["yaw"])
            except Exception as e:                   # noqa: BLE001
                failed += 1
                log.debug("couldn't place %s: %s", o["table_id"], e)
        after = None
        try:
            after = run["after"] = await self.g(self.scan_lot)
            self.keep_lot(dict(after, site=int(h["site"]), site_id=h.get("site_id")))
        except Exception as e:                       # noqa: BLE001
            log.debug("reading the house after applying: %s", e)
        if after is not None:                        # (what the player did meanwhile, in the build mode on screen)
            before = mid or dict(mine, objects=[o for o in mine["objects"] if not any(o is r for r in plan["remove"])])
            run["edits"] = apply_edits(plan, before, after)
        left_over = self.note_leftover(plan, held, after, q["from"]) if after is not None else 0
        run["left"] = True
        await self.leave_build_mode()
        walls = len(plan["build"]) + len(plan["delete"])
        log.info("applied %s's house (%s): %d placed, %d removed, %d walls built, %d deleted, %d failed, %d left "
                 "as they are", q["from"], plan["mode"], len(place), len(plan["remove"]), len(plan["build"]),
                 len(plan["delete"]), failed, left_over)
        if plan_size(plan) or failed:
            self.system_line("House updated: " + house_label(place, plan["remove"], walls) +
                             (f" ({max(failed, left_over)} couldn't be changed)" if failed or left_over else ""))
        return {"state": "done", "placed": len(place), "removed": len(plan["remove"]), "walls": walls,
                "failed": failed, "left": left_over}

    def note_leftover(self, plan: dict, held: list, after: dict, name: str) -> int:
        """After an apply: what the game didn't take (by the reading after it) is counted, so it's tried again only a
        few times, along with other changes; said once when it's given up on. -> how many things were left."""
        old = self.hs().get("leftover") or []
        ao, aw = after.get("objects") or [], after.get("walls") or []
        ids = {str(o.get("entity_id")) for o in ao}
        failed = {"place": [o for o in plan["place"] if not has(ao, o)],
                  "remove": [m for m in plan["remove"] if str(m.get("entity_id")) in ids],
                  "build": [w for w in plan["build"] if aw and not covered(w, aw)],
                  "delete": [w for w in plan["delete"] if aw and covered(w, aw)]}
        new = list(held)
        for kind, items in failed.items():
            for it in items:
                e = next((e for e in old if e.get("kind") == kind and same_item(kind, e["item"], it)), None)
                new.append({"kind": kind, "item": clean_obj(it) if kind in ("place", "remove") else it,
                            "tries": (e["tries"] if e else 0) + 1, "told": bool(e and e.get("told"))})
        newly = [e for e in new if e["tries"] >= MAX_TRIES and not e.get("told")]
        if newly:
            for e in newly:
                e["told"] = True
            n = len(newly)
            log.info("%d things of %s's house can't be put in this game: left as they are", n, name)
            self.system_line(f"{n} thing{'s' if n != 1 else ''} from {name}'s house couldn't be put in your game: "
                             f"left as {'they are' if n != 1 else 'it is'}")
        self.hs()["leftover"] = [e for e in new if e.get("item")][-MAX_LEFTOVER:]
        self.save_hs()
        return sum(len(v) for v in failed.values())
