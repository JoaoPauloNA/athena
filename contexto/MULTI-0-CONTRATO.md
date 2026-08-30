# Contrato MULTI-0 — Harmonia, leases granulares e isolamento

Estado: **ACTIVE** em 2026-08-29, após aceitação independente de CLIO-0.

## Objetivo

Entregar o núcleo determinístico de equipe do Athena: Harmonia transforma
subtarefas já definidas/autorizadas em etapas sequenciais ou grupos paralelos,
aplica backpressure e orçamentos, e usa leases granulares ou worktrees isoladas
para impedir conflito de escrita. Na dúvida, executa sequencialmente ou abstém.

## Autoridades

- Zeus define funções, dependências e escopos propostos; Nike já escolheu cada
  trabalhador. MULTI-0 não escolhe agente, modelo, provider ou meio de acesso.
- Harmonia calcula ordem e concorrência; não autoriza risco nem executa shell.
- Aegis autoriza plano, recursos, paralelismo e write-set antes da reserva.
- Lease impõe exclusividade. Iris continua único adaptador de execução.
- Chronos recebe falhas/replanejamento; Evidence Gate valida resultado/diff;
  Clio registra metadados permitidos. Nenhum desses papéis é fundido.

## Contrato de equipe

Cada subtarefa possui ID, dependências, trabalhador já escolhido, escopo de
leitura/escrita, recursos, tipo de operação, orçamento, prazo e hash do Selo.
O schema é fechado, limitado e acíclico. O plano explicável registra grupos,
motivo do paralelismo/serialização e reason codes estáveis, sem prompt/comando.

Concorrência real = mínimo entre necessidade independente, limite do usuário,
limite do projeto, autorização Aegis, workers e tokens disponíveis de CPU, RAM,
GPU e provider. Ausência de dado necessário reduz para sequencial; capacidade
indisponível retorna `busy` com estimativa limitada, sem fila infinita.

## Conflitos e isolamento

- Leituras podem compartilhar. Escrita é exclusiva por arquivo canônico.
  Diretório cobre descendentes; links, `..`, caminhos relativos ambíguos e
  duplicatas por alias são resolvidos/rejeitados antes da reserva.
- Git, migração, formatador global, geração de código, lockfile/dependências e
  operação de repositório usam recurso global exclusivo.
- Escopo definido e pequeno usa leases granulares. Escopo amplo/incerto usa
  worktree isolada. O core decide a estratégia por regra fechada, não por IA.
- Reserva de todos os recursos de uma subtarefa é atômica e ordenada; nunca
  mantém subconjunto enquanto espera. Fila FIFO limitada evita starvation.
- Owner contém tarefa/subtarefa/tentativa. Heartbeat renova TTL limitado;
  expiração automática libera recursos. Liberação é idempotente pelo owner e
  outro owner nunca libera/renova.

## Execução e violação

Harmonia entrega somente grupos autorizados a um executor injetado. Falha de
uma subtarefa cancela apenas dependentes, não trabalhos independentes. Antes e
depois, um verificador compara arquivos realmente alterados com write-set. Saída
de escopo produz evidência sanitizada, interrompe/reprova apenas a subtarefa e
nunca promove seu resultado. Ampliação automática só ocorre se já estiver
dentro do Selo e sem conflito; caso contrário retorna ao Aegis.

## Worktree

O adapter de worktree recebe repositório e base já autorizados, cria diretório
temporário contido, usa nome opaco, rejeita worktree preexistente/externa e
remove apenas a worktree que ele próprio criou após preservação da evidência.
Testes reais usam exclusivamente repositório Git sintético temporário; nunca
criam branch/worktree/commit no Athena-MCP ou em repositório do usuário.

## Escopo permitido

Novo `athena/harmonia/`; evolução compatível de `athena/lease/`; integração
interna mínima com Cápsula/Iris/FLOW somente por contratos; testes focados,
adversariais e E2E sintético; benchmark. Pode ajustar fronteiras no
`pyproject.toml`. Biblioteca padrão apenas. Permanecem sete tools e schemas MCP.

## Proibições

Não alterar seleção Zeus/Nike, Selo Aegis, Evidence Gate/Chronos ou resultado
FLOW. Não criar tool/UI/Olimpo, rede, IA, provider, credencial, commit, push,
branch/worktree no projeto real, beta, release ou deploy. Não tocar Aletheia,
Themis, Argos ou `Aegis/build/`. Não ler, editar, importar, lintar ou testar
`athena/api_mode.py` e `tests/test_api_mode.py`.

## Aceitação

1. DAG fechada rejeita ciclo, dependência ausente, IDs duplicados e limites
   inválidos; o plano é determinístico e explicável.
2. Paralelismo usa o menor limite autorizado; tarefas conflitantes nunca
   coexistem; independentes realmente sobrepõem execução em teste controlado.
3. Reserva multi-recurso é atômica/FIFO, sem deadlock; read compartilhado,
   write/global exclusivo, owner/heartbeat/TTL/expiração comprovados.
4. Backpressure limita fila/workers/CPU/RAM/GPU/provider e retorna `busy`
   sanitizado sem aceitar trabalho infinito.
5. Estratégia escolhe lease granular para write-set fechado e worktree para
   escopo amplo/global incerto; worktree real só em Git sintético temporário.
6. Dois agentes podem alterar arquivos diferentes; mesmo arquivo/global é
   serializado. Saída de escopo reprova apenas infrator e preserva independente.
7. Falha cancela apenas dependentes; recursos são liberados em sucesso, falha,
   timeout e cancelamento. Nenhum subprocesso/worktree temporário fica órfão.
8. Benchmark 30/3 comprova planejamento/reserva sem I/O/IA no caminho rápido e
   registra teto de caracterização sem regredir bridge/Clio.
9. Diff-check, Ruff protegido-aware, fronteiras, focados, suíte integral sem os
   protegidos e regressões lentas, E2E real sintético e sete tools passam.

## Limites declarados

MULTI-0 entrega o motor interno e sua execução controlada por contratos. A
entrada pública de planos de equipe e a negociação com clientes pertencem a
MCP-2026; a configuração visual pertence a OLIMPO-0. Coordenação é local a um
processo; multi-host exige contrato futuro.

## Rollback

Remover a integração Harmonia e restaurar o lease de diretório anterior,
preservando o fluxo sequencial FLOW-1. Regressão reabre MULTI-0 e bloqueia
OLIMPO-0.
