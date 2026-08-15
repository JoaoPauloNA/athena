"""Tests for athena.workspace_lease (Sprint A6 — in-process workspace lease).

All tests exercise the module directly with synthetic execution-metadata
dicts (the shape of `RunResult.execution` / `ExecutionRecord.to_dict()`) —
no real CLIs, subprocesses, or network are ever touched here.

IMPORTANT (mirrors the module docstring): this lease is in-process only. It
protects two callers *inside the same Python process* from racing on a
workspace; it provides zero protection between separate OS processes or
hosts sharing a filesystem. That limitation is intentional Sprint A6 scope,
not a bug these tests are checking for.
"""
from __future__ import annotations

import pytest

from athena import workspace_lease


@pytest.fixture(autouse=True)
def _clean_lease_registry():
    """The lease registry is process-global state; reset it around every
    test so tests never leak leases into each other."""
    workspace_lease._reset_for_tests()
    yield
    workspace_lease._reset_for_tests()


def _meta(state, **overrides):
    base = {
        "state": state,
        "process_created": True,
        "direct_process_terminated_confirmed": True,
        "process_tree_terminated_confirmed": True,
        "pgid": None,
        "client_abandoned": False,
    }
    base.update(overrides)
    return base


# --- 1. same-workspace second execution blocked ----------------------------


def test_same_workspace_second_execution_blocked(tmp_path):
    ws = str(tmp_path)
    assert workspace_lease.acquire_for_scope(ws, "exec-a", "attempt-1") is True
    assert workspace_lease.acquire_for_scope(ws, "exec-a", "attempt-1") is False
    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        workspace_lease.acquire(ws, "exec-b", "attempt-1")
    # The original owner is unaffected by the rejected attempt.
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-1")


# --- 2. different workspaces allowed ----------------------------------------


def test_different_workspaces_allowed(tmp_path):
    ws1 = tmp_path / "a"
    ws2 = tmp_path / "b"
    ws1.mkdir()
    ws2.mkdir()

    workspace_lease.acquire(str(ws1), "exec-a", "attempt-1")
    workspace_lease.acquire(str(ws2), "exec-b", "attempt-1")  # must not raise

    assert workspace_lease.current_holder(str(ws1)) == ("exec-a", "attempt-1")
    assert workspace_lease.current_holder(str(ws2)) == ("exec-b", "attempt-1")


# --- 3. fallback cannot receive lease before termination confirmation ------


def test_fallback_cannot_receive_lease_before_termination_confirmation(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")

    unconfirmed = _meta(
        "TERMINATION_UNCONFIRMED",
        direct_process_terminated_confirmed=True,
        process_tree_terminated_confirmed=False,
    )
    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        workspace_lease.transfer(
            ws, "exec-a", "attempt-1", "attempt-2", execution_meta=unconfirmed
        )


# --- 4. TERMINATION_UNCONFIRMED retains lock --------------------------------


def test_termination_unconfirmed_retains_lock(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")

    unconfirmed = _meta("TERMINATION_UNCONFIRMED")
    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        workspace_lease.transfer(
            ws, "exec-a", "attempt-1", "attempt-2", execution_meta=unconfirmed
        )

    # The lease is still held by the original attempt -- nothing released it.
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-1")

    # A different execution must still be refused the workspace.
    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        workspace_lease.acquire(ws, "exec-b", "attempt-1")

    # release_after_attempt() must also refuse to release under this metadata.
    released = workspace_lease.release_after_attempt(
        ws, "exec-a", "attempt-1", execution_meta=unconfirmed
    )
    assert released is False
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-1")


# --- 5. pre-Popen failure releases lease ------------------------------------


def test_pre_popen_failure_releases_lease(tmp_path):
    """process_created=False means Popen() itself raised (e.g. missing
    command) -- no process ever existed, so there is nothing to confirm
    terminated; the lease must release immediately."""
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")

    never_started = _meta(
        "FAILED",
        process_created=False,
        direct_process_terminated_confirmed=False,
        process_tree_terminated_confirmed=False,
    )
    never_started["execution_id"] = "exec-a"
    never_started["attempt_id"] = "attempt-1"
    released = workspace_lease.release_after_attempt(
        ws, "exec-a", "attempt-1", execution_meta=never_started
    )
    assert released is True
    assert workspace_lease.current_holder(ws) is None


# --- 6. confirmed normal error permits fallback transfer -------------------


def test_confirmed_normal_error_permits_fallback_transfer(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")

    confirmed_failed = _meta(
        "FAILED",
        process_created=True,
        direct_process_terminated_confirmed=True,
        process_tree_terminated_confirmed=True,
        pgid=123,
    )
    confirmed_failed["execution_id"] = "exec-a"
    confirmed_failed["attempt_id"] = "attempt-1"
    workspace_lease.transfer(
        ws, "exec-a", "attempt-1", "attempt-2", execution_meta=confirmed_failed
    )
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-2")

    # A different execution is still correctly refused -- the lease moved to
    # a new attempt of the SAME execution, it was never released to the wild.
    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        workspace_lease.acquire(ws, "exec-b", "attempt-1")


# --- 7. success releases -----------------------------------------------------


def test_success_releases(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")

    completed = _meta("COMPLETED")
    completed["execution_id"] = "exec-a"
    completed["attempt_id"] = "attempt-1"
    released = workspace_lease.release_after_attempt(
        ws, "exec-a", "attempt-1", execution_meta=completed
    )
    assert released is True
    assert workspace_lease.current_holder(ws) is None


def test_failed_confirmed_releases(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    meta = _meta("FAILED", pgid=123)
    meta["execution_id"] = "exec-a"
    meta["attempt_id"] = "attempt-1"
    assert workspace_lease.release_after_attempt(ws, "exec-a", "attempt-1", execution_meta=meta) is True
    assert workspace_lease.current_holder(ws) is None


def test_cancelled_confirmed_releases(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    meta = _meta("CANCELLED", pgid=123)
    meta["execution_id"] = "exec-a"
    meta["attempt_id"] = "attempt-1"
    assert workspace_lease.release_after_attempt(ws, "exec-a", "attempt-1", execution_meta=meta) is True
    assert workspace_lease.current_holder(ws) is None


def test_remote_completed_without_remote_confirmation_does_not_release(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    meta = _meta("COMPLETED", pgid=123)
    meta["execution_id"] = "exec-a"
    meta["attempt_id"] = "attempt-1"
    meta["transport"] = "ssh"
    meta["remote_session_started"] = True
    meta["remote_termination_confirmed"] = False
    released = workspace_lease.release_after_attempt(
        ws, "exec-a", "attempt-1", execution_meta=meta
    )
    assert released is False
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-1")


def test_is_safe_attempt_end_validates_expected_ids_and_state():
    good = _meta("COMPLETED", pgid=123)
    good["execution_id"] = "exec-a"
    good["attempt_id"] = "attempt-1"
    assert workspace_lease.is_safe_attempt_end(good) is True
    assert (
        workspace_lease.is_safe_attempt_end(
            good,
            expected_execution_id="exec-a",
            expected_attempt_id="attempt-1",
        )
        is True
    )
    assert (
        workspace_lease.is_safe_attempt_end(
            good,
            expected_execution_id="exec-b",
            expected_attempt_id="attempt-1",
        )
        is False
    )
    assert (
        workspace_lease.is_safe_attempt_end(
            good,
            expected_execution_id="exec-a",
            expected_attempt_id="attempt-x",
        )
        is False
    )
    bad = dict(good)
    bad["state"] = "TERMINATION_UNCONFIRMED"
    assert workspace_lease.is_safe_attempt_end(bad) is False


# --- 8. repeated release safe -----------------------------------------------


def test_repeated_release_safe(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")

    workspace_lease.release(ws, "exec-a", "attempt-1")
    workspace_lease.release(ws, "exec-a", "attempt-1")  # idempotent: no error, still unheld
    assert workspace_lease.current_holder(ws) is None

    # release_after_attempt() on an already-unheld workspace is safe too.
    completed = _meta("COMPLETED")
    completed["execution_id"] = "exec-a"
    completed["attempt_id"] = "attempt-1"
    released = workspace_lease.release_after_attempt(
        ws, "exec-a", "attempt-1", execution_meta=completed
    )
    assert released is True
    assert workspace_lease.current_holder(ws) is None

    # acquire() is also idempotent for the same (execution_id, attempt_id).
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-1")


# --- 9. real path / symlink collide -----------------------------------------


def test_real_path_and_symlink_collide(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir)

    workspace_lease.acquire(str(real_dir), "exec-a", "attempt-1")
    # Same directory reached via a symlink must collide with the real path.
    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        workspace_lease.acquire(str(link_dir), "exec-b", "attempt-1")

    # Also collides with a non-canonical spelling (trailing slash / "..").
    noncanonical = str(real_dir) + "/./"
    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        workspace_lease.acquire(noncanonical, "exec-c", "attempt-1")


# --- 10. unexpected exception does not release while execution may be active


def test_unexpected_exception_does_not_release_while_execution_may_be_active(tmp_path):
    """The module offers no implicit finally-release / context manager --
    release only ever happens via an explicit call. This documents that
    contract: a caller whose code raises before reaching its release call
    leaves the lease exactly as it was, because nothing in this module
    releases it on their behalf."""
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")

    class Boom(Exception):
        pass

    def _caller_flow():
        # Simulates a router-style caller: acquire (idempotent re-affirm),
        # then the provider call raises unexpectedly before any release.
        workspace_lease.acquire(ws, "exec-a", "attempt-1")
        raise Boom("provider raised unexpectedly")

    with pytest.raises(Boom):
        _caller_flow()

    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-1")


# --- 11. exact re-acquire idempotent; same-execution different-attempt rejected


def test_exact_reacquire_idempotent(tmp_path):
    """Re-acquiring with the identical (execution_id, attempt_id) pair is a
    safe no-op re-affirmation -- it must not raise and must not disturb the
    held lease."""
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-1")


def test_same_execution_different_attempt_acquire_rejected(tmp_path):
    """acquire() must NOT silently move the lease to a new attempt_id of the
    same execution_id -- that would bypass the `_is_safe_to_release` check
    that `transfer()` enforces. Callers must use transfer() instead, and the
    lease must remain exactly as it was on the original attempt."""
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")

    with pytest.raises(workspace_lease.WorkspaceLeaseError, match="transfer"):
        workspace_lease.acquire(ws, "exec-a", "attempt-2")

    # The original attempt is untouched by the rejected acquire.
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-1")

    # The legitimate path -- transfer() with confirmed-safe metadata -- still
    # works and actually moves the lease.
    confirmed_failed = _meta(
        "FAILED",
        process_created=True,
        direct_process_terminated_confirmed=True,
        process_tree_terminated_confirmed=True,
        pgid=123,
    )
    confirmed_failed["execution_id"] = "exec-a"
    confirmed_failed["attempt_id"] = "attempt-1"
    workspace_lease.transfer(
        ws, "exec-a", "attempt-1", "attempt-2", execution_meta=confirmed_failed
    )
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-2")


# --- canonicalize_workspace --------------------------------------------------


def test_canonicalize_workspace_resolves_relative_and_trailing_slash(tmp_path, monkeypatch):
    real_dir = tmp_path / "proj"
    real_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    assert workspace_lease.canonicalize_workspace("proj/") == str(real_dir.resolve())
    assert workspace_lease.canonicalize_workspace(str(real_dir)) == str(real_dir.resolve())


def test_delayed_cleanup_attempt1_cannot_release_attempt2_lease(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    meta_attempt1 = _meta("FAILED", pgid=123)
    meta_attempt1["execution_id"] = "exec-a"
    meta_attempt1["attempt_id"] = "attempt-1"
    workspace_lease.transfer(ws, "exec-a", "attempt-1", "attempt-2", execution_meta=meta_attempt1)

    # Stale cleanup from attempt-1 must not remove attempt-2 ownership.
    released = workspace_lease.release_after_attempt(
        ws, "exec-a", "attempt-1", execution_meta=meta_attempt1
    )
    assert released is False
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-2")


def test_release_rejects_metadata_from_other_attempt(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    wrong_meta = _meta("FAILED", pgid=123)
    wrong_meta["execution_id"] = "exec-a"
    wrong_meta["attempt_id"] = "attempt-2"
    released = workspace_lease.release_after_attempt(
        ws, "exec-a", "attempt-1", execution_meta=wrong_meta
    )
    assert released is False
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-1")


def test_client_abandoned_with_confirmed_cancelled_releases(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    meta = _meta(
        "CANCELLED",
        client_abandoned=True,
        direct_process_terminated_confirmed=True,
        process_tree_terminated_confirmed=True,
        pgid=123,
    )
    meta["execution_id"] = "exec-a"
    meta["attempt_id"] = "attempt-1"
    assert workspace_lease.release_after_attempt(ws, "exec-a", "attempt-1", execution_meta=meta) is True
    assert workspace_lease.current_holder(ws) is None


def test_client_abandoned_without_confirmation_retains(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    meta = _meta(
        "TERMINATION_UNCONFIRMED",
        client_abandoned=True,
        direct_process_terminated_confirmed=True,
        process_tree_terminated_confirmed=False,
        pgid=123,
    )
    meta["execution_id"] = "exec-a"
    meta["attempt_id"] = "attempt-1"
    assert workspace_lease.release_after_attempt(ws, "exec-a", "attempt-1", execution_meta=meta) is False
    assert workspace_lease.current_holder(ws) == ("exec-a", "attempt-1")


def test_timed_out_logical_phase_without_process_releases(tmp_path):
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "exec-a", "attempt-1")
    meta = _meta(
        "TIMED_OUT",
        process_created=False,
        direct_process_terminated_confirmed=False,
        process_tree_terminated_confirmed=False,
    )
    meta["execution_id"] = "exec-a"
    meta["attempt_id"] = "attempt-1"
    assert workspace_lease.release_after_attempt(ws, "exec-a", "attempt-1", execution_meta=meta) is True
    assert workspace_lease.current_holder(ws) is None
