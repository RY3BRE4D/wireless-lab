# wireless-lab

A Raspberry Pi that you control from a web browser, for experimenting with **infrared (IR) remotes**, **NFC tags**, and **Wi-Fi**.

You wire a few cheap modules to a Pi, install wireless-lab, and get a web page where you can capture a TV remote's signal and replay it, read and write NFC tags, check on the Pi's health, and manage its Wi-Fi — all without plugging in a keyboard or monitor. If the Pi can't find a known Wi-Fi network, it starts its own hotspot so you can always reach it.

> Built and tested on a **Raspberry Pi Zero 2 W** running **Raspberry Pi OS Lite (Bookworm, 64-bit)**. Other Pi models should work, but are untested.

---

## Contents

- [How It Works](#how-it-works)
- [What You Need](#what-you-need)
- [Wiring](#wiring)
- [Installation](#installation)
- [First Boot and Getting Connected](#first-boot-and-getting-connected)
- [Using the Web UI](#using-the-web-ui)
- [The Panic Button](#the-panic-button-optional)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)
- [Future Plans](#future-plans)
- [License](#license)

---

## How It Works

```
 Phone / laptop browser
          │  http://<pi-ip>:5000
          ▼
 ┌──────────── Raspberry Pi (software) ────────────┐     ┌──── Hardware ────────────────┐
 │                                                 │     │                              │
 │  wireless-lab.service (Flask web app, app.py)   │     │                              │
 │    ├── IR module     (ir-ctl, ir-keytable) ─────┼─────┼─► IR receiver  (GPIO 23)     │
 │    │                                       ─────┼─────┼─► IR LED / transmitter       │
 │    │                                            │     │                  (GPIO 18)   │
 │    ├── NFC module    (I²C bus 1) ───────────────┼─────┼─► PN532 NFC board            │
 │    ├── Wi-Fi module  (nmcli) ───────────────────┼─────┼─► Pi's built-in Wi-Fi        │
 │    └── Stats module  (psutil, vcgencmd)         │     │                              │
 │                                                 │     │                              │
 │  wifiFallback.service                           │     │                              │
 │    └── turns on a setup hotspot when offline    │     │                              │
 │                                                 │     │                              │
 │  panicButton.service (optional) ────────────────┼─────┼─► Push button  (GPIO 17)     │
 │                                  ───────────────┼─────┼─► OLED screen  (I²C bus 1)   │
 └─────────────────────────────────────────────────┘     └──────────────────────────────┘
```

The IR **receiver** picks up signals from remotes; the IR **LED** is the transmitter that sends them back out, like a remote would.

wireless-lab is three small programs that run in the background as **systemd services**, so they start automatically at boot:

| Service | What it does |
|---|---|
| `wireless-lab` | The web UI. A Flask app on port **5000** that talks to the hardware for you. |
| `wifiFallback` | Every 20 seconds it checks whether the Pi is on Wi-Fi. If not, it turns on a setup hotspot. Once the Pi joins a real network, the hotspot shuts off. |
| `panicButton` | *(Optional.)* Watches a physical button for reboot / shutdown / Wi-Fi recovery, and shows status on a small OLED screen. It runs separately so it still works if the web UI crashes. |

The web UI doesn't talk to hardware in any special way — it runs the same Linux tools you would use in a terminal (`ir-ctl`, `ir-keytable`, `nmcli`) and reads the PN532 over I²C with Adafruit's library. If something doesn't work in the UI, trying the equivalent command by hand is a good way to debug.

Each feature can be switched on or off in `config/features.json` (or from the **Modules** page), so you can run wireless-lab with only the hardware you actually have.

---

## What You Need

**Required**
- Raspberry Pi with Wi-Fi (Zero 2 W recommended) and a microSD card
- A computer to flash the SD card and SSH in

**For each feature you want**
| Feature | Hardware |
|---|---|
| IR receive | 38 kHz IR receiver module (3-pin, e.g. a TSOP-style receiver) |
| IR transmit | IR LED driven through a transistor (a GPIO pin alone can't supply enough current for good range) |
| NFC | PN532 NFC board, **set to I²C mode** (most boards have DIP switches or jumpers for this) |
| Panic button | A momentary push button |
| Status screen | 128×64 SSD1306 I²C OLED (address `0x3C`) |

Wi-Fi management and system stats need no extra hardware.

---

## Wiring

All pin numbers below are **BCM GPIO numbers**, with the physical header pin in parentheses. The **Pinout** page in the web UI shows a full Pi pinout diagram.

| Part | Connects to |
|---|---|
| IR receiver – signal | GPIO 23 (pin 16) |
| IR LED / transmitter – signal | GPIO 18 (pin 12) |
| PN532 – SDA | GPIO 2 / SDA (pin 3) |
| PN532 – SCL | GPIO 3 / SCL (pin 5) |
| OLED – SDA / SCL | Same as the PN532 (they share the I²C bus) |
| Panic button | GPIO 17 (pin 11) and any GND pin — no resistor needed, the internal pull-up is used |

Power each module from **3.3 V** (pin 1 or 17) and **GND**, unless your module's documentation says otherwise.

When everything is wired correctly, `i2cdetect -y 1` should show the PN532 at **`24`** and the OLED at **`3c`**.

---

## Installation

These steps assume a fresh Raspberry Pi OS Lite install where you've set the hostname, user, Wi-Fi, and SSH in Raspberry Pi Imager.

> **Username note:** the service files assume the user is **`piradio`** and the project lives in **`/home/piradio/wireless-lab`**. Using those names is the easiest path. If you use different ones, edit the paths and `User=` lines in the three files in `services/` before installing them.

### 1. Enable I²C and the IR overlays

Add these lines to the end of `/boot/firmware/config.txt`:

```
dtparam=i2c_arm=on
dtoverlay=gpio-ir,gpio_pin=23
dtoverlay=pwm-ir-tx,gpio_pin=18
```

Then reboot. Afterward you should see `/dev/i2c-1`, `/dev/lirc0` (receiver), and `/dev/lirc1` (transmitter).

### 2. Get the code and install system packages

```
cd ~
git clone https://github.com/RY3BRE4D/wireless-lab.git
cd wireless-lab
sudo apt update
grep -vE '^\s*(#|$)' system-deps.txt | xargs sudo apt install -y
```

### 3. Set up the web UI's Python environment

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Try it

```
python app.py
```

Open `http://<pi-ip>:5000` in a browser (find the IP with `hostname -I` on the Pi). Press `Ctrl+C` to stop it.

### 5. Install the services so it starts at boot

```
sudo cp services/wireless-lab.service services/wifiFallback.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wireless-lab wifiFallback
```

### 6. *(Optional)* Panic button + OLED

The panic button has its own environment. It uses `--system-site-packages` so it can use the GPIO libraries installed by apt.

```
cd ~/wireless-lab/services/panicButton
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt
deactivate
sudo cp ../panicButton.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now panicButton
```

### 7. *(Optional)* Free up memory on a headless Pi

A Pi Zero 2 W has only 512 MB of RAM. If nothing is plugged into the HDMI port, you can give most of the graphics memory back to Linux and turn off services wireless-lab doesn't use.

In `/boot/firmware/config.txt`, comment out the display driver and shrink the GPU memory split:

```
#dtoverlay=vc4-kms-v3d
gpu_mem=16
```

Disable services that aren't needed. This covers the cellular modem manager, Bluetooth, keyboard hotkeys, and the LIRC daemon; wireless-lab talks to the kernel's IR driver directly, so it doesn't need `lircd`.

```
sudo systemctl disable --now ModemManager bluetooth hciuart triggerhappy.socket triggerhappy lircd.socket lircd
sudo reboot
```

To undo, uncomment the `dtoverlay` line, remove `gpu_mem=16`, and `enable` the services again. Skip this step if you plan to use a screen, a camera, or Bluetooth.

### About permissions

The web UI runs as your normal user but needs to run `nmcli`, `ir-keytable`, and `systemctl reboot`/`poweroff` with `sudo`. It uses `sudo` **without a password prompt**, which is how Raspberry Pi OS sets up the first user by default. If you've changed that, those features will fail.

Access to `/dev/lirc*` comes from the `video` group, and I²C from the `i2c` group. The default Pi OS user is already in both.

---

## First Boot and Getting Connected

**If the Pi is on your Wi-Fi:** browse to `http://<pi-ip>:5000`. You can also try `http://wireless-lab.local:5000` (use whatever hostname you gave the Pi, plus `.local`). The `.local` name doesn't work on every device; see [Troubleshooting](#troubleshooting).

**If the Pi can't find a known network** (new location, changed password, etc.), within about 20 seconds it starts a hotspot:

| | |
|---|---|
| Network name | `wireless-lab` |
| Password | `RFRulez0` |
| Web UI | `http://10.42.0.1:5000` |

1. Connect your phone or laptop to the `wireless-lab` network.
2. Open the web UI and go to the **WiFi** page.
3. Scan, pick your network, enter the password, and connect.
4. The hotspot shuts off and the Pi joins your network. Reconnect your device to your normal Wi-Fi and open the web UI at the Pi's normal address.

> The Pi has one Wi-Fi radio, so it can either be on your network **or** run the hotspot — not both at once.

**Change the hotspot password** before taking the Pi anywhere public. See [Configuration](#configuration).

---

## Using the Web UI

The top of every page has links to each enabled feature. Pages for disabled features are hidden.

### Home
Shortcuts to each enabled feature.

### Stats
CPU, memory, temperature, and network status at a glance. The **Restart** and **Shutdown** buttons reboot or power off the Pi cleanly — use Shutdown before pulling the power.

### IR
Capture signals from any IR remote and send them back out.

- **RAW Capture** records the raw on/off timings of whatever the receiver sees. This works with any remote, even ones Linux doesn't recognize.
- **Decoded Capture** asks the Linux kernel to recognize the remote's protocol, and shows lines like `lirc protocol(nec): scancode = 0x45`.
- **Send** replays a signal through the IR LED, either as **RAW** timings or as **Decoded** protocol + scancode (e.g. `nec` + `0x45`). Click any captured line to fill in the send form automatically.

**Try this first:** press **Start** under Decoded Capture, point a TV remote at the receiver, and press a button. Click the line that appears, then **Send Decoded** with the IR LED pointed at the TV.

If Decoded Capture shows nothing for a remote, use RAW instead. Not every remote uses a protocol the kernel knows.

### NFC
Read, write, and inspect NFC tags with the PN532.

- **Scan Once / Probe** detects a tag and reports what kind it is.
- **Read** shows the tag's NDEF content (the standard format phones use).
- **Write** puts something useful on a tag. Pick from a menu (website, phone call, SMS, email, map location, Wi-Fi login, calendar event, app link, plain text, and more), fill in the friendly form, and tap a tag. Phones will act on the tag when they tap it.
- **MIFARE Classic tools** dump and wipe Classic cards. These are best-effort and only work on sectors that use common default keys.
- **Type 2 Tag Tools** are for NTAG213/215/216 and similar stickers and cards: detect the exact type, dump every page with labels, read, wipe, or format the tag, and export the dump as JSON or text.

**Try this first:** hold an NTAG sticker on the reader and press **Detect Type 2 Tag**, then **Read NDEF**. Next, write a **Web / HTTP** link and tap the sticker with your phone.

### WiFi
Manage the Pi's Wi-Fi using NetworkManager.

- **Scan** for nearby networks and **Connect** to one.
- **Save Network** to add a network that isn't in range right now (e.g. before moving the Pi somewhere new).
- Manage **saved networks**: delete them, or set their autoconnect and priority.
- **Setup AP** controls let you start or stop the setup hotspot by hand.

### Modules
Turn features on or off. This writes to `config/features.json`. **Restart** afterward for changes to fully take effect: either reboot (Stats → Restart), or run `sudo systemctl restart wireless-lab wifiFallback`. The Wi-Fi fallback service only runs while the WiFi module is enabled, so turning WiFi back on needs a restart too.

### Pinout
A Raspberry Pi pinout diagram, handy while wiring.

---

## The Panic Button (Optional)

A physical button for when you can't reach the web UI. **Hold** it and **let go** at the right time. The OLED counts up while you hold, so you can see which action you'll get.

| Action | What happens |
|---|---|
| Double-tap | Toggle the web UI on/off (start/stop `wireless-lab.service`) |
| Hold less than 1 s | Nothing |
| Hold 3–5 s | Reboot |
| Hold 5–8 s | Shut down |
| Hold 8–10 s | Wi-Fi recovery (restart NetworkManager and rescan) |
| Hold 10 s or more | Cancel, nothing happens |

When idle, the OLED shows the Pi's status, including its hostname, IP address, and Wi-Fi network.

Timings and pins are set at the top of `services/panicButton/panicButton.py`.

---

## Configuration

Settings live in `config/features.json`:

```json
{
  "ir":          { "enabled": true },
  "nfc_pn532":   { "enabled": true, "interface": "i2c", "i2cBus": 1 },
  "stats":       { "enabled": true },
  "wifi":        { "enabled": true },
  "rfid_mfrc522":{ "enabled": false }
}
```

Optional extra settings:

| Key | Default | Meaning |
|---|---|---|
| `nfc_pn532.i2cAddress` | `0x24` | PN532 I²C address |
| `wifi.setupSsid` | `wireless-lab` | Hotspot name |
| `wifi.setupPassword` | `RFRulez0` | Hotspot password (8+ characters) |

After editing, restart the services: `sudo systemctl restart wireless-lab wifiFallback`.

> `rfid_mfrc522` is a placeholder for a future RFID module and doesn't do anything yet.

---

## Troubleshooting

Logs are the first place to look:

```
journalctl -u wireless-lab -f
journalctl -u wifiFallback -f
journalctl -u panicButton -f
```

| Problem | Things to check |
|---|---|
| Can't find the Pi's address | Check your router's device list, run `hostname -I` on the Pi, or look at the OLED. |
| `wireless-lab.local` doesn't load, but the IP does | `.local` names rely on mDNS, which many Android phones and VPNs (e.g. Tailscale) don't support. Use the IP address, or disconnect the VPN. Plain `wireless-lab` (without `.local`) only works if your router provides local DNS names. |
| Web UI won't start | Run `python app.py` by hand inside the venv to see the error. If the NFC board isn't connected, disable NFC in `config/features.json`. |
| NFC: no tag found / init errors | Is the PN532 switched to **I²C** mode? Does `i2cdetect -y 1` show `24`? Check SDA/SCL aren't swapped. |
| IR: no `/dev/lirc0` or `/dev/lirc1` | Check the `dtoverlay` lines in `/boot/firmware/config.txt` and reboot. |
| IR: decoded capture shows nothing | Use RAW capture. That remote's protocol may not be supported by the kernel. |
| IR: sending doesn't affect the TV | Point the LED straight at the TV from close range. A bare LED on a GPIO pin is weak; a transistor driver helps a lot. |
| Wi-Fi buttons do nothing | The web UI needs passwordless `sudo` for `nmcli` (see [About permissions](#about-permissions)). |
| Hotspot never appears | Is `wifiFallback` running? `systemctl status wifiFallback` |
| OLED blank | Does `i2cdetect -y 1` show `3c`? Is `panicButton` running? |

> **Security note:** the web UI has no login. Anyone on the same network can use it, including rebooting the Pi or changing its Wi-Fi. Use it on networks you trust.

---

## Project Structure

```
wireless-lab/
├── app.py                 # Flask web app: routes + API (/api/...)
├── config/features.json   # Feature on/off switches and settings
├── modules/               # One file per feature (IR, NFC, Wi-Fi, stats, ...)
├── templates/             # HTML page for each feature
├── static/                # Images (pinout)
├── services/              # systemd unit files + background scripts
│   ├── wireless-lab.service
│   ├── wifiFallback.service / wifiFallback.py
│   └── panicButton.service / panicButton/
├── requirements.txt       # Python packages for the web UI
└── system-deps.txt        # apt packages
```

---

## Future Plans

- More RF technology (sub-GHz, 2.4 GHz, etc.)
- Modular app system (separate feature apps)
- Improved hardware abstraction
- Prebuilt system image

---

## License

MIT. See `LICENSE`. Third-party dependencies are listed in `THIRD_PARTY_LICENSES.md`.
