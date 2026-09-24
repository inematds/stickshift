# Plano v2: StickShift no Linux e no Windows, com painel web e chat

**Status: v2, escopo escolhido pelo Nei em 24/09/2026. Implementação ainda não iniciada.**

| Versão | O que muda |
|---|---|
| v1 (24/09) | Linux/Windows via multiplexador (tmux, WezTerm), só CLI; painel nativo por sistema. |
| **v2 (atual)** | O painel vira **web**, um só para Linux, Windows e macOS, e ganha **chat nível 1**: prompts para o agente do painel escolhido. |
| v3 (anotado, fora do escopo) | **Chat com permissões**: aprovar ou negar pelo chat os pedidos de permissão do agente. |

## O problema

O StickShift de hoje depende de três peças que só existem no macOS:
- a **Acessibilidade**, para ler o terminal;
- o **`CGEvent`**, para digitar;
- a **assinatura de código e o TCC**, para provar que o outro lado é mesmo o Claude Code ou o Codex.

O `docs/WINDOWS.md` estima de 2 a 4 semanas só para o Windows Terminal nativo.

## A ideia: pedir ao terminal, não ao sistema operacional

Multiplexadores e terminais modernos têm uma API oficial para ler o texto de um painel, mandar texto para ele e dizer qual processo roda ali:

| Necessidade | macOS (hoje) | tmux | WezTerm¹ | kitty¹ |
|---|---|---|---|---|
| Ler o painel | Acessibilidade | `tmux capture-pane -p -t <painel>` | `wezterm cli get-text --pane-id` | `kitty @ get-text` |
| Digitar | `CGEvent` + layout | `tmux send-keys -l` + `Enter` | `wezterm cli send-text --no-paste` | `kitty @ send-text` |
| Listar painéis | janela em foco | `tmux list-panes -a -F …` | `wezterm cli list` | `kitty @ ls` |
| Qual processo | tty + grupo | `#{pane_pid}` + `/proc/<pid>/stat` | `wezterm cli list` | `kitty @ ls` |
| Onde roda | só macOS | Linux, macOS, WSL | Linux, macOS, **Windows nativo** | Linux, macOS |

¹ Campos tirados da documentação; confirmar na fase 0 do backend correspondente.

**A leitura via tmux já foi testada** neste servidor em 24/09/2026. O `capture-pane` leu o rodapé, o seletor de modelos e os seletores de esforço do Codex 0.156.1 e o seletor `/model` do Claude Code 2.1.282.

## Arquitetura v2

```
navegador (painel + chat) ──HTTP/WebSocket──► stickshift-web (Python, 127.0.0.1)
                                                ├─ catálogo de modelos, planos, máquina de estados
                                                ├─ checagens de segurança (fail-closed) e log
                                                └─ backend: tmux | WezTerm ──► painéis com claude / codex
```

- **Núcleo em Python.** O classificador, os planos, o catálogo (`Models.m` do branch `catalogo-modelos`), a máquina de estados e os códigos de recusa são portados do Objective-C, junto com os testes (fixtures com telas reais). A versão macOS em Objective-C continua como está.
- **Backends de terminal** com a mesma interface: `listar`, `ler`, `digitar` e `processo`. O tmux vem primeiro; o WezTerm depois.
- **Painel web.** É o `gearbox.html` atual servido localmente. Um adaptador troca a ponte `webkit.messageHandlers` (`shift`, `policy`; `drag` e `resize` saem) por chamadas HTTP/WebSocket e alimenta `setProfiles`, `setLive` e `outcome`.
- **Lista de painéis em vez de "painel em foco".** Na web, o foco é do navegador. O painel lista todos os painéis com agente (sessão, pasta, agente, modelo e esforço atuais) e você escolhe o alvo. Isso resolve o problema de foco do macOS.

## Chat nível 1 (escopo v2)

**O que é:** uma caixa de chat ao lado do câmbio. O texto vai como **prompt para o agente do painel escolhido** (Claude Code ou Codex), e a resposta volta lida da tela, com histórico por painel.

**Regras:**
- **Só envia com o agente ocioso e o campo vazio**, com as mesmas provas das trocas de modelo. Se o agente estiver ocupado ou houver rascunho, recusa com motivo.
- **Tarefas no sistema passam pelo agente.** "Veja o disco" ou "reinicie o serviço X" são executados pelo Claude ou pelo Codex, com o sistema de permissões **deles**, no próprio terminal. O chat nunca executa comando de shell.
- **Texto livre é tratado como texto.** Vai com `send-keys -l`, literal, sem interpretar teclas. Um `Enter` só é enviado para submeter o prompt. Mensagens que começam com `/` são recusadas; modelo e esforço se trocam pelo câmbio, não pelo chat.
- **Não responde pedidos de permissão.** Se o agente abrir um "Allow this command?", o chat mostra que o agente está esperando no terminal e para por aí. Aprovar pelo chat é a **v3**.
- **Leitura da resposta.** O chat considera a resposta pronta quando o agente volta a ficar ocioso e captura o trecho novo da tela desde o envio. Respostas longas ficam truncadas, com a indicação "ver no terminal".

## Segurança (vale para painel e chat)

- Escuta só em **`127.0.0.1`** por padrão.
- **Token** gerado a cada início, exigido em toda chamada, com **checagem de `Origin`**, para que nenhum outro site aberto no navegador dispare trocas ou prompts.
- **Acesso de outra máquina** (celular, outro PC pelo Tailscale) só com opção explícita, sempre com token.
- **Log** de tudo o que foi enviado: data, painel, texto e resultado.
- Nada de terminal web cru (tipo `ttyd`). O StickShift só digita `/model`, `/effort` e prompts para um agente reconhecido.
- **Identidade mais fraca que no macOS**, sem assinatura de código. Fica o caminho do executável na instalação conhecida (`~/.local/share/claude/versions/<v>`, pacote do Codex), a versão e o hash registrado na qualificação. A diferença fica documentada.

## Fases

| Fase | Entrega | Critério de pronto | Estimativa |
|---|---|---|---|
| **0. Spike** | Abrir `claude` e `codex` no tmux, capturar e rodar o classificador. A captura já foi feita em 24/09 | Status, esforço, ocioso e campo vazio reconhecidos nos dois agentes | ½ dia |
| **1. Núcleo em Python** | Classificador, planos, catálogo, máquina de estados, recusas e testes portados | Mesmos casos passando que no macOS | 2–3 dias |
| **2. tmux + CLI (Linux)** | `status`, simulação, `--commit` e atalhos `bind-key` | Troca real de modelo e esforço nos dois agentes, testada **neste servidor** | 1–2 dias |
| **3. Painel web** | `stickshift-web`, `gearbox.html` com o adaptador, lista de painéis, token/Origin e log | Câmbio e acelerador funcionando no navegador, com recusas visíveis | 1–2 dias |
| **4. Chat nível 1** | Prompt para o painel escolhido, resposta lida da tela, histórico, regras acima | Pergunta e resposta nos dois agentes; recusa com o agente ocupado ou com rascunho | 2–3 dias |
| 5. WezTerm (Windows nativo) | Segundo backend | Troca e chat testados no Linux; no Windows, numa máquina Windows | 1–2 dias + teste no Windows |

**Total das fases 0 a 4: cerca de 7 a 11 dias**, todos testáveis neste servidor. No Windows, quem usa WSL já fica coberto com o tmux; o Windows nativo vem com a fase 5. O Windows Terminal nativo via UIA (`docs/WINDOWS.md`, 2–4 semanas) continua fora.

## v3 (anotado, fora do escopo atual)

**Chat com permissões.** Quando o agente pedir permissão ("Allow this command?", "Do you want to proceed?"), o chat mostra o pedido com botões **aprovar** e **negar**.
- Desligado por padrão, ligado por configuração explícita.
- Só com token, e o log registra quem aprovou, o quê e quando.
- Só em pedidos reconhecidos pelo classificador (texto exato do diálogo). Diálogo desconhecido = nada é apertado.
- Revisar antes a política de cada agente, porque aprovar pelo chat equivale a aprovar no terminal.

## Riscos

- **Formato da tela muda entre versões.** O Codex 0.156.1 já mudou o rodapé e o seletor de esforço. Mitigação: fixtures com telas reais + requalificação (`status` e simulação) a cada versão.
- **Linha de status do Claude.** O classificador de hoje lê o modelo de uma linha `📂 <pasta> · <Modelo>`, que parece vir de um statusline personalizado do autor original. Aqui o statusline é outro. A fase 0 decide a fonte do modelo: exigir um statusline conhecido, ou usar a confirmação `Set model to …` e o chip `◐ medium · /effort` do Claude Code padrão.
- **Chat lendo resposta pela tela.** Respostas longas rolam para fora do `capture-pane`. Mitigação: capturar com histórico (`-S -`), limitar o tamanho e oferecer "ver no terminal".
- **Painel exposto na rede por engano.** Mitigação: loopback + token + `Origin` por padrão; o acesso remoto é opcional.
- **Usuário fora de tmux/WezTerm/kitty.** Não é suportado; recusa com motivo claro.
