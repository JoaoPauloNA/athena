# THEMIS-1 — reputação verificável e integração Nike

Status: `CONCLUÍDA` em 2026-08-30; Ruff/formato verdes e `54 passed` em Python 3.12.

## Objetivo

Levar o repositório separado Themis de protótipo v0.1 a núcleo técnico terminal,
sem juiz online obrigatório: eventos imutáveis, identidade completa, eixos de
medição separados, incerteza explícita e saída compatível com Nike.

## Escopo autorizado

- alterar somente o repositório `Athena/Themis`, seus testes e este contexto;
- corrigir `HONEST_FAILURE` para evidência de falha honesta, distinta de erro do harness;
- usar identidade modelo + versão + provider/CLI;
- separar capacidade/qualidade, honestidade, confiabilidade do harness e eficiência;
- tornar mistura de tarefas e suficiência estatística explícitas antes de `valid=true`;
- persistir eventos brutos por API append-only local, com duplicata rejeitada;
- importar resultados Aletheia somente por leitura e calibrar concordância por dados fornecidos;
- preservar a projeção mínima `valid` + `final_score` consumida pelo Nike e testá-la contra o contrato público real.

## Proibições

- não alterar Athena runtime, Aletheia, Argos, Aegis/build, Vault, Git, remotes ou beta;
- não chamar juiz/modelo/rede externa nem inventar nota de mercado;
- não permitir que Themis autorize, execute, bloqueie ou escolha agentes;
- não tocar, importar, lintar ou testar os dois arquivos protegidos do Athena;
- não fazer commit, push, tag, release ou deploy.

## Gate de saída

1. Eventos são validados, imutáveis e identificados sem colisão silenciosa.
2. `HONEST_FAILURE`, `FALSE_SUCCESS` e `HARNESS_ERROR` afetam eixos distintos.
3. Score inválido não influencia Nike; score válido mantém contrato compatível.
4. Mistura de tarefas, tamanho da amostra e incerteza aparecem no resultado.
5. Store append-only, importador Aletheia read-only e calibração determinística têm testes.
6. Suíte Themis, cross-repo Nike, Ruff e validações estruturais passam.
