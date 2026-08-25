# Argos — contrato do piloto de uma página (v0.1)

## Escopo mínimo (uma página, uma rodada)

1. Abrir uma URL local autorizada em navegador controlado.
2. Capturar evidência visual: screenshot full-page + título + status HTTP.
3. Verificações determinísticas sobre a evidência:
   - HTTP 2xx;
   - `<title>` não vazio;
   - zero erro de console JavaScript crítico;
   - screenshot existe e é PNG válido (>10 KB).
4. Emitir relatório JSON estruturado com veredito PASS/FAIL por check.

## Fronteiras absolutas

- Somente URLs **loopback/localhost** autorizadas na linha de comando.
- Nunca publica, nunca modifica a página, nunca controla o SO.
- Evidência vai para diretório dedicado; nenhum cookie/sessão é persistido.

## Critério de saída do piloto

Rodada real em página servida localmente com os 4 checks passando e o
relatório JSON reproduzível (re-execução produz mesmo veredito).
