"""Testes do contrato de relatório de 10 tópicos."""
from athena.contract import apply_contract, check_report_format


def test_apply_contract_wraps_prompt():
    out = apply_contract("corrija o bug")
    assert "corrija o bug" in out
    assert "10 tópicos" in out or "tópicos numerados" in out
    assert "Status final: OK ou FALHA" in out


def test_check_report_format_valid():
    report = "\n".join(
        [
            "1. O que foi feito: x",
            "2. Arquivos alterados: a.py",
            "3. Arquivos analisados: b.py",
            "4. O que não foi alterado: resto",
            "5. Testes executados: pytest",
            "6. Resultado dos testes: ok",
            "7. Pendências: nenhuma",
            "8. Riscos: nenhum",
            "9. Status final: OK",
            "10. Próximo passo recomendado: commit",
        ]
    )
    assert check_report_format(report) is True


def test_check_report_format_rejects_missing_status():
    report = "\n".join(f"{i}. tópico {i}" for i in range(1, 11))
    assert check_report_format(report) is False


def test_check_report_format_rejects_empty():
    assert check_report_format("") is False
