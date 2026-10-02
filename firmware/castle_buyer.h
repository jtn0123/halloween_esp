// The buyer build's C (castle_buyer.yaml): the AP's name, port 80 shared
// between ESPHome's captive portal and the castle's server, and ESPHome's
// OTA password kept equal to the castle key.
//
// Included ONLY by castle_buyer.yaml. The yard build has no `ap:` and no
// `captive_portal:`, so it neither includes this nor could compile it.

#pragma once

#include <cstdio>

#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "esphome/components/wifi/wifi_component.h"
#include "esphome/components/captive_portal/captive_portal.h"
#include "esphome/components/web_server_base/web_server_base.h"
#include "esphome/components/esphome/ota/ota_esphome.h"
#include "sd_web.h"

namespace castle_buyer {

static const char *const TAG = "castle_buyer";

/// "Castle-B2C3": the last two bytes of the station MAC, which are also the
/// last four characters of the mDNS name name_add_mac_suffix builds
/// (castle-a1b2c3), so a label can print one and the owner can find the
/// other. Upper case on the AP because it is read off a phone's list.
inline void name_the_ap() {
  if (esphome::wifi::global_wifi_component == nullptr) return;
  uint8_t mac[6];
  esphome::get_mac_address_raw(mac);
  char ssid[16];
  snprintf(ssid, sizeof(ssid), "Castle-%02X%02X", mac[4], mac[5]);
  esphome::wifi::WiFiAP ap;
  ap.set_ssid(ssid);
  esphome::wifi::global_wifi_component->set_ap(ap);
}

/// Port 80 belongs to whichever of the two servers the castle needs NOW.
///
/// Both ESPHome's portal (web_server_base) and castle_web bind :80 and IDF's
/// default control socket, so at most one listens. While the portal is up
/// there is no network to serve the castle's page on anyway; the moment Wi-Fi
/// joins, ESPHome ends the portal and frees the port, and this brings the
/// castle's server back within a second. The other direction — a castle
/// that lost its router for `ap_timeout` — finds the castle's server holding
/// the port the portal just failed to bind, so that server is stopped and the
/// portal's listener restarted in its place. Called every second; one bool
/// compare when nothing changes.
inline void share_port_80() {
  auto *portal = esphome::captive_portal::global_captive_portal;
  if (portal == nullptr) return;
  if (portal->is_active()) {
    if (castle_web::g_server == nullptr) return;
    ESP_LOGI(TAG, "captive portal up: handing port 80 to it");
    castle_web::stop();
    auto *base = esphome::web_server_base::global_web_server_base;
    if (base != nullptr && base->get_server() != nullptr) base->get_server()->begin();
  } else if (castle_web::g_server == nullptr) {
    ESP_LOGI(TAG, "captive portal down: the castle's server takes port 80");
    castle_web::start();
  }
}

/// ESPHome's own OTA port follows the castle key (sd_web_prefs.h): with a
/// key set, `esphome upload` needs it as the OTA password, so the key guards
/// the firmware on both doors and not just on PUT /api/ota. Main loop only —
/// the component reads its password there — and only when the key changed.
inline void follow_key(esphome::ESPHomeOTAComponent *ota) {
  static unsigned seen = 0;
  const unsigned gen = castle_web::g_key_gen.load();
  if (ota == nullptr || gen == seen) return;
  seen = gen;
  ota->set_auth_password(castle_web::key_copy());
  ESP_LOGI(TAG, "OTA password %s", castle_web::locked() ? "follows the castle key" : "off");
}

}  // namespace castle_buyer
