import json
import shlex
import subprocess
from typing import Any, Dict, List, Optional


def _runCmd(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["sudo"] + args, capture_output=True, text=True)


def _runCmdCheck(args: List[str]) -> str:
    result = subprocess.run(["sudo"] + args, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def networkManagerAvailable() -> bool:
    result = _runCmd(["systemctl", "is-active", "NetworkManager"])
    return result.returncode == 0 and result.stdout.strip() == "active"


def getWifiDevice() -> Optional[str]:
    result = _runCmd([
        "nmcli",
        "-t",
        "-f",
        "DEVICE,TYPE,STATE",
        "device",
        "status",
    ])
    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        device, devType, _state = parts[0], parts[1], parts[2]
        if devType == "wifi" and not device.startswith("p2p-dev-"):
            return device

    return None


def getCurrentStatus() -> Dict[str, Any]:
    wifiDevice = getWifiDevice()
    result = _runCmd([
        "nmcli",
        "-t",
        "-f",
        "DEVICE,TYPE,STATE,CONNECTION",
        "device",
        "status",
    ])

    status = {
        "ok": result.returncode == 0,
        "networkManager": networkManagerAvailable(),
        "wifiDevice": wifiDevice,
        "connected": False,
        "connectionName": "",
        "state": "",
    }

    if result.returncode != 0:
        status["error"] = result.stderr.strip() or "Unable To Read Device Status"
        return status

    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue

        device, devType, devState, connectionName = parts[0], parts[1], parts[2], parts[3]
        if device == wifiDevice and devType == "wifi":
            status["state"] = devState
            status["connectionName"] = connectionName if connectionName != "--" else ""
            status["connected"] = devState == "connected"
            break

    return status


def scanNetworks() -> Dict[str, Any]:
    rescan = _runCmd(["nmcli", "device", "wifi", "rescan"])
    # Even If Rescan Fails, Still Try To Read The Existing Scan Cache

    result = _runCmd([
        "nmcli",
        "-t",
        "-f",
        "IN-USE,SSID,SIGNAL,SECURITY,BSSID",
        "device",
        "wifi",
        "list",
    ])

    if result.returncode != 0:
        return {
            "ok": False,
            "error": result.stderr.strip() or "WiFi Scan Failed",
            "rescanOk": rescan.returncode == 0,
            "networks": [],
        }

    networks = []
    seen = set()

    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 5:
            continue

        inUse, ssid, signal, security, bssid = parts[0], parts[1], parts[2], parts[3], parts[4]

        # Skip Blank SSIDs For V1
        if not ssid.strip():
            continue

        key = (ssid, bssid)
        if key in seen:
            continue
        seen.add(key)

        networks.append({
            "inUse": inUse == "*",
            "ssid": ssid,
            "signal": int(signal) if signal.isdigit() else 0,
            "security": security,
            "bssid": bssid,
        })

    networks.sort(key=lambda item: (not item["inUse"], -item["signal"], item["ssid"].lower()))

    return {
        "ok": True,
        "rescanOk": rescan.returncode == 0,
        "networks": networks,
    }


def listSavedConnections() -> Dict[str, Any]:
    result = _runCmd([
        "nmcli",
        "-t",
        "-f",
        "NAME,TYPE,DEVICE,AUTOCONNECT,AUTOCONNECT-PRIORITY",
        "connection",
        "show",
    ])

    if result.returncode != 0:
        return {
            "ok": False,
            "error": result.stderr.strip() or "Unable To List Saved Connections",
            "connections": [],
        }

    connections = []
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 5:
            continue

        name, connType, device, autoConnect, priority = parts[0], parts[1], parts[2], parts[3], parts[4]
        if connType != "802-11-wireless" and connType != "wifi":
            continue

        connections.append({
            "name": name,
            "device": "" if device == "--" else device,
            "autoconnect": autoConnect.lower() == "yes",
            "priority": int(priority) if priority.lstrip("-").isdigit() else 0,
        })

    connections.sort(key=lambda item: item["name"].lower())

    return {
        "ok": True,
        "connections": connections,
    }


def connectToNetwork(
    ssid: str,
    password: str = "",
    saveProfile: bool = True,
    autoConnect: bool = True,
    priority: int = 0,
) -> Dict[str, Any]:
    if not ssid.strip():
        return {"ok": False, "error": "Missing SSID"}

    args = ["nmcli", "device", "wifi", "connect", ssid]

    if password.strip():
        args += ["password", password]

    # Save Profile Is The Default For Normal nmcli Connect
    result = _runCmd(args)
    if result.returncode != 0:
        return {
            "ok": False,
            "error": result.stderr.strip() or result.stdout.strip() or "Connect Failed",
        }

    connectionName = ssid

    if not saveProfile:
        # Best-Effort Cleanup If User Wanted Temporary Connect Only
        _runCmd(["nmcli", "connection", "modify", connectionName, "connection.autoconnect", "no"])
    else:
        _runCmd([
            "nmcli",
            "connection",
            "modify",
            connectionName,
            "connection.autoconnect",
            "yes" if autoConnect else "no",
        ])
        _runCmd([
            "nmcli",
            "connection",
            "modify",
            connectionName,
            "connection.autoconnect-priority",
            str(priority),
        ])

    return {
        "ok": True,
        "message": f"Connected To {ssid}",
        "connectionName": connectionName,
    }

def addNetwork(
    ssid: str,
    password: str = "",
    autoConnect: bool = True,
    priority: int = 0,
) -> dict:

    if not ssid.strip():
        return {"ok": False, "error": "SSID Required"}

    result = _runCmd([
        "nmcli", "connection", "add",
        "type", "wifi",
        "ifname", "*",
        "con-name", ssid,
        "ssid", ssid,
    ])

    if result.returncode != 0:
        return {
            "ok": False,
            "error": result.stderr.strip() or "Failed To Add Network",
        }

    if password.strip():
        _runCmd([
            "nmcli", "connection", "modify", ssid,
            "wifi-sec.key-mgmt", "wpa-psk"
        ])
        _runCmd([
            "nmcli", "connection", "modify", ssid,
            "wifi-sec.psk", password
        ])

    _runCmd([
        "nmcli", "connection", "modify", ssid,
        "connection.autoconnect", "yes" if autoConnect else "no"
    ])
    _runCmd([
        "nmcli", "connection", "modify", ssid,
        "connection.autoconnect-priority", str(priority)
    ])

    return {
        "ok": True,
        "message": f"Saved Network {ssid}"
    }

def setAutoconnect(connectionName: str, enabled: bool) -> Dict[str, Any]:
    result = _runCmd([
        "nmcli",
        "connection",
        "modify",
        connectionName,
        "connection.autoconnect",
        "yes" if enabled else "no",
    ])

    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or "Failed To Update Autoconnect"}

    return {"ok": True}


def setPriority(connectionName: str, priority: int) -> Dict[str, Any]:
    result = _runCmd([
        "nmcli",
        "connection",
        "modify",
        connectionName,
        "connection.autoconnect-priority",
        str(priority),
    ])

    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or "Failed To Update Priority"}

    return {"ok": True}


def deleteConnection(connectionName: str) -> Dict[str, Any]:
    result = _runCmd(["nmcli", "connection", "delete", connectionName])
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or "Delete Failed"}
    return {"ok": True}


def ensureSetupApProfile(setupSsid: str, setupPassword: str, priority: int = -50) -> Dict[str, Any]:
    # Recreate The Profile In A Predictable Way
    _runCmd(["nmcli", "connection", "delete", "wireless-lab-setup"])

    result = _runCmd([
        "nmcli", "connection", "add",
        "type", "wifi",
        "ifname", "*",
        "con-name", "wireless-lab-setup",
        "autoconnect", "no",
        "ssid", setupSsid,
    ])

    if result.returncode != 0:
        return {
            "ok": False,
            "error": result.stderr.strip() or "Failed To Create Setup AP Profile",
        }

    settings = [
        ["802-11-wireless.mode", "ap"],
        ["802-11-wireless.band", "bg"],
        ["ipv4.method", "shared"],
        ["ipv6.method", "ignore"],
        ["wifi-sec.key-mgmt", "wpa-psk"],
        ["wifi-sec.psk", setupPassword],
        ["connection.autoconnect", "no"],
        ["connection.autoconnect-priority", str(priority)],
    ]

    for key, value in settings:
        result = _runCmd(["nmcli", "connection", "modify", "wireless-lab-setup", key, value])
        if result.returncode != 0:
            return {
                "ok": False,
                "error": f"Failed To Set {key}: {result.stderr.strip() or result.stdout.strip()}",
            }

    return {"ok": True}


def startSetupAp() -> Dict[str, Any]:
    result = _runCmd(["nmcli", "connection", "up", "wireless-lab-setup"])
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or "Failed To Start Setup AP"}
    return {"ok": True}


def stopSetupAp() -> Dict[str, Any]:
    result = _runCmd(["nmcli", "connection", "down", "wireless-lab-setup"])
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or "Failed To Stop Setup AP"}
    return {"ok": True}
