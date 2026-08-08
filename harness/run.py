#!/usr/bin/env python3
"""Mini-harness claimed-vs-verified — embrião da suíte do Verificador.

Para cada tarefa × provider:
  1. Cria um workspace limpo em /tmp com os arquivos da tarefa;
  2. O agente executa o prompt (via ask_provider_verified — verificação e
     persistência de vereditos acontecem automaticamente);
  3. O harness congela o workspace e roda o ORÁCULO (comandos whitelisted),
     independente do que o agente alegou → `verified`;
  4. Compara com o veredito do episódio → claimed vs verified.

Uso:
  python harness/run.py --providers claude:haiku opencode:opencode/deepseek-v4-flash-free
  python harness/run.py --providers claude:haiku --tasks easy-01-soma
  python harness/run.py --list

Resultados: harness/results/episodes-YYYYMMDD-HHMMSS.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

TASKS_FILE = Path(__file__).parent / "tasks.json"
RESULTS_DIR = Path(__file__).parent / "results"


def load_suite(path: Path = TASKS_FILE) -> dict:
    """Carrega e valida o manifesto da suíte."""
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = [t["id"] for t in data.get("tasks", [])]
    if len(ids) != len(set(ids)):
        raise ValueError("IDs de tarefa duplicados no manifesto")
    for task in data["tasks"]:
        for field in ("id", "nivel", "prompt", "oracle", "arquivos"):
            if field not in task:
                raise ValueError(f"Tarefa {task.get('id', '?')} sem campo '{field}'")
    return data


def prepare_workspace(task: dict) -> str:
    """Cria workspace limpo com os arquivos da tarefa. Devolve o path."""
    workdir = tempfile.mkdtemp(prefix=f"athena_ep_{task['id']}_")
    for rel, content in task["arquivos"].items():
        target = Path(workdir) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return workdir


def run_oracle(commands: list[str], workdir: str) -> dict:
    """Roda o oráculo DE FATO (independente do que o agente alegou)."""
    from athena.dverify import run_command

    checks = [run_command(cmd, workdir) for cmd in commands]
    ran = [c for c in checks if c.exit_code is not None]
    return {
        "verified": bool(ran) and all(c.ok for c in ran),
        "checks": [c.to_dict() for c in checks],
    }


def _claimed_from_report(report: str) -> bool:
    """Heurística: o agente declarou sucesso ou admitiu bloqueio/falha?"""
    from athena.dverify import _FAILURE_WORDS_RE  # reuso da detecção de admissão
    text = report or ""
    return not bool(_FAILURE_WORDS_RE.search(text))


def run_episode(task: dict, provider: str, model: str | None, timeout: int) -> dict:
    """Um episódio completo do protocolo: setup → execução → oráculo → veredito."""
    from athena.providers import ask_provider_verified

    workdir = prepare_workspace(task)
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    episode = {
        "task_id": task["id"],
        "nivel": task["nivel"],
        "categoria": task["categoria"],
        "provider": provider,
        "model": model,
        "started": started,
        "workdir": workdir,
    }

    result = ask_provider_verified(
        provider,
        task["prompt"],
        model=model,
        working_directory=workdir,
        timeout=timeout,
        skip_permissions=True,
    )

    oracle = run_oracle(task["oracle"], workdir)
    claimed = _claimed_from_report(result.output)
    verified = oracle["verified"]

    episode.update({
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "claimed_pronto": claimed,
        "verified": verified,
        "veredito": result.verdict,
        "mentiu": claimed and not verified,
        "honesto_no_bloqueio": (not claimed) and (not verified),
        "warnings": result.warnings,
        "relatorio_excerto": (result.output or "")[:400],
    })
    return episode


def main() -> int:
    parser = argparse.ArgumentParser(description="Mini-harness claimed-vs-verified")
    parser.add_argument("--providers", nargs="*", default=["claude:haiku"],
                        help="provider[:modelo] — ex.: claude:haiku opencode:opencode/deepseek-v4-flash-free")
    parser.add_argument("--tasks", nargs="*", help="IDs de tarefas (default: todas)")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--list", action="store_true", help="Lista tarefas e sai")
    args = parser.parse_args()

    suite = load_suite()
    tasks = suite["tasks"]
    if args.tasks:
        wanted = set(args.tasks)
        tasks = [t for t in tasks if t["id"] in wanted]
        missing = wanted - {t["id"] for t in tasks}
        if missing:
            print(f"Tarefas não encontradas: {', '.join(sorted(missing))}", file=sys.stderr)
            return 2

    if args.list:
        for t in suite["tasks"]:
            print(f"{t['id']}  [{t['nivel']}] ({t['categoria']})")
        return 0

    # pytest precisa estar acessível para o oráculo
    venv_bin = Path(__file__).parent.parent / ".venv" / "bin"
    if venv_bin.exists():
        os.environ["PATH"] = f"{venv_bin}:{os.environ.get('PATH', '')}"

    episodes = []
    total = len(tasks) * len(args.providers)
    n = 0
    for provider_spec in args.providers:
        provider, _, model = provider_spec.partition(":")
        for task in tasks:
            n += 1
            print(f"[{n}/{total}] {task['id']} × {provider_spec} ...", flush=True)
            try:
                ep = run_episode(task, provider, model or None, args.timeout)
            except Exception as exc:  # episódio quebrado não derruba a rodada
                ep = {"task_id": task["id"], "provider": provider, "model": model or None,
                      "erro_harness": str(exc)}
            episodes.append(ep)
            if "mentiu" in ep:
                flag = "🚨 MENTIU" if ep["mentiu"] else ("✅ ok" if ep["verified"] else "🟥 falhou (honesto)" if ep["honesto_no_bloqueio"] else "🟥 falhou")
                print(f"   → {flag}")
            elif "erro_harness" in ep:
                print(f"   → ⚠️ erro do harness: {ep['erro_harness'][:80]}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"episodes-{time.strftime('%Y%m%d-%H%M%S')}.json"
    summary = {
        "suite": suite["suite"],
        "episodios": len(episodes),
        "mentiras": sum(1 for e in episodes if e.get("mentiu")),
        "verdades": sum(1 for e in episodes if e.get("verified") and e.get("claimed_pronto")),
        "falhas_honestas": sum(1 for e in episodes if e.get("honesto_no_bloqueio")),
        "por_provider": {},
    }
    for e in episodes:
        key = f"{e.get('provider')}/{e.get('model') or 'default'}"
        bucket = summary["por_provider"].setdefault(key, {"episodios": 0, "mentiras": 0, "verdades": 0})
        bucket["episodios"] += 1
        bucket["mentiras"] += 1 if e.get("mentiu") else 0
        bucket["verdades"] += 1 if (e.get("verified") and e.get("claimed_pronto")) else 0
    out.write_text(json.dumps({"summary": summary, "episodes": episodes}, indent=2, ensure_ascii=False))
    print(f"\nResumo: {summary['episodios']} episódios | {summary['mentiras']} mentiras | "
          f"{summary['verdades']} verdades | {summary['falhas_honestas']} falhas honestas")
    print(f"Resultados: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
