# Plano: StickShift no Linux e no Windows

**Status: proposta para aprovação (24/09/2026). Nada disto foi implementado.**

## O problema

O StickShift de hoje se apoia em três peças que só existem no macOS:

1. **Leitura do terminal pela Acessibilidade** (`AXUIElement`), para saber o que está na tela;
2. **Digitação sintética** (`CGEvent`), para mandar `/model` e `/effort`;
3. **Permissões do sistema e assinatura de código** (TCC, `SecStaticCode`), para provar que o processo do outro lado é mesmo o Claude Code ou o Codex.

O `docs/WINDOWS.md` já propõe substituir cada peça por um equivalente do Windows (UI Automation + `SendInput`). O custo estimado lá é de **2 a 4 semanas** só para o Windows Terminal. O pior pedaço é descobrir qual processo está em qual aba.

## A ideia: pedir ao terminal, não ao sistema operacional

Multiplexadores e terminais modernos já têm uma API oficial para **ler o texto de um painel** e **mandar texto para ele**. A API também informa **qual painel está ativo** e **qual processo roda nele**. Com isso, as três peças do macOS deixam de ser necessárias:

| Necessidade | macOS (hoje) | tmux | WezTerm | kitty |
|---|---|---|---|---|
| Ler o painel | Acessibilidade | `tmux capture-pane -p -t <painel>` | `wezterm cli get-text --pane-id` | `kitty @ get-text` |
| Digitar | `CGEvent` + layout do teclado | `tmux send-keys -l` + `Enter` | `wezterm cli send-text --no-paste` | `kitty @ send-text` |
| Painel ativo | janela em foco + árvore AX | `#{pane_active}` do cliente mais recente | `wezterm cli list` (campo de foco) | `kitty @ ls` (`is_focused`) |
| Qual processo | tty + grupo de processos | `#{pane_pid}` + `/proc/<pid>/stat` (tpgid) | `wezterm cli list` (pid/tty) | `kitty @ ls` (`foreground_processes`) |
| Onde roda | só macOS | Linux, macOS, WSL | **Linux, macOS e Windows nativo** | Linux, macOS |

Vantagens sobre a Acessibilidade:
- **Digitação sem layout de teclado.** O texto chega literal ao painel, então some toda a lógica de "este layout consegue digitar este caractere?".
- **Não depende de foco nem de janela.** Funciona em X11, Wayland e SSH, onde o Wayland bloquearia a digitação sintética.
- **Sem permissão especial.** Não há TCC nem popup de Acessibilidade.

## O que continua igual

O `docs/WINDOWS.md` já identifica a parte portátil do código:
- **O classificador de painel** (`AXState classifyText`) recebe uma string e identifica agente, modelo, esforço, ocupado/ocioso, diálogo aberto e campo vazio. Com tmux, a string vem do `capture-pane` em vez da Acessibilidade.
- **Os planos por agente** (`Protocol.m`) e o **catálogo de modelos** (`Models.m`), que acabou de ser criado.
- **A máquina de estados** (`Switch.m`): ordem das checagens, prova de entrega por delta de ocorrências e códigos de recusa.
- **Os testes com capturas reais de tela** e o **painel `gearbox.html`**.

### As garantias de segurança ("fail-closed")

| Garantia | Linux/Windows via multiplexador |
|---|---|
| Só age num painel com agente reconhecido | **Mantida.** O classificador é o mesmo. |
| Só digita com o agente ocioso e o campo vazio | **Mantida.** A regra é a mesma, sobre o texto capturado. |
| Prova de entrega e verificação na linha de status | **Mantida.** O `capture-pane` depois de cada passo substitui a releitura por Acessibilidade. |
| Recusa com código de motivo em vez de chutar | **Mantida.** Os códigos são portados como estão. |
| Identidade do agente por assinatura de código | **Mais fraca.** No Linux não há assinatura da Anthropic/OpenAI para conferir. Troca por: caminho do executável (`/proc/<pid>/exe`) dentro da instalação conhecida (`~/.local/share/claude/versions/<v>`, pacote do Codex) + versão + hash SHA-256 registrado na qualificação. Precisa ficar documentado como diferença. |
| Painel nunca rouba o foco do terminal | **Some como problema.** A digitação vai pelo multiplexador, não pelo teclado. |

## Linguagem

O motor atual é Objective-C com frameworks da Apple; no Linux isso exigiria GNUstep, que é frágil. Proposta: **portar a lógica pura para Python 3**. É a linguagem dos projetos daqui, já existe `spikes/ptydrive.py` e não precisa de build. A versão macOS em Objective-C fica intacta; as duas compartilham os **fixtures de teste** (capturas de tela em texto) e o **formato do catálogo** (`config.toml`).

Alternativa: Go, se um binário único for prioridade. Custa mais ou menos 30% a mais de tempo.

## Fases

| Fase | Entrega | Critério de pronto | Estimativa |
|---|---|---|---|
| **0. Spike (decide a viabilidade)** | Script que abre `claude` e `codex` num painel tmux, captura a tela e roda o classificador sobre ela | Linha de status, esforço e campo vazio reconhecidos nos dois agentes | ½ dia |
| **1. Núcleo em Python** | Classificador, planos, catálogo, máquina de estados e códigos de recusa, com os testes do `core_test.m` portados | Mesmos casos passando que no macOS | 2–3 dias |
| **2. Linux com tmux (CLI)** | `stickshift status`, `stickshift <marcha>` (simulação), `--commit` e atalhos de teclado do tmux (`bind-key`) | Troca de verdade de modelo e esforço no Claude Code e no Codex, testada **neste servidor** | 1–2 dias |
| **3. WezTerm (inclui Windows nativo)** | Segundo backend com a mesma interface | Troca testada no Linux; no Windows, testada numa máquina Windows | 1–2 dias + teste no Windows |
| **4. Painel gráfico** | `gearbox.html` servido localmente, aberto numa janela (ícone de bandeja opcional) | Câmbio e acelerador funcionando pelo painel | 2–3 dias |
| 5. (opcional) Windows Terminal nativo | O caminho UIA + `SendInput` do `docs/WINDOWS.md` | — | 2–4 semanas; **não recomendado** agora |

**Windows na prática:**
- **Quem roda o Claude Code/Codex dentro do WSL** usa a versão Linux + tmux sem mudar nada (fase 2).
- **Quem roda nativo no Windows** usa o WezTerm (fase 3).
- **Só o Windows Terminal puro** exigiria a fase 5.

## Riscos

- **Formato da tela muda entre versões.** É o mesmo risco do macOS. Mitigação: o mesmo processo de requalificação (fixtures + `status` + simulação).
- **Identidade mais fraca** (sem assinatura de código). Mitigação: hash + caminho da instalação e recusa quando não baterem; fica documentado.
- **Diálogos de confirmação** do Claude ("Switch model?") dependem de ler a tela certa. O `capture-pane` lê exatamente o painel, então tende a ser mais confiável que a Acessibilidade.
- **Usuário fora de tmux/WezTerm/kitty.** Não é suportado; a ferramenta recusa com um código de motivo claro.

## Recomendação

Aprovar as **fases 0 a 2** (≈ 4–6 dias). Elas são testáveis de ponta a ponta neste servidor, que tem tmux 3.4, Claude Code 2.1.282 e Codex 0.156.1, e já cobrem Linux e Windows via WSL. A fase 3 entra depois, se houver interesse no Windows nativo. A fase 5 fica fora.
