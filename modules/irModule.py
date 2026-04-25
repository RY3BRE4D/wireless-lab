import os
import time
import threading
import subprocess
import tempfile
from collections import deque
from .irInit import enableIrProtocols

class IRDecodeManager:
    """
    Uses Kernel Decode Via: sudo ir-keytable -s rc0 -t
    Keeps A Rolling Buffer Of Decoded Lines Like:
      lirc protocol(nec): scancode = 0x45
    """

    def __init__(self, rcName="rc0", maxLines=200):
        self.rcName = rcName
        self.maxLines = maxLines

        self._lines = deque(maxlen=maxLines)
        self._lock = threading.Lock()

        self._thread = None
        self._stopEvent = threading.Event()
        self._proc = None

        self._running = False
        self._lastError = None
        self._startedAt = None

    def status(self):
        with self._lock:
            return {
                "running": self._running,
                "rcName": self.rcName,
                "lines": list(self._lines),
                "lastError": self._lastError,
                "startedAt": self._startedAt,
            }

    def start(self):
        # Enable Protocols Right Before Starting Decode
        try:
            ok = enableIrProtocols()
            if not ok:
                raise RuntimeError("enableIrProtocols() Returned False")
        except Exception as e:
            with self._lock:
                self._running = False
                self._lastError = f"[IR] Failed To Enable Protocols: {e}"
            return False

        with self._lock:
            if self._running:
                return True
            self._lines.clear()
            self._lastError = None
            self._stopEvent.clear()
            self._running = True
            self._startedAt = time.time()

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        with self._lock:
            if not self._running:
                return True
            self._running = False

        self._stopEvent.set()
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass
        return True

    def clear(self):
        with self._lock:
            self._lines.clear()
        return True

    def _loop(self):
        try:
            cmd = ["/usr/bin/stdbuf", "-oL", "-eL", "/usr/bin/ir-keytable", "-s", self.rcName, "-t"]
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            while not self._stopEvent.is_set():
                line = self._proc.stdout.readline() if self._proc and self._proc.stdout else ""
                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                # Filter Noise If You Want (Optional)
                # if "imon" in line and "0x7fffffff" in line: continue
                if "lirc protocol" not in line:
                    continue

                stamped = f"{time.strftime('%H:%M:%S')}  {line}"
                with self._lock:
                    self._lines.append(stamped)

        except Exception as e:
            with self._lock:
                self._lastError = str(e)
        finally:
            try:
                if self._proc and self._proc.poll() is None:
                    self._proc.terminate()
            except Exception:
                pass
            with self._lock:
                self._running = False


class IRCaptureManager:
    """
    Runs `ir-ctl -r` In The Background And Keeps A Rolling Buffer Of Raw Lines.
    Works Great For IR "Live Monitor" And Replay Workflows.
    """

    def __init__(self, rxDev=None, txDev=None, maxLines=200):
        # Allow Environment Overrides So You Don't Hardcode Device Nodes
        self.rxDev = rxDev or os.environ.get("IR_RX_DEV", "/dev/lirc0")
        self.txDev = txDev or os.environ.get("IR_TX_DEV", "/dev/lirc1")

        self.maxLines = maxLines
        self._lines = deque(maxlen=maxLines)
        self._lock = threading.Lock()

        self._thread = None
        self._stopEvent = threading.Event()
        self._proc = None

        self._running = False
        self._lastError = None
        self._startedAt = None

    def status(self):
        with self._lock:
            return {
                "running": self._running,
                "rxDev": self.rxDev,
                "txDev": self.txDev,
                "lines": list(self._lines),
                "lastError": self._lastError,
                "startedAt": self._startedAt,
            }

    def start(self):
        with self._lock:
            if self._running:
                return True

            self._lines.clear()
            self._lastError = None
            self._stopEvent.clear()
            self._running = True
            self._startedAt = time.time()

        self._thread = threading.Thread(target=self._captureLoop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        with self._lock:
            if not self._running:
                return True
            self._running = False

        self._stopEvent.set()

        # Try To Stop Subprocess Cleanly
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass

        return True

    def clear(self):
        with self._lock:
            self._lines.clear()
        return True

    def sendRawText(self, rawText):
        """
        Accepts Text In ir-ctl Raw Format, e.g.
          pulse 9000
          space 4500
          pulse 560
          space 560
          ...
        Writes To A Temp File, Then Sends With ir-ctl.
        """
        rawText = (rawText or "").strip()
        if not rawText:
            return {"ok": False, "error": "No RAW Text Provided."}

        # Must Contain pulse/space Or +/-
        hasWords = ("pulse" in rawText) or ("space" in rawText) or ("+" in rawText and "-" in rawText)
        if not hasWords:
            return {"ok": False, "error": "RAW Text Doesn't Look Like ir-ctl Format."}

        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False, prefix="ir_", suffix=".ir") as f:
                f.write(rawText)
                tmpPath = f.name

            # Send Using ir-ctl
            # Note: `-s <file>` Sends A Raw File
            cmd = ["ir-ctl", "-d", self.txDev, "-s", tmpPath]
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)

            return {"ok": True, "output": out.strip()}
        except subprocess.CalledProcessError as e:
            return {"ok": False, "error": (e.output or str(e)).strip()}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            try:
                if "tmpPath" in locals() and os.path.exists(tmpPath):
                    os.remove(tmpPath)
            except Exception:
                pass

    def sendScancode(self, protocol, scancode):
        """
        Sends A Decoded Scancode Using ir-ctl, e.g.:
          ir-ctl -d /dev/lirc1 -S nec:0x45
        """
        protocol = (protocol or "").strip().lower()
        scancode = (scancode or "").strip().lower()

        if not protocol or not scancode:
            return {"ok": False, "error": "Protocol And Scancode Are Required."}

        # Basic Safety Validation (Avoid Shell Injection / Weird Stuff)
        # protocol Should Be Like: nec, rc5, rc6, sony, panasonic, etc.
        for ch in protocol:
            if not (ch.isalnum() or ch in ("_", "-", ".")):
                return {"ok": False, "error": "Protocol Contains Invalid Characters."}

        # scancode Can Be Hex Like 0x45 Or Decimal Like 69
        isHex = scancode.startswith("0x") and all(c in "0123456789abcdef" for c in scancode[2:])
        isDec = scancode.isdigit()
        if not (isHex or isDec):
            return {"ok": False, "error": "Scancode Must Be Hex (0x..) Or Decimal Digits."}

        try:
            cmd = ["ir-ctl", "-d", self.txDev, "-S", f"{protocol}:{scancode}"]
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
            return {"ok": True, "output": (out or "").strip()}
        except subprocess.CalledProcessError as e:
            return {"ok": False, "error": (e.output or str(e)).strip()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _captureLoop(self):
        """
        Capture With:
          sudo ir-ctl -d /dev/lirc0 -r
        This Prints Raw Timings As A Stream Like:
          +171 -275 +160 -298 ...
        We Store Lines Into A Rolling Deque.
        """
        try:
            cmd = ["ir-ctl", "-d", self.rxDev, "-r"]
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            while not self._stopEvent.is_set():
                if not self._proc or not self._proc.stdout:
                    break

                line = self._proc.stdout.readline()
                if not line:
                    # Process Ended
                    break

                line = line.strip()
                if not line:
                    continue

                stamped = f"{time.strftime('%H:%M:%S')}  {line}"

                with self._lock:
                    self._lines.append(stamped)

        except Exception as e:
            with self._lock:
                self._lastError = str(e)

        finally:
            try:
                if self._proc and self._proc.poll() is None:
                    self._proc.terminate()
            except Exception:
                pass

            with self._lock:
                self._running = False
