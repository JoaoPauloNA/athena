# ADR-0001 — Escopo de fechamento do Athena v1 e classificações terminais

**Data:** 2026-08-30 · **Status:** ACEITO · **Decisor:** loop de fechamento v1 (autoridade executora)

## Contexto

Fechamento técnico do Athena v1. As decisões do CEO para IAProxy, Content Gate,
aceite externo, Olimpo e Metis precisam de registro canônico com classificações
terminais explícitas.

## Decisões

### 1. Escopo REQUIRED do v1 (todos IMPLEMENTED_AND_VERIFIED)

Athena-MCP 7-tool runtime · Zeus/Nike roteamento determinístico · Aegis ·
Chronos · Evidence Gate (EG-1 + EG-3A sink atômico) · Tasks/Flow duráveis ·
Clio · Harmonia · Capsule/Iris · OLIMPO-0 · integração Aletheia · Themis v0.2 ·
Moiras shadow · Argos QA observacional · MLX/local com oráculo comum · beta
smoke · documentação canônica.

Runtime baseline de evidência: `5319763` (715 testes, P0 fresh).
Commits documentais posteriores não alteram a evidência de runtime.

### 2. IAProxy — OPTIONAL_NOT_CONFIGURED

zchat/kimi dependem de login manual do usuário. Ausência de sessão:
- não falha startup, submissão ou status core do v1;
- não dispara retry nem ruído no hot path;
- qwen/deepsweep ou qualquer adaptador disponível pode ser validado; nenhum é obrigatório.

### 3. Content Gate — OPTIONAL_FUTURE (corpus CG-0)

Não é mandatório para o v1 técnico. Pipeline técnico completo; calibração
exige corpus humano 50–100 anotado, que não existe e nunca será fabricado.
Termina como OPTIONAL_FUTURE até CG-0 existir.

### 4. Aceite externo — EXTERNAL_ACCEPTANCE_PENDING

Evidência valiosa pós-release; não bloqueia fechamento interno técnico.

### 5. Olimpo — OLIMPO-0 implementado; O-2..O-5 OPTIONAL_FUTURE

OLIMPO-0 (read-only local loopback) cobre a operação segura do v1.
Expansão meramente percentual é proibida.

### 6. SSH — INTENTIONALLY_CLOSED (D-SSH)

Transporte dormente testável; nunca ativar rede silenciosamente.

### 7. Metis — **OPÇÃO B: DEFERRED_BY_ADR**

Justificativa: a Opção A (Metis mínimo) exigiria contrato de fragmentos,
validador determinístico, versionamento e rollback — uma fatia nova com
superfície de segurança sensível (prompts proprietários), sem demanda medida
no v1 e sem consumo em produção. O princípio "não criar infraestrutura
especulativa" e o WIP=1 determinam diferir.

Pré-requisitos para implementação futura:
1. volume real de manutenção de prompts medido (ECO-1 instrumentação);
2. ADR de segurança de fragmentos aprovado;
3. Themis com projeções válidas para recomendar (nunca sobrescrever);
4. consumidor real no ciclo de especialista (ESP-*).

Metis permanece CONCEITO APROVADO; removido dos bloqueadores do v1.
Nenhum código placeholder será escrito.

## Consequências

- v1 fecha sem Metis/Content Gate/IAProxy-login/aceite externo sem violar gates.
- Toda classificação OPTIONAL_FUTURE/DEFERRED_BY_ADR/EXTERNAL_ACCEPTANCE_PENDING
  é terminal e não impede o fechamento técnico.
- Reabertura de qualquer item requer nova decisão canônica.
