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

#include "castle_rtc.h"
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

  // What castle_health::init() does first thing, and the firmware's own
  // precondition for the RTC copy: decide whether the segment holds a
  // previous life's block, and stamp a fresh one when it does not.
  castle_rtc::begin();

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

  // The mailbox's own rule: a LIGHT replacing a pending LIGHT drops the
  // frame underneath and counts it; a LIGHT that finds a STOP waiting
  // leaves the slot alone and is dropped ITSELF — also a frame that never
  // ran, and since v5.60 also counted (A6), so a page can reconcile
  // applied + evicted against what it sent.
  castle_web::set_pending(ActionType::LIGHT, "111111");
  castle_web::set_pending(ActionType::LIGHT, "222222");
  castle_web::set_pending(ActionType::LIGHT, "333333");
  CHECK(castle_web::g_light_evicted.load() == 2);
  CHECK(castle_web::take_pending().arg == "333333");
  castle_web::set_pending(ActionType::STOP, "");
  castle_web::set_pending(ActionType::LIGHT, "444444");
  CHECK(castle_web::g_light_evicted.load() == 3);
  CHECK(castle_web::take_pending().type == ActionType::STOP);

  // The dropped frames are ONE line a second at most, carrying how many
  // were lost since the last one — an import streaming at 4 Hz must not
  // push every other event out of the ring.
  const size_t lines = castle_web::g_events_written;
  castle_web::note_light_evictions(5000 * MS);
  CHECK(castle_web::g_events_written == lines + 1);
  castle_web::g_light_evicted.fetch_add(4);
  castle_web::note_light_evictions(5200 * MS);   // inside the second: silent
  CHECK(castle_web::g_events_written == lines + 1);
  castle_web::note_light_evictions(6200 * MS);   // a second later: the backlog
  CHECK(castle_web::g_events_written == lines + 2);
  castle_web::note_light_evictions(9000 * MS);   // nothing new to report
  CHECK(castle_web::g_events_written == lines + 2);
  CHECK(json().find(R"({"t":5000,"e":"light_evicted","a":"3"})") != std::string::npos);
  CHECK(json().find(R"({"t":6200,"e":"light_evicted","a":"4"})") != std::string::npos);

  // The audio clock's two transitions: the speaker started (sound), and
  // playback ended on its own (silent). v5.62 (L10): the first NAMES the
  // track and the second says how many milliseconds of it were audible —
  // both carried an empty arg until then, which made the pair useless for
  // the only question worth asking of it the next morning ("did the track
  // that killed it always kill it").
  castle_web::restart_audio_clock(10000 * MS);
  castle_web::mirror_audio(true, false, 10200 * MS, "10_ballad.mp3");
  const size_t quiet = castle_web::g_events_written;
  castle_web::mirror_audio(true, true, 10400 * MS, "10_ballad.mp3");
  CHECK(castle_web::g_events_written == quiet + 1);
  castle_web::mirror_audio(true, true, 10600 * MS, "10_ballad.mp3");
  CHECK(castle_web::g_events_written == quiet + 1);
  castle_web::mirror_audio(false, false, 10800 * MS, "10_ballad.mp3");
  CHECK(castle_web::g_events_written == quiet + 2);
  CHECK(json().find(R"({"t":10400,"e":"sound","a":"10_ballad.mp3"})") !=
        std::string::npos);
  CHECK(json().find(R"({"t":10800,"e":"silent","a":"400"})") != std::string::npos);
  // A clock that was still ARMED when the pipeline gave up never made a
  // sound: the honest answer is 0 ms, not "however long we waited".
  castle_web::restart_audio_clock(12000 * MS);
  castle_web::mirror_audio(false, false, 12100 * MS, "quiet.mp3");
  castle_web::mirror_audio(false, false, 13800 * MS, "quiet.mp3");
  CHECK(json().find(R"({"t":13800,"e":"silent","a":"0"})") != std::string::npos);

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

  // ── L1: the copy that survives the crash (castle_rtc.h) ───────────────
  // Every line above also went into RTC slow memory, 10 characters of arg
  // each. The ring in RAM is gone after a panic; this one is the whole of
  // what castle_health::log_boot_to_sd writes to the card at the next boot.
  CHECK(castle_rtc::g_rtc.written == castle_web::g_events_written);
  CHECK(castle_rtc::g_rtc.magic == castle_rtc::kMagic);
  CHECK(castle_rtc::g_rtc.check == castle_rtc::compute_check());
  // The truncation is the RTC ring's own, not the RAM ring's.
  castle_web::record_event(EventKind::PLAY, "a_very_long_track_name.mp3",
                           30000 * MS);
  const castle_rtc::Slot &last =
      castle_rtc::g_rtc.ev[(castle_rtc::g_rtc.written - 1) % castle_rtc::kRing];
  CHECK(std::string(last.arg) == "a_very_lon");
  CHECK(std::string(last.arg).size() == castle_rtc::kArg - 1);

  // A reboot: begin() decides whether what is in the segment is a previous
  // life's, the boot-time dump reads it, and then this life starts fresh.
  const uint32_t lived = castle_rtc::g_rtc.written;
  castle_rtc::begin();
  CHECK(castle_rtc::g_prev_valid);
  CHECK(castle_rtc::prev_held() == castle_rtc::kRing);
  CHECK(std::string(castle_rtc::prev_slot(castle_rtc::kRing - 1).arg) ==
        "a_very_lon");
  CHECK(castle_rtc::g_prev_written == lived);
  castle_rtc::start_fresh();
  CHECK(castle_rtc::g_rtc.written == 0);
  CHECK(castle_rtc::g_rtc.check == castle_rtc::compute_check());

  // A COLD boot is the segment holding anything else at all: one flipped
  // byte and the block is refused rather than dumped as a story.
  castle_rtc::record(castle_rtc::Kind::PLAY, "vigil", 100 * MS);
  castle_rtc::g_rtc.ev[0].arg[0] ^= 0x20;
  castle_rtc::begin();
  CHECK(!castle_rtc::g_prev_valid);
  CHECK(castle_rtc::prev_held() == 0);
  CHECK(castle_rtc::g_rtc.written == 0);   // begin() wiped what it refused

  if (failures == 0) std::printf("event ring OK\n");
  return failures == 0 ? 0 : 1;
}
