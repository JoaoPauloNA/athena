"""Primitivas conservadoras de inspeção e teardown POSIX."""

from __future__ import annotations

import os
import signal
import subprocess
import time


def process_group_is_empty(process_group: int) -> bool:
    """Confirmar positivamente que um grupo de processos não existe mais."""
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def pid_may_be_alive(pid: int) -> bool:
    """Tratar ausência de permissão como vida possível, nunca como confirmação."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def observe_descendants(
    root_pid: int,
    owned_group: int,
    previously_seen: set[int],
) -> tuple[set[int], set[int]]:
    """Acumular descendentes e identificar os que saíram do grupo possuído.

    PIDs já ligados à árvore permanecem rastreados depois de reparenting. Isso
    fecha a janela em que um filho é visto no grupo e chama ``setsid`` antes da
    fotografia seguinte da tabela de processos.
    """
    try:
        snapshot = subprocess.run(
            ("ps", "-axo", "pid=,ppid=,pgid="),
            capture_output=True,
            check=False,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return set(previously_seen), set()
    if snapshot.returncode != 0:
        return set(previously_seen), set()

    rows: list[tuple[int, int, int]] = []
    for line in snapshot.stdout.splitlines():
        try:
            pid, parent, group = (int(piece) for piece in line.split())
        except (TypeError, ValueError):
            continue
        rows.append((pid, parent, group))

    family = {root_pid, *previously_seen}
    changed = True
    while changed:
        changed = False
        for pid, parent, _group in rows:
            if parent in family and pid not in family:
                family.add(pid)
                changed = True
    family.discard(root_pid)
    groups = {pid: group for pid, _parent, group in rows}
    escaped = {
        pid
        for pid in family
        if pid in groups and groups[pid] != owned_group
    }
    return family, escaped


def terminate_owned_group(
    process: subprocess.Popen[bytes],
    process_group: int,
    *,
    grace_s: float,
) -> tuple[bool, bool]:
    """Encerrar somente o grupo possuído e confirmar processo direto e grupo."""
    try:
        os.killpg(process_group, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if process.poll() is not None and process_group_is_empty(process_group):
            break
        time.sleep(0.01)

    if not process_group_is_empty(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    try:
        process.wait(timeout=max(0.1, grace_s))
    except subprocess.TimeoutExpired:
        pass

    deadline = time.monotonic() + max(0.1, grace_s)
    while time.monotonic() < deadline and not process_group_is_empty(process_group):
        time.sleep(0.01)
    return process.poll() is not None, process_group_is_empty(process_group)
