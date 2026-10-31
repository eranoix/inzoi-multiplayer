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
        'Multiplayer - {n} online': 'Multiplayer - {n} online',
        'Multiplayer - online': 'Multiplayer - online',
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
        'You are {name}, hosting on this PC': 'Você é {name}, hospedando neste PC',
        'this PC': 'este PC',
        'Your multiplayer save': 'Seu save multijogador',
        "Your partner's Zoi is in your world": 'O Zoi do seu par está no seu mundo',
        'Answer the question about your multiplayer save at the top of this panel (or load it).': 'Responda à pergunta sobre o seu save multijogador no topo deste painel (ou carregue esse save).',
        'Unpause your game (the time controls at the bottom of the screen).': 'Tire o jogo da pausa (controles de tempo no canto da tela).',
        'You need to be in the same city: travel to where your partner is.': 'Vocês precisam estar na mesma cidade: viaje até onde seu par está.',
        'Your partner needs to load their multiplayer save and unpause (their panel says what is missing).': 'Seu par precisa carregar o save multijogador e tirar o jogo da pausa (o painel dele diz o que falta).',
        'Wait a few seconds: their Zoi is being brought in.': 'Espere alguns segundos: o Zoi do seu par está chegando.',
        '{name} is getting their multiplayer save ready...': '{name} está preparando o save multijogador...',
        '{name} has a question on screen about their multiplayer save: they need to answer it.': '{name} tem uma pergunta na tela sobre o save multijogador: precisa responder.',
        '{name} is playing another save: multiplayer is paused for them until they switch.': '{name} está jogando outro save: o multijogador fica pausado até trocar para o save multijogador.',
        '{name} is at the main menu or loading.': '{name} está no menu principal ou carregando.',
        "{name}'s game is paused.": 'O jogo de {name} está pausado.',
        '{name} is in a menu or build mode.': '{name} está num menu ou no modo construção.',
        'Your game is paused: {name} shows up when time runs again.': 'Seu jogo está pausado: {name} aparece quando o tempo voltar a correr.',
        'Close the menu or build mode: {name} shows up when your game runs again.': 'Feche o menu ou o modo construção: {name} aparece quando o jogo voltar a rodar.',
        'Each of you plays your own save (a multiplayer copy, so your normal saves stay untouched). You meet on the same map.':
            'Cada um joga o próprio save (numa cópia multijogador, então seus saves normais ficam intactos). Vocês se encontram no mesmo mapa.',
        'server': 'servidor',
        'getting the game ready': 'preparando o jogo',
        "The program isn't open": 'O programa não está aberto',
        // people
        'Players': 'Jogadores',
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
        'Unchanged unless you type': 'Vazio = manter a atual',
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
        // the couple: partner card, actions, requests
        'connected through the local network': 'conectado pela rede local',
        'Show or hide the details': 'Mostrar ou esconder os detalhes',
        'Go to my partner': 'Ir até meu par',
        'Invite to my home': 'Convidar para casa',
        // the Couple tab
        'inZOI Co-op': 'inZOI Co-op',
        'Smaller': 'Diminuir', 'Bigger': 'Aumentar',
        'Always accept': 'Sempre aceitar',
        "The game doesn't offer this between you right now": 'O jogo não oferece isso entre vocês agora',
        "The game doesn't offer that between you right now ({what})": 'O jogo não oferece isso entre vocês agora ({what})',
        'Accept this kind of interaction from now on without asking': 'Aceitar este tipo de interação daqui em diante, sem perguntar',
        '{name} wants to interact with you: {what}': '{name} quer interagir com você: {what}',
        'Looking for a new version...': 'Procurando versão nova...',
        'Downloading {v}... {pct}%': 'Baixando a {v}... {pct}%',
        'Restarting...': 'Reiniciando...',
        'You already have the newest version ({v}).': 'Você já está na versão mais nova ({v}).',
        'Updating to {v}: the program restarts in a moment (your game keeps running).': 'Atualizando para a {v}: o programa reinicia em instantes (o jogo continua aberto).',
        'A hug': 'Abraço', 'A kiss': 'Beijo', 'A dance': 'Dança', 'Hands': 'Mãos', 'Twirl': 'Giro',
        'Not connected': 'Desconectado',
        'Waiting for your partner': 'Esperando seu par',
        'Offline': 'Offline',
        'Online': 'Online',
        'Near you': 'Perto de você',
        'In another city': 'Em outra cidade',
        'You live together. Ask again to put both saves in step': 'Vocês moram juntos. Peça de novo para acertar os dois saves',
        'sleeping': 'dormindo', 'taking a shower': 'tomando banho', 'in the bathroom': 'no banheiro',
        'cooking': 'cozinhando', 'eating': 'comendo', 'having a drink': 'bebendo algo', 'reading': 'lendo',
        'on the phone': 'no celular', 'watching TV': 'vendo TV', 'on the computer': 'no computador',
        'kissing': 'beijando', 'hugging': 'abraçando', 'dancing': 'dançando', 'taking photos': 'tirando fotos',
        'playing music': 'tocando música', 'playing': 'jogando', 'exercising': 'se exercitando', 'cleaning': 'limpando',
        'getting ready': 'se arrumando', 'working': 'trabalhando', 'gardening': 'cuidando das plantas',
        'talking': 'conversando', 'sitting': 'sentado(a)', 'standing': 'em pé', 'walking': 'andando',
        'busy with something': 'ocupado(a) com alguma coisa',
        'Couple': 'Casal',
        'Chat': 'Conversa',
        'Chat ({n})': 'Conversa ({n})',
        'Romance': 'Romance',
        'Family': 'Família',
        'Fun': 'Diversão',
        'Friendship': 'Amizade',
        'Talk': 'Conversar',
        'Business': 'Negócios',
        'Conflict': 'Conflito',
        'Search actions...': 'Procurar uma ação...',
        "What the game offers your Zoi with {name}'s right now. It shows up in both games.":
            'O que o jogo oferece agora entre o seu Zoi e o de {name}. Acontece nos dois jogos.',
        'Asks {name} first; when they say yes, both of your Zois do it together.':
            '{name} recebe o pedido; quando aceitar, os dois Zois fazem juntos.',
        'Milestones for the two of you. {name} is asked first; then it becomes true in both saves.':
            'Grandes momentos do casal. {name} recebe o pedido; ao aceitar, vale nos dois saves.',
        'Careful: these can hurt your relationship.': 'Cuidado: estas podem prejudicar o relacionamento.',
        'Move in together': 'Morar juntos',
        'Done: you live together': 'Feito: vocês moram juntos',
        'One family, one home': 'Uma família, uma casa',
        'Get engaged': 'Noivar',
        'Engagement ring': 'Pedido de noivado',
        'Get married': 'Casar',
        'A wedding for the two of you': 'O casamento de vocês',
        'Have a baby': 'Ter um bebê',
        'A baby on the way': 'Um bebê a caminho',
        'Family: you live together in this save': 'Família: vocês moram juntos neste save',
        'Nobody else is on the server yet. When your partner joins, everything you can do together shows up here.':
            'Ainda não tem mais ninguém no servidor. Quando seu par entrar, tudo o que vocês podem fazer juntos aparece aqui.',
        '{name} is offline. Their Zoi lives its own life in your save until they come back.':
            '{name} está offline. O Zoi dele vive normalmente no seu save até a volta.',
        "{name}'s Zoi isn't in your world right now (another city, the menu, or a loading screen).":
            'O Zoi de {name} não está no seu mundo agora (outra cidade, o menu ou uma tela de carregamento).',
        'Results for "{q}" in every group': 'Resultados para "{q}" em todas as categorias',
        'Asked {name}. Waiting for an answer...': 'Pedido enviado para {name}. Esperando a resposta...',
        'Nothing found.': 'Nada encontrado.',
        'The game offers nothing in this group right now. Get a little closer to {name} or try another group.':
            'O jogo não oferece nada nesta categoria agora. Chegue mais perto de {name} ou escolha outra.',
        "Your partner's Zoi isn't in your world right now.": 'O Zoi do seu par não está no seu mundo agora.',
        "The game doesn't offer that right now. Try again in a moment.": 'O jogo não oferece isso agora. Tente de novo daqui a pouco.',
        '{name} wants to move in with you: one family, one home': '{name} quer morar com você: uma família, uma casa',
        '{name} wants to get engaged to you': '{name} quer noivar com você',
        '{name} wants to marry you': '{name} quer se casar com você',
        '{name} wants to have a baby with you': '{name} quer ter um bebê com você',
        'Moving in together': 'Morar juntos',
        'Engagement': 'Noivado',
        'Wedding': 'Casamento',
        'Baby': 'Bebê',
        'You live together now': 'Agora vocês moram juntos',
        "You're engaged!": 'Vocês estão noivos!',
        "You're married!": 'Vocês se casaram!',
        'A baby is on the way!': 'Um bebê está a caminho!',
        'Hug': 'Abraçar',
        'Kiss': 'Beijar',
        'Dance': 'Dançar',
        'Wave': 'Acenar',
        'High five': 'Toca aqui',
        'Hold hands': 'Dar as mãos',
        'Selfie': 'Selfie',
        'Spin': 'Rodopiar',
        '{name} wants a high five': '{name} quer um "toca aqui"',
        '{name} wants to hold your hand': '{name} quer dar as mãos com você',
        '{name} wants a selfie with you': '{name} quer tirar uma selfie com você',
        '{name} wants to spin you around in a hug': '{name} quer te abraçar rodopiando',
        'High five request': 'Pedido de "toca aqui"',
        'Hold hands request': 'Pedido para dar as mãos',
        'Selfie request': 'Pedido de selfie',
        'Spin request': 'Pedido para rodopiar',
        'Where: {place}': 'Onde: {place}',
        'Where: not known yet': 'Onde: ainda não sabemos',
        'near you': 'perto de você',
        'in another city': 'em outra cidade',
        'Sleeping': 'Dormindo',
        'with you': 'com você',
        'with {name}': 'com {name}',
        'Doing: {what}': 'Fazendo: {what}',
        'Together': 'Juntos',
        'Time in their game: {time}': 'Hora no jogo do seu par: {time}',
        'Offline': 'Offline',
        'Connected, waiting for their game to start': 'Conectado, esperando o jogo do outro lado abrir',
        'Last seen just now': 'Visto agora há pouco',
        'Last seen {n} min ago': 'Visto há {n} min',
        'Last seen {n} h ago': 'Visto há {n} h',
        'your partner': 'seu par',
        'Going to {name}...': 'Indo até {name}...',
        'today at {time}': 'hoje às {time}',
        '{date} at {time}': '{date} às {time}',
        '{name} wants to hug you': '{name} quer te abraçar',
        '{name} wants to kiss you': '{name} quer te beijar',
        '{name} wants to dance with you': '{name} quer dançar com você',
        '{name} is waving at you': '{name} está acenando para você',
        '{name} invited you to their home': '{name} te convidou para a casa dele(a)',
        '{name} sent you a request': '{name} te mandou um pedido',
        '{n} seconds left to answer': 'Faltam {n} segundos para responder',
        'Accept': 'Aceitar',
        'Decline': 'Recusar',
        'You said yes!': 'Você aceitou!',
        "It didn't work. {msg}": 'Não deu certo. {msg}',
        'Hug request': 'Pedido de abraço',
        'Kiss request': 'Pedido de beijo',
        'Dance request': 'Pedido de dança',
        'Wave request': 'Aceno',
        'Invitation': 'Convite',
        '{what}: waiting for {name} to answer...': '{what}: esperando {name} responder...',
        '{what}: {name} said yes!': '{what}: {name} aceitou!',
        '{what}: {name} said no.': '{what}: {name} recusou.',
        "{what}: {name} didn't answer.": '{what}: {name} não respondeu.',
        '{what}: happening now!': '{what}: acontecendo agora!',
        '{what}: done!': '{what}: pronto!',
        "{what}: it didn't work. {msg}": '{what}: não deu certo. {msg}',
        '{what}: {state}': '{what}: {state}',
        "not in the game's world": 'você não está dentro do jogo',
        "your partner's Zoi isn't here": 'o Zoi do seu par não está aqui',
        // errors of the couple calls, by code
        "Your partner isn't online right now.": 'Seu par não está online agora.',
        'Go into the game first (load your save).': 'Entre no jogo primeiro (carregue o seu save).',
        'Load your multiplayer save first.': 'Carregue o seu save multijogador primeiro.',
        "We don't know where your partner is yet. Try again in a few seconds.": 'Ainda não sabemos onde seu par está. Tente de novo em alguns segundos.',
        'Your partner is in another city ({city}). Travel there first.': 'Seu par está em outra cidade ({city}). Viaje para lá primeiro.',
        'Your partner is in another city. Travel there first.': 'Seu par está em outra cidade. Viaje para lá primeiro.',
        "You don't have a home in this save yet.": 'Você ainda não tem uma casa neste save.',
        'Not connected to the server.': 'Sem conexão com o servidor.',
        "That doesn't work.": 'Isso não funciona.',
        'That request has expired.': 'Esse pedido já expirou.',
        'Only whoever hosts can undo the shared save.': 'Só quem hospeda pode desfazer o save compartilhado.',
        'There is no earlier save to go back to.': 'Não há um save anterior para voltar.',
        'There is no backup on this PC yet.': 'Ainda não há cópia de segurança neste PC.',
        'Busy with the save right now. Try again in a moment.': 'Ocupado com o save agora. Tente de novo daqui a pouco.',
        'The server said no. Try again in a moment.': 'O servidor recusou. Tente de novo daqui a pouco.',
        'Something went wrong.': 'Algo deu errado.',
        // settings: living together, clock
        'Living together': 'Morar juntos',
        'Share one household (we play in the same world and save it together)': 'Morar juntos (jogamos no mesmo mundo e salvamos juntos)',
        'Last save: you, {when}': 'Último save: você, {when}',
        'Last save: {name}, {when}': 'Último save: {name}, {when}',
        'No shared save yet: it starts with the next save.': 'Ainda não há save compartilhado: começa no próximo save.',
        'Your world is out of date: there is a newer save. Load it when the panel asks.': 'Seu mundo está desatualizado: há um save mais novo. Carregue quando o painel perguntar.',
        'Working on the save...': 'Cuidando do save...',
        'Waiting for the game to save (save the game, or wait for the autosave).': 'Esperando o jogo salvar (salve o jogo ou espere o salvamento automático).',
        'Downloading the shared world': 'Baixando o mundo compartilhado',
        'Uploading the shared world': 'Enviando o mundo compartilhado',
        'Going back... {msg}': 'Voltando... {msg}',
        'Done: {msg}': 'Pronto: {msg}',
        "Couldn't go back: {msg}": 'Não deu para voltar: {msg}',
        'restoring your backup': 'restaurando sua cópia de segurança',
        'Undo the last save': 'Desfazer último save',
        'Yes, go back': 'Sim, voltar',
        'Cancel': 'Cancelar',
        'The shared world goes back to how it was before the last save, for both of you. The game leaves without saving and loads it. What you did since then is lost. Go back?':
            'O mundo compartilhado volta a ser como era antes do último save, para vocês dois. O jogo sai sem salvar e carrega essa versão. O que vocês fizeram depois disso será perdido. Voltar?',
        'Your save goes back to the last backup on this PC. The game leaves without saving and loads it. What you did since then is lost. Go back?':
            'Seu save volta para a última cópia de segurança deste PC. O jogo sai sem salvar e carrega essa versão. O que você fez depois disso será perdido. Voltar?',
        'Done: the shared world went back to the save before. Loading it...': 'Pronto: o mundo compartilhado voltou para o save anterior. Carregando...',
        'Going back to the backup... the game reloads by itself.': 'Voltando para a cópia de segurança... o jogo recarrega sozinho.',
        'Backups on this PC': 'Cópias de segurança neste PC',
        'No backups yet. One is made before a save is loaded.': 'Nenhuma cópia ainda. Uma é feita antes de carregar um save.',
        'Go back to this one': 'Voltar para esta',
        'Go back to the backup from {when}? The game leaves without saving and loads it. What you did since then is lost.':
            'Voltar para a cópia de {when}? O jogo sai sem salvar e carrega essa versão. O que você fez depois disso será perdido.',
        'before loading save number {v}': 'antes de carregar o save número {v}',
        'before loading the save': 'antes de carregar o save',
        'before going back': 'antes de voltar',
        'Clock': 'Relógio',
        'Follow whoever hosts (recommended)': 'Seguir quem hospeda (recomendado)',
        'Adjust between the two of us': 'Ajustar entre os dois',
        'Adjusting: whoever is behind in game time moves forward to the other.': 'Ajustar: quem estiver atrasado no tempo do jogo avança até o outro.',
        // help
        'Help': 'Ajuda',
        'Something not working? Check the connection: each line says what is wrong and what to do.':
            'Algo não está funcionando? Verifique a conexão: cada linha diz o que está errado e o que fazer.',
        'Check connection': 'Verificar conexão',
        'Checking...': 'Verificando...',
        'Still not working?': 'Ainda não funciona?',
        'Send diagnostics': 'Enviar diagnóstico',
        'Making the file... this takes a few seconds.': 'Criando o arquivo... isso leva alguns segundos.',
        'File saved on the Desktop: {name}. Send this file to Arthur.': 'Arquivo salvo na Área de Trabalho: {name}. Mande esse arquivo para o Arthur.',
        'Everything looks fine.': 'Está tudo certo.',
        '{n} problem(s) found: follow the yellow tips, then check again.': '{n} problema(s) encontrado(s): siga as dicas em amarelo e verifique de novo.',
        'Not needed here': 'Não é necessário aqui',
        'local network': 'rede local',
        'You: {mine} - partner: {theirs}': 'Você: {mine} - seu par: {theirs}',
        'The game is open': 'O jogo está aberto',
        'The mod is running in the game': 'O mod está funcionando no jogo',
        'The server answers': 'O servidor responde',
        'Signed in to the server': 'Entrou no servidor',
        'Same version as your partner': 'Mesma versão do seu par',
        'Tailscale is on': 'O Tailscale está ligado',
        'NordVPN is not in the way': 'O NordVPN não está atrapalhando',
        'Open inZOI and wait until it reaches the menu or your game.': 'Abra o inZOI e espere chegar no menu ou no seu jogo.',
        'Load your multiplayer save in the game.': 'Carregue o seu save multijogador no jogo.',
        "The mod isn't answering. Close inZOI and open it again.": 'O mod não está respondendo. Feche o inZOI e abra de novo.',
        'Choose a server: click "Servers" at the top of this panel.': 'Escolha um servidor: clique em "Servidores" no alto deste painel.',
        "The server isn't answering. Ask whoever hosts to check that the inZOI Multiplayer program is open.":
            'O servidor não responde. Peça para quem hospeda ver se o programa inZOI Multiplayer está aberto.',
        'Open Tailscale and check that it says Connected, on both computers.': 'Abra o Tailscale e veja se está escrito "Connected" (conectado) nos dois computadores.',
        'Close the inZOI Multiplayer program and open it again.': 'Feche o programa inZOI Multiplayer e abra de novo.',
        'The password is wrong. Check it with your partner (Settings tab).': 'A senha está errada. Confira com seu par (aba Ajustes).',
        'Your partner has an older version. Ask them to update inZOI Multiplayer.': 'Seu par está com uma versão mais antiga. Peça para atualizar o inZOI Multiplayer.',
        'Your version is older. Update inZOI Multiplayer.': 'Sua versão está mais antiga. Atualize o inZOI Multiplayer.',
        'You were banned from this server. Talk to whoever hosts.': 'Você foi banido deste servidor. Fale com quem hospeda.',
        'The server is full.': 'O servidor está cheio.',
        'Still connecting. Wait a few seconds and check again.': 'Ainda conectando. Espere alguns segundos e verifique de novo.',
        'Install Tailscale and sign in to the same account as your partner.': 'Instale o Tailscale e entre na mesma conta do seu par.',
        'Turn off NordVPN, or turn off its "Invisibility on LAN" option.': 'Desligue o NordVPN ou desative a "Invisibilidade na LAN" dele.',
        // notices by code
        'code.wrong_password': 'senha errada. Confira a senha com seu par em Ajustes.',
        'code.version_mismatch': 'versões diferentes do inZOI Multiplayer. Os dois precisam atualizar para a mesma versão.',
        'code.firewall_declined': 'o Firewall do Windows não foi liberado, então seu par pode não conseguir entrar. Hospede de novo e clique em Sim quando o Windows perguntar.',
        'code.version_differs': 'vocês estão com versões diferentes do inZOI Multiplayer. Atualizem os dois para evitar problemas.',
        'code.full': 'o servidor está cheio.',
        'code.banned': 'você foi banido deste servidor.',
        'code.bad_request': 'o servidor recusou a conexão (pedido inválido).',
        'code.unreachable': 'não deu para alcançar o servidor. Ele está aberto? O Tailscale está ligado?'
    };

    var EN = {
        'code.wrong_password': 'wrong password. Check the password with your partner in Settings.',
        'code.version_mismatch': "different inZOI Multiplayer versions. You both need the same version.",
        'code.firewall_declined': 'Windows Firewall was not opened: your partner may not reach you. Host again and click Yes when Windows asks.',
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
        [/^(.*) wants to hug you$/, '$1 quer te abraçar'],
        [/^(.*) wants to kiss you$/, '$1 quer te beijar'],
        [/^(.*) wants to dance with you$/, '$1 quer dançar com você'],
        [/^(.*) wants to high-five you$/, '$1 quer um "toca aqui"'],
        [/^(.*) wants to hold your hand$/, '$1 quer dar as mãos com você'],
        [/^(.*) wants to take a selfie with you$/, '$1 quer tirar uma selfie com você'],
        [/^(.*) wants to spin you around in a hug$/, '$1 quer te abraçar rodopiando'],
        [/^(.*) wants to invite you to their home$/, '$1 te convidou para a casa dele(a)'],
        [/^(.*) wants to wave at you$/, '$1 está acenando para você'],
        [/^(.*) said yes \(hug\)$/, '$1 aceitou (abraço)'],
        [/^(.*) said yes \(kiss\)$/, '$1 aceitou (beijo)'],
        [/^(.*) said yes \(dance\)$/, '$1 aceitou (dança)'],
        [/^(.*) said yes \(high five\)$/, '$1 aceitou (toca aqui)'],
        [/^(.*) said yes \(hold hands\)$/, '$1 aceitou (dar as mãos)'],
        [/^(.*) said yes \(selfie\)$/, '$1 aceitou (selfie)'],
        [/^(.*) said yes \(spin\)$/, '$1 aceitou (rodopiar)'],
        [/^(.*) said yes \(invitation\)$/, '$1 aceitou (convite)'],
        [/^(.*) said no \(hug\)$/, '$1 recusou (abraço)'],
        [/^(.*) said no \(kiss\)$/, '$1 recusou (beijo)'],
        [/^(.*) said no \(dance\)$/, '$1 recusou (dança)'],
        [/^(.*) said no \(high five\)$/, '$1 recusou (toca aqui)'],
        [/^(.*) said no \(hold hands\)$/, '$1 recusou (dar as mãos)'],
        [/^(.*) said no \(selfie\)$/, '$1 recusou (selfie)'],
        [/^(.*) said no \(spin\)$/, '$1 recusou (rodopiar)'],
        [/^(.*) said no \(invitation\)$/, '$1 recusou (convite)'],
        [/^(.*) wants to move in with you \(one family, one home\)$/, '$1 quer morar com você (uma família, uma casa)'],
        [/^(.*) wants to get engaged to you$/, '$1 quer noivar com você'],
        [/^(.*) wants to marry you$/, '$1 quer se casar com você'],
        [/^(.*) wants to have a baby with you$/, '$1 quer ter um bebê com você'],
        [/^(.*) said yes \(moving in together\)$/, '$1 aceitou (morar juntos)'],
        [/^(.*) said yes \(engagement\)$/, '$1 aceitou (noivado)'],
        [/^(.*) said yes \(wedding\)$/, '$1 aceitou (casamento)'],
        [/^(.*) said yes \(baby\)$/, '$1 aceitou (bebê)'],
        [/^(.*) said no \(moving in together\)$/, '$1 recusou (morar juntos)'],
        [/^(.*) said no \(engagement\)$/, '$1 recusou (noivado)'],
        [/^(.*) said no \(wedding\)$/, '$1 recusou (casamento)'],
        [/^(.*) said no \(baby\)$/, '$1 recusou (bebê)'],
        [/^(.*) wants to interact with you: (.*)$/, '$1 quer interagir com você: $2'],
        [/^(.*) wants to interact with you$/, '$1 quer interagir com você'],
        [/^(.*) said yes \(interaction\)$/, '$1 aceitou'],
        [/^(.*) said no \(interaction\)$/, '$1 recusou'],
        [/^The game doesn't offer that between you right now \((.*)\)$/, 'O jogo não oferece isso entre vocês agora ($1)'],
        [/^(.*) joined$/, '$1 entrou'],
        [/^(.*) left$/, '$1 saiu'],
        [/^(.*) saved the shared world$/, '$1 salvou o mundo compartilhado'],
        [/^(.*) shared a house: (.*)$/, '$1 compartilhou uma casa: $2'],
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
        [/^Restarting into inZOI Multiplayer (.*)\.\.\.$/, 'Reiniciando no inZOI Multiplayer $1...'],
        // undo of the shared save (household.undo.message)
        [/^the shared world is back at version (\d+)$/, 'o mundo compartilhado voltou para a versão $1'],
        [/^your save is back as it was at (.*)$/, 'seu save voltou a ser como era em $1'],
        [/^there's no backup of your multiplayer save yet$/, 'ainda não há cópia de segurança do seu save multijogador']
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
