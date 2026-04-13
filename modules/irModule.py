import os
import time
import threading
import subprocess
import tempfile
from collections import deque
from .irInit import enableIrProtocols

class IRDecodeManager:
    """
    Uses kernel decode via: sudo ir-keytable -s rc0 -t
    Keeps a rolling buffer of decoded lines like:
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
                raise RuntimeError("enableIrProtocols() returned False")
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

                # Filter noise if you want (optional)
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
    Runs `ir-ctl -r` in the background and keeps a rolling buffer of raw lines.
    Works great for IR "Live Monitor" and replay workflows.
    """

    def __init__(self, rxDev=None, txDev=None, maxLines=200):
        # Allow environment overrides so you don't hardcode device nodes
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

        # Try to stop subprocess cleanly
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
        Accepts text in ir-ctl raw format, e.g.
          pulse 9000
          space 4500
          pulse 560
          space 560
          ...
        Writes to a temp file, then sends with ir-ctl.
        """
        rawText = (rawText or "").strip()
        if not rawText:
            return {"ok": False, "error": "No RAW text provided."}

        # Must contain pulse/space or +/-
        hasWords = ("pulse" in rawText) or ("space" in rawText) or ("+" in rawText and "-" in rawText)
        if not hasWords:
            return {"ok": False, "error": "RAW text doesn't look like ir-ctl format."}

        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False, prefix="ir_", suffix=".ir") as f:
                f.write(rawText)
                tmpPath = f.name

            # Send using ir-ctl
            # Note: `-s <file>` sends a raw file
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
        Sends a decoded scancode using ir-ctl, e.g.:
          ir-ctl -d /dev/lirc1 -S nec:0x45
        """
        protocol = (protocol or "").strip().lower()
        scancode = (scancode or "").strip().lower()

        if not protocol or not scancode:
            return {"ok": False, "error": "Protocol and scancode are required."}

        # Basic safety validation (avoid shell injection / weird stuff)
        # protocol should be like: nec, rc5, rc6, sony, panasonic, etc.
        for ch in protocol:
            if not (ch.isalnum() or ch in ("_", "-", ".")):
                return {"ok": False, "error": "Protocol contains invalid characters."}

        # scancode can be hex like 0x45 or decimal like 69
        isHex = scancode.startswith("0x") and all(c in "0123456789abcdef" for c in scancode[2:])
        isDec = scancode.isdigit()
        if not (isHex or isDec):
            return {"ok": False, "error": "Scancode must be hex (0x..) or decimal digits."}

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
        Capture with:
          sudo ir-ctl -d /dev/lirc0 -r
        This prints raw timings as a stream like:
          +171 -275 +160 -298 ...
        We store lines into a rolling deque.
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
                    # process ended
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
