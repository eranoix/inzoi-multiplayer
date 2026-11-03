# inZOI Multiplayer wire protocol (v1)

TCP, one JSON object per line (UTF-8, `\n`-terminated). Every message has `t` (its type). Default port 24242.

What the server accepts: strict UTF-8 (CESU-8 surrogate bytes are refused), a JSON object, nested at most 100 levels
deep, with no lone UTF-16 surrogate (`"\ud800"` alone; an escaped surrogate *pair*, an emoji, is fine) and no integer
longer than 4300 digits (what Python 3.11+ reads at most). Anything else gets `bad_json` and is dropped, so no stored
value can later fail to be written or read back. What the server sends is ASCII JSON
(anything else as `\u` escapes, which any JSON reader decodes the same).

## Joining

Client → `{"t":"hello","protocol":1,"key":"<16-128 chars, generated once per player and kept secret>","name":"Ana","password":"","host":false,"version":"0.8.1"}`
(`version`, optional: the companion app's version; passed on to the others in `Player.version` so they can warn on a
mismatch. A different app version is never refused: only `protocol` must match.)

Server → `{"t":"welcome","id":"p1a2b3c4","server_version":"0.8.0","protocol":1,"leader":"<player id>","host":"<player id>"|null,"world":{...}|null,"players":[Player...],"houses":[HouseMeta...],"modset":ModSet,"world_save":WorldMeta|null,"motd":"","server":{id,name,description,motd,public,password,max_players,version,hosted,directory},"admin":false,"shared":{key:item},"seq":n,"epoch":"<hex>"}`
or `{"t":"error","fatal":true,"code":"...","error":"..."}` and the connection closes. The welcome is always the first
line on the connection, and nothing said by others in the meantime is missed (see "Shared state" for `shared`, `seq`
and `epoch`).

The welcome always fits in one line a companion reads (32 MB): what a player can store or relay into it is capped (a
Zoi at 2 MB, a state and a world at 64 KB, a mod list at 2 MB, the mod set at 8 MB, a house's listed `name`, `by` and
`lot` at 200 characters, the houses listed at 200, the shared state at 16 MB). Only if it would still be bigger (many
players online with big Zois), the welcome's `players` carry `profile: null` and `state: null` and its `modset` is
`null`, and they follow at once, before anything else, as the lines that bring their changes: `modset`, then
`profile {id, profile}` and `state {id, state}` for each player. A welcome that can't fit even so (never with the
caps) is refused with `bad_request`.

A connection must say hello within 20 s of connecting (or it's closed; pings don't extend that). Every `error` has a
machine-readable `code` (for the UI to translate) next to the English `error` text:
`wrong_password`, `version_mismatch` (also carries `protocol`, `server_version`), `bad_key`, `banned` (also
`banned: true`), `full`, `busy` (too many connections: 64 in all, 12 per address, 24 not yet joined and 6 per address
not yet joined; connections from 127.0.0.1, the host's own program, are never refused), `rate_limited` (from another
machine: more than 10 wrong passwords a minute, or more than 5 new players per 10 minutes, from that address; wait and
try again), `not_joined` (a file connection's hello from another machine for a player who isn't on the server; see
below), `too_large` (a line over 64 KB before a successful hello, over 32 MB after), all fatal; and, not fatal (the
connection stays open), `bad_json`, `bad_request` (a malformed message; carries the message's `ref` if it had one),
`hello_first`, `wrong_connection`, `unknown_type`, `not_online`, and for `set`: `bad_key`, `too_big`, `storage_failed`
(see "Shared state"). Passwords are compared as UTF-8 bytes (accents are fine). A hello that's refused (wrong
password, full, banned, rate limited) never creates a player record.

A `ref` is echoed back only if it's a number, a boolean or a string of at most 200 characters (otherwise `null`).

`host: true` counts only on a server started inside a player's program ("Host and play", `Server(hosted=True)`) and only
from that same PC (127.0.0.1): that player becomes the host (their mods are everyone's, they lead the clock).

A second connection with `"files": true` in its hello is the same player's mod file connection: the server answers
`{"t":"files_ok"}` and accepts only `blob_check`, `blob_put`, `blob_get` (and ping/pong) on it. From another machine it
must belong to a player who is on the server (joined on their main connection, or in the linger after it broke):
otherwise the hello gets the fatal `not_joined` and no player record is made, so nobody can hold connection slots with
file connections (which may sit idle for 10 minutes) without joining. A player has at most 3 file connections open
(the companion runs at most 3 transfers at once): a 4th closes the one that has been quiet longest. They're closed when
their player leaves (after the linger, a `bye`, a kick or a ban). File connections from 127.0.0.1 (the host's own
program) don't need a joined player and aren't closed when their player leaves; the 3-per-player limit applies to them
too.

`Player` = `{"id","name","profile","state","leader","version"}`. The `id` is stable for a key, so houses and profiles survive reconnects.
Connecting again with the same key replaces the old session (it gets `{"t":"kicked"}`) without a break: the others get
no `left`/`joined`, only `{"t":"rejoined","player":Player}` (the player keeps their last state and the clock if they
led it), so their Zoi stays in everyone's world. Companions that don't know `rejoined` ignore it. A connection that
breaks without `bye` keeps its player listed for 10 s (the linger), so a quick reconnect is that silent rejoin; after
that the others get `left`. Once a connection is closed by the server (kicked, banned, replaced by a reconnect, a fatal
error), nothing more it sent is handled, even lines already on their way: a `set` among them isn't applied (after a
reconnect the companion sees that in its new welcome and sends it again, as for any set whose answer never came).

## Slow and vanished connections

Nothing the server sends ever waits for the receiver: each connection has its own outbound queue. So a player whose PC
froze, whose Wi-Fi died without a word, or who is downloading a big welcome over a weak link holds up nobody else, not
even the keepalive. For a connection that's behind, a newer `state` (per player), skeleton frame (`event` kind `pose`,
per player and per `k`/`d`), `world` or `ping` replaces the older one still waiting (latest wins); everything else is
delivered in order. A connection is cut (and reconnects to a fresh welcome) when it takes none of what it's sent for
20 s, or when more than 40 MB is queued for it behind what's being sent (1 MB before hello).

A joined player is dropped after 45 s in which nothing at all arrived from it (file connections: 10 minutes) and it took
nothing of what it was sent: a long line still arriving, or a big welcome still downloading, counts as alive.
Timeouts, rate limits and the uptime count on a clock that only goes forward: a step of the server's wall clock (a
Raspberry Pi without a clock battery setting its time from the network) cuts nobody off.

## Client → server

| `t` | fields | what happens |
|---|---|---|
| `state` | `state` | live Zoi state (position, rotation, animation, interaction, site); relayed to everyone else as `state`. Over 64 KB of JSON: ignored |
| `profile` | `profile` | the player's Zoi (name, appearance, traits); stored, relayed as `profile`. Over 2 MB of JSON: not stored nor relayed, the sender gets `error {code: "too_big"}` (not fatal) |
| `world` | `world` | clock/weather/season; only accepted from the leader, relayed as `world`. Over 64 KB of JSON: ignored |
| `take_leader` | | become the world-clock leader; everyone gets `leader`. On a hosted server while the host is online only the host can: anyone else just gets `leader` back with the host's id |
| `chat` | `text` (≤500) | broadcast to everyone, including the sender, as `chat` |
| `event` | `kind`, `data`, `to?`, `ref?` | generic relay to one player (`to`) or everyone else; arrives as `event` with `from`. To a player who isn't online: `error {code: "not_online", ref, to, kind, id}` (`id` = `data.id` if it has one). To a player who is reconnecting (in the linger after a broken connection), it's held (up to 64 events, 1 MB) and delivered right after their welcome when they're back; if they don't come back, the sender gets that `not_online` then (`error`: "... went offline"); if the hold is full, at once, with `lingering: true` |
| `house_put` | `house` | store a snapshot of your house; everyone gets `house_updated` with its `meta` |
| `house_get` | `owner` | reply `house` with `meta` and `house` (null if none, and for an `owner` that isn't a player id `p` + 8 hex digits) |
| `house_list` | | reply `house_list` (the same houses as the welcome) |
| `who` | | reply `who` with everyone online |
| `mods` | `mods: [Mod]` | the player's enabled mods; stored; everyone gets `modset` if the set changed. Past 2 MB of JSON, only the mods that fit (from the start of the list) are kept, and the sender gets `error {code: "too_big"}` (not fatal) saying how many |
| `ping` / `pong` | `echo?` | keepalive; the server pings every 15 s and drops clients silent for 45 s (file connections get no pings and are dropped after 10 minutes of silence; see "Slow and vanished connections") |

## Mod file connection (`files: true`)

| `t` | fields | answer |
|---|---|---|
| `blob_check` | `h: [sha256]` | `blob_missing {h}`: the ones the server doesn't have yet, or has damaged (stored with another size than your mod list gives, and not matching its sha256: cut short by a power cut; it's deleted, upload it again) |
| `blob_put` | `h, o (offset), d (base64), end` | in order, only files of your own mods; pieces up to 8 MB; after `end`: `blob_ok {h}` (the file is on disk for good: flushed, renamed, its folder flushed) or `blob_bad {h, error}`. A wrong offset gets `blob_bad {h, error, o}` with the offset expected; a bad piece (not base64, too big, past the file's size) discards the upload: `blob_bad {h, error, o: 0}` |
| `blob_get` | `h, o` | `blob {h, o, d, end}` (512 KB pieces), or `blob {h, error: "not shared" / "not uploaded yet"}`. `o` is a JSON integer, 0 or more (anything else: `bad_request`) |

`Mod` = `{id, name, version, built, path, size, files: [{p, h, s}]}`: the mod_manifest's ProjectName, FriendlyName,
Version and PackageTime, its folder under Documents/inZOI/Mods, and each file's path, sha256 and size.
`ModSet` = `{source: "host"|"everyone", host, mods: [Mod + by: [names]]}`: hosted, the host's mods; otherwise everyone's
pooled (the newest build of a mod wins). At most 8 MB of JSON: past it, the host's last mods, or (pooled) the mods of
the lists updated longest ago, are left out.
`HouseMeta` = `{owner, updated, name, by, lot}`: `name` and `by` cut to 200 characters; `lot` a whole number or a text
cut to 200 characters. The welcome and `house_list` list at most 200: the houses of the players online, then the newest.

## Before joining: info and the public list (no hello needed)

| `t` | answer |
|---|---|
| `info` | `info {id, name, description, motd, public, password (bool), max_players, players, names, host, hosted, version, protocol, directory, uptime, world, mods: {source, list: [{id, name, version, built, size, fp}]}}`: what the servers page shows and checks before joining. `fp` is a short fingerprint of a mod's files (not its mod_manifest.json), equal when two PCs have the very same mod. |
| `dir_announce` | to a server started with `--directory`: `{id, secret, port, address?, name, description, players, max_players, password, version, protocol, mods_count, names, world}`. The directory checks the server answers `info` with that id at `address` (default: the announcer's IP and `port`), lists it, and answers `dir_announce {ok, address, reachable, error}`. A public server announces every 60 s; a listing goes after 180 s of silence. The `secret` (kept in server.json) stops anyone else from taking over or removing the listing. Limits (not for the directory's own machine): an `address` must lead to the announcer itself (an IP, or a name that resolves to it), otherwise the server is listed at the address it announced from; at most 500 listings in all, 4 per address and 5 new ones per address per 10 minutes (`ok: false` past that); a reachability check at most every 10 s per address; `port` must be 1-65535. The numbers (`port`, `players`, `max_players`, `protocol`, `mods_count`) are JSON numbers; a short string of digits is taken too, a longer one or anything that isn't a number counts as 0 (for `port`: a bad port). |
| `dir_remove` | `{id, secret}`: unlist (the server went private). |
| `dir_list` | `dir_list {servers: [{id, name, address, ...}]}`: the listed servers that can be reached (at most 200, the most recently heard from first). A server on the directory's own machine is given at the address the asker used. |

## LAN discovery (UDP)

Every server started through `start()` (stand-alone, "Host and play", "Host only") also listens on **UDP** at the same
port number (24242). A datagram `INZOI-MP?` (9 bytes, broadcast or not) gets one JSON datagram back:
`{"t":"discover","name","port","version","protocol","id","password":bool,"players","max_players","hosted"}`.
Only private, link-local, loopback and Tailscale (100.64.0.0/10) addresses are answered, each at most 5 times a
second. The companion broadcasts it on every IPv4 interface (and 255.255.255.255) when the picked server can't be
reached for ~15 s, or no server is picked, and plays on the answer whose `id` it joined last (or the only answer),
remembering that address as an alternate (`cfg["lan_alt"]`) without replacing the picked one. The firewall must allow
UDP 24242 in too.

## Admin console

The server's admins (data/server.json `admins`; the host of a "Host and play" server always is one) send
`{"t":"admin","op":...,"ref":n,...}` and get `{"t":"admin","ref":n,"op":...,"ok":true|false,"error"?,...}`:

| `op` | fields | |
|---|---|---|
| `status` | | settings, admin code, players online (with IP), everyone who has joined, bans, admins, world and its earlier versions, mods, shared state (`shared {keys, size, max_size, seq}`), public listing status, recent log lines |
| `set` | `name, description, motd, password, max_players, public, public_address, directories` (any) | saved; everyone gets `server_info {server}`; going public/private (un)lists it |
| `kick` / `ban` | `id, reason?` | the player gets `kicked {reason, by_admin}`; a banned player's hello gets `error {fatal, banned}` |
| `unban`, `admin_add`, `admin_remove` | `id` (admin_add also takes a name) | |
| `say` | `text` | a chat line from "Server" |
| `restart` | `in` (seconds) | stand-alone servers only: everyone saves (`restart_soon`), then the server exits with code 75 for systemd to start it again |
| `shared_remove` | `key` or `prefix` | removes that shared key (or every key starting with `prefix`) as a `set` with `value: null` would: everyone gets `shared {item: null, ref_by: null}`; answers `removed` (how many). For a shared state that's full (`too_big`) |
| `world_restore` | `version` | an earlier world version becomes the current one again (as a new version) |

`{"t":"admin_claim","code":"...","ref":n}` makes the sender an admin if the code is the server's admin code (printed by
`inzoi_mp_server.py --admin-code` on the server's machine, and shown to admins in their console). At most 5 tries a
minute per address; more get `{"t":"admin","op":"claim","ok":false,"code":"rate_limited","error":"..."}`.
From the server's own machine (127.0.0.1), before hello: `{"t":"make_admin","who":"<name or id>"}` (`--make-admin`).

## Server → client

`welcome`, `joined {player}`, `rejoined {player}`, `left {id,name}`, `leader {id}`, `state {id,state}`, `profile {id,profile}`,
`world {world}`, `modset {source,host,mods}`, `chat {id,name,text,time}`, `event {from,kind,data}`, `house_updated {meta}`, `house`, `house_list`,
`who`, `ping`, `pong`, `kicked {reason, by_admin?}`, `error {code, error, fatal?, banned?, ref?, key?, to?, kind?, id?, lingering?}`, `server_info {server, admin?}`, `admin {ref, op, ok, ...}`,
`shared`, `shared_all`, `set_refused` (see "Shared state").

## What the companion puts in messages (the server relays them as-is)

- `state.state` = `{n, x, y, z, yaw, city, site, act, anim, outfit, clock, sleep, lot, time, speed}`: sample counter, world position (cm) and facing
  (degrees) of the player's Zoi, its city map and lot, and
  - `act`: what it's doing, or null: `{bag}` alone, `{bag, player}` with another player's Zoi,
    `{bag, obj, ox, oy, oz}` with an object (table id + world position), `{bag, npc: true}` with someone only that
    world has
  - `anim`: the motion playing (shown when the action itself can't be copied)
  - `outfit`: the outfit category worn (Everyday, Sports, Swimwear...)
  - `clock`: `[hour, minute]` of the player's game while it runs (null while paused). With `clock_mode` "catchup" a
    player who's behind catches up with whoever is furthest ahead; with "host" (the default) only the leader sends it
    (the others send null) and the others follow the leader's clock and `speed`
  - `lot`: the name of the lot the Zoi is on ("" if unknown); `time`: `[hour, minute]` always (for showing, never
    followed); `speed`: the game's time scale
  - `sleep`: the player's Zoi is asleep; while everyone's is, everyone's game runs fast, otherwise at normal speed
- `profile.profile` = `{first_name, last_name, gender, growth_step, personality, characteristics, voice,
  gender_identity, look}` (look = the game's appearanceSetting JSON).
- `house_put.house` = `{name, by, lot, site, files: {filename: base64}}`: a lot from the player's in-game library
  (Documents/inZOI/Creations/MySites/<id>/). The stored meta is `{owner, updated, name, by, lot}`.
- `world.world` = `{season, weather}` from the leader; the others match them (unless they turned time sync off).

## Event kinds used by the companion (relayed opaquely by the server)

Defined by the companion app; the server never needs to know them.

- `couple` (client/couple.py), always with `to`: `data` = `{"type":"request","id","action","name","site"?}` (action
  `hug`, `kiss`, `dance`, `wave` or `invite`) or `{"type":"reply","id","accept"}`. On accept the asker's Zoi starts a
  hug/kiss on the partner's puppet (the partner's PC replays it through `state.act`); dance/wave start on both PCs;
  an accepted `invite` takes the guest's Zoi to the host's.

- `pose` (client/posesync.py), to everyone: `data` = `{"k": keyframe}` or `{"d": frame}`, the sender's Zoi skeleton
  (see lua/B1/Main.lua). The server keeps each player's latest `k` and `d` and sends them to whoever joins.

## Shared state (the truth for everyone)

The server keeps keyed values, each with a version (a server-wide sequence), who set it and when, in data/shared/.

- `{"t":"set","key","value","base"?,"ref"?}` → everyone (the sender too: its confirmation) gets
  `{"t":"shared","key","item":{"key","value","v","by","by_name","at"},"v","ref_by","ref"}`: `ref_by` is the sender's
  id and `ref` the set's own `ref`, so a sender knows its set went through when its `shared` with that ref comes back.
  `value: null` removes the key (`item: null`). With `base` (the version the change was made on, a JSON integer: anything
  else, a numeric string too, gets `bad_request`) a change on an older version gets
  `{"t":"set_refused","key","ref","current":item}` instead (to the sender only).
  Keys: `[a-z0-9_.:-]{1,96}`; values up to 4 MB of JSON; all values together up to 16 MB in at most 4096 keys (the
  welcome carries them all in one line). When a change would go past that, the server first makes room by removing
  old lots: every `house:<lot>:*` and `obj:<lot>:*` key of a lot, the lot changed longest ago first (by the items'
  `at`), each removal going to everyone as a `shared` with `item: null`, `ref_by: null`, until the change fits. Never
  removed for room: the lot of the key being set, the family's home (`family`'s `home_site`), a lot changed in the
  last 10 minutes, and any key that isn't a lot's (`family`); and nothing is removed unless that makes the change fit.
  A `family` change that moves the home keeps the new home's lot; the old home (no longer the home after it) is removed
  only if the other old lots aren't enough.
  A shared state already over the cap when the server starts (a data folder from a server without the cap) is brought
  under it before anyone can join, by the same rules (lots changed in the last 10 minutes go too if the others aren't
  enough): otherwise the welcome, which carries all of it in one line, could be too big for any companion to read.
  Errors to the sender only, with `key` and `ref`: `bad_key`; `too_big` (this value is over 4 MB, or the shared state
  is full even without the old lots: a change that makes it grow is refused; one that doesn't, and every removal, is
  always taken); `storage_failed` (the server couldn't save it: nothing changed, no version was used; try again). A
  change is saved before it's applied or sent, so what players saw is always what a restart brings back, and no
  version is handed out twice (versions only ever go up; there may be gaps). That holds for removals too (a set to
  null, room being made, an admin's `shared_remove`): the key's files are deleted and the folder flushed before
  anyone gets `item: null`; a batch deletes every key's files, flushes once, then sends the removals.
- The welcome carries `"shared": {key: item}`, `"seq"` and `"epoch"`; `{"t":"shared_get"}` →
  `{"t":"shared_all","shared","seq","epoch"}`. The `epoch` is a random hex string made once per data folder and kept
  with the shared state: a new or wiped folder has a new one, so versions a client remembers from another epoch mean
  nothing here (everything has to be looked at again).
- Keys used by the companion (client/shared.py): `house:<lot>:live`, `house:<lot>:exact`, `family`, `obj:<lot>:<object key>` (one object's states: client/objsync.py).

## Storage

data/*.json files are written to a .tmp, flushed (fsync), then swapped in with one atomic replace (on Linux the folder
is fsynced too), so a file always exists under its own name with either its old or its new content; the old content
stays as `.bak`. A file that's missing or damaged is recovered from what's left of its last save (its `.tmp` if
complete, then its `.bak`), with a warning in the log, and put back (a `.tmp` is renamed into place, never rewritten,
so a failure there leaves it for the next start; a copy that's locked is read again at the next start, and nothing is
written over it); a damaged file is kept as `.corrupt-<time>`.
Files of which only a `.bak` or `.tmp` is left are found at start like any other. players.json is saved at once for a
new player or a new name; "last seen" times every minute and when the server stops; a failed save never refuses a
join (it's retried). An uploaded blob is flushed (fsync) before it's renamed into place, and its folder after, before
`blob_ok`; one found damaged anyway (a blob stored by a server before that, cut short by a power cut) is deleted when
its owner's `blob_check` or world offer lists it with another size and its content isn't its sha256, so it's uploaded
again. Blobs (mod and world files, one folder) that no stored mod list, current world or kept world
version (nor an upload under way) uses are deleted at start and after each world save, once over an hour old;
unfinished uploads (`.part`) after a day.

## Stopping

Every way of stopping a server never waits on a player's connection: `stop(srv, tasks, goodbye)` (what the stand-alone
server does on SIGTERM / Ctrl+C / an admin's restart) stops accepting connections, sends the goodbye chat line, gives it
up to a second to go out, cuts every connection and ends the background tasks. A server started with `start()` may also
be stopped with `srv.close()`, `await srv.mp_server.shutdown(goodbye)` and `tasks.cancel()`.
