#!/usr/bin/env python3
"""
Fantech Kanata S (VX9S) - Linux Configuration & Control Utility
Reverse-engineered native Linux driver for Fantech Kanata S gaming mouse (USB ID: 30fa:1040).

Hardware Protocol:
  - Command 0x10: Button Remapping [0x07, 0x10, button_id, func_code, 0, 0, 0, 0]
  - Command 0x13: Live Runtime State (LED Mode, DPI Stage, Speed) [0x07, 0x13, dpi, b3, 0, 0, 0, 2]
  - Command 0x14: DPI Stage & Indicator Color / Polling Rate [0x07, 0x14, b2, b3, 0, 0, 0, 0]
  - Command 0x15: Color Preset Indices [0x07, 0x15, c0, c1, c2, c3, c4, c5]
  - Command 0x16: Flow Direction & Symmetry Flags [0x07, 0x16, 0, flags, 0, 0, 0, 0]
  - Command 0x18: EEPROM Memory Read & Write Interface
  - Command 0x20: EEPROM Commit / Flash Non-Volatile Memory
"""

import os
import sys
import glob
import time
import fcntl
import argparse
import curses
import json
import select

# Hardware constants
TARGET_VID = "30fa"
TARGET_PID = "1040"

REPORT_LEN = 8
REPORT_ID = 0x07

# Linux HIDRAW IOCTL codes
HIDIOCGFEAT = 0xc0084807
HIDIOCSFEAT = 0xc0084806

# State persistence path (mirrors Windows config.ini behavior)
STATE_DIR = os.path.expanduser("~/.config/kanata-mouse")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
PID_FILE = os.path.join(STATE_DIR, "daemon.pid")

def load_cached_state():
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def save_cached_state(data):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass

def is_daemon_running():
    if os.path.isfile(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return pid
        except (OSError, ValueError):
            return None
    return None

DPI_STEPS = [
    200, 400, 600, 800, 1000, 1200, 1600, 2000, 2400, 3200, 4000, 4800
]

# Factory default DPI values for the 6 hardware stages
DPI_STAGES = {
    1: 400,
    2: 800,
    3: 1600,
    4: 2400,
    5: 3200,
    6: 4800,
}

POLLING_RATES = {
    125:  (0xa0, 0x40),
    250:  (0x20, 0x00),
    500:  (0xf0, 0x40),
    1000: (0x60, 0x00),
}

# Verified firmware LED modes (reverse-engineered from Gaming Mouse 3.0.exe @ 0x42f567)
# (hw_mode_lo3, flag_b3, mode_bit3, display_name)
LED_MODES = {
    "dpi_breathing":    (0, 0, 0, "DPI Breathing Effect"),
    "cycle_breathing":  (1, 0, 0, "Cycle Breathing Effect"),
    "static":           (2, 0, 0, "Static Light On"),
    "flowing":          (3, 0, 0, "Flowing Water Effect"),
    "mono_water":       (4, 0, 0, "Mono Water Effect"),
    "comet":            (5, 0, 0, "Comet Streak"),
    "neon":             (6, 0, 0, "Neon"),
    "ambilight":        (7, 0, 0, "Ambilight"),
    "flicker":          (0, 0, 1, "Flicker"),
    "star_trek":        (1, 0, 1, "Star Trek"),
    "ripple":           (2, 0, 1, "Ripple"),
    "enraptured":       (3, 0, 1, "Enraptured"),
    "button_response":  (1, 1, 0, "Button Response"),
    "off":              (0, 0, 0, "LED Off"),
}

# Physical button names (1-6)
BUTTON_NAMES = {
    1: "Left Click",
    2: "Middle Click (Wheel)",
    3: "Right Click",
    4: "Backward (Side 1)",
    5: "Forward (Side 2)",
    6: "DPI Loop",
}

# Firmware button function codes (Command 0x10)
BUTTON_FUNCTIONS = {
    0:  "Disabled / No Function",
    1:  "Left Click",
    2:  "Middle Click",
    3:  "Right Click",
    4:  "Backward",
    5:  "Forward",
    6:  "DPI Loop",
    13: "Double Click",
    14: "Rapid Fire",
    15: "LED Effect Loop",
    16: "DPI +",
    17: "DPI -",
}

FUNCTION_NAME_TO_CODE = {
    "disabled":     0,
    "none":         0,
    "left":         1,
    "left_click":   1,
    "middle":       2,
    "middle_click": 2,
    "right":        3,
    "right_click":  3,
    "back":         4,
    "backward":     4,
    "forward":      5,
    "dpi":          6,
    "dpi_loop":     6,
    "double_click": 13,
    "rapid_fire":   14,
    "led_loop":     15,
    "dpi_up":       16,
    "dpi_down":     17,
}


class KanataMouse:
    def __init__(self, dev_path=None):
        self.dev_path = dev_path or self.find_device()
        if not self.dev_path:
            raise FileNotFoundError("Fantech Kanata S VX9S (30fa:1040) not found on /dev/hidraw*")

    @staticmethod
    def find_device():
        """Locates the vendor HID interface (Interface 1 with Report ID 7)."""
        for hidraw_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
            uevent_file = os.path.join(hidraw_path, "device", "uevent")
            if not os.path.isfile(uevent_file):
                continue
            with open(uevent_file, "r") as f:
                content = f.read().lower()
            if f"v0000{TARGET_VID}" in content and f"p0000{TARGET_PID}" in content:
                desc_file = os.path.join(hidraw_path, "device", "report_descriptor")
                if os.path.isfile(desc_file):
                    with open(desc_file, "rb") as df:
                        desc = df.read()
                        if b"\x85\x07" in desc:
                            return f"/dev/{os.path.basename(hidraw_path)}"
        return None

    def _open(self):
        try:
            return os.open(self.dev_path, os.O_RDWR)
        except PermissionError:
            print(f"Error: Permission denied accessing {self.dev_path}.")
            print("To fix, add udev rule or run with sudo:")
            print("  echo 'KERNEL==\"hidraw*\", ATTRS{idVendor}==\"30fa\", ATTRS{idProduct}==\"1040\", MODE=\"0666\"' | sudo tee /etc/udev/rules.d/99-kanata-mouse.rules")
            print("  sudo udevadm control --reload-rules && sudo udevadm trigger")
            sys.exit(1)

    def read_feature_report(self):
        fd = self._open()
        try:
            buf = bytearray([REPORT_ID] + [0] * (REPORT_LEN - 1))
            fcntl.ioctl(fd, HIDIOCGFEAT, buf, True)
            return buf
        finally:
            os.close(fd)

    def write_feature_report(self, payload):
        if len(payload) != REPORT_LEN:
            raise ValueError(f"Packet must be {REPORT_LEN} bytes")
        fd = self._open()
        try:
            buf = bytearray(payload)
            fcntl.ioctl(fd, HIDIOCSFEAT, buf, True)
        finally:
            os.close(fd)

    def read_eeprom_byte(self, addr):
        """Reads a byte from mouse onboard EEPROM using hardware handshake."""
        fd = self._open()
        try:
            addr_lo = addr & 0xff
            addr_hi = (addr >> 8) & 0xff

            # Step 1: Pre-read strobe (Function 0x38c00)
            pkt1 = bytearray([REPORT_ID, 0x18, 0x03, 0x00, addr_lo, addr_hi, 0, 0])
            fcntl.ioctl(fd, HIDIOCSFEAT, pkt1, True)
            time.sleep(0.002)

            # Step 2: Bus release
            pkt2 = bytearray([REPORT_ID, 0x18, 0x00, 0x00, addr_lo, addr_hi, 0, 0])
            fcntl.ioctl(fd, HIDIOCSFEAT, pkt2, True)
            time.sleep(0.002)

            # Step 3: Fetch command (Function 0x24e80)
            pkt3 = bytearray([REPORT_ID, 0x18, 0x05, 0x00, addr_lo, addr_hi, 0, 0])
            fcntl.ioctl(fd, HIDIOCSFEAT, pkt3, True)
            time.sleep(0.002)

            # Step 4: Read result buffer
            res = bytearray([REPORT_ID] + [0] * (REPORT_LEN - 1))
            fcntl.ioctl(fd, HIDIOCGFEAT, res, True)

            # Bus idle
            fcntl.ioctl(fd, HIDIOCSFEAT, pkt2, True)
            return res[1]
        finally:
            os.close(fd)

    def read_eeprom_block_8(self, addr):
        """Reads an 8-byte page from onboard EEPROM."""
        res = []
        for i in range(8):
            res.append(self.read_eeprom_byte(addr + i))
        return res

    def write_eeprom_block_8(self, addr, data8):
        """
        Writes an 8-byte page to onboard EEPROM using the verified hardware sequence.
        (Reverse-engineered from Function 0x24f50)
        """
        if len(data8) != 8:
            raise ValueError("Data block must be exactly 8 bytes")

        fd = self._open()
        try:
            addr_lo = addr & 0xff
            addr_hi = (addr >> 8) & 0xff

            # Step 1: Transmit each of the 8 bytes into the hardware write buffer
            for i in range(8):
                pkt = bytearray([REPORT_ID, 0x18, 0x03, i, addr_lo, addr_hi, data8[i], 7])
                fcntl.ioctl(fd, HIDIOCSFEAT, pkt, True)
                time.sleep(0.002)

            time.sleep(0.005)

            # Step 2: Strobe write execution (Opcode 0x09)
            strobe_pkt = bytearray([REPORT_ID, 0x18, 0x09, 0, addr_lo, addr_hi, 0, 7])
            fcntl.ioctl(fd, HIDIOCSFEAT, strobe_pkt, True)
            time.sleep(0.005)

            # Step 3: Bus release
            idle_pkt = bytearray([REPORT_ID, 0x18, 0x00, 0, addr_lo, addr_hi, 0, 0])
            fcntl.ioctl(fd, HIDIOCSFEAT, idle_pkt, True)
            time.sleep(0.005)
        finally:
            os.close(fd)

    def commit_eeprom(self):
        """
        Commits changes to non-volatile flash memory.
        (Reverse-engineered from Function 0x25000 & 0x16a24)
        """
        fd = self._open()
        try:
            # 1. Flash write unlock (Function 0x25000)
            pkt1 = bytearray([REPORT_ID, 0x18, 0x10, 0, 0, 0, 0, 0])
            fcntl.ioctl(fd, HIDIOCSFEAT, pkt1, True)
            time.sleep(0.005)

            # 2. Release strobe
            pkt2 = bytearray([REPORT_ID, 0x18, 0x00, 0, 0, 0, 0, 0])
            fcntl.ioctl(fd, HIDIOCSFEAT, pkt2, True)
            time.sleep(0.005)

            # 3. Flash commit Command 0x20
            pkt3 = bytearray([REPORT_ID, 0x20, 0, 0, 0, 0, 0, 0])
            fcntl.ioctl(fd, HIDIOCSFEAT, pkt3, True)
            time.sleep(0.020)
        finally:
            os.close(fd)

    def get_status(self):
        """Queries hardware report and EEPROM configuration state."""
        report = self.read_feature_report()
        # Byte 6: bit 1 is EEPROM size, bits 2-3 is profile index (0-3)
        profile_idx = (report[6] >> 2) & 0x03
        mem_flag = "4 KB" if (report[6] & 0x02) else "2 KB"

        # Check cached/live state from hardware event listener or previous switch
        cached = load_cached_state() or {}
        dpi_stage = cached.get("active_dpi_stage", 1)
        dpi_val = cached.get("active_dpi_value", DPI_STAGES.get(dpi_stage, 400))
        dpi_source = cached.get("source", "default")
        daemon_pid = is_daemon_running()

        # Read active polling rate from onboard EEPROM byte 0
        b0 = self.read_eeprom_byte(0x00)
        rate_code = b0 & 0x07
        rate_map = {0: 125, 1: 250, 2: 500, 3: 1000}
        rate_hz = rate_map.get(rate_code, 500)

        # Read LED configuration from EEPROM blocks 0x18, 0x20, 0x30, 0x38
        b18 = self.read_eeprom_block_8(0x18)
        b20 = self.read_eeprom_block_8(0x20)
        b30 = self.read_eeprom_block_8(0x30)
        b38 = self.read_eeprom_block_8(0x38)

        led_raw = b18[4]
        mode_lo3 = (led_raw >> 4) & 0x07
        flag_b3 = (led_raw >> 3) & 0x01
        speed = led_raw & 0x07
        mode_bit3 = (b30[3] >> 7) & 0x01
        hw_bright = (b38[7] >> 4) & 0x07
        brightness = max(0, 7 - hw_bright)
        # Active-low (common-anode) RGB nibbles: 0=255 (full on), 15=0 (off)
        static_rgb = (
            (15 - (b20[0] & 0x0f)) * 17,
            (15 - (b20[1] & 0x0f)) * 17,
            (15 - (b20[2] & 0x0f)) * 17,
        )

        # Map to friendly name
        mode_name = "unknown"
        mode_desc = f"Mode {(mode_bit3 << 3) | mode_lo3}"
        if (mode_lo3 == 0 and flag_b3 == 0 and mode_bit3 == 0 and speed == 7) or led_raw == 0x00:
            mode_name = "off"
            mode_desc = "LED Off"
        else:
            for k, (m_lo, fb3, mb3, desc) in LED_MODES.items():
                if k == "off":
                    continue
                if m_lo == mode_lo3 and fb3 == flag_b3 and mb3 == mode_bit3:
                    mode_name = k
                    mode_desc = desc
                    break

        return {
            "dpi_stage": dpi_stage,
            "dpi_value": dpi_val,
            "dpi_index": dpi_stage - 1,
            "dpi_source": dpi_source,
            "profile_index": profile_idx,
            "daemon_pid": daemon_pid,
            "polling_rate_hz": rate_hz,
            "memory_size": mem_flag,
            "led_mode": mode_name,
            "led_desc": mode_desc,
            "led_speed": speed,
            "led_brightness": brightness,
            "led_rgb": static_rgb,
            "led_raw": f"0x{led_raw:02x}",
            "raw_report": [f"0x{b:02x}" for b in report],
        }

    def set_dpi_stage(self, stage_1based):
        """Switches active DPI stage (1-6)."""
        if not 1 <= stage_1based <= 6:
            raise ValueError("DPI Stage must be between 1 and 6")
        idx = stage_1based - 1

        # EEPROM Block 0x18 Byte 0 stores 0x18 | (active_dpi_idx & 7)
        b18 = self.read_eeprom_block_8(0x18)
        b18[0] = 0x18 | (idx & 0x07)
        self.write_eeprom_block_8(0x18, b18)
        self.commit_eeprom()

        # Cache active DPI
        st = load_cached_state() or {}
        st["active_dpi_stage"] = stage_1based
        st["active_dpi_value"] = DPI_STAGES.get(stage_1based, 400)
        st["last_updated"] = time.time()
        st["source"] = "software"
        save_cached_state(st)

    def set_polling_rate(self, rate_hz):
        """Sets polling rate (125, 250, 500, 1000 Hz)."""
        if rate_hz not in POLLING_RATES:
            raise ValueError(f"Invalid rate: {rate_hz}. Choices: {list(POLLING_RATES.keys())}")
        b2, b3 = POLLING_RATES[rate_hz]
        self.write_feature_report([REPORT_ID, 0x14, b2, b3, 0, 0, 0, 0])

    def set_led_mode(self, mode_name, speed=3, brightness=7, color_mask=None, direction=0, symmetry=0):
        """
        Sets RGB lighting effect preset and persists to EEPROM.
        Supports all 14 hardware modes with exact EEPROM Blocks 0x18, 0x30, 0x38 and Command 0x13.
        """
        key = mode_name.lower().replace("-", "_").replace(" ", "_")
        if key not in LED_MODES:
            raise ValueError(f"Invalid mode '{mode_name}'. Choices: {list(LED_MODES.keys())}")

        m_lo, flag_b3, mode_bit3, desc = LED_MODES[key]
        speed = max(0, min(6, int(speed)))
        brightness = max(0, min(7, int(brightness)))
        hw_bright = 7 - brightness  # Hardware brightness is inverted: 0=brightest, 7=dimmest

        if key == "off":
            # LED Off: mode=0, flag_b3=0, speed=7 (0x87), mode_bit3=0
            target_b4 = 0x87
            mode_bit3 = 0
        elif key == "static":
            # Static Light On: mode=2, speed forced to 6 (0xa6)
            target_b4 = 0x80 | (m_lo << 4) | 6
        else:
            target_b4 = 0x80 | (m_lo << 4) | (flag_b3 << 3) | (speed & 0x07)

        if color_mask is None:
            color_mask = 0x01 if key == "static" else 0x7f
        color_mask &= 0x7f

        # 1. Update Block 0x30 Byte 3 bit 7 (mode bit 3 for modes 8..11)
        b30 = self.read_eeprom_block_8(0x30)
        b30[3] = (b30[3] & 0x7f) | (mode_bit3 << 7)
        self.write_eeprom_block_8(0x30, b30)

        # 2. Update Block 0x38 Byte 2 (symmetry/direction) & Byte 7 (brightness)
        b38 = self.read_eeprom_block_8(0x38)
        b38[2] = ((symmetry & 0x01) << 6) | ((direction & 0x03) << 4) | 0x04
        b38[7] = ((hw_bright & 0x07) << 4) | (b38[7] & 0x0f)
        self.write_eeprom_block_8(0x38, b38)

        # 3. Update Block 0x18 Byte 4 (mode+speed) & Byte 5 (0x80 | 7-color mask)
        b18 = self.read_eeprom_block_8(0x18)
        b18[4] = target_b4
        b18[5] = 0x80 | color_mask
        self.write_eeprom_block_8(0x18, b18)

        # 4. Commit to non-volatile flash
        self.commit_eeprom()

        # 5. Strobe live state via Command 0x13 (matches Gaming Mouse 3.0.exe @ 0x445110)
        self.write_feature_report([
            REPORT_ID,
            0x13,
            0x80 | color_mask,
            target_b4,
            b38[2],
            hw_bright & 0x07,
            (mode_bit3 << 7),
            0x02,
        ])

    def set_led_color(self, r, g, b, brightness=7):
        """
        Sets custom static RGB color (0-255):
        - Mode 2 (Static Light On) displays the active DPI stage color (LOW nibbles of 0x20..0x31)
          and responds live to Command 0x14.
        - Also updates User Color Slot 0 (HIGH nibbles of 0x20..0x22) for effect modes.
        """
        r = max(0, min(255, int(r)))
        g = max(0, min(255, int(g)))
        b = max(0, min(255, int(b)))

        # Convert 8-bit RGB to inverted 4-bit active-low nibbles (0=full on, 15=off)
        rn = 15 - ((r >> 4) & 0x0f)
        gn = 15 - ((g >> 4) & 0x0f)
        bn = 15 - ((b >> 4) & 0x0f)
        rgb_nibbles = [rn, gn, bn]

        # Read EEPROM blocks 0x20, 0x28, 0x30 (covers 0x20..0x31 = 6 slots x 3 bytes)
        blocks = {
            0x20: self.read_eeprom_block_8(0x20),
            0x28: self.read_eeprom_block_8(0x28),
            0x30: self.read_eeprom_block_8(0x30),
        }

        # Update LOW nibbles (DPI/Static color) for all 6 DPI stages so color applies
        # regardless of which hardware DPI stage is currently active
        for slot in range(6):
            for i, nib in enumerate(rgb_nibbles):
                addr = 0x20 + slot * 3 + i
                blk_addr = addr & ~0x07
                blk_off = addr & 0x07
                blocks[blk_addr][blk_off] = (blocks[blk_addr][blk_off] & 0xf0) | nib

        # Also update HIGH nibbles of User Color Slot 0 (0x20..0x22)
        for i, nib in enumerate(rgb_nibbles):
            blocks[0x20][i] = (nib << 4) | (blocks[0x20][i] & 0x0f)

        for blk_addr, data8 in blocks.items():
            self.write_eeprom_block_8(blk_addr, data8)

        # Activate Static Light On (Mode 2) and commit EEPROM
        self.set_led_mode("static", speed=6, brightness=brightness, color_mask=0x01)

        # Send live Command 0x14 for current DPI stage: [0x07, 0x14, (dpi<<5)|G, (R<<4)|B, 0, 0, 0, 0]
        cached = load_cached_state() or {}
        dpi_idx = max(0, min(5, cached.get("active_dpi_stage", 1) - 1))
        self.write_feature_report([
            REPORT_ID,
            0x14,
            ((dpi_idx & 0x07) << 5) | gn,
            (rn << 4) | bn,
            0x00, 0x00, 0x00, 0x00,
        ])

    def remap_button(self, button_id, func_name_or_code):
        """
        Remaps a mouse button using Command 0x10.
        button_id: 1=Left, 2=Middle, 3=Right, 4=Back, 5=Forward, 6=DPI
        """
        if not 1 <= button_id <= 6:
            raise ValueError(f"Button ID must be 1 to 6. Got: {button_id}")

        if isinstance(func_name_or_code, str):
            key = func_name_or_code.lower().replace("-", "_").replace(" ", "_")
            if key not in FUNCTION_NAME_TO_CODE:
                raise ValueError(f"Unknown function '{func_name_or_code}'. Available: {list(FUNCTION_NAME_TO_CODE.keys())}")
            func_code = FUNCTION_NAME_TO_CODE[key]
        else:
            func_code = int(func_name_or_code)
            if func_code not in BUTTON_FUNCTIONS:
                raise ValueError(f"Unknown function code: {func_code}")

        # Safety warning if disabling Button 1 (Left click)
        if button_id == 1 and func_code == 0:
            print("WARNING: Disabling Left Mouse Button! Run 'restore-buttons' to re-enable.")

        # Command 0x10: [0x07, 0x10, button_id, func_code, 0, 0, 0, 0]
        self.write_feature_report([REPORT_ID, 0x10, button_id, func_code, 0, 0, 0, 0])

    def restore_default_buttons(self):
        """Restores factory default button bindings (ensures Button 1 is Left Click)."""
        defaults = {
            1: 1,  # Button 1 -> Left Click
            2: 2,  # Button 2 -> Middle Click
            3: 3,  # Button 3 -> Right Click
            4: 4,  # Button 4 -> Backward
            5: 5,  # Button 5 -> Forward
            6: 6,  # Button 6 -> DPI Loop
        }
        for btn, fn in defaults.items():
            self.write_feature_report([REPORT_ID, 0x10, btn, fn, 0, 0, 0, 0])
            time.sleep(0.005)


# ==============================================================================
# Interactive Terminal User Interface (TUI)
# ==============================================================================
def safe_addstr(win, y, x, text, attr=0):
    try:
        h, w = win.getmaxyx()
        if 0 <= y < h and 0 <= x < w:
            avail = max(0, w - x - 1)
            if avail > 0:
                win.addstr(y, x, text[:avail], attr)
    except curses.error:
        pass


def run_tui(stdscr, mouse):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_MAGENTA, -1)
    curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)

    mode_keys = [
        "neon",
        "ambilight",
        "flowing",
        "mono_water",
        "comet",
        "cycle_breathing",
        "dpi_breathing",
        "static",
        "flicker",
        "star_trek",
        "ripple",
        "enraptured",
        "button_response",
        "off",
    ]
    curr_led_idx = 0
    rates = [125, 250, 500, 1000]
    curr_rate_idx = 2  # default 500 Hz
    curr_dpi = 1
    status_msg = "Ready. Use Hotkeys below to configure mouse."

    # Always ensure default buttons are active so mouse click works perfectly
    try:
        mouse.restore_default_buttons()
    except Exception:
        pass

    # Read current state if possible
    try:
        st = mouse.get_status()
        curr_dpi = st["dpi_stage"]
        if st["polling_rate_hz"] in rates:
            curr_rate_idx = rates.index(st["polling_rate_hz"])
        if st["led_mode"] in mode_keys:
            curr_led_idx = mode_keys.index(st["led_mode"])
    except Exception:
        pass

    color_palette = [
        (255, 0, 0, "Red"),
        (0, 255, 0, "Green"),
        (0, 0, 255, "Blue"),
        (0, 255, 255, "Cyan"),
        (255, 255, 0, "Yellow"),
        (255, 0, 255, "Purple"),
        (255, 255, 255, "White"),
    ]
    curr_color_idx = 0

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        if h < 16 or w < 50:
            safe_addstr(stdscr, 1, 2, "Terminal too small!", curses.color_pair(3) | curses.A_BOLD)
            safe_addstr(stdscr, 2, 2, f"Size: {w}x{h}. Please enlarge window or press 'q' to quit.")
            stdscr.refresh()
            ch = stdscr.getch()
            if ch in (ord('q'), ord('Q'), 27):
                break
            continue

        # Title
        title = " Fantech Kanata S (VX9S) - Linux Control "
        safe_addstr(stdscr, 0, max(0, (w - len(title)) // 2), title, curses.color_pair(5) | curses.A_BOLD)

        # Device info
        dev_info = f"Node: {mouse.dev_path} | VID:PID 30fa:1040"
        safe_addstr(stdscr, 1, max(0, (w - len(dev_info)) // 2), dev_info, curses.color_pair(1))

        # Status summary
        col = max(2, (w - 60) // 2)
        safe_addstr(stdscr, 3, col, "┌──────────────── Current Status ───────────────┐", curses.color_pair(1))
        safe_addstr(stdscr, 4, col, f"│ DPI     : Stage {curr_dpi}/6 ({DPI_STAGES.get(curr_dpi, 400)} DPI){'':<20} │")
        safe_addstr(stdscr, 5, col, f"│ Polling : {rates[curr_rate_idx]} Hz{'':<31} │")
        mode_desc = LED_MODES[mode_keys[curr_led_idx]][3]
        safe_addstr(stdscr, 6, col, f"│ Lighting: {mode_desc:<35} │")
        safe_addstr(stdscr, 7, col, f"│ Buttons : Factory Default (Left/Mid/Right/Back/Fwd/DPI)│")
        safe_addstr(stdscr, 8, col, "└───────────────────────────────────────────────┘", curses.color_pair(1))

        # Controls list
        safe_addstr(stdscr, 10, col, "Hotkeys:", curses.color_pair(3) | curses.A_BOLD)
        safe_addstr(stdscr, 11, col, " [1-6]    Switch DPI Level (1 to 6)", curses.color_pair(2))
        safe_addstr(stdscr, 12, col, " [R]      Cycle Polling Rate (125 -> 250 -> 500 -> 1000 Hz)", curses.color_pair(2))
        safe_addstr(stdscr, 13, col, " [L / N]  Next / Prev RGB Effect (Neon, Flowing, Breathing...)", curses.color_pair(2))
        safe_addstr(stdscr, 14, col, " [C]      Cycle Static Colors (Red, Green, Blue, Cyan...)", curses.color_pair(2))
        safe_addstr(stdscr, 15, col, " [O]      Toggle LED On / Off", curses.color_pair(2))
        safe_addstr(stdscr, 16, col, " [B]      Restore Default Button Mappings", curses.color_pair(2))
        safe_addstr(stdscr, 17, col, " [Q]      Quit", curses.color_pair(4) | curses.A_BOLD)

        safe_addstr(stdscr, min(h - 1, 19), col, f"Status: {status_msg}", curses.color_pair(3))

        stdscr.refresh()
        ch = stdscr.getch()

        if ch in (ord('q'), ord('Q'), 27):  # q or ESC
            break
        elif ch in (ord('1'), ord('2'), ord('3'), ord('4'), ord('5'), ord('6')):
            curr_dpi = ch - ord('0')
            mouse.set_dpi_stage(curr_dpi)
            status_msg = f"Switched to DPI Stage {curr_dpi}"
        elif ch in (ord('r'), ord('R')):
            curr_rate_idx = (curr_rate_idx + 1) % len(rates)
            mouse.set_polling_rate(rates[curr_rate_idx])
            status_msg = f"Polling rate set to {rates[curr_rate_idx]} Hz"
        elif ch in (ord('l'), ord('L')):
            curr_led_idx = (curr_led_idx + 1) % len(mode_keys)
            mouse.set_led_mode(mode_keys[curr_led_idx])
            status_msg = f"LED effect: {LED_MODES[mode_keys[curr_led_idx]][3]}"
        elif ch in (ord('n'), ord('N')):
            curr_led_idx = (curr_led_idx - 1) % len(mode_keys)
            mouse.set_led_mode(mode_keys[curr_led_idx])
            status_msg = f"LED effect: {LED_MODES[mode_keys[curr_led_idx]][3]}"
        elif ch in (ord('o'), ord('O')):
            if mode_keys[curr_led_idx] == "off":
                curr_led_idx = mode_keys.index("neon")
            else:
                curr_led_idx = mode_keys.index("off")
            mouse.set_led_mode(mode_keys[curr_led_idx])
            status_msg = f"LED: {LED_MODES[mode_keys[curr_led_idx]][3]}"
        elif ch in (ord('b'), ord('B')):
            mouse.restore_default_buttons()
            status_msg = "Factory default button mappings restored."
        elif ch in (ord('c'), ord('C')):
            curr_color_idx = (curr_color_idx + 1) % len(color_palette)
            c = color_palette[curr_color_idx]
            mouse.set_led_color(c[0], c[1], c[2])
            curr_led_idx = mode_keys.index("static")
            status_msg = f"Static color set to {c[3]} (RGB {c[0]}, {c[1]}, {c[2]})"


def run_daemon():
    """Runs background listener for hardware DPI button presses."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    try:
        mouse = KanataMouse()
    except Exception as e:
        print(f"Error finding mouse: {e}")
        sys.exit(1)

    print(f"Fantech Kanata S DPI Daemon started (PID {os.getpid()}). Listening on {mouse.dev_path}...")
    fd1 = os.open(mouse.dev_path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        while True:
            r, _, _ = select.select([fd1], [], [], 1.0)
            if r:
                try:
                    data = os.read(fd1, 64)
                except OSError:
                    continue
                if len(data) >= 3 and data[0] == 0x06:
                    stage_idx = data[2] & 0x07
                    stage = stage_idx + 1
                    dpi_val = DPI_STAGES.get(stage, 400)
                    st = {
                        "active_dpi_stage": stage,
                        "active_dpi_value": dpi_val,
                        "last_updated": time.time(),
                        "source": "hardware_event"
                    }
                    save_cached_state(st)
                    print(f"[{time.strftime('%H:%M:%S')}] Hardware DPI switched to Stage {stage} ({dpi_val} DPI)")
    finally:
        os.close(fd1)
        if os.path.isfile(PID_FILE):
            os.remove(PID_FILE)


def install_systemd_service():
    service_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(service_dir, exist_ok=True)
    service_path = os.path.join(service_dir, "kanata-mouse.service")
    script_path = os.path.abspath(sys.argv[0])
    unit_content = f"""[Unit]
Description=Fantech Kanata S (VX9S) Mouse Hardware DPI Event Listener
After=default.target

[Service]
Type=simple
ExecStart={sys.executable} {script_path} daemon
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""
    with open(service_path, "w") as f:
        f.write(unit_content)
    print(f"✓ Installed systemd service to: {service_path}")
    print("To enable and start immediately, run:")
    print("  systemctl --user daemon-reload")
    print("  systemctl --user enable --now kanata-mouse.service")


# ==============================================================================
# CLI Entrypoint
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Fantech Kanata S (VX9S) Linux Mouse Configuration Utility"
    )
    sub = parser.add_subparsers(dest="command")

    # status
    sub.add_parser("status", help="Show mouse hardware state, active DPI, polling rate, etc.")

    # dpi
    p_dpi = sub.add_parser("dpi", help="Set active DPI level (1-6)")
    p_dpi.add_argument("level", type=int, choices=[1, 2, 3, 4, 5, 6], help="DPI Stage (1-6)")

    # rate
    p_rate = sub.add_parser("rate", help="Set USB polling rate in Hz")
    p_rate.add_argument("hz", type=int, choices=[125, 250, 500, 1000], help="Polling rate (125, 250, 500, 1000)")

    # led-mode
    p_led = sub.add_parser("led-mode", help="Set RGB lighting effect preset (all 14 hardware modes)")
    p_led.add_argument("mode", type=str, choices=list(LED_MODES.keys()), help="LED effect mode name")
    p_led.add_argument("--speed", type=int, default=3, choices=range(0, 7), help="Effect speed (0-6, default: 3)")
    p_led.add_argument("--brightness", type=int, default=7, choices=range(0, 8), help="Brightness (0-7, 7=brightest, default: 7)")
    p_led.add_argument("--direction", type=int, default=0, choices=range(0, 4), help="Flow direction (0=CW, 1=CCW, 2=Two-way, 3=Overwrite)")
    p_led.add_argument("--symmetry", type=int, default=0, choices=[0, 1], help="Symmetry flag (0 or 1)")

    # led-color
    p_col = sub.add_parser("led-color", help="Set custom static RGB color (0-255)")
    p_col.add_argument("r", type=int, help="Red (0-255)")
    p_col.add_argument("g", type=int, help="Green (0-255)")
    p_col.add_argument("b", type=int, help="Blue (0-255)")
    p_col.add_argument("--brightness", type=int, default=7, choices=range(0, 8), help="Brightness (0-7, 7=brightest, default: 7)")

    # button-remap
    p_btn = sub.add_parser("button-remap", help="Remap mouse button to a function")
    p_btn.add_argument("button", type=int, choices=[1, 2, 3, 4, 5, 6], help="Button: 1=Left, 2=Mid, 3=Right, 4=Back, 5=Fwd, 6=DPI")
    p_btn.add_argument("function", type=str, choices=list(FUNCTION_NAME_TO_CODE.keys()), help="Function name (e.g. left_click, middle_click, back, forward, dpi_loop, disabled)")

    # restore-buttons
    sub.add_parser("restore-buttons", help="Restore all button bindings to factory default")

    # daemon
    p_daemon = sub.add_parser("daemon", help="Run background listener daemon for hardware DPI button presses")
    p_daemon.add_argument("--install-service", action="store_true", help="Install and enable systemd user service")

    # tui
    sub.add_parser("tui", help="Launch interactive Terminal User Interface (TUI)")

    args = parser.parse_args()

    if args.command == "daemon":
        if args.install_service:
            install_systemd_service()
            return
        run_daemon()
        return

    try:
        mouse = KanataMouse()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    if args.command == "status" or args.command is None:
        st = mouse.get_status()
        daemon_str = f"Active (PID: {st['daemon_pid']})" if st['daemon_pid'] else "Inactive (run './kanata_ctl.py daemon &')"
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  Fantech Kanata S VX9S Hardware Status")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"  Device Node      : {mouse.dev_path}")
        print(f"  Active DPI Stage : Level {st['dpi_stage']} - {st['dpi_value']} DPI (Index: {st['dpi_index']})")
        print(f"  DPI Event Source : {st['dpi_source']}")
        print(f"  Listener Daemon  : {daemon_str}")
        print(f"  Polling Rate     : {st['polling_rate_hz']} Hz")
        print(f"  Active Profile   : Profile {st['profile_index'] + 1}")
        print(f"  Lighting Mode    : {st['led_desc']} ({st['led_mode']})")
        print(f"  Lighting Speed   : {st['led_speed']}  |  Brightness: {st['led_brightness']}/7")
        print(f"  Static Slot 0 RGB: RGB{st['led_rgb']}")
        print(f"  Onboard Memory   : {st['memory_size']}")
        print(f"  LED State Byte   : {st['led_raw']}")
        print(f"  Raw Report 7     : {' '.join(st['raw_report'])}")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("Tip: Run './kanata_ctl.py tui' for interactive GUI in terminal.")

    elif args.command == "dpi":
        mouse.set_dpi_stage(args.level)
        dpi_val = DPI_STAGES.get(args.level, 400)
        print(f"✓ Switched to DPI Stage {args.level} ({dpi_val} DPI)")

    elif args.command == "rate":
        mouse.set_polling_rate(args.hz)
        print(f"✓ Polling rate set to {args.hz} Hz")

    elif args.command == "led-mode":
        mouse.set_led_mode(
            args.mode,
            speed=args.speed,
            brightness=args.brightness,
            direction=args.direction,
            symmetry=args.symmetry,
        )
        desc = LED_MODES[args.mode][3]
        print(f"✓ LED effect set to: {desc} ({args.mode}, speed={args.speed}, brightness={args.brightness}/7)")

    elif args.command == "led-color":
        mouse.set_led_color(args.r, args.g, args.b, brightness=args.brightness)
        print(f"✓ Static LED color set to RGB({args.r}, {args.g}, {args.b}) (brightness={args.brightness}/7)")

    elif args.command == "button-remap":
        mouse.remap_button(args.button, args.function)
        btn_desc = BUTTON_NAMES[args.button]
        fn_code = FUNCTION_NAME_TO_CODE[args.function]
        fn_desc = BUTTON_FUNCTIONS[fn_code]
        print(f"✓ Button {args.button} ({btn_desc}) remapped to: {fn_desc}")

    elif args.command == "restore-buttons":
        mouse.restore_default_buttons()
        print("✓ All mouse buttons restored to factory default bindings!")

    elif args.command == "tui":
        curses.wrapper(lambda scr: run_tui(scr, mouse))


if __name__ == "__main__":
    main()
