// The live-light contract of firmware/sd_web_state.h, run on this machine.
//
// A synced import streams colour frames at ~4 Hz into a mailbox the main
// loop drains every 200 ms, while the same mailbox carries the stop, the
// volume and the play a hand presses. tests/test_live_light_playback.py
// compiles and runs this; a failed check prints its line and exits 1.
#include <cstdio>

#include "sd_web_state.h"

namespace {
int failures = 0;
void check(bool ok, const char *what, int line) {
  if (!ok) { std::printf("FAIL line %d: %s\n", line, what); failures++; }
}
#define CHECK(x) check((x), #x, __LINE__)
}  // namespace

int main() {
  using castle_web::Action;
  using castle_web::ActionType;
  using castle_web::set_pending;
  using castle_web::take_pending;

  // An empty mailbox is the normal state.
  CHECK(take_pending().type == ActionType::NONE);

  // A light frame that lands in the same tick as a stop does NOT evict it:
  // "stop" on a synced import used to vanish into the frame stream. The
  // frame itself is dropped, and since v5.60 counted as the eviction it is.
  const unsigned evicted_before = castle_web::g_light_evicted.load();
  set_pending(ActionType::STOP, "");
  set_pending(ActionType::LIGHT, "ff0000");
  CHECK(take_pending().type == ActionType::STOP);
  CHECK(take_pending().type == ActionType::NONE);
  CHECK(castle_web::g_light_evicted.load() == evicted_before + 1);

  // Volume and PIR survive the same way — the slider used to snap back.
  set_pending(ActionType::VOLUME, "35");
  set_pending(ActionType::LIGHT, "112233");
  set_pending(ActionType::LIGHT, "445566");
  {
    const Action a = take_pending();
    CHECK(a.type == ActionType::VOLUME && a.arg == "35");
  }

  // A colour-picker drag still lands its last colour: LIGHT over LIGHT wins.
  set_pending(ActionType::LIGHT, "ff0000");
  set_pending(ActionType::LIGHT, "00ff00");
  {
    const Action a = take_pending();
    CHECK(a.type == ActionType::LIGHT && a.arg == "00ff00");
  }

  // A light frame takes an empty slot as before.
  set_pending(ActionType::LIGHT, "off");
  CHECK(take_pending().arg == "off");

  // A flashed image reboots even when later requests take the slot: the web
  // OTA's restart is a latch, drained ahead of whatever else is waiting.
  set_pending(ActionType::RESTART, "");
  set_pending(ActionType::LIGHT, "ff0000");
  set_pending(ActionType::SCENE, "vigil");
  CHECK(take_pending().type == ActionType::RESTART);
  {
    const Action a = take_pending();
    CHECK(a.type == ActionType::SCENE && a.arg == "vigil");
  }
  CHECK(take_pending().type == ActionType::NONE);

  // Handing the pixels back to Show is the COMMAND "show", optionally for
  // one zone or at a brightness — never a zone that merely contains it.
  using castle_web::light_spec_is_show;
  CHECK(light_spec_is_show("show"));
  CHECK(light_spec_is_show("show@50"));
  CHECK(light_spec_is_show("roof:show"));
  CHECK(!light_spec_is_show("show:off"));
  CHECK(!light_spec_is_show("shower:off"));
  CHECK(!light_spec_is_show("shower:ff00aa"));
  CHECK(!light_spec_is_show("off"));
  CHECK(!light_spec_is_show("ff00aa"));

  if (failures == 0) std::printf("light mailbox OK\n");
  return failures == 0 ? 0 : 1;
}
