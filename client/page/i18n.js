/* Translations for the servers page (loaded before servers.js).
   window.I18N.t(key, vars)      -> text in the current language. Keys are the English text (gettext style): a missing
                                    translation falls back to readable English. {name} is replaced from vars.
   window.I18N.tr(text)          -> free text from the program/servers, translated when it's a known one (or pattern).
   window.I18N.notice(code, txt) -> an error/notice: by its machine-readable code when there is one (wrong_password,
                                    version_mismatch, full, banned, bad_request, version_differs), else tr(txt).
   window.I18N.apply(root)       -> static markup: [data-i18n] text, [data-i18n-ph] placeholder, [data-i18n-title] title.
   Language: localStorage 'mp.lang' if set, else navigator.language (pt* -> pt-BR, en* -> en), default pt-BR. */
(function () {
  'use strict';

  const PT = {
    // top bar, tabs, filters
    'Back to the game (Esc)': 'Voltar ao jogo (Esc)',
    'Back': 'Voltar',
    'Multiplayer': 'Multijogador',
    'Couple mode': 'Modo casal',
    'Language': 'Idioma',
    'Public Servers': 'Servidores públicos',
    'Private Servers': 'Servidores privados',
    'Add Server': 'Adicionar servidor',
    'Host a Server': 'Hospedar um servidor',
    'Search': 'Buscar',
    'Status': 'Status',
    'All Status': 'Todos',
    'Online': 'Online',
    'Has Room': 'Com vaga',
    'Offline': 'Offline',
    'Version Compatibility': 'Compatibilidade de versão',
    'All': 'Todos',
    'Ready to Join': 'Pronto para entrar',
    "Can't Join": 'Não dá para entrar',
    'Mods': 'Mods',
    'All Mods': 'Todos os mods',
    'You Have Them All': 'Você tem todos',
    'Mods to Download': 'Mods para baixar',
    'No Mods': 'Sem mods',
    'Access': 'Acesso',
    'No Password': 'Sem senha',
    'Password': 'Senha',
    'Server Type': 'Tipo de servidor',
    'Hosted by a Player': 'Hospedado por um jogador',
    'Always On': 'Sempre ligado',
    'Reset': 'Limpar',
    'Saved in Private Servers': 'Salvo nos servidores privados',
    'Selected': 'Selecionado',
    'No server selected': 'Nenhum servidor selecionado',
    'Sort: {by}': 'Ordenar: {by}',
    'Sorted by {by}': 'Ordenado por {by}',
    'sort.players': 'jogadores',
    'sort.ping': 'ping',
    'sort.name': 'nome',
    // time
    'just now': 'agora mesmo',
    '{n} min': '{n} min',
    '{n} h': '{n} h',
    '{n} days': '{n} dias',
    '{time} ago': 'há {time}',
    // rows
    'Your server, running in this program': 'Seu servidor, rodando neste programa',
    'Hosting · Here': 'Hospedando · Aqui',
    'Hosting': 'Hospedando',
    'Connected': 'Conectado',
    '1 Mod to Get': '1 mod para baixar',
    '{n} Mods to Get': '{n} mods para baixar',
    'Full': 'Cheio',
    'Older Server': 'Servidor antigo',
    'Ready': 'Pronto',
    '{n} mods': '{n} mods',
    'No mods': 'Sem mods',
    'Remove {name} from Private Servers?': 'Remover {name} dos servidores privados?',
    'You can add it again with its address. (Your multiplayer save for it stays on your PC.)':
      'Você pode adicionar de novo pelo endereço. (Seu save multijogador dele continua no seu PC.)',
    'Remove': 'Remover',
    'Removed from Private Servers.': 'Removido dos servidores privados.',
    'Saved in Private Servers.': 'Salvo nos servidores privados.',
    "Couldn't get the list of public servers right now.": 'Não deu para pegar a lista de servidores públicos agora.',
    'Updated {when}': 'Atualizado {when}',
    'Looking for servers...': 'Procurando servidores...',
    'Connecting to the inZOI Multiplayer program...': 'Conectando ao programa inZOI Multiplayer...',
    'No server matches these filters.': 'Nenhum servidor combina com esses filtros.',
    'No public servers right now. Host one yourself in Private Servers and make it public.':
      'Nenhum servidor público agora. Hospede um em Servidores privados e deixe-o público.',
    "The list of public servers comes from a server that keeps one (like a friend's Raspberry Pi). Join one in Private Servers first, or host your own.":
      'A lista de servidores públicos vem de um servidor que mantém uma (como o Raspberry Pi de um amigo). Entre em um nos Servidores privados primeiro, ou hospede o seu.',
    'No saved servers yet. Add one with its address (the friend who hosts gives it to you), or host your own.':
      'Nenhum servidor salvo ainda. Adicione um pelo endereço (quem hospeda passa para você) ou hospede o seu.',
    'Your server': 'Seu servidor',
    'In Private Servers': 'Nos servidores privados',
    'Save in Private Servers': 'Salvar nos servidores privados',
    // details
    'You can view details by selecting a server': 'Veja os detalhes selecionando um servidor',
    'from the list on the left.': 'na lista à esquerda.',
    'Hosted by you, on this PC': 'Hospedado por você, neste PC',
    "Can't reach this server: {why}": 'Não deu para alcançar este servidor: {why}',
    'offline': 'offline',
    "You're playing on your own server.": 'Você está jogando no seu próprio servidor.',
    "You're on this server.": 'Você está neste servidor.',
    'About': 'Sobre',
    'Players': 'Jogadores',
    '{n} of {max}': '{n} de {max}',
    'Run by': 'Mantido por',
    'You': 'Você',
    '{host} (plays on it too)': '{host} (também joga nele)',
    'a player': 'um jogador',
    'a stand-alone server': 'um servidor independente',
    'Who can join': 'Quem pode entrar',
    'Everyone (public)': 'Todo mundo (público)',
    'People with the address (private)': 'Quem tem o endereço (privado)',
    'Yes': 'Sim',
    'No': 'Não',
    'Shared world': 'Mundo compartilhado',
    'version {v}': 'versão {v}',
    ', saved by {by}': ', salvo por {by}',
    'Up for': 'Ligado há',
    "the host's mods": 'os mods de quem hospeda',
    "everyone's mods, shared": 'os mods de todos, compartilhados',
    'Welcome message': 'Mensagem de boas-vindas',
    'Playing now': 'Jogando agora',
    'Mods ({n})': 'Mods ({n})',
    'This server uses no mods.': 'Este servidor não usa mods.',
    'you have it': 'você tem',
    '{size} to get': '{size} para baixar',
    'yours is {mine}: {size}': 'o seu é {mine}: {size}',
    'different': 'diferente',
    "When you join, the program offers to download the mods you don't have. Without them, some clothes and things won't show up for you.":
      'Ao entrar, o programa oferece baixar os mods que você não tem. Sem eles, algumas roupas e objetos não aparecem para você.',
    'Your other mods ({list}) will be shared with the players there.': 'Seus outros mods ({list}) serão compartilhados com os jogadores de lá.',
    // connection box
    'Your connection': 'Sua conexão',
    'Connected as {name}': 'Conectado como {name}',
    'Connecting...': 'Conectando...',
    'Your multiplayer save here: {save}': 'Seu save multijogador aqui: {save}',
    'Your multiplayer save here: {save}. It loads by itself when you go back to the main menu.':
      'Seu save multijogador aqui: {save}. Ele carrega sozinho quando você voltar ao menu principal.',
    'Load the save you want to play here: the program asks whether to make a multiplayer copy of it.':
      'Carregue o save em que quer jogar aqui: o programa pergunta se deve fazer uma cópia multijogador dele.',
    "This server uses {n} mod(s) you don't have ({size}).": 'Este servidor usa {n} mod(s) que você não tem ({size}).',
    'Waiting for them to be shared... {done} of {total}': 'Esperando serem compartilhados... {done} de {total}',
    'Downloading mods: {done} of {total}': 'Baixando mods: {done} de {total}',
    'Installing the mods...': 'Instalando os mods...',
    'Mods installed.': 'Mods instalados.',
    // hosting box
    'Friends join with': 'Seu par entra com',
    'This PC has no address friends can reach yet: install Tailscale and share this PC with them, or forward port {port} in your router.':
      'Este PC ainda não tem um endereço que seu par consiga alcançar: instale o Tailscale e compartilhe este PC com ele, ou libere a porta {port} no roteador.',
    'and the password you picked.': 'e a senha que você escolheu.',
    'Public listing': 'Listagem pública',
    'Public: telling the server lists about it...': 'Público: avisando as listas de servidores...',
    'Listed': 'Listado',
    "Listed, but the list can't reach it: {why}": 'Listado, mas a lista não consegue alcançá-lo: {why}',
    'not listed': 'não listado',
    // actions
    'Join': 'Entrar',
    'Check Again': 'Verificar de novo',
    'Play Here': 'Jogar aqui',
    'Server Settings': 'Ajustes do servidor',
    'Stop Hosting': 'Parar de hospedar',
    'Join and Get Mods': 'Entrar e baixar mods',
    'Admin Console': 'Console de admin',
    'Admin Code': 'Código de admin',
    'Edit': 'Editar',
    'Save to Private': 'Salvar nos privados',
    '{n} playing now': '{n} jogando agora',
    'Online on': 'Online em',
    'as': 'como',
    'Not connected': 'Não conectado',
    'Checking {name}...': 'Verificando {name}...',
    // joining
    'Playing on your own server.': 'Jogando no seu próprio servidor.',
    "Can't reach this server right now.": 'Não dá para alcançar este servidor agora.',
    'This server is full.': 'Este servidor está cheio.',
    'Password for {name}': 'Senha de {name}',
    'This server needs a password: the person running it can give it to you.': 'Este servidor pede senha: quem hospeda pode passar para você.',
    'Joining {name}...': 'Entrando em {name}...',
    "You're on {name}.": 'Você está em {name}.',
    "You're on {name}. Go back to the game to play.": 'Você está em {name}. Volte ao jogo para jogar.',
    'Get the mods this server uses?': 'Baixar os mods que este servidor usa?',
    "{n} mod(s) you don't have ({size}): {list}. Without them, some of the other players' clothes and things won't show up for you.":
      '{n} mod(s) que você não tem ({size}): {list}. Sem eles, algumas roupas e objetos dos outros não aparecem para você.',
    'Not Now': 'Agora não',
    'Download': 'Baixar',
    'Downloading mods...': 'Baixando mods...',
    // saving servers
    'e.g. 100.107.110.78': 'ex.: 100.107.110.78',
    'Optional': 'Opcional',
    '(saved: type to change)': '(salva: digite para trocar)',
    'If the server has one': 'Se o servidor tiver',
    'Edit Server': 'Editar servidor',
    'Add a Server': 'Adicionar um servidor',
    "The friend who runs the server gives you its address (for a server over Tailscale, the 100.x.x.x address of their PC or Raspberry Pi). Add :port at the end if it isn't 24242.":
      'Quem hospeda passa o endereço para você (num servidor pelo Tailscale, o endereço 100.x.x.x do PC ou Raspberry Pi dele). Coloque :porta no final se não for 24242.',
    'Address': 'Endereço',
    'Name in your list': 'Nome na sua lista',
    'Cancel': 'Cancelar',
    'Save': 'Salvar',
    'Save and Join': 'Salvar e entrar',
    'Remove {name}?': 'Remover {name}?',
    'It leaves your Private Servers list. You can add it again with its address. (Your multiplayer save for it stays on your PC.)':
      'Ele sai da sua lista de servidores privados. Você pode adicionar de novo pelo endereço. (Seu save multijogador dele continua no seu PC.)',
    // hosting
    "{name}'s server": 'Servidor de {name}',
    "What it is, who it's for (shown on the servers page)": 'O que é, para quem é (aparece na página de servidores)',
    'None': 'Nenhuma',
    'Public server': 'Servidor público',
    'Listed in Public Servers for everyone with inZOI Multiplayer. Off: only people you give the address to can join.':
      'Aparece em Servidores públicos para todos com o inZOI Multiplayer. Desligado: só entra quem tiver o endereço.',
    'Host for my partner': 'Hospedar para meu par',
    'Your game becomes the server while this program is open. Then give your partner the address shown on the next screen.':
      'Seu PC vira o servidor enquanto este programa estiver aberto. Depois passe para seu par o endereço que aparece na próxima tela.',
    "Your server runs in this program while it's open, and you play on it too. Everyone who joins gets your mods.":
      'Seu servidor roda neste programa enquanto ele estiver aberto, e você joga nele também. Quem entrar recebe seus mods.',
    'Server name': 'Nome do servidor',
    'Description': 'Descrição',
    'Password (optional)': 'Senha (opcional)',
    'Max players': 'Máx. de jogadores',
    'Port': 'Porta',
    'Start Hosting': 'Começar a hospedar',
    'Your server is running. Give your partner the address shown here.': 'Seu servidor está rodando. Passe para seu par o endereço que aparece aqui.',
    'Your server is running. Friends join with the addresses shown on the right.': 'Seu servidor está rodando. Os amigos entram com os endereços à direita.',
    'Stop hosting?': 'Parar de hospedar?',
    "Everyone on your server is disconnected. Your server's settings, players and world stay saved for the next time you host.":
      'Todos no seu servidor são desconectados. Os ajustes, jogadores e mundo do servidor ficam salvos para a próxima vez.',
    'Your server is stopped.': 'Seu servidor foi parado.',
    // admin console
    'Admin code': 'Código de admin',
    "You're not an admin of this server. If the server's owner gave you its admin code, type it here. (The owner finds it in their admin console, or with --admin-code on the server's PC.)":
      'Você não é admin deste servidor. Se o dono passou o código de admin, digite aqui. (O dono encontra no console de admin dele, ou com --admin-code no PC do servidor.)',
    'Close': 'Fechar',
    'Become Admin': 'Virar admin',
    "That code didn't work.": 'Esse código não funcionou.',
    "You're an admin of this server now.": 'Agora você é admin deste servidor.',
    "That didn't work.": 'Não funcionou.',
    'Overview': 'Visão geral',
    'Settings': 'Ajustes',
    'World': 'Mundo',
    'Log': 'Registro',
    'Your server, on this PC': 'Seu servidor, neste PC',
    'You are an admin of this server': 'Você é admin deste servidor',
    'up for {time}': 'ligado há {time}',
    'Players online': 'Jogadores online',
    'Listed as': 'Listado como',
    'Public': 'Público',
    'Private': 'Privado',
    'Telling the server lists about this server...': 'Avisando as listas de servidores sobre este servidor...',
    'No server list to be listed on: add one under Settings (a server that keeps the public list, e.g. the address of a Raspberry Pi running with --directory).':
      'Nenhuma lista onde aparecer: adicione uma em Ajustes (um servidor que mantém a lista pública, ex.: o endereço de um Raspberry Pi rodando com --directory).',
    'Listed by {dir} as {address}': 'Listado por {dir} como {address}',
    'Message everyone': 'Mensagem para todos',
    'Shown in everyone\'s chat as "Server"': 'Aparece no chat de todos como "Servidor"',
    'Send': 'Enviar',
    'Sent.': 'Enviada.',
    'Give this code to someone you trust: typed in their Admin Console, it makes them an admin of this server.':
      'Passe este código só para quem você confia: digitado no console de admin, torna a pessoa admin deste servidor.',
    'Restart': 'Reiniciar',
    '{n} seconds': '{n} segundos',
    'Warn players': 'Avisar os jogadores',
    'Restart Server': 'Reiniciar servidor',
    'Restart the server?': 'Reiniciar o servidor?',
    "Everyone gets a warning, their games are saved, and they reconnect by themselves once it's back.":
      'Todos recebem um aviso, os jogos são salvos e reconectam sozinhos quando ele voltar.',
    'Restarting...': 'Reiniciando...',
    'This server runs inside your inZOI Multiplayer program: to restart it, close and open the program.':
      'Este servidor roda dentro do seu programa inZOI Multiplayer: para reiniciar, feche e abra o programa.',
    "This server can't be restarted from here.": 'Este servidor não pode ser reiniciado daqui.',
    'Online now': 'Online agora',
    'Name': 'Nome',
    'Here since': 'Aqui desde',
    'Nobody is online.': 'Ninguém online.',
    'host': 'anfitrião',
    'admin': 'admin',
    'banned': 'banido',
    'Kick {name}': 'Expulsar {name}',
    'They can join again.': 'A pessoa pode entrar de novo.',
    'Reason (optional)': 'Motivo (opcional)',
    '{name} was kicked.': '{name} foi expulso(a).',
    'Kick': 'Expulsar',
    'Ban {name}': 'Banir {name}',
    "They can't join again until you unban them.": 'A pessoa não pode entrar de novo até você desbanir.',
    '{name} is banned.': '{name} foi banido(a).',
    'Ban': 'Banir',
    '{name} is no longer an admin.': '{name} não é mais admin.',
    'Remove admin': 'Tirar admin',
    '{name} is an admin now.': '{name} agora é admin.',
    'Make admin': 'Tornar admin',
    'Banned': 'Banidos',
    'Reason': 'Motivo',
    'By': 'Por',
    '{name} can join again.': '{name} pode entrar de novo.',
    'Unban': 'Desbanir',
    'Everyone who has joined': 'Todos que já entraram',
    'Last seen': 'Visto por último',
    'Unbanned.': 'Desbanido.',
    'Banned.': 'Banido.',
    'No longer an admin.': 'Não é mais admin.',
    'Admin now.': 'Agora é admin.',
    'Automatic': 'Automático',
    'Listed in Public Servers for everyone. Off: private, only people with the address can join.':
      'Aparece em Servidores públicos para todos. Desligado: privado, só entra quem tiver o endereço.',
    'Description (on the servers page)': 'Descrição (na página de servidores)',
    'Welcome message (when someone joins)': 'Mensagem de boas-vindas (quando alguém entra)',
    'Public listing (advanced)': 'Listagem pública (avançado)',
    'Address players use to reach this server': 'Endereço que os jogadores usam para alcançar este servidor',
    'Leave empty: the server list uses the address it sees this server at.': 'Deixe vazio: a lista usa o endereço em que vê este servidor.',
    'Server lists to be listed on (comma-separated addresses)': 'Listas de servidores onde aparecer (endereços separados por vírgula)',
    'Servers that keep the public list, e.g. a Raspberry Pi started with --directory.': 'Servidores que mantêm a lista pública, ex.: um Raspberry Pi iniciado com --directory.',
    'Save Settings': 'Salvar ajustes',
    'Settings saved.': 'Ajustes salvos.',
    'No shared world on this server yet.': 'Ainda não há mundo compartilhado neste servidor.',
    'unnamed': 'sem nome',
    'Version': 'Versão',
    'Saved by': 'Salvo por',
    'Size': 'Tamanho',
    'Earlier versions': 'Versões anteriores',
    'When': 'Quando',
    'current': 'atual',
    'Go back to version {v}?': 'Voltar para a versão {v}?',
    'It becomes the shared world again (as a new version), and everyone is offered to load it. Nothing is deleted: you can go forward again the same way.':
      'Ela vira o mundo compartilhado de novo (como uma nova versão), e todos recebem a oferta de carregá-la. Nada é apagado: dá para avançar de novo do mesmo jeito.',
    'Go Back': 'Voltar',
    'Done.': 'Feito.',
    'Go back to this': 'Voltar para esta',
    'Nothing yet.': 'Nada ainda.',
    'OK': 'OK',
    // couple mode
    'Play with my partner': 'Jogar com meu par',
    'your own server': 'seu próprio servidor',
    'Connected to {server} as {name}': 'Conectado a {server} como {name}',
    'Your partner is online.': 'Seu par está online.',
    '{n} other players online.': '{n} outros jogadores online.',
    'Waiting for your partner to join...': 'Esperando seu par entrar...',
    "Couldn't connect: {reason}": 'Não conectou: {reason}',
    'Try again': 'Tentar de novo',
    'Trying again...': 'Tentando de novo...',
    'Connecting to {server}...': 'Conectando a {server}...',
    'Not on a server yet: one of you hosts, the other joins.': 'Ainda sem servidor: um de vocês hospeda e o outro entra.',
    "You're hosting": 'Você está hospedando',
    'Give your partner one of these addresses:': 'Passe para seu par um destes endereços:',
    'Your PC becomes the server while this program is open. Your partner then clicks "Join my partner".':
      'Seu PC vira o servidor enquanto este programa estiver aberto. Depois seu par clica em "Entrar no meu par".',
    'Join my partner': 'Entrar no meu par',
    'Use the address your partner sees after clicking "Host for my partner".': 'Use o endereço que seu par vê depois de clicar em "Hospedar para meu par".',
    'Type the address your partner sees on their screen after clicking "Host for my partner" (and the password, if they picked one).':
      'Digite o endereço que aparece na tela do seu par depois que ele clica em "Hospedar para meu par" (e a senha, se ele escolheu uma).',
    'Join again': 'Entrar de novo',
    // program messages
    "The inZOI Multiplayer program isn't running. Open inZOI Multiplayer.exe and keep it open.":
      'O programa inZOI Multiplayer não está aberto. Abra o inZOI Multiplayer.exe e deixe-o aberto.',
    "type the server's address": 'digite o endereço do servidor',
    "you're not hosting a server": 'você não está hospedando um servidor',
    'not connected to a server': 'sem conexão com um servidor',
    "the server didn't answer": 'o servidor não respondeu',
    "you're not an admin of this server": 'você não é admin deste servidor',
    "that's not this server's admin code": 'esse não é o código de admin deste servidor',
    'wrong server password': 'senha do servidor errada',
    'server is full': 'o servidor está cheio',
    'not connected to the server': 'sem conexão com o servidor',
    // notices by code
    'code.wrong_password': 'senha errada. Confira a senha com seu par.',
    'code.version_mismatch': 'versões diferentes do inZOI Multiplayer. Os dois precisam da mesma versão.',
    'code.version_differs': 'vocês estão com versões diferentes do inZOI Multiplayer. Atualizem os dois para evitar problemas.',
    'code.full': 'o servidor está cheio.',
    'code.banned': 'você foi banido deste servidor.',
    'code.bad_request': 'o servidor recusou a conexão (pedido inválido).'
  };

  const EN = {
    'sort.players': 'players',
    'sort.ping': 'ping',
    'sort.name': 'name',
    'code.wrong_password': 'wrong password. Check the password with your partner.',
    'code.version_mismatch': 'different inZOI Multiplayer versions. You both need the same version.',
    'code.version_differs': 'you have different inZOI Multiplayer versions. Update both to avoid problems.',
    'code.full': 'the server is full.',
    'code.banned': "you're banned from this server.",
    'code.bad_request': 'the server refused the connection (bad request).'
  };

  // Free text with parts that vary: [regex, pt-BR template]; $1.. = groups, $T1.. = group translated again.
  const PATTERNS = [
    [/^Can't reach the server \((.*)\): retrying$/, 'Não deu para alcançar o servidor ($1): tentando de novo'],
    [/^Server: (.*)$/, 'Servidor: $T1'],
    [/^You were (.*)$/, 'Você foi $1'],
    [/^Not on a server yet: pick one on the servers page\.$/, 'Ainda sem servidor: escolha um nesta página.'],
    [/^you're banned from this server(.*)$/, 'você foi banido deste servidor$1'],
    [/^version mismatch(.*)$/, 'versões diferentes do inZOI Multiplayer$1'],
    [/^(.+?): (wrong server password|server is full|you're banned from this server.*|version mismatch.*)$/, '$1: $T2']
  ];

  function pickLang() {
    let stored = null;
    try { stored = localStorage.getItem('mp.lang'); } catch (e) { stored = null; }
    if (stored === 'pt-BR' || stored === 'en') return stored;
    const nav = String(navigator.language || '');
    if (/^pt/i.test(nav)) return 'pt-BR';
    if (/^en/i.test(nav)) return 'en';
    return 'pt-BR';
  }
  let lang = pickLang();
  document.documentElement.setAttribute('lang', lang);

  const fill = (text, vars) => !vars ? text : text.replace(/\{(\w+)\}/g, (m, k) =>
    Object.prototype.hasOwnProperty.call(vars, k) && vars[k] !== null && vars[k] !== undefined ? String(vars[k]) : m);

  function t(key, vars) {
    const d = lang === 'pt-BR' ? PT : EN;
    const s = Object.prototype.hasOwnProperty.call(d, key) ? d[key]
      : Object.prototype.hasOwnProperty.call(EN, key) ? EN[key] : key;
    return fill(s, vars);
  }

  function tr(text) {
    if (text === null || text === undefined || text === '') return text;
    text = String(text);
    if (lang !== 'pt-BR') return text;
    if (Object.prototype.hasOwnProperty.call(PT, text)) return PT[text];
    for (const [re, tpl] of PATTERNS) {
      const m = re.exec(text);
      if (m) return tpl.replace(/\$(T?)(\d)/g, (all, deep, n) => (deep ? tr(m[+n] || '') : (m[+n] || '')));
    }
    return text;
  }

  function notice(code, text) {
    const key = code ? 'code.' + code : null;
    if (key && (PT[key] || EN[key])) return t(key);
    return tr(text);
  }

  function apply(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach((e) => {
      const k = e.getAttribute('data-i18n') || e.textContent.trim().replace(/\s+/g, ' ');
      e.setAttribute('data-i18n', k);
      e.textContent = t(k);
    });
    scope.querySelectorAll('[data-i18n-ph]').forEach((e) => {
      const k = e.getAttribute('data-i18n-ph') || e.getAttribute('placeholder') || '';
      e.setAttribute('data-i18n-ph', k);
      e.setAttribute('placeholder', t(k));
    });
    scope.querySelectorAll('[data-i18n-title]').forEach((e) => {
      const k = e.getAttribute('data-i18n-title') || e.getAttribute('title') || '';
      e.setAttribute('data-i18n-title', k);
      e.setAttribute('title', t(k));
      if (!e.getAttribute('aria-label')) e.setAttribute('aria-label', t(k));
    });
  }

  function setLang(l) {
    if (l !== 'pt-BR' && l !== 'en') return;
    try { localStorage.setItem('mp.lang', l); } catch (e) { /* storage off */ }
    lang = l;
    document.documentElement.setAttribute('lang', lang);
  }

  window.I18N = { t, tr, notice, apply, setLang, lang: () => lang };
})();
