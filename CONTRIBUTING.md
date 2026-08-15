# Contributing

🇧🇷 Português abaixo · 🇨🇳 中文见下文

## English

Thanks for your interest in contributing to Athena-MCP!

### Setup

```bash
git clone https://github.com/JoaoPauloNA/athena.git
cd athena
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Before opening a PR

1. `ruff check athena tests` — must pass with zero errors
2. Run the test suite against an isolated data dir, with CLI auto-discovery disabled (matches CI and `harness/p0_gate.py`):
   ```bash
   export ATHENA_DATA_DIR="$(mktemp -d)"
   export ATHENA_SKIP_AUTODISCOVERY=1
   pytest -q
   ```
   `ATHENA_SKIP_AUTODISCOVERY=1` skips scanning your real PATH for AI CLIs (avoids slow/flaky discovery in CI and dev shells); `ATHENA_DATA_DIR` must point at an existing writable directory so ratings/usage/verdicts caches don't touch your real `~/.athena/`.
3. Add tests for any new behavior (scanner heuristics, routing, execution lifecycle, verifier logic…)
4. Keep docs in sync — if you change behavior, update `docs/en` and the PT-BR mirror in `docs/pt-BR`. The `docs/zh-CN` tree is a community translation and is not required to stay in sync.

### Guidelines

- **Cross-platform is the goal, not yet a guarantee everywhere.** Any path, subprocess or filesystem logic should aim to work on macOS, Windows and Linux. In practice, process-tree lifecycle cleanup (confirmed termination of a whole process group on timeout/cancel) is currently exercised by the test suite on **POSIX (macOS/Linux) only**; Windows support for that specific guarantee is `NOT_GUARANTEED` (see `harness/p0_gate.py` and [docs/en/architecture.md](docs/en/architecture.md#platform-support)). If you touch `bridge.py`, `execution.py` or process signaling, call out explicitly which platforms you validated.
- **The orchestrator's context is sacred:** executors return lean reports, never code dumps
- **Cheap before expensive:** prefer free/local models for auxiliary work (verification, classification)
- **Fail closed on lifecycle uncertainty:** fallback, workspace-lease transfer/release and verification should refuse to proceed rather than guess when a prior attempt's termination isn't positively confirmed — don't relax this without discussing it first
- Don't swallow exceptions silently — at least log them

---

## Português

Obrigado pelo interesse em contribuir com o Athena-MCP!

### Configuração

```bash
git clone https://github.com/JoaoPauloNA/athena.git
cd athena
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Antes de abrir um PR

1. `ruff check athena tests` — zero erros
2. Rode a suíte de testes com um diretório de dados isolado e auto-descoberta de CLIs desligada (igual ao CI e ao `harness/p0_gate.py`):
   ```bash
   export ATHENA_DATA_DIR="$(mktemp -d)"
   export ATHENA_SKIP_AUTODISCOVERY=1
   pytest -q
   ```
   `ATHENA_SKIP_AUTODISCOVERY=1` desliga a varredura do PATH real em busca de CLIs de IA (evita descoberta lenta/instável em CI e shells de dev); `ATHENA_DATA_DIR` precisa apontar para um diretório existente e gravável, para que os caches de notas/uso/vereditos não toquem no seu `~/.athena/` real.
3. Adicione testes para qualquer comportamento novo (heurísticas do scanner, roteamento, ciclo de vida de execução, lógica do verificador…)
4. Mantenha a documentação em sincronia — se mudar comportamento, atualize `docs/en` e o espelho PT-BR em `docs/pt-BR`. A árvore `docs/zh-CN` é uma tradução da comunidade e não precisa ficar sincronizada.

### Diretrizes

- **Cross-platform é a meta, ainda não uma garantia em tudo.** Qualquer lógica de path, subprocesso ou filesystem deve mirar funcionar em macOS, Windows e Linux. Na prática, a limpeza de ciclo de vida da árvore de processos (terminação confirmada de um grupo de processos inteiro em timeout/cancelamento) hoje é exercitada pela suíte de testes **só em POSIX (macOS/Linux)**; o suporte do Windows para essa garantia específica é `NOT_GUARANTEED` (ver `harness/p0_gate.py` e [docs/pt-BR/arquitetura.md](docs/pt-BR/arquitetura.md#suporte-por-plataforma)). Se você mexer em `bridge.py`, `execution.py` ou sinalização de processos, deixe explícito quais plataformas você validou.
- **O contexto do orquestrador é sagrado**
- **Barato antes de caro**
- **Fail closed na incerteza de ciclo de vida:** fallback, transferência/liberação de lease de workspace e verificação devem se recusar a prosseguir em vez de arriscar um palpite quando a terminação de uma tentativa anterior não está confirmada positivamente — não relaxe isso sem discutir antes
- Não engula exceções em silêncio — pelo menos registre um log

---

## 中文

感谢您对 Athena-MCP 的贡献兴趣!

### 提交 PR 前

1. `ruff check athena tests` —— 零错误
2. 在隔离的数据目录下运行测试套件,并关闭 CLI 自动发现(与 CI 和 `harness/p0_gate.py` 一致):
   ```bash
   export ATHENA_DATA_DIR="$(mktemp -d)"
   export ATHENA_SKIP_AUTODISCOVERY=1
   pytest -q
   ```
3. 为新行为添加测试(扫描器启发式、路由、执行生命周期、验证器逻辑……)
4. 保持文档同步(`docs/en` 以及 `docs/pt-BR` 镜像;`docs/zh-CN` 是社区翻译,不要求保持同步)

### 准则

- **跨平台是目标,但目前并非在所有方面都有保证。** 路径、子进程、文件系统逻辑应力求兼容 macOS / Windows / Linux。实际上,进程树生命周期清理(超时/取消时确认整个进程组终止)目前测试套件**仅在 POSIX(macOS/Linux)上验证**;Windows 上这一具体保证被标记为 `NOT_GUARANTEED`(见 `harness/p0_gate.py`)。
- **编排者的上下文是神圣的**
- **先便宜后昂贵**
- **在生命周期不确定时 fail closed:** 当上一次尝试的终止未被positively确认时,fallback、workspace lease 的转移/释放以及验证都应拒绝继续,而不是猜测
- 不要静默吞掉异常 —— 至少记录日志
