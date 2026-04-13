import psutil
import subprocess
import time

from .networkStats import getNetworkInfo


def getCpuTempC():
    # Try vcgencmd (Typical On Raspberry Pi OS)
    try:
        out = subprocess.check_output(["vcgencmd", "measure_temp"], text=True).strip()
        # Example: temp=48.2'C
        return float(out.split("=")[1].split("'")[0])
    except Exception:
        pass

    # Fallback: sysfs thermal zone
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return float(f.read().strip()) / 1000.0
    except Exception:
        return None


def getStats():
    bootSeconds = time.time() - psutil.boot_time()

    cpuPercent = psutil.cpu_percent(interval=0.2)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    tempC = getCpuTempC()

    # Network (Safe If Down)
    try:
        net = getNetworkInfo()
    except Exception:
        net = {
            "hostname": "-",
            "interface": "-",
            "ipAddr": "-",
            "ssid": "-",
            "signal": "-",
            "gateway": "-",
            "internet": "No",
            "SSH": "Offline",
        }

    return {
        "cpuPercent": cpuPercent,
        "cpuTempC": tempC,
        "ramPercent": ram.percent,
        "ramUsedMb": round(ram.used / (1024 * 1024), 1),
        "ramTotalMb": round(ram.total / (1024 * 1024), 1),
        "diskPercent": disk.percent,
        "diskUsedGb": round(disk.used / (1024 * 1024 * 1024), 2),
        "diskTotalGb": round(disk.total / (1024 * 1024 * 1024), 2),
        "bootMinutes": round(bootSeconds / 60.0, 1),
        "net": net,
    }
