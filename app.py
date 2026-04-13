import os
from flask import Flask, jsonify, request
from modules.ui import renderPage
from modules.systemStats import getStats
from modules.irModule import IRCaptureManager, IRDecodeManager
from modules.featureConfig import loadFeatures, saveFeatures, setFeatureEnabled, isEnabled
from modules.irInit import enableIrProtocols
from modules.pn532Module import PN532Module

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

# ---------- Routes ----------

@app.get("/")
def home():
    cards = []

    if isEnabled(features, "stats"):
        cards.append("""
        <div class="card">
          <div class="row">
            <div class="label">Stats UI</div>
            <div class="value"><a href="/stats">Open</a></div>
          </div>
          <div class="small muted">System Stats</div>
        </div>
        """)

    if isEnabled(features, "ir"):
        cards.append("""
        <div class="card">
          <div class="row">
            <div class="label">IR Lab</div>
            <div class="value"><a href="/ir">Open</a></div>
          </div>
          <div class="small muted">RAW + DECODED Monitor + Replay</div>
        </div>
        """)

    if isEnabled(features, "nfc_pn532"):
        cards.append("""
        <div class="card">
          <div class="row">
            <div class="label">NFC (PN532)</div>
            <div class="value"><a href="/nfc">Open</a></div>
          </div>
          <div class="small muted">Classification + Capability Probe + NDEF Read/Write</div>
        </div>
        """)

    cards.append("""
    <div class="card">
      <div class="row">
        <div class="label">Modules</div>
        <div class="value"><a href="/modules">Manage</a></div>
      </div>
      <div class="small muted">Enable/Disable Tech Without Deleting Code</div>
    </div>
    """)

    cards.append("""
    <div class="card">
      <div class="small muted">
        API Endpoints Live Under <b>/api</b>.
      </div>
    </div>
    """)

    return renderPage("Pi Zero Lab", "\n".join(cards), features=features)

@app.get("/modules")
def modulesPage():
    def rowToggle(key, label, desc):
        enabled = "checked" if isEnabled(features, key) else ""
        return f"""
        <div class="card">
          <div class="row">
            <div class="label">{label}</div>
            <div class="value">
              <input type="checkbox" id="t_{key}" {enabled} />
            </div>
          </div>
          <div class="small muted">{desc}</div>
        </div>
        """

    body = """
    <div class="card">
      <div class="small muted">
        Toggle Features On/Off. Changes Save To <span class="mono">config/features.json</span>.<br>
        <b>Restart Required</b> To Fully Apply Route/Manager Changes (Because systemd boots the app once).
      </div>
    </div>
    """

    body += rowToggle("stats", "Stats", "psutil-based CPU/RAM/Disk/Uptime UI")
    body += rowToggle("ir", "IR", "RAW capture (ir-ctl) + decoded capture (ir-keytable) + send RAW")
    body += rowToggle("rfid_mfrc522", "RFID (MFRC522)", "SPI RC522 reader (kept disabled until you need it)")
    body += rowToggle("nfc_pn532", "NFC (PN532)", "I2C PN532: classify + probe + NDEF read/write")

    body += """
    <div class="card">
      <div class="row">
        <div class="label">Save</div>
        <div class="value">
          <button class="pill" onclick="saveModules()">Save</button>
        </div>
      </div>
      <div class="small muted" id="saveStatus">-</div>
    </div>

    <script>
      async function apiPost(url, data) {
        const res = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data || {})
        });
        return await res.json();
      }

      async function saveModules() {
        const payload = {
          stats: document.getElementById('t_stats').checked,
          ir: document.getElementById('t_ir').checked,
          rfid_mfrc522: document.getElementById('t_rfid_mfrc522').checked,
          nfc_pn532: document.getElementById('t_nfc_pn532').checked
        };

        document.getElementById('saveStatus').textContent = 'Saving...';
        const r = await apiPost('/api/modules/save', payload);

        if (r.ok) {
          document.getElementById('saveStatus').textContent =
            'Saved. Restart Required: sudo systemctl restart webUI.service';
        } else {
          document.getElementById('saveStatus').textContent =
            'Save Error: ' + (r.error || 'Unknown');
        }
      }
    </script>
    """

    return renderPage("Pi Zero Lab - Modules", body, features=features)

@app.get("/pinout")
def pinoutPage():
    body = """
    <div class="card">
      <div class="row">
        <div class="label">Raspberry Pi Zero Pinout</div>
        <div class="value">
          <a class="pill" href="/static/images/piPinout.jpg" target="_blank" rel="noopener">Open Full Size</a>
        </div>
      </div>
      <div class="small muted">Click The Image To Open Full Size.</div>
    </div>

    <div class="card" style="display:flex; justify-content:center;">
      <a href="/static/images/piPinout.jpg" target="_blank" rel="noopener">
        <img
          src="/static/images/piPinout.jpg"
          alt="Pi Zero Pinout"
          style="width:100%; max-width:980px; height:auto; display:block; border-radius:12px;"
        />
      </a>
    </div>
    """
    return renderPage("Pi Zero Lab - Pinout", body, features=features)

# ---------- Stats Routes/API ----------

if isEnabled(features, "stats"):

    @app.get("/stats")
    def statsPage():
        body = """
        <div class="small" id="status">Loading...</div>

        <div class="card">
          <div class="row"><div class="label">CPU Usage</div><div class="value" id="cpuPercent">-</div></div>
          <div class="row"><div class="label">CPU Temp</div><div class="value" id="cpuTemp">-</div></div>
        </div>

        <div class="card">
          <div class="row"><div class="label">RAM</div><div class="value" id="ram">-</div></div>
          <div class="row"><div class="label">Disk</div><div class="value" id="disk">-</div></div>
        </div>

        <div class="card">
          <div class="row"><div class="label">Uptime</div><div class="value" id="uptime">-</div></div>
        </div>

        <div class="card">
          <div class="row"><div class="label">Hostname :</div><div class="value" id="netHostname">-</div></div>
          <div class="row"><div class="label">SSH      :</div><div class="value mono" id="netSsh">-</div></div>
          <div class="row"><div class="label">Interface:</div><div class="value" id="netInterface">-</div></div>
          <div class="row"><div class="label">IP Addr  :</div><div class="value mono" id="netIp">-</div></div>
          <div class="row"><div class="label">SSID     :</div><div class="value" id="netSsid">-</div></div>
          <div class="row"><div class="label">Signal   :</div><div class="value" id="netSignal">-</div></div>
          <div class="row"><div class="label">Gateway  :</div><div class="value mono" id="netGateway">-</div></div>
          <div class="row"><div class="label">Internet :</div><div class="value" id="netInternet">-</div></div>
        </div>

        <script>
          function setText(id, val) {
            const el = document.getElementById(id);
            if (!el) return;
            el.textContent = (val === undefined || val === null || val === '') ? '-' : val;
          }

          async function refreshStats() {
            try {
              const res = await fetch('/api/stats', { cache: 'no-store' });
              const s = await res.json();

              setText('cpuPercent', s.cpuPercent.toFixed(1) + '%');
              setText('cpuTemp', (s.cpuTempC === null) ? 'N/A' : s.cpuTempC.toFixed(1) + ' °C');
              setText('ram', s.ramPercent.toFixed(1) + '% (' + s.ramUsedMb + ' / ' + s.ramTotalMb + ' MB)');
              setText('disk', s.diskPercent.toFixed(1) + '% (' + s.diskUsedGb + ' / ' + s.diskTotalGb + ' GB)');
              setText('uptime', s.bootMinutes.toFixed(1) + ' minutes');

              const n = s.net || {};
              setText('netHostname', n.hostname);
              setText('netSsh', n.ssh);
              setText('netInterface', n.interface);
              setText('netIp', n.ipAddr);
              setText('netSsid', n.ssid);
              setText('netSignal', n.signal);
              setText('netGateway', n.gateway);
              setText('netInternet', n.internet);

              document.getElementById('status').textContent = 'OK';
            } catch (e) {
              document.getElementById('status').textContent = 'Error: ' + e;
            }
          }

          refreshStats();
          setInterval(refreshStats, 1500);
        </script>
        """
        return renderPage("Pi Zero Lab - Stats", body, features=features)

    @app.get("/api/stats")
    def apiStats():
        return jsonify(getStats())

# ---------- IR Routes/API ----------

if isEnabled(features, "ir"):

    @app.get("/ir")
    def irPage():
        body = """

        <div class="card" id="deviceCard">
          <div class="row">
            <div class="label">IR Devices</div>
            <div class="value small muted" id="devInfoTop">RX: - | TX: -</div>
          </div>
        </div>

        <div class="card">
          <div class="row">
            <div class="label">RAW Capture</div>
            <div class="value">
              <button class="pill" onclick="startCapture()">Start</button>
              <button class="pill" onclick="stopCapture()">Stop</button>
              <button class="pill" onclick="clearCapture()">Clear</button>
            </div>
          </div>

          <div class="small muted" id="irStatus">Idle</div>
          <div class="small muted" id="feedMeta">-</div>

          <textarea
            id="feedBox"
            readonly
            style="
              width:100%;
              height:260px;
              margin-top:10px;
              font-family: monospace;
              white-space: pre;
              resize: vertical;
            ">
          </textarea>
        </div>

        <div class="card">
          <div class="row">
            <div class="label">DECODED Capture</div>
            <div class="value">
              <button class="pill" onclick="startDecoded()">Start</button>
              <button class="pill" onclick="stopDecoded()">Stop</button>
              <button class="pill" onclick="clearDecoded()">Clear</button>
            </div>
          </div>

          <div class="small muted" id="decodedStatus">Loading...</div>
          <div class="small muted" id="decodedMeta">-</div>

          <textarea
            id="decodedBox"
            readonly
            style="
              width:100%;
              height:180px;
              margin-top:10px;
              font-family: monospace;
              white-space: pre;
              resize: vertical;
            ">
          </textarea>
        </div>

        <div class="card">
          <div class="row">
            <div class="label">Send IR</div>
            <div class="value" style="display:flex; gap:8px; align-items:center;">
              <button class="pill" id="modeRawBtn" onclick="setSendMode('raw')">RAW</button>
              <button class="pill" id="modeDecBtn" onclick="setSendMode('decoded')">Decoded</button>
            </div>
          </div>

          <div class="small muted" id="sendHelp">
            Choose RAW or Decoded.
          </div>

          <!-- RAW SEND -->
          <div id="sendRawWrap" style="margin-top:10px;">
            <div class="small muted">Tip: click a raw or decoded line to autofill.</div>
            <textarea id="rawText" style="width:100%; height:140px; margin-top:10px; font-family: monospace;"></textarea>
            <div style="margin-top:10px;">
              <button class="pill" onclick="sendRaw()">Send RAW</button>
            </div>
          </div>

          <!-- DECODED SEND -->
          <div id="sendDecodedWrap" style="margin-top:10px; display:none;">
            <div class="small muted">Tip: click a raw or decoded line to autofill.</div>

            <div class="row" style="margin-top:10px;">
              <div class="label">Protocol</div>
              <div class="value" style="font-weight:400;">
                <input id="decProtocol" style="width:160px;" placeholder="nec" />
              </div>
            </div>

            <div class="row">
              <div class="label">Scancode</div>
              <div class="value" style="font-weight:400;">
                <input id="decScancode" style="width:160px;" placeholder="0x45" />
              </div>
            </div>

            <div style="margin-top:10px;">
              <button class="pill" onclick="sendDecoded()">Send Decoded</button>
            </div>
          </div>

          <div class="small muted" id="sendResult" style="margin-top:10px;"></div>
        </div>


        <script>
          let pauseFeed = false;
          let sendMode = 'raw';

          async function apiPost(url, data) {
            const res = await fetch(url, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(data || {})
            });
            return await res.json();
          }

          async function refreshIr() {
            try {
              const res = await fetch('/api/ir/status', { cache: 'no-store' });
              const s = await res.json();

              document.getElementById('irStatus').textContent =
                s.running ? 'Capturing...' : 'Idle';

              document.getElementById('devInfoTop').textContent =
                'RX: ' + s.rxDev +
                ' | TX: ' + s.txDev +
                (s.lastError ? (' | Error: ' + s.lastError) : '');

              const lines = s.lines || [];
              document.getElementById('feedMeta').textContent =
                lines.length + ' lines';

              const feed = document.getElementById('feedBox');
              const newText = lines.length ? lines.join('\\n') : '';

              if (!pauseFeed && feed && feed.value !== newText) {
                const nearBottom =
                  (feed.scrollTop + feed.clientHeight) >= (feed.scrollHeight - 20);

                feed.value = newText;

                if (nearBottom) {
                  feed.scrollTop = feed.scrollHeight;
                }
              }

            } catch (e) {
              document.getElementById('irStatus').textContent =
                'Error: ' + e;
            }
          }

          async function refreshDecoded() {
            try {
              const res = await fetch('/api/ir/decoded/status', { cache: 'no-store' });
              const s = await res.json();

              const statusEl = document.getElementById('decodedStatus');
              const metaEl   = document.getElementById('decodedMeta');
              const box      = document.getElementById('decodedBox');

              if (statusEl) {
                statusEl.textContent = s.running ? 'Capturing...' : 'Idle';
              }

              const lines = s.lines || [];

              if (metaEl) {
                metaEl.textContent =
                  lines.length + ' lines' +
                  (s.lastError ? (' | Error: ' + s.lastError) : '');
              }

              if (box) {
                const newText = lines.length ? lines.join('\\n') : '';

                if (!pauseFeed && box.value !== newText) {
                  const nearBottom =
                    (box.scrollTop + box.clientHeight) >= (box.scrollHeight - 20);

                  box.value = newText;

                  if (nearBottom) {
                    box.scrollTop = box.scrollHeight;
                  }
                }
              }

            } catch (e) {
              const statusEl = document.getElementById('decodedStatus');
              if (statusEl) statusEl.textContent = 'Decoded Error: ' + e;
            }
          }

          async function startCapture() {
            await apiPost('/api/ir/start', {});
            refreshIr();
          }

          async function stopCapture() {
            await apiPost('/api/ir/stop', {});
            refreshIr();
          }

          async function clearCapture() {
            await apiPost('/api/ir/clear', {});
            refreshIr();
          }

          async function startDecoded() {
            await apiPost('/api/ir/decoded/start', {});
            refreshDecoded();
          }

          async function stopDecoded() {
            await apiPost('/api/ir/decoded/stop', {});
            refreshDecoded();
          }

          async function clearDecoded() {
            await apiPost('/api/ir/decoded/clear', {});
            refreshDecoded();
          }

          async function sendRaw() {
            document.getElementById('sendResult').textContent = 'Sending...';
            const rawText = document.getElementById('rawText').value;
            const r = await apiPost('/api/ir/sendRaw', { rawText });
            document.getElementById('sendResult').textContent =
              r.ok ? ('OK ' + (r.output || '')) : ('Error: ' + (r.error || 'Unknown'));
          }

          function setSendMode(mode) {
            sendMode = (mode === 'decoded') ? 'decoded' : 'raw';

            const rawBtn = document.getElementById('modeRawBtn');
            const decBtn = document.getElementById('modeDecBtn');
            const rawWrap = document.getElementById('sendRawWrap');
            const decWrap = document.getElementById('sendDecodedWrap');
            const help = document.getElementById('sendHelp');

            if (rawBtn) rawBtn.style.fontWeight = (sendMode === 'raw') ? '700' : '400';
            if (decBtn) decBtn.style.fontWeight = (sendMode === 'decoded') ? '700' : '400';

            if (rawWrap) rawWrap.style.display = (sendMode === 'raw') ? 'block' : 'none';
            if (decWrap) decWrap.style.display = (sendMode === 'decoded') ? 'block' : 'none';

            if (help) {
              help.textContent = (sendMode === 'raw')
                ? 'RAW mode: pulse/space or + / -'
                : 'Decoded mode: protocol + scancode';
            }
          }

          async function sendDecoded() {
            document.getElementById('sendResult').textContent = 'Sending...';
            const protocol = document.getElementById('decProtocol').value;
            const scancode = document.getElementById('decScancode').value;

            const r = await apiPost('/api/ir/sendDecoded', { protocol, scancode });
            document.getElementById('sendResult').textContent =
              r.ok ? ('OK ' + (r.output || '')) : ('Error: ' + (r.error || 'Unknown'));
          }

          const feedBox    = document.getElementById('feedBox');
          const decodedBox = document.getElementById('decodedBox');
          const rawBox     = document.getElementById('rawText');

          function parseDecodedLine(line) {
            // matches: "12:34:56  lirc protocol(nec): scancode = 0x45"
            const m = line.match(/protocol\\(([^)]+)\\):\\s*scancode\\s*=\\s*(0x[0-9a-fA-F]+|\\d+)/);
            if (!m) return null;
            return { protocol: m[1].trim().toLowerCase(), scancode: m[2].trim().toLowerCase() };
          }

          function stripTimestamp(line) {
            // "12:34:56  +9014 -4511 ..." -> "+9014 -4511 ..."
            const parts = (line || '').split(/\\s{2,}/);
            if (parts.length >= 2) return (parts[1] || '').trim();
            return (line || '').trim();
          }

          function stripTrailingGap(raw, gapThreshold = 25000) {
            if (!raw) return raw;
            const parts = raw.trim().split(/\\s+/);
            if (parts.length < 2) return raw;
            const last = parts[parts.length - 1];
            if (!last || last[0] !== '-') return raw;
            const gap = Number(last.slice(1));
            if (!Number.isFinite(gap)) return raw;
            if (gap >= gapThreshold) {
              parts.pop();
            }
            return parts.join(' ');
          }

          function currentLineFromTextarea(textarea) {
            const text = textarea.value || '';
            const pos = textarea.selectionStart || 0;
            const start = text.lastIndexOf('\\n', pos - 1) + 1;
            let end = text.indexOf('\\n', pos);
            if (end === -1) end = text.length;
            return text.substring(start, end).trim();
          }

          // Click decoded line -> autofill decoded send
          if (decodedBox) {
            decodedBox.addEventListener('click', () => {
              const line = currentLineFromTextarea(decodedBox);
              const parsed = parseDecodedLine(line);
              if (!parsed) return;

              const p = document.getElementById('decProtocol');
              const s = document.getElementById('decScancode');
              if (p) p.value = parsed.protocol;
              if (s) s.value = parsed.scancode;

              setSendMode('decoded');
            });
          }

          // Click RAW line -> autofill RAW send (space-separated + / -)
          if (feedBox) {
            feedBox.addEventListener('click', () => {
              const line = currentLineFromTextarea(feedBox);
              if (!line) return;

              let raw = stripTimestamp(line);
              raw = stripTrailingGap(raw);

              // Must look like + / - timing data
              if (!(raw.includes('+') && raw.includes('-'))) return;

              const rawTextEl = document.getElementById('rawText');
              if (rawTextEl) rawTextEl.value = raw.trim(); // trims trailing space issue

              setSendMode('raw');
            });
          }

          // Pause feed while interacting
          if (feedBox) {
            feedBox.addEventListener('mousedown', () => pauseFeed = true);
            feedBox.addEventListener('mouseup',   () => pauseFeed = false);
            feedBox.addEventListener('mouseleave',() => pauseFeed = false);
          }

          if (decodedBox) {
            decodedBox.addEventListener('mousedown', () => pauseFeed = true);
            decodedBox.addEventListener('mouseup',   () => pauseFeed = false);
            decodedBox.addEventListener('mouseleave',() => pauseFeed = false);
          }

          if (rawBox) {
            rawBox.addEventListener('focus', () => pauseFeed = true);
            rawBox.addEventListener('blur',  () => pauseFeed = false);
          }

          setSendMode('raw');
          refreshIr();
          refreshDecoded();
          setInterval(() => {
            refreshIr();
            refreshDecoded();
          }, 800);
        </script>
        """

        return renderPage("Pi Zero Lab - IR", body, features=features)

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
        body = """
        <div class="card">
          <div class="row">
            <div class="label">PN532</div>
            <div class="value">
              <button class="pill" onclick="refreshStatus()">Status</button>
            </div>
          </div>
          <div class="small muted mono" id="fwInfo">-</div>
        </div>

        <div class="card">
          <div class="row">
            <div class="label">Scan + Classify</div>
            <div class="value">
              <button class="pill" onclick="scanOnce()">Scan Once</button>
              <button class="pill" onclick="probe()">Probe</button>
            </div>
          </div>
          <div class="small muted" id="scanStatus">-</div>
          <pre class="mono" id="scanOut" style="white-space:pre-wrap; margin-top:10px;">-</pre>
        </div>

        <div class="card">
          <div class="row">
            <div class="label">Read NDEF</div>
            <div class="value">
              <button class="pill" onclick="readNdef()">Read</button>
            </div>
          </div>
          <div class="small muted">Best-Effort NDEF Read (Type 2 First, Then MIFARE Classic With Common Keys).</div>
          <div class="small muted" id="readStatus" style="margin-top:10px;">-</div>
        </div>

        <div class="card">
          <div class="row">
            <div class="label">Write NDEF Text</div>
            <div class="value">
              <button class="pill" onclick="writeText()">Write</button>
            </div>
          </div>
          <div class="small muted">Writes A Simple NDEF Text Record To A Writable Type 2 Tag. (Classic Fallback Uses Common Keys)</div>
          <input id="ndefText" style="width:100%; margin-top:10px;" placeholder="Hello From Pi Zero" />
          <div class="small muted" id="writeStatus" style="margin-top:10px;">-</div>
        </div>

        <div class="card">
          <div class="row">
            <div class="label">Write NDEF URL (Open URL)</div>
            <div class="value">
              <button class="pill" onclick="writeUrl()">Write</button>
            </div>
          </div>
          <div class="small muted">Writes A Single NDEF URI Record (No NFC Tools / NFC Tasks AAR Breadcrumbs).</div>
          <input id="ndefUrl" style="width:100%; margin-top:10px;" placeholder="http://wireless-lab.local/" />
          <div class="small muted" id="writeUrlStatus" style="margin-top:10px;">-</div>
        </div>

        <div class="card">
          <div class="row">
            <div class="label">MIFARE Classic Tools (Best-Effort)</div>
            <div class="value">
              <button class="pill" onclick="dumpClassic()">Dump</button>
              <button class="pill" onclick="wipeClassic()">Wipe</button>
            </div>
          </div>

          <div class="small muted">
            These Only Touch Sectors That Authenticate With Common Keys. Dump Skips Trailer Blocks By Default.
          </div>

          <div class="row" style="margin-top:10px;">
            <div class="label">Include Trailers (Dump)</div>
            <div class="value" style="font-weight:400;">
              <input type="checkbox" id="classicIncludeTrailers" />
            </div>
          </div>

          <div class="row">
            <div class="label">Reset Keys (Wipe)</div>
            <div class="value" style="font-weight:400;">
              <input type="checkbox" id="classicResetKeys" checked />
            </div>
          </div>

          <div class="row">
            <div class="label">Wipe Data (Wipe)</div>
            <div class="value" style="font-weight:400;">
              <input type="checkbox" id="classicWipeData" checked />
            </div>
          </div>

          <div class="small muted" id="classicStatus" style="margin-top:10px;">-</div>
        </div>

        <script>
          async function apiGet(url) {
            const res = await fetch(url, { cache: 'no-store' });
            return await res.json();
          }

          async function apiPost(url, data) {
            const res = await fetch(url, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(data || {})
            });
            return await res.json();
          }

          function showJson(elId, obj) {
            const el = document.getElementById(elId);
            if (!el) return;
            el.textContent = JSON.stringify(obj, null, 2);
          }

          async function refreshStatus() {
            const r = await apiGet('/api/nfc/status');
            const el = document.getElementById('fwInfo');
            if (el) el.textContent = JSON.stringify(r, null, 2);
          }

          async function scanOnce() {
            document.getElementById('scanStatus').textContent = 'Scanning...';
            const r = await apiGet('/api/nfc/scan');
            document.getElementById('scanStatus').textContent =
              r.ok ? (r.found ? 'Found Tag' : 'No Tag') : ('Error: ' + (r.error || 'Unknown'));
            showJson('scanOut', r);
          }

          async function probe() {
            document.getElementById('scanStatus').textContent = 'Probing...';
            const r = await apiGet('/api/nfc/probe');
            document.getElementById('scanStatus').textContent =
              r.ok ? (r.found ? ('Probe OK (riskScore=' + r.riskScore + ')') : 'No Tag') : ('Error: ' + (r.error || 'Unknown'));
            showJson('scanOut', r);
          }

          async function readNdef() {
            const st = document.getElementById('readStatus');
            if (st) st.textContent = 'Reading...';
            const r = await apiGet('/api/nfc/readNdef');
            if (st) st.textContent = r.ok ? (r.hasNdef ? 'NDEF Found' : 'No NDEF') : ('Error: ' + (r.error || 'Unknown'));
            showJson('scanOut', r);
          }

          async function writeText() {
            document.getElementById('writeStatus').textContent = 'Writing...';
            const text = document.getElementById('ndefText').value || '';
            const r = await apiPost('/api/nfc/writeNdefText', { text: text, language: 'en' });
            document.getElementById('writeStatus').textContent =
              r.ok ? ('OK' + (r.tagFamily ? (' (' + r.tagFamily + ')') : '')) : ('Error: ' + (r.error || 'Unknown'));
            showJson('scanOut', r);
          }

          async function writeUrl() {
            document.getElementById('writeUrlStatus').textContent = 'Writing...';
            const url = document.getElementById('ndefUrl').value || '';
            const r = await apiPost('/api/nfc/writeNdefUrl', { url: url });
            document.getElementById('writeUrlStatus').textContent =
              r.ok ? ('OK' + (r.tagFamily ? (' (' + r.tagFamily + ')') : '')) : ('Error: ' + (r.error || 'Unknown'));
            showJson('scanOut', r);
          }

          async function dumpClassic() {
            const st = document.getElementById('classicStatus');
            if (st) st.textContent = 'Dumping...';
            const includeTrailers = !!document.getElementById('classicIncludeTrailers').checked;
            const r = await apiPost('/api/nfc/dumpClassic', { includeTrailers: includeTrailers });
            if (st) st.textContent = r.ok ? (r.found ? 'Dump OK' : 'No Tag') : ('Error: ' + (r.error || 'Unknown'));
            showJson('scanOut', r);
          }

          async function wipeClassic() {
            const st = document.getElementById('classicStatus');
            if (st) st.textContent = 'Wiping...';
            const resetKeys = !!document.getElementById('classicResetKeys').checked;
            const wipeData  = !!document.getElementById('classicWipeData').checked;
            const r = await apiPost('/api/nfc/wipeClassic', { resetKeys: resetKeys, wipeData: wipeData });
            if (st) st.textContent = r.ok ? (r.found ? 'Wipe OK' : 'No Tag') : ('Error: ' + (r.error || 'Unknown'));
            showJson('scanOut', r);
          }

          refreshStatus();
        </script>
        """
        return renderPage("Pi Zero Lab - NFC (PN532)", body, features=features)

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

    # For making other tasks or even a custom URI writer
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

# ---------- Modules API ----------

@app.post("/api/modules/save")
def apiModulesSave():
    global features

    data = request.get_json(force=True) or {}

    setFeatureEnabled(features, "stats", bool(data.get("stats", False)))
    setFeatureEnabled(features, "ir", bool(data.get("ir", False)))
    setFeatureEnabled(features, "rfid_mfrc522", bool(data.get("rfid_mfrc522", False)))
    setFeatureEnabled(features, "nfc_pn532", bool(data.get("nfc_pn532", False)))

    ok, err = saveFeatures(FEATURES_PATH, features)
    if not ok:
        return jsonify({"ok": False, "error": err})

    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
