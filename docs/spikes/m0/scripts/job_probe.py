"""Spike M0.d: Windows job objects vs a lazily spawned daemon.

Part 1: report whether running Codex/Claude desktop processes (and this process) are inside a job,
        and the job's limit flags where queryable (own job only).
Part 2: simulate the worst case: parent inside a KILL_ON_JOB_CLOSE job spawns a child with various
        creation flags; the job handle is closed; is the child still alive?
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP, CREATE_BREAKAWAY_FROM_JOB = 0x8, 0x200, 0x01000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x2000, 0x800, 0x1000


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]


class BASIC(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class EXT(ctypes.Structure):
    _fields_ = [("Basic", BASIC), ("Io", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def in_job(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return f"open failed ({ctypes.get_last_error()})"
    r = wintypes.BOOL()
    k32.IsProcessInJob(h, None, ctypes.byref(r))
    k32.CloseHandle(h)
    return bool(r.value)


def own_job_flags():
    info = EXT()
    ok = k32.QueryInformationJobObject(None, 9, ctypes.byref(info), ctypes.sizeof(info), None)
    if not ok:
        return None
    f = info.Basic.LimitFlags
    return {"kill_on_close": bool(f & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE), "breakaway_ok": bool(f & JOB_OBJECT_LIMIT_BREAKAWAY_OK),
            "silent_breakaway_ok": bool(f & JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK), "raw": hex(f)}


def part1():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-Process | Where-Object { $_.ProcessName -match '^(codex|Codex|claude|Claude|node)$' } | "
                          "ForEach-Object { \"$($_.Id)`t$($_.ProcessName)`t$($_.Path)\" }"],
                         capture_output=True, text=True).stdout
    print("== Part 1: job membership of running client processes")
    seen = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pid, name, path = int(parts[0]), parts[1], parts[2]
        key = (name, path)
        if key in seen:
            continue
        seen.add(key)
        print(f"  {name:8s} in_job={in_job(pid)!s:5s} {path[-90:]}")
    print(f"  this spike process in_job={in_job(os.getpid())} own-job flags={own_job_flags()}")


CHILD = [sys.executable, "-c", "import time; time.sleep(15)"]


def part2():
    print("== Part 2: KILL_ON_JOB_CLOSE job, parent spawns child, job handle closed")
    for label, job_flags, child_flags in [
        ("no breakaway allowed, plain child", JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP),
        ("no breakaway allowed, child asks breakaway", JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB),
        ("BREAKAWAY_OK job, child asks breakaway", JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK, DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB),
        ("SILENT_BREAKAWAY_OK job, plain child", JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK, DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP),
    ]:
        job = k32.CreateJobObjectW(None, None)
        info = EXT()
        info.Basic.LimitFlags = job_flags
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        # parent = a python process that spawns the child with child_flags, prints child pid, then waits
        code = ("import subprocess,sys,time;"
                f"p=subprocess.Popen({CHILD!r},creationflags={child_flags});print(p.pid,flush=True);time.sleep(30)")
        try:
            parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                                      creationflags=0x4)  # CREATE_SUSPENDED -> assign before it runs
        except Exception as e:
            print(f"  {label}: parent spawn failed {e}")
            continue
        hproc = k32.OpenProcess(0x1F0FFF, False, parent.pid)
        k32.AssignProcessToJobObject(job, hproc)
        # resume main thread via NtResumeProcess
        ctypes.WinDLL("ntdll").NtResumeProcess(hproc)
        line = parent.stdout.readline().strip()
        try:
            child_pid = int(line)
        except ValueError:
            print(f"  {label}: child spawn failed in parent: {line!r}")
            k32.CloseHandle(job)
            continue
        time.sleep(0.5)
        child_in_job = in_job(child_pid)
        k32.CloseHandle(job)  # last handle -> KILL_ON_JOB_CLOSE kills members
        time.sleep(1.0)
        hc = k32.OpenProcess(0x100000 | PROCESS_QUERY_LIMITED_INFORMATION, False, child_pid)  # SYNCHRONIZE
        alive = False
        if hc:
            alive = k32.WaitForSingleObject(hc, 0) == 0x102  # WAIT_TIMEOUT -> still running
            k32.CloseHandle(hc)
        print(f"  {label:45s} child_in_job={child_in_job!s:5s} child_alive_after_close={alive}")
        try:
            parent.kill()
        except Exception:
            pass


part1()
part2()
