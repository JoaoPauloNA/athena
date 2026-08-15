# Verificação — claimed vs verified

> Por que o Athena tem um verificador, o que ele faz hoje e o caminho para
> verificação totalmente determinística.

## Motivação

Um relatório do executor e o estado observável do projeto podem divergir: um
comando pode não ter rodado, um arquivo citado pode não existir ou uma falha
pode ter sido omitida. O Athena trata o relatório como alegação a inspecionar,
não como ground truth. Este documento não afirma prevalência desse desvio fora
das evidências locais do próprio Athena.

O verificador do Athena existe para medir, localmente e por provider, a
distância entre **claimed** (o que o agente declara ter feito) e
**verified** (o que pode ser confirmado de fato) — para as alegações
específicas que o relatório faz, não como prova geral de que o trabalho
está correto.

## Duas camadas, regras diferentes

| Camada | Como funciona | Papel |
|---|---|---|
| **Verificador determinístico** (`athena/dverify.py`, implementado) | Nenhum modelo na cadeia. Re-executa exatamente o que o relatório alega ter rodado — uma whitelist fixa de comandos de teste/lint (`pytest`, `ruff`, `npm test`, …), tokenizados via `shlex` (nunca shell), limitado a 3 comandos com timeout por comando — e compara os exit codes reais; separadamente checa se arquivos que o relatório alega ter criado/editado realmente existem. Não re-roda um comando se o relatório já admite a falha perto dele. | A camada que produz o sinal mais confiável, mas só para comandos da whitelist e arquivos citados. Fica em silêncio sobre qualquer outra coisa — sem veredito, não é um "passou". Tem prioridade sobre a advisory quando é conclusiva. Controlada por `ATHENA_VERIFY_MODE=auto\|deterministic\|advisory`. |
| **Verificador advisory** (`athena/verifier.py`, implementado) | Um modelo barato (tier gratuito primeiro, ex. opencode free) lê o relatório do executor + evidências objetivas do projeto (`git status`, `git diff --stat`, existência de arquivos citados) e emite verdadeiro/falso. Anti-conluio: o verificador nunca é o mesmo provider do executor. FALSO pode acionar uma correção ou fallback condicional; FALSO repetido escala. | Triagem para o que a camada determinística não consegue decidir (sem oráculo automatizável: prosa, configuração, exploração). Um modelo julgando outro: o veredito pode conduzir o workflow configurado, mas não é prova nem métrica objetiva de correção. |

## Escopo e limites

- As duas camadas só julgam o que o próprio relatório torna checável: comandos citados e arquivos citados. Uma alegação fora dessas duas categorias não é verificada nem para um lado nem para o outro.
- "Verdadeiro" na camada determinística significa "os comandos re-executados saíram com exit 0 e os arquivos citados existem" — não "a mudança está correta" ou "nada mais quebrou".
- A camada advisory é triagem baseada em modelo, não um oráculo; trate um FALSO dela como sinal forte para olhar com mais cuidado, e um VERDADEIRO como "nada contraditório encontrado", não como garantia.
- `run_combo(verify=true)` e `ask_provider(verify=true)` usam esse pipeline. **O `deliberate` não roda nenhuma verificação** — ver o roadmap abaixo.
- Uma fase de verificação que não consegue confirmar a própria terminação de subprocesso reporta `TERMINATION_UNCONFIRMED` em vez de arriscar um veredito, e isso bloqueia fallback/liberação de lease do mesmo jeito que uma tentativa do executor sem confirmação (ver [Arquitetura](arquitetura.md#ciclo-de-vida-da-execução)).

## Roadmap

- [x] Persistir vereditos por CLI → ranking pessoal de confiabilidade (taxa
      local de claimed vs verified), via `list_reliability` e o card
      Confiabilidade do dashboard.
- [x] Alimentar as notas de confiabilidade de volta no `recommend`
      (roteamento ponderado por confiança, peso de 30%, avisos para
      providers com ≥50% de relatórios verified-false).
- [x] Suporte a `verify=true` em `run_combo` (relatório FALSO aciona
      failover).
- [ ] Suporte a `verify=true` em `deliberate`.
- [ ] Prazo mais longo: suítes de tarefas com oráculo escondido (testes que
      o agente nunca vê ao declarar sucesso), permitindo uma taxa pública e
      comparável de sucesso falso por CLI.

---

*English: see `docs/en/verification.md`.*
