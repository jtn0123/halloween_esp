// The pending-action mailbox and the event ring of sd_web_state.h, driven
// by a script on stdin so a seeded fuzz in Python can hold the REAL C to a
// model, step by step. events_check.cpp asks the questions someone thought
// of; this executes whatever order of set/take/note/record the generator
// draws, thousands of them, and prints what the C did so the runner can
// compare it with what the emulator's port (tools/castle_emu_events.py)
// says should have happened.
//
// Lines, one command each, args hex-encoded so any byte can travel:
//   S <type> <hex>         set_pending(type, arg)
//   N <now_us>             note_light_evictions(now_us)
//   T <now_us>             take_pending(); record_action() unless NONE
//                          -> "taken <type> <hex>"
//   E <kind> <now_us> <hex> record_event(kind, arg, now_us)
//   D                      -> "counters <applied> <evicted>" then
//                             "events <bytes>" and the events_json bytes
//   Q                      quit
#include <esp_http_server.h>

#include <cstdio>
#include <cstdlib>
#include <string>

#include "sd_web_events.h"
#include "sd_web_state.h"

namespace {

using castle_web::ActionType;
using castle_web::EventKind;

std::string unhex(const char *s) {
  std::string out;
  for (; s[0] != '\0' && s[1] != '\0'; s += 2) {
    const char pair[3] = {s[0], s[1], '\0'};
    out.push_back((char) strtoul(pair, nullptr, 16));
  }
  return out;
}

void hex(const std::string &s) {
  for (const unsigned char c : s) printf("%02x", c);
}

bool read_line(std::string &out) {
  out.clear();
  int c;
  while ((c = fgetc(stdin)) != EOF) {
    if (c == '\n') return true;
    out.push_back((char) c);
  }
  return !out.empty();
}

}  // namespace

int main() {
  std::string line;
  while (read_line(line)) {
    unsigned type = 0;
    long long now = 0;
    char buf[512] = {0};
    if (line == "Q") break;
    if (line == "D") {
      printf("counters %u %u\n", castle_web::g_light_applied.load(),
             castle_web::g_light_evicted.load());
      castle_web::Event evs[castle_web::kEventRing];
      const size_t n = castle_web::copy_events(evs);
      const std::string json = castle_web::events_json(evs, n);
      printf("events %zu\n", json.size());
      fwrite(json.data(), 1, json.size(), stdout);
      fflush(stdout);
      continue;
    }
    if (sscanf(line.c_str(), "S %u %511s", &type, buf) == 2 ||
        sscanf(line.c_str(), "S %u", &type) == 1) {
      castle_web::set_pending((ActionType) type, unhex(buf));
    } else if (sscanf(line.c_str(), "N %lld", &now) == 1) {
      castle_web::note_light_evictions(now);
    } else if (sscanf(line.c_str(), "T %lld", &now) == 1) {
      const castle_web::Action a = castle_web::take_pending();
      if (a.type != ActionType::NONE) castle_web::record_action(a.type, a.arg, now);
      printf("taken %u ", (unsigned) a.type);
      hex(a.arg);
      printf("\n");
      fflush(stdout);
    } else if (sscanf(line.c_str(), "E %u %lld %511s", &type, &now, buf) >= 2) {
      castle_web::record_event((EventKind) type, unhex(buf), now);
    } else {
      fprintf(stderr, "mailbox_fuzz_check: bad line %s\n", line.c_str());
      return 2;
    }
  }
  return 0;
}
