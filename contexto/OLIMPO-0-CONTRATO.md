# Contrato OLIMPO-0 — controle local observável

## Objetivo

Entregar o primeiro painel local do Athena sem colocá-lo no caminho quente. O
Olimpo observa o estado real e permite preparar configurações já suportadas,
mas nunca escolhe agente, autoriza risco, executa tarefa ou altera runtime por
conta própria.

## Sequência interna

WIP=1 também vale dentro da fatia:

1. `O-0` — schemas fechados e adapter HTTP local somente em loopback.
2. `O-1` — frontend isolado em `olimpo/`, consumindo apenas O-0.
3. `O-2` — validação integrada, visual e de desempenho.

## Superfície mínima

- Saúde e versão do Athena, distinguindo implementado, indisponível e planejado.
- Tarefas e execuções por projeções sanitizadas já existentes.
- Estado técnico do Clio e seleção dos quatro níveis por proposta validada.
- Inventário somente leitura das funções/providers/modelos já presentes no
  snapshot/registry; ausência aparece como indisponível, nunca é inventada.
- Configuração de projeto já suportada pelo snapshot, sempre por
  `validar -> mostrar diff sanitizado -> aplicar atomicamente`.

## Autoridade e segurança

- Bind padrão exclusivo em `127.0.0.1`; nenhuma exposição de rede, OAuth,
  telemetria ou serviço pago.
- Schema fechado, limites de bytes/itens, `Content-Type` estrito, proteção CSRF
  local e origem permitida explícita. Respostas nunca contêm prompt, comando,
  ambiente, segredo, token, caminho sensível ou conteúdo completo de log.
- Escrita só chama validadores e publicação atômica existentes. Falha fechado
  se não provar versão/hash atual; conflito não sobrescreve.
- Sugestões de modelos são advisory e offline. Zeus/Nike/Aegis continuam as
  autoridades; Olimpo não escreve diretamente em registry, lease, fila, Selo
  ou execução.

## Caminho quente e desempenho

O servidor Olimpo é processo opt-in separado. Athena-MCP não importa frontend,
Node ou servidor Olimpo no startup normal. Com Olimpo desligado, overhead deve
ser zero mensurável; ligado, leitura possui limites e cache curto por versão.

## Escopo de arquivos

- Backend: novo `athena/olimpo/` e `tests/test_olimpo0.py`.
- Frontend: novo `olimpo/`, sem editar backend fora do adapter.
- Harness/documentação estritamente necessários ao gate.
- Dependências mínimas e confinadas ao frontend; backend usa biblioteca padrão
  sempre que suficiente.

## Proibições

Não criar tool MCP, instalar/desinstalar modelo, CLI ou proxy, executar shell,
resolver segredo, editar prompts especializados, abrir acesso remoto,
implementar aprovação humana autenticada ou decidir M-20 a M-30. Não alterar
Aletheia, Themis, Argos ou `Aegis/build/`. Não ler, editar, importar, lintar ou
testar `athena/api_mode.py` e `tests/test_api_mode.py`. Sem commit, push, beta,
release ou deploy.

## Aceitação

1. Schemas/endpoints locais são fechados, limitados, sanitizados e
   determinísticos; métodos, headers e origens inválidos falham fechado.
2. Configuração usa validação, preview e compare-and-swap por hash; conflito ou
   snapshot inválido preserva o estado anterior.
3. UI responsiva mostra loading, vazio, indisponível, conflito, erro e sucesso;
   nenhuma função planejada aparece como ativa.
4. Testes backend, lint/typecheck/build frontend e E2E local passam; validação
   visual cobre desktop e celular.
5. P0, fronteiras e suíte protegida-aware passam; continuam sete tools MCP.
6. Benchmark 30/3 comprova zero alteração no MCP desligado e caracteriza a
   latência p95 de leitura local sem inventar SLO.

## Rollback

Remover `athena/olimpo/` e `olimpo/` restaura o estado anterior sem migração de
dados. Regressão reabre OLIMPO-0 e bloqueia MCP-2026.
