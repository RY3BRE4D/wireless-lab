# wireless-lab

A modular Raspberry Pi-based wireless experimentation platform featuring:

- Web-based control interface (Flask)
- IR capture and transmission
- NFC (PN532) interaction
- System and network monitoring
- WiFi management and fallback AP service
- Optional hardware-based panic button service

---

## Overview

**wireless-lab** is designed as a headless Linux system with a web UI that exposes hardware-level capabilities.

The system is built to be:
- modular
- reproducible
- hardware-aware
- service-driven (systemd)

---

## Features (Continuously Growing)

### Web UI
- Flask-based interface
- Central control panel for all modules and system features
- Feature toggles via config
- Real-time system/network info

### IR Support
- Capture raw IR signals
- Decode protocols
- Transmit signals (raw or encoded)

### NFC (PN532)
- Read NFC tags
- Inspect tag data
- I2C-based communication
- NDEF read (Type 2 first, MIFARE Classic best-effort fallback)
- NDEF write (text record and URL/URI record)
- MIFARE Classic Tools (best-effort, common-keys only)
  - Dump readable sectors (skips trailers by default)
  - Wipe to factory state (only sectors that authenticate)
- Type 2 Tag Tools (NTAG213, NTAG215, NTAG216, and other NFC Forum Type 2 tags)
  - Detect Type 2 tag (parses Capability Container, identifies NTAG variant from CC)
  - Dump tag (page-numbered rows, region-annotated: manufacturer / lock / CC / user / config)
  - Read NDEF (TLV walk from page 4)
  - Wipe user memory (empty NDEF TLV at page 4, or zero all user pages)
  - Format empty NDEF (writes valid CC if missing, then empty NDEF TLV)
  - Export dump (copy to clipboard, download as JSON or TXT)

### System Monitoring
- CPU / memory stats
- Network information
- Interface status
- Restart and Shutdown buttons (graceful reboot/poweroff from Stats page)

### WiFi Management
- Scan nearby WiFi networks (via NetworkManager)
- Add networks manually (SSID + password)
- Connect to networks on demand (with optional autoconnect + priority)
- Manage saved profiles (list, delete)
- Configure autoconnect behavior and connection priority
- Show/hide toggle on password fields

### Fallback Access Point
- Automatically start AP when no WiFi connection is available
- Hosted using NetworkManager (no hostapd/dnsmasq required)
- Provides local access to Web UI for recovery/setup
- Automatically shuts down when a valid WiFi connection is established

### Panic Button (Optional)
- GPIO-based hardware trigger
- OLED status display
- Runs as independent systemd service for reliability
- OLED clears cleanly on graceful shutdown (SIGTERM handler)

---

## Project Structure (basic)

```
wireless-lab/
├── app.py
├── config/
├── modules/
├── templates/
├── static/
├── services/
│   ├── wireless-lab.service
│   ├── wifiFallback.service
│   ├── wifiFallback.py
│   ├── panicButton.service
│   └── panicButton/
│       ├── panicButton.py
│       ├── requirements.txt
│       ├── font1.ttf
│       └── venv/
├── requirements.txt
├── system-deps.txt
└── venv/
```

---

## Installation

### 1. Install System Dependencies

```
sudo apt update
xargs -a system-deps.txt sudo apt install -y
```

---

### 2. Set Up Web UI

```
cd wireless-lab
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

### 3. Configure Features

Edit:

```
config/features.json
```

Enable or disable modules:
- IR
- NFC
- etc.

---

## Running the Web UI

You can test the webUI. If you want it to automatically start at boot, see Systemd Services

```
source venv/bin/activate
python app.py
```

Then open:

```
http://<device-ip>:5000
```

---

## Panic Button Service (Optional)

### Setup

```
cd services/panicButton
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt
```

### Install Service

```
sudo cp ../panicButton.service /etc/systemd/system/panicButton.service
sudo systemctl daemon-reload
sudo systemctl enable panicButton
sudo systemctl start panicButton
```

### Debug

```
journalctl -u panicButton -f
```

---

## Systemd Services

### wireless-lab

```
sudo cp services/wireless-lab.service /etc/systemd/system/wireless-lab.service
sudo systemctl daemon-reload
sudo systemctl enable wireless-lab
sudo systemctl start wireless-lab
```

### wifiFallback

```
sudo cp services/wifiFallback.service /etc/systemd/system/wifiFallback.service
sudo systemctl daemon-reload
sudo systemctl enable wifiFallback
sudo systemctl start wifiFallback
```

---

## WiFi Management Requirements

This project uses NetworkManager (`nmcli`) for WiFi control.

Required:
- network-manager
- sudo access (or run service as root)

Optional:
- AP mode support on WiFi hardware

---

## Notes

- The panic button service uses `--system-site-packages` to access GPIO libraries installed via apt
- The web UI uses an isolated venv
- Hardware access may require root or proper group permissions

---

## Future Plans
- More RF technology (sub-Ghz, 2.4Ghz, etc.)
- Modular app system (separate feature apps)
- Improved hardware abstraction
- Prebuilt system image

---

## License

This project is licensed under the MIT License.

See `LICENSE` for details.

Third-party dependencies are listed in `THIRD_PARTY_LICENSES.md`.
