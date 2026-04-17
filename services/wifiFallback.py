#!/usr/bin/env python3

import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.featureConfig import loadFeatures
from modules.wifiManager import getCurrentStatus, ensureSetupApProfile, startSetupAp, stopSetupAp

FEATURES_PATH = os.path.join(BASE_DIR, "config", "features.json")


def main():
    features = loadFeatures(FEATURES_PATH)
    wifiCfg = features.get("wifi", {})

    if not wifiCfg.get("enabled", False):
        print("WiFi Feature Disabled")
        return

    setupSsid = wifiCfg.get("setupSsid", "wireless-lab")
    setupPassword = wifiCfg.get("setupPassword", "RFRulez0")
    setupPriority = int(wifiCfg.get("setupPriority", -50))

    ensureSetupApProfile(setupSsid, setupPassword, setupPriority)

    while True:
        status = getCurrentStatus()

        if status.get("connected"):
            stopSetupAp()
        else:
            startSetupAp()

        time.sleep(20)


if __name__ == "__main__":
    main()
