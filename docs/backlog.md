# Backlog

- Integração Athena × Moiras: o adapter e os testes cross-repo existiam na arquitetura monolítica (`legado/athena/moiras_adapter.py`) e não foram portados para o núcleo modular. O job de CI que os cobria foi removido em 2026-08-20 porque apontava para arquivos inexistentes. Reimplementar é trabalho novo, com fila e gate próprios. Rastro da integração anterior: repositório `JoaoPauloNA/moiras`, ref `v0.1.1`.
