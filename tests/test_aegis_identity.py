import importlib

WRONG_AEGIS_MESSAGE = (
    "O aegis importável não é o Aegis interno do ecossistema Athena. "
    "Provavelmente foi instalado o pacote público aegis do PyPI em vez do interno. "
    "Corrija com: pip install -e ../Aegis"
)


def test_importable_aegis_is_internal_package() -> None:
    """Exigir os módulos e símbolos que identificam o Aegis interno."""
    try:
        contracts = importlib.import_module("aegis.contracts")
        importlib.import_module("aegis.classification")
        decision = importlib.import_module("aegis.decision")
    except (ImportError, ModuleNotFoundError) as exc:
        raise AssertionError(WRONG_AEGIS_MESSAGE) from exc

    required_symbols = {
        "ServiceProfile": contracts,
        "FailureCondition": contracts,
        "RiskContext": contracts,
        "RiskDecision": contracts,
        "evaluate": decision,
    }
    missing_symbols = [
        name for name, module in required_symbols.items() if not hasattr(module, name)
    ]

    assert not missing_symbols, (
        f"{WRONG_AEGIS_MESSAGE}. Símbolos ausentes: {', '.join(missing_symbols)}"
    )
