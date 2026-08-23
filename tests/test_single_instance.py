"""The single-instance lock.

Two copies of the app would both install global Esc hotkeys and both could drive
the mouse, so the lock is a correctness feature, not a nicety.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from app.single_instance import AlreadyRunning, SingleInstance

#: Child program that tries to take the lock and reports which way it went.
_TRY_ACQUIRE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from app.single_instance import SingleInstance, AlreadyRunning
    try:
        SingleInstance().acquire()
        print("ACQUIRED")
    except AlreadyRunning:
        print("BLOCKED")
    """
)


def _child_result(repo_root: str) -> str:
    out = subprocess.run(
        [sys.executable, "-c", _TRY_ACQUIRE, repo_root],
        capture_output=True, text=True, timeout=60,
    )
    return out.stdout.strip()


@pytest.fixture
def repo_root() -> str:
    from pathlib import Path
    return str(Path(__file__).resolve().parent.parent)


def test_second_instance_is_blocked(repo_root):
    lock = SingleInstance()
    lock.acquire()
    try:
        assert _child_result(repo_root) == "BLOCKED"
    finally:
        lock.release()


def test_lock_is_reusable_after_release(repo_root):
    lock = SingleInstance()
    lock.acquire()
    lock.release()

    assert _child_result(repo_root) == "ACQUIRED"


def test_release_is_idempotent():
    lock = SingleInstance()
    lock.acquire()
    lock.release()
    lock.release()  # must not raise or double-free the handle


def test_context_manager_releases():
    with SingleInstance():
        pass
    # If __exit__ failed to release, this second acquire would raise.
    second = SingleInstance()
    second.acquire()
    second.release()


def test_acquiring_twice_in_one_process_reports_conflict(repo_root):
    """A second SingleInstance object is still a second claim on the mutex."""
    first = SingleInstance()
    first.acquire()
    try:
        with pytest.raises(AlreadyRunning):
            SingleInstance().acquire()
    finally:
        first.release()


def test_error_message_names_the_app():
    """The message goes straight into a dialog, so it must read plainly."""
    exc = AlreadyRunning({"pid": 4321})

    assert "already running" in str(exc)
    assert "4321" in str(exc)
