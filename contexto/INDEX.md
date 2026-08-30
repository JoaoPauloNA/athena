# Contexto operacional — Athena-MCP

Este diretório é a base mínima de retomada do Athena-MCP. Ele separa estado verificado, fila de trabalho, contrato de handoff e histórico gerencial para evitar que planos sejam confundidos com runtime ativo.

## Ordem de leitura

1. `INDEX.md` — explica o conjunto e a ordem de leitura.
2. `ESTADO_ATUAL.md` — fotografia verificável do checkout, com maturidade e riscos separados.
3. `ROADMAP.md` — fila canônica finish-to-start e seu gate atual.
4. `CLAUDE_ORQUESTRADOR.md` — contrato curto para o gerente principal ativo e seus executores.
5. `gerencia_athena-mcp.md` — handoff técnico extenso, decisões vigentes e registros históricos.

## Papel de cada arquivo

- `ESTADO_ATUAL.md` responde “o que existe agora?” e deve privilegiar Git, código e testes presentes no checkout.
- `ROADMAP.md` responde “qual fatia pode abrir?”; WIP=1 impede iniciar uma linha posterior antes do fechamento verificável da anterior.
- `CLAUDE_ORQUESTRADOR.md` responde “como o gerente e o executor trabalham?” sem repetir todo o handoff técnico.
- `gerencia_athena-mcp.md` preserva a história, os checkpoints e as decisões arquiteturais detalhadas. Afirmações históricas não substituem a fotografia atual.

Em conflito, preservar as fontes e registrar a divergência. Código e Git provam o checkout; testes provam apenas o comportamento que exercitam; Vault e gerência governam decisões e sequência, mas não tornam uma proposta implementada.
