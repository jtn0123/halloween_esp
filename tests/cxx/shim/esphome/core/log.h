#pragma once
// ESPHome's logging header, as far as the castle's C++ uses it: the ESP_LOGx
// macros and nothing else. ESPHome defines them over its own logger; on the
// host they are esp_log.h's, so a file that includes either gets the same
// four letters.
#include <esp_log.h>
