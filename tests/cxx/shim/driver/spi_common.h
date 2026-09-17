#pragma once
// The SPI bus, declared and never driven. sd_audio.h's mount() is compiled
// by this harness (sd_web.h includes the header for castle_sd::g_mounted
// and g_quiesce) but never called: there is no card to bring up, only a
// directory the fs shim points /sd at.

#include <cstdint>
#include <esp_err.h>

typedef int gpio_num_t;

typedef enum {
  SPI1_HOST = 0,
  SPI2_HOST = 1,
  SPI3_HOST = 2,
} spi_host_device_t;

typedef enum {
  SPI_DMA_DISABLED = 0,
  SPI_DMA_CH_AUTO = 3,
} spi_common_dma_t;

typedef struct {
  int mosi_io_num;
  int miso_io_num;
  int sclk_io_num;
  int quadwp_io_num;
  int quadhd_io_num;
  int max_transfer_sz;
  uint32_t flags;
  int intr_flags;
} spi_bus_config_t;

inline esp_err_t spi_bus_initialize(spi_host_device_t host,
                                    const spi_bus_config_t *cfg,
                                    spi_common_dma_t dma) {
  (void) host;
  (void) cfg;
  (void) dma;
  return ESP_OK;
}
