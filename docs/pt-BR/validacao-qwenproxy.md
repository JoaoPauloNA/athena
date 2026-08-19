# Validação QwenProxy via Athena

## Falha observada

Chamada `ask_provider(provider=qwen, model=qwenproxy-3.8-max)` retornou **401** do Model Studio (Alibaba), enquanto o comando direto na shell funcionava:

```bash
qwen --safe-mode --model qwenproxy-3.8-max --prompt ... --output-format json
```

## Causa raiz

`resolve_model()` em `athena/models.py` substituía silenciosamente qualquer modelo explícito ausente do catálogo pelo *recommended default* do provider. O ID `qwenproxy-3.8-max` é válido na CLI do usuário, mas não aparece no catálogo Athena; o roteador trocava por `qwen3.7-plus` (externo), gerando **401** do Model Studio (Alibaba) e violando a intenção explícita do chamador.

## Causa complementar (montagem do comando)

O builder do provider `qwen` em `athena/providers.py` também montava:

```text
qwen -p <prompt> --model <model>
```

Sem `--safe-mode` e com ordem de argumentos diferente da invocação manual que roteia pelo QwenProxy.

## Correções aplicadas

1. **Integridade de modelo:** `resolve_model()` agora preserva modelos explícitos desconhecidos (texto normalizado), em vez de fazer fallback silencioso para o default do catálogo. `model=None` continua usando o recomendado; `model="auto"` continua retornando `None`.

2. **Montagem do comando Qwen:** o builder passa a montar, quando há modelo:

```text
qwen --safe-mode --model <model> -p <prompt>
```

Sem modelo explícito, mantém `qwen -p <prompt>`.

## Validação end-to-end

Executada em 2026-08-16 após as duas correções:

```text
Athena ask_provider → Qwen Code --safe-mode → qwenproxy-3.8-max → QwenProxy local
```

Resultado: saída `0`, estado de execução `COMPLETED`, sem timeout e com a
resposta-sentinela `ATHENA_QWEN_PROXY_OK`. O comando registrado pelo Athena
preservou o ID local explícito `qwenproxy-3.8-max`; nenhum fallback para Model
Studio ocorreu.

O smoke não usou ferramentas nem alterou arquivos. Ele valida transporte e
seleção de modelo; não mede qualidade do modelo, custo sob carga ou a campanha
do Aletheia.

## Limite observado: delegação com escrita

Uma delegação posterior, limitada a criar o esqueleto isolado de um proxy novo,
não produziu relatório nem arquivos antes do teto operacional. Nenhuma alteração
fora do escopo foi observada. O provider Qwen atual envia `--safe-mode`, mas não
encaminha uma autorização não interativa de escrita; portanto, ele está aceito
no Athena para triagem, planejamento, revisão e smoke controlado, **não** para
mutação autônoma de workspace nesta fase.

Quando uma tarefa exigir escrita, o Athena deve registrar a tentativa e escalar
para um executor explicitamente autorizado, mantendo a validação determinística
posterior. Não habilitar aprovação ampla de ferramentas apenas para contornar
esse limite.

## Regressão observada: execução sem relatório (2026-08-16)

Em uma revisão somente leitura do desenho do ZchatProxy, a chamada abaixo foi
encaminhada pelo Athena para o modelo local `qwenproxy-3.8-max-thinking`:

```text
Athena ask_provider(qwen, local_model) → Qwen Code --safe-mode → QwenProxy local
```

O processo externo encerrou sem texto capturado pelo invocador — inclusive sem o
relatório contratual exigido pelo Athena. Isso **não é** uma análise concluída e
não pode receber estado de sucesso apenas pelo término do subprocesso.

Também foi exposta uma inconsistência de configuração: `read_only`, o nome usado
para essa intenção, não existe entre os service profiles. Os perfis disponíveis
incluem `local_model`, `verification` e `text_generation`; a chamada foi então
reclassificada como `local_model`, sem autorização de escrita.

### Ação requerida

1. O verificador deve marcar saída vazia ou relatório ausente como
   `UNCONFIRMED`/falha de integridade, mesmo que o exit code seja zero.
2. O Athena deve expor um profile explícito `read_only` ou documentar a escolha
   canônica para revisões sem escrita.
3. O provider Qwen permanece aceito apenas quando houver conteúdo e contrato
   verificáveis. Para mutação ou para uma saída vazia, a política é registrar a
   falha e escalar a um executor autorizado; nunca repetir cegamente o prompt.

## Teste do fallback Cursor via Athena (2026-08-16)

Foi executado o mesmo tipo de smoke pelo provider `agent`, usando
`composer-2.5` e uma tarefa que proibia leitura, ferramentas e escrita.

| Tentativa | Resultado | Evidência |
| --- | --- | --- |
| Sem `skip_permissions` | Falhou antes do modelo | Cursor retornou `Workspace Trust Required`; o Athena não passava `--trust`. |
| Com `skip_permissions=True` | Aprovada | exit `0`, execução `COMPLETED`, sentinela `ATHENA_CURSOR_PROXY_OK` e relatório contratual retornados. |

O segundo teste usou `--trust` apenas para confirmar a confiança do workspace;
ele **não** usou `--yolo`, não concedeu aprovação irrestrita e não alterou
arquivos. Isso confirma o caminho de contingência:

```text
Athena → Cursor Agent (composer-2.5) → resposta contratual verificável
```

Para chamadas não interativas do Cursor em workspace já autorizado, Athena deve
expor claramente a decisão `skip_permissions`/`--trust` e registrá-la na
execução. A permissão de escrita continua uma decisão separada e explícita.
