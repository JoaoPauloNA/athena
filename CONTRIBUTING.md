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
2. `pytest -q` — all tests must pass
3. Add tests for any new behavior (scanner heuristics, routing, verifier logic…)
4. Keep docs in sync — if you change behavior, update `docs/en` and, when possible, the PT-BR/ZH-CN mirrors

### Guidelines

- **Cross-platform first:** any path, subprocess or filesystem logic must work on macOS, Windows and Linux
- **The orchestrator's context is sacred:** executors return lean reports, never code dumps
- **Cheap before expensive:** prefer free/local models for auxiliary work (verification, classification)
- Don't swallow exceptions silently — at least log them

---

## Português

Obrigado pelo interesse em contribuir com o Athena-MCP!

### Antes de abrir um PR

1. `ruff check athena tests` — zero erros
2. `pytest -q` — todos os testes passando
3. Adicione testes para qualquer comportamento novo
4. Mantenha a documentação em sincronia (`docs/en` e, quando possível, os espelhos PT-BR/ZH-CN)

### Diretrizes

- **Cross-platform primeiro:** qualquer lógica de path, subprocesso ou filesystem deve funcionar em macOS, Windows e Linux
- **O contexto do orquestrador é sagrado**
- **Barato antes de caro**
- Não engula exceções em silêncio — pelo menos registre um log

---

## 中文

感谢您对 Athena-MCP 的贡献兴趣!

### 提交 PR 前

1. `ruff check athena tests` —— 零错误
2. `pytest -q` —— 全部测试通过
3. 为新行为添加测试
4. 保持文档同步(`docs/en` 以及 PT-BR/ZH-CN 镜像)

### 准则

- **跨平台优先:** 路径、子进程、文件系统逻辑必须兼容 macOS / Windows / Linux
- **编排者的上下文是神圣的**
- **先便宜后昂贵**
- 不要静默吞掉异常 —— 至少记录日志
