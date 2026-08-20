# Harnesses comparativos

Os comparativos permanentes entre o núcleo novo e o legado ficam fora do gate
rápido. Para executá-los a partir da raiz do repositório:

```sh
ATHENA_REGRESSION=1 python -m pytest -m regression
```

## Pré-requisitos

- macOS com o serviço local do Ollama em execução;
- CLI em `/usr/local/bin/ollama` e o binário da aplicação em
  `/Applications/Ollama.app/Contents/Resources/ollama`;
- modelo-base local, obtido com `ollama pull qwen3:8b`;
- ambiente de desenvolvimento instalado, por exemplo com
  `python -m pip install -e '.[dev]'`.

O modelo `athena-gate` é derivado de `qwen3:8b`. O harness
`compare_cores_deterministic.py` o cria ou recria automaticamente no começo de
cada execução, usando parâmetros determinísticos e ajustando `num_predict` na
calibração. Portanto, para recriá-lo basta garantir o modelo-base e executar:

```sh
ollama pull qwen3:8b
ATHENA_REGRESSION=1 python -m pytest -m regression
```

## Dependência local: Aegis

A dependência de runtime `aegis` é declarada por nome. Como os repositórios
Athena-MCP e Aegis vivem lado a lado, resolva-a no ambiente local com uma
instalação editável a partir da raiz do Athena-MCP:

```sh
.venv/bin/pip install -e ../Aegis
```

A suíte leva aproximadamente 15 minutos. Sem `ATHENA_REGRESSION=1`, ou quando
um binário Ollama exigido não existe, os testes são pulados com uma razão
explícita. O preparo para Windows está documentado no relatório produzido pelo
harness de investigação, mas a regressão ainda não foi testada no Windows.
