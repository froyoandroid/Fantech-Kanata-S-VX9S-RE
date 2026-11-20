# Fantech Kanata S (VX9S) — Linux Driver & Reverse Engineering

Native Linux configuration utility, hardware protocol documentation, and `libratbag` driver patch for the **Fantech Kanata S (VX9S)** gaming mouse (`USB ID: 30fa:1040`).

Reverse-engineered from the official Windows software (`Gaming Mouse 3.0.exe` / `Kanata_S_VX9S_Software_V1.0_23111700.exe`).

## Repository Structure

```text
kanata-mouse/
├── kanata_ctl.py              # Native Linux CLI, TUI, and hardware DPI event daemon
├── docs/
│   └── protocol.md            # Reverse-engineered USB HID & EEPROM protocol specification
├── libratbag-fantech/         # C driver & patch for libratbag / Piper
│   ├── driver-fantech.c
│   ├── fantech-kanata-s-vx9s.device
│   └── add-fantech-kanata-s.patch
├── udev/
│   └── 99-kanata-mouse.rules  # Non-root hidraw access udev rule
└── vendor/                    # (git-ignored) Proprietary Windows software for RE reference
```

## Setup (udev Rule)

Grant non-root read/write access to the mouse HID interfaces:

```bash
sudo cp udev/99-kanata-mouse.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## Usage

### Hardware Status
```bash
./kanata_ctl.py status
```

### Interactive TUI
```bash
./kanata_ctl.py tui
```

### RGB Lighting Control (All 14 Hardware Modes)
```bash
# Custom static RGB color (0-255) + brightness (0-7, 7=brightest)
./kanata_ctl.py led-color 0 255 255 --brightness 7

# Built-in effects:
#   dpi_breathing, cycle_breathing, static, flowing, mono_water, comet,
#   neon, ambilight, flicker, star_trek, ripple, enraptured, button_response, off
./kanata_ctl.py led-mode neon --speed 4 --brightness 7
./kanata_ctl.py led-mode button_response
./kanata_ctl.py led-mode off
```

### DPI & Polling Rate
```bash
./kanata_ctl.py dpi 3          # Switch DPI stage (1-6: 400, 800, 1600, 2400, 3200, 4800)
./kanata_ctl.py rate 1000      # Set USB polling rate (125, 250, 500, 1000 Hz)
```

### Hardware DPI Button Listener Daemon
Because the mouse reports physical DPI button presses asynchronously via HID Input Report ID `0x06`, run the daemon to keep `status` in sync in real time:

```bash
./kanata_ctl.py daemon --install-service
systemctl --user daemon-reload
systemctl --user enable --now kanata-mouse.service
```

### Button Remapping & Safety Restore
```bash
./kanata_ctl.py button-remap 6 dpi_loop
./kanata_ctl.py restore-buttons
```

## Protocol Documentation

See [`docs/protocol.md`](docs/protocol.md) for the complete reverse-engineered HID report layout, command opcodes, EEPROM memory map, and disassembly offsets.
