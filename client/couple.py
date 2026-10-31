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
REQUEST_KINDS = ACTIONS + ("invite",) + FAMILY
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
REQUEST_TTL = 90                           # seconds a request can be answered
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
LEARN = {"hug": re.compile(r"hug", re.I), "kiss": re.compile(r"kiss", re.I), "dance": re.compile(r"dance", re.I),
         "wave": re.compile(r"(?<!micro)wave", re.I)}             # (not Use_Microwave)
ACTION_NAMES = {"hug": "hug", "kiss": "kiss", "dance": "dance", "wave": "high five", "holdhands": "hold hands",
                "selfie": "selfie", "spin": "spin", "invite": "invitation", "movein": "moving in together",
                "engage": "engagement", "marry": "wedding", "baby": "baby"}       # (chat lines)
VERBS = {"hug": "hug you", "kiss": "kiss you", "dance": "dance with you", "wave": "high-five you",
         "holdhands": "hold your hand", "selfie": "take a selfie with you", "spin": "spin you around in a hug",
         "invite": "invite you to their home", "movein": "move in with you (one family, one home)",
         "engage": "get engaged to you", "marry": "marry you", "baby": "have a baby with you"}


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
        return next((b for b in want if b in offered), None)

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
        return {"requests": [{k: q.get(k) for k in ("id", "from", "from_id", "action", "at")}
                             for q in sorted(self.requests.values(), key=lambda q: q["at"])],
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
                          "state": "waiting", "message": ""}
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
            self.requests[rid] = {"id": rid, "from": name, "from_id": frm, "action": action, "at": time.time()}
            self.system_line(f"{name} wants to {VERBS[action]}")
            log.info("%s asks: %s", name, action)
        elif data.get("type") == "reply":
            s = self.sent.get(rid)
            if not s or s["to_id"] != frm or s["state"] != "waiting":
                return
            s["state"] = "accepted" if data.get("accept") else "declined"
            self.system_line(f"{s['to']} {'said yes' if data.get('accept') else 'said no'} "
                             f"({ACTION_NAMES.get(s['action'], s['action'])})")
            if data.get("accept"):
                asyncio.get_running_loop().create_task(self.couple_perform(s, initiator=True))

    async def couple_respond(self, rid: str, accept: bool) -> dict:
        self.prune_requests()
        q = self.requests.pop(str(rid), None)
        if not q:
            return {"error": "that request isn't open any more", "code": "expired"}
        if not (self.writer and self.me_id):
            return {"error": "not connected to the server", "code": "not_connected"}
        await self.send({"t": "event", "kind": "couple", "to": q["from_id"],
                         "data": {"type": "reply", "id": q["id"], "accept": bool(accept)}})
        out = {"ok": True}
        if accept:
            out["result"] = await self.couple_perform(q, initiator=False)
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
        if action in FAMILY:                             # both saves: each PC makes it so in its own
            try:
                res = await self.g(self.family_apply, action, pid)
            except Exception as e:                       # noqa: BLE001 - the game refused: say so in the panel
                log.warning("family %s failed: %s", action, e)
                res = {"error": str(e)}
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
            if found:
                op["bag"], how = found, "offered by the game"
            op["key"], op["now"] = pid, True
        log.info("couple action %s: interaction %s (%s)%s", action, op["bag"], how,
                 " on your partner's Zoi" if together else "")
        self.mail.op("social", **op)
        return self.mark(req, "started")

    # ------------------------------------------------------------------ family
    def family_apply(self, action: str, pid: str) -> dict:
        """Make a family milestone true in this save, between my Zoi and the partner's Zoi here (runs in a thread).
        Anything but moving in needs them in the family first, so that comes first when it isn't so yet."""
        r = self.remotes.get(pid)
        eid = self.puppet_ids.get(pid)
        if not r or not eid or not self.my_entity:
            return {"error": "your partner's Zoi isn't in your world right now", "code": "not_here"}
        call = self.game.call
        if self.family_zoi(r) != eid:
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
        return {"ok": True, "family": bool(r.adopted), "bond": bond,
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
        rid = secrets.token_hex(6)
        self.sent[rid] = {"id": rid, "to": r.name, "to_id": r.pid, "action": bag, "name": offered[1][bag],
                          "at": time.time(), "state": "started", "message": ""}
        self.mail.op("social", rid=rid, bag=bag, entity=self.my_entity, key=r.pid, now=True)
        log.info("interaction with %s: %s", r.name, bag)
        return {"ok": True, "id": rid}

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
            return await self.couple_respond(str(body.get("id", "")), bool(body.get("accept")))

        async def undo(body):
            return await self.world_undo(str(body.get("source") or "auto"), body.get("version"), body.get("backup"))

        async def backups(_):
            folder = self.local_world().get("folder") or (self.cfg.get("mp_save") or {}).get("folder")
            return {"backups": await self.g(self.backups_view, folder)}

        async def interactions(body):
            return await self.g(self.partner_interactions, body.get("key"))

        async def do(body):
            return await self.g(self.partner_do, body.get("key"), str(body.get("bag", "")))

        async def family(body):
            act = str(body.get("action", ""))
            if act not in FAMILY:
                return {"error": f"unknown action {act!r}", "code": "bad_action"}
            r = self.partner(body.get("key"))
            if not r:
                return {"error": "your partner isn't online", "code": "no_partner"}
            if r.pid not in self.puppet_ids:
                return {"error": "your partner's Zoi isn't in your world right now", "code": "not_here"}
            return await self.couple_request(r, act)

        return {"/partner/interactions": interactions, "/partner/do": do, "/partner/family": family,
                "/partner/goto": goto, "/partner/invite": invite, "/partner/action": action,
                "/partner/respond": respond, "/world/undo": undo, "/world/backups": backups}
