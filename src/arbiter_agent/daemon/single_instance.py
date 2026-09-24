"""At most one daemon per install (spec §4.4.1). The OS-level file lock is held for the whole
daemon lifetime and released automatically if the process dies, so a crashed daemon never
blocks the next one."""

from __future__ import annotations

import os
import sys
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> InstanceLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise AlreadyRunning(f"another daemon holds {self.path}") from exc
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(self._fd)
        self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None
