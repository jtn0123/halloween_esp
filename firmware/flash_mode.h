// Reboot into the ROM's USB download mode, from software.
//
// WHY THIS EXISTS: getting firmware onto this board has meant physically
// holding BOOT and tapping RESET. That is fine at a desk and useless the
// moment the castle is on a porch and you are not in the same building — and
// it has already blocked work twice.
//
// Neither usual escape hatch is available here. There is no USB serial
// console (the ESP32-S2 has no USB Serial/JTAG peripheral, so the application
// never enumerates a port), and OTA is off because the embedded audio makes
// the binary too large for two app slots.
//
// But the ROM bootloader checks a bit in an always-on RTC register during
// early boot. That register survives a software reset, so setting it and
// restarting brings the chip up in download mode with its USB bootloader
// enumerated, ready for esptool over the wire.
//
// This is one-way on purpose. Once called, the device stays in download mode
// until something reflashes it — there is no application running to change
// its mind. So it belongs behind a deliberate action, never on a timer or an
// error path.

#pragma once

#include "esp_system.h"
#include "soc/rtc_cntl_reg.h"

namespace castle_sd {

inline void reboot_to_download_mode() {
  REG_WRITE(RTC_CNTL_OPTION1_REG, RTC_CNTL_FORCE_DOWNLOAD_BOOT);
  esp_restart();
}

}  // namespace castle_sd
