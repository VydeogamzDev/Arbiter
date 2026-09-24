"""Can a process inside a job launch a daemon OUTSIDE any job (and not as its descendant)?
Method: WMI Win32_Process.Create via PowerShell CIM (process parented by WmiPrvSE)."""
import ctypes, subprocess, sys, time, os
from ctypes import wintypes
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
def in_job(pid):
    h = k32.OpenProcess(0x1000, False, pid)
    if not h: return f"open failed {ctypes.get_last_error()}"
    r = wintypes.BOOL(); k32.IsProcessInJob(h, None, ctypes.byref(r)); k32.CloseHandle(h); return bool(r.value)
print("caller in_job:", in_job(os.getpid()))
cmd = f'"{sys.executable}" -c "import time; time.sleep(20)"'
t = time.perf_counter()
out = subprocess.run(["powershell", "-NoProfile", "-Command",
    f"$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine='{cmd}'}}; \"$($r.ReturnValue) $($r.ProcessId)\""],
    capture_output=True, text=True)
dt = (time.perf_counter() - t) * 1000
rv, pid = out.stdout.split()
pid = int(pid)
time.sleep(0.5)
parent = subprocess.run(["powershell", "-NoProfile", "-Command",
    f"$p=(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'); $pp=(Get-CimInstance Win32_Process -Filter \"ProcessId=$($p.ParentProcessId)\"); \"$($p.ParentProcessId) $($pp.Name)\""],
    capture_output=True, text=True).stdout.strip()
print(f"WMI create rv={rv} pid={pid} in_job={in_job(pid)} parent={parent} launch_ms={dt:.0f}")
subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
