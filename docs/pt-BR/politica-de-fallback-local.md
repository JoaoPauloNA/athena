# Política de execução econômica local

Data: 2026-08-16  
Escopo: desenvolvimento privado do Athena e validações do Aletheia. Esta política
não muda nem é publicada junto da vertente pública do Athena.

## Ordem obrigatória

Cada tarefa começa pelo Athena e cada tentativa recebe um registro de modelo,
comando/provider, início/fim, saída, evidência e veredito. Encerrar um processo
não é prova de que a tarefa foi concluída.

1. **Athena → QwenProxy**: provider `qwen`, modelo explícito
   `qwenproxy-3.8-max` (ou `qwenproxy-3.8-max-thinking` somente quando houver
   ambiguidade). É a primeira opção econômica.
2. **Athena → DeepsProxy**: usar somente depois de existir um adaptador Athena
   OpenAI-compatible que aponte a `127.0.0.1:3103`. Hoje esse adapter **não
   existe**. `OpenCode → DeepsProxy` continua não aceito por incompatibilidade
   de streaming/contexto; não fingir que ele substitui essa etapa.
3. **Diagnóstico da falha**: Cursor via Athena (`agent`, `composer-2.5`) é a
   primeira revisão; Claude via Athena é alternativa quando estiver
   autenticado. Ambos devem receber o erro, logs sanitizados e escopo somente
   leitura antes de qualquer correção.
4. **Nova tentativa com LLM local**: Athena → `ollama` usando o modelo local
   configurado (atualmente `qwen3:8b`, se o serviço e o modelo estiverem
   disponíveis). Essa etapa é gratuita/local, mas não é evidência suficiente
   sem testes determinísticos.
5. **Executor de contingência**: Athena → Cursor ou Claude, com `--trust`
   somente quando o workspace já foi explicitamente confiado. `--yolo` não é
   permitido por esta política.
6. **Falha do próprio Athena**: chamar a CLI escolhida diretamente, com o
   mesmo modelo e prompt mínimo, e registrar a diferença entre Athena e CLI.
   Isso diagnostica o orquestrador sem atribuir sua falha ao modelo.

## Regras de escalonamento

- Saída vazia, relatório contratual ausente, timeout, `401`, erro de sessão ou
  erro de transporte são **falhas**, não sucesso parcial.
- Não repetir o mesmo prompt automaticamente em serviço autenticado.
- Cursor/Claude corrigem apenas após o diagnóstico registrar causa, arquivos e
  critério de aceitação.
- Toda correção precisa de teste local e, quando houver provider, um smoke de
  chat com sentinela curta.

## Estado inicial conhecido

| Caminho | Estado | Próxima evidência necessária |
| --- | --- | --- |
| Athena → QwenProxy | Transporte já funcionou; houve regressão de saída vazia em revisão | Tratar saída/relatório vazio como `UNCONFIRMED`. |
| Athena → DeepsProxy | Bloqueado por ausência de adapter Athena | Criar e testar adapter OpenAI-compatible local. |
| Athena → Ollama | Binário presente; configuração indica `qwen3:8b` | Confirmar serviço, modelo instalado e resposta curta. |
| Athena → Cursor | Smoke aprovado com `--trust`, sem escrita | Usar como diagnóstico/correção controlada. |
| Athena → Claude | CLI instalada; autenticação ainda deve ser comprovada | Smoke sem escrita depois do login válido. |

## Registro por tentativa

```text
data/hora | tarefa/seed | caminho | modelo | exit/HTTP | duração | saída válida?
evidência | veredito | próximo passo
```

O registro de uma campanha do Aletheia deve incluir também seed, alegação do
agente, evidência observada, classificação `claimed vs verified` e artefatos
reproduzíveis.
