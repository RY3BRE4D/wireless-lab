# wireless-lab

A modular Raspberry Pi-based wireless experimentation platform featuring:

- Web-based control interface (Flask)
- IR capture and transmission
- NFC (PN532) interaction
- System and network monitoring
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
- Feature toggles via config
- Real-time system/network info

### IR Support
- Capture raw IR signals
- Decode protocols
- Transmit signals

### NFC (PN532)
- Read NFC tags
- Inspect tag data
- I2C-based communication

### System Monitoring
- CPU / memory stats
- Network information
- Interface status

### Panic Button (Optional)
- GPIO-based hardware trigger
- OLED status display
- Runs as independent systemd service

---

## Project Structure

```
webUI/
├── app.py
├── config/
├── modules/
├── static/
├── services/
│   ├── webUI.service
│   ├── panicButton.service
│   └── panicButton/
│       ├── panicButton.py
│       ├── requirements.txt
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
cd webUI
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

### Web UI

```
sudo cp services/webUI.service /etc/systemd/system/webUI.service
sudo systemctl daemon-reload
sudo systemctl enable webUI
sudo systemctl start webUI
```

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
