# inZOI Multiplayer wire protocol (v1)

TCP, one JSON object per line (UTF-8, `\n`-terminated). Every message has `t` (its type). Default port 24242.

## Joining

Client → `{"t":"hello","protocol":1,"key":"<16-128 chars, generated once per player and kept secret>","name":"Ana","password":"","host":false,"version":"0.8.1"}`
(`version`, optional: the companion app's version; passed on to the others in `Player.version` so they can warn on a
mismatch. A different app version is never refused: only `protocol` must match.)

Server → `{"t":"welcome","id":"p1a2b3c4","server_version":"0.8.0","protocol":1,"leader":"<player id>","host":"<player id>"|null,"world":{...}|null,"players":[Player...],"houses":[HouseMeta...],"modset":ModSet,"world_save":WorldMeta|null,"motd":"","server":{id,name,description,motd,public,password,max_players,version,hosted,directory},"admin":false}`
or `{"t":"error","fatal":true,"code":"...","error":"..."}` and the connection closes.

Every `error` has a machine-readable `code` (for the UI to translate) next to the English `error` text:
`wrong_password`, `version_mismatch` (also carries `protocol`, `server_version`), `bad_key`, `banned` (also
`banned: true`), `full`, `busy` (too many connections: 64 in all, 12 per address except 127.0.0.1), `too_large` (a
line over 64 KB before a successful hello, over 32 MB after), all fatal; and, not fatal (the connection stays open),
`bad_json`, `bad_request` (a malformed message; carries the message's `ref` if it had one), `hello_first`,
`wrong_connection`, `unknown_type`, `not_online`. Passwords are compared as UTF-8 bytes (accents are fine).

`host: true` counts only on a server started inside a player's program ("Host and play", `Server(hosted=True)`) and only
from that same PC (127.0.0.1): that player becomes the host (their mods are everyone's, they lead the clock).

A second connection with `"files": true` in its hello is the same player's mod file connection: the server answers
`{"t":"files_ok"}` and accepts only `blob_check`, `blob_put`, `blob_get` (and ping/pong) on it.

`Player` = `{"id","name","profile","state","leader","version"}`. The `id` is stable for a key, so houses and profiles survive reconnects.
Connecting again with the same key replaces the old session (it gets `{"t":"kicked"}`) without a break: the others get
no `left`/`joined`, only `{"t":"rejoined","player":Player}` (the player keeps their last state and the clock if they
led it), so their Zoi stays in everyone's world. Companions that don't know `rejoined` ignore it.

## Client → server

| `t` | fields | what happens |
|---|---|---|
| `state` | `state` | live Zoi state (position, rotation, animation, interaction, site); relayed to everyone else as `state` |
| `profile` | `profile` | the player's Zoi (name, appearance, traits); stored, relayed as `profile` |
| `world` | `world` | clock/weather/season; only accepted from the leader, relayed as `world` |
| `take_leader` | | become the world-clock leader; everyone gets `leader`. On a hosted server while the host is online only the host can: anyone else just gets `leader` back with the host's id |
| `chat` | `text` (≤500) | broadcast to everyone, including the sender, as `chat` |
| `event` | `kind`, `data`, `to?` | generic relay to one player (`to`) or everyone else; arrives as `event` with `from` |
| `house_put` | `house` | store a snapshot of your house; everyone gets `house_updated` |
| `house_get` | `owner` | reply `house` with `meta` and `house` (null if none) |
| `house_list` | | reply `house_list` |
| `who` | | reply `who` with everyone online |
| `mods` | `mods: [Mod]` | the player's enabled mods; stored; everyone gets `modset` if the set changed |
| `ping` / `pong` | `echo?` | keepalive; the server pings every 15 s and drops clients silent for 45 s (file connections get no pings and are dropped after 10 minutes of silence) |

## Mod file connection (`files: true`)

| `t` | fields | answer |
|---|---|---|
| `blob_check` | `h: [sha256]` | `blob_missing {h}`: the ones the server doesn't have yet |
| `blob_put` | `h, o (offset), d (base64), end` | in order, only files of your own mods; pieces up to 8 MB; after `end`: `blob_ok {h}` or `blob_bad {h, error}`. A wrong offset gets `blob_bad {h, error, o}` with the offset expected; a bad piece (not base64, too big, past the file's size) discards the upload: `blob_bad {h, error, o: 0}` |
| `blob_get` | `h, o` | `blob {h, o, d, end}` (512 KB pieces), or `blob {h, error: "not shared" / "not uploaded yet"}` |

`Mod` = `{id, name, version, built, path, size, files: [{p, h, s}]}`: the mod_manifest's ProjectName, FriendlyName,
Version and PackageTime, its folder under Documents/inZOI/Mods, and each file's path, sha256 and size.
`ModSet` = `{source: "host"|"everyone", host, mods: [Mod + by: [names]]}`: hosted, the host's mods; otherwise everyone's
pooled (the newest build of a mod wins).

## Before joining: info and the public list (no hello needed)

| `t` | answer |
|---|---|
| `info` | `info {id, name, description, motd, public, password (bool), max_players, players, names, host, hosted, version, protocol, directory, uptime, world, mods: {source, list: [{id, name, version, built, size, fp}]}}`: what the servers page shows and checks before joining. `fp` is a short fingerprint of a mod's files (not its mod_manifest.json), equal when two PCs have the very same mod. |
| `dir_announce` | to a server started with `--directory`: `{id, secret, port, address?, name, description, players, max_players, password, version, protocol, mods_count, names, world}`. The directory checks the server answers `info` with that id at `address` (default: the announcer's IP and `port`), lists it, and answers `dir_announce {ok, address, reachable, error}`. A public server announces every 60 s; a listing goes after 180 s of silence. The `secret` (kept in server.json) stops anyone else from taking over or removing the listing. |
| `dir_remove` | `{id, secret}`: unlist (the server went private). |
| `dir_list` | `dir_list {servers: [{id, name, address, ...}]}`: the listed servers that can be reached. A server on the directory's own machine is given at the address the asker used. |

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
| `status` | | settings, admin code, players online (with IP), everyone who has joined, bans, admins, world and its earlier versions, mods, public listing status, recent log lines |
| `set` | `name, description, motd, password, max_players, public, public_address, directories` (any) | saved; everyone gets `server_info {server}`; going public/private (un)lists it |
| `kick` / `ban` | `id, reason?` | the player gets `kicked {reason, by_admin}`; a banned player's hello gets `error {fatal, banned}` |
| `unban`, `admin_add`, `admin_remove` | `id` (admin_add also takes a name) | |
| `say` | `text` | a chat line from "Server" |
| `restart` | `in` (seconds) | stand-alone servers only: everyone saves (`restart_soon`), then the server exits with code 75 for systemd to start it again |
| `world_restore` | `version` | an earlier world version becomes the current one again (as a new version) |

`{"t":"admin_claim","code":"...","ref":n}` makes the sender an admin if the code is the server's admin code (printed by
`inzoi_mp_server.py --admin-code` on the server's machine, and shown to admins in their console). At most 5 tries a
minute per address; more get `{"t":"admin","op":"claim","ok":false,"code":"rate_limited","error":"..."}`.
From the server's own machine (127.0.0.1), before hello: `{"t":"make_admin","who":"<name or id>"}` (`--make-admin`).

## Server → client

`welcome`, `joined {player}`, `rejoined {player}`, `left {id,name}`, `leader {id}`, `state {id,state}`, `profile {id,profile}`,
`world {world}`, `modset {source,host,mods}`, `chat {id,name,text,time}`, `event {from,kind,data}`, `house_updated {meta}`, `house`, `house_list`,
`who`, `ping`, `pong`, `kicked {reason, by_admin?}`, `error {code, error, fatal?, banned?, ref?}`, `server_info {server, admin?}`, `admin {ref, op, ok, ...}`.

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
  `{"t":"shared","key","item":{"key","value","v","by","by_name","at"},"v","ref_by","ref"}`; `value: null` removes the
  key (`item: null`). With `base` (the version the change was made on) a change on an older version gets
  `{"t":"set_refused","key","ref","current":item}` instead. Keys: `[a-z0-9_.:-]{1,96}`; values up to 4 MB of JSON.
- The welcome carries `"shared": {key: item}` and `"seq"`; `{"t":"shared_get"}` → `{"t":"shared_all","shared","seq"}`.
- Keys used by the companion (client/shared.py): `house:<lot>:live`, `house:<lot>:exact`, `family`.

## Storage

data/*.json files are written to a .tmp, flushed (fsync) and swapped in; the previous copy stays as `.bak`. A damaged
file is renamed `.corrupt-<time>` and its `.bak` used, with a warning in the log. Blobs (mod and world files, one
folder) that no stored mod list, current world or kept world version (nor an upload under way) uses are deleted at start
and after each world save, once over an hour old; unfinished uploads (`.part`) after a day.
