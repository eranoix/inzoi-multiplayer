# Revisão do co-op inZOI (branch coop-casal, v0.8.0)

Revisão completa do código (cliente Python, servidor, mod Lua e interface) mais uma pesquisa na comunidade, feita em 30/10/2026.
Os itens marcados com ✔ foram conferidos diretamente no código. Os demais vêm da leitura dos revisores, com arquivo e linha.

## Contexto da pesquisa

- **O multiplayer oficial ainda não saiu.** A prévia de 18/09/2026 diz "em desenvolvimento", sem data. O nosso mod continua sendo o caminho.
- **O inZOI v0.11.0 (15/10/2026) lançou mods Lua oficiais**, e o v0.11.2 (29/10) mexeu no MCP. A API do jogo está mudando, então é bom testar a cada patch.
- **A porta da API (11212) é documentada oficialmente** e pode ser trocada com `-climcpport=<porta>`. O mod hoje fixa a 11212.
- **O projeto original está parado:** 0 estrelas, 0 issues, último commit em 12/10. A manutenção fica por nossa conta.
- **UE4SS:** o link fixo dá 404 porque a release "experimental-latest" muda o nome do zip a cada build. A solução é consultar a API do GitHub na hora do build e fixar o sha256 do build testado.
- **Boas práticas de mods parecidos** (Skyrim Together, BeamMP, SMAPI/Stardew):
  - interpolar o movimento com um buffer de 100–200 ms;
  - mandar um "keyframe" completo de tempos em tempos;
  - verificar versão e mods no handshake;
  - fazer backup do save antes de cada sessão;
  - fazer a atualização automática só com binários assinados (já fizemos).

Fontes: [mod-docs.playinzoi.com](https://mod-docs.playinzoi.com), [notas v0.11.0](https://simscommunity.info/2026/10/15/inzoi-update-october-15-2026-notes/), [prévia de setembro](https://simscommunity.info/2026/09/18/inzoi-update-coming-october/), [Gaffer On Games: snapshot interpolation](https://gafferongames.com/post/snapshot_interpolation/), [UE4SS releases](https://github.com/UE4SS-RE/RE-UE4SS/releases).

---

## P0: bugs que quebram o jogo a dois (corrigir primeiro)

| # | Problema | Onde | O que acontece | Correção |
|---|---|---|---|---|
| 1 ✔ | Senha com acento derruba a conexão | `server/inzoi_mp_server.py:429` (e :713) | `compare_digest` com "coração" lança `TypeError`. O servidor fecha a conexão sem mandar mensagem e a esposa fica em "Conectando..." para sempre. | Comparar em bytes (`.encode()`) e envolver o `dispatch` num `except Exception` que responde com erro. |
| 2 ✔ | Uma resposta estranha do jogo mata o loop de sincronização | `client/companion.py:725`, `client/game.py:61` | Só `GameError`/`OSError` são tratados. Um JSON vazio ou um `KeyError` (num loading ou teleporte) encerra a tarefa. O painel continua dizendo "conectado", mas o seu Zoi para de ser enviado. | O `Game.call` passa a converter `ValueError`/`KeyError`/`HTTPException` em `GameError`, e todo loop longo passa a ter `except Exception`: registra no log, espera e continua. |
| 3 ✔ | O Zoi do parceiro some e não volta | `client/companion.py:508`, `Main.lua:542`, servidor :450 | O spawn é marcado como feito antes de acontecer. Se o jogo estiver em pausa ou no modo construção, o comando expira em 4 s e nunca é refeito. Uma queda rápida de Wi-Fi gera "saiu" e depois "entrou", com uma corrida que deixa o fantoche apagado. | Se o fantoche não aparecer no heartbeat depois de 10 s, tentar de novo. Reconexão da mesma pessoa vira só uma troca de sessão, sem "saiu/entrou". O Lua avisa quando o spawn falha. |
| 4 | Fantoche com entidade sumida enche o log a cada frame | `Main.lua:585` | Erro a cada tick, gravado em arquivo a cada tick: disco e CPU em uso constante, e o fantoche não volta. | Depois de 3 erros seguidos, remover o fantoche e avisar com "despawned". Limitar logs repetidos. |
| 5 | Arquivo travado derruba a conexão | `client/mailbox.py:39`, `companion.py:445` | Se o Defender ou o OneDrive segurar o `MPBulk.lua`, o `PermissionError` é tratado como "conexão perdida": todos os fantoches somem e o cliente reconecta. | Capturar o erro no local e tentar de novo no `mailbox_loop`. |
| 6 | Erro do jogo antes de ele abrir derruba o host inteiro | `companion.py:298`, `lua_data.py:84` | Um `GameError` no `run()` encerra o programa, e com ele o servidor da esposa. | O mesmo tratamento do item 2. |

## P1: robustez e desempenho

7. **O relógio do host nunca se ajusta** (`companion.py:524`). Quando o host pausa, ele fica atrás e só o convidado corrige. Resultado: horários diferentes. *(Provável. Confirmar em teste.)*
8. **Sem timeout de leitura no cliente** (`companion.py:321`). Se o PC do host dormir, o convidado fica "conectado" para sempre. Usar `wait_for(readline(), 45)`.
9. **Chamadas que bloqueiam o loop no host** (`servers.py:400`, `mailbox.py:39`, hash SHA-256 no servidor). O `tailscale status` roda a cada 1,5 s com a página aberta, e o hash de um save de 500 MB trava o relay. Solução: cache e `asyncio.to_thread`.
10. **Movimento travado, com cerca de 1 s de atraso** (`Main.lua:347-402`, `companion.py` game_loop):
    - a sincronização roda a ~3 Hz, não aos ~5 Hz documentados;
    - o fantoche mira só no último ponto da trilha e para e recomeça a cada 3 m;
    - a rotação é feita por teleporte.

    Solução: mirar à frente usando a velocidade, seguir os cantos da trilha, usar conexão HTTP persistente e paralelizar as chamadas ao MCP.
11. **O `MPCmd.lua` é reescrito 10x por segundo mesmo sem mudanças** (`mailbox.py:78`), e o Lua recompila o arquivo a cada 0,1 s. Gravar só quando algo mudou, com um arquivo `MPSeq` pequeno.
12. **Versões diferentes passam despercebidas.** O convidado se atualiza sozinho e o host roda do código-fonte. Mandar a `VERSION` no hello e avisar quando forem diferentes.
13. **Servidor exposto e frágil** (`server :46`, `servers.py:350`):
    - escuta em 0.0.0.0, sem limite de conexões;
    - aceita linhas de 32 MB antes do login;
    - a senha é opcional.

    Solução: escutar só na rede local e no Tailscale, aceitar no máximo 64 KB antes do hello e tirar da interface a dica de abrir porta no roteador.
14. **Dados do servidor podem corromper** (`server :133-140`). Não há `fsync`, e um arquivo corrompido volta ao padrão: a senha some e todos perdem o perfil. Solução: `fsync`, guardar um `.bak` e não usar o padrão em silêncio.
15. **O disco cresce para sempre**: blobs de mundo antigos nunca são apagados (`server :210`).
16. **Fechar o jogo do host derruba o servidor** (`launcher.py:233`). No modo host, esperar o jogo voltar.
17. **Cache de structs envenenado** (`lua_data.py:95`). Uma resposta vazia durante um loading fica salva para sempre e o fantoche aparece sem cabelo ou maquiagem. Não guardar resultados vazios.
18. **A porta do MCP é fixa.** Respeitar `-climcpport`.
19. **Menores:**
    - `save_config` não é atômico;
    - `bulk_seq` reinicia em 0;
    - o limite de chat é 200 na interface e 500 no servidor;
    - o `game.json` do UE4SS não é atômico;
    - a conexão de arquivos não recebe ping.

## P2: interface e experiência (UI/UX)

1. **Português (PT-BR).** Hoje está tudo em inglês. Criar um dicionário `t()` e fazer o servidor mandar códigos de erro (`wrong_password`) em vez de frases.
2. **Modo casal.** Esconder servidores públicos, filtros e console de admin, e deixar dois botões grandes: **"Hospedar para meu par"** e **"Entrar no meu par"**.
3. **Faixa de conexão clara**, com ícone e texto em vez de só uma bolinha de 9 px:
   - "Conectado com [nome]";
   - "[nome] caiu, aguardando...";
   - "Não conectou: [motivo] [Tentar de novo]".
4. **Legibilidade.** O texto de 12–13 px em cinza 62% é difícil de ler por cima do jogo. Aumentar o contraste e a fonte, usar botões de verdade e permitir rolagem no chat.
5. **Mensagens que dizem o que fazer**, por exemplo "Seu par salvou mais recentemente. [Carregar o save dele]".
6. **Bugs da interface:**
   - não dá para remover a senha pelo painel (`app.js:172`);
   - um aviso antigo fica na tela depois de resolvido (`app.js:129`);
   - a lista de jogadores é refeita a cada segundo e engole cliques em "Join" (`app.js:51`).

## P3: features pensadas para o casal

1. **"Ir até meu par"**: viajar até o lote do parceiro e convidar para casa automaticamente (o `admit` já existe).
2. **Ações a dois sincronizadas**: abraçar, beijar ou dançar com um clique, começando nos dois PCs ao mesmo tempo, com aceitar/recusar.
3. **Família compartilhada**: os dois Zois no mesmo save, com um fluxo guiado de quem salva, backup automático antes de cada envio e "desfazer o último save" (o histórico já existe no servidor).
4. **Card "onde está meu par / o que está fazendo"** no painel.
5. **Álbum compartilhado**: fotos tiradas no jogo vão automaticamente para o chat ou um álbum.
6. **UE4SS automático**: buscar o build atual pela API do GitHub e fixar o sha256, para voltar a ter o botão de verdade no menu.

---

## Ordem sugerida

1. **P0 (1–6)** e os itens P1 de 8, 12 e 16: deixam a primeira sessão real estável. Cabe numa única release, a v0.8.1.
2. Primeiro teste real entre os dois PCs, com os logs dos dois lados.
3. **P1** de movimento e desempenho (10, 11, 9) e **P2** com PT-BR, modo casal e faixa de conexão: v0.9.
4. **P3**, uma feature por release.
