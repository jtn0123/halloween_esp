#pragma once
// Status screen on the 2.13" eInk FeatherWing (#4195 mono, SSD1680, 250x122).
//
// The wing has been on the castle all along carrying the SD slot; this turns
// its unused panel into the thing eInk is actually good at: slow, persistent
// status you can read in daylight with the power off. Scene, track, uptime,
// SD state, crash counters, and a QR code that gets a phone to the web remote
// without typing an IP in the dark.
//
// Constraints that shaped this file:
//
//  * SHARED BUS. The SD card owns an ESP-IDF SPI bus (sd_audio.h). ESPHome's
//    own SPI/display components would initialise a second driver on the same
//    pins, so the panel joins the existing bus as a plain spi_master device
//    instead (CS GPIO9, D/C GPIO10 — the wing's fixed wiring).
//
//  * NO RESET, NO BUSY. The FeatherWing does not route the panel's RST or
//    BUSY lines to the Feather. No BUSY means fixed worst-case delays instead
//    of polling. No RST means deep sleep is a one-way door (only a hardware
//    reset exits it) — so the controller is never put to deep sleep; after an
//    update the 0xF7 sequence has already disabled the analog block and the
//    idle draw is negligible.
//
//  * SINGLE CORE. A full refresh takes ~4 s. On the S2 every blocking wait
//    starves the watched main loop (see sd_web_site.h for the scar), so all
//    panel work happens on its own low-priority task and every wait is a
//    vTaskDelay. The main loop only ever sets an atomic flag.
//
// Refresh policy: once at boot, on scene change (min 3 min apart — eInk
// panels age per refresh), hourly so the uptime stays honest, and on demand
// via the "Refresh eInk" button (min 15 s, for bench work).

#include <atomic>
#include <cstdio>
#include <cstring>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>
#include <driver/spi_master.h>
#include <driver/sdspi_host.h>
#include <driver/gpio.h>
#include <esp_netif.h>
#include <esp_timer.h>
#include <esp_vfs_fat.h>
#include "esphome/core/log.h"

#include "sd_audio.h"       // castle_sd::g_mounted — is the card up
#include "castle_health.h"  // boots / crashes / reset reason
#include "qr_castle.h"      // generated: QR of the web remote's URL
#include "eink_font.h"      // generated: 6x13 Menlo cells

namespace castle_eink {

static const char *const TAG = "eink";

// Panel geometry. RAM x is the SHORT axis: 122 visible pixels in 16 bytes.
constexpr int PANEL_W = 122, PANEL_H = 250, ROW_BYTES = 16;
// Logical drawing space is landscape: 250 wide, 122 tall.
constexpr int W = PANEL_H, H = PANEL_W;
// Flip if the first photo shows the text upside down relative to the
// castle's mounting. One constant, no other change.
constexpr bool ROT180 = false;

constexpr uint32_t MIN_GAP_MS = 3 * 60 * 1000;   // scene-change refreshes
constexpr uint32_t FORCE_GAP_MS = 15 * 1000;     // button presses
constexpr uint32_t STALE_MS = 60 * 60 * 1000;    // hourly, for the uptime

inline uint8_t g_fb[ROW_BYTES * PANEL_H];
inline spi_device_handle_t g_spi = nullptr;
inline gpio_num_t g_dc;
inline int g_cs, g_sck, g_mosi, g_miso;
inline char g_version[16] = "?";

// Written by the main loop (note_state), read by the panel task.
inline SemaphoreHandle_t g_lock = nullptr;
inline char g_scene[32] = "-";
inline char g_track[64] = "-";
inline std::atomic<bool> g_pending{false};
inline std::atomic<bool> g_force{false};

// ── Framebuffer drawing (bit set = white; eInk convention) ───────────

inline void px_black(int x, int y) {
  if (x < 0 || x >= W || y < 0 || y >= H) return;
  if (ROT180) { x = W - 1 - x; y = H - 1 - y; }
  // Landscape (x right, y down) -> panel RAM (rows run along the short axis).
  const int panel_x = H - 1 - y, panel_y = x;
  g_fb[panel_y * ROW_BYTES + panel_x / 8] &= ~(0x80 >> (panel_x % 8));
}

inline void text(int x, int y, const char *s, int scale = 1) {
  namespace f = castle_font;
  for (; *s; s++, x += f::W * scale) {
    const int c = (unsigned char) *s;
    if (c < f::FIRST || c > f::LAST) continue;
    const uint8_t *g = &f::GLYPHS[(c - f::FIRST) * f::H];
    for (int gy = 0; gy < f::H; gy++)
      for (int gx = 0; gx < f::W; gx++) {
        if (!(g[gy] & (0x80 >> gx))) continue;
        for (int sy = 0; sy < scale; sy++)
          for (int sx = 0; sx < scale; sx++)
            px_black(x + gx * scale + sx, y + gy * scale + sy);
      }
  }
}

inline void draw_qr(int x, int y, int scale) {
  namespace q = castle_qr;
  for (int my = 0; my < q::SIZE; my++)
    for (int mx = 0; mx < q::SIZE; mx++) {
      if (!(q::BITS[my * q::ROW_BYTES + mx / 8] & (0x80 >> (mx % 8)))) continue;
      for (int sy = 0; sy < scale; sy++)
        for (int sx = 0; sx < scale; sx++)
          px_black(x + mx * scale + sx, y + my * scale + sy);
    }
}

// ── SSD1680, minimal full-refresh driver ─────────────────────────────

inline void cmd(uint8_t c) {
  gpio_set_level(g_dc, 0);
  spi_transaction_t t{};
  t.length = 8;
  t.tx_buffer = &c;
  spi_device_polling_transmit(g_spi, &t);
}

inline void data(const uint8_t *d, size_t n) {
  gpio_set_level(g_dc, 1);
  while (n > 0) {
    const size_t chunk = n > 512 ? 512 : n;
    spi_transaction_t t{};
    t.length = chunk * 8;
    t.tx_buffer = d;
    spi_device_polling_transmit(g_spi, &t);
    d += chunk;
    n -= chunk;
    vTaskDelay(1);  // this task is not watched, but the idle task is
  }
}

inline void data1(uint8_t d) { data(&d, 1); }

/// Push the framebuffer to the panel and run a full refresh. ~5 s of fixed
/// delays — panel task only.
inline void flush() {
  cmd(0x12);  // software reset (never in deep sleep, so this always works)
  vTaskDelay(pdMS_TO_TICKS(20));
  cmd(0x01);  // driver output: 250 gate lines
  data1(PANEL_H - 1); data1(0x00); data1(0x00);
  cmd(0x11); data1(0x03);                       // data entry: x+, y+
  cmd(0x44); data1(0x00); data1(ROW_BYTES - 1); // x window, in bytes
  cmd(0x45); data1(0x00); data1(0x00);          // y window, 0..249
  data1(PANEL_H - 1); data1(0x00);
  cmd(0x3C); data1(0x05);                       // border: follow LUT
  cmd(0x21); data1(0x00); data1(0x80);          // RAM content option
  cmd(0x18); data1(0x80);                       // internal temp sensor
  cmd(0x4E); data1(0x00);                       // RAM counters home
  cmd(0x4F); data1(0x00); data1(0x00);
  cmd(0x24);
  data(g_fb, sizeof(g_fb));
  cmd(0x22); data1(0xF7);                       // full update sequence
  cmd(0x20);
  // No BUSY line on the wing: wait out the worst case instead of polling.
  vTaskDelay(pdMS_TO_TICKS(4500));
}

// ── The status layout ────────────────────────────────────────────────

inline void ip_string(char *out, size_t n) {
  esp_netif_t *sta = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
  esp_netif_ip_info_t info{};
  if (sta != nullptr && esp_netif_get_ip_info(sta, &info) == ESP_OK &&
      info.ip.addr != 0) {
    snprintf(out, n, IPSTR, IP2STR(&info.ip));
  } else {
    snprintf(out, n, "no wifi");
  }
}

inline void render() {
  memset(g_fb, 0xFF, sizeof(g_fb));
  char line[44];

  char scene[32], track[64];
  xSemaphoreTake(g_lock, portMAX_DELAY);
  strlcpy(scene, g_scene, sizeof(scene));
  strlcpy(track, g_track, sizeof(track));
  xSemaphoreGive(g_lock);

  snprintf(line, sizeof(line), "CASTLE v%s", g_version);
  text(4, -2, line, 2);

  snprintf(line, sizeof(line), "scene %.20s", scene);
  text(4, 28, line);
  // Track names come from the card and can be book-length; the trim is the
  // column width next to the QR code.
  snprintf(line, sizeof(line), "track %.21s", track);
  text(4, 41, line);

  if (castle_sd::g_mounted) {
    uint64_t total = 0, free_b = 0;
    if (esp_vfs_fat_info("/sd", &total, &free_b) == ESP_OK)
      snprintf(line, sizeof(line), "SD    %.1f GB free",
               free_b / (1024.0 * 1024.0 * 1024.0));
    else
      snprintf(line, sizeof(line), "SD    mounted");
  } else {
    snprintf(line, sizeof(line), "SD    ** NO CARD **");
  }
  text(4, 54, line);

  const uint32_t up_min = (uint32_t) (esp_timer_get_time() / 60000000ULL);
  snprintf(line, sizeof(line), "up    %ud %02u:%02u", up_min / 1440,
           (up_min / 60) % 24, up_min % 60);
  text(4, 67, line);

  snprintf(line, sizeof(line), "boots %u  crashes %u",
           (unsigned) castle_health::g_boots,
           (unsigned) castle_health::g_crashes);
  text(4, 80, line);
  snprintf(line, sizeof(line), "reset %s", castle_health::reason_str());
  text(4, 93, line);

  char ip[20];
  ip_string(ip, sizeof(ip));
  text(4, 108, ip);

  // QR to the web remote, right-hand column. 25 modules x3 = 75 px; the
  // white page around it is the quiet zone.
  draw_qr(W - 75 - 6, H - 75 - 6, 3);
  text(W - 75 - 6, 2, "remote", 1);
}

// ── Task + public surface ────────────────────────────────────────────

inline void task(void *) {
  // The SD mount usually created the bus already; if there was no card the
  // bus may or may not exist, so try and accept "already initialised".
  spi_bus_config_t bus{};
  bus.sclk_io_num = g_sck;
  bus.mosi_io_num = g_mosi;
  bus.miso_io_num = g_miso;
  bus.quadwp_io_num = -1;
  bus.quadhd_io_num = -1;
  // Same host the SD driver picks, so a card-less boot still lands the panel
  // on the bus the next mount will want.
  const sdmmc_host_t sd_host = SDSPI_HOST_DEFAULT();
  const auto host = (spi_host_device_t) sd_host.slot;
  esp_err_t err = spi_bus_initialize(host, &bus, SPI_DMA_CH_AUTO);
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_LOGE(TAG, "SPI bus unavailable: %s — no status screen",
             esp_err_to_name(err));
    vTaskDelete(nullptr);
    return;
  }

  gpio_set_direction(g_dc, GPIO_MODE_OUTPUT);
  spi_device_interface_config_t dev{};
  dev.mode = 0;
  dev.clock_speed_hz = 10 * 1000 * 1000;
  dev.spics_io_num = g_cs;
  dev.queue_size = 2;
  if (spi_bus_add_device(host, &dev, &g_spi) != ESP_OK) {
    ESP_LOGE(TAG, "cannot add panel to SPI bus — no status screen");
    vTaskDelete(nullptr);
    return;
  }

  uint32_t last = 0;
  bool ever = false;
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(1000));
    const uint32_t now = (uint32_t) (esp_timer_get_time() / 1000ULL);
    const uint32_t gap = g_force.load() ? FORCE_GAP_MS : MIN_GAP_MS;
    const bool due = g_pending.load() || g_force.load() ||
                     (ever && now - last > STALE_MS) || !ever;
    if (!due) continue;
    if (ever && now - last < gap) continue;  // flags stay set; retried later
    g_pending.store(false);
    g_force.store(false);
    last = now;
    ever = true;
    render();
    flush();
    ESP_LOGI(TAG, "panel refreshed");
  }
}

/// Start the panel task. Call after the SD mount attempt (bus reuse), from
/// on_boot. Pin numbers are the wing's fixed wiring, passed from YAML so the
/// pin map stays in one place.
inline void begin(const char *version, int cs, int dc, int sck, int mosi,
                  int miso) {
  strlcpy(g_version, version, sizeof(g_version));
  g_cs = cs;
  g_dc = (gpio_num_t) dc;
  g_sck = sck;
  g_mosi = mosi;
  g_miso = miso;
  g_lock = xSemaphoreCreateMutex();
  // 4 KB stack: render() uses ~200 B of locals; snprintf is the deep call.
  xTaskCreate(task, "eink", 4096, nullptr, 2, nullptr);
}

/// Mirror show state in from the 200 ms bridge interval. Cheap to call every
/// tick; only a scene change queues a refresh.
inline void note_state(const std::string &scene, const std::string &track) {
  if (g_lock == nullptr) return;
  xSemaphoreTake(g_lock, portMAX_DELAY);
  const bool changed = scene != g_scene;
  strlcpy(g_scene, scene.empty() ? "-" : scene.c_str(), sizeof(g_scene));
  strlcpy(g_track, track.empty() ? "-" : track.c_str(), sizeof(g_track));
  xSemaphoreGive(g_lock);
  if (changed) g_pending.store(true);
}

/// The "Refresh eInk" button: refresh soon, bench-friendly rate limit.
inline void request_refresh() { g_force.store(true); }

}  // namespace castle_eink
