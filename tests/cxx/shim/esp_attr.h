#pragma once
// RTC_NOINIT_ATTR, on a machine with no RTC slow memory.
//
// The attribute is what makes castle_rtc.h's block survive a panic on the
// device: it names a section the bootloader does not clear on a warm start.
// A host has no such section and no such reset, so the block is ordinary
// static storage here — which is exactly right for the harness, because
// what the tests are judging is the LAYOUT and the checksum, not the
// silicon. tests/cxx/events_check.cpp drives begin()/record()/start_fresh()
// over it directly.

#define RTC_NOINIT_ATTR
#define RTC_DATA_ATTR
#define IRAM_ATTR
