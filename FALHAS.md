# FALHAS — uma linha por falha (mais recente no topo)

| data | o que quebrou | menor correção | prompt \| infra |
|---|---|---|---|
| 2026-09-24 | Claude Code 2.1.282 mostra sugestão de próximo prompt no campo (texto dim) → lido como rascunho, toda troca recusava com DRAFT_PRESENT | capturar com `tmux capture-pane -e` e tratar texto só-dim após o `❯` como campo vazio | infra |
| 2026-09-24 | Claude Code 2.1.282 tirou o "esc to interrupt" do estado ocupado (`· Pontificating…`) → agente trabalhando parecia ocioso | ocupado = linha acima do campo começando com glifo de spinner e contendo "…" | infra |
| 2026-09-24 | Codex 0.156.1: rodapé com display name, Max/Ultra em submenu e `»` no Ultra → parser e plano recusariam | regex sem distinção de maiúsculas + normalizar ao slug; plano via "More reasoning…"; aceitar `›` e `»` | infra |
