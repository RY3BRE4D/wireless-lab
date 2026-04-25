#!/usr/bin/env python3
import os
import signal
import time
import subprocess
import threading
from time import monotonic

# Panic Button GPIO
panicGpioPin = 17

# Main App Service To Toggle
mainServiceName = "wireless-lab.service"

# Timing (Seconds)
minPressSeconds = 0.05
doubleTapWindowSeconds = 0.60

quickCancelHoldSeconds = 1.0
rebootHoldSeconds = 3.0
shutdownHoldSeconds = 5.0
wifiRecoveryHoldSeconds = 8.0
longCancelHoldSeconds = 10.0

# OLED Settings (You Can Set oledEnabled To False To Run Without OLED Indication)
oledEnabled = True
oledI2cPort = 1
oledI2cAddress = 0x3C
oledRef = None

'''
def runCmd(cmdList):
    # Run Command And Return (rc, stdout, stderr)
    proc = subprocess.run(cmdList, text=True, capture_output=True)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
'''

def runCmd(cmdList, timeout=1.5):
    # Run Command And Return (rc, stdout, stderr)
    try:
        proc = subprocess.run(
            cmdList,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"

def log(msg):
    # Log With Timestamp (systemd Captures stdout In journalctl)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def isServiceActive(serviceName):
    rc, out, _ = runCmd(["systemctl", "is-active", serviceName])
    return (rc == 0) and (out.strip() == "active")

def toggleMainService():
    if isServiceActive(mainServiceName):
        log(f"DoubleTap: Stopping {mainServiceName}")
        runCmd(["systemctl", "stop", mainServiceName])
    else:
        log(f"DoubleTap: Starting {mainServiceName}")
        runCmd(["systemctl", "start", mainServiceName])

def doShutdown():
    log("Hold: Clean Shutdown Requested")
    setLastAction("Shutdown...", showSeconds=6.0)
    showOledNow(["PANIC ACTION", "Shutdown...", "", "Please Wait..."], 0.6)
    runCmd(["systemctl", "poweroff"])

def doReboot():
    log("Hold: Reboot Requested")
    setLastAction("Rebooting...", showSeconds=6.0)
    showOledNow(["PANIC ACTION", "Rebooting...", "", "Please Wait..."], 0.6)
    runCmd(["systemctl", "reboot"])

def doWifiRecovery():
    # Recovery Without You Guessing SSID:
    # Restart NetworkManager + Rescan
    log("Hold: WiFi Recovery Requested (Restarting NetworkManager + Rescan)")
    runCmd(["systemctl", "restart", "NetworkManager"])
    time.sleep(2.0)
    runCmd(["nmcli", "dev", "wifi", "rescan"])

# ---- TTL Cache (For Expensive Command Calls) ----

ttlCacheLock = threading.Lock()
ttlCache = {}

def ttlGet(cacheKey, ttlSeconds, fn):
    now = monotonic()

    with ttlCacheLock:
        item = ttlCache.get(cacheKey)
        if item and now < item["until"]:
            return item["value"]

    val = fn()

    with ttlCacheLock:
        ttlCache[cacheKey] = {
            "value": val,
            "until": now + ttlSeconds
        }

    return val

# ---- OLED Support  ----

class OledUi:
    def __init__(self):
        self.device = None
        self.Image = None
        self.ImageDraw = None
        self.ImageFont = None
        self.font = None
        self.ready = False

    def init(self):
        if not oledEnabled:
            return

        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306
            from PIL import Image, ImageDraw, ImageFont

            serial = i2c(port=oledI2cPort, address=oledI2cAddress)
            device = ssd1306(serial)

            self.device = device
            self.Image = Image
            self.ImageDraw = ImageDraw
            self.ImageFont = ImageFont
            #self.font = ImageFont.load_default()
            self.font = ImageFont.truetype('font1.ttf', 12)
            self.ready = True

            log(f"OLED Ready (SSD1306 I2C Port={oledI2cPort} Address=0x{oledI2cAddress:02X})")

        except Exception as e:
            self.ready = False
            log(f"OLED Disabled (Init Failed): {e}")

    def renderLines(self, lines):
        if not self.ready:
            return

        try:
            w = self.device.width
            h = self.device.height

            image = self.Image.new("1", (w, h))
            draw = self.ImageDraw.Draw(image)

            y = 0
            #lineHeight = 11  # Default Font Height (Rough)
            bbox = self.font.getbbox("Ag")
            lineHeight = (bbox[3] - bbox[1]) + 1
            for line in lines[:6]:
                draw.text((0, y), line, font=self.font, fill=255)
                y += lineHeight

            self.device.display(image)

        except Exception as e:
            # Do Not Kill Panic Button If OLED Misbehaves
            self.ready = False
            log(f"OLED Disabled (Render Failed): {e}")

    def blank(self):
        if not self.ready:
            return
        try:
            self.ready = False
            time.sleep(0.05)
            self.device.hide()
        except Exception:
            pass

def getHostname():
    rc, out, _ = runCmd(["hostname"])
    return out if rc == 0 and out else "?"

def getIpAddr():
    # Prefer wlan0 IPv4
    rc, out, _ = runCmd(["nmcli", "-g", "IP4.ADDRESS", "dev", "show", "wlan0"])
    if rc == 0 and out:
        return out.splitlines()[0].split("/")[0]
    return "No IP"

def getSsid():
    # First Try: Kernel-Reported SSID (Works For Hidden Networks Too)
    rc, out, _ = runCmd(["iwgetid", "-r"])
    if rc == 0 and out.strip():
        return out.strip()

    # Fallback: NetworkManager Connection Name
    rc, out, _ = runCmd(["nmcli", "-g", "GENERAL.CONNECTION", "dev", "show", "wlan0"])
    if rc == 0 and out:
        val = out.strip()
        return val if val and val != "--" else "Hidden"

    return "?"

# You Can Fall Back To These Options For SSID And IP Retrieval If The Above Method Break Or Fail On Your Device
'''
def getSsid():
    rc, out, _ = runCmd(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"])
    if rc == 0 and out:
        for line in out.splitlines():
            # yes:<ssid>
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0].strip() == "yes":
                return parts[1].strip() or "Hidden"

    return "?"

def getIpAddr():
    rc, out, _ = runCmd(["nmcli", "-g", "IP4.ADDRESS[1]", "dev", "show", "wlan0"])
    if rc == 0 and out:
        return out.split("/")[0].strip()
    return "No IP"

def getSsid():
    rc, out, _ = runCmd(["nmcli", "-g", "GENERAL.CONNECTION", "dev", "show", "wlan0"])
    if rc == 0 and out:
        return out.strip() or "?"
    return "?"
'''

def getSignalDbm():
    # Best Source: iw Link Shows Real dBm When Connected
    rc, out, _ = runCmd(["iw", "dev", "wlan0", "link"])
    if rc == 0 and out:
        for line in out.splitlines():
            line = line.strip()
            # signal: -42 dBm
            if line.startswith("signal:"):
                try:
                    val = line.split("signal:", 1)[1].strip().split(" ", 1)[0]
                    return f"{val}dBm"
                except Exception:
                    pass

    # Fallback: nmcli Percent
    rc, out, _ = runCmd(["nmcli", "-t", "-f", "IN-USE,SIGNAL", "dev", "wifi"])
    if rc == 0 and out:
        for line in out.splitlines():
            if line.startswith("*:"):
                try:
                    pct = line.split(":", 1)[1].strip()
                    return f"{pct}%"
                except Exception:
                    pass

    return "-"

def getUptimeShort():
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            seconds = float(f.read().split()[0])
        total = int(seconds)
        hours = total // 3600
        mins = (total % 3600) // 60
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"
    except Exception:
        return "-"

# OLED Shared State
stateLock = threading.Lock()
buttonIsDown = False
pressStartTime = None
lastActionText = ""
lastActionUntil = 0.0

def showOledNow(lines, sleepSeconds=0.40):
    global oledRef
    try:
        if oledRef and oledRef.ready:
            oledRef.renderLines(lines)
            time.sleep(sleepSeconds)
    except Exception:
        pass

def setLastAction(text, showSeconds=1.5):
    global lastActionText, lastActionUntil
    with stateLock:
        lastActionText = text
        lastActionUntil = monotonic() + showSeconds

def getNextThresholdInfo(heldSeconds):
    # Return (actionName, secondsRemaining)
    if heldSeconds < quickCancelHoldSeconds:
        return ("CANCEL", quickCancelHoldSeconds - heldSeconds)
    if heldSeconds < rebootHoldSeconds:
        return ("REBOOT", rebootHoldSeconds - heldSeconds)
    if heldSeconds < shutdownHoldSeconds:
        return ("SHUTDOWN", shutdownHoldSeconds - heldSeconds)
    if heldSeconds < wifiRecoveryHoldSeconds:
        return ("WIFI RESET", wifiRecoveryHoldSeconds - heldSeconds)
    if heldSeconds < longCancelHoldSeconds:
        return ("CANCEL", longCancelHoldSeconds - heldSeconds)
    return ("CANCEL", 0.0)

def displayLoop(oled):
    hostname = getHostname()

    while True:
        try:
            now = monotonic()

            with stateLock:
                localButtonDown = buttonIsDown
                localPressStart = pressStartTime
                localLastActionText = lastActionText
                localLastActionUntil = lastActionUntil

            # Action Splash Screen
            if localLastActionText and now < localLastActionUntil:
                ipAddr = ttlGet("ipAddr", 10.0, getIpAddr)

                oled.renderLines([
                    "PANIC ACTION",
                    localLastActionText,
                    "",
                    f"Host: {hostname}",
                    f"IP: {ipAddr}",
                ])
                time.sleep(0.15)
                continue

            # Pressed Countdown Screen
            if localButtonDown and localPressStart is not None:
                heldSeconds = now - localPressStart
                nextAction, nextIn = getNextThresholdInfo(heldSeconds)

                # Compute Remaining For Each Threshold (Never Negative)
                shutdownIn = max(0.0, shutdownHoldSeconds - heldSeconds)
                rebootIn = max(0.0, rebootHoldSeconds - heldSeconds)
                wifiIn = max(0.0, wifiRecoveryHoldSeconds - heldSeconds)

                oled.renderLines([
                    f"HOLD {heldSeconds:0.1f}s",
                    f"Next: {nextAction}",
                    f"Rebt: {rebootIn:0.1f}s",
                    f"Shdn: {shutdownIn:0.1f}s",
                    f"WiFi: {wifiIn:0.1f}s",
                ])
                time.sleep(0.08)
                continue

            # Idle Screen (Slow Refresh)
            ssid = ttlGet("ssid", 15.0, getSsid)
            ipAddr = ttlGet("ipAddr", 10.0, getIpAddr)
            signal = ttlGet("signal", 3.0, getSignalDbm)
            webUiState = ttlGet(
                "webUiState",
                2.0,
                lambda: ("RUN" if isServiceActive(mainServiceName) else "STOP")
            )
            uptime = getUptimeShort()

            oled.renderLines([
                f"{hostname} {ipAddr}",
                f"WiFi {ssid}",
                f"Sig {signal}",
                f"WebUI {webUiState}",
                f"Up {uptime}",
            ])
            time.sleep(1.5)

        except Exception as e:
            # Do Not Kill Panic Button If OLED Loop Has Issues
            log(f"OLED Loop Error: {e}")
            time.sleep(2.0)

def main():
    log(f"Panic Button Starting On GPIO{panicGpioPin} (Main Service: {mainServiceName})")

    # Init OLED (Optional)
    oled = OledUi()
    oled.init()
    global oledRef
    oledRef = oled

    def handleShutdown(signum, frame):
        log("Shutdown Signal Received - Blanking OLED")
        if oledRef and oledRef.ready:
            oledRef.blank()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handleShutdown)
    signal.signal(signal.SIGINT, handleShutdown)

    # If oledEnabled Is Set To False, The Program Will Continue Without OLED And Related Functions/Modules
    if oled.ready:
        t = threading.Thread(target=displayLoop, args=(oled,), daemon=True)
        t.start()

    pressStart = None
    lastTapTime = None
    tapCount = 0

    # Use gpiozero If Available
    try:
        from gpiozero import Button
        btn = Button(panicGpioPin, pull_up=True, bounce_time=0.04)

        def onPressed():
            nonlocal pressStart
            global buttonIsDown, pressStartTime
            pressStart = monotonic()

            with stateLock:
                buttonIsDown = True
                pressStartTime = pressStart

        def onReleased():
            nonlocal pressStart, lastTapTime, tapCount
            global buttonIsDown, pressStartTime
            if pressStart is None:
                return

            heldSeconds = monotonic() - pressStart
            pressStart = None

            with stateLock:
                buttonIsDown = False
                pressStartTime = None

            if heldSeconds < minPressSeconds:
                return

            # Hold Actions (Longest First)
            if heldSeconds >= longCancelHoldSeconds:
                log(f"Button Released After {heldSeconds:.2f}s (No Action)")
                setLastAction("No Action")
                tapCount = 0
                lastTapTime = None
                return

            if heldSeconds >= wifiRecoveryHoldSeconds:
                log(f"Button Released After {heldSeconds:.2f}s (WiFi Recovery)")
                setLastAction("WiFi Reset")
                doWifiRecovery()
                tapCount = 0
                lastTapTime = None
                return

            if heldSeconds >= shutdownHoldSeconds:
                log(f"Button Released After {heldSeconds:.2f}s (Shutdown)")
                setLastAction("Shutdown")
                doShutdown()
                tapCount = 0
                lastTapTime = None
                return

            if heldSeconds >= rebootHoldSeconds:
                log(f"Button Released After {heldSeconds:.2f}s (Reboot)")
                setLastAction("Reboot")
                doReboot()
                tapCount = 0
                lastTapTime = None
                return

            if heldSeconds >= quickCancelHoldSeconds:
                log(f"Button Released After {heldSeconds:.2f}s (No Action)")
                setLastAction("No Action")
                tapCount = 0
                lastTapTime = None
                return

            # Tap Logic
            now = monotonic()
            if lastTapTime is None or (now - lastTapTime) > doubleTapWindowSeconds:
                tapCount = 0

            tapCount += 1
            lastTapTime = now

            # Evaluate Double-Tap Quickly
            if tapCount >= 2:
                log("DoubleTap Detected")
                setLastAction("Toggle WebUI")
                toggleMainService()
                tapCount = 0
                lastTapTime = None

        btn.when_pressed = onPressed
        btn.when_released = onReleased

        while True:
            time.sleep(1)

    except Exception as e:
        # Fallback To RPi.GPIO
        log(f"gpiozero Not Available Or Failed ({e}). Falling Back To RPi.GPIO")

        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(panicGpioPin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        def handleEdge(channel):
            nonlocal pressStart, lastTapTime, tapCount
            global buttonIsDown, pressStartTime

            level = GPIO.input(panicGpioPin)

            if level == 0:
                pressStart = monotonic()
                with stateLock:
                    buttonIsDown = True
                    pressStartTime = pressStart
                return

            if pressStart is None:
                return

            heldSeconds = monotonic() - pressStart
            pressStart = None

            with stateLock:
                buttonIsDown = False
                pressStartTime = None

            if heldSeconds < minPressSeconds:
                return

            if heldSeconds >= longCancelHoldSeconds:
                log(f"Button Released After {heldSeconds:.2f}s (No Action)")
                setLastAction("No Action")
                tapCount = 0
                lastTapTime = None
                return

            if heldSeconds >= wifiRecoveryHoldSeconds:
                log(f"Button Released After {heldSeconds:.2f}s (WiFi Recovery)")
                setLastAction("WiFi Reset")
                doWifiRecovery()
                tapCount = 0
                lastTapTime = None
                return

            if heldSeconds >= shutdownHoldSeconds:
                log(f"Button Released After {heldSeconds:.2f}s (Shutdown)")
                setLastAction("Shutdown")
                doShutdown()
                tapCount = 0
                lastTapTime = None
                return

            if heldSeconds >= rebootHoldSeconds:
                log(f"Button Released After {heldSeconds:.2f}s (Reboot)")
                setLastAction("Reboot")
                doReboot()
                tapCount = 0
                lastTapTime = None
                return

            if heldSeconds >= quickCancelHoldSeconds:
                log(f"Button Released After {heldSeconds:.2f}s (No Action)")
                setLastAction("No Action")
                tapCount = 0
                lastTapTime = None
                return

            now = monotonic()
            if lastTapTime is None or (now - lastTapTime) > doubleTapWindowSeconds:
                tapCount = 0

            tapCount += 1
            lastTapTime = now

            if tapCount >= 2:
                log("DoubleTap Detected")
                setLastAction("Toggle WebUI")
                toggleMainService()
                tapCount = 0
                lastTapTime = None

        GPIO.add_event_detect(panicGpioPin, GPIO.BOTH, callback=handleEdge, bouncetime=40)

        try:
            while True:
                time.sleep(1)
        finally:
            GPIO.cleanup()

if __name__ == "__main__":
    main()
