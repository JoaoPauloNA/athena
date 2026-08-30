# INT-ALE-0 — integração real Aletheia ↔ Athena

Status: `CONCLUÍDA` em 2026-08-30 após correção mínima, reparo P0 (duplicate in-flight id → `-32602`) e gates verdes.

## Objetivo

Fechar a ponte pública real entre Aletheia e o Athena instalado, eliminando a
incompatibilidade de forma e o descarte silencioso de modelo/timeout. Aletheia
continua repositório separado e não ganha autoridade sobre roteamento, permissões
ou configuração do Athena.

## Escopo autorizado

- corrigir `Aletheia/athena_compat.py` e testes próprios necessários;
- ajustar contratos públicos mínimos do Athena (`execution_modes`/resultado do
  bridge) somente quando indispensável para modelo, timeout e resultado tipado;
- fazer o provider/modelo chegar como argumentos de processo seguros, sem shell;
- aplicar timeout como deadline real do Athena e expor estado terminal compatível;
- preservar stdout e stderr separados, erro sanitizado e resultado completo;
- executar testes unitários de ambos os repositórios, teste cross-repo sem mock
  de forma e episódio real controlado com CLI sintética local.

## Proibições

- Não alterar Themis ou Argos nesta fatia; serão WIPs posteriores.
- Não alterar Aegis/build, beta, Vault, credenciais, remotos ou dependências.
- Não ler, editar, importar, lintar ou testar `athena/api_mode.py` e
  `tests/test_api_mode.py`; verificar apenas os hashes conhecidos.
- Não escolher provider/modelo dentro do Aletheia quando o Athena abstiver.
- Não usar `shell=True`, comando textual concatenado, segredo ou rede externa.
- Não fazer commit, push, tag, release, promoção de beta ou deploy.

## Gate de saída

1. `ask_provider` retorna contrato estruturado real consumível por `run_episode`.
2. Modelo solicitado chega à CLI como argumento separado e validado; ausência
   usa configuração declarada sem inventar modelo.
3. Timeout chega ao lifecycle e produz estado `timed_out` comprovado.
4. Teste cross-repo usa o pacote Athena real e uma CLI sintética, sem mock da
   forma do resultado; stdout/stderr/erro são verificados.
5. Suítes Aletheia e Athena protegida-aware, Ruff, P0 e hashes passam.
6. Nenhum processo/arquivo temporário fica órfão; contexto registra limites.
