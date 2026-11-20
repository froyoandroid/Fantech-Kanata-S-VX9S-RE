/*
 * Fantech Kanata S (VX9S) driver for libratbag
 *
 * Reverse-engineered from Kanata_S_VX9S_Software_V1.0_23111700.exe
 * Hardware: Fantech Kanata S VX9S (USB ID: 30fa:1040)
 */

#include "config.h"
#include <assert.h>
#include <errno.h>
#include <libevdev/libevdev.h>
#include <linux/input.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>

#include "libratbag-private.h"
#include "libratbag-hidraw.h"

#define FANTECH_REPORT_ID            0x07
#define FANTECH_REPORT_SIZE          8

#define FANTECH_NUM_PROFILES         4
#define FANTECH_NUM_DPI              6
#define FANTECH_NUM_BUTTONS          6
#define FANTECH_NUM_LEDS             1

#define FANTECH_CMD_BUTTON_REMAP     0x10
#define FANTECH_CMD_LIVE_STATE       0x13
#define FANTECH_CMD_LIVE_DPI_COLOR   0x14
#define FANTECH_CMD_LED_COLOR        0x15
#define FANTECH_CMD_EEPROM           0x18
#define FANTECH_CMD_COMMIT           0x20

static const unsigned int fantech_dpi_list[] = {
	200, 400, 600, 800, 1000, 1200, 1600, 2000, 2400, 3200, 4000, 4800
};

static const unsigned int fantech_rates[] = {
	125, 250, 500, 1000
};

struct fantech_data {
	uint8_t raw_report[FANTECH_REPORT_SIZE];
};

static int
fantech_read_status(struct ratbag_device *device, uint8_t *report)
{
	report[0] = FANTECH_REPORT_ID;
	return ratbag_hidraw_raw_request(device, FANTECH_REPORT_ID,
					 report, FANTECH_REPORT_SIZE,
					 HID_FEATURE_REPORT, HID_REQ_GET_REPORT);
}

static int
fantech_send_cmd(struct ratbag_device *device, const uint8_t *payload)
{
	uint8_t buf[FANTECH_REPORT_SIZE];
	memcpy(buf, payload, FANTECH_REPORT_SIZE);
	buf[0] = FANTECH_REPORT_ID;
	return ratbag_hidraw_raw_request(device, FANTECH_REPORT_ID,
					 buf, FANTECH_REPORT_SIZE,
					 HID_FEATURE_REPORT, HID_REQ_SET_REPORT);
}

static uint8_t
fantech_read_eeprom_byte(struct ratbag_device *device, uint16_t addr)
{
	uint8_t addr_lo = addr & 0xff;
	uint8_t addr_hi = (addr >> 8) & 0xff;
	uint8_t res[FANTECH_REPORT_SIZE];

	/* Step 1: Pre-read strobe (0x38c00) */
	uint8_t pkt1[FANTECH_REPORT_SIZE] = { FANTECH_REPORT_ID, FANTECH_CMD_EEPROM, 0x03, 0x00, addr_lo, addr_hi, 0, 0 };
	fantech_send_cmd(device, pkt1);
	usleep(2000);

	/* Step 2: Bus release */
	uint8_t pkt2[FANTECH_REPORT_SIZE] = { FANTECH_REPORT_ID, FANTECH_CMD_EEPROM, 0x00, 0x00, addr_lo, addr_hi, 0, 0 };
	fantech_send_cmd(device, pkt2);
	usleep(2000);

	/* Step 3: Fetch command (0x24e80) */
	uint8_t pkt3[FANTECH_REPORT_SIZE] = { FANTECH_REPORT_ID, FANTECH_CMD_EEPROM, 0x05, 0x00, addr_lo, addr_hi, 0, 0 };
	fantech_send_cmd(device, pkt3);
	usleep(2000);

	/* Step 4: Read result buffer */
	fantech_read_status(device, res);

	/* Bus idle */
	fantech_send_cmd(device, pkt2);
	return res[1];
}

static void
fantech_read_eeprom_block_8(struct ratbag_device *device, uint16_t addr, uint8_t *data8)
{
	for (unsigned int i = 0; i < 8; i++) {
		data8[i] = fantech_read_eeprom_byte(device, addr + i);
	}
}

static void
fantech_write_eeprom_block_8(struct ratbag_device *device, uint16_t addr, const uint8_t *data8)
{
	uint8_t addr_lo = addr & 0xff;
	uint8_t addr_hi = (addr >> 8) & 0xff;

	/* Step 1: Send 8 bytes */
	for (unsigned int i = 0; i < 8; i++) {
		uint8_t pkt[FANTECH_REPORT_SIZE] = {
			FANTECH_REPORT_ID, FANTECH_CMD_EEPROM, 0x03, (uint8_t)i, addr_lo, addr_hi, data8[i], 7
		};
		fantech_send_cmd(device, pkt);
		usleep(2000);
	}
	usleep(5000);

	/* Step 2: Opcode 0x09 strobe */
	uint8_t strobe[FANTECH_REPORT_SIZE] = {
		FANTECH_REPORT_ID, FANTECH_CMD_EEPROM, 0x09, 0, addr_lo, addr_hi, 0, 7
	};
	fantech_send_cmd(device, strobe);
	usleep(5000);

	/* Step 3: Idle release */
	uint8_t idle[FANTECH_REPORT_SIZE] = {
		FANTECH_REPORT_ID, FANTECH_CMD_EEPROM, 0x00, 0, addr_lo, addr_hi, 0, 0
	};
	fantech_send_cmd(device, idle);
	usleep(5000);
}

static void
fantech_commit_eeprom(struct ratbag_device *device)
{
	uint8_t unlock[FANTECH_REPORT_SIZE] = { FANTECH_REPORT_ID, FANTECH_CMD_EEPROM, 0x10, 0, 0, 0, 0, 0 };
	fantech_send_cmd(device, unlock);
	usleep(5000);

	uint8_t idle[FANTECH_REPORT_SIZE] = { FANTECH_REPORT_ID, FANTECH_CMD_EEPROM, 0x00, 0, 0, 0, 0, 0 };
	fantech_send_cmd(device, idle);
	usleep(5000);

	uint8_t commit[FANTECH_REPORT_SIZE] = { FANTECH_REPORT_ID, FANTECH_CMD_COMMIT, 0, 0, 0, 0, 0, 0 };
	fantech_send_cmd(device, commit);
	usleep(20000);
}

static int
fantech_set_active_profile(struct ratbag_device *device, unsigned int index)
{
	/* Profiles 0-3 are stored in onboard memory blocks */
	return 0;
}

static void
fantech_read_profile(struct ratbag_profile *profile)
{
	struct ratbag_device *device = profile->device;
	struct ratbag_resolution *res;
	struct ratbag_button *btn;
	struct ratbag_led *led;
	unsigned int i;

	/* Setup DPI stages */
	for (i = 0; i < FANTECH_NUM_DPI; i++) {
		res = ratbag_profile_get_resolution(profile, i);
		if (!res)
			continue;
		ratbag_resolution_set_dpi_list(res, fantech_dpi_list, ARRAY_SIZE(fantech_dpi_list));
		ratbag_resolution_set_dpi(res, fantech_dpi_list[i < ARRAY_SIZE(fantech_dpi_list) ? i * 2 : 0]);
		res->is_active = (i == 0);
	}

	ratbag_profile_set_report_rate_list(profile, fantech_rates, ARRAY_SIZE(fantech_rates));
	ratbag_profile_set_report_rate(profile, 500);

	/* Setup 6 Buttons (Factory bindings: 1=Left, 2=Right, 3=Middle, 4=Back, 5=Forward, 6=DPI) */
	btn = ratbag_profile_get_button(profile, 0); // Left Click
	ratbag_button_enable_action_type(btn, RATBAG_BUTTON_ACTION_TYPE_BUTTON);
	ratbag_button_set_button(btn, 1);

	btn = ratbag_profile_get_button(profile, 1); // Right Click
	ratbag_button_enable_action_type(btn, RATBAG_BUTTON_ACTION_TYPE_BUTTON);
	ratbag_button_set_button(btn, 2);

	btn = ratbag_profile_get_button(profile, 2); // Middle Click
	ratbag_button_enable_action_type(btn, RATBAG_BUTTON_ACTION_TYPE_BUTTON);
	ratbag_button_set_button(btn, 3);

	btn = ratbag_profile_get_button(profile, 3); // Backward
	ratbag_button_enable_action_type(btn, RATBAG_BUTTON_ACTION_TYPE_BUTTON);
	ratbag_button_set_button(btn, 4);

	btn = ratbag_profile_get_button(profile, 4); // Forward
	ratbag_button_enable_action_type(btn, RATBAG_BUTTON_ACTION_TYPE_BUTTON);
	ratbag_button_set_button(btn, 5);

	btn = ratbag_profile_get_button(profile, 5); // DPI Switch
	ratbag_button_enable_action_type(btn, RATBAG_BUTTON_ACTION_TYPE_SPECIAL);
	ratbag_button_set_special(btn, RATBAG_BUTTON_ACTION_SPECIAL_RESOLUTION_CYCLE_UP);

	/* Setup LED */
	led = ratbag_profile_get_led(profile, 0);
	if (led) {
		led->type = RATBAG_LED_TYPE_RGB;
		led->colormode = RATBAG_LED_COLORMODE_RGB;
		ratbag_led_set_mode(led, RATBAG_LED_CYCLE);
	}
}

static int
fantech_probe(struct ratbag_device *device)
{
	int rc;
	struct ratbag_profile *profile;
	struct fantech_data *drv_data;
	uint8_t status_report[FANTECH_REPORT_SIZE];

	rc = ratbag_open_hidraw(device);
	if (rc)
		return rc;

	if (!ratbag_hidraw_has_report(device, FANTECH_REPORT_ID)) {
		ratbag_close_hidraw(device);
		return -ENODEV;
	}

	drv_data = zalloc(sizeof(*drv_data));
	ratbag_set_drv_data(device, drv_data);

	rc = fantech_read_status(device, status_report);
	if (rc < 0) {
		ratbag_close_hidraw(device);
		free(drv_data);
		return rc;
	}

	memcpy(drv_data->raw_report, status_report, FANTECH_REPORT_SIZE);

	ratbag_device_init_profiles(device,
				    FANTECH_NUM_PROFILES,
				    FANTECH_NUM_DPI,
				    FANTECH_NUM_BUTTONS,
				    FANTECH_NUM_LEDS);

	ratbag_device_for_each_profile(device, profile)
		fantech_read_profile(profile);

	/* Set active profile based on hardware status report byte 6 bits 2-3 */
	uint8_t active_profile_idx = (status_report[6] >> 2) & 0x03;
	profile = ratbag_device_get_profile(device, active_profile_idx);
	if (profile)
		profile->is_active = true;

	return 0;
}

static void
fantech_remove(struct ratbag_device *device)
{
	ratbag_close_hidraw(device);
	free(ratbag_get_drv_data(device));
}

static int
fantech_commit(struct ratbag_device *device)
{
	struct ratbag_profile *profile;
	struct ratbag_resolution *res;
	struct ratbag_led *led;
	int rc = 0;

	ratbag_device_for_each_profile(device, profile) {
		if (!profile->dirty)
			continue;

		uint8_t active_dpi = 0;

		/* Check active resolution change */
		ratbag_profile_for_each_resolution(profile, res) {
			if (res->is_active) {
				active_dpi = (uint8_t)res->index;
				break;
			}
		}

		uint8_t b18[8], b30[8], b38[8];
		fantech_read_eeprom_block_8(device, 0x18, b18);
		fantech_read_eeprom_block_8(device, 0x30, b30);
		fantech_read_eeprom_block_8(device, 0x38, b38);

		/* Update active DPI stage in EEPROM Block 0x18 Byte 0 */
		b18[0] = 0x18 | (active_dpi & 0x07);

		/* Update USB Polling Rate divider in EEPROM Block 0x18 Byte 6 (0x1e) */
		if (profile->hz > 0) {
			switch (profile->hz) {
			case 125:  b18[6] = 0x07; break;
			case 250:  b18[6] = 0x03; break;
			case 500:  b18[6] = 0x01; break;
			case 1000: b18[6] = 0x00; break;
			default: break;
			}
		}

		/* Check LED settings */
		led = ratbag_profile_get_led(profile, 0);
		if (led) {
			uint8_t mode_byte = 0xe3; /* Neon default */
			uint8_t color_mask = 0x7f;
			uint8_t hw_bright = 0x00; /* 0 = max brightness (inverted) */

			/* Clear mode bit 3 in Block 0x30 Byte 3 for modes 0..7 */
			b30[3] &= 0x7f;
			b38[2] = 0x04;
			b38[7] = (hw_bright << 4) | (b38[7] & 0x0f);

			if (led->mode == RATBAG_LED_OFF) {
				mode_byte = 0x87; /* Mode 13: LED Off (speed=7) */
				b18[4] = mode_byte;
				b18[5] = 0x80 | color_mask;
				fantech_write_eeprom_block_8(device, 0x30, b30);
				fantech_write_eeprom_block_8(device, 0x38, b38);
				fantech_write_eeprom_block_8(device, 0x18, b18);
				fantech_commit_eeprom(device);

				uint8_t live_off[8] = { FANTECH_REPORT_ID, FANTECH_CMD_LIVE_STATE, 0x80 | color_mask, mode_byte, 0x04, hw_bright, 0x00, 0x02 };
				fantech_send_cmd(device, live_off);
			} else if (led->mode == RATBAG_LED_ON) {
				/* Custom static RGB color (active-low inverted 4-bit nibbles) */
				uint8_t rn = 15 - ((led->color.red >> 4) & 0x0f);
				uint8_t gn = 15 - ((led->color.green >> 4) & 0x0f);
				uint8_t bn = 15 - ((led->color.blue >> 4) & 0x0f);
				uint8_t nibs[3] = { rn, gn, bn };

				uint8_t b20[8], b28[8];
				fantech_read_eeprom_block_8(device, 0x20, b20);
				fantech_read_eeprom_block_8(device, 0x28, b28);

				/* Update LOW nibbles (DPI/Static color) across all 6 slots */
				for (int slot = 0; slot < 6; slot++) {
					for (int i = 0; i < 3; i++) {
						int addr = 0x20 + slot * 3 + i;
						int off = addr & 0x07;
						if (addr < 0x28)
							b20[off] = (b20[off] & 0xf0) | nibs[i];
						else if (addr < 0x30)
							b28[off] = (b28[off] & 0xf0) | nibs[i];
						else
							b30[off] = (b30[off] & 0xf0) | nibs[i];
					}
				}
				/* Also update User Color Slot 0 HIGH nibbles */
				b20[0] = (rn << 4) | (b20[0] & 0x0f);
				b20[1] = (gn << 4) | (b20[1] & 0x0f);
				b20[2] = (bn << 4) | (b20[2] & 0x0f);

				fantech_write_eeprom_block_8(device, 0x20, b20);
				fantech_write_eeprom_block_8(device, 0x28, b28);
				fantech_write_eeprom_block_8(device, 0x30, b30);
				fantech_write_eeprom_block_8(device, 0x38, b38);

				mode_byte = 0xa6; /* Mode 2: Static Light On (speed=6) */
				color_mask = 0x01;
				b18[4] = mode_byte;
				b18[5] = 0x80 | color_mask;
				fantech_write_eeprom_block_8(device, 0x18, b18);
				fantech_commit_eeprom(device);

				uint8_t live_on[8] = { FANTECH_REPORT_ID, FANTECH_CMD_LIVE_STATE, 0x80 | color_mask, mode_byte, 0x04, hw_bright, 0x00, 0x02 };
				fantech_send_cmd(device, live_on);

				uint8_t live_col[8] = { FANTECH_REPORT_ID, FANTECH_CMD_LIVE_DPI_COLOR, ((active_dpi & 0x07) << 5) | gn, (rn << 4) | bn, 0, 0, 0, 0 };
				fantech_send_cmd(device, live_col);
			} else if (led->mode == RATBAG_LED_BREATHING) {
				mode_byte = 0x93; /* Mode 1: Cycle Breathing */
				b18[4] = mode_byte;
				b18[5] = 0x80 | color_mask;
				fantech_write_eeprom_block_8(device, 0x30, b30);
				fantech_write_eeprom_block_8(device, 0x38, b38);
				fantech_write_eeprom_block_8(device, 0x18, b18);
				fantech_commit_eeprom(device);

				uint8_t live_br[8] = { FANTECH_REPORT_ID, FANTECH_CMD_LIVE_STATE, 0x80 | color_mask, mode_byte, 0x04, hw_bright, 0x00, 0x02 };
				fantech_send_cmd(device, live_br);
			} else {
				mode_byte = 0xe3; /* Mode 6: Neon */
				b18[4] = mode_byte;
				b18[5] = 0x80 | color_mask;
				fantech_write_eeprom_block_8(device, 0x30, b30);
				fantech_write_eeprom_block_8(device, 0x38, b38);
				fantech_write_eeprom_block_8(device, 0x18, b18);
				fantech_commit_eeprom(device);

				uint8_t live_neon[8] = { FANTECH_REPORT_ID, FANTECH_CMD_LIVE_STATE, 0x80 | color_mask, mode_byte, 0x04, hw_bright, 0x00, 0x02 };
				fantech_send_cmd(device, live_neon);
			}
		} else {
			fantech_write_eeprom_block_8(device, 0x18, b18);
			fantech_commit_eeprom(device);
		}
	}

	return rc;
}

struct ratbag_driver fantech_driver = {
	.name = "Fantech",
	.id = "fantech",
	.probe = fantech_probe,
	.remove = fantech_remove,
	.commit = fantech_commit,
	.set_active_profile = fantech_set_active_profile,
};
