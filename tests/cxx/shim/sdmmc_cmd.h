#pragma once
// The card as the SDSPI driver describes it: enough of sdmmc_card_t for
// sd_audio.h's mount log to compile (CID name, CSD capacity and sector
// size), and nothing that would pretend a card is present.

#include <cstdint>
#include <driver/spi_common.h>

typedef struct {
  char name[9];
} sdmmc_cid_t;

typedef struct {
  int capacity;
  int sector_size;
} sdmmc_csd_t;

typedef struct {
  sdmmc_cid_t cid;
  sdmmc_csd_t csd;
} sdmmc_card_t;

typedef struct {
  int slot;
  int max_freq_khz;
  uint32_t flags;
} sdmmc_host_t;

#define SDSPI_HOST_DEFAULT()                                                   \
  { /*slot*/ SPI2_HOST, /*max_freq_khz*/ 20000, /*flags*/ 0 }
