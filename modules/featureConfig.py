import json
import os
import tempfile

DEFAULT_FEATURES = {
    "stats": {"enabled": True},
    "ir": {"enabled": True},
    "rfid_mfrc522": {
        "enabled": False,
        "spiBus": 0,
        "spiDevice": 0,
        "rstPin": 25,
        "csPin": 8,
    },
    "nfc_pn532": {
        "enabled": False,
        "interface": "i2c",
        "i2cBus": 1,
    },
}

def _deepMergeDicts(base, overlay):
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        return overlay
    out = dict(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deepMergeDicts(out[k], v)
        else:
            out[k] = v
    return out

def loadFeatures(configPath):
    if not os.path.exists(configPath):
        return dict(DEFAULT_FEATURES)

    try:
        with open(configPath, "r") as f:
            data = json.load(f) or {}
        return _deepMergeDicts(DEFAULT_FEATURES, data)
    except Exception:
        return dict(DEFAULT_FEATURES)

def saveFeatures(configPath, featuresDict):
    os.makedirs(os.path.dirname(configPath), exist_ok=True)

    fd, tmpPath = tempfile.mkstemp(prefix="features_", suffix=".json", dir=os.path.dirname(configPath))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(featuresDict, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmpPath, configPath)
        return True, None
    except Exception as e:
        try:
            if os.path.exists(tmpPath):
                os.remove(tmpPath)
        except Exception:
            pass
        return False, str(e)

def setFeatureEnabled(featuresDict, featureKey, enabledBool):
    if featureKey not in featuresDict or not isinstance(featuresDict[featureKey], dict):
        featuresDict[featureKey] = {"enabled": bool(enabledBool)}
    else:
        featuresDict[featureKey]["enabled"] = bool(enabledBool)

def isEnabled(featuresDict, featureKey):
    try:
        return bool(featuresDict.get(featureKey, {}).get("enabled", False))
    except Exception:
        return False
