# Security Policy

## Reporting a Vulnerability

Please **do not** open a public issue for security vulnerabilities. Use GitHub's private
[Security Advisories](https://github.com/JoaoPauloNA/athena/security/advisories/new) instead.

We aim to acknowledge reports within 72 hours.

## Scope notes

Athena-MCP is **alpha software, built for controlled local sharing** — your own machine, or people/machines you already trust. It has not been hardened for public or untrusted exposure. It **executes local CLIs with your user permissions** by design. Keep in mind:

- Prompts sent to providers may include file contents and project paths. Executed CLIs run as your user, with your filesystem access.
- `skip_permissions=true` passes each CLI's no-confirmation flag (e.g. `--dangerously-skip-permissions`) — this lets the CLI act without per-step approval. Use only in projects/workspaces you trust, never against untrusted prompts or repositories.
- The dashboard binds to localhost and has **no authentication**. Do not expose ports 7100/20129 to a network you don't trust — anyone who can reach them can trigger provider calls and read execution/usage data.
- Runtime data lives in `~/.athena/` (usage counters, categorized verdict history, model/ratings cache and user-defined provider configuration). Current verdict records do not persist task excerpts, project paths, prompts, reports or credentials. The directory is still local, unencrypted state and may identify configured providers/models; protect it like other local configuration. Athena does not transmit it by itself.
- `get_execution`/`list_executions` responses are sanitized (no prompts/reports/secrets), but they do reveal execution timing, state and provider/profile identifiers to anyone who can call the MCP server or reach the dashboard.
- **SSH executions (`ask_provider(..., ssh_host=...)`) never confirm that the remote process actually terminated.** A timeout or `cancel_execution` against a remote run leaves the attempt as `TERMINATION_UNCONFIRMED` rather than `CANCELLED` — the remote CLI may keep running on the target host after Athena stops waiting on it. Only point `ssh_host` at hosts and CLIs you trust and can independently check.
- **Process-tree cleanup on timeout/cancel is only exercised on POSIX (macOS/Linux) by the test suite.** On Windows, Athena controls the direct child process (`CREATE_NEW_PROCESS_GROUP`) but does not guarantee cleanup of the wider process tree — a timed-out/cancelled call can leave orphaned descendant processes running on Windows.
- **The workspace lease (`athena/workspace_lease.py`) is in-process only.** It prevents two attempts inside the *same* Athena process from touching the same working directory concurrently, but gives no protection if you run multiple Athena processes, worker pools, or hosts against the same shared filesystem — concurrent writes from separate processes are possible in that setup.
- Never paste secrets, credentials or tokens into prompts; the report contract explicitly forbids executors from including them in reports, but prevention beats detection.

---

## Política de Segurança

**Não** abra uma issue pública para vulnerabilidades — use os Security Advisories privados do GitHub.

O Athena-MCP é **software alpha, feito para compartilhamento local controlado** (sua própria máquina, ou pessoas/máquinas em que você já confia) — não passou por hardening para exposição pública. Executa CLIs locais com as permissões do seu usuário por design. `skip_permissions=true` remove a confirmação passo a passo da CLI — use só em projetos de confiança. O dashboard roda em localhost sem autenticação — não exponha as portas 7100/20129. Execuções via SSH nunca confirmam a terminação do processo remoto (um cancelamento pode deixar o processo remoto rodando). No Windows, a limpeza da árvore de processos em timeout/cancelamento **não é garantida** — só o processo direto é controlado. O lease de workspace é **intraprocesso**, sem proteção entre múltiplos processos/workers/hosts compartilhando o mesmo filesystem. Nunca cole segredos em prompts.

---

## 安全政策

请**勿**通过公开 issue 报告安全漏洞 —— 请使用 GitHub 私有的 Security Advisories。

Athena-MCP 是**面向可控本地分享场景的 alpha 阶段软件**(自己的机器,或你已经信任的人与机器),尚未针对公开或不受信任的暴露场景做加固。它按设计以你的用户权限执行本地 CLI。`skip_permissions=true` 会跳过 CLI 的逐步确认 —— 仅在受信任的项目中使用。面板绑定 localhost 且无认证 —— 请勿将 7100/20129 端口暴露到不受信任的网络。通过 SSH 执行的任务永远无法确认远程进程是否真正终止(取消操作可能让远程进程继续运行)。在 Windows 上,超时/取消时的进程树清理**不被保证**——只有直接子进程受控。工作区租约(workspace lease)**仅在单个进程内有效**,多个进程/worker/主机共享同一文件系统时没有任何保护。切勿在 prompt 中粘贴密钥或令牌。
