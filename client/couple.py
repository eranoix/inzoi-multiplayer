"""Features for a couple playing together (mixed into the Companion, like servers.Servers):

- "Go to my partner" (/partner/goto): the player's Zoi is teleported next to the partner's puppet (the same call as
  the panel's "Join"), or to the partner's last known spot when their puppet isn't in this world yet.
- "Invite to my house" (/partner/invite): the partner's puppet is let through this home's doors (the `admit` op) and
  the partner gets an invitation; accepting it takes their Zoi to this player (the same "go to" on their side).
- Couple actions (/partner/action: hug, kiss, dance, wave): a request to the partner through the server's generic
  `event` relay (kind "couple"); their panel lists it (/status "requests") and answers (/partner/respond).
  On accept:
    * hug / kiss (done to someone): the asker's Zoi starts the social on the partner's puppet, on the asker's PC
      only. The live sync carries it over: on the partner's PC the asker's puppet does it to the partner's own Zoi
      (Companion.set_state, act with "player"), which answers it like any social. Starting it on both PCs would make
      each Zoi start its own hug and the two would fight over the same pair.
    * dance / wave (alone): both PCs start it on their own player's Zoi; each one shows up on the other PC through
      the live sync too.
  The interaction ids aren't documented: they're learned from what the players' Zois do (a hug done by hand once
  teaches the program the hug's id), can be set in config.json "couple_bags", and fall back to guesses.
- Your multiplayer save: each player plays their own (no shared world: see Companion.shared_world_on), with backups
  (saves.backup) before each load, "undo the last save" (/world/undo: the newest local backup) and status for the
  panel. The shared-world paths (the server's previous world version) are only reached by the tests.

Event data on the wire: {"t":"event","kind":"couple","to":<player id>,"data":{...}} with data
    {"type":"request","id","action","name","site"?}     action: hug | kiss | dance | wave | invite
    {"type":"reply","id","accept"}
"""
import asyncio
import json
import logging
import re
import secrets
import time

import saves
import world

log = logging.getLogger("couple")

ACTIONS = ("hug", "kiss", "dance", "wave", "holdhands", "selfie", "spin")
TARGETED = ("hug", "kiss", "holdhands", "selfie", "spin")   # done to the partner's Zoi (it must be here)
TOGETHER = ("dance", "wave")               # with the partner's Zoi when it's here, else alone
# Family milestones: asked like the couple actions; on "yes" each PC makes it so in its own save (both saves agree).
FAMILY = ("movein", "engage", "marry", "baby")
REQUEST_KINDS = ACTIONS + ("invite",) + FAMILY + ("interaction",)
ACCEPTED_FOR = 25                          # s after a "yes" in which the partner's Zoi doing it here isn't asked again
# Every interaction the game offers between the two Zois (interaction.list_available, with the game's own names),
# sorted into the panel's groups by their ids. First match wins; the rest is "talk".
GROUPS = (
    ("conflict", re.compile(r"_B\d|GetOut|Theft|Criminal|Imprison|RemoveGroupMember|Tease|Borrow_Money|Insult|Fight|"
                            r"Slap", re.I)),
    ("talk", re.compile(r"InformationReveal|_R\d+(_\d+)?$|Skill_|Sharing_|SLM_\w*Talk|_Talk", re.I)),
    ("romance", re.compile(r"Kiss|Peck|Romance|Romantic|Love|Lover|Flirt|Propos|Marr|Engage|Date|Cohabit|Hug|HoldHand|"
                           r"Woo", re.I)),
    ("fun", re.compile(r"HighFIve|HighFive|Selfie|TakePic|Dance|Buddy|MoneyGun|Joke|Toast|Pose|Game|Play|Prank|Gift",
                       re.I)),
    ("business", re.compile(r"Business|Namecard|HandShake|Box_Give|Errand|Info_Share|Invest", re.I)),
    ("friendship", re.compile(r"Friendship|Friend|CheerUp|Karma|SLM|HopeLife|Sharing|Compliment|Comfort", re.I)),
)
GROUP_ORDER = ("romance", "fun", "friendship", "talk", "business", "conflict")
FAMILY_LINES = {"movein": "You live together now", "engage": "You're engaged!", "marry": "You're married!",
                "baby": "A baby is on the way!"}
REQUEST_TTL = 90                           # seconds a request sent stays open on the sender's side
ANSWER_WITHIN = 5                          # seconds to answer a request here: not answered by then, it's accepted
MAX_PENDING = 20
BAG_ID = re.compile(r"^(anim:)?[A-Za-z0-9_]{1,80}$")
# Unverified guesses for doing it alone (no list of the game's interaction ids is available offline).
GUESS_BAGS = {"hug": "Social_Hug", "kiss": "Social_Kiss", "dance": "Dance_Solo", "wave": "Social_Wave"}
# Done to the partner's Zoi: the game's own ids, checked in the real game (31/10/2026) with interaction.list_available
# once the two Zois are close (relationships.set_value_between, see bond_puppet). {g}: Male/Female of each Zoi.
WITH_PARTNER = {
    "hug": ("Co_DeepHug_{me}To{them}_Success", "Co_HugUp_{me}To{them}_Success", "Co_HugKiss_{me}To{them}_Romance"),
    "kiss": ("Co_Peck_{me}To{them}_Success", "Co_KissForehead_{me}To{them}_Success", "Co_DeepKiss_{me}To{them}_Success",
             "Co_HugKiss_{me}To{them}_Romance"),
    "dance": ("Co_CoupleDance", "Co_LoverHugDance", "Co_Dance_Together_{me}To{them}_Friendship", "CoDance"),
    "wave": ("Co_HighFIve_{me}To{them}_Friendship", "Co_Friendship_G04_Start", "Co_Friendship_G04_Start2"),
    "holdhands": ("Co_HoldHand", "Co_HoldHands_{me}To{them}_Romance"),
    "selfie": ("Co_Friend_Selfie_Together_{me}To{them}_Friendship", "Co_Lover_TakePic_{me}To{them}_Romance"),
    "spin": ("Co_PicturePose_Couple", "Co_LoverHugDance"),
}
ALONE_BAGS = {"dance": "Next_Dance_Start"}  # (checked in the real game)
# The quick actions, matched against what the game offers right now between the two Zois (first pattern that finds
# something wins): married couples get "Family_Spouse" ids, friends "Friendship" ones... never a made-up id.
QUICK_PATTERNS = {
    "hug": (r"DeepHug", r"HugUp", r"(?<!Kiss)Hug(?!Kiss)", r"HugKiss"),
    "kiss": (r"DeepKiss", r"Peck", r"KissForehead", r"(?<!Hug)Kiss", r"HugKiss"),
    "dance": (r"CoupleDance", r"LoverHugDance", r"Dance_Together", r"Dance"),
    "wave": (r"HighF",),
    "holdhands": (r"^Co_HoldHand$", r"HoldHand"),
    "selfie": (r"Selfie", r"Lover_TakePic", r"TakePic"),
    "spin": (r"PicturePose_Couple", r"Spin", r"Twirl", r"LoverHugDance"),
}
LEARN = {"hug": re.compile(r"hug", re.I), "kiss": re.compile(r"kiss", re.I), "dance": re.compile(r"dance", re.I),
         "wave": re.compile(r"(?<!micro)wave", re.I)}             # (not Use_Microwave)
ACTION_NAMES = {"hug": "hug", "kiss": "kiss", "dance": "dance", "wave": "high five", "holdhands": "hold hands",
                "selfie": "selfie", "spin": "spin", "invite": "invitation", "movein": "moving in together",
                "engage": "engagement", "marry": "wedding", "baby": "baby", "interaction": "interaction"}  # (chat)
VERBS = {"hug": "hug you", "kiss": "kiss you", "dance": "dance with you", "wave": "high-five you",
         "holdhands": "hold your hand", "selfie": "take a selfie with you", "spin": "spin you around in a hug",
         "invite": "invite you to their home", "movein": "move in with you (one family, one home)",
         "engage": "get engaged to you", "marry": "marry you", "baby": "have a baby with you",
         "interaction": "interact with you"}


class Couple:
    def couple_init(self):
        self.requests: dict[str, dict] = {}    # incoming, unanswered: id -> {id, from, from_id, action, at, site?}
        self.sent: dict[str, dict] = {}        # outgoing: id -> {id, to, to_id, action, at, state, message}
        self.undo_state = None                 # the last "undo": {state, source, message, at}
        self.upload_wait = None                # when an upload last waited for the game to finish writing the save
        self.last_backup = None                # {name, reason, at} of the newest backup made in this run
        bags = self.cfg.get("couple_bags") or {}
        for action in [a for a, bag in bags.items() if a in LEARN and not LEARN[a].search(bag)]:
            del bags[action]                   # learned wrong by an older version (a microwave as "wave")

    # ------------------------------------------------------------------ helpers
    def partner(self, key=None):
        """The remote player `key` (a player id), or with no key the only other player online."""
        if key:
            return self.remotes.get(str(key))
        return next(iter(self.remotes.values())) if len(self.remotes) == 1 else None

    def couple_bag(self, action: str) -> str:
        return ((self.cfg.get("couple_bags") or {}).get(action) or ALONE_BAGS.get(action) or GUESS_BAGS.get(action)
                or WITH_PARTNER[action][0].format(me="Male", them="Female"))

    def bond_puppet(self, eid: int):
        """The partner's Zoi just came into my world: the two are a couple, so their relationship is set high
        (both ways). Without it the game offers a stranger's interactions only: no hug, kiss or couple dance."""
        if not self.my_entity or not eid or eid == self.my_entity:
            return
        try:
            self.game.call("relationships.set_value_between", owner_id=self.my_entity, target_id=eid, Value=100)
        except Exception as e:                           # noqa: BLE001 (a game without it: guesses only)
            log.debug("couldn't set the relationship with puppet %s: %s", eid, e)

    def family_zoi(self, r) -> int | None:
        """The partner's Zoi as a member of my household in this save (same first and last name), or None. Looked
        up at most every 20 s per partner."""
        prof = (r.profile or {}) if r else {}
        name = ((prof.get("first_name") or "").strip().casefold(), (prof.get("last_name") or "").strip().casefold())
        if not name[0]:
            return None
        cache = self.__dict__.setdefault("_family", {})
        hit = cache.get(r.pid)
        if hit and hit[2] == name and time.time() - hit[1] < 20:
            return hit[0]
        found = hit[0] if hit and hit[2] == name else None
        try:
            res = self.game.call("household.get_members") or {}
            found = next((int(m["entity_id"]) for m in res.get("members") or []
                          if m.get("entity_id") and int(m["entity_id"]) != self.my_entity
                          and ((m.get("first_name") or "").strip().casefold(),
                               (m.get("last_name") or "").strip().casefold()) == name), None)
        except Exception as e:                           # noqa: BLE001 (keep the last answer)
            log.debug("couldn't list my household: %s", e)
        cache[r.pid] = (found, time.time(), name)
        return found

    def bag_with(self, action: str, eid: int, them: str | None) -> str | None:
        """The interaction id for doing `action` to the partner's Zoi (entity eid): the first of WITH_PARTNER (or
        the learned one) that the game offers right now; None if it offers none of them."""
        me = (self.__dict__.get("my_gender") or "Male").title()
        them = (them or "Female").title()
        want = [b.format(me=me, them=them) for b in WITH_PARTNER[action]]
        learned = (self.cfg.get("couple_bags") or {}).get(action)
        if learned and learned not in want:
            want.append(learned)                         # (learned from either Zoi: maybe the other way round)
        try:
            avail = self.game.call("interaction.list_available", entity_id=self.my_entity, target_entity_id=eid)
            offered = {i.get("interaction_bag_id") for i in (avail or {}).get("interactions") or [] if i.get("enabled", True)}
        except Exception:                                # noqa: BLE001
            offered = set()
        if not offered:
            return want[0]                               # (no list from the game: the learned or the checked id)
        return next((b for b in want if b in offered), None) or self.quick_match(action, offered)

    def quick_match(self, action: str, offered) -> str | None:
        """The interaction the game offers now that best fits a quick action (QUICK_PATTERNS), or None."""
        for pat in QUICK_PATTERNS.get(action, ()):
            rx = re.compile(pat, re.I)
            hit = sorted(b for b in offered if b and rx.search(b))
            if hit:
                return hit[0]
        return None

    def learn_bag(self, act: dict | None):
        """An interaction one of the players' Zois is doing: remember it as the hug/kiss/dance/wave id if it is one.
        (The interaction ids are the game's own: learning beats guessing.)"""
        bag = (act or {}).get("bag")
        if not bag or not BAG_ID.match(bag) or bag.startswith("anim:"):
            return
        for action, rx in LEARN.items():
            if rx.search(bag) and (action not in TARGETED or act.get("player")):
                bags = self.cfg.setdefault("couple_bags", {})
                if bags.get(action) != bag:
                    bags[action] = bag
                    log.info("learned the %s interaction: %s", action, bag)
                    self.save_cfg()
                return

    async def answer_loop(self):
        """A request nobody answered within ANSWER_WITHIN seconds is accepted (the couple plays together: a popup left
        alone - away from the keyboard, the panel closed - shouldn't hold the partner up)."""
        while True:
            await asyncio.sleep(1)
            try:
                now = time.time()
                for rid, q in list(self.requests.items()):
                    if now - q["at"] >= ANSWER_WITHIN and not q.get("auto"):
                        q["auto"] = True
                        log.info("%s's request wasn't answered in %d s: accepted (%s %s)", q.get("from"),
                                 ANSWER_WITHIN, q.get("action"), q.get("bag") or "")
                        asyncio.get_running_loop().create_task(self.couple_respond(rid, True))
            except Exception as e:                       # noqa: BLE001 - never worth stopping the loop
                log.debug("answer loop: %s", e)

    def prune_requests(self):
        now = time.time()
        for rid in [rid for rid, q in self.requests.items() if now - q["at"] > REQUEST_TTL]:
            del self.requests[rid]
        for s in self.sent.values():
            if s["state"] == "waiting" and now - s["at"] > REQUEST_TTL:
                s["state"] = "expired"
        for rid in [rid for rid, s in self.sent.items() if now - s["at"] > 600]:
            del self.sent[rid]

    def couple_view(self) -> dict:
        """For /status: the requests to answer, and the state of the ones this player sent."""
        self.prune_requests()
        return {"requests": [{k: q.get(k) for k in ("id", "from", "from_id", "action", "at", "bag", "label")}
                             for q in sorted(self.requests.values(), key=lambda q: q["at"])],
                "pending_lot": {k: (self.cfg.get("pending_lot") or {}).get(k) for k in ("from", "at")}
                if self.cfg.get("pending_lot") else None,
                "couple": {"sent": [{k: s.get(k) for k in ("id", "to", "to_id", "action", "name", "at", "state", "message")}
                                    for s in sorted(self.sent.values(), key=lambda s: s["at"])][-5:],
                           "bags": {a: {"id": self.couple_bag(a),
                                        "learned": bool((self.cfg.get("couple_bags") or {}).get(a))}
                                    for a in ACTIONS}}}

    # ------------------------------------------------------------------ go to my partner / invite
    async def partner_goto(self, key=None) -> dict:
        r = self.partner(key)
        if not r:
            return {"error": "your partner isn't online", "code": "no_partner"}
        if self.world == "out" or not self.my_entity:
            return {"error": "you're not in the game's world", "code": "not_in_game"}
        if not self.mp_active():
            return {"error": "this isn't your multiplayer save", "code": "not_mp_save"}
        st = r.state or {}
        if not st:
            return {"error": f"where {r.name} is isn't known yet", "code": "no_position"}
        if st.get("city") != self.my_city:
            return {"error": f"{r.name} is in another city ({st.get('city') or '?'}): travel there with the map "
                             "first", "code": "other_city", "city": st.get("city")}
        eid = self.puppet_ids.get(r.pid)
        if eid and not r.hidden:
            await self.g(self.game.go_to, eid)           # the panel's "Join": next to their puppet
            how = "puppet"
        elif all(isinstance(st.get(k), (int, float)) for k in ("x", "y", "z")):
            self.mail.op("goto_point", entity=self.my_entity, x=st["x"] + 80, y=st["y"], z=st["z"])
            how = "position"
        else:
            return {"error": f"where {r.name} is isn't known yet", "code": "no_position"}
        log.info("going to %s (%s)", r.name, how)
        return {"ok": True, "how": how}

    async def partner_invite(self, key=None) -> dict:
        r = self.partner(key)
        if not r:
            return {"error": "your partner isn't online", "code": "no_partner"}
        if not self.home:
            return {"error": "your home isn't known yet: go home once (the program finds its front door there)",
                    "code": "no_home"}
        self.admit_partner(r)
        return await self.couple_request(r, "invite", site=self.home["site"])

    def admit_partner(self, r):
        if self.home and (r.spawned or r.pid in self.puppet_ids):
            self.mail.op("admit", key=r.pid, door=self.home["door"], site=self.home["site"])

    # ------------------------------------------------------------------ requests
    async def couple_request(self, r, action: str, **extra) -> dict:
        if action not in REQUEST_KINDS:
            return {"error": f"unknown action {action!r}", "code": "bad_action"}
        if not (self.writer and self.me_id):
            return {"error": "not connected to the server", "code": "not_connected"}
        self.prune_requests()
        rid = secrets.token_hex(6)
        self.sent[rid] = {"id": rid, "to": r.name, "to_id": r.pid, "action": action, "at": time.time(),
                          "state": "waiting", "message": "", "bag": extra.get("bag"), "name": extra.get("label")}
        await self.send({"t": "event", "kind": "couple", "to": r.pid,
                         "data": {"type": "request", "id": rid, "action": action, "name": self.cfg.get("name"),
                                  **extra}})
        log.info("asked %s: %s", r.name, action)
        return {"ok": True, "id": rid}

    def on_couple_event(self, m: dict):
        frm, data = str(m.get("from") or ""), m.get("data")
        if not isinstance(data, dict) or not frm:
            return
        rid = str(data.get("id") or "")[:32]
        if data.get("type") == "request":
            action = data.get("action")
            if action not in REQUEST_KINDS or not rid:
                return
            self.prune_requests()
            if len(self.requests) >= MAX_PENDING:
                return
            r = self.remotes.get(frm)
            name = r.name if r else str(data.get("name") or "Your partner")[:40]
            bag = str(data.get("bag") or "")[:80] if action == "interaction" else None
            if action == "interaction" and not BAG_ID.match(bag or ""):
                return
            self.requests[rid] = {"id": rid, "from": name, "from_id": frm, "action": action, "at": time.time(),
                                  "home_site": data.get("home_site") if isinstance(data.get("home_site"), int) else None,
                                  "bag": bag, "label": str(data.get("label") or "")[:80] or None}
            if self.always_yes(action, bag):
                log.info("%s asks: %s %s (always yes)", name, action, bag or "")
                asyncio.get_running_loop().create_task(self.couple_respond(rid, True))
                return
            label = self.requests[rid]["label"]
            self.system_line(f"{name} wants to {VERBS[action]}" + (f": {label}" if label else ""))
            log.info("%s asks: %s %s", name, action, bag or "")
        elif data.get("type") == "result":
            s = self.sent.get(rid)
            if not s or s["to_id"] != frm:
                return
            if data.get("ok"):
                log.info("%s's save: %s done", s["to"], s["action"])
            else:
                why = str(data.get("message") or "")[:300]
                self.mark(s, "failed", why)
                self.system_line(f"{s['to']}: it didn't work in their save ({ACTION_NAMES.get(s['action'], s['action'])}): {why}")
                log.warning("%s's save: %s failed: %s", s["to"], s["action"], why)
        elif data.get("type") == "house_request":     # (housesync.py: the partner wants our home as it is)
            asyncio.get_running_loop().create_task(self.on_house_request(frm))
        elif data.get("type") == "house":             # (housesync.py: the partner changed our home)
            self.on_house(frm, data)
        elif data.get("type") == "lot":               # (housesync.py: their exact house, the game's property)
            self.on_lot(frm, data)
        elif data.get("type") == "update_request":       # (the partner: "get the newest version")
            log.info("%s asked this program to update", self.remotes[frm].name if frm in self.remotes else frm)
            asyncio.get_running_loop().create_task(self.update_now())
        elif data.get("type") == "telemetry_request":
            asyncio.get_running_loop().create_task(self.send_telemetry(frm, data))
        elif data.get("type") == "telemetry":
            self.save_partner_telemetry(frm, data)
        elif data.get("type") == "log_request":
            asyncio.get_running_loop().create_task(self.send_log(frm, rid))
        elif data.get("type") == "log":
            self.save_partner_log(frm, data)
        elif data.get("type") == "diag_request" and frm in self.remotes:     # (the partner's "test my side")
            asyncio.get_running_loop().create_task(self.send_diag(frm, rid))
        elif data.get("type") == "diag":
            self.save_partner_diag(frm, data)
        elif data.get("type") == "reply":
            s = self.sent.get(rid)
            if not s or s["to_id"] != frm or s["state"] != "waiting":
                return
            s["state"] = "accepted" if data.get("accept") else "declined"
            if data.get("home_site"):
                s["partner_home"] = data["home_site"]             # (the family head's home, when that's them)
            self.system_line(f"{s['to']} {'said yes' if data.get('accept') else 'said no'} "
                             f"({ACTION_NAMES.get(s['action'], s['action'])})")
            if data.get("accept"):
                asyncio.get_running_loop().create_task(self.couple_perform(s, initiator=True))

    def always_key(self, action: str, bag: str | None) -> str:
        return f"interaction:{bag}" if action in ("interaction", "game") and bag else action

    def always_yes(self, action: str, bag: str | None) -> bool:
        return self.always_key(action, bag) in (self.cfg.get("always_yes") or [])

    def mirror_ok(self, r, bag: str) -> bool:
        """The partner's Zoi does something to mine in their game: it plays here without asking when it's an "always
        yes" or right after a yes (the couple action just accepted)."""
        return self.always_yes("interaction", bag) or time.time() < self.__dict__.get("_accepted", {}).get(r.pid, 0)

    def label_for(self, bag: str) -> str:
        """A readable name for an interaction id: the game's own (from the last list) or one made from the id."""
        offered = self.__dict__.get("_offered")
        if offered and bag in offered[1]:
            return offered[1][bag]
        text = re.sub(r"^(Co_|Coop_|Social_)", "", bag)
        text = re.sub(r"_(Male|Female)To(Male|Female)", "", text)
        text = re.sub(r"_(Success|Friendship|Romance|Business|Start\d*)$", "", text)
        return re.sub(r"([a-z])([A-Z])", r"\1 \2", text).replace("_", " ").strip()

    def ask_local(self, r, bag: str, op: dict):
        """Their Zoi already does it in their game; here, a popup asks before it plays (yes / no / always yes)."""
        self.prune_requests()
        if any(q.get("local") and q["from_id"] == r.pid and q.get("bag") == bag for q in self.requests.values()):
            return
        rid = secrets.token_hex(6)
        label = self.label_for(bag)
        self.requests[rid] = {"id": rid, "from": r.name, "from_id": r.pid, "action": "game", "bag": bag,
                              "label": label, "at": time.time(), "local": True, "op": op}
        self.system_line(f"{r.name} wants to interact with you: {label}")
        log.info("%s's Zoi does %s to yours: asking first", r.name, bag)

    async def couple_respond(self, rid: str, accept: bool, always: bool = False) -> dict:
        self.prune_requests()
        q = self.requests.pop(str(rid), None)
        if not q:
            return {"error": "that request isn't open any more", "code": "expired"}
        if accept and always:
            keys = self.cfg.setdefault("always_yes", [])
            key = self.always_key(q["action"], q.get("bag"))
            if key not in keys:
                keys.append(key)
                self.save_cfg()
                log.info("always yes from now on: %s", key)
        if accept:
            self.__dict__.setdefault("_accepted", {})[q["from_id"]] = time.time() + ACCEPTED_FOR
        if q.get("local") and q.get("action") == "house":
            if not accept:
                return {"ok": True, "result": {"state": "declined"}}
            self.house_yes[q["from_id"]] = time.time()
            return {"ok": True, "result": await self.apply_house(q)}
        if q.get("local"):                               # their Zoi already did it in their game: show it here or not
            if accept:
                self.mail.op("act", key=q["from_id"], **q["op"])
            return {"ok": True, "result": {"state": "done" if accept else "declined"}}
        if not (self.writer and self.me_id):
            return {"error": "not connected to the server", "code": "not_connected"}
        reply = {"type": "reply", "id": q["id"], "accept": bool(accept)}
        if accept and q["action"] in FAMILY:
            reply["home_site"] = await self.g(self.my_home_site)
        await self.send({"t": "event", "kind": "couple", "to": q["from_id"], "data": reply})
        out = {"ok": True}
        if accept:
            out["result"] = res = await self.couple_perform(q, initiator=False)
            if q["action"] in FAMILY:
                try:
                    await self.send({"t": "event", "kind": "couple", "to": q["from_id"],
                                     "data": {"type": "result", "id": q["id"], "ok": res.get("state") == "done",
                                              "message": res.get("message", "")}})
                except (OSError, ConnectionError):
                    pass
        return out

    async def couple_perform(self, req: dict, initiator: bool) -> dict:
        """Accepted: do it on this PC. req: a sent request (initiator: I asked) or a received one."""
        action = req["action"]
        pid = req.get("to_id") if initiator else req.get("from_id")
        r = self.remotes.get(pid)
        if action == "invite":
            if initiator:                                # my partner is coming: make sure my doors let them in
                if r:
                    self.admit_partner(r)
                return self.mark(req, "done")
            res = await self.partner_goto(pid)
            return self.mark(req, "done" if res.get("ok") else "failed", res.get("error", ""))
        if action == "interaction":
            if not initiator:
                return self.mark(req, "done")            # (their Zoi starts it; it shows up here through the live sync)
            eid = self.puppet_ids.get(pid)
            if not eid or not self.my_entity:
                return self.mark(req, "failed", "your partner's Zoi isn't here")
            self.mail.op("social", rid=req["id"], bag=req["bag"], entity=self.my_entity, key=pid, now=True)
            log.info("interaction with %s: %s (they said yes)", r.name if r else pid, req["bag"])
            return self.mark(req, "started")
        if action in FAMILY:                             # both saves: each PC makes it so in its own
            # The host's family is the family, whoever asked: on the host's PC the partner's Zoi joins the host's
            # household; on the other PC that player's own Zoi joins the household living in the host's home.
            if self.leader and self.leader == self.me_id:
                home = None
            elif self.leader and self.leader == pid:
                home = req.get("home_site") if not initiator else req.get("partner_home")
            else:
                home = None if initiator else req.get("home_site")          # (no host among us: whoever asked)
            try:
                res = await self.g(self.family_apply, action, pid, home)
            except Exception as e:                       # noqa: BLE001 - the game refused: say so in the panel
                log.warning("family %s failed: %s", action, e)
                res = {"error": str(e)}
            if req.get("state") == "failed":             # (their save said it didn't work: that stays)
                return {"state": "failed", "message": req.get("message", "")}
            if action == "movein" and res.get("ok") and (self.leader == self.me_id or not self.leader):
                # the family is the server's truth from now on: every save is brought to it (shared.py). The home is
                # looked up in that task: nothing may wait between the check above and the mark below, or their
                # "it didn't work" arriving meanwhile would be overwritten with "done".
                async def share(members):
                    await self.share_family(await self.g(self.my_home_site), members)
                asyncio.get_running_loop().create_task(share([self.me_id, pid]))
            return self.mark(req, "done" if res.get("ok") else "failed", res.get("error", ""))
        together = action in TARGETED or (action in TOGETHER and pid in self.puppet_ids)
        if together and not initiator:
            # (their Zoi starts it in their game; it reaches mine through the live sync, on my own Zoi)
            log.info("%s: %s's Zoi starts it; it shows up here through the live sync", action, r.name if r else pid)
            return self.mark(req, "done")
        if self.world == "out" or not self.my_entity:
            log.warning("couple action %s: not in the game's world, nothing done", action)
            return self.mark(req, "failed", "not in the game's world")
        bag = self.couple_bag(action)
        if not together and action not in ALONE_BAGS and not (self.cfg.get("couple_bags") or {}).get(action):
            return self.mark(req, "failed", "your partner's Zoi isn't here")     # (no made-up id for doing it alone)
        op = {"rid": req["id"], "bag": bag, "entity": self.my_entity}
        how = "learned" if (self.cfg.get("couple_bags") or {}).get(action) else "a guess"
        if together:
            if pid not in self.puppet_ids:
                log.warning("couple action %s: %s's Zoi isn't in your world, nothing done", action,
                            r.name if r else pid)
                return self.mark(req, "failed", "your partner's Zoi isn't here")
            eid = self.puppet_ids[pid]
            await self.g(self.bond_puppet, eid)
            found = await self.g(self.bag_with, action, eid, (r.profile or {}).get("gender") if r else None)
            if not found:
                why = f"the game doesn't offer {ACTION_NAMES.get(action, action)} between your Zois right now"
                log.warning("couple action %s: %s", action, why)
                self.system_line(f"The game doesn't offer that between you right now ({ACTION_NAMES.get(action, action)})")
                return self.mark(req, "failed", why)
            if found:
                op["bag"], how = found, "offered by the game"
            op["key"], op["now"] = pid, True
        log.info("couple action %s: interaction %s (%s)%s", action, op["bag"], how,
                 " on your partner's Zoi" if together else "")
        self.mail.op("social", **op)
        return self.mark(req, "started")

    # ------------------------------------------------------------------ family
    def my_household(self) -> dict:
        return self.game.call("household.get_members") or {}

    def all_households(self) -> list[dict]:
        """Every household (household.list include=all, page by page: residential families only would miss a family
        the game doesn't list as "can move in")."""
        out, offset = [], 0
        while offset < 5000:
            page = self.game.call("household.list", Limit=500, Offset=offset, Include="all") or {}
            got = page.get("households") or []
            out += got
            offset += len(got)
            if not got or offset >= (page.get("total") or 0):
                break
        return out

    def my_home_site(self) -> int | None:
        """The lot my household lives on (household.list: its residence), or None."""
        hid = self.my_household().get("household_id")
        for h in self.all_households():
            if h.get("household_id") == hid and h.get("residence_site_id"):
                return int(h["residence_site_id"])
        return None

    def household_at(self, site: int) -> int | None:
        """The household that lives on lot `site` in this save (one with members), or None."""
        for h in self.all_households():
            if h.get("residence_site_id") == site and h.get("household_id") and (h.get("member_count") or 0) > 0:
                return int(h["household_id"])
        return None

    def has_member(self, household: int, r) -> bool:
        """The partner's Zoi (by full name) is a member of that household in this save."""
        prof = r.profile or {}
        name = ((prof.get("first_name") or "").strip().casefold(), (prof.get("last_name") or "").strip().casefold())
        members = (self.game.call("household.get_members", household_id=household) or {}).get("members") or []
        return bool(name[0]) and any(((m.get("first_name") or "").strip().casefold(),
                                      (m.get("last_name") or "").strip().casefold()) == name for m in members)

    def family_zoi_anywhere(self, r) -> int | None:
        """The partner's Zoi in this world by full name (any household; a copy of ours last), or None."""
        prof = r.profile or {}
        name = ((prof.get("first_name") or "").strip().casefold(), (prof.get("last_name") or "").strip().casefold())
        found, offset = [], 0
        while name[0] and offset < 2000:
            page = self.game.call("world.list_characters", Limit=100, Offset=offset) or {}
            chars = page.get("characters") or []
            found += [int(c["entity_id"]) for c in chars if c.get("entity_id") and int(c["entity_id"]) != self.my_entity
                      and ((c.get("first_name") or "").strip().casefold(),
                           (c.get("last_name") or "").strip().casefold()) == name]
            offset += len(chars)
            if not chars or offset >= (page.get("total") or 0):
                break
        copy = self.puppet_ids.get(r.pid)
        found.sort(key=lambda e: e == copy and not r.adopted)
        return found[0] if found else None

    async def send_log(self, to: str, rid: str):
        """The partner's program asked for this one's log (to see what happened here): the last lines."""
        try:
            from companion import CONFIG_DIR
            text = (CONFIG_DIR / "companion.log").read_text("utf-8", errors="replace")
        except OSError as e:
            text = f"(no log: {e})"
        lines = text.splitlines()[-400:]
        lines += await self.g(self.family_report)
        await self.send({"t": "event", "kind": "couple", "to": to,
                         "data": {"type": "log", "id": rid, "version": self.version, "lines": lines}})
        log.info("sent the last %d log lines to %s (asked for)", len(lines), self.remotes[to].name if to in self.remotes else to)

    async def send_telemetry(self, to: str, data: dict):
        """The partner asked for this game's telemetry (the last minutes): sent compressed."""
        import telemetry
        minutes = max(1.0, min(float(data.get("minutes") or 15), 120.0))
        lines = await self.g(self.tele.recent, minutes)
        await self.send({"t": "event", "kind": "couple", "to": to,
                         "data": {"type": "telemetry", "id": data.get("id"), "version": self.version,
                                  "data": telemetry.pack(lines)}})
        log.info("sent %d telemetry lines (%.0f min) to %s", len(lines), minutes,
                 self.remotes[to].name if to in self.remotes else to)

    async def send_diag(self, to: str, rid: str):
        """The partner's program asked for this one's diagnostics, to test both PCs from one: the self-test, the game,
        and every sync's state here (what this game holds of the shared state, the objects, the house, the skeletons).
        Nothing secret goes (no key, no password: not the config)."""
        try:
            tests = await asyncio.wait_for(self.self_test(), 30)
        except Exception as e:                           # noqa: BLE001
            tests = [{"id": "self_test", "ok": False, "detail": f"couldn't run: {e}"}]
        try:
            report = await self.g(self.sync_report)
        except Exception as e:                           # noqa: BLE001
            report = {"error": f"couldn't read the sync state: {e}"}
        status = self.diag_status(tests)
        status["recent"] = status.get("recent", [])[-200:]
        await self.send({"t": "event", "kind": "couple", "to": to,
                         "data": {"type": "diag", "id": rid, "version": self.version, "status": status,
                                  "sync": report}})
        log.info("sent diagnostics to %s (asked for)", self.remotes[to].name if to in self.remotes else to)

    def sync_report(self) -> dict:
        """What each sync holds here right now (read-only): sent with the diagnostics."""
        shared = {k: {"v": (i or {}).get("v"), "by": (i or {}).get("by_name"), "seen": self.seen(k)}
                  for k, i in sorted(self.shared.items())}
        objects = {k: {"t": o.get("t"), "s": o.get("s"), "ch": o.get("ch"), "p": [round(c) for c in o.get("p") or []]}
                   for k, o in list(self.obj_last.items())
                   if [s for s in o.get("s") or [] if s != "Normal:Normal"] or o.get("ch")}
        try:
            sim = self.game.call("environment.get_simulation")
        except Exception as e:                           # noqa: BLE001
            sim = {"error": str(e)}
        try:
            home = self.my_home_site()
        except Exception as e:                           # noqa: BLE001
            home = f"error: {e}"
        return {"epoch": self.shared_epoch, "seq": self.shared_seq, "shared": shared,
                "objects": {"site": self.obj_site, "read": len(self.obj_last), "counts": dict(self.obj_counts),
                            "pending": list(self.obj_pending), "not_normal": objects},
                "house": {"home": home, "dirty": self.cfg.get("house_dirty"), "pending_lot": self.cfg.get("pending_lot"),
                          "lot_hash": self.cfg.get("lot_hash"), "busy": bool(getattr(self, "lot_busy", False))},
                "poses": {"counts": dict(self.pose_counts), "from": {self.remotes[p].name if p in self.remotes else p:
                                                                     round(time.time() - t, 1)
                                                                     for p, t in self.pose_from.items()}},
                "sim": sim, "world": self.world, "entity": self.my_entity, "fast_pos": self.__dict__.get("fast_pos")}

    def save_partner_diag(self, frm: str, data: dict):
        from companion import CONFIG_DIR
        r = self.remotes.get(frm)
        name = re.sub(r"[^A-Za-z0-9_-]", "_", r.name if r else frm)[:30]
        path = CONFIG_DIR / f"partner-{name}-diag.json"
        path.write_text(json.dumps({"version": data.get("version"), "at": time.time(), "status": data.get("status"),
                                    "sync": data.get("sync")}, indent=1, default=str), "utf-8")
        log.info("saved %s's diagnostics: %s", name, path)

    def save_partner_telemetry(self, frm: str, data: dict):
        import telemetry
        from companion import CONFIG_DIR
        r = self.remotes.get(frm)
        name = re.sub(r"[^A-Za-z0-9_-]", "_", r.name if r else frm)[:30]
        path = CONFIG_DIR / f"partner-{name}-telemetry.jsonl"
        try:
            lines = telemetry.unpack(data.get("data") or "")
        except Exception as e:                           # noqa: BLE001
            log.warning("couldn't read %s's telemetry: %s", name, e)
            return
        path.write_text("\n".join(lines) + "\n", "utf-8")
        log.info("saved %s's telemetry (%d lines): %s", name, len(lines), path)

    def family_report(self) -> list[str]:
        """Read-only facts about families and homes in this game, sent along with the log (what the partner needs to
        see why a house is or isn't someone's)."""
        out = ["# --- family report ---"]
        for name, fn in (("possessed", lambda: self.game.call("entity.get_possessed")),
                         ("my household", lambda: self.game.call("household.get_members")),
                         ("my state", lambda: self.game.state_of(self.my_entity) if self.my_entity else None),
                         ("families that matter", self.households_that_matter)):
            try:
                out.append(f"# {name}: {json.dumps(fn(), ensure_ascii=False)[:6000]}")
            except Exception as e:                       # noqa: BLE001
                out.append(f"# {name}: error {e}")
        return out

    def households_that_matter(self) -> list[dict]:
        """Mine, the ones ever played, and whoever lives where my Zoi stands (lot 1038 for the couple's home)."""
        mine = self.my_household().get("household_id")
        here = (self.game.state_of(self.my_entity) or {}).get("site") if self.my_entity else None
        return [h for h in self.all_households() if h.get("household_id") == mine or h.get("ever_possessed")
                or h.get("residence_site_id") in (here, 1038) or (h.get("name") or "").lower().startswith("oliveira")]

    def save_partner_log(self, frm: str, data: dict):
        from companion import CONFIG_DIR
        r = self.remotes.get(frm)
        name = re.sub(r"[^A-Za-z0-9_-]", "_", r.name if r else frm)[:30]
        path = CONFIG_DIR / f"partner-{name}.log"
        lines = [str(x)[:2000] for x in (data.get("lines") or [])][:2000]
        path.write_text(f"# {r.name if r else frm}'s log (version {data.get('version')})\n" + "\n".join(lines), "utf-8")
        log.info("saved %s's log: %s", r.name if r else frm, path)

    def drop_duplicates(self, r, keep: int):
        """Other characters with the partner's full name (copies made before they were family) leave the world: only
        their real Zoi stays."""
        prof = r.profile or {}
        name = ((prof.get("first_name") or "").strip().casefold(), (prof.get("last_name") or "").strip().casefold())
        if not name[0]:
            return
        offset = 0
        while offset < 2000:
            page = self.game.call("world.list_characters", Limit=100, Offset=offset) or {}
            chars = page.get("characters") or []
            for c in chars:
                same = ((c.get("first_name") or "").strip().casefold(), (c.get("last_name") or "").strip().casefold())
                eid = c.get("entity_id")
                if same == name and eid and int(eid) not in (keep, self.my_entity):
                    self.game.call("zoi.destroy", entity_id=int(eid))
                    log.info("removed a copy of %s's Zoi (%s): their real Zoi is in your family", r.name, eid)
            offset += len(chars)
            if not chars or offset >= (page.get("total") or 0):
                break

    def family_apply(self, action: str, pid: str, home_site: int | None = None) -> dict:
        """Make a family milestone true in this save, between my Zoi and the partner's Zoi here (runs in a thread).
        Moving in: the family lives in the home of whoever asked, in both saves. On the asker's PC the partner's Zoi
        joins the asker's household; on the other PC (home_site: the asker's lot) this player's own Zoi joins the
        household living on that lot - there the house is theirs too. With nobody living there, the partner's Zoi
        joins this player's household instead. Anything else needs them in the family first, so that comes first."""
        r = self.remotes.get(pid)
        eid = self.puppet_ids.get(pid)
        if not r or not eid or not self.my_entity:
            return {"error": "your partner's Zoi isn't in your world right now", "code": "not_here"}
        call = self.game.call
        target = self.household_at(home_site) if home_site else None
        mine = self.my_household().get("household_id")
        if home_site and not target and mine and self.my_home_site() != home_site:
            # Nobody lives in the asker's home in this save: my family moves there (the game's own residence change,
            # done by the in-game controller), so the house is ours here too.
            self.mail.op("move_home", household=int(mine), site=int(home_site))
            for _ in range(20):
                time.sleep(0.5)
                if self.my_home_site() == home_site:
                    break
            else:
                return {"error": f"the game didn't move your family to lot {home_site} (is the game paused?)",
                        "code": "move_failed"}
            log.info("your family moved to lot %s (%s's home): the house is yours too", home_site, r.name)
        if target and target != mine and not self.has_member(target, r):
            log.info("the household on lot %s here isn't %s's family: your Zoi stays where it is", home_site, r.name)
            target = None                                # (never into strangers' home)
        log.info("moving in with %s: their home here is lot %s (household %s), mine is household %s",
                 r.name, home_site, target, mine)
        if target and target != mine:
            try:
                call("household.add_member", entity_id=self.my_entity, household_id=target)
            except Exception as e:                       # noqa: BLE001 - said below, with the game's answer
                log.warning("the game didn't move your Zoi into that household: %s", e)
            now = self.my_household().get("household_id")
            if now != target:
                fam = self.family_zoi_anywhere(r)
                if fam:
                    # their Zoi here asks mine to live with them: the game's own way, with its own question
                    call("interaction.execute", entity_id=fam, interaction_bag_id="Co_Cohabit_Friendship",
                         target_entity_id=self.my_entity, insert_front=True)
                    log.info("asked the game: %s's Zoi asks yours to live with them", r.name)
                    return {"error": "the game asks on screen: answer yes to live together", "code": "asked_game"}
                return {"error": f"the game didn't move your Zoi into the household on lot {home_site}",
                        "code": "move_failed"}
            self.__dict__.setdefault("_family", {}).pop(pid, None)
            log.info("your Zoi moved into %s's home: one family, one home", r.name)
            fam = self.family_zoi(r)
            if fam:
                self.drop_duplicates(r, fam)
                if fam != eid:
                    extra = {"home_door": self.home["door"], "home_site": self.home["site"]} if self.home else {}
                    st = r.state or {}
                    self.mail.op("adopt", key=pid, entity=fam, **{k: st[k] for k in ("x", "y", "z") if k in st},
                                 **extra)
                    self.puppet_ids[pid], eid = fam, fam
                r.adopted = True
        elif self.family_zoi(r) != eid:
            # The copy joins my household: from now on it is their Zoi in this save (it ages and lives here).
            call("household.add_member", entity_id=eid)
            for cmd in ("life_and_death.stop_aging", "life_and_death.immortal"):
                try:
                    call(cmd, entity_id=eid, Enable=False)
                except Exception:                        # noqa: BLE001
                    pass
            self.__dict__.setdefault("_family", {}).pop(pid, None)
            extra = {"home_door": self.home["door"], "home_site": self.home["site"]} if self.home else {}
            self.mail.op("adopt", key=pid, entity=eid, **extra)       # (the controller keeps it: never destroyed)
            r.adopted = True
            log.info("%s's Zoi moved in: one family, one home", r.name)
        if action == "engage":
            call("marriage.engage", entity_id=self.my_entity, target_entity_id=eid)
        elif action == "marry":
            call("marriage.marry", entity_id=self.my_entity, target_entity_id=eid)
        elif action == "baby":
            mine = (self.__dict__.get("my_gender") or "").lower()
            theirs = ((r.profile or {}).get("gender") or "").lower()
            mother, father = (eid, self.my_entity) if theirs == "female" and mine != "female" else (self.my_entity, eid)
            call("pregnancy_and_birth.get_baby_with", entity_id=mother, partner_id=father)
        log.info("family with %s: %s", r.name, action)
        self.system_line(FAMILY_LINES[action])
        return {"ok": True}

    # ------------------------------------------------------------------ every interaction the game offers
    def partner_interactions(self, pid: str | None) -> dict:
        """What my Zoi can do with the partner's Zoi right now, in the game's own words, by group; and how the two
        get on (the game's relationship stats). Runs in a thread."""
        r = self.partner(pid)
        if not r:
            return {"error": "your partner isn't online", "code": "no_partner"}
        eid = self.puppet_ids.get(r.pid)
        if not eid or not self.my_entity or self.world == "out":
            return {"error": "your partner's Zoi isn't in your world right now", "code": "not_here"}
        avail = self.game.call("interaction.list_available", entity_id=self.my_entity, target_entity_id=eid) or {}
        groups: dict[str, list] = {}
        seen: dict[str, str] = {}
        for i in avail.get("interactions") or []:
            bag = str(i.get("interaction_bag_id") or "")
            if not BAG_ID.match(bag) or bag in seen or bag.startswith("anim:"):
                continue
            seen[bag] = str(i.get("display_name") or bag)
            g = next((k for k, rx in GROUPS if rx.search(bag)), "talk")
            groups.setdefault(g, []).append({"id": bag, "name": i.get("display_name") or bag,
                                             "hint": i.get("hint") or "", "enabled": i.get("enabled", True)})
        self.__dict__["_offered"] = (r.pid, seen)
        bond = {}
        try:
            stats = (self.game.call("relationships.get_value_between", owner_id=self.my_entity,
                                    target_id=eid) or {}).get("stats") or {}
            bond = {k.lower(): {"value": round(v.get("value") or 0), "level": v.get("level") or 0}
                    for k, v in stats.items() if isinstance(v, dict)}
        except Exception:                                # noqa: BLE001
            pass
        quick = {a: (lambda b: {"bag": b, "name": seen[b]} if b else None)(self.quick_match(a, seen)) for a in QUICK_PATTERNS}
        return {"ok": True, "family": bool(r.adopted), "bond": bond, "quick": quick,
                "groups": [{"key": k, "items": sorted(groups[k], key=lambda x: x["name"])}
                           for k in GROUP_ORDER if k in groups]}

    def partner_do(self, pid: str | None, bag: str) -> dict:
        """Do one of those with the partner's Zoi: my Zoi starts it here, and the live sync carries it over (on
        their PC my Zoi does it to theirs, which answers it the way the game does)."""
        r = self.partner(pid)
        if not r:
            return {"error": "your partner isn't online", "code": "no_partner"}
        eid = self.puppet_ids.get(r.pid)
        if not eid or not self.my_entity:
            return {"error": "your partner's Zoi isn't in your world right now", "code": "not_here"}
        offered = self.__dict__.get("_offered")
        if not BAG_ID.match(bag or "") or not offered or offered[0] != r.pid or bag not in offered[1]:
            return {"error": "the game doesn't offer that right now", "code": "not_offered"}
        return {"ask": (r, offered[1][bag])}

    def mark(self, req: dict, state: str, message: str = "") -> dict:
        req["state"], req["message"] = state, message
        return {"state": state, **({"message": message} if message else {})}

    def on_social_report(self, text: str):
        """The controller ran a couple action: "<rid> ok|fail <text>"."""
        rid, _, rest = text.partition(" ")
        status, _, why = rest.partition(" ")
        req = self.sent.get(rid)
        if status == "ok":
            log.info("couple action %s started", req["action"] if req else rid)
            if req:
                self.mark(req, "done")
        else:
            log.warning("couple action %s failed in the game: %s (the interaction id may be wrong: set it in "
                        "config.json \"couple_bags\" or do it once by hand)", req["action"] if req else rid, why)
            if req:
                self.mark(req, "failed", why)

    # ------------------------------------------------------------------ shared household: backups, undo, status
    def backup_save(self, folder: str | None, reason: str):
        """Back the save folder up (never fails the caller: a backup that can't be made is logged)."""
        if not folder:
            return None
        try:
            path = saves.backup(folder, reason)
        except (OSError, ValueError) as e:
            log.warning("couldn't back up the save %s: %s", folder, e)
            return None
        if path:
            self.last_backup = {"name": path.name, "reason": reason, "at": time.time()}
            log.info("backed up your multiplayer save (%s): %s", reason, path.name)
        return path

    def backups_view(self, folder: str | None = None) -> list[dict]:
        return [{k: b[k] for k in ("name", "folder", "reason", "created", "size")}
                for b in saves.list_backups(folder)]

    def household_view(self) -> dict:
        meta, mine = self.world_meta or {}, self.local_world()
        folder = mine.get("folder") or (self.cfg.get("mp_save") or {}).get("folder")
        try:
            backups = saves.list_backups(folder) if folder else []
        except OSError:
            backups = []
        server_v, local_v = meta.get("version"), mine.get("version")
        return {"on": self.shared_world_on(), "shared_world": self.shared_world_on(),
                "version": server_v, "local_version": local_v,
                "behind": bool(self.shared_world_on() and server_v and server_v > (local_v or 0)),
                "saved_by": meta.get("by"), "saved_by_me": bool(meta and meta.get("pid") == self.me_id),
                "saved_at": meta.get("time"), "name": meta.get("name"), "size": meta.get("size"),
                "busy": bool(self.world_busy), "job": self.world_job,
                "waiting_for_game_save": bool(self.upload_wait and time.time() - self.upload_wait < 120),
                "backups": len(backups),
                "last_backup": {k: backups[0][k] for k in ("name", "reason", "created")} if backups else None,
                "can_undo": {"server": self.can_undo_server(), "local": bool(backups)},
                "undo": self.undo_state}

    def hosting_here(self) -> bool:
        """This program runs the server the player is playing on (its host is an admin there)."""
        return bool(getattr(self, "hosted", None) and getattr(self, "play_local", False))

    def can_undo_server(self) -> bool:
        meta = self.world_meta or {}
        return bool(self.shared_world_on() and meta.get("version", 0) >= 2 and self.writer
                    and (self.is_admin or self.hosting_here()))

    async def world_undo(self, source: str = "auto", version=None, backup: str | None = None) -> dict:
        """Undo the last save: the server's world goes back to an earlier version (default: the one before the
        current one; admins only: the host always is one), or this PC's save goes back to a local backup (default:
        the newest). Either way the game leaves this save without saving and loads the restored one."""
        if self.world_busy or self.save_busy:
            return {"error": "busy with the save right now: try again in a moment", "code": "busy"}
        if source == "auto":
            source = "server" if self.can_undo_server() else "local"
        if source == "server":
            meta = self.world_meta or {}
            target = int(version) if version else meta.get("version", 0) - 1
            if not self.shared_world_on() or target < 1:
                return {"error": "there's no earlier shared world version", "code": "no_version"}
            if not (self.is_admin or self.hosting_here()):
                return {"error": "only the host can take the shared world back", "code": "not_admin"}
            res = await self.admin("world_restore", local=bool(self.hosting_here()), version=target)
            if not res.get("ok"):
                self.undo_state = {"state": "failed", "source": "server", "message": res.get("error"), "at": time.time()}
                return {"error": res.get("error") or "the server refused", "code": "server_refused"}
            self.undo_state = {"state": "done", "source": "server", "version": res.get("version"), "from": target,
                               "message": f"the shared world is back at version {target}", "at": time.time()}
            log.info("undo: the shared world is back at version %d (now version %s)", target, res.get("version"))
            if self.world != "out" and self.mp_active():
                asyncio.get_running_loop().create_task(self.undo_reload())
            return {"ok": True, "source": "server", "version": res.get("version"), "restored": target}
        if source != "local":
            return {"error": f"unknown source {source!r}", "code": "bad_request"}
        folder = self.local_world().get("folder") or (self.cfg.get("mp_save") or {}).get("folder")
        found = saves.list_backups(folder) if folder else []
        pick = next((b for b in found if b["name"] == backup), None) if backup else (found[0] if found else None)
        if not pick:
            return {"error": "there's no backup of your multiplayer save yet", "code": "no_backup"}
        asyncio.get_running_loop().create_task(self.restore_local(pick))
        return {"ok": True, "source": "local", "backup": pick["name"]}

    async def undo_reload(self):
        """After the server's world went back: leave the game without saving, download it and load it."""
        try:
            await self.reload_world()
            self.world_box = None
        except Exception as e:                           # noqa: BLE001 - shown in the panel
            log.warning("loading the restored world failed: %s", e)
            self.undo_state = {**(self.undo_state or {}), "state": "failed", "message": str(e)}

    async def restore_local(self, pick: dict):
        self.save_busy = True
        self.undo_state = {"state": "working", "source": "local", "backup": pick["name"], "at": time.time(),
                           "message": "restoring your backup"}
        try:
            if self.world != "out":
                self.despawn_all()
                await asyncio.sleep(1.5)
                await self.g(self.game.call, "game.goto_lobby", save=False)
                await self.wait_game(lambda st: st.get("is_lobby"), 240)
            await self.g(self.backup_save, pick["folder"], "before-undo")
            folder = await self.g(saves.restore, pick["path"])
            w = self.cfg.get("world")
            if w and w.get("folder") == folder:          # (no upload of the restored files until the game saves again)
                slot = world.newest_slot(folder)
                w["mtime"] = slot.stat().st_mtime if slot else w.get("mtime", 0)
                self.save_cfg()
            self.auto_loaded = False                     # loads at the main menu
            self.undo_state = {"state": "done", "source": "local", "backup": pick["name"], "at": time.time(),
                               "message": "your save is back as it was at " +
                                          time.strftime("%d/%m %H:%M", time.localtime(pick["created"]))}
            log.info("undo: restored the backup %s", pick["name"])
        except Exception as e:                           # noqa: BLE001 - shown in the panel
            log.warning("restoring the backup failed: %s", e)
            self.undo_state = {"state": "failed", "source": "local", "backup": pick["name"], "message": str(e),
                               "at": time.time()}
        finally:
            self.save_busy = False

    # ------------------------------------------------------------------ routes
    def couple_routes(self) -> dict:
        async def goto(body):
            return await self.partner_goto(body.get("key"))

        async def invite(body):
            return await self.partner_invite(body.get("key"))

        async def action(body):
            act = str(body.get("action", ""))
            if act not in ACTIONS:
                return {"error": f"unknown action {act!r}", "code": "bad_action"}
            r = self.partner(body.get("key"))
            if not r:
                return {"error": "your partner isn't online", "code": "no_partner"}
            return await self.couple_request(r, act)

        async def respond(body):
            return await self.couple_respond(str(body.get("id", "")), bool(body.get("accept")), bool(body.get("always")))

        async def undo(body):
            return await self.world_undo(str(body.get("source") or "auto"), body.get("version"), body.get("backup"))

        async def backups(_):
            folder = self.local_world().get("folder") or (self.cfg.get("mp_save") or {}).get("folder")
            return {"backups": await self.g(self.backups_view, folder)}

        async def interactions(body):
            return await self.g(self.partner_interactions, body.get("key"))

        async def do(body):
            res = await self.g(self.partner_do, body.get("key"), str(body.get("bag", "")))
            if "ask" not in res:
                return res
            r, label = res["ask"]
            return await self.couple_request(r, "interaction", bag=str(body.get("bag")), label=label)

        async def family(body):
            act = str(body.get("action", ""))
            if act not in FAMILY:
                return {"error": f"unknown action {act!r}", "code": "bad_action"}
            r = self.partner(body.get("key"))
            if not r:
                return {"error": "your partner isn't online", "code": "no_partner"}
            if r.pid not in self.puppet_ids:
                return {"error": "your partner's Zoi isn't in your world right now", "code": "not_here"}
            return await self.couple_request(r, act, home_site=await self.g(self.my_home_site))

        async def partner_log(body):
            r = self.partner(body.get("key"))
            if not r:
                return {"error": "your partner isn't online", "code": "no_partner"}
            await self.send({"t": "event", "kind": "couple", "to": r.pid,
                             "data": {"type": "log_request", "id": secrets.token_hex(6)}})
            return {"ok": True}

        async def partner_update(body):
            r = self.partner(body.get("key"))
            if not r:
                return {"error": "your partner isn't online", "code": "no_partner"}
            await self.send({"t": "event", "kind": "couple", "to": r.pid,
                             "data": {"type": "update_request", "id": secrets.token_hex(6)}})
            return {"ok": True}

        async def house_exact_now(body):
            return await self.apply_lot_now()

        async def partner_diag(body):
            r = self.partner(body.get("key"))
            if not r:
                return {"error": "your partner isn't online", "code": "no_partner"}
            await self.send({"t": "event", "kind": "couple", "to": r.pid,
                             "data": {"type": "diag_request", "id": secrets.token_hex(6)}})
            return {"ok": True}

        async def partner_telemetry(body):
            r = self.partner(body.get("key"))
            if not r:
                return {"error": "your partner isn't online", "code": "no_partner"}
            await self.send({"t": "event", "kind": "couple", "to": r.pid,
                             "data": {"type": "telemetry_request", "id": secrets.token_hex(6),
                                      "minutes": body.get("minutes") or 15}})
            return {"ok": True}

        async def partner_house(body):
            r = self.partner(body.get("key"))
            if not r:
                return {"error": "your partner isn't online", "code": "no_partner"}
            return await self.request_house(r)

        return {"/partner/telemetry": partner_telemetry, "/partner/house/exact-now": house_exact_now, "/partner/house": partner_house, "/partner/update": partner_update, "/partner/log": partner_log, "/partner/diag": partner_diag, "/partner/interactions": interactions, "/partner/do": do, "/partner/family": family,
                "/partner/goto": goto, "/partner/invite": invite, "/partner/action": action,
                "/partner/respond": respond, "/world/undo": undo, "/world/backups": backups}
