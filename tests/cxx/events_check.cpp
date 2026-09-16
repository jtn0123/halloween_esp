// The event ring and the light counters of firmware/sd_web_state.h, run on
// this machine, plus the JSON firmware/sd_web_events.h renders them into.
//
// The ring is what /api/events hands a page that polls once a second and
// therefore cannot see a scene that started and stopped in between, so the
// two properties that matter are order (oldest first) and the wraparound at
// 64 — a ring that dropped the NEWEST entry, or renumbered on wrap, would
// still answer 200 and still be useless. tests/test_firmware_web_cxx.py
// compiles and runs this; a failed check prints its line and exits 1.
#include <cstdio>
#include <cstdlib>
#include <string>

#include "sd_web_events.h"
#include "sd_web_state.h"

namespace {
int failures = 0;
void check(bool ok, const char *what, int line) {
  if (!ok) { std::printf("FAIL line %d: %s\n", line, what); failures++; }
}
#define CHECK(x) check((x), #x, __LINE__)
constexpr long long MS = 1000;   // microseconds per millisecond

/// The ring as /api/events would send it.
std::string json() {
  std::array<castle_web::Event, castle_web::kEventRing> snap{};
  const size_t n = castle_web::copy_events(snap.data());
  return castle_web::events_json(snap.data(), n);
}
}  // namespace

int main() {
  using castle_web::ActionType;
  using castle_web::EventKind;
  using castle_web::record_action;
  using castle_web::record_event;

  // Nothing has happened yet: an empty ARRAY, not a null or an object.
  CHECK(json() == "[]");

  // One line per command the main loop executed, in the order it ran them,
  // with the command's own argument.
  record_action(ActionType::PLAY, "wicked_winds.mp3", 1000 * MS);
  record_action(ActionType::SCENE, "vigil", 2000 * MS);
  record_action(ActionType::VOLUME, "45", 3000 * MS);
  CHECK(json() ==
        R"([{"t":1000,"e":"play","a":"wicked_winds.mp3"},)"
        R"({"t":2000,"e":"scene","a":"vigil"},)"
        R"({"t":3000,"e":"volume","a":"45"}])");

  // A LIGHT is far too frequent to record: it counts instead. PIRCFG is not
  // a show event at all, and neither is an empty mailbox.
  const size_t before = castle_web::g_events_written;
  record_action(ActionType::LIGHT, "ff0000", 4000 * MS);
  record_action(ActionType::LIGHT, "00ff00", 4200 * MS);
  record_action(ActionType::PIRCFG, "1|60|storm", 4400 * MS);
  record_action(ActionType::NONE, "", 4600 * MS);
  CHECK(castle_web::g_events_written == before);
  CHECK(castle_web::g_light_applied.load() == 2);

  // The mailbox's own rule: a LIGHT replacing a pending LIGHT drops a
  // frame and counts it; a LIGHT that finds a STOP waiting does not touch
  // the slot at all, and is not an eviction of anything.
  castle_web::set_pending(ActionType::LIGHT, "111111");
  castle_web::set_pending(ActionType::LIGHT, "222222");
  castle_web::set_pending(ActionType::LIGHT, "333333");
  CHECK(castle_web::g_light_evicted.load() == 2);
  CHECK(castle_web::take_pending().arg == "333333");
  castle_web::set_pending(ActionType::STOP, "");
  castle_web::set_pending(ActionType::LIGHT, "444444");
  CHECK(castle_web::g_light_evicted.load() == 2);
  CHECK(castle_web::take_pending().type == ActionType::STOP);

  // The dropped frames are ONE line a second at most, carrying how many
  // were lost since the last one — an import streaming at 4 Hz must not
  // push every other event out of the ring.
  const size_t lines = castle_web::g_events_written;
  castle_web::note_light_evictions(5000 * MS);
  CHECK(castle_web::g_events_written == lines + 1);
  castle_web::g_light_evicted.fetch_add(3);
  castle_web::note_light_evictions(5200 * MS);   // inside the second: silent
  CHECK(castle_web::g_events_written == lines + 1);
  castle_web::note_light_evictions(6200 * MS);   // a second later: the backlog
  CHECK(castle_web::g_events_written == lines + 2);
  castle_web::note_light_evictions(9000 * MS);   // nothing new to report
  CHECK(castle_web::g_events_written == lines + 2);
  CHECK(json().find(R"({"t":5000,"e":"light_evicted","a":"2"})") != std::string::npos);
  CHECK(json().find(R"({"t":6200,"e":"light_evicted","a":"3"})") != std::string::npos);

  // The audio clock's two transitions: the speaker started (sound), and
  // playback ended on its own (silent). Both carry an empty arg.
  castle_web::restart_audio_clock(10000 * MS);
  castle_web::mirror_audio(true, false, 10200 * MS);   // armed, not audible
  const size_t quiet = castle_web::g_events_written;
  castle_web::mirror_audio(true, true, 10400 * MS);    // the amplifier has it
  CHECK(castle_web::g_events_written == quiet + 1);
  castle_web::mirror_audio(true, true, 10600 * MS);    // still sounding: no line
  CHECK(castle_web::g_events_written == quiet + 1);
  castle_web::mirror_audio(false, false, 10800 * MS);  // the song ended
  CHECK(castle_web::g_events_written == quiet + 2);
  CHECK(json().find(R"({"t":10400,"e":"sound","a":""})") != std::string::npos);
  CHECK(json().find(R"({"t":10800,"e":"silent","a":""})") != std::string::npos);

  // An arg longer than the ring's slot is truncated, not heaped.
  record_event(EventKind::PLAY, std::string(80, 'a'), 11000 * MS);
  std::array<castle_web::Event, castle_web::kEventRing> snap{};
  size_t n = castle_web::copy_events(snap.data());
  CHECK(std::string(snap[n - 1].arg).size() == castle_web::kEventArgMax - 1);

  // Wraparound: fill past 64 and the window is the LAST 64, oldest first,
  // with no gap and no repeat at the seam.
  for (int i = 0; i < 100; i++)
    record_event(EventKind::VOLUME, std::to_string(i), (20000 + i) * MS);
  n = castle_web::copy_events(snap.data());
  CHECK(n == castle_web::kEventRing);
  for (size_t i = 0; i < n; i++) {
    CHECK(snap[i].t_ms == (long long) (20036 + i));
    CHECK(std::string(snap[i].arg) == std::to_string(36 + i));
  }
  // And the rendering agrees with the copy it was handed.
  const std::string out = json();
  CHECK(out.rfind(R"([{"t":20036,"e":"volume","a":"36"},)", 0) == 0);
  CHECK(out.size() > 64 && out.back() == ']');
  CHECK(out.find(R"({"t":20099,"e":"volume","a":"99"}])") != std::string::npos);
  CHECK(out.find(R"("t":20035)") == std::string::npos);

  if (failures == 0) std::printf("event ring OK\n");
  return failures == 0 ? 0 : 1;
}
