from __future__ import annotations

import shutil
import subprocess


def _run(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def get_mac_status() -> str:
    total, used, free = shutil.disk_usage("/")
    disk = f"Disk: {used // (1024**3)}GB used / {total // (1024**3)}GB total, {free // (1024**3)}GB free"
    uptime = _run(["uptime"]) or "Uptime: unavailable"
    load = _run(["sysctl", "-n", "vm.loadavg"]) or "Load: unavailable"
    pressure = _run(["memory_pressure"])
    parts = [disk, f"Uptime: {uptime}", f"Load: {load}"]
    if pressure:
        parts.append("Memory pressure:\n" + "\n".join(pressure.splitlines()[:8]))
    return "\n".join(parts)

