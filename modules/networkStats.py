import socket
import subprocess


def _run(cmd, timeout=2):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
    except Exception:
        return ""


def _firstNonEmpty(*vals):
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s != "":
            return s
    return ""


def _getHostname():
    out = _run(["hostname"])
    if out:
        return out
    try:
        return socket.gethostname()
    except Exception:
        return ""


def _getDefaultRouteInterfaceAndGateway():
    # Example Output: "default via 10.0.0.1 dev wlan0 proto dhcp src 10.0.0.42 metric 303"
    line = _run(["ip", "route", "show", "default"])
    if not line:
        return "", ""

    iface = ""
    gw = ""

    parts = line.split()
    for i, p in enumerate(parts):
        if p == "dev" and i + 1 < len(parts):
            iface = parts[i + 1]
        if p == "via" and i + 1 < len(parts):
            gw = parts[i + 1]

    return iface, gw


def _getIPv4ForInterface(iface):
    # Example Output: "2: wlan0    inet 10.0.0.42/24 brd 10.0.0.255 scope global dynamic noprefixroute wlan0"
    if not iface:
        return ""
    out = _run(["ip", "-o", "-4", "addr", "show", "dev", iface])
    if not out:
        return ""

    parts = out.split()
    if "inet" in parts:
        idx = parts.index("inet")
        if idx + 1 < len(parts):
            cidr = parts[idx + 1].strip()
            return cidr.split("/")[0]
    return ""


def _getSSID(iface):
    # iwgetid Is The Simplest If Available
    if not iface:
        return ""
    ssid = _run(["iwgetid", "-r", iface])
    return ssid


def _getSignalDbm(iface):
    # Example Output From `iw dev wlan0 link`:
    # "signal: -61 dBm"
    if not iface:
        return ""
    out = _run(["iw", "dev", iface, "link"])
    if not out:
        return ""

    for line in out.splitlines():
        line = line.strip()
        if line.startswith("signal:"):
            # Example Match: "signal: -61 dBm"
            return line.replace("signal:", "").strip()
    return ""


def _internetOk():
    # Fast And Good Enough
    # Flags: -n Numeric, -c 1 One Ping, -W 1 One Second Timeout
    try:
        subprocess.check_output(["ping", "-n", "-c", "1", "-W", "1", "1.1.1.1"], text=True, stderr=subprocess.DEVNULL, timeout=2)
        return True
    except Exception:
        return False


def getNetworkInfo():
    hostname = _getHostname()

    # Prefer Default Route Info
    iface, gateway = _getDefaultRouteInterfaceAndGateway()

    # Fallback If Default Route Missing: Try Common Interfaces
    if not iface:
        for candidate in ["wlan0", "eth0", "usb0"]:
            ip = _getIPv4ForInterface(candidate)
            if ip:
                iface = candidate
                break

    ipAddr = _getIPv4ForInterface(iface)

    ssid = ""
    signal = ""
    if iface.startswith("wlan"):
        ssid = _getSSID(iface)
        signal = _getSignalDbm(iface)

    internet = _internetOk()

    # Format For UI
    uiHost = _firstNonEmpty(hostname, "-") or "-"
    ssh = "Offline" if uiHost in ["-", ""] else f"ssh piradio@{uiHost}.local"

    return {
        "hostname": _firstNonEmpty(hostname, "-") or "-",
        "interface": _firstNonEmpty(iface, "-") or "-",
        "ipAddr": _firstNonEmpty(ipAddr, "-") or "-",
        "ssid": _firstNonEmpty(ssid, "-") or "-",
        "signal": _firstNonEmpty(signal, "-") or "-",
        "gateway": _firstNonEmpty(gateway, "-") or "-",
        "internet": "OK" if internet else "No",
        "ssh": ssh,
    }

