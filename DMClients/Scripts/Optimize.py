import psutil
import sys
import ctypes
from ctypes import wintypes

PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
ProcessPowerThrottling = 4
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_INFORMATION = 0x0200
IDLE_PRIORITY_CLASS = 0x00000040

class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", wintypes.ULONG),
        ("ControlMask", wintypes.ULONG),
        ("StateMask", wintypes.ULONG),
    ]

def enable_efficiency_mode(pid: int) -> bool:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        
        hProcess = kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_SET_INFORMATION,
            False,
            pid
        )
        if not hProcess:
            return False
        
        kernel32.SetPriorityClass(hProcess, IDLE_PRIORITY_CLASS)
        
        throttle_state = PROCESS_POWER_THROTTLING_STATE()
        throttle_state.Version = PROCESS_POWER_THROTTLING_CURRENT_VERSION
        throttle_state.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED
        throttle_state.StateMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED
        
        result = kernel32.SetProcessInformation(
            hProcess,
            ProcessPowerThrottling,
            ctypes.byref(throttle_state),
            ctypes.sizeof(throttle_state)
        )
        
        kernel32.CloseHandle(hProcess)
        return result != 0
    except Exception:
        return False

found = 0
for proc in psutil.process_iter(['name', 'pid']):
    try:
        name = proc.info['name'].lower()
        if name.startswith('hddnet') and name.endswith('.exe'):
            pid = proc.info['pid']
            if enable_efficiency_mode(pid):
                log(f"✅ {name} (PID {pid}) → Efficiency Mode + IDLE priority")
            else:
                log(f"⚠️ {name} (PID {pid}) → failed to enable")
            found += 1
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        log(f"⚠️ Error on process: {e}")

log("")
log(f"Processed {found} processes.")