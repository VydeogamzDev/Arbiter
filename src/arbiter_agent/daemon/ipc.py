"""Local IPC transport (spec §4.4.1, §18.8). Stdlib only (imported by shims).

Windows: a named pipe created with a protected DACL granting only the current user,
``PIPE_REJECT_REMOTE_CLIENTS`` and ``FILE_FLAG_FIRST_PIPE_INSTANCE`` (so a squatter that created
the name first makes startup fail instead of intercepting clients).
POSIX: a Unix socket inside a 0700 directory, socket file 0600.
Fallback: loopback TCP. On every transport both sides run a mutual HMAC challenge with the
per-install token before any frame is exchanged.
"""

from __future__ import annotations

import os
import sys
import time
from multiprocessing import AuthenticationError
from multiprocessing import connection as mpc
from typing import Any

from arbiter_agent.daemon import protocol

Connection = Any  # multiprocessing Connection / PipeConnection

ERROR_FILE_NOT_FOUND = 2
ERROR_SEM_TIMEOUT = 121
ERROR_PIPE_BUSY = 231
PIPE_BUFSIZE = 8192  # same as multiprocessing.connection.BUFSIZE


class IPCUnavailable(ConnectionError):
    """No daemon endpoint (not running, or not reachable in time)."""


class IPCAuthError(ConnectionError):
    """Token challenge failed."""


# --------------------------------------------------------------------------- server side
if sys.platform == "win32":
    import _winapi

    class SecurePipeListener(mpc.PipeListener):  # type: ignore[name-defined]
        """PipeListener whose pipe instances carry a user-only DACL and reject remote clients."""

        def __init__(self, address: str) -> None:
            from arbiter_agent.winsec import UserOnlySecurityAttributes

            self._sa = UserOnlySecurityAttributes()
            super().__init__(address)

        def _new_handle(self, first: bool = False) -> int:
            flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
            if first:
                flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
            mode = (_winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT
                    | 0x00000008)  # PIPE_REJECT_REMOTE_CLIENTS
            return _winapi.CreateNamedPipe(self._address, flags, mode, _winapi.PIPE_UNLIMITED_INSTANCES,
                                           PIPE_BUFSIZE, PIPE_BUFSIZE, _winapi.NMPWAIT_WAIT_FOREVER, self._sa.address)


def _check_socket_dir(path: str) -> None:
    """Refuse a socket directory another user owns or that others can access (squatting)."""
    st = os.stat(path)
    if st.st_uid != os.getuid() or (st.st_mode & 0o077):  # type: ignore[attr-defined,unused-ignore]
        raise PermissionError(f"unsafe socket directory {path}")


class Endpoint:
    """Listening endpoint; ``accept()`` returns an *unauthenticated* connection."""

    def __init__(self, kind: str, address: Any, listener: Any) -> None:
        self.kind = kind
        self.address = address
        self._listener = listener

    def accept(self) -> Connection:
        return self._listener.accept()

    def close(self) -> None:
        try:
            self._listener.close()
        except Exception:
            pass

    def describe(self) -> dict[str, Any]:
        addr = list(self.address) if isinstance(self.address, tuple) else self.address
        return {"kind": self.kind, "address": addr}


def listen(pipe_address: str, prefer: str = "auto") -> Endpoint:
    if prefer in ("auto", "pipe", "unix"):
        try:
            if sys.platform == "win32":
                return Endpoint("pipe", pipe_address, SecurePipeListener(pipe_address))
            _check_socket_dir(os.path.dirname(pipe_address))
            if os.path.exists(pipe_address):
                os.unlink(pipe_address)  # stale socket; the single-instance lock is held by us
            lst = mpc.Listener(pipe_address, family="AF_UNIX")
            os.chmod(pipe_address, 0o600)
            return Endpoint("unix", pipe_address, lst)
        except OSError:
            if prefer != "auto":
                raise
    lst = mpc.Listener(("127.0.0.1", 0), family="AF_INET")
    return Endpoint("tcp", lst.address, lst)


def server_handshake(conn: Connection, token: bytes) -> None:
    """Mutual challenge (server side). Raises IPCAuthError on failure."""
    try:
        mpc.deliver_challenge(conn, token)
        mpc.answer_challenge(conn, token)
    except (AuthenticationError, EOFError, OSError) as exc:
        raise IPCAuthError(str(exc)) from exc


# --------------------------------------------------------------------------- client side
if sys.platform == "win32":

    def _pipe_connect(address: str, timeout: float) -> Connection:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _winapi.WaitNamedPipe(address, max(1, int((deadline - time.monotonic()) * 1000)))
                h = _winapi.CreateFile(address, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0, _winapi.NULL,
                                       _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, _winapi.NULL)
                break
            except OSError as exc:
                winerr = getattr(exc, "winerror", None)
                if winerr in (ERROR_SEM_TIMEOUT, ERROR_PIPE_BUSY) and time.monotonic() < deadline:
                    continue
                raise IPCUnavailable(f"pipe unavailable ({winerr})") from exc
        _winapi.SetNamedPipeHandleState(h, _winapi.PIPE_READMODE_MESSAGE, None, None)
        return mpc.PipeConnection(h)

else:

    def _pipe_connect(address: str, timeout: float) -> Connection:
        raise IPCUnavailable("named pipes are Windows-only")


def connect(endpoint: dict[str, Any] | None, pipe_address: str, token: bytes, timeout: float = 1.0) -> Connection:
    """Connect and authenticate. ``endpoint`` comes from daemon.json (tcp fallback) or None."""
    kind = (endpoint or {}).get("kind") or ("pipe" if sys.platform == "win32" else "unix")
    try:
        if kind == "pipe":
            conn = _pipe_connect(pipe_address, timeout)
        elif kind == "unix":
            if not os.path.exists(pipe_address):
                raise IPCUnavailable("socket missing")
            conn = mpc.Client(pipe_address, family="AF_UNIX")
        else:
            host, port = (endpoint or {})["address"]
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise IPCUnavailable("refusing non-loopback endpoint")
            conn = mpc.Client((host, int(port)), family="AF_INET")
    except IPCUnavailable:
        raise
    except (OSError, EOFError) as exc:
        raise IPCUnavailable(str(exc)) from exc
    try:
        mpc.answer_challenge(conn, token)
        mpc.deliver_challenge(conn, token)
    except (AuthenticationError, EOFError, OSError) as exc:
        conn.close()
        raise IPCAuthError(str(exc)) from exc
    return conn


def call(conn: Connection, msg: dict[str, Any], timeout: float) -> dict[str, Any]:
    conn.send_bytes(protocol.encode(msg))
    if not conn.poll(timeout):
        raise TimeoutError("daemon did not answer in time")
    return protocol.decode(conn.recv_bytes(protocol.MAX_FRAME))
