/**
 * app.js  --  inZOI Multiplayer panel.
 * Window management is handled by window.js (loaded before this file); see the `wf` global.
 * Text goes through i18n.js (the I18N global: t / tr / notice), loaded before this file.
 *
 * Everything comes from the companion app on this PC (client/companion.py) over XMLHttpRequest to 127.0.0.1:24243.
 * Gameface quirks: no fetch, POST + JSON body only (query strings get mangled), non-2xx answers arrive with an
 * empty body, so the companion always answers 200 and puts errors in {"error": "..."}.
 */
(function () {
    'use strict';

    var API = 'http://127.0.0.1:24243';
    var root = (window.wf && wf.root) || document.getElementById('app-root');   // wf is missing in a plain browser preview
    var $ = function (id) { return document.getElementById(id); };
    var I = window.I18N || { t: function (k) { return k; }, tr: function (x) { return x; },
                             notice: function (c, x) { return x; }, guessCode: function () { return null; },
                             apply: function () {}, setLang: function () {}, lang: function () { return 'en'; } };
    var t = I.t;
    var dot = document.querySelector('#title-text .dot');
    var label = $('title-label');
    var unread = 0, seenUpTo = 0, lastStatus = null, busy = false;
    var CHAT_KEEP = 100;

    I.apply(document);

    function post(path, body, cb) {
        var x = new XMLHttpRequest();
        x.open('POST', API + path, true);
        x.setRequestHeader('Content-Type', 'application/json');
        x.timeout = 4000;
        x.onreadystatechange = function () {
            if (x.readyState !== 4) { return; }
            var res = null;
            try { res = JSON.parse(x.responseText); } catch (e) { res = null; }
            if (cb) { cb(res); }
        };
        x.send(JSON.stringify(body || {}));
    }

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) { e.className = cls; }
        if (text !== undefined && text !== null) { e.appendChild(document.createTextNode(String(text))); }
        return e;
    }

    function btn(cls, text, disabled) {
        var b = el('button', 'btn' + (cls ? ' ' + cls : '') + (disabled ? ' off' : ''), text);
        b.setAttribute('type', 'button');
        if (disabled) { b.setAttribute('disabled', 'disabled'); }
        return b;
    }

    function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

    function setText(node, text) {
        text = text === null || text === undefined ? '' : String(text);
        if (node.textContent !== text) { node.textContent = text; }
    }

    // An error from the companion: by its code when it has one, else its (English) text, translated when known.
    function errText(r) {
        if (!r) { return null; }
        if (!r.error) { return null; }
        return I.notice(r.code || r.error_code || null, r.error);
    }

    // "Use_BarBell_Row" -> "barbell row", "Co_Friendship_G04_Start" -> "friendship"
    function prettyAction(bag) {
        if (!bag) { return ''; }
        var s = bag.replace(/^(Co_|Object_|Use_|Next_|Self_|Do_)/, '').replace(/_(G|B)\d+.*$/, '')
            .replace(/_(Start|Loop|End)\d*$/, '').replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2');
        return s.toLowerCase();
    }

    // ------------------------------------------------------------------ players (updated in place: a row that is
    // rebuilt every second swallows the click that lands while it's being replaced)
    var playerRows = {};          // id -> {row, name, sub, join, p}
    var playersEmpty = null;
    function playerSub(p) {
        var where = p.here ? t('Here with you') : (p.away ? t('In another city') : t('On the way...'));
        var doing = prettyAction(p.doing);
        return (p.zoi ? p.zoi + ' - ' : '') + where + (doing ? ' - ' + doing : '');
    }
    function renderPlayers(s) {
        var box = $('players');
        var seen = {}, order = [];
        if (!s.players.length) {
            for (var id in playerRows) { if (playerRows.hasOwnProperty(id)) { box.removeChild(playerRows[id].row); } }
            playerRows = {};
            var msg = s.connected ? t('Nobody else is online right now.') : t('Not connected yet.');
            if (!playersEmpty) { playersEmpty = el('div', 'empty'); box.appendChild(playersEmpty); }
            setText(playersEmpty, msg);
            return;
        }
        if (playersEmpty) { box.removeChild(playersEmpty); playersEmpty = null; }
        s.players.forEach(function (p) {
            var r = playerRows[p.id];
            if (!r) {
                r = { row: el('div', 'player'), name: el('div', 'name'), sub: el('div', 'sub'), join: btn('', t('Join')) };
                var who = el('div', 'who');
                who.appendChild(r.name);
                who.appendChild(r.sub);
                r.row.appendChild(who);
                r.row.appendChild(r.join);
                (function (rr) {
                    rr.join.addEventListener('click', function () {
                        if (!rr.p || !rr.p.here) { return; }
                        post('/join', { id: rr.p.id }, function (res) { flash(res ? errText(res) : t(NOT_RUNNING)); });
                    });
                })(r);
                playerRows[p.id] = r;
            }
            r.p = p;
            setText(r.name, p.name);
            setText(r.sub, playerSub(p));
            var subCls = 'sub' + (p.here ? ' here' : '');
            if (r.sub.className !== subCls) { r.sub.className = subCls; }
            var joinCls = 'btn' + (p.here ? '' : ' off');
            if (r.join.className !== joinCls) { r.join.className = joinCls; }
            if (p.here) { r.join.removeAttribute('disabled'); } else { r.join.setAttribute('disabled', 'disabled'); }
            setText(r.join, t('Join'));
            r.join.setAttribute('title', t('Go to {name}', { name: p.name }));
            seen[p.id] = true;
            order.push(r.row);
        });
        for (var k in playerRows) {
            if (playerRows.hasOwnProperty(k) && !seen[k]) { box.removeChild(playerRows[k].row); delete playerRows[k]; }
        }
        for (var i = 0; i < order.length; i++) {                    // move rows only when the order changed
            if (box.children[i] !== order[i]) { box.insertBefore(order[i], box.children[i] || null); }
        }
    }

    // ------------------------------------------------------------------ chat (the companion sends its last 30 lines;
    // the panel keeps the last CHAT_KEEP and appends only the new ones, so scrolling back works)
    var chatKeys = {}, chatCount = 0;
    function chatKey(m) { return m.time + '|' + (m.name || '') + '|' + m.text; }
    function renderChat(s) {
        var lines = s.chat;
        var newest = lines.length ? lines[lines.length - 1].time : 0;
        unread = 0;
        if (root._minimized) {
            lines.forEach(function (m) { if (!m.mine && !m.system && m.time > seenUpTo) { unread++; } });
        } else {
            seenUpTo = Math.max(seenUpTo, newest);
        }
        var box = $('chat');
        var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 30;
        var added = false;
        lines.forEach(function (m) {
            var key = chatKey(m);
            if (chatKeys[key]) { return; }
            chatKeys[key] = true;
            var row = el('div', 'msg' + (m.mine ? ' mine' : '') + (m.system ? ' system' : ''));
            if (!m.system) { row.appendChild(el('span', 'from', m.name)); }
            row.appendChild(document.createTextNode(m.system ? I.tr(m.text) : m.text));
            row.setAttribute('data-key', key);
            box.appendChild(row);
            chatCount++;
            added = true;
        });
        while (chatCount > CHAT_KEEP && box.firstChild) {
            var old = box.firstChild;
            delete chatKeys[old.getAttribute('data-key')];
            box.removeChild(old);
            chatCount--;
        }
        if (added && atBottom) { box.scrollTop = box.scrollHeight; }
    }

    function renderTitle(s) {
        var n = s ? s.players.length : 0;
        dot.className = 'dot' + (s && s.connected ? ' on' : (s ? ' wait' : ''));
        var text = t('inZOI Multiplayer');
        if (s && s.connected) {
            text += ' - ' + (n ? (n === 1 ? t('1 friend online') : t('{n} friends online', { n: n })) : t('online'));
        }
        clear(label);
        label.appendChild(document.createTextNode(text));
        if (unread && root._minimized) { label.appendChild(el('span', 'badge', t('{n} new', { n: unread }))); }
    }

    // ------------------------------------------------------------------ notices
    // A message from something the user did (shown for a while), or the companion's current notice while connected.
    // When the state no longer has a notice, it goes away (a notice that was solved doesn't stay on screen).
    var flashText = null, flashUntil = 0;
    function flash(text, ms) {
        flashText = text || null;
        flashUntil = text ? Date.now() + (ms || 6000) : 0;
        showNotice(flashText);
    }
    function showNotice(text) {
        var n = $('notice');
        n.style.display = text ? 'block' : 'none';
        setText(n, text || '');
    }
    function renderNotice(s) {
        if (flashText && Date.now() < flashUntil) { showNotice(flashText); return; }
        flashText = null;
        // while disconnected the banner shows the reason; while connected, a notice the companion still has
        showNotice(s && s.connected && s.notice ? I.notice(s.notice_code, s.notice) : null);
    }

    var NOT_RUNNING = "The inZOI Multiplayer program isn't running. Open \"inZOI Multiplayer.exe\" and keep its window open while you play.";

    // ------------------------------------------------------------------ the connection banner
    var FATAL = { wrong_password: 1, version_mismatch: 1, full: 1, banned: 1, bad_request: 1 };
    var lastPartners = [], droppedName = null, droppedAt = 0, disconnectedSince = 0, retrying = 0;
    function renderConn(s) {
        var box = $('conn'), ico = $('conn-ico'), main = $('status'), sub = $('conn-sub'), retry = $('conn-retry');
        var cls = 'wait', icon = '…', text = '', subText = '', showRetry = false;
        main.removeAttribute('data-i18n');
        if (!s) {
            cls = 'bad'; icon = '!';
            text = t("The program isn't open");
            subText = t(NOT_RUNNING);
            disconnectedSince = disconnectedSince || Date.now();
        } else if (s.connected) {
            disconnectedSince = 0;
            var names = s.players.map(function (p) { return p.name; });
            if (names.length) {
                droppedName = null;
                droppedAt = 0;
                lastPartners = names;
                cls = 'on'; icon = '✓';
                text = t('Connected with {names}', { names: names.join(', ') });
            } else {
                if (lastPartners.length && !droppedName) { droppedName = lastPartners.join(', '); droppedAt = Date.now(); }
                if (droppedName && Date.now() - droppedAt > 120000) { droppedName = null; lastPartners = []; }
                if (droppedName) {
                    cls = 'wait'; icon = '…';
                    text = t('{name} dropped, waiting...', { name: droppedName });
                } else {
                    cls = 'on'; icon = '✓';
                    text = t('Connected to {server}: waiting for your partner', { server: s.server });
                }
            }
            subText = t('You are {name} on {server}', { name: s.name, server: s.server }) +
                (s.mod !== 'running' ? ' (' + t('getting the game ready') + ')' : '');
        } else {
            disconnectedSince = disconnectedSince || Date.now();
            var code = s.notice_code || I.guessCode(s.notice);
            if (code && FATAL[code]) {
                cls = 'bad'; icon = '!';
                text = t("Couldn't connect: {reason}", { reason: I.notice(code, s.notice) });
                showRetry = true;
            } else {
                cls = 'wait'; icon = '↻';
                text = t('Reconnecting to {server}...', { server: s.server });
                if (s.notice) { subText = I.notice(s.notice_code, s.notice); }
                showRetry = Date.now() - disconnectedSince > 10000;     // give the automatic retry a moment first
            }
            if (s.mod !== 'running') { subText += (subText ? ' ' : '') + '(' + t('getting the game ready') + ')'; }
        }
        if (retrying && Date.now() - retrying < 5000) { showRetry = true; }
        if (box.className !== 'conn ' + cls) { box.className = 'conn ' + cls; }
        setText(ico, icon);
        setText(main, text);
        setText(sub, subText);
        sub.style.display = subText ? '' : 'none';
        retry.style.display = showRetry ? '' : 'none';
        var rt = retrying && Date.now() - retrying < 5000 ? t('Trying again...') : t('Try again');
        setText(retry, rt);
    }
    // "Try again": join the server we're set to again (the servers page's own join call; the saved password is used).
    $('conn-retry').addEventListener('click', function () {
        if (retrying && Date.now() - retrying < 5000) { return; }
        retrying = Date.now();
        renderConn(lastStatus);
        post('/servers/state', {}, function (st) {
            if (!st || !st.current) {
                retrying = 0;
                flash(st ? I.notice(st.notice_code || st.error_code || st.code, st.error || '') : t(NOT_RUNNING));
                return;
            }
            post('/servers/join', { address: st.current }, function (r) {
                if (!r) { flash(t(NOT_RUNNING)); } else if (r.error) { flash(errText(r)); }
                disconnectedSince = Date.now();
                poll();
            });
        });
    });

    function render(s) {
        renderConn(s);
        renderNotice(s);
        if (!s) {
            renderTitle(null);
            return;
        }
        renderPlayers(s);
        renderChat(s);
        renderTitle(s);
        renderMods(s.mods);
        renderSave(s.save);
        renderBox('update', s.update && s.update.box);
        renderBox('world', s.world && s.world.box);
        setText($('s-version'), t('inZOI Multiplayer {v}', { v: s.version }) +
            (s.save && s.save.name ? t(' - multiplayer save: {name}', { name: s.save.name }) : ''));
        $('s-sync').className = 'toggle' + (s.sync_time ? ' on' : '');
        $('s-sync').setAttribute('aria-checked', s.sync_time ? 'true' : 'false');
        $('s-sync').style.display = s.host ? 'none' : '';
        if (!lastStatus) {
            $('s-name').value = s.name;
            $('s-server').value = s.server;
            $('s-port').value = s.port;
            seenUpTo = s.chat.length ? s.chat[s.chat.length - 1].time : 0;
        }
        lastStatus = s;
    }

    function poll() {
        if (busy) { return; }
        busy = true;
        post('/status', {}, function (s) { busy = false; render(s); });
    }

    // ------------------------------------------------------------------ input
    function send() {
        var input = $('say');
        var text = input.value.replace(/^\s+|\s+$/g, '');
        if (!text) { return; }
        input.value = '';
        post('/chat', { text: text }, function (r) { if (r && r.error) { flash(errText(r)); } poll(); });
    }
    $('send').addEventListener('click', send);
    $('say').addEventListener('keydown', function (e) {
        if (e.keyCode === 13) { send(); e.preventDefault(); }
        e.stopPropagation();                         // typing must not drive the game camera / hotkeys
    });

    // Password: the field starts empty (the saved one isn't shown). Typing changes it; clearing what you typed, or
    // "No password", removes it (sent as an empty string). Untouched, it isn't sent and stays as it was.
    var pwTouched = false;
    function pwPlaceholder() {
        var k = pwTouched && !$('s-password').value ? 'Will be removed when you save' : 'Unchanged unless you type';
        $('s-password').setAttribute('data-i18n-ph', k);
        $('s-password').setAttribute('placeholder', t(k));
    }
    $('s-password').addEventListener('input', function () { pwTouched = true; pwPlaceholder(); });
    $('s-nopw').addEventListener('click', function () { $('s-password').value = ''; pwTouched = true; pwPlaceholder(); });
    $('s-save').addEventListener('click', function () {
        var body = { name: $('s-name').value, server: $('s-server').value };
        if ($('s-port').value) { body.port = $('s-port').value; }
        if (pwTouched) { body.password = $('s-password').value; }
        post('/settings', body, function (r) {
            if (r && !r.error) { pwTouched = false; $('s-password').value = ''; pwPlaceholder(); }
            flash(r ? (errText(r) || t('Saved - reconnecting...')) : t(NOT_RUNNING));
        });
    });
    $('servers-btn').addEventListener('click', function (e) {
        e.stopPropagation();                         // not a title-bar click (that restores a minimized panel)
        post('/page/open', {}, function (r) { if (!r || r.error) { flash(r ? errText(r) : t(NOT_RUNNING)); } });
    });
    $('s-sync').addEventListener('click', function () {
        var on = $('s-sync').className.indexOf(' on') < 0;
        $('s-sync').className = 'toggle' + (on ? ' on' : '');
        $('s-sync').setAttribute('aria-checked', on ? 'true' : 'false');
        post('/settings', { sync_time: on }, function (r) { if (!r || r.error) { flash(r ? errText(r) : t(NOT_RUNNING)); } });
    });
    $('s-lang').value = I.lang();
    $('s-lang').addEventListener('change', function () {
        I.setLang($('s-lang').value);
        shownBoxes = {};
        modsKey = null;
        render(lastStatus);
    });
    ['s-name', 's-server', 's-port', 's-password'].forEach(function (id) {
        $(id).addEventListener('keydown', function (e) { e.stopPropagation(); });
    });

    // ------------------------------------------------------------------ questions: the multiplayer save, updates
    var shownBoxes = {};
    function renderBox(name, box) {                             // name: 'save', 'update' or 'world' (also its endpoint)
        var wrap = $(name + '-box'), buttons = $(name + '-buttons');
        wrap.style.display = box ? 'block' : 'none';
        if (!box) { shownBoxes[name] = null; return; }
        setText($(name + '-text'), I.tr(box.text));
        if (shownBoxes[name] === box.key + box.text) { return; }   // keep the buttons while they're being clicked
        shownBoxes[name] = box.key + box.text;
        clear(buttons);
        box.buttons.forEach(function (b) {
            var bt = btn('', I.tr(b.label));
            bt.addEventListener('click', function () {
                post('/' + name, { key: box.key, action: b.id }, function (r) {
                    if (!r) { flash(t(NOT_RUNNING)); } else { shownBoxes[name] = null; poll(); }
                });
            });
            buttons.appendChild(bt);
        });
    }
    function renderSave(sv) { renderBox('save', sv && sv.box); }

    // ------------------------------------------------------------------ mods
    var modsKey = null, modsListKey = null;
    function sizeText(n) {
        n = n || 0;
        return n < 104857600 ? (n / 1048576).toFixed(1) + ' MB' : Math.round(n / 1048576) + ' MB';
    }
    function renderMods(m) {
        m = m || {};
        var o = m.offer, text = '', ask = false, bar = false;
        var who = o ? (o.from.length ? o.from.join(', ') : t('someone')) : '';
        modsKey = o ? o.key : null;
        if (!m.source) {
            text = t('Waiting for the server...');
        } else if (!o || o.state === 'done') {
            text = m.count ? (m.source === 'host' ? t('You have all the mods the host uses.') : t('You have all the mods your friends use.'))
                : (m.source === 'host' ? t('The host uses no mods.') : t('Nobody has shared any mods yet.'));
            if (o) { text = t('Mods installed. {msg}', { msg: I.tr(o.message || '') }); }
        } else if (o.state === 'ask' || o.state === 'declined' || o.state === 'error') {
            var v = { who: who, n: o.names.length, size: sizeText(o.size) };
            text = m.source === 'host'
                ? t("The host ({who}) uses {n} mod(s) you don't have ({size}). Without them, some of their clothes or things won't show up for you. Download them?", v)
                : t("Your friends ({who}) use {n} mod(s) you don't have ({size}). Without them, some of their clothes or things won't show up for you. Download them?", v);
            if (o.state === 'declined') { text = t('You chose not to download {n} mod(s) ({size}).', v); }
            if (o.state === 'error') { text = t('The download stopped: {msg}', { msg: I.tr(o.message) }); }
            ask = true;
        } else if (o.state === 'downloading' || o.state === 'waiting') {
            text = o.state === 'waiting' ? t('Waiting for {who} to finish sharing them...', { who: who })
                : t('Downloading {n} mod(s): {done} of {total}', { n: o.names.length, done: sizeText(o.done), total: sizeText(o.total) });
            bar = true;
        } else if (o.state === 'installing') {
            text = t('Installing...');
        } else {
            text = I.tr(o.message);
        }
        var frac = o && o.total ? o.done / o.total : 0;
        if (m.sharing && !bar) {
            frac = m.sharing.done / Math.max(1, m.sharing.total);
            text += ' ' + t('Sharing your mods with friends: {done} of {total}', { done: sizeText(m.sharing.done), total: sizeText(m.sharing.total) });
            bar = true;
        }
        if (bar) { text += ' (' + Math.floor(100 * frac) + '%)'; }
        setText($('m-state'), text);
        $('m-buttons').style.display = ask ? 'block' : 'none';
        setText($('m-yes'), o && o.state === 'error' ? t('Try again') : t('Download'));
        $('m-no').style.display = o && o.state === 'ask' ? '' : 'none';
        $('m-bar').style.display = bar ? 'block' : 'none';
        $('m-fill').style.width = Math.floor(100 * Math.min(1, frac)) + '%';
        var tab = $('tab-mods'), alert = o && o.state === 'ask';
        setText(tab, alert ? t('Mods ({n})', { n: o.names.length }) : t('Mods'));
        tab.className = 'tab' + (tab.className.indexOf(' on') >= 0 ? ' on' : '') + (alert ? ' alert' : '');
        var listKey = I.lang() + JSON.stringify(m.list || []);
        if (listKey === modsListKey) { return; }
        modsListKey = listKey;
        var list = $('m-list');
        clear(list);
        if (!(m.list || []).length) { list.appendChild(el('div', 'empty', t('None yet.'))); }
        (m.list || []).forEach(function (x) {
            var row = el('div', 'mod');
            row.appendChild(el('span', x.have ? 'ok' : 'no', x.have ? 'OK' : '--'));
            row.appendChild(document.createTextNode(x.name));
            list.appendChild(row);
        });
    }
    function answerMods(yes) {
        if (!modsKey) { return; }
        post('/mods', { key: modsKey, answer: yes ? 'yes' : 'no' }, function (r) {
            if (!r) { flash(t(NOT_RUNNING)); } else { renderMods(r); }
        });
    }
    $('m-yes').addEventListener('click', function () { answerMods(true); });
    $('m-no').addEventListener('click', function () { answerMods(false); });

    // ------------------------------------------------------------------ houses
    function houseRow(title, sub, text, on, cb) {
        var row = el('div', 'house');
        var tt = el('div', 't', title);
        if (sub) { tt.appendChild(el('div', 's', sub)); }
        row.appendChild(tt);
        var b = btn('', text, !on);
        if (on) { b.addEventListener('click', cb); }
        row.appendChild(b);
        return row;
    }

    var housesKey = null;
    function loadHouses() {
        post('/houses', {}, function (h) {
            if (!h || h.error) { flash(h ? errText(h) : t(NOT_RUNNING)); return; }
            var key = I.lang() + JSON.stringify(h);
            if (key === housesKey) { return; }                     // unchanged: keep the buttons under the cursor
            housesKey = key;
            var mine = $('h-mine'), shared = $('h-shared');
            clear(mine);
            clear(shared);
            if (!h.library.length) { mine.appendChild(el('div', 'empty', t('No saved houses in your library yet.'))); }
            h.library.slice(0, 6).forEach(function (x) {
                var sharing = h.sharing === x.title;
                mine.appendChild(houseRow(x.title, x.beds ? t('{size}, {beds} bed', { size: x.size, beds: x.beds }) : x.size,
                    sharing ? t('Shared') : t('Share'), !sharing, function () {
                        post('/share', { id: x.id }, function (r) {
                            flash(r ? (errText(r) || t('Shared "{name}" with everyone.', { name: r.name })) : null);
                            housesKey = null;
                            loadHouses();
                        });
                    }));
            });
            if (!h.shared.length) { shared.appendChild(el('div', 'empty', t('Nobody has shared a house yet.'))); }
            h.shared.forEach(function (x) {
                shared.appendChild(houseRow(x.name, x.lot ? t('by {by} - goes on {lot}', { by: x.by, lot: x.lot }) : t('by {by}', { by: x.by }),
                    x.added ? t('Added') : t('Add to library'), !x.added,
                    function () {
                        flash(t('Downloading {name}...', { name: x.name }), 30000);
                        post('/add_house', { owner: x.owner }, function (r) {
                            flash(r ? (errText(r) || t('Added "{title}" to your library. Place it in Build mode.', { title: r.title })) : null);
                            housesKey = null;
                            loadHouses();
                        });
                    }));
            });
        });
    }
    var housesTimer = null;
    function showTab(name) {
        var tabs = document.querySelectorAll('.tab');
        for (var i = 0; i < tabs.length; i++) {
            var on = tabs[i].getAttribute('data-tab') === name;
            tabs[i].className = 'tab' + (on ? ' on' : '') + (tabs[i].className.indexOf(' alert') >= 0 ? ' alert' : '');
            tabs[i].setAttribute('aria-selected', on ? 'true' : 'false');
        }
        ['people', 'houses', 'mods', 'settings'].forEach(function (p) {
            $('pane-' + p).className = 'pane' + (p === name ? ' on' : '');
        });
        if (housesTimer) { clearInterval(housesTimer); housesTimer = null; }
        if (name === 'houses') { housesKey = null; loadHouses(); housesTimer = setInterval(loadHouses, 5000); }
    }
    (function () {
        var tabs = document.querySelectorAll('.tab');
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].addEventListener('click', function (e) { showTab(e.currentTarget.getAttribute('data-tab')); });
        }
    })();

    poll();
    setInterval(function () { if (!root._minimized) { poll(); } }, 1000);
    setInterval(function () { if (root._minimized) { poll(); } }, 3000);
})();
