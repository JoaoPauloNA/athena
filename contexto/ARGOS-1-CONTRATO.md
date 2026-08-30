# ARGOS-1 — Browser QA real e seguro

Status: `CONCLUÍDA` em 2026-08-30; Ruff/formato verdes, `28 passed` e smokes Chromium reais.

## Objetivo

Substituir o piloto HTTP que aceitava screenshot como `SKIP` por um MVP
independente e executável em Chromium real, capaz de produzir evidência visual
verificável sem controlar o sistema operacional ou executar ações destrutivas.

## Escopo autorizado

- criar o projeto separado `Athena/Argos`, sem inicializar ou alterar Git;
- contrato tipado para escopo autorizado, cenário somente observacional e relatório;
- executar Brave ou Edge Chromium local em perfil temporário isolado;
- navegar somente para URL explicitamente permitida, capturar status, título,
  erros críticos de console e screenshot PNG full-page;
- validar PNG, tamanho mínimo, hash SHA-256 e containment do diretório de evidência;
- terminar navegador e perfil temporário em sucesso, falha, timeout ou cancelamento;
- bloquear ações irreversíveis; o MVP não clica, digita, envia nem autentica;
- incluir CLI, testes determinísticos e smoke real em página loopback temporária.

## Proibições

- não usar sessão/cookies/perfil real do usuário, navegador autenticado, OAuth ou segredos;
- não acessar abas existentes, Finder, Terminal por automação, rede não autorizada ou SO;
- não alterar Athena runtime, Aletheia, Themis, Aegis/build, Vault, beta ou dependências globais;
- não chamar modelo visual nem declarar julgamento sem evidência determinística;
- não tocar/importar/testar os dois arquivos protegidos do Athena;
- não fazer commit, push, tag, release, deploy ou publicação de extensão.

## Gate de saída

1. URL fora do escopo falha antes de iniciar navegador.
2. Chromium real abre página loopback, produz PNG válido >10 KB e relatório estruturado.
3. HTTP, título, console/page errors e evidência participam do veredito; sem `SKIP` verde.
4. Evidência fica contida em diretório dedicado, com hash e sem cookies persistentes.
5. Timeout/falha encerram árvore do navegador e removem perfil temporário.
6. Suíte, Ruff, validação estrutural e três smokes reais repetidos passam.

## Limite declarado

Este gate fecha o MVP técnico observacional. Extensão Chrome distribuída, interação
com abas existentes, ações de usuário e análise multimodal permanecem escopo futuro
porque exigem permissões do navegador e aprovação humana próprias.
