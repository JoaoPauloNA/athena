# Compartilhamento local e validação

🇺🇸 [EN](../en/local-sharing.md)

> Como validar um checkout do Athena-MCP antes de compartilhá-lo localmente — com pessoas ou máquinas em que você já confia — sem precisar de nenhuma CLI de IA instalada.

## Por que isso existe

O Athena-MCP é software alpha, feito para compartilhamento local controlado, não para exposição pública ou não confiável (ver o [README](../../README.pt-BR.md#limitações) na raiz). Antes de passar um checkout para outra pessoa, ou de confiar numa mudança você mesmo, rode a sequência reproduzível abaixo. Depois que as dependências de desenvolvimento estiverem disponíveis, os comandos de validação não precisam de rede nem de CLI de IA instalada — a lógica testada é exercitada pela suíte de testes, não por subprocessos reais de provider.

## Sequência

1. Configure um virtualenv e instale as dependências de dev (ver [CONTRIBUTING.md](../../CONTRIBUTING.md)).
2. Lint: `ruff check athena tests` — precisa sair com código 0.
3. Suíte de testes completa, com diretório de dados isolado e auto-descoberta desligada:
   ```bash
   export ATHENA_DATA_DIR="$(mktemp -d)"
   export ATHENA_SKIP_AUTODISCOVERY=1
   pytest -q
   ```
   Precisa sair com código 0. `ATHENA_DATA_DIR` precisa apontar para um diretório existente, gravável e de scratch — a suíte grava arquivos de cache/registro lá em vez do seu `~/.athena/` real.
4. Gate sintético offline:
   ```bash
   python harness/p0_gate.py
   ```
   Esta etapa gerencia seu próprio `ATHENA_DATA_DIR` isolado (um diretório temporário) e define `ATHENA_SKIP_AUTODISCOVERY=1` internamente — não precisa configurar env manualmente aqui. Ela re-executa um `ruff check` com escopo restrito mais os arquivos de teste de ciclo de vida de execução, router, lease de workspace, registro/cancel/EOF do MCP, construtor SSH, service_profile, privacidade/confiabilidade e testes unitários do adaptador Moiras opcional, cada um como um estágio separado, e grava um relatório JSON em `harness/results/p0-gate-<timestamp>.json` (ou no caminho passado via `--output`).
5. Ao revisar o contrato real opcional com Moiras, instale o extra e rode separadamente os testes entre repositórios:
   ```bash
   pip install -e '.[moiras]'
   pytest -q tests/test_moiras_adapter.py tests/test_moiras_adapter_integration.py
   ```
   Essa checagem adicional usa a dependência Moiras e, portanto, não pertence ao gate offline independente de Moiras. A CI executa um job de contrato equivalente a partir de checkouts separados do Athena e do Moiras.

## Critérios PASS / FAIL

- **PASS**: a etapa 2 sai com código 0, a etapa 3 sai com código 0, e o relatório da etapa 4 tem `overall_status: "passed"` — todo item do array `stages` tem `status: "passed"`.
- **FAIL**: qualquer uma das etapas acima falha. Antes de compartilhar um checkout, leia o `id` e o `exit_code` do estágio que falhou no relatório JSON (ou a saída bruta do `pytest`/`ruff`) em vez de assumir que não tem relação com sua mudança.
- O relatório JSON do gate é a fonte da verdade para **quantos testes rodaram** em um dado momento — esse número muda conforme a suíte cresce, então intencionalmente não é fixado neste documento nem no `CHANGELOG.md`.
- Um PASS significa que o código e sua própria suíte de testes são internamente consistentes na máquina que rodou o gate. Ele não exercita nenhuma CLI de IA real de ponta a ponta e não precisa de acesso à rede.

## Limitações e escopo

- Esta é uma sequência de validação **local, de uma única máquina**. Ela não diz nada sobre o comportamento em outro SO, outra versão do Python, ou com CLIs reais instaladas, além do que o bloco `support_classification` do relatório JSON captura para a máquina que rodou o gate.
- `support_classification` (`posix: LOCAL_CONTROLLED_ONLY`, `windows: NOT_GUARANTEED`, mais qual dos dois é `effective` na máquina que rodou o gate) reflete **cobertura da suíte de testes**, não uma sondagem de capacidade ao vivo: POSIX é `LOCAL_CONTROLLED_ONLY` porque os testes de ciclo de vida da árvore de processos rodam e são verificados só lá; Windows é `NOT_GUARANTEED` pelo mesmo motivo — a classificação é sobre o que é testado, não uma alegação de que o Windows é conhecido por falhar.
- Passar no gate é uma pré-condição para compartilhar localmente, não um substituto para os avisos em [Segurança e validação](../../README.pt-BR.md#segurança-e-validação) e [SECURITY.md](../../SECURITY.md). `skip_permissions`, o dashboard sem autenticação, a falta de confirmação de terminação remota via SSH e o lease de workspace intraprocesso continuam valendo independentemente do status do gate.
- O gate é sintético e offline por construção — nenhum subprocesso real de CLI é exercitado. Ele valida a *lógica* de ciclo de vida de execução, router, lease, registro, construtor SSH e verificador, não uma execução real de ponta a ponta contra Codex/Claude Code/Cursor/etc.
- O gate não define `ATHENA_MOIRAS_SHADOW`; o servidor MCP só ativa o sampler por opt-in explícito na inicialização. Os testes unitários usam um contrato compatível injetado, enquanto o teste de integração separado exige Moiras `0.1.x` / schema `1.0`. Ativado ou testado, o advisory continua somente observacional e nunca participa do caminho de controle PASS/FAIL acima.
- Em POSIX, o tratamento de escapes por `setsid()`/`setpgid()` é positivo e conservador: enxergar um descendente escapado bloqueia a confirmação da árvore, mas não enxergar não prova contenção universal. A classificação `LOCAL_CONTROLLED_ONLY` continua intencional.
