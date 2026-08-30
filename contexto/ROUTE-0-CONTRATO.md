# Contrato ROUTE-0 — soberania determinística Zeus/Nike

Estado: **ACTIVE** em 2026-08-28, após aceitação independente de CAP-0.

## Objetivo e decisão do CEO

No caminho MCP normal, o cliente pode descrever a tarefa e sugerir candidatos,
mas não escolhe o executor final. Zeus determina o especialista/persona e os
requisitos; Nike resolve o provider configurado e elegível; somente depois o
plano segue para Aegis/Cápsula/Iris. Nenhuma IA participa do caminho quente.

A decisão M-19 fica fechada nesta fatia: roteamento operacional é
determinístico. Modelos entre Luna Low e Terra Medium podem, futuramente e
fora do caminho quente, sugerir configuração ao Olimpo; nunca alteram a rota
ativa diretamente.

## Superfícies MCP

- `run_combo` é a superfície autônoma. Seus `attempts` são receitas candidatas
  advisory. A ordem enviada pelo cliente não concede prioridade. O runtime só
  pode executar receita cujo `provider` coincida com a decisão interna.
- `run_combo` recebe contexto de roteamento fechado: `task_type`,
  `primary_domain`, `risk_level`, `required_capabilities` e
  `explicit_agent_tag` opcional. Ausência, ambiguidade ou forma inválida
  produz abstenção terminal antes do bridge.
- `ask_provider` permanece a superfície de comando direto. `provider_id` é a
  escolha explícita declarada pelo host MCP, mas não contorna configuração,
  estado observado, Aegis, Cápsula ou Iris. A receita executada deve ter o
  mesmo provider pedido.
- Limite conhecido: o protocolo MCP atual não prova criptograficamente se a
  chamada `ask_provider` nasceu de um gesto humano. ROUTE-0 registra e limita
  essa fronteira; autenticação/aprovação humana pertence a fatia futura.
- A superfície continua com exatamente sete tools. Não criar nova tool.

## Fontes de autoridade

- Registro Zeus: versão válida carregada de persistência interna. Registro
  ausente, vazio, adulterado ou inválido causa abstenção; não há agente default.
- Providers/personas/funções: snapshot seguro CFG-SEC-0 apontado por
  `ATHENA_CONFIG_DIR`. Snapshot ausente ou inválido causa abstenção; não há
  bootstrap silencioso nem uso de provider inventado a partir do cliente.
- Estado observado sanitizado pode retirar elegibilidade, nunca habilitar.
- Dados do cliente descrevem a demanda e fornecem receitas; não publicam
  registro, provider, lifecycle, aprovação ou política.

## Algoritmo canônico

1. Validar chamada e contexto sem executar processo.
2. Carregar uma fotografia coerente de configuração e registro na composição
   de produção; falhar fechado se não existir.
3. Construir `TaskRequest` estrito.
4. Zeus produz somente elegibilidade de especialista/persona/runtime.
5. Nike resolve provider determinístico pela configuração e estado observado.
6. Para `run_combo`, filtrar receitas advisory pela decisão; zero correspondência
   abstém. Duplicidade conflitante para o mesmo provider abstém. A ordem do
   cliente não altera a decisão.
7. Para `ask_provider`, validar provider direto e exigir receita coincidente.
8. Encaminhar somente o plano interno resultante ao router e depois à fronteira
   CAP-0. Aegis continua sendo a única autorização de execução.

## Reason codes mínimos

Preservar códigos Zeus/Nike existentes e acrescentar apenas códigos estáveis e
sanitizados necessários à integração: contexto ausente/inválido, configuração
ou registro indisponível, sugestão divergente/ambígua e provider direto negado.
Nenhum erro ecoa prompt, comando, cwd, valores de ambiente ou conteúdo de
configuração.

## Compatibilidade e desempenho

- As sete tools e campos anteriores permanecem reconhecidos. `run_combo` sem
  contexto novo falha fechado com código estável; não executa pela regra antiga.
- `ask_provider` preserva o uso direto, agora vinculando `provider_id` à receita.
- Nenhuma LLM, rede, subprocesso de descoberta ou gravação entra na decisão.
- Cache é permitido somente para fotografia já validada e deve ser invalidado
  por identidade/hash. Falha de refresh preserva a última fotografia válida
  somente se essa política já estiver explicitamente configurada; caso
  contrário, abstém.
- Overhead incremental da integração ROUTE-0 deve ficar abaixo de 5 ms p95 em
  30 amostras após 3 warmups nesta máquina, medido separadamente do processo.

## Escopo permitido

`contexto/ROUTE-0-CONTRATO.md` e checkpoints; plumbing estritamente necessário
em `mcp_runtime`, `mcp_stdio`, `mcp_server` e `router`; `athena/zeus/` apenas
para consolidar a implementação canônica; adapter interno de roteamento e
testes focados. `config_loader` só pode mudar se uma lacuna demonstrada impedir
consumo somente leitura do snapshot já validado.

## Proibições

Não implementar FLOW-1, Clio, Harmonia, multiagente, Olimpo, aprovação humana,
treinamento, descoberta, instalação, nova tool, novo provider, rede ou modelo.
Não alterar Aletheia, Themis ou Argos. Não ler/editar/testar/importar os dois
arquivos protegidos. Não tocar `Aegis/build/`. Sem dependências, credenciais,
commit, push, beta, release ou deploy.

## Aceitação

1. Contrato ACTIVE precede código.
2. `run_combo` nunca executa provider escolhido apenas pela ordem/quantidade das
   sugestões do cliente.
3. Mesma tarefa + mesma fotografia gera a mesma decisão e plano.
4. Alterar ordem das receitas não altera provider selecionado.
5. Receita ausente, divergente ou duplicada conflituosa abstém antes do runner.
6. Registro/config ausente, inválido ou adulterado abstém antes do runner.
7. Tag explícita sugere especialista, mas não contorna lifecycle/capacidade.
8. `ask_provider` exige correspondência exata e continua sujeito aos gates.
9. Toda tentativa que chega ao bridge conserva CAP-0 válido.
10. Sete tools preservadas; reason codes sanitizados; nenhum segredo vaza.
11. Testes adversariais, caminho MCP real, diff-check, Ruff, boundaries, P0,
    suíte integral e benchmark passam; hashes protegidos permanecem.

## Rollback

Restaurar somente o plumbing ROUTE-0 e remover seus novos adapters/testes,
preservando CAP-0 e mudanças anteriores. Em falha, ROUTE-0 permanece ACTIVE e
FLOW-1 não é liberada.
