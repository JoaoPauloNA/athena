# Contrato CLIO-0 — observabilidade não bloqueante

Estado: **COMPLETED** em 2026-08-29, após aceitação gerencial independente.

## Fechamento verificado

- Quatro níveis, precedência anti-elevação, privacidade fail-closed, fila e
  gerações de writer limitadas, retenção e shutdown com prazo único foram
  cobertos por 64 testes focados e revisão adversarial em três correções.
- Integração FLOW/MCP passou em 140 testes; smoke JSON-RPC real comprovou
  persistência técnica e nível `none` sem storage Clio.
- Gate final: Ruff, diff-check, nove fronteiras e P0 passaram; suíte
  protegida-aware `591 passed, 3 deselected`; sete tools preservadas.
- Benchmark 30/3: enqueue p95 `0.004 ms` e `none` p95 `0.000208 ms`, ambos
  aprovados. Hashes protegidos inalterados; nenhum commit, push ou beta.

## Objetivo

Entregar o núcleo determinístico do Clio com quatro níveis configuráveis,
eventos sanitizados, fila limitada e persistência fora do caminho quente. A
observabilidade nunca pode decidir, autorizar, executar, atrasar sem limite nem
derrubar uma tarefa.

## Níveis

- `complete`: pedido, decisões, respostas e resultados somente quando houver
  autorização direta comprovada, retenção de 7 dias e armazenamento protegido.
  Sem protetor seguro injetado, falha fechado e não persiste conteúdo.
- `partial`: resumo limitado do pedido, restrições, decisões e resultado, com
  retenção de 30 dias e remoção determinística de conteúdo sensível.
- `technical`: padrão; IDs opacos, horários, estados, provider/modelo, meio de
  acesso, duração, custo monetário informado, custo temporal, fila, timeout,
  repetição e códigos de erro; nunca conteúdo da tarefa. Retenção de 90 dias.
- `none`: desvia antes de construir ou serializar eventos comuns e não faz I/O
  Clio. Estado operacional e auditoria obrigatória de segurança são contratos
  separados e não podem carregar conteúdo da tarefa.

O cliente MCP pode sugerir um nível, mas não pode elevá-lo. Precedência:
segurança/Aegis -> política global -> projeto -> ordem direta comprovada do
usuário -> sugestão MCP. CLIO-0 implementa a política/injeção interna; a tela e
a edição pelo usuário pertencem a OLIMPO-0.

## Caminho quente e falhas

- Com nível ativo, o produtor valida um evento já estruturado, faz
  `put_nowait` em fila de capacidade fixa e retorna. Não abre banco, não espera
  writer, não chama IA/rede e não executa descoberta.
- Fila cheia, writer falho ou evento inválido incrementa contador sanitizado e
  nunca altera o resultado da tarefa. Não há fila infinita nem retry infinito.
- Um writer em background grava lotes em SQLite local separado, com permissões
  restritas, retenção por nível e shutdown/flush limitados. Falha de persistência
  permanece observável por contadores, sem exceção no caminho de execução.
- O enqueue ativo deve ter p95 <= 1 ms em benchmark 30/3. O modo `none` deve
  provar zero criação de diretório/arquivo e overhead praticamente nulo.

## Evento v1 e privacidade

O schema é fechado e versionado. Campos técnicos são tipados, limitados e
allowlisted. Prompts, comandos, cwd, ambiente, stdout/stderr, respostas, tokens,
chaves, credenciais e URLs com segredo são proibidos. Campos textuais de
`partial` passam por limite e redator determinístico antes da fila. `complete`
aceita conteúdo somente como envelope protegido opaco produzido por um
protetor injetado; plaintext nunca entra no evento persistível.

Mudança de nível gera evento técnico sem conteúdo. O ledger é append-only no
contrato público; retenção e exclusão só removem registros vencidos ou atendem
ação administrativa futura do Olimpo, nunca reescrevem o histórico.

## Integração permitida

Novo pacote `athena/clio/`; injeção opcional na composição; emissão mínima nos
marcos de FLOW-1 e execução, sem alterar seus vereditos. Reusar IDs e fatos já
calculados; não duplicar Moiras. Moiras mede para aprendizagem; Clio registra o
histórico permitido. Permanecem exatamente sete tools MCP e os payloads públicos
não ganham campos Clio nesta fatia.

## Escopo permitido

`athena/clio/`, plumbing mínimo em `mcp_runtime`, `mcp_server` e/ou FLOW,
fronteiras de importação, testes Clio, benchmark e documentação operacional
local. Pode usar apenas biblioteca padrão e o state dir já governado. Não
alterar schemas MCP nem dependências.

## Proibições

Não implementar UI/Olimpo, multiagente/Harmonia, nova tool, IA, rede, exportação
remota, telemetria externa, keychain próprio, aprovação humana autenticada ou
segredo persistente. Não alterar Aletheia, Themis ou Argos. Não ler, editar,
testar, importar ou lintar `athena/api_mode.py` e `tests/test_api_mode.py`. Não
tocar `Aegis/build/`. Sem commit, push, beta, release ou deploy.

## Aceitação

1. Quatro níveis e precedência têm contrato fechado; elevação por sugestão é
   rejeitada e `complete` sem protetor seguro falha fechado.
2. `none` não constrói evento comum, não inicia writer e não cria storage.
3. Níveis ativos usam fila limitada e `put_nowait`; saturação/falha nunca bloqueia
   nem muda execução e possui contadores sanitizados.
4. Writer persiste lotes, reinício preserva eventos, retenção remove vencidos e
   permissões locais são restritas.
5. Allowlist, limites e testes adversariais impedem segredos e campos proibidos.
6. Integração real registra ao menos início e término do FLOW sem alterar
   Evidence Gate, Chronos, resultado MCP ou as sete tools.
7. Benchmark 30/3 comprova enqueue p95 <= 1 ms e mede `none` separadamente.
8. Diff-check, Ruff protegido-aware, fronteiras, testes focados, suíte integral
   excluindo os protegidos e smoke JSON-RPC real passam.

## Limites declarados

CLIO-0 não oferece UI nem armazenamento de plaintext completo. O envelope
protegido depende de um protetor aprovado e injetado; até lá, `complete` é
indisponível de forma segura. A auditoria mínima obrigatória de segurança terá
contrato próprio com Aegis/Olimpo e não é simulada por logs comuns.

## Rollback

Remover apenas a injeção e o pacote Clio, preservando FLOW-1 e módulos anteriores.
Regressão reabre CLIO-0 e bloqueia MULTI-0.
