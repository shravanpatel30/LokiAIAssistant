"""Gather local system statistics."""
import socket
import time
import psutil


def _bytes_to_gb(b):
    return round(b / (1024 ** 3), 1)


def get_cpu():
    return {
        "usage_percent": psutil.cpu_percent(interval=0.5),
        "cores_physical": psutil.cpu_count(logical=False),
        "cores_logical": psutil.cpu_count(logical=True),
    }


def get_ram():
    mem = psutil.virtual_memory()
    return {
        "total_gb": _bytes_to_gb(mem.total),
        "used_gb": _bytes_to_gb(mem.used),
        "available_gb": _bytes_to_gb(mem.available),
        "percent_used": mem.percent,
    }


def get_disks():
    disks = []
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append({
            "drive": part.device,
            "total_gb": _bytes_to_gb(usage.total),
            "used_gb": _bytes_to_gb(usage.used),
            "free_gb": _bytes_to_gb(usage.free),
            "percent_used": usage.percent,
        })
    return disks


def get_battery():
    batt = psutil.sensors_battery()
    if batt is None:
        return None
    return {
        "percent": round(batt.percent),
        "plugged_in": batt.power_plugged,
        "minutes_left": (round(batt.secsleft / 60)
                         if batt.secsleft not in (psutil.POWER_TIME_UNLIMITED,
                                                  psutil.POWER_TIME_UNKNOWN)
                         else None),
    }


def get_uptime():
    boot = psutil.boot_time()
    seconds = time.time() - boot
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return {"hours": hours, "minutes": minutes}


def get_local_ip():
    try:
        # Connect to a public address to find which local interface is used
        # (doesn't actually send data)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def gather_all():
    """Collect everything into one dict for the LLM to interpret."""
    data = {
        "cpu": get_cpu(),
        "ram": get_ram(),
        "disks": get_disks(),
        "uptime": get_uptime(),
        "local_ip": get_local_ip(),
    }
    batt = get_battery()
    if batt is not None:
        data["battery"] = batt
    return data