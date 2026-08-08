# Security Policy

## Reporting a Vulnerability

Please **do not** open a public issue for security vulnerabilities. Use GitHub's private
[Security Advisories](https://github.com/JoaoPauloNA/athena/security/advisories/new) instead.

We aim to acknowledge reports within 72 hours.

## Scope notes

Athena-MCP **executes local CLIs with your user permissions** by design. Keep in mind:

- Prompts sent to providers may include file contents and project paths. Executed CLIs run as your user.
- `skip_permissions=true` passes each CLI's no-confirmation flag (e.g. `--dangerously-skip-permissions`) — use only in trusted projects.
- The dashboard binds to localhost and has **no authentication**. Do not expose ports 7100/20129 to a network you don't trust.
- Runtime data lives in `~/.athena/` — it may contain project paths and report excerpts. It is never transmitted anywhere by Athena itself.
- Never paste secrets, credentials or tokens into prompts; the report contract explicitly forbids executors from including them in reports, but prevention beats detection.

---

## Política de Segurança

**Não** abra uma issue pública para vulnerabilidades — use os Security Advisories privados do GitHub.
O Athena executa CLIs locais com as permissões do seu usuário por design; o dashboard roda em localhost sem autenticação — não exponha as portas 7100/20129. Nunca cole segredos em prompts.

---

## 安全政策

请**勿**通过公开 issue 报告安全漏洞 —— 请使用 GitHub 私有的 Security Advisories。
Athena-MCP 按设计以你的用户权限执行本地 CLI;面板绑定 localhost 且无认证 —— 请勿将 7100/20129 端口暴露到不受信任的网络。切勿在 prompt 中粘贴密钥或令牌。
