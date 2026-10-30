/**
 * i18n.js  --  translations for the in-game panel (loaded before app.js; ES5 only, Gameface).
 *
 * window.I18N.t(key, vars)      -> the text in the current language. Keys are the English text (gettext style), so a
 *                                  missing translation falls back to readable English. {name} is replaced from vars.
 * window.I18N.tr(text)          -> translate free text that comes from the companion/server (exact match or pattern).
 * window.I18N.notice(code, txt) -> a server/companion notice: by its machine-readable code when there is one,
 *                                  otherwise the English text (translated when it's a known one).
 * window.I18N.apply(root)       -> translate static markup: [data-i18n] text, [data-i18n-ph] placeholder,
 *                                  [data-i18n-title] title (the attribute holds the English key; empty = own text).
 * Language: localStorage 'mp.lang' if set, else pt-BR (Gameface's navigator.language isn't reliable).
 */
(function () {
    'use strict';

    var PT = {
        // title bar, tabs, banner
        'inZOI Multiplayer': 'inZOI Multiplayer',
        'Servers': 'Servidores',
        'Servers: join one, host your own, admin console': 'Servidores: entrar, hospedar, console de admin',
        'Minimize': 'Minimizar',
        'Close': 'Fechar',
        'People': 'Pessoas',
        'Houses': 'Casas',
        'Mods': 'Mods',
        'Mods ({n})': 'Mods ({n})',
        'Settings': 'Ajustes',
        '{n} new': '{n} nova(s)',
        'online': 'online',
        '1 friend online': '1 amigo online',
        '{n} friends online': '{n} amigos online',
        'Looking for the inZOI Multiplayer program...': 'Procurando o programa inZOI Multiplayer...',
        "The inZOI Multiplayer program isn't running. Open \"inZOI Multiplayer.exe\" and keep its window open while you play.":
            'O programa inZOI Multiplayer não está aberto. Abra o "inZOI Multiplayer.exe" e deixe a janela dele aberta enquanto joga.',
        'Connected with {names}': 'Conectado com {names}',
        'Connected to {server}: waiting for your partner': 'Conectado a {server}: esperando seu par entrar',
        '{name} dropped, waiting...': '{name} caiu, aguardando...',
        'Reconnecting to {server}...': 'Reconectando a {server}...',
        "Couldn't connect: {reason}": 'Não conectou: {reason}',
        'Try again': 'Tentar de novo',
        'Trying again...': 'Tentando de novo...',
        'You are {name} on {server}': 'Você é {name} em {server}',
        'getting the game ready': 'preparando o jogo',
        "The program isn't open": 'O programa não está aberto',
        // people
        'Players': 'Jogadores',
        'Chat': 'Chat',
        'Nobody else is online right now.': 'Ninguém mais está online agora.',
        'Not connected yet.': 'Ainda não conectado.',
        'Here with you': 'Aqui com você',
        'In another city': 'Em outra cidade',
        'On the way...': 'A caminho...',
        'Join': 'Ir até',
        'Go to {name}': 'Ir até {name}',
        'Say something...': 'Escreva algo...',
        'Send': 'Enviar',
        // houses
        'To have friends visit you at home: in Build mode, save your home to your library, then share it here. Friends add it to their library and place it on the same lot with the build tools.':
            'Para receber visitas em casa: no modo Construção, salve sua casa na biblioteca e compartilhe aqui. Seu par adiciona à biblioteca dele e coloca no mesmo lote com as ferramentas de construção.',
        'Your saved houses': 'Suas casas salvas',
        'Shared by friends': 'Compartilhadas pelos amigos',
        'No saved houses in your library yet.': 'Nenhuma casa salva na sua biblioteca ainda.',
        'Nobody has shared a house yet.': 'Ninguém compartilhou uma casa ainda.',
        'Shared': 'Compartilhada',
        'Share': 'Compartilhar',
        'Added': 'Adicionada',
        'Add to library': 'Adicionar à biblioteca',
        '{size}, {beds} bed': '{size}, {beds} cama(s)',
        'by {by}': 'de {by}',
        'by {by} - goes on {lot}': 'de {by} - vai no lote {lot}',
        'Shared "{name}" with everyone.': '"{name}" compartilhada com todos.',
        'Downloading {name}...': 'Baixando {name}...',
        'Added "{title}" to your library. Place it in Build mode.': '"{title}" adicionada à sua biblioteca. Coloque no modo Construção.',
        // mods
        "Everyone needs the same mods to see each other's clothes and things. When someone hosts and plays, the host's mods count; on a stand-alone server, everyone's mods are shared.":
            'Todos precisam dos mesmos mods para ver as roupas e objetos uns dos outros. Quando alguém hospeda e joga, valem os mods de quem hospeda; num servidor independente, os mods de todos são compartilhados.',
        'Download': 'Baixar',
        'No thanks': 'Não, obrigado',
        'Mods everyone uses': 'Mods que todos usam',
        'None yet.': 'Nenhum ainda.',
        'someone': 'alguém',
        'Waiting for the server...': 'Esperando o servidor...',
        'You have all the mods the host uses.': 'Você tem todos os mods de quem hospeda.',
        'You have all the mods your friends use.': 'Você tem todos os mods que seu par usa.',
        'The host uses no mods.': 'Quem hospeda não usa mods.',
        'Nobody has shared any mods yet.': 'Ninguém compartilhou mods ainda.',
        'Mods installed. {msg}': 'Mods instalados. {msg}',
        "The host ({who}) uses {n} mod(s) you don't have ({size}). Without them, some of their clothes or things won't show up for you. Download them?":
            'Quem hospeda ({who}) usa {n} mod(s) que você não tem ({size}). Sem eles, algumas roupas ou objetos não vão aparecer para você. Baixar?',
        "Your friends ({who}) use {n} mod(s) you don't have ({size}). Without them, some of their clothes or things won't show up for you. Download them?":
            'Seu par ({who}) usa {n} mod(s) que você não tem ({size}). Sem eles, algumas roupas ou objetos não vão aparecer para você. Baixar?',
        'You chose not to download {n} mod(s) ({size}).': 'Você escolheu não baixar {n} mod(s) ({size}).',
        'The download stopped: {msg}': 'O download parou: {msg}',
        'Waiting for {who} to finish sharing them...': 'Esperando {who} terminar de compartilhar...',
        'Downloading {n} mod(s): {done} of {total}': 'Baixando {n} mod(s): {done} de {total}',
        'Installing...': 'Instalando...',
        'Sharing your mods with friends: {done} of {total}': 'Compartilhando seus mods: {done} de {total}',
        // settings
        'Your name': 'Seu nome',
        'Server': 'Servidor',
        'Port': 'Porta',
        'Password': 'Senha',
        'No password': 'Sem senha',
        'Unchanged unless you type': 'Mantida se você não digitar',
        'Will be removed when you save': 'Será removida ao salvar',
        'Language': 'Idioma',
        "Follow the host's time of day, season and weather": 'Seguir a hora do dia, estação e clima de quem hospeda',
        'Save and reconnect': 'Salvar e reconectar',
        'Saved - reconnecting...': 'Salvo - reconectando...',
        'inZOI Multiplayer {v}': 'inZOI Multiplayer {v}',
        ' - multiplayer save: {name}': ' - save multijogador: {name}',
        // buttons that come from the companion (boxes)
        'Make a multiplayer copy': 'Fazer uma cópia multijogador',
        'Use this save': 'Usar este save',
        'Not now': 'Agora não',
        'Yes': 'Sim',
        'No': 'Não',
        'Switch to my multiplayer save': 'Trocar para meu save multijogador',
        'Load it': 'Carregar',
        'Later': 'Depois',
        "Make it everyone's world": 'Tornar o mundo de todos',
        'Update now': 'Atualizar agora',
        // errors the companion answers with
        'not connected to the server': 'sem conexão com o servidor',
        "they're not in your world right now": 'essa pessoa não está no seu mundo agora',
        "that house isn't shared any more": 'essa casa não está mais compartilhada',
        // notices by code
        'code.wrong_password': 'senha errada. Confira a senha com seu par em Ajustes.',
        'code.version_mismatch': 'versões diferentes do inZOI Multiplayer. Os dois precisam atualizar para a mesma versão.',
        'code.version_differs': 'vocês estão com versões diferentes do inZOI Multiplayer. Atualizem os dois para evitar problemas.',
        'code.full': 'o servidor está cheio.',
        'code.banned': 'você foi banido deste servidor.',
        'code.bad_request': 'o servidor recusou a conexão (pedido inválido).',
        'code.unreachable': 'não deu para alcançar o servidor. Ele está aberto? O Tailscale está ligado?'
    };

    var EN = {
        'code.wrong_password': 'wrong password. Check the password with your partner in Settings.',
        'code.version_mismatch': "different inZOI Multiplayer versions. You both need the same version.",
        'code.version_differs': 'you have different inZOI Multiplayer versions. Update both to avoid problems.',
        'code.full': 'the server is full.',
        'code.banned': "you're banned from this server.",
        'code.bad_request': 'the server refused the connection (bad request).',
        'code.unreachable': "can't reach the server. Is it open? Is Tailscale on?",
        '{n} new': '{n} new',
        'Join': 'Join'
    };

    // Free text from the companion/server, with parts that vary. [regex, pt-BR template ($1.. = groups)].
    var PATTERNS = [
        [/^Can't reach the server \((.*)\): retrying$/, 'Não deu para alcançar o servidor ($1): tentando de novo'],
        [/^Server: (.*)$/, 'Servidor: $T1'],
        [/^You were (.*)$/, 'Você foi $1'],
        [/^wrong server password$/, 'senha do servidor errada'],
        [/^server is full$/, 'o servidor está cheio'],
        [/^you're banned from this server(.*)$/, 'você foi banido deste servidor$1'],
        [/^version mismatch(.*)$/, 'versões diferentes do inZOI Multiplayer$1'],
        [/^(.*): wrong server password$/, '$1: senha do servidor errada'],
        [/^Not on a server yet: pick one on the servers page\.$/, 'Ainda sem servidor: escolha um na página de Servidores.'],
        [/^Multiplayer plays in a save of its own, so your normal saves stay untouched\. Make a multiplayer copy of this save\? The game saves, goes to the main menu for a moment and loads the copy\.$/,
            'O multijogador usa um save próprio, então seus saves normais ficam intactos. Fazer uma cópia multijogador deste save? O jogo salva, vai ao menu principal por um instante e carrega a cópia.'],
        [/^Is this your multiplayer save \((.*)\)\? Multiplayer waits until you say\.$/,
            'Este é o seu save multijogador ($1)? O multijogador espera você responder.'],
        [/^This isn't (.*): multiplayer is paused here, so this save stays untouched\.$/,
            'Este não é $1: o multijogador está pausado aqui, então este save fica intacto.'],
        [/^Saving your game and making your multiplayer copy\.\.\.$/, 'Salvando o jogo e fazendo sua cópia multijogador...'],
        [/^Saving this game and loading (.*)\.\.\.$/, 'Salvando este jogo e carregando $1...'],
        [/^Making the multiplayer copy failed: (.*)$/, 'Não deu para fazer a cópia multijogador: $1'],
        [/^Switching saves failed: (.*)$/, 'Não deu para trocar de save: $1'],
        [/^(.*) saved a newer version of the shared world\. Load it now\? \(What you did since your last save stays only in your copy\.\)$/,
            '$1 salvou uma versão mais nova do mundo compartilhado. Carregar agora? (O que você fez desde o seu último save fica só na sua cópia.)'],
        [/^This server has no shared world yet\. Make your multiplayer save \((.*)\) everyone's world\? Friends then play in it too, with you\.$/,
            'Este servidor ainda não tem um mundo compartilhado. Tornar seu save multijogador ($1) o mundo de todos? Aí seu par joga nele também, com você.'],
        [/^The update didn't work: (.*)$/, 'A atualização não funcionou: $1'],
        [/^Downloading inZOI Multiplayer (.*)\.\.\. (\d+)%$/, 'Baixando o inZOI Multiplayer $1... $2%'],
        [/^Restarting into inZOI Multiplayer (.*)\.\.\.$/, 'Reiniciando no inZOI Multiplayer $1...']
    ];

    function pickLang() {
        var stored = null;
        try { stored = window.localStorage && localStorage.getItem('mp.lang'); } catch (e) { stored = null; }
        if (stored === 'pt-BR' || stored === 'en') { return stored; }
        var nav = String((navigator && (navigator.language || navigator.userLanguage)) || '');
        if (/^pt/i.test(nav)) { return 'pt-BR'; }
        // coop-casal: Gameface tends to report en-US whatever Windows says, so English only when picked in Ajustes
        return 'pt-BR';
    }

    var lang = pickLang();
    try { document.documentElement.setAttribute('lang', lang); } catch (e) {}

    function fill(text, vars) {
        if (!vars) { return text; }
        return text.replace(/\{(\w+)\}/g, function (m, k) {
            return vars.hasOwnProperty(k) && vars[k] !== null && vars[k] !== undefined ? String(vars[k]) : m;
        });
    }

    function t(key, vars) {
        var d = lang === 'pt-BR' ? PT : EN;
        var s = d.hasOwnProperty(key) ? d[key] : (EN.hasOwnProperty(key) ? EN[key] : key);
        return fill(s, vars);
    }

    function tr(text) {
        if (text === null || text === undefined || text === '') { return text; }
        text = String(text);
        if (lang !== 'pt-BR') { return text; }
        if (PT.hasOwnProperty(text)) { return PT[text]; }
        for (var i = 0; i < PATTERNS.length; i++) {
            var m = PATTERNS[i][0].exec(text);
            if (m) {
                return PATTERNS[i][1].replace(/\$(T?)(\d)/g, function (all, deep, n) {
                    var g = m[+n] || '';
                    return deep ? tr(g) : g;
                });
            }
        }
        return text;
    }

    // Guess a code from English text when the companion didn't send one (older companion).
    function guessCode(text) {
        var s = String(text || '').toLowerCase();
        if (!s) { return null; }
        if (s.indexOf('password') >= 0) { return 'wrong_password'; }
        if (s.indexOf('version mismatch') >= 0) { return 'version_mismatch'; }
        if (s.indexOf('versions differ') >= 0 || s.indexOf('version differs') >= 0) { return 'version_differs'; }
        if (s.indexOf('banned') >= 0) { return 'banned'; }
        if (s.indexOf('server is full') >= 0) { return 'full'; }
        return null;
    }

    function notice(code, text) {
        var key = code ? 'code.' + code : null;
        if (key && (PT.hasOwnProperty(key) || EN.hasOwnProperty(key))) { return t(key); }
        return tr(text);
    }

    function apply(root) {
        var scope = root || document;
        var i, e, k, list;
        list = scope.querySelectorAll('[data-i18n]');
        for (i = 0; i < list.length; i++) {
            e = list[i];
            k = e.getAttribute('data-i18n') || e.textContent.replace(/^\s+|\s+$/g, '').replace(/\s+/g, ' ');
            e.setAttribute('data-i18n', k);
            e.textContent = t(k);
        }
        list = scope.querySelectorAll('[data-i18n-ph]');
        for (i = 0; i < list.length; i++) {
            e = list[i];
            k = e.getAttribute('data-i18n-ph') || e.getAttribute('placeholder') || '';
            e.setAttribute('data-i18n-ph', k);
            e.setAttribute('placeholder', t(k));
        }
        list = scope.querySelectorAll('[data-i18n-title]');
        for (i = 0; i < list.length; i++) {
            e = list[i];
            k = e.getAttribute('data-i18n-title') || e.getAttribute('title') || '';
            e.setAttribute('data-i18n-title', k);
            e.setAttribute('title', t(k));
            if (!e.getAttribute('aria-label')) { e.setAttribute('aria-label', t(k)); }
        }
    }

    function setLang(l) {
        if (l !== 'pt-BR' && l !== 'en') { return; }
        try { localStorage.setItem('mp.lang', l); } catch (e) {}
        lang = l;
        try { document.documentElement.setAttribute('lang', lang); } catch (e) {}
        apply(document);
    }

    window.I18N = {
        t: t, tr: tr, notice: notice, guessCode: guessCode, apply: apply, setLang: setLang,
        lang: function () { return lang; }
    };
})();
