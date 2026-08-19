# Verificação reforçada

> Política que sinaliza quando um resultado do `run_combo` merece verificação
> mais rigorosa do que a padrão, por ter sido produzido fora do caminho
> previsto para a tarefa.

Implementação: [`athena/reinforced_verification.py`](../../athena/reinforced_verification.py) ·
Integração: [`athena/router.py`](../../athena/router.py)

---

## O que é

Nem todo resultado bem-sucedido merece a mesma confiança. Um combo pode
terminar com `exit_code=0` depois de cair para um agente que não estava no
plano, ou usando um modelo mais fraco que o recomendado para a tarefa. Nesses
casos o resultado continua válido — mas o caminho até ele foi menos confiável
do que o declarado.

A política de verificação reforçada detecta exatamente essas situações e
levanta um sinal auditável: `requires_reinforced_verification` mais a lista de
`reasons` que o justificam.

## A regra

`requires_reinforced_verification` é verdadeiro quando **ao menos uma** destas
condições ocorre:

| # | Condição | Código da razão |
|---|---|---|
| 1 | Foi usado um modelo de IA local | `local_model_used` |
| 2 | A tarefa é complexa **e** foi usado um agente fora do padrão definido para ela | `complex_task_non_standard_agent` |
| 3 | O agente usado está fora do nível recomendado para a tarefa | `agent_outside_recommended_level` |
| 4 | Houve fallback para um agente não previsto na cadeia original | `fallback_outside_original_chain` |

> **Tarefa complexa sozinha não ativa.** Uma tarefa complexa executada
> inteiramente pela cadeia prevista e por agentes do nível recomendado **não**
> exige verificação reforçada. Complexidade não é, por si, sinal de
> desconfiança — desvio do plano é.

Todas as razões aplicáveis são preservadas: não há short-circuit. Se quatro
condições ocorrem ao mesmo tempo, as quatro aparecem em `reasons`.

## A política sinaliza, não coage

Esta é a decisão de projeto mais importante do módulo:

- **não** altera `exit_code`, `output` ou o veredito do verificador;
- **não** dispara verificação automaticamente quando `verify=False`;
- **não** muda a política de fallback do combo nem a cadeia executada.

Ela apenas **anexa** a decisão a `RunResult.reinforced_verification`, adiciona
um aviso legível em `RunResult.warnings` e emite um evento no Flight Recorder.
Quem decide o que fazer com o sinal é a camada de cima — orquestrador ou
humano. Disparar verificação extra silenciosamente mudaria custo e latência
sem consentimento, exatamente o tipo de comportamento que o Athena evita.

## Como cada condição é determinada

### 1. Modelo local

Em ordem de precedência:

1. override explícito `used_local_model`;
2. `used_provider_id` em `LOCAL_MODEL_PROVIDER_IDS` (`ollama`, `goose`);
3. `service_profile_id == "local_model"`.

### 2. Agente fora do padrão

O **padrão** da tarefa é o provider primário do combo (`chain[0]`) — é ele que
o plano declara como executor esperado. Qualquer outro agente, mesmo previsto
como fallback na cadeia, é "fora do padrão".

Isso mantém as regras 2 e 4 independentes: cair para `chain[1]` numa tarefa
complexa dispara a regra 2 (agente não é o padrão), mas não a 4 (continua
dentro da cadeia original).

Sem padrão declarado, a regra não dispara — não há como julgar desvio.

### 3. Fora do nível recomendado

O nível recomendado deriva da complexidade estimada da tarefa:

| Complexidade | Nível recomendado |
|---|---|
| `simple` | `light` |
| `medium` | `medium` |
| `complex` | `heavy` |

A tabela espelha `athena.recommend._MAX_WEIGHT_BY_COMPLEXITY`, mas é declarada
no próprio módulo por ser uma decisão desta política.

**Desvio em qualquer direção conta.** Sub-dimensionar arrisca alegação falsa
(modelo fraco declarando sucesso em tarefa difícil); super-dimensionar indica
que o plano da tarefa não foi seguido. A direção fica registrada em
`details[...]["direction"]` como `below_recommended` ou `above_recommended`.

Níveis desconhecidos (modelo não declarado no passo do combo) não disparam.

### 4. Fallback fora da cadeia original

A referência é a cadeia **declarada no combo**, capturada antes de qualquer
override. Isso cobre dois casos:

- fallback que executou um agente ausente da cadeia declarada;
- override de cadeia por `orchestrator_continuation`, que injeta providers que
  o plano original não previa.

Em ambos, executou-se algo que o plano não autorizava previamente.

## Contexto insuficiente nunca ativa

A política só dispara com **evidência positiva**. Campos ausentes (`None` ou
vazios) não ativam nenhuma regra. Isso preserva o comportamento de chamadas
legadas que não fornecem contexto completo — elas continuam retornando
`requires_reinforced_verification: False`, sem ruído.

## Formato da decisão

```json
{
  "requires_reinforced_verification": true,
  "reasons": ["local_model_used", "fallback_outside_original_chain"],
  "details": {
    "local_model_used": {
      "provider_id": "ollama",
      "model": "llama3",
      "service_profile": "local_model"
    },
    "fallback_outside_original_chain": {
      "used_provider_id": "ollama",
      "original_chain": ["claude", "codex"],
      "is_fallback": true
    }
  }
}
```

Disponível em `RunResult.reinforced_verification` e no `to_dict()` do
resultado.

## Evento no Flight Recorder

Quando — e somente quando — a verificação reforçada é exigida, o router emite
`reinforced_verification_required` via
[Flight Recorder](flight-recorder.md):

| Campo | Conteúdo |
|---|---|
| `event_type` | `reinforced_verification_required` |
| `requires_reinforced_verification` | sempre `true` (só emite quando exigido) |
| `reasons` | lista de códigos de razão |
| `details` | contexto por razão |
| `combo_id` / `provider_id` | combo e agente que produziram o resultado |
| `attempted_chain` | providers efetivamente tentados |
| `original_chain` | cadeia declarada no combo |

Resultados que não exigem reforço **não** geram evento — o log não vira ruído.
Falhas de escrita do Flight Recorder são não-fatais e nunca interrompem o
combo.

## Ponto de avaliação

A decisão é avaliada no **caminho de sucesso** do `run_combo`, depois da
verificação (quando `verify=True`) e antes do retorno. É o ponto em que existe
um resultado que alguém vai confiar — que é justamente onde o sinal importa.

Caminhos de falha (timeout, erro, escalada ao orquestrador) não recebem a
anotação: ali o resultado já não é tratado como confiável, e o combo tem
mecanismos próprios (failover, `FallbackBlocked`, escalada).

## Uso direto da função pura

```python
from athena.reinforced_verification import (
    ReinforcedVerificationContext,
    evaluate_reinforced_verification,
)

decision = evaluate_reinforced_verification(
    ReinforcedVerificationContext(
        used_provider_id="codex",
        used_model="gpt-5.5",
        used_weight="heavy",
        task_complexity="complex",
        standard_provider_ids=("claude",),
        original_chain=("claude", "codex"),
        is_fallback=True,
    )
)

decision.requires_reinforced_verification  # True
decision.reasons                           # ["complex_task_non_standard_agent"]
```

Função pura: sem I/O, sem estado global, sem efeitos colaterais.

## Ligações

- [Verificação](verificacao.md) — camadas determinística e advisory
- [Flight Recorder](flight-recorder.md) — registro forense por tentativa
- [Arquitetura](arquitetura.md)
