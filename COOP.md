# Co-op no inZOI: Arthur + esposa

Fork pessoal do [Darker117/inzoi-multiplayer](https://github.com/Darker117/inzoi-multiplayer) (beta, sem licença: uso pessoal).
Cada um joga no próprio PC com o próprio Zoi. O Zoi do outro aparece no seu mundo e faz o que a outra pessoa faz.
As correções e melhorias desta branch estão em [REVISAO.md](REVISAO.md).

## PC da esposa: só o `.exe`

Mandar para ela o `dist\inZOI-Multiplayer.exe`, que está assinado com o certificado do Arthur. Ela dá dois cliques e pronto:

- Não precisa de Python nem de instalação nenhuma.
- Na primeira vez, o Windows pergunta se deseja **instalar o certificado** "inZOI Co-op (eranoix)": é só clicar em **Sim**.
- O programa já entra no servidor do Arthur (`100.99.177.28`, o IP do Tailscale).
- Ele se atualiza sozinho a partir das releases de `github.com/eranoix/inzoi-multiplayer`. Só aceita versões assinadas
  com o certificado do Arthur.

Se o `.exe` chegar por WhatsApp ou e-mail e o Windows reclamar ("O Windows protegeu o computador"): ela clica com o botão
direito no arquivo, vai em **Propriedades**, marca **Desbloquear** e aplica. Isso é preciso só uma vez; as atualizações não passam por esse aviso.
O plano B, se a pergunta do certificado não aparecer, é o `dist\confiar-certificado.bat`.

## PC do Arthur (host)

1. Python 3.10 ou mais novo, com Tailscale ligado.
2. Dar dois cliques em `hospedar.bat`. Na primeira vez, o Windows pergunta "Permitir que este app faça alterações?" para liberar o firewall (só a porta 24242, só redes privadas): clique em **Sim**.
3. Ao fechar o jogo, o servidor para de escutar (a porta fecha) e volta sozinho quando você abre o inZOI de novo. Para desligar de vez, feche a janela preta.

Se usar NordVPN, desligue-o ou desative a "Invisibilidade na LAN".

## Para jogar

1. Os dois abrem o mesmo `inZOI-Multiplayer.exe` (atalho "inZOI Co-op"). No PC do Arthur ele hospeda sozinho; no dela, entra no servidor do Arthur.
2. **Cada um joga o próprio save.** Na primeira vez, cada um aceita criar a **cópia multiplayer do save**: os saves normais não são mexidos.
3. Vocês se encontram no **mesmo mapa**: precisam estar na mesma cidade do jogo para se verem.
4. No painel do jogo, o **Modo casal** mostra dois botões: **Hospedar para meu par** e **Entrar no meu par**.
5. Os dois precisam estar na **mesma versão do inZOI**. Os mods do host são oferecidos automaticamente.

## No painel do jogo

- **Card do par:** onde o outro está e o que está fazendo, com os botões **Ir até meu par** e **Convidar para casa**.
  Se o Zoi do outro ainda não apareceu, o card diz o motivo: está no menu, com o jogo pausado, em outro save,
  com uma pergunta na tela... ou é o **seu** jogo que está pausado.
- **Ações a dois:** Abraçar, Beijar, Dançar, Toca aqui, Dar as mãos, Selfie e Rodopiar. O outro recebe um pedido com
  **Aceitar** / **Recusar**. Testadas no jogo: o programa escolhe a interação que o próprio jogo oferece para o casal
  (os dois Zois ficam com relacionamento alto quando o Zoi do par entra no mundo).
- **Ajustes → Seu save multijogador:** cópias de segurança automáticas e **Desfazer último save** (do seu próprio save).
- **Ajustes → Relógio:** seguir quem hospeda (padrão) ou ajustar entre os dois.
- **Ajuda:** **Verificar conexão** (diz o que está errado e como resolver) e **Enviar diagnóstico**, que gera um zip
  na Área de Trabalho para mandar ao Arthur.
- Se o Tailscale não responder, o programa acha o PC do Arthur sozinho pela rede de casa.
  O firewall é liberado sozinho na primeira vez que alguém hospeda (o Windows pergunta uma vez: clique em **Sim**).

## Proteções automáticas

- **Queda rápida de conexão** (Wi-Fi, atualização do programa): o Zoi do outro fica até 10 segundos esperando e não some.
- **Reinício do programa com o jogo aberto:** ele lembra que você estava no save multijogador e continua, sem perguntar.
- **Ir até meu par** passa por uma tela de carregamento: o programa sabe que é o mesmo save e não pausa o multijogador.
- **Zoi do par gravado no seu save** (o jogo salva sozinho a cada 15 min): ao carregar, se o par estiver online o Zoi
  é reaproveitado; se não, é removido.

- **Reversão automática:** se uma versão nova não abrir direito duas vezes seguidas, o programa volta sozinho para a anterior.
- **Botão Multiplayer no menu (UE4SS):** se o inZOI fechar logo depois de abrir duas vezes seguidas, o UE4SS é removido sozinho.

## Publicar uma versão nova (Arthur)

1. Mudar `VERSION` em `client/companion.py` (por exemplo, `0.8.2`).
2. Rodar `python tools/build_release.py --publish notas.md`. Ele gera o `.exe`, assina e publica a release no GitHub.
3. O PC dela baixa a versão nova sozinho e aplica na próxima vez que ela abrir o jogo.

O certificado foi criado com `tools/criar-certificado.ps1`. **Guarde um backup do `.pfx`**: sem a chave, as atualizações
deixam de ser aceitas no PC dela e você precisa mandar um `.exe` novo manualmente.

## Quando algo não funciona

- Os logs ficam em `%APPDATA%\inzoi-mp\companion.log` (nos dois PCs). Mande os dois para o Claude analisar.
- Rodar com `-v` mostra mais detalhes: `hospedar.bat -v`.
- Se o jogo atualizou e quebrou: corrigir na branch e publicar uma versão nova.
- Sem UE4SS (o build fixado pelo projeto saiu do GitHub), o botão do menu é desenhado por cima do jogo e a página de
  servidores abre numa janela do Edge. Funciona igual, só é menos bonito.
- Para deixar um PC com um único jeito de abrir: rodar `powershell -ExecutionPolicy Bypass -File .\instalar-coop.ps1`.
  Ele baixa a versão mais nova como **inZOI Co-op.exe** direto na área de trabalho e apaga as outras cópias e atalhos
  (Downloads, área de trabalho, raiz de Documentos, `%LOCALAPPDATA%\Programs\inZOI Co-op`, a instalação antiga
  `entrar.bat`). O programa se atualiza ali mesmo; a cópia da versão anterior fica escondida. Saves e configurações
  não são tocados.
