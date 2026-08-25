"""Chronos: ciclo de correção governado (módulo mínimo).

Implementa exatamente as regras do conceito canônico:
- compara esperado x ocorrido por critério objetivo;
- circuit breaker de 3 falhas no mesmo ciclo;
- reabertura só se TODAS as condições do documento forem verdadeiras;
- escalada ao humano em risco alto/mudança de escopo/limite de tentativas.
Não executa, não escolhe modelo, não aprova risco.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_ATTEMPTS_PER_CYCLE = 3


@dataclass(frozen=True)
class CycleAttempt:
    attempt_number: int
    verdict: str            # PASS|FAIL|INCONCLUSIVE|ESCALATE (do Evidence Gate)
    in_scope: bool          # correção manteve-se no escopo autorizado?
    new_exit_criteria_written: bool


@dataclass
class ChronosCycle:
    cycle_id: str
    expected_criteria: tuple[str, ...]
    attempts: list[CycleAttempt] = field(default_factory=list)

    def record(self, attempt: CycleAttempt) -> dict[str, Any]:
        """Registrar tentativa e decidir o próximo passo do ciclo."""
        self.attempts.append(attempt)
        n = len(self.attempts)

        if attempt.verdict == "PASS":
            return {"action": "CLOSED", "attempts_used": n,
                    "reason": "CRITERIA_MET"}

        if attempt.verdict == "ESCALATE" or not attempt.in_scope:
            return {"action": "HUMAN_REVIEW", "attempts_used": n,
                    "reason": "ESCALATION_POLICY"}

        if n >= MAX_ATTEMPTS_PER_CYCLE:
            return {"action": "HUMAN_REVIEW", "attempts_used": n,
                    "reason": "CIRCUIT_BREAKER_3_FAILURES"}

        if not attempt.new_exit_criteria_written:
            return {"action": "HUMAN_REVIEW", "attempts_used": n,
                    "reason": "NO_NEW_EXIT_CRITERIA"}

        return {"action": "REOPEN_FOR_CORRECTION", "attempts_used": n,
                "attempt_budget_remaining": MAX_ATTEMPTS_PER_CYCLE - n}

    def summary(self) -> dict[str, Any]:
        last = self.attempts[-1] if self.attempts else None
        return {
            "cycle_id": self.cycle_id,
            "expected_criteria": list(self.expected_criteria),
            "attempts": len(self.attempts),
            "last_verdict": last.verdict if last else None,
            "budget_remaining": max(0, MAX_ATTEMPTS_PER_CYCLE - len(self.attempts)),
        }
