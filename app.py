import os
import subprocess
from flask import Flask, jsonify, request, render_template, redirect, url_for
from modules.wifiManager import (
    getCurrentStatus,
    scanNetworks,
    listSavedConnections,
    connectToNetwork,
    addNetwork,
    deleteConnection,
    ensureSetupApProfile,
    startSetupAp,
    stopSetupAp,
)
from modules.ui import renderPage
from modules.systemStats import getStats
from modules.irModule import IRCaptureManager, IRDecodeManager
from modules.featureConfig import loadFeatures, saveFeatures, setFeatureEnabled, isEnabled
from modules.irInit import enableIrProtocols
from modules.pn532Module import PN532Module
from modules.type2TagTools import Type2TagTools

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FEATURES_PATH = os.path.join(BASE_DIR, "config", "features.json")

features = loadFeatures(FEATURES_PATH)

# ---- IR Managers ----
enableIrProtocols()

irMgr = None
irDec = None

if isEnabled(features, "ir"):
    irMgr = IRCaptureManager(maxLines=250)
    irDec = IRDecodeManager(rcName="rc0", maxLines=250)

# ---- NFC (PN532) Manager ----
pn532 = None
type2Tools = None

if isEnabled(features, "nfc_pn532"):
    cfg = features.get("nfc_pn532", {}) or {}
    addr = cfg.get("i2cAddress", 0x24)

    if isinstance(addr, str):
        try:
            addr = int(addr, 0)
        except Exception:
            addr = 0x24

    pn532 = PN532Module(i2cAddress=int(addr), debug=bool(cfg.get("debug", False)))
    pn532.init()
    type2Tools = Type2TagTools(pn532)

# ---------- Routes ----------

@app.get("/")
def home():
    body = render_template(
        "home.html",
        showStats=isEnabled(features, "stats"),
        showIr=isEnabled(features, "ir"),
        showNfc=isEnabled(features, "nfc_pn532"),
        showWifi=isEnabled(features, "wifi"),
    )
    return renderPage("Pi Lab", body, features=features)

@app.get("/modules")
def modulesPage():
    modules = [
        {"key": "stats",        "label": "Stats",          "desc": "psutil-Based CPU/RAM/Disk/Uptime UI",                             "enabled": isEnabled(features, "stats")},
        {"key": "ir",           "label": "IR",             "desc": "RAW Capture (ir-ctl) + Decoded Capture (ir-keytable) + Send RAW", "enabled": isEnabled(features, "ir")},
        {"key": "rfid_mfrc522", "label": "RFID (MFRC522)", "desc": "SPI RC522 Reader (Kept Disabled Until You Need It)",              "enabled": isEnabled(features, "rfid_mfrc522")},
        {"key": "nfc_pn532",    "label": "NFC (PN532)",    "desc": "I2C PN532: Classify + Probe + NDEF Read/Write",                   "enabled": isEnabled(features, "nfc_pn532")},
        {"key": "wifi",         "label": "WiFi",           "desc": "Scan + Connect + Saved Profiles + Setup AP",                      "enabled": isEnabled(features, "wifi")},
    ]
    body = render_template("modules.html", modules=modules)
    return renderPage("Pi Lab - Modules", body, features=features)

@app.get("/pinout")
def pinoutPage():
    body = render_template("pinout.html")
    return renderPage("Pi Lab - Pinout", body, features=features)

# ---------- Stats Routes/API ----------

if isEnabled(features, "stats"):

    @app.get("/stats")
    def statsPage():
        body = render_template("stats.html")
        return renderPage("Pi Lab - Stats", body, features=features)

    @app.get("/api/stats")
    def apiStats():
        return jsonify(getStats())

    @app.post("/api/system/restart")
    def apiSystemRestart():
        subprocess.Popen(["sudo", "systemctl", "reboot"])
        return jsonify({"ok": True})

    @app.post("/api/system/shutdown")
    def apiSystemShutdown():
        subprocess.Popen(["sudo", "systemctl", "poweroff"])
        return jsonify({"ok": True})

# ---------- IR Routes/API ----------

if isEnabled(features, "ir"):

    @app.get("/ir")
    def irPage():
        body = render_template("ir.html")
        return renderPage("Pi Lab - IR", body, features=features)

    @app.get("/api/ir/status")
    def apiIrStatus():
        return jsonify(irMgr.status())

    @app.post("/api/ir/start")
    def apiIrStart():
        irMgr.start()
        return jsonify({"ok": True})

    @app.post("/api/ir/stop")
    def apiIrStop():
        irMgr.stop()
        return jsonify({"ok": True})

    @app.post("/api/ir/clear")
    def apiIrClear():
        irMgr.clear()
        return jsonify({"ok": True})

    @app.post("/api/ir/sendRaw")
    def apiIrSendRaw():
        data = request.get_json(force=True) or {}
        rawText = data.get("rawText", "")
        return jsonify(irMgr.sendRawText(rawText))

    @app.post("/api/ir/sendDecoded")
    def apiIrSendDecoded():
        data = request.get_json(force=True) or {}
        protocol = data.get("protocol", "")
        scancode = data.get("scancode", "")
        return jsonify(irMgr.sendScancode(protocol, scancode))

    @app.get("/api/ir/decoded/status")
    def apiIrDecodedStatus():
        return jsonify(irDec.status())

    @app.post("/api/ir/decoded/start")
    def apiIrDecodedStart():
        irDec.start()
        return jsonify({"ok": True})

    @app.post("/api/ir/decoded/stop")
    def apiIrDecodedStop():
        irDec.stop()
        return jsonify({"ok": True})

    @app.post("/api/ir/decoded/clear")
    def apiIrDecodedClear():
        irDec.clear()
        return jsonify({"ok": True})

# ---------- NFC (PN532) Routes/API ----------

if isEnabled(features, "nfc_pn532"):

    @app.get("/nfc")
    def nfcPage():
        body = render_template("nfc.html")
        return renderPage("Pi Lab - NFC (PN532)", body, features=features)

    @app.get("/api/nfc/status")
    def apiNfcStatus():
        if not pn532:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        return jsonify(pn532.init())

    @app.get("/api/nfc/scan")
    def apiNfcScan():
        if not pn532:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        return jsonify(pn532.scanOnce(timeoutSeconds=0.35))

    @app.get("/api/nfc/probe")
    def apiNfcProbe():
        if not pn532:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        return jsonify(pn532.probeCapabilities())

    @app.get("/api/nfc/readNdef")
    def apiNfcReadNdef():
        if not pn532:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        return jsonify(pn532.tryReadNdef())

    @app.post("/api/nfc/writeNdefText")
    def apiNfcWriteNdefText():
        if not pn532:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        data = request.get_json(force=True) or {}
        text = (data.get("text") or "").strip()
        lang = (data.get("language") or "en").strip()
        if not text:
            return jsonify({"ok": False, "error": "Missing Text"})
        return jsonify(pn532.tryWriteNdefText(text=text, language=lang))

    @app.post("/api/nfc/writeNdefUrl")
    def apiNfcWriteNdefUrl():
        if not pn532:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        data = request.get_json(force=True) or {}
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"ok": False, "error": "Missing URL"})
        return jsonify(pn532.tryWriteNdefUri(uri=url))

    # For Making Other Tasks Or Even A Custom URI Writer
    """
    @app.post("/api/nfc/writeNdefUri")
    def apiNfcWriteNdefUri():
        if not pn532:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        data = request.get_json(force=True) or {}
        uri = (data.get("uri") or "").strip()
        if not uri:
            return jsonify({"ok": False, "error": "Missing URI"})
        return jsonify(pn532.tryWriteNdefUri(uri=uri))
    """

    @app.post("/api/nfc/dumpClassic")
    def apiNfcDumpClassic():
        if not pn532:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        data = request.get_json(force=True) or {}
        includeTrailers = bool(data.get("includeTrailers", False))
        return jsonify(pn532.dumpMifareClassic(includeTrailers=includeTrailers))

    @app.post("/api/nfc/wipeClassic")
    def apiNfcWipeClassic():
        if not pn532:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        data = request.get_json(force=True) or {}
        resetKeys = bool(data.get("resetKeys", True))
        wipeData = bool(data.get("wipeData", True))
        return jsonify(pn532.wipeMifareClassicToFactory(resetKeys=resetKeys, wipeData=wipeData))

    # ---- Type 2 / NTAG Tools (NTAG213/NTAG215/NTAG216 And Compatible) ----

    @app.post("/api/nfc/type2/detect")
    def apiNfcType2Detect():
        if not type2Tools:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        return jsonify(type2Tools.detectType2Tag())

    @app.post("/api/nfc/type2/dump")
    def apiNfcType2Dump():
        if not type2Tools:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        return jsonify(type2Tools.dumpType2Tag())

    @app.post("/api/nfc/type2/readNdef")
    def apiNfcType2ReadNdef():
        if not type2Tools:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        return jsonify(type2Tools.readType2Ndef())

    @app.post("/api/nfc/type2/wipeUser")
    def apiNfcType2WipeUser():
        if not type2Tools:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        data = request.get_json(force=True) or {}
        mode = (data.get("mode") or "ndef").strip().lower()
        return jsonify(type2Tools.wipeType2UserMemory(mode=mode))

    @app.post("/api/nfc/type2/formatEmpty")
    def apiNfcType2FormatEmpty():
        if not type2Tools:
            return jsonify({"ok": False, "error": "PN532 Disabled"})
        return jsonify(type2Tools.formatEmptyType2Ndef())

# ---------- Network Routes ----------

if isEnabled(features, "wifi"):

    @app.get("/wifi")
    def wifiPage():
        status = getCurrentStatus()
        scan = scanNetworks()
        saved = listSavedConnections()
        message = request.args.get("message", "")

        body = render_template(
            "wifi.html",
            status=status,
            scan=scan,
            saved=saved,
            message=message,
        )

        return renderPage("Pi Lab - WiFi", body, features=features)

    @app.post("/wifi/connect")
    def wifiConnect():
        ssid = (request.form.get("ssid") or "").strip()
        password = request.form.get("password") or ""
        autoConnect = bool(request.form.get("autoConnect"))
        priorityText = (request.form.get("priority") or "0").strip()

        try:
            priority = int(priorityText)
        except ValueError:
            priority = 0

        result = connectToNetwork(
            ssid=ssid,
            password=password,
            saveProfile=True,
            autoConnect=autoConnect,
            priority=priority,
        )

        msg = result.get("message") if result.get("ok") else result.get("error", "Connect Failed")
        return redirect(url_for("wifiPage", message=msg))

    @app.post("/wifi/add")
    def wifiAdd():
        ssid = (request.form.get("ssid") or "").strip()
        password = request.form.get("password") or ""
        autoConnect = bool(request.form.get("autoConnect"))
        priorityText = (request.form.get("priority") or "0").strip()

        try:
            priority = int(priorityText)
        except ValueError:
            priority = 0

        result = addNetwork(
            ssid=ssid,
            password=password,
            autoConnect=autoConnect,
            priority=priority,
        )

        msg = result.get("message") if result.get("ok") else result.get("error", "Failed")
        return redirect(url_for("wifiPage", message=msg))

    @app.post("/wifi/delete")
    def wifiDelete():
        connectionName = (request.form.get("connectionName") or "").strip()
        result = deleteConnection(connectionName)
        msg = "Connection Deleted" if result.get("ok") else result.get("error", "Delete Failed")
        return redirect(url_for("wifiPage", message=msg))

    @app.post("/wifi/setup-ap/ensure")
    def wifiEnsureSetupAp():
        wifiCfg = features.get("wifi", {})
        result = ensureSetupApProfile(
            setupSsid=wifiCfg.get("setupSsid", "wireless-lab-setup"),
            setupPassword=wifiCfg.get("setupPassword", "changeme123"),
            priority=int(wifiCfg.get("setupPriority", -50)),
        )
        msg = "Setup AP Profile Ready" if result.get("ok") else result.get("error", "Failed")
        return redirect(url_for("wifiPage", message=msg))

    @app.post("/wifi/setup-ap/start")
    def wifiSetupApStart():
        result = startSetupAp()
        msg = "Setup AP Started" if result.get("ok") else result.get("error", "Failed")
        return redirect(url_for("wifiPage", message=msg))

    @app.post("/wifi/setup-ap/stop")
    def wifiSetupApStop():
        result = stopSetupAp()
        msg = "Setup AP Stopped" if result.get("ok") else result.get("error", "Failed")
        return redirect(url_for("wifiPage", message=msg))

# ---------- Modules API ----------

@app.post("/api/modules/save")
def apiModulesSave():
    global features

    data = request.get_json(force=True) or {}

    setFeatureEnabled(features, "stats", bool(data.get("stats", False)))
    setFeatureEnabled(features, "ir", bool(data.get("ir", False)))
    setFeatureEnabled(features, "rfid_mfrc522", bool(data.get("rfid_mfrc522", False)))
    setFeatureEnabled(features, "nfc_pn532", bool(data.get("nfc_pn532", False)))
    setFeatureEnabled(features, "wifi", bool(data.get("wifi", False)))

    ok, err = saveFeatures(FEATURES_PATH, features)
    if not ok:
        return jsonify({"ok": False, "error": err})

    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
