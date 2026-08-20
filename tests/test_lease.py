"""Testes dos leases em processo para diretórios canônicos."""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import pytest

from athena.lease import (
    DirectoryLeaseContract,
    DirectoryLeaseManager,
    LeaseAcquisitionTimeout,
    LeaseOwnershipError,
)


def test_public_implementation_satisfies_lease_contract() -> None:
    assert isinstance(DirectoryLeaseManager(), DirectoryLeaseContract)


def test_symlink_and_target_share_the_same_canonical_lease(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "workspace-link"
    alias.symlink_to(workspace, target_is_directory=True)
    manager = DirectoryLeaseManager()

    target_key = manager.acquire(workspace, "execution-1", "attempt-1")

    assert target_key == workspace.resolve()
    assert manager.canonicalize(alias) == target_key
    with pytest.raises(LeaseAcquisitionTimeout):
        manager.acquire(alias, "execution-2", "attempt-1", timeout=0)

    manager.release(alias, "execution-1", "attempt-1")


def test_same_directory_is_serialized_across_attempt_transfer(
    tmp_path: Path,
) -> None:
    manager = DirectoryLeaseManager()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    waiting = threading.Event()
    acquired = threading.Event()

    manager.acquire(workspace, "execution-1", "attempt-1")

    def compete() -> None:
        waiting.set()
        manager.acquire(workspace, "execution-2", "attempt-1", timeout=1.0)
        acquired.set()
        manager.release(workspace, "execution-2", "attempt-1")

    contender = threading.Thread(target=compete)
    contender.start()
    assert waiting.wait(timeout=1.0)
    assert not acquired.wait(timeout=0.05)

    manager.transfer(workspace, "execution-1", "attempt-1", "attempt-2")
    assert not acquired.wait(timeout=0.05)

    manager.release(workspace, "execution-1", "attempt-2")
    assert acquired.wait(timeout=1.0)
    contender.join(timeout=1.0)
    assert not contender.is_alive()


def test_real_concurrent_access_to_one_directory_never_overlaps(
    tmp_path: Path,
) -> None:
    manager = DirectoryLeaseManager()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_count = 12
    barrier = threading.Barrier(thread_count)
    state_lock = threading.Lock()
    active = 0
    maximum_active = 0

    def work(index: int) -> None:
        nonlocal active, maximum_active
        barrier.wait()
        manager.acquire(workspace, f"execution-{index}", "attempt-1", timeout=2.0)
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.005)
        with state_lock:
            active -= 1
        manager.release(workspace, f"execution-{index}", "attempt-1")

    threads = [threading.Thread(target=work, args=(index,)) for index in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3.0)

    assert all(not thread.is_alive() for thread in threads)
    assert maximum_active == 1


def test_different_directories_never_block_each_other(tmp_path: Path) -> None:
    manager = DirectoryLeaseManager()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    acquired_second = threading.Event()

    manager.acquire(first, "execution-1", "attempt-1")

    def acquire_other_directory() -> None:
        manager.acquire(second, "execution-2", "attempt-1", timeout=0.5)
        acquired_second.set()
        manager.release(second, "execution-2", "attempt-1")

    contender = threading.Thread(target=acquire_other_directory)
    contender.start()

    assert acquired_second.wait(timeout=0.25)
    manager.release(first, "execution-1", "attempt-1")
    contender.join(timeout=1.0)
    assert not contender.is_alive()


def test_acquisition_timeout_does_not_steal_the_lease(tmp_path: Path) -> None:
    manager = DirectoryLeaseManager()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager.acquire(workspace, "execution-1", "attempt-1")

    with pytest.raises(LeaseAcquisitionTimeout):
        manager.acquire(workspace, "execution-2", "attempt-1", timeout=0.02)

    manager.transfer(workspace, "execution-1", "attempt-1", "attempt-2")
    manager.release(workspace, "execution-1", "attempt-2")


def test_transfer_and_release_require_the_exact_owner(tmp_path: Path) -> None:
    manager = DirectoryLeaseManager()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager.acquire(workspace, "execution-1", "attempt-1")

    with pytest.raises(LeaseOwnershipError):
        manager.transfer(workspace, "execution-1", "wrong-attempt", "attempt-2")
    with pytest.raises(LeaseOwnershipError):
        manager.release(workspace, "execution-2", "attempt-1")

    manager.release(workspace, "execution-1", "attempt-1")


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan")])
def test_timeout_must_be_non_negative_and_finite(
    tmp_path: Path,
    timeout: float,
) -> None:
    manager = DirectoryLeaseManager()

    with pytest.raises(ValueError, match="timeout"):
        manager.acquire(tmp_path, "execution-1", "attempt-1", timeout=timeout)


def test_lease_imports_no_other_core_package() -> None:
    package = Path(__file__).resolve().parents[1] / "athena" / "lease"
    imported_core_packages: set[str] = set()

    for module in package.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_core_packages.add(node.module)
            elif isinstance(node, ast.Import):
                imported_core_packages.update(alias.name for alias in node.names)

    assert not {
        name
        for name in imported_core_packages
        if name == "athena" or name.startswith("athena.")
    }
