from __future__ import annotations

import re

# Contrato de execução aplicado a TODO prompt enviado a qualquer CLI (agent, claude, agy, codex).
# Objetivo: o CLI executor carrega o peso da tarefa (ler contexto, decidir, agir, testar) e devolve
# só um relatório enxuto — o orquestrador nunca recebe dump de código/logs de volta no seu contexto.

_CONTRACT_HEADER = (
    "Você está executando uma tarefa delegada por um orquestrador técnico. "
    "Regras obrigatórias:\n"
    "- Atue somente dentro do escopo pedido abaixo. Não altere arquivos, módulos ou "
    "comportamento fora desse escopo.\n"
    "- Leia o contexto relevante do projeto (diretório de trabalho, arquivos citados) antes de agir.\n"
    "- Não execute ações destrutivas, commit, push, deploy ou migração de dados sem "
    "autorização explícita contida no prompt abaixo.\n"
)

_CONTRACT_FOOTER = (
    "\n\nAo terminar, responda EXCLUSIVAMENTE com um relatório de exatamente 10 tópicos numerados, "
    "nesta ordem:\n"
    "1. O que foi feito\n"
    "2. Arquivos alterados\n"
    "3. Arquivos analisados\n"
    "4. O que não foi alterado\n"
    "5. Testes executados\n"
    "6. Resultado dos testes\n"
    "7. Pendências\n"
    "8. Riscos\n"
    "9. Status final: OK ou FALHA\n"
    "10. Próximo passo recomendado\n\n"
    "Não inclua dump de código, logs completos, segredos, credenciais, URLs sensíveis ou "
    "dados pessoais no relatório."
)

_REPORT_NUMBER_RE = re.compile(r"(?m)^\s*(\d{1,2})[.)]\s+\S")
_STATUS_LINE_RE = re.compile(r"(?i)status\s*final.*\b(ok|falha)\b")


def apply_contract(prompt: str) -> str:
    """Envolve o prompt (já com papel aplicado, se houver) com o contrato de execução."""
    return f"{_CONTRACT_HEADER}\nTarefa:\n{prompt}{_CONTRACT_FOOTER}"


def check_report_format(output: str) -> bool:
    """Checagem barata (regex) de que a saída segue o relatório de 10 tópicos + status final.

    Não tenta extrair nem corrigir nada — só sinaliza para o orquestrador decidir se precisa
    reforçar o formato no próximo prompt.
    """
    if not output:
        return False
    numbers = {int(match.group(1)) for match in _REPORT_NUMBER_RE.finditer(output)}
    has_report_numbers = len(numbers & set(range(1, 11))) >= 8
    has_status_line = bool(_STATUS_LINE_RE.search(output))
    return has_report_numbers and has_status_line
