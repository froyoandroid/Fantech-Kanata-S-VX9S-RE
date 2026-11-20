# Fantech Kanata S (VX9S) — USB HID & EEPROM Protocol Specification

- **Device:** Fantech Kanata S (VX9S)
- **USB VID:PID:** `30fa:1040`
- **Reference Binary:** `Gaming Mouse 3.0.exe` (PE32 x86, ImageBase `0x00400000`)

---

## 1. USB HID Interfaces & Reports

| Interface | Linux Node | Purpose | Report IDs |
| :--- | :--- | :--- | :--- |
| Interface 0 | `/dev/hidraw0` | Standard Boot Mouse (5 buttons, X/Y, wheel) | `0x01` (Input, 7 bytes) |
| Interface 1 | `/dev/hidraw1` | Vendor Control, Keyboard/Media Emulation, HW Events | `0x02` (Consumer), `0x03` (Keyboard), `0x06` (Vendor Input, 3 bytes), `0x07` (Vendor Feature, 8 bytes), `0x08` (System) |

---

## 2. Asynchronous Hardware Events (Input Report `0x06`)

Read via `ReadFile` on Interface 1 (`0x0042a84f`, `0x0042d366`):
```text
Byte 0: 0x06  (Report ID)
Byte 1: Event/Button code (e.g. 0xf6 = Button 6 / DPI Switch)
Byte 2: (press_flag << 7) | (active_dpi_stage & 0x07)
        - Bit 7 = 1 on press, 0 on release
        - Bits 0..2 = Active DPI stage index (0..5)
```

---

## 3. Feature Report `0x07` Status (`HIDIOCGFEAT`)

8-byte Feature Report on Interface 1 (`0x0042a9dd`, `0x0042dd2c`):
```text
Byte 0: 0x07  (Report ID)
Byte 1: Last EEPROM read byte (after EEPROM fetch strobe)
Byte 2..5: Runtime status / internal MCU state
Byte 6:
  - Bit 1 (0x02): Onboard EEPROM size (1 = 4 KB, 0 = 2 KB)
  - Bits 2..3 (0x0C): Active profile index (CurConfig, 0..3)
Byte 7:
  - Bits 4..7: Active LED mode acknowledge nibble (e.g. 0x2_ = Static, 0x6_ = Neon, 0xc_ = Button Response, 0xd_ = Off)
```

---

## 4. Feature Report `0x07` Commands (`HIDIOCSFEAT`)

### Command `0x10` — Button Remapping (`0x004452b0`)
```text
[0x07, 0x10, button_id (1..6), func_code, 0x00, 0x00, 0x00, 0x00]
```
Function codes: `0`=Disabled, `1`=Left, `2`=Middle, `3`=Right, `4`=Back, `5`=Forward, `6`=DPI Loop, `13`=Double Click, `14`=Rapid Fire, `15`=LED Loop, `16`=DPI+, `17`=DPI-.

### Command `0x13` — Live LED Runtime State (`0x00445110`, `0x00470bd0`)
```text
Byte 0: 0x07
Byte 1: 0x13
Byte 2: 0x80 | (color_mask & 0x7f)          # 0x81 = Slot 0 only, 0xff = All 7 colors
Byte 3: 0x80 | ((mode_lo3 & 7) << 4) | ((flag_b3 & 1) << 3) | (speed & 7)
Byte 4: ((scolor & 1) << 7) | ((symmetry & 1) << 6) | ((direction & 3) << 4) | 0x04
Byte 5: hw_brightness & 0x07                # Inverted: 0 = brightest, 7 = dimmest
Byte 6: (mode_bit3 & 1) << 7                # Bit 7 = bit 3 of LED mode (for modes 8..11)
Byte 7: 0x02
```

### Command `0x14` — USB Polling Rate (`0x00444f20`)
```text
[0x07, 0x14, b2, b3, 0x00, 0x00, 0x00, 0x00]
```
- `125 Hz`: `(0xa0, 0x40)`
- `250 Hz`: `(0x20, 0x00)`
- `500 Hz`: `(0xf0, 0x40)`
- `1000 Hz`: `(0x60, 0x00)`

### Command `0x18` & `0x20` — EEPROM Read / Write / Commit (`0x00425a80`, `0x00425b50`, `0x00436380`)
- **Read byte at `addr`**:
  1. `[0x07, 0x18, 0x03, 0x00, addr_lo, addr_hi, 0x00, 0x00]` (pre-read strobe)
  2. `[0x07, 0x18, 0x00, 0x00, addr_lo, addr_hi, 0x00, 0x00]` (bus idle)
  3. `[0x07, 0x18, 0x05, 0x00, addr_lo, addr_hi, 0x00, 0x00]` (fetch)
  4. `HIDIOCGFEAT` Report `0x07` → result in `buf[1]`
  5. `[0x07, 0x18, 0x00, 0x00, addr_lo, addr_hi, 0x00, 0x00]` (bus idle)
- **Write 8-byte page at `addr` (8-byte aligned)**:
  1. For `i = 0..7`: `[0x07, 0x18, 0x03, i, addr_lo, addr_hi, data[i], 0x07]`
  2. Strobe execute: `[0x07, 0x18, 0x09, 0x00, addr_lo, addr_hi, 0x00, 0x07]`
  3. Bus idle: `[0x07, 0x18, 0x00, 0x00, addr_lo, addr_hi, 0x00, 0x00]`
- **Commit to Flash**:
  1. Unlock: `[0x07, 0x18, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00]`
  2. Idle: `[0x07, 0x18, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]`
  3. Commit: `[0x07, 0x20, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]`

---

## 5. Onboard EEPROM Memory Map (`0x00`–`0x3F`)

| Address | Block | Description |
| :--- | :--- | :--- |
| `0x00` | `0x00` | Magic `0x5a` / Polling rate low bits |
| `0x01..0x07` | `0x00` | Profile header, `0xa5`, PID/VID (`40 10 fa 30`) |
| `0x08..0x0f` | `0x08` | Button mappings (`21 22 23 24 25 06 20 20`) |
| `0x10..0x17` | `0x10` | DPI step values |
| `0x18` | `0x18[0]` | `0x18 \| (active_dpi_stage & 0x07)` (`0x0042f556`) |
| `0x19` | `0x18[1]` | 6-bit DPI stage enable mask (`0x3f` = all 6 enabled) |
| `0x1c` | `0x18[4]` | LED Mode (lo 3 bits) + `flag_b3` + Speed: `0x80 \| (mode_lo3 << 4) \| (flag_b3 << 3) \| speed` |
| `0x1d` | `0x18[5]` | `0x80 \| (color_mask & 0x7f)` (7-color enable mask) |
| `0x20..0x31` | `0x20..0x30` | **Color Palette (Slots 0..5, 3 bytes each: R, G, B)** (`0x0042f9cf`, `0x00416b72`):<br>• **Active-low (common-anode) 4-bit nibbles**: `hw = 15 - (val >> 4)` (`0` = full ON, `15` = OFF)<br>• **High nibble (`>> 4`)**: User LED color (for multi-color animated effects)<br>• **Low nibble (`& 0x0f`)**: DPI / Static Light On color (also updated live via Command `0x14`) |
| `0x33` | `0x30[3]` | **Bit 7 (`0x80`)**: Bit 3 of LED mode (`1` for modes `8..11`, `0` for `0..7`) (`0x004303ee`) |
| `0x3a` | `0x38[2]` | `((scolor & 1) << 7) \| ((symmetry & 1) << 6) \| ((direction & 3) << 4) \| 0x04` (`0x004305c3`) |
| `0x3e` | `0x38[6]` | `(UserColor[6].R << 4) \| UserColor[6].G` |
| `0x3f` | `0x38[7]` | `((hw_brightness & 7) << 4) \| UserColor[6].B` (`0x00430850`) |

---

## 6. Hardware LED Modes (`0x0042f567`)

| `LED_TYPE` | Name | `mode_lo3` | `flag_b3` | `mode_bit3` (`0x33` b7) | `0x1c` Byte |
| :---: | :--- | :---: | :---: | :---: | :---: |
| `0` | DPI Breathing Effect | `0` | `0` | `0` | `0x80 \| speed` |
| `1` | Cycle Breathing Effect | `1` | `0` | `0` | `0x90 \| speed` |
| `2` | Static Light On | `2` | `0` | `0` | `0xa6` (`speed=6`) |
| `3` | Flowing Water Effect | `3` | `0` | `0` | `0xb0 \| speed` |
| `4` | Mono Water Effect | `4` | `0` | `0` | `0xc0 \| speed` |
| `5` | Comet Streak | `5` | `0` | `0` | `0xd0 \| speed` |
| `6` | Neon | `6` | `0` | `0` | `0xe0 \| speed` |
| `7` | Ambilight | `7` | `0` | `0` | `0xf0 \| speed` |
| `8` | Flicker | `0` | `0` | `1` | `0x80 \| speed` |
| `9` | Star Trek | `1` | `0` | `1` | `0x90 \| speed` |
| `10` | Ripple | `2` | `0` | `1` | `0xa0 \| speed` |
| `11` | Enraptured | `3` | `0` | `1` | `0xb0 \| speed` |
| `12` | Button Response | `1` | `1` | `0` | `0x98 \| speed` |
| `13` | LED Off | `0` | `0` | `0` | `0x87` (`speed=7`) |
