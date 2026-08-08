# Verificação — claimed vs verified

> Por que o Athena tem um verificador, o que ele faz hoje e o caminho para
> verificação totalmente determinística.

## Motivação

CLIs de agentes frequentemente relatam "pronto, testes passando" quando não
foi isso que aconteceu. Evidências de avaliação de 2026 são preocupantes:
uma parcela grande dos "passes" em benchmarks amplamente citados
correspondia a tarefas nunca resolvidas de fato, e a literatura acadêmica já
batizou o fenômeno (*"Confident and Wrong"*, *"Silent Semantic Failures"*).
Harnesses construídos pelos próprios fabricantes de agentes têm incentivo
estrutural para marcar sucesso — uma camada neutra de verificação não tem
esse conflito de interesse.

O verificador do Athena existe para medir, localmente e por provider, a
distância entre **claimed** (o que o agente declara ter feito) e
**verified** (o que pode ser confirmado de fato).

## Duas camadas, regras diferentes

| Camada | Como funciona | Papel |
|---|---|---|
| **Verificador advisory** (implementado) | Um modelo barato (tier gratuito primeiro, ex. opencode free) lê o relatório do executor + evidências do projeto e emite verdadeiro/falso. Anti-conluio: o verificador nunca é o mesmo provider do executor. FALSO → volta para correção; FALSO 2× → escala ao orquestrador/humano. | Triagem. Nunca bloqueia sozinho, nunca gera número público. |
| **Verificador determinístico** (roadmap) | Re-executa exatamente o que o relatório alega (suíte de testes, lint), compara exit codes reais e o `git diff` real com o escopo declarado. **Nenhum modelo na cadeia** — IA julgando IA destruiria a credibilidade do número. | A camada que produz métricas confiáveis de claimed vs verified. |

A camada advisory continua útil mesmo depois dos checks determinísticos:
muitas tarefas não têm oráculo automatizável (prosa, configuração,
exploração), e a triagem barata evita gastar quota de modelos pagos com
verificação.

## Roadmap

- [ ] Persistir vereditos por CLI → ranking pessoal de confiabilidade (taxa
      local de claimed vs verified).
- [ ] Modo determinístico: executar o que o relatório alega e comparar exit
      codes; `git diff` real vs escopo de arquivos declarado.
- [ ] Suporte a `verify=true` em `run_combo` e `deliberate`.
- [ ] Prazo mais longo: suítes de tarefas com oráculo escondido (testes que
      o agente nunca vê ao declarar sucesso), permitindo uma taxa pública e
      comparável de sucesso falso por CLI.

---

*English: see `docs/en/verification.md`.*
