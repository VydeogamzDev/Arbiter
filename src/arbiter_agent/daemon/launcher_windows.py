"""Launch the daemon outside the calling client's job object (decision 0018). Stdlib only.

Desktop clients (Codex desktop, Claude desktop) run inside Windows job objects that may kill
all descendants when the app closes and may deny breakaway. ``Win32_Process.Create`` via WMI
creates the process under ``WmiPrvSE.exe``: not in any job and not a descendant of the client.

WMI-created processes do not inherit our environment, so everything the daemon needs (for
example ``--home``) must be on its command line.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def quote_cmdline(argv: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(argv))


def _ps_single_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def launch_via_wmi(argv: Sequence[str], cwd: str | None = None) -> subprocess.Popen[bytes]:
    """Start ``argv`` through WMI. Returns the (short-lived) PowerShell helper process."""
    cmdline = quote_cmdline(argv)
    args = f"@{{CommandLine={_ps_single_quote(cmdline)}"
    if cwd:
        args += f"; CurrentDirectory={_ps_single_quote(cwd)}"
    args += "}"
    script = (f"$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments {args}; "
              "exit [int]$r.ReturnValue")
    return subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW)


def launch_detached(argv: Sequence[str], cwd: str | None = None) -> subprocess.Popen[bytes]:
    """Fallback: detached child, breaking away from the job when permitted."""
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:
        return subprocess.Popen(list(argv), cwd=cwd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                close_fds=True)
    except OSError:
        return subprocess.Popen(list(argv), cwd=cwd, creationflags=flags, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
