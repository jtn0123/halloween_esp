#pragma once
// The SDSPI slot config sd_audio.h fills in before mounting.

#include <driver/spi_common.h>

typedef struct {
  spi_host_device_t host_id;
  gpio_num_t gpio_cs;
  gpio_num_t gpio_cd;
  gpio_num_t gpio_wp;
  gpio_num_t gpio_int;
} sdspi_device_config_t;

#define SDSPI_DEVICE_CONFIG_DEFAULT()                                          \
  { SPI2_HOST, -1, -1, -1, -1 }
