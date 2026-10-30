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
2. Dar dois cliques em `hospedar.bat`. Na primeira vez, o Windows pergunta sobre o firewall: permitir em **rede privada**.
3. O servidor **continua no ar mesmo se o jogo fechar ou travar**: é só abrir o inZOI de novo. Para desligar, feche a janela preta.

Se usar NordVPN, desligue-o ou desative a "Invisibilidade na LAN".

## Para jogar

1. O Arthur abre o `hospedar.bat`.
2. A esposa abre o `inZOI-Multiplayer.exe`.
3. Na primeira vez, cada um aceita criar a **cópia multiplayer do save**: os saves normais não são mexidos.
4. No painel do jogo, o **Modo casal** mostra dois botões: **Hospedar para meu par** e **Entrar no meu par**.
5. Os dois precisam estar na **mesma versão do inZOI**. Os mods do host são oferecidos automaticamente.

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
- Alternativa sem `.exe`: o `instalar-coop.ps1` instala pelo código-fonte (versão antiga `bafbdf4`, sem as correções).
