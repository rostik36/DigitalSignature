"""Allow only one running copy of the app.

Two instances are genuinely harmful here, not merely untidy: both would install
global Esc hotkeys and both could drive the mouse, so a replay from one would
fight the other mid-signature.

The lock is a **named kernel mutex** (``CreateMutexW``) rather than a lock file.
Windows destroys the handle when the process ends -- including a crash, a kill
from Task Manager, or a power loss -- so there is no stale lock to clean up and
no "delete this file to recover" advice to give. A lock *file* cannot promise
that: a crash leaves the file behind and the next launch has to guess whether
the owner is still alive.

A small pointer file is still written alongside it, but only as a breadcrumb for
the user ("who holds it, since when"); it is never consulted to decide the lock.

On non-Windows systems the mutex does not exist, so we fall back to an
``O_EXCL`` lock file with a liveness check on the recorded PID.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import tempfile
from typing import Optional

#: Global kernel namespace so the lock spans terminal servers/sessions. Prefixed
#: rather than bare to avoid colliding with any other app's mutex name.
MUTEX_NAME = "Global\\DigitalSignature.SingleInstance.v1"

_ERROR_ALREADY_EXISTS = 183


def _info_path() -> str:
    return os.path.join(tempfile.gettempdir(), "DigitalSignature.instance.json")


class AlreadyRunning(Exception):
    """Another instance holds the lock."""

    def __init__(self, info: Optional[dict] = None) -> None:
        self.info = info or {}
        pid = self.info.get("pid")
        detail = f" (process {pid})" if pid else ""
        super().__init__(f"Digital Signature is already running{detail}.")


class SingleInstance:
    """Hold the single-instance lock for the lifetime of the process.

    Usage::

        lock = SingleInstance()
        lock.acquire()          # raises AlreadyRunning if a copy is up
    """

    def __init__(self) -> None:
        self._handle = None
        self._lock_fd: Optional[int] = None
        self._acquired = False

    # ------------------------------------------------------------------
    def acquire(self) -> None:
        """Take the lock, or raise :class:`AlreadyRunning`."""
        if sys.platform.startswith("win"):
            self._acquire_windows()
        else:
            self._acquire_posix()
        self._acquired = True
        self._write_info()
        atexit.register(self.release)

    def _acquire_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE

        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        err = ctypes.get_last_error()
        if not handle:
            # Can't create the mutex at all (e.g. no rights to the Global
            # namespace). Don't block the user over a lock we can't take.
            return
        if err == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise AlreadyRunning(self._read_info())
        self._handle = handle

    def _acquire_posix(self) -> None:
        path = _info_path()
        try:
            self._lock_fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            info = self._read_info()
            if _pid_alive(info.get("pid")):
                raise AlreadyRunning(info) from None
            # Owner is gone; reclaim the stale file.
            os.unlink(path)
            self._lock_fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)

    # ------------------------------------------------------------------
    def _write_info(self) -> None:
        """Best-effort breadcrumb. Never fatal -- the mutex is the real lock."""
        try:
            with open(_info_path(), "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "exe": sys.executable}, fh)
        except OSError:
            pass

    def _read_info(self) -> dict:
        try:
            with open(_info_path(), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def release(self) -> None:
        """Drop the lock. Safe to call twice; also runs via ``atexit``."""
        if not self._acquired:
            return
        self._acquired = False

        if self._handle is not None:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex(self._handle)
            kernel32.CloseHandle(self._handle)
            self._handle = None

        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None
            try:
                os.unlink(_info_path())
            except OSError:
                pass

    def __enter__(self) -> "SingleInstance":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def _pid_alive(pid: Optional[int]) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)  # signal 0 only checks for existence
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True
