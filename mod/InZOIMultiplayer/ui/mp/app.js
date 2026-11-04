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
    // the panel's size: smaller by default, - / + in the header or Ctrl + mouse wheel over it; kept on this PC
    var scale = 0.8;
    try { scale = parseFloat(localStorage.getItem('mp.scale')) || 0.8; } catch (e) { scale = 0.8; }
    function applyScale() {
        scale = Math.max(0.55, Math.min(1.3, Math.round(scale * 100) / 100));
        root.style.transformOrigin = 'top left';       // (always the same corner: the panel stays where it was left)
        root.style.transform = 'scale(' + scale + ')';
        try { localStorage.setItem('mp.scale', String(scale)); } catch (e) {}
    }
    applyScale();
    $('sz-down').addEventListener('click', function (e) { e.stopPropagation(); scale -= 0.1; applyScale(); });
    $('sz-up').addEventListener('click', function (e) { e.stopPropagation(); scale += 0.1; applyScale(); });
    root.addEventListener('wheel', function (e) {
        if (!e.ctrlKey) { return; }
        e.preventDefault();
        scale += (e.deltaY < 0 ? 0.05 : -0.05);
        applyScale();
    });
    setInterval(function () {
        var cls = modalWas ? 'modal' : (root._minimized ? 'min' : '');
        if (root.className !== cls) { root.className = cls; if (lastStatus) { renderTitle(lastStatus); } }
    }, 150);
    [2500, 5000].forEach(function (ms) {
        setTimeout(function () {
            if (!userOpened && !root._minimized && root._applyMinimize && !document.querySelector('.req')) { root._applyMinimize(true); }
        }, ms);
    });
    var userOpened = false;
    $('title-bar').addEventListener('click', function () { userOpened = true; });
    // Open while the mouse is on it, a capsule again a moment after it leaves (the player's choice, 0.9.71) - but never
    // while a question waits for an answer, something is being typed or chosen, or the panel is being dragged.
    var hovered = false, hoverAt = 0, leftAt = 0;
    root.addEventListener('mouseover', function () {
        if (!hovered) { hovered = true; hoverAt = Date.now(); }
    });
    root.addEventListener('mouseout', function (e) {
        var to = e.relatedTarget;
        if (to && root.contains(to)) { return; }               // (from one of its parts to another: still on it)
        hovered = false; leftAt = Date.now();
    });
    function busy() {
        var a = document.activeElement;
        return !!modalWas || !!document.querySelector('.req') || root._dragging ||
            (a && a !== document.body && root.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName));
    }
    setInterval(function () {
        if (!root._applyMinimize) { return; }
        var now = Date.now();
        if (hovered && root._minimized && now - hoverAt > 120) {
            userOpened = true;
            root._applyMinimize(false);
        } else if (!hovered && !root._minimized && now - leftAt > 700 && !busy()) {
            root._applyMinimize(true);
        }
    }, 100);
    setTimeout(function () {
        if (!root._minimized && root._applyMinimize && !document.querySelector('.req')) { root._applyMinimize(true); }
    }, 900);

    function post(path, body, cb, ms) {                // ms: how long to wait (default 4 s; the self-test takes longer)
        var x = new XMLHttpRequest();
        x.open('POST', API + path, true);
        x.setRequestHeader('Content-Type', 'application/json');
        x.timeout = ms || 4000;
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
    // What a Zoi is doing, from the game's interaction id (it gives no readable name): the first activity whose
    // words are in the id; something unknown is just "busy" (never the raw id).
    var ACTIVITIES = [
        [/sleep|nap|bed/i, 'sleeping'], [/shower|bath(?!room)/i, 'taking a shower'], [/toilet|pee/i, 'in the bathroom'],
        [/cook|kitchen|stove|fridge/i, 'cooking'], [/(^|_)eat|meal|food|snack|dine/i, 'eating'],
        [/drink|coffee|(^|_)tea(_|$)|juice|beer|wine/i, 'having a drink'], [/newspaper|read|book|magazine/i, 'reading'],
        [/phone|smartphone|call|message|sns/i, 'on the phone'], [/tv|television|watch/i, 'watching TV'],
        [/computer|laptop|pc_|desk/i, 'on the computer'], [/kiss|peck/i, 'kissing'], [/hug/i, 'hugging'],
        [/dance/i, 'dancing'], [/selfie|pic|photo|camera/i, 'taking photos'],
        [/guitar|piano|violin|music|sing/i, 'playing music'], [/game|play/i, 'playing'],
        [/exercise|workout|gym|jog|run|yoga|stretch/i, 'exercising'], [/clean|wash|mop|vacuum|dish/i, 'cleaning'],
        [/appearance|mirror|makeup|dress|outfit|care/i, 'getting ready'], [/work|job|career/i, 'working'],
        [/garden|plant|water_/i, 'gardening'], [/^co_|social|talk|chat|friendship|romance|business/i, 'talking'],
        [/sit|chair|sofa|couch|bench/i, 'sitting'], [/stand|idle|wait/i, 'standing'],
        [/walk|move|turn|stop|forward|run_to|goto/i, 'walking']
    ];
    function prettyAction(bag) {
        if (!bag) { return ''; }
        for (var i = 0; i < ACTIVITIES.length; i++) {
            if (ACTIVITIES[i][0].test(bag)) { return t(ACTIVITIES[i][1]); }
        }
        return t('busy with something');
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
        if (root._minimized || currentTab !== 'people') {
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

    var askedOpen = false;               // a question about the save opened the panel once (closed again: it stays closed)
    function renderTitle(s) {
        var p = s ? pickPartner(s) : null;
        var on = !!(s && s.connected);
        dot.className = 'dot' + (on && p && p.online ? ' on' : (s && !on ? ' wait' : ''));
        var name = t('inZOI Co-op'), sub = '';
        if (!s) { sub = t('Looking for the inZOI Multiplayer program...'); }
        else if (!on) { sub = t('Not connected'); }
        else if (!p) { sub = t('Waiting for your partner'); }
        else {
            name = p.name;
            sub = !p.online ? t('Offline') : (p.here ? (p.sleep ? t('Sleeping') : (prettyAction(p.action) || t('Near you')))
                : (p.away ? t('In another city') : t('Online')));
        }
        setText(label, name);
        setText($('title-sub'), sub);
        setText($('t-initial'), p ? String(p.name || '?').charAt(0).toUpperCase() : '+');
        var waiting = unread + ((s && s.requests) || []).length;
        var asks = !!(s && ((s.save && s.save.box) || (s.world && s.world.box) || (s.update && s.update.box &&
            s.update.box.buttons && s.update.box.buttons.length)));
        if (asks && root._minimized && root._applyMinimize && !askedOpen) { askedOpen = true; root._applyMinimize(false); }
        if (!asks) { askedOpen = false; }
        var badge = $('t-badge');
        setText(badge, asks ? '!' : (waiting ? String(waiting) : ''));
        if (asks) { waiting = 1; }
        badge.className = 'badge' + (waiting && root._minimized ? ' on' : '');
        var tb = $('tb-chat');
        setText(tb, unread && currentTab !== 'people' ? String(unread) : '');
        tb.className = 'tb' + (unread && currentTab !== 'people' ? ' on' : '');
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
        showNotice(s && s.connected && s.notice && s.notice_code !== 'version_differs' ? I.notice(s.notice_code, s.notice) : null);
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
                    text = t('Connected to {server}: waiting for your partner', { server: srv(s) });
                }
            }
            subText = (s.server === 'this PC' ? t('You are {name}, hosting on this PC', { name: s.name })
                : t('You are {name} on {server}', { name: s.name, server: s.server })) +
                (s.via_lan ? ' - ' + t('connected through the local network') : '') +
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
                text = t('Reconnecting to {server}...', { server: srv(s) });
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

    function renderExact(s) {                       // their exact house waits for the game to reopen
        var p = s && s.pending_lot, box = $('h-exact'), btn = $('h-exact-now');
        box.style.display = btn.style.display = p ? '' : 'none';      // (the game's browser ignores [hidden])
        if (p) { setText(box, t("{name}'s exact house (paint, floors, roof, everything) comes in when you reopen the game.", { name: p.from || partnerName() })); }
    }

    function render(s) {
        renderConn(s);
        renderNotice(s);
        if (!s) {
            renderTitle(null);
            $('pcard').style.display = 'none';
            renderCouple(null, null);
            return;
        }
        renderPlayers(s);
        renderRequests(s);
        renderCard(s);
        renderSent(s);
        renderHousehold(s);
        renderExact(s);
        renderClock(s);
        renderChat(s);
        renderTitle(s);
        renderMods(s.mods);
        renderSave(s.save);
        renderBox('update', s.update && s.update.box);
        renderBox('world', s.world && s.world.box);
        setText($('s-version'), t('inZOI Multiplayer {v}', { v: s.version }) +
            (s.save && s.save.name ? t(' - multiplayer save: {name}', { name: s.save.name }) : ''));
        // the update under way: on the button itself (a download takes a while; it mustn't look like nothing happens)
        var u = s.update || {};
        if (u.state === 'downloading' || u.state === 'restarting') {
            var pct = u.total ? Math.round(100 * (u.done || 0) / u.total) : 0;
            setText($('s-update'), u.state === 'restarting' ? t('Restarting...') : t('Downloading {v}... {pct}%', { v: u.version, pct: pct }));
            off($('s-update'), true);
        } else if ($('s-update').disabled && !updateAsked) {
            setText($('s-update'), t('Update now'));
            off($('s-update'), false);
        }
        $('s-sync').className = 'toggle' + (s.sync_time ? ' on' : '');
        $('s-sync').setAttribute('aria-checked', s.sync_time ? 'true' : 'false');
        $('s-sync').style.display = s.host ? 'none' : '';
        if (!lastStatus) {
            $('s-name').value = s.name;
            $('s-server').value = srv(s);
            $('s-server').readOnly = $('s-port').readOnly = s.server === 'this PC';   // (hosting: it's this PC)
            $('s-port').value = s.port;
            seenUpTo = s.chat.length ? s.chat[s.chat.length - 1].time : 0;
        }
        lastStatus = s;
        reHide();
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
    var updateAsked = false;
    $('h-sync').addEventListener('click', function () {
        post('/partner/house', {}, function (r) {
            flash(!r || r.error ? coupleErr(r) : t("Asked for {name}'s house: what's different shows up in a moment.", { name: partnerName() }), 5000);
        });
    });
    $('h-exact-now').addEventListener('click', function () {
        off($('h-exact-now'), true);
        flash(t('Saving and restarting the game...'), 8000);
        post('/partner/house/exact-now', {}, function (r) {
            off($('h-exact-now'), false);
            if (!r || r.error) { flash(coupleErr(r), 6000); }
        });
    });
    $('s-update').addEventListener('click', function () {
        var out = $('s-update-out');
        updateAsked = true;
        setText(out, t('Looking for a new version...'));
        off($('s-update'), true);
        post('/update/now', {}, function (r) {
            updateAsked = false;
            if (r && !r.error && !r.latest) { poll(); return; }        // (the button shows the download from now on)
            off($('s-update'), false);
            if (!r || r.error) { setText(out, r ? errText(r) : t(NOT_RUNNING)); return; }
            setText(out, r.latest ? t('You already have the newest version ({v}).', { v: r.version })
                : t('Updating to {v}: the program restarts in a moment (your game keeps running).', { v: r.version }));
        }, 20000);
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
        backupsKey = null;
        if (hhOpen) { loadBackups(); }
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
        setText($('tb-mods'), alert ? String(o.names.length) : '');
        $('tb-mods').className = 'tb' + (alert ? ' on' : '');
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
        currentTab = name;
        if (name === 'couple') { iaKey = ''; loadInteractions(true); }
        ['couple', 'people', 'houses', 'mods', 'settings', 'help'].forEach(function (p) {
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

    // ------------------------------------------------------------------ the couple: partner card, hugs and kisses,
    // invitations, requests from the partner. Everything updated in place, like the player rows.
    var N = function (s) { return s; };             // marks an English text as a translation key (i18n.js has it)
    var REQ_LIFE = 5;                               // seconds to answer: then the companion accepts it (couple.py)
    var ERRORS = {
        no_partner: N("Your partner isn't online right now."),
        no_pending: N("No house from your partner waiting to be applied."),
        restart: N("Couldn't save and restart the game."),
        not_in_game: N('Go into the game first (load your save).'),
        not_mp_save: N('Load your multiplayer save first.'),
        no_position: N("We don't know where your partner is yet. Try again in a few seconds."),
        other_city: N('Your partner is in another city ({city}). Travel there first.'),
        no_home: N("You don't have a home in this save yet."),
        not_connected: N('Not connected to the server.'),
        not_here: N("Your partner's Zoi isn't in your world right now."),
        not_offered: N("The game doesn't offer that right now. Try again in a moment."),
        bad_action: N("That doesn't work."),
        expired: N('That request has expired.'),
        not_admin: N('Only whoever hosts can undo the shared save.'),
        no_version: N('There is no earlier save to go back to.'),
        no_backup: N('There is no backup on this PC yet.'),
        busy: N('Busy with the save right now. Try again in a moment.'),
        server_refused: N('The server said no. Try again in a moment.')
    };
    function coupleErr(r) {
        if (!r) { return t(NOT_RUNNING); }
        if (r.code === 'other_city' && !r.city) { return t('Your partner is in another city. Travel there first.'); }
        if (r.code && ERRORS[r.code]) { return t(ERRORS[r.code], { city: cityName(r.city) }); }
        return errText(r) || t('Something went wrong.');
    }
    var ASKS = { hug: N('{name} wants to hug you'), kiss: N('{name} wants to kiss you'),
                 dance: N('{name} wants to dance with you'), wave: N('{name} wants a high five'),
                 holdhands: N('{name} wants to hold your hand'), selfie: N('{name} wants a selfie with you'),
                 spin: N('{name} wants to spin you around in a hug'),
                 invite: N('{name} invited you to their home'),
                 movein: N('{name} wants to move in with you: one family, one home'),
                 engage: N('{name} wants to get engaged to you'), marry: N('{name} wants to marry you'),
                 baby: N('{name} wants to have a baby with you'),
                 interaction: N('{name} wants to interact with you: {what}'),
                 game: N('{name} wants to interact with you: {what}'),
                 house: N('{name} changed the house ({what}). Apply it in your game?') };
    var SENT_NAME = { hug: N('Hug request'), kiss: N('Kiss request'), dance: N('Dance request'), wave: N('High five request'),
                      holdhands: N('Hold hands request'), selfie: N('Selfie request'), spin: N('Spin request'),
                      invite: N('Invitation'), movein: N('Moving in together'), engage: N('Engagement'),
                      marry: N('Wedding'), baby: N('Baby') };
    var SENT_STATE = { waiting: N('{what}: waiting for {name} to answer...'), accepted: N('{what}: {name} said yes!'),
                       declined: N('{what}: {name} said no.'), expired: N("{what}: {name} didn't answer."),
                       started: N('{what}: happening now!'), done: N('{what}: done!'),
                       failed: N("{what}: it didn't work. {msg}") };

    function off(b, isOff) {                        // enable/disable a button without rebuilding it
        var cls = b.className.replace(/ off$/, '') + (isOff ? ' off' : '');
        if (b.className !== cls) { b.className = cls; }
        if (isOff) { b.setAttribute('disabled', 'disabled'); } else { b.removeAttribute('disabled'); }
    }
    function srv(s) { return s.server === 'this PC' ? t('this PC') : s.server; }   // hosting here
    function cityName(c) {                          // the game's map id ("RedCity_Map") -> "Red City"
        return String(c || '').replace(/_Map$/i, '').replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2');
    }
    function two(n) { return (n < 10 ? '0' : '') + n; }
    function hm(tm) { return tm && tm.length >= 2 ? tm[0] + ':' + two(tm[1]) : ''; }
    function whenText(epoch) {                      // "hoje às 14:05" / "28/10 às 22:10"
        if (!epoch) { return ''; }
        var d = new Date(epoch * 1000), now = new Date();
        var clock = two(d.getHours()) + ':' + two(d.getMinutes());
        if (d.toDateString() === now.toDateString()) { return t('today at {time}', { time: clock }); }
        return t('{date} at {time}', { date: two(d.getDate()) + '/' + two(d.getMonth() + 1), time: clock });
    }
    function presenceText(p) {                      // why their Zoi isn't here yet, as their program says
        var q = p.presence;
        if (!q) { return ''; }
        var v = { name: p.name };
        if (q.busy) { return t('{name} is getting their multiplayer save ready...', v); }
        if (q.box === 'setup' || q.box === 'confirm') { return t('{name} has a question on screen about their multiplayer save: they need to answer it.', v); }
        if (q.box === 'paused' || q.mp === false) { return t('{name} is playing another save: multiplayer is paused for them until they switch.', v); }
        if (q.world === 'out') { return t('{name} is at the main menu or loading.', v); }
        if (q.world === 'stopped') { return t("{name}'s game is paused.", v); }
        if (q.world === 'paused') { return t('{name} is in a menu or build mode.', v); }
        return '';
    }
    function seenText(sec) {
        // still on the server (the card only lists connected friends) but nothing from their game yet
        if (sec === null || sec === undefined) { return t('Connected, waiting for their game to start'); }
        if (sec < 90) { return t('Last seen just now'); }
        if (sec < 5400) { return t('Last seen {n} min ago', { n: Math.round(sec / 60) }); }
        return t('Last seen {n} h ago', { n: Math.round(sec / 3600) });
    }

    // incoming requests: a big box at the top with Accept / Decline; it goes away when answered or after 90 s
    var reqRows = {}, answered = {};
    function renderRequests(s) {
        var box = $('reqs'), now = Date.now() / 1000, seen = {};
        (s.requests || []).forEach(function (q) {
            var left = Math.round((q.at || now) + REQ_LIFE - now);
            if (left <= 0 || answered[q.id]) { return; }
            seen[q.id] = true;
            var r = reqRows[q.id];
            if (!r) {
                r = { row: el('div', 'req'), text: el('div'), left: el('div', 'left'),
                      yes: btn('yes', t('Accept')), no: btn('no', t('Decline')), always: btn('always', t('Always accept')) };
                var row2 = el('div', 'row2');
                row2.appendChild(r.always);
                row2.appendChild(r.no);
                row2.appendChild(r.yes);
                r.row.appendChild(r.text);
                r.row.appendChild(row2);
                r.row.appendChild(r.left);
                (function (id, rr) {
                    rr.yes.addEventListener('click', function () { answerRequest(id, true, rr); });
                    rr.no.addEventListener('click', function () { answerRequest(id, false, rr); });
                    rr.always.addEventListener('click', function () { answerRequest(id, true, rr, true); });
                })(q.id, r);
                box.appendChild(r.row);
                reqRows[q.id] = r;

            }
            setText(r.text, t(ASKS[q.action] || N('{name} sent you a request'), { name: q.from, what: q.label ? I.tr(q.label) : '' }));
            setText(r.left, t('{n} s to answer, then it is accepted', { n: Math.max(0, left) }));
            setText(r.yes, t('Accept'));
            setText(r.no, t('Decline'));
            setText(r.always, t('Always accept'));
            r.always.setAttribute('title', t('Accept this kind of interaction from now on without asking'));
        });
        for (var id in reqRows) {
            if (reqRows.hasOwnProperty(id) && !seen[id]) { box.removeChild(reqRows[id].row); delete reqRows[id]; }
        }
        modal(box.firstChild !== null);
        reHide();
    }
    function reHide() {                              // (redraws reset some classes: keep the rest of the window hidden)
        if (modalWas) {
            modalHidden.forEach(function (n) { if (n.className.indexOf('mhide') < 0) { n.className += ' mhide'; } });
        }
    }

    // A request is a dialog in the middle of the screen (the window itself moves there: clicks only reach it inside
    // its own rectangle); answered, the window goes back exactly where it was, open or closed (nothing is saved).
    var modalWas = null, modalHidden = [];
    function modal(on) {
        if (on && !modalWas) {
            modalWas = { left: root.style.left, top: root.style.top, min: !!root._minimized, transform: root.style.transform };
            if (root._minimized && root._applyMinimize) { root._applyMinimize(false); }
            root.className = 'modal';
            // (the game's UI engine doesn't do :not(): everything but the request is hidden here, one by one)
            modalHidden = [$('title-bar')];
            var kids = $('content').children;
            for (var i = 0; i < kids.length; i++) { if (kids[i].id !== 'reqs') { modalHidden.push(kids[i]); } }
            modalHidden.forEach(function (n) { n.className = (n.className ? n.className + ' ' : '') + 'mhide'; });
            var w = 600, vw = window.innerWidth || 1920, vh = window.innerHeight || 1080;
            root.style.left = Math.max(0, Math.round((vw - w) / 2)) + 'px';
            root.style.top = Math.round(vh * 0.26) + 'px';
            $('dim').className = 'on';
            if (root._reportContentRect) { root._reportContentRect(); }
        } else if (!on && modalWas) {
            var was = modalWas;
            modalWas = null;
            $('dim').className = '';
            root.className = '';
            modalHidden.forEach(function (n) { n.className = n.className.replace(/ ?mhide/g, ''); });
            modalHidden = [];
            root.style.left = was.left;
            root.style.top = was.top;
            root.style.transform = was.transform;
            if (was.min && root._applyMinimize) { root._applyMinimize(true); }
            if (root._reportContentRect) { root._reportContentRect(); }
        }
    }
    function answerRequest(id, yes, rr, always) {
        if (rr.sending) { return; }
        rr.sending = true;
        off(rr.yes, true);
        off(rr.no, true);
        off(rr.always, true);
        post('/partner/respond', { id: id, accept: yes, always: !!always }, function (r) {
            answered[id] = true;
            if (reqRows[id]) { $('reqs').removeChild(reqRows[id].row); delete reqRows[id]; }
            if (!r || r.error) { flash(coupleErr(r)); return; }
            var res = r.result;
            if (res && res.state === 'failed') {
                flash(t("It didn't work. {msg}", { msg: I.tr(res.message || '') }));
            } else if (yes) {
                flash(t('You said yes!'), 3000);
            }
            poll();
        }, 15000);
    }

    // the partner card
    var cardOpen = true, partnerKey = null;
    try { cardOpen = localStorage.getItem('mp.card') !== '0'; } catch (e) { cardOpen = true; }
    function pickPartner(s) {
        var list = s.partners || [];
        for (var i = 0; i < list.length; i++) { if (list[i].online) { return list[i]; } }
        return list[0] || null;
    }
    function showCardBody() {
        $('p-body').style.display = cardOpen ? '' : 'none';
        setText($('p-fold'), cardOpen ? '-' : '+');
    }
    showCardBody();
    $('p-head').addEventListener('click', function () {
        cardOpen = !cardOpen;
        try { localStorage.setItem('mp.card', cardOpen ? '1' : '0'); } catch (e) {}
        showCardBody();
    });
    function renderCard(s) {
        var p = pickPartner(s);
        $('pcard').style.display = p ? 'block' : 'none';
        partnerKey = p ? p.id : null;
        if (!p) { renderCouple(s, null); return; }
        setText($('p-name'), p.zoi ? p.name + ' (' + p.zoi + ')' : p.name);
        var dotCls = 'pdot' + (p.online ? ' on' : '');
        if ($('p-dot').className !== dotCls) { $('p-dot').className = dotCls; }
        var place = p.lot && p.city ? p.lot + ', ' + cityName(p.city) : (p.lot || cityName(p.city) || '');
        var where = place ? t('Where: {place}', { place: place }) : t('Where: not known yet');
        if (p.here) { where += ' - ' + t('near you'); } else if (p.away) { where += ' - ' + t('in another city'); }
        setText($('p-where'), where);
        // the network line, like a game's net graph: their skeleton frames, the newest one's age, the distance and
        // the clocks' difference (shown only while they're online)
        var n = p.net || {}, parts = [];
        if (p.online && n.frames_s !== undefined) {
            parts.push(t('{n} frames/s', { n: Math.round(n.frames_s) }));
            if (n.frame_ms !== null && n.frame_ms !== undefined && n.frame_ms < 10000) { parts.push(t('last {ms} ms ago', { ms: n.frame_ms })); }
            if (n.dist_m !== undefined) { parts.push(t('{m} m away', { m: n.dist_m })); }
            if (n.clock_min !== undefined) { parts.push(t('clock {d} min', { d: (n.clock_min > 0 ? '+' : '') + n.clock_min })); }
        }
        setText($('p-net'), parts.length ? t('Sync: {what}', { what: parts.join(' · ') }) : '');
        $('p-net').style.display = parts.length ? '' : 'none';
        var doing = p.sleep ? t('Sleeping') : (prettyAction(p.action) || (p.action_text ? prettyAction(p.action_text) : ''));
        var withText = p['with'] === 'you' ? t('with you') : (p['with'] ? t('with {name}', { name: p['with'] }) : '');
        setText($('p-doing'), doing ? (p.sleep ? doing : t('Doing: {what}', { what: doing })) + (withText ? ' ' + withText : '')
            : (withText ? t('Together') + ' ' + withText : ''));
        var seen = p.online ? '' : (presenceText(p) || seenText(p.last_seen_s));
        // their clock only matters when the two clocks can differ (each one's own, or "adjust between the two")
        if (p.time && (s.clock_mode === 'catchup' || !s.sync_time)) {
            seen = t('Time in their game: {time}', { time: hm(p.time) }) + (seen ? ' - ' + seen : '');
        }
        // my own game holds everything up: nothing runs in it while paused (their Zoi can't come in or move)
        if (p.online && !p.here && (s.game === 'stopped' || s.game === 'paused')) {
            seen = s.game === 'stopped' ? t('Your game is paused: {name} shows up when time runs again.', { name: p.name })
                : t('Close the menu or build mode: {name} shows up when your game runs again.', { name: p.name });
        }
        seenText_ = seen;
        $('p-seen').style.display = 'none';
        $('p-where').style.display = p.here || !p.online ? 'none' : '';
        var cant = !s.connected || !p.online;
        off($('p-goto'), cant);
        off($('p-invite'), cant);
        renderCouple(s, p);
    }
    function partnerCall(path, body, okText) {
        if (partnerKey) { body.key = partnerKey; }
        post(path, body, function (r) {
            if (!r || r.error) { flash(coupleErr(r)); return; }
            if (okText) { flash(okText(r), 4000); }
            poll();
        }, 15000);
    }
    function partnerName() {
        var p = lastStatus ? pickPartner(lastStatus) : null;
        return p ? p.name : t('your partner');
    }
    $('p-goto').addEventListener('click', function () {
        partnerCall('/partner/goto', {}, function () { return t('Going to {name}...', { name: partnerName() }); });
    });
    $('p-invite').addEventListener('click', function () {
        partnerCall('/partner/invite', {}, null);
    });

    // ------------------------------------------------------------------ the Couple tab
    // Groups: "moments" (asked first, then done by both Zois), "family" (milestones both saves make true), then every
    // interaction the game itself offers between the two Zois, in its own words, sorted by the companion.
    var GROUP_NAMES = { moments: N('Together'), romance: N('Romance'), family: N('Family'), fun: N('Fun'),
                        friendship: N('Friendship'), talk: N('Talk'), business: N('Business'), conflict: N('Conflict') };
    var GAME_DESC = N("What the game offers your Zoi with {name}'s right now. It shows up in both games.");
    var GROUP_DESC = {
        moments: N('Asks {name} first; when they say yes, both of your Zois do it together.'),
        family: N('Milestones for the two of you. {name} is asked first; then it becomes true in both saves.'),
        conflict: N('Careful: these can hurt your relationship.')
    };
    var MOMENTS = [['hug', N('Hug')], ['kiss', N('Kiss')], ['dance', N('Dance')], ['wave', N('High five')],
                   ['holdhands', N('Hold hands')], ['selfie', N('Selfie')], ['spin', N('Spin')]];
    var FAMILY = [['movein', N('Move in together'), N('One family, one home')],
                  ['engage', N('Get engaged'), N('Engagement ring')],
                  ['marry', N('Get married'), N('A wedding for the two of you')],
                  ['baby', N('Have a baby'), N('A baby on the way')]];
    var BOND = [['romance', N('Romance')], ['friendship', N('Friendship')], ['family', N('Family')],
                ['business', N('Business')]];
    var seenText_ = '';
    var currentTab = 'couple', chip = 'moments', iaData = null, iaAt = 0, iaLoading = false, iaKey = '';
    try { chip = localStorage.getItem('mp.chip') || 'romance'; } catch (e) { chip = 'romance'; }
    if (chip === 'moments') { chip = 'romance'; }

    function partnerHere(s, p) { return !!(s && s.connected && p && p.online && p.here); }
    function currentPartner() { return lastStatus ? pickPartner(lastStatus) : null; }
    function loadInteractions(force) {
        var p = currentPartner();
        if (!partnerHere(lastStatus, p) || iaLoading) { return; }
        if (!force && Date.now() - iaAt < 6000) { return; }
        iaLoading = true;
        post('/partner/interactions', { key: p.id }, function (r) {
            iaLoading = false;
            iaAt = Date.now();
            if (r && r.ok) { iaData = r; } else if (r && r.code === 'not_here') { iaData = null; }
            iaKey = '';
            renderCouple(lastStatus, currentPartner());
        }, 8000);
    }
    function renderBond(r) {
        var box = $('bond');
        var sig = r && r.bond ? JSON.stringify(r.bond) + (r.family ? 'f' : '') + I.lang() : '';
        if (box.getAttribute('data-sig') === sig) { return; }
        box.setAttribute('data-sig', sig);
        clear(box);
        if (!sig) { box.style.display = 'none'; return; }
        box.style.display = '';
        if (r.family) { box.appendChild(el('div', 'tag', t('Family: you live together in this save'))); }
        BOND.forEach(function (b) {
            var v = r.bond[b[0]];
            if (!v) { return; }
            var cell = el('div', 'b ' + b[0]), line = el('div', 'bl');
            line.appendChild(el('span', '', t(b[1])));
            line.appendChild(el('span', 'v', String(v.value)));
            var track = el('div', 'track'), fill = el('div', 'fill');
            fill.style.width = Math.max(0, Math.min(100, v.value)) + '%';
            track.appendChild(fill);
            cell.appendChild(line);
            cell.appendChild(track);
            box.appendChild(cell);
        });
    }
    var QUICK_ICONS = {
        hug: '<path d="M8 8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM16 8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM3.5 20c0-4 2-7 4.5-7 1.7 0 2.8 1 4 2.5 1.2-1.5 2.3-2.5 4-2.5 2.5 0 4.5 3 4.5 7"/>',
        kiss: '<path d="M12 19s-6.5-4-6.5-9a3.6 3.6 0 0 1 6.5-2.2A3.6 3.6 0 0 1 18.5 10c0 5-6.5 9-6.5 9z"/><path d="M9.5 11.5c1.5 1 3.5 1 5 0"/>',
        dance: '<path d="M9 18V6l10-2v12"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="16" r="2"/>',
        wave: '<path d="M8 13V6.5a1.5 1.5 0 0 1 3 0V12M11 11V4.5a1.5 1.5 0 0 1 3 0V11M14 11V6a1.5 1.5 0 0 1 3 0v7c0 4-2.5 7-6 7-2.5 0-4-1.5-5.5-4L4 13a1.5 1.5 0 0 1 2.5-1.5L8 13"/>',
        holdhands: '<path d="M9.5 14.5l5-5M7 12l-2 2a3.5 3.5 0 0 0 5 5l2-2M17 12l2-2a3.5 3.5 0 0 0-5-5l-2 2"/>',
        selfie: '<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M9 7l1.5-2.5h3L15 7"/><circle cx="12" cy="13" r="3"/>',
        spin: '<path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19 4.5V8h-3.5"/>'
    };
    var QUICK_SHORT = { hug: N('A hug'), kiss: N('A kiss'), dance: N('A dance'), wave: N('High five'),
                        holdhands: N('Hands'), selfie: N('Selfie'), spin: N('Twirl') };
    var quickSig = '';
    function renderQuick() {
        var quick = (iaData && iaData.quick) || null;
        var box = $('quick'), sig = I.lang() + JSON.stringify(quick);
        if (quickSig === sig) { return; }
        quickSig = sig;
        clear(box);
        MOMENTS.forEach(function (m) {
            var b = el('button', 'qa'), c = el('span', 'c'), l = el('span', 'l', t(QUICK_SHORT[m[0]]));
            b.setAttribute('type', 'button');
            var q = quick ? quick[m[0]] : undefined;      // (undefined: the game's list isn't in yet)
            b.setAttribute('title', q ? q.name : (q === null ? t("The game doesn't offer this between you right now") : t(m[1])));
            if (q === null) { b.setAttribute('disabled', 'disabled'); }
            c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" ' +
                'stroke-linecap="round" stroke-linejoin="round">' + QUICK_ICONS[m[0]] + '</svg>';
            b.appendChild(c);
            b.appendChild(l);
            b.addEventListener('click', function () {
                if (b.getAttribute('disabled')) { return; }
                b.className = 'qa done';
                setTimeout(function () { b.className = 'qa'; }, 1500);
                partnerCall('/partner/action', { action: m[0] }, function () {
                    return t('Asked {name}. Waiting for an answer...', { name: partnerName() });
                });
            });
            box.appendChild(b);
        });
    }
    function groupsList() {
        var list = [{ key: 'family', items: FAMILY }];
        ((iaData && iaData.groups) || []).forEach(function (g) { list.push({ key: g.key, items: g.items, game: true }); });
        return list;
    }
    function renderChips(groups) {
        var box = $('chips');
        var sig = groups.map(function (g) { return g.key + ':' + g.items.length; }).join(',') + '|' + chip + '|' + I.lang();
        if (box.getAttribute('data-sig') === sig) { return; }
        box.setAttribute('data-sig', sig);
        clear(box);
        groups.forEach(function (g) {
            var c = el('button', 'chip ' + g.key + (g.key === chip ? ' on' : ''));
            c.setAttribute('type', 'button');
            c.setAttribute('role', 'tab');
            c.setAttribute('aria-selected', g.key === chip ? 'true' : 'false');
            c.appendChild(document.createTextNode(t(GROUP_NAMES[g.key] || g.key)));
            if (g.game) { c.appendChild(el('span', 'n', String(g.items.length))); }
            c.addEventListener('click', function () {
                chip = g.key;
                try { localStorage.setItem('mp.chip', chip); } catch (e) {}
                iaKey = '';
                renderCouple(lastStatus, currentPartner());
            });
            box.appendChild(c);
        });
    }
    function iaButton(group, title, sub, onClick) {
        var b = el('button', 'ia ' + group), inner = el('span', 'in');
        b.setAttribute('type', 'button');
        inner.appendChild(document.createTextNode(title));
        if (sub) { inner.appendChild(el('span', 's', sub)); }
        b.appendChild(inner);
        b.addEventListener('click', function () {
            b.className = 'ia ' + group + ' done';
            setTimeout(function () { b.className = 'ia ' + group; }, 1500);
            onClick();
        });
        return b;
    }
    function renderCouple(s, p) {
        var name = p ? p.name : t('your partner'), empty = $('c-empty'), here = partnerHere(s, p), msg = '';
        if (!s || !s.connected) { msg = t('Not connected to the server.'); }
        else if (!p) { msg = t('Nobody else is on the server yet. When your partner joins, everything you can do together shows up here.'); }
        else if (!p.online) { msg = t('{name} is offline. Their Zoi lives its own life in your save until they come back.', { name: name }); }
        else if (!p.here) { msg = seenText_ || t("{name}'s Zoi isn't in your world right now (another city, the menu, or a loading screen).", { name: name }); }
        empty.style.display = msg ? 'block' : 'none';
        setText(empty, msg);
        $('c-main').style.display = msg ? 'none' : 'flex';
        if (!here) { return; }
        if (currentTab === 'couple') { loadInteractions(false); }
        renderQuick();
        renderBond(iaData);
        var groups = groupsList();
        var want = chip;
        if (!groups.some(function (g) { return g.key === chip; })) { chip = 'family'; }
        renderChips(groups);
        var shownChip = chip;
        chip = want;
        var q = $('ia-search').value.replace(/^\s+|\s+$/g, '').toLowerCase();
        var key = shownChip + '|' + q + '|' + I.lang() + '|' + name + '|' + iaAt;
        if (key === iaKey) { return; }
        iaKey = key;
        setText($('c-desc'), q ? t('Results for "{q}" in every group', { q: q })
            : t(GROUP_DESC[shownChip] || GAME_DESC, { name: name }));
        var grid = $('ia-grid');
        clear(grid);
        var shown = 0;
        groups.forEach(function (g) {
            if (!q && g.key !== shownChip) { return; }
            g.items.forEach(function (it) {
                var title, sub = '', click;
                if (g.key === 'family') {
                    title = t(it[1]);
                    sub = t(it[2]);
                    if (it[0] === 'movein' && iaData && iaData.family) { sub = t('You live together. Ask again to put both saves in step'); }
                    click = function () {
                        partnerCall('/partner/family', { action: it[0] }, function () {
                            return t('Asked {name}. Waiting for an answer...', { name: name });
                        });
                    };
                } else {
                    title = it.name;           // (the game's hints are in English only: left out)
                    click = function () {
                        partnerCall('/partner/do', { bag: it.id }, function () { return t('Doing: {what}', { what: it.name }); });
                    };
                }
                if (q && title.toLowerCase().indexOf(q) < 0 && sub.toLowerCase().indexOf(q) < 0) { return; }
                grid.appendChild(iaButton(g.key, title, sub, click));
                shown++;
            });
        });
        if (!shown) {
            grid.appendChild(el('div', 'ia-empty', q ? t('Nothing found.') :
                t('The game offers nothing in this group right now. Get a little closer to {name} or try another group.', { name: name })));
        }
    }
    $('ia-search').addEventListener('input', function () { iaKey = ''; renderCouple(lastStatus, currentPartner()); });
    $('ia-search').addEventListener('keydown', function (e) { e.stopPropagation(); });

    // what happened to what I asked for (shown while waiting, then for a few seconds)
    var sentSeen = {};
    function renderSent(s) {
        var box = $('p-sent'), list = (s.couple && s.couple.sent) || [], best = null, now = Date.now(), text = '';
        list.forEach(function (x) { if (!best || (x.at || 0) > (best.at || 0)) { best = x; } });
        if (best && now / 1000 - (best.at || 0) < 600) {
            var k = best.id + '|' + best.state;
            if (!sentSeen[k]) { sentSeen[k] = now; }
            var showIt = best.state === 'waiting' ? now / 1000 - (best.at || 0) < REQ_LIFE : now - sentSeen[k] < 12000;
            if (showIt) {
                text = t(SENT_STATE[best.state] || N('{what}: {state}'), {
                    what: SENT_NAME[best.action] ? t(SENT_NAME[best.action]) : (best.name || best.action),
                    name: best.to, state: best.state,
                    msg: I.tr(best.message || '') }).replace(/\s+$/, '');
            }
        }
        box.style.display = text ? 'block' : 'none';
        setText(box, text);
    }

    // ------------------------------------------------------------------ settings: living together (shared household)
    var hhOpen = false, clockOpen = false, pending = {}, backupsKey = null, lastBackups = null, lastUndoAt = null;
    function pendingOr(key, value) {                 // a choice just clicked wins over a /status that predates it
        var p = pending[key];
        return p && Date.now() < p.until ? p.v : value;
    }
    function setPending(key, v) { pending[key] = { v: v, until: Date.now() + 3000 }; }
    function openSect(id, arrow, open) {
        $(id).className = 'sect' + (open ? ' open' : '');
        setText($(arrow), open ? '-' : '+');
    }
    $('hh-head').addEventListener('click', function () {
        hhOpen = !hhOpen;
        openSect('sect-home', 'hh-arrow', hhOpen);
        if (hhOpen) { backupsKey = null; loadBackups(); }
    });
    $('clock-head').addEventListener('click', function () {
        clockOpen = !clockOpen;
        openSect('sect-clock', 'clock-arrow', clockOpen);
    });
    function lineClass(node, cls) { if (node.className !== cls) { node.className = cls; } }
    function showLine(node, text, cls) {
        setText(node, text);
        node.style.display = text ? '' : 'none';
        if (cls) { lineClass(node, 'hh-line ' + cls); }
    }
    function renderHousehold(s) {
        var h = s.household || {};
        var on = pendingOr('hh', !!h.on);
        $('hh-toggle').className = 'toggle' + (on ? ' on' : '');
        $('hh-toggle').setAttribute('aria-checked', on ? 'true' : 'false');
        var saved = '';
        if (h.saved_at) {
            saved = h.saved_by_me ? t('Last save: you, {when}', { when: whenText(h.saved_at) })
                : t('Last save: {name}, {when}', { name: h.saved_by || t('your partner'), when: whenText(h.saved_at) });
        } else if (h.on) {
            saved = t('No shared save yet: it starts with the next save.');
        }
        showLine($('hh-saved'), saved);
        showLine($('hh-behind'), h.behind ? t('Your world is out of date: there is a newer save. Load it when the panel asks.') : '');
        var busy = '';
        if (h.job) {
            busy = I.tr(h.job.what) + (h.job.total ? ' (' + Math.floor(100 * (h.job.done || 0) / h.job.total) + '%)' : '...');
        } else if (h.busy) {
            busy = t('Working on the save...');
        }
        if (h.waiting_for_game_save) {
            busy += (busy ? ' ' : '') + t('Waiting for the game to save (save the game, or wait for the autosave).');
        }
        showLine($('hh-busy'), busy);
        var u = h.undo, undoText = '', undoCls = '';
        if (u && (u.state === 'working' || Date.now() / 1000 - (u.at || 0) < 180)) {
            var msg = I.tr(u.message || '');
            if (u.state === 'working') { undoText = t('Going back... {msg}', { msg: msg }); undoCls = 'warn'; }
            else if (u.state === 'done') { undoText = t('Done: {msg}', { msg: msg }); undoCls = 'ok'; }
            else { undoText = t("Couldn't go back: {msg}", { msg: msg }); undoCls = 'bad'; }
        }
        showLine($('hh-undo'), undoText.replace(/[:\s]+$/, ''), undoCls);
        var can = h.can_undo || {};
        off($('hh-undo-btn'), !(can.server || can.local) || !!h.busy || !!(u && u.state === 'working'));
        // the backup list: reload when the number of backups changes or an undo finished
        if (hhOpen && (h.backups !== lastBackups || (u && u.at !== lastUndoAt))) { backupsKey = null; loadBackups(); }
        lastBackups = h.backups;
        lastUndoAt = u ? u.at : null;
    }
    $('hh-toggle').addEventListener('click', function () {
        var on = $('hh-toggle').className.indexOf(' on') < 0;
        setPending('hh', on);
        $('hh-toggle').className = 'toggle' + (on ? ' on' : '');
        post('/settings', { shared_household: on }, function (r) {
            if (!r || r.error) { pending.hh = null; flash(r ? errText(r) : t(NOT_RUNNING)); }
        });
    });

    var confirmAction = null;
    function askConfirm(text, fn) {
        confirmAction = fn;
        setText($('hh-confirm-text'), text);
        $('hh-confirm').style.display = 'block';
    }
    function closeConfirm() { confirmAction = null; $('hh-confirm').style.display = 'none'; }
    $('hh-confirm-no').addEventListener('click', closeConfirm);
    $('hh-confirm-yes').addEventListener('click', function () {
        var f = confirmAction;
        closeConfirm();
        if (f) { f(); }
    });
    function doUndo(body) {
        post('/world/undo', body, function (r) {
            if (!r || r.error) { flash(coupleErr(r)); return; }
            flash(r.source === 'server' ? t('Done: the shared world went back to the save before. Loading it...')
                : t('Going back to the backup... the game reloads by itself.'), 8000);
            poll();
        }, 30000);
    }
    $('hh-undo-btn').addEventListener('click', function () {
        if ($('hh-undo-btn').getAttribute('disabled')) { return; }
        var h = (lastStatus && lastStatus.household) || {};
        askConfirm((h.can_undo && h.can_undo.server)
            ? t('The shared world goes back to how it was before the last save, for both of you. The game leaves without saving and loads it. What you did since then is lost. Go back?')
            : t('Your save goes back to the last backup on this PC. The game leaves without saving and loads it. What you did since then is lost. Go back?'),
            function () { doUndo({ source: 'auto' }); });
    });
    function reasonText(r) {
        var m = /^before-v(\d+)$/.exec(r || '');
        if (m) { return t('before loading save number {v}', { v: m[1] }); }
        if (r === 'before-load') { return t('before loading the save'); }
        if (r === 'before-undo') { return t('before going back'); }
        return r || '';
    }
    function loadBackups() {
        post('/world/backups', {}, function (r) {
            var box = $('hh-backups');
            if (!r || r.error) { backupsKey = null; clear(box); box.appendChild(el('div', 'empty', r ? coupleErr(r) : t(NOT_RUNNING))); return; }
            var list = r.backups || [];
            var key = I.lang() + JSON.stringify(list);
            if (key === backupsKey) { return; }                   // unchanged: keep the buttons under the cursor
            backupsKey = key;
            clear(box);
            if (!list.length) { box.appendChild(el('div', 'empty', t('No backups yet. One is made before a save is loaded.'))); }
            list.slice(0, 8).forEach(function (b) {
                var row = el('div', 'bk'), tt = el('div', 't', whenText(b.created));
                tt.appendChild(el('div', 's', reasonText(b.reason) + ' - ' + sizeText(b.size)));
                row.appendChild(tt);
                var bt = btn('', t('Go back to this one'));
                bt.addEventListener('click', function () {
                    askConfirm(t('Go back to the backup from {when}? The game leaves without saving and loads it. What you did since then is lost.',
                        { when: whenText(b.created) }), function () { doUndo({ source: 'local', backup: b.name }); });
                });
                row.appendChild(bt);
                box.appendChild(row);
            });
        }, 10000);
    }

    // ------------------------------------------------------------------ settings: the clock
    function renderClock(s) {
        var m = pendingOr('clock', s.clock_mode || 'host');
        lineClass($('clock-host'), 'choice' + (m === 'host' ? ' on' : ''));
        lineClass($('clock-catchup'), 'choice' + (m === 'catchup' ? ' on' : ''));
    }
    ['host', 'catchup'].forEach(function (m) {
        $('clock-' + m).addEventListener('click', function () {
            setPending('clock', m);
            if (lastStatus) { renderClock(lastStatus); }
            post('/settings', { clock_mode: m }, function (r) {
                if (!r || r.error) { pending.clock = null; flash(r ? errText(r) : t(NOT_RUNNING)); }
            });
        });
    });

    // ------------------------------------------------------------------ help: check the connection, diagnostics
    var CHECKS = { game_api: N('The game is open'), mod_loaded: N('The mod is running in the game'),
                   server_reachable: N('The server answers'), logged_in: N('Signed in to the server'),
                   versions_match: N('Same version as your partner'), partner_here: N("Your partner's Zoi is in your world"),
                   tailscale: N('Tailscale is on'),
                   nordvpn_running: N('NordVPN is not in the way') };
    var HINTS = {
        start_game: N('Open inZOI and wait until it reaches the menu or your game.'),
        load_save: N('Load your multiplayer save in the game.'),
        answer_save: N('Answer the question about your multiplayer save at the top of this panel (or load it).'),
        unpause: N('Unpause your game (the time controls at the bottom of the screen).'),
        same_city: N('You need to be in the same city: travel to where your partner is.'),
        partner_game: N('Your partner needs to load their multiplayer save and unpause (their panel says what is missing).'),
        wait_puppet: N('Wait a few seconds: their Zoi is being brought in.'),
        reload_mod: N("The mod isn't answering. Close inZOI and open it again."),
        pick_server: N('Choose a server: click "Servers" at the top of this panel.'),
        check_server: N("The server isn't answering. Ask whoever hosts to check that the inZOI Multiplayer program is open."),
        check_tailscale: N('Open Tailscale and check that it says Connected, on both computers.'),
        restart_program: N('Close the inZOI Multiplayer program and open it again.'),
        check_password: N('The password is wrong. Check it with your partner (Settings tab).'),
        update_partner: N('Your partner has an older version. Ask them to update inZOI Multiplayer.'),
        update_self: N('Your version is older. Update inZOI Multiplayer.'),
        banned: N('You were banned from this server. Talk to whoever hosts.'),
        server_full: N('The server is full.'),
        wait_connect: N('Still connecting. Wait a few seconds and check again.'),
        install_tailscale: N("Install Tailscale and sign in to the same account as your partner."),
        disable_nordvpn: N('Turn off NordVPN, or turn off its "Invisibility on LAN" option.')
    };
    function checkExtra(c) {
        var d = c.data || {};
        if (c.ok === null) { return t('Not needed here'); }
        if (c.id === 'server_reachable' && d.address) {
            return d.address + (d.ms !== null && d.ms !== undefined ? ' - ' + d.ms + ' ms' : '') +
                (d.via_lan ? ' - ' + t('local network') : '');
        }
        if (c.id === 'versions_match' && d.mine) {
            var theirs = d.theirs, parts = [];         // {name: version} ("server" for the server's)
            if (theirs && typeof theirs === 'object') {
                for (var k in theirs) { if (theirs.hasOwnProperty(k)) { parts.push((k === 'server' ? t('server') : k) + ' ' + theirs[k]); } }
                theirs = parts.join(', ');
            }
            return t('You: {mine} - partner: {theirs}', { mine: d.mine, theirs: theirs || '?' });
        }
        if (c.id === 'tailscale' && d.ip) { return d.ip; }
        return '';
    }
    var checking = false;
    $('help-check').addEventListener('click', function () {
        if (checking) { return; }
        checking = true;
        off($('help-check'), true);
        setText($('help-check'), t('Checking...'));
        post('/selftest', {}, function (r) {
            checking = false;
            off($('help-check'), false);
            setText($('help-check'), t('Check connection'));
            var box = $('help-checks');
            clear(box);
            if (!r || r.error) { box.appendChild(el('div', 'empty', r ? errText(r) : t(NOT_RUNNING))); return; }
            var bad = 0;
            (r.checks || []).forEach(function (c) {
                var row = el('div', 'check' + (c.ok === true ? ' ok' : (c.ok === false ? ' bad' : '')));
                row.appendChild(el('span', 'mark', c.ok === true ? '✓' : (c.ok === false ? '!' : '-')));
                var tt = el('div', 't', t(CHECKS[c.id] || c.id));
                var extra = checkExtra(c);
                if (extra) { tt.appendChild(el('div', 's', extra)); }
                if (c.ok === false) {
                    bad++;
                    tt.appendChild(el('div', 'fix', c.hint_code && HINTS[c.hint_code] ? t(HINTS[c.hint_code]) : I.tr(c.detail)));
                }
                row.appendChild(tt);
                box.appendChild(row);
            });
            box.appendChild(el('div', 'hint', bad ? t('{n} problem(s) found: follow the yellow tips, then check again.', { n: bad })
                : t('Everything looks fine.')));
        }, 30000);
    });
    var diagBusy = false;
    $('help-diag').addEventListener('click', function () {
        if (diagBusy) { return; }
        diagBusy = true;
        off($('help-diag'), true);
        setText($('help-diag-out'), t('Making the file... this takes a few seconds.'));
        post('/diag', {}, function (r) {
            diagBusy = false;
            off($('help-diag'), false);
            if (!r || r.error) { setText($('help-diag-out'), r ? errText(r) : t(NOT_RUNNING)); return; }
            var name = String(r.path || '').split(/[\\\/]/).pop();
            setText($('help-diag-out'), t('File saved on the Desktop: {name}. Send this file to Arthur.', { name: name }));
        }, 90000);
    });

    poll();
    setInterval(function () { if (!root._minimized) { poll(); } }, 1000);
    setInterval(function () { if (root._minimized) { poll(); } }, 3000);
})();
