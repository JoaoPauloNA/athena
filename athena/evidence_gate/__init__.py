"""Evidence Gate: validação determinística de envelopes antes de julgamento."""

from .engine import GateVerdict, evaluate_result

__all__ = ["GateVerdict", "evaluate_result"]
