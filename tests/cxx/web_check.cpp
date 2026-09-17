// The castle's web API, running on this machine.
//
// firmware/sd_web.h and the six headers it pulls in are the only part of
// the firmware that a Mac can drive end to end: the handlers are plain
// functions of a httpd_req_t, and everything below them — the httpd, the
// card, the OTA slot, the clock — is small enough to fake honestly
// (tests/cxx/shim/). So this program compiles THE REAL HEADERS, starts the
// server the same start() the device calls, and answers requests on a pipe.
//
// tests/test_firmware_web_cxx.py drives it beside tools/castle_emu.py and
// compares the two answers byte for byte. That is the point: until now the
// emulator was held to the firmware by PARSING it, which catches a renamed
// route or a changed error string and cannot catch a handler that decides
// differently.
//
// PROTOCOL (stdin/stdout, many requests per process, binary-safe):
//
//   request   "<METHOD> <urilen> <declared> <actual> <port>\n"
//             <urilen> bytes of request target, then <actual> body bytes.
//             `declared` is the Content-Length the request claims, which is
//             deliberately allowed to exceed `actual` — that gap is how the
//             413 cap, the 507 precondition and the short-write leg are
//             reached. "QUIT\n" ends the run.
//   response  "<status> <bodylen> <nhdr>\n", <nhdr> lines of "name: value",
//             then <bodylen> raw body bytes. Content-Type is the first
//             header; the rest follow in the order the handler set them.
//   tick      "TICK <now_us> <playing> <sounding>\n" — ONE main-loop tick
//             (C6). `playing` is the media pipeline's word and `sounding`
//             the speaker's, the two inputs castle_sd_common.yaml's 200 ms
//             interval reads off ESPHome before it calls into the firmware.
//             Answered with "<ACTION> <arglen>\n" + <arglen> arg bytes,
//             naming what the tick drained from the mailbox ("NONE" most
//             of the time). Until this existed nothing downstream of
//             set_pending was ever run in C: the mirror, the event ring
//             and the audio clock were emulator-only, which is the
//             structural reason C3, C4 and C7 were invisible to the pair.
//
// The URI is length-prefixed rather than whitespace-delimited because the
// fuzz sends names that decode to spaces and newlines, and a protocol that
// could not carry them would quietly stop testing them.
//
// `--rules` is a second mode for the byte rules alone: lines of "<len>\n"
// + <len> bytes, answered with safe_name's verdict and url_decode's and
// json_escape's output, so those three are RUN in C rather than re-derived.

#include <castle_shim.h>

#include "sd_web.h"

namespace {

// The show state the main loop publishes. On the device these are ESPHome
// template entities the generated scene scripts write (current_scene,
// current_track, pir_scene); here they are three strings that TICK moves
// exactly the way castle_sd_common.yaml's lambda moves them, because
// /api/status prints them and a pair test has to be able to get the castle
// into a state rather than being handed one at startup. (C6)
std::string g_scene, g_track, g_pir_scene;

/// The device's globals, seeded the way the main loop would have by the
/// time a request arrives. Every one of them is something /api/status
/// prints, so the emulator can be constructed to match and the comparison
/// is about the handler rather than about boot timing.
void seed_from_env() {
  castle_sd::g_mounted = castle_shim::env("CASTLE_MOUNTED", "1") != "0";
  // A1 (v5.61): the flag sd_web_ota.h raises while it burns flash. On the
  // device it is up for the length of an upload; here it is simply given,
  // so the gate the stream server now honours can be driven without an OTA
  // in flight.
  castle_sd::g_quiesce = castle_shim::env("CASTLE_QUIESCE", "0") != "0";

  // The ids this "build" was compiled with — /api/scene 404s anything else.
  std::vector<std::string> ids;
  const std::string csv = castle_shim::env("CASTLE_SCENE_IDS");
  for (size_t i = 0; i < csv.size();) {
    const size_t c = csv.find(',', i);
    const std::string id = csv.substr(i, c == std::string::npos ? c : c - i);
    if (!id.empty()) ids.push_back(id);
    if (c == std::string::npos) break;
    i = c + 1;
  }
  castle_web::set_scene_ids(ids);
  castle_web::set_missing(castle_shim::env("CASTLE_MISSING"));
  castle_web::g_volume = (int) castle_shim::env_ul("CASTLE_VOLUME", 70);
  // LAST, and for the same reason the device calls it last: since v5.60
  // this is the one publish point, copying every atomic above into the
  // snapshot /api/status answers from (sd_web_state.h).
  g_scene = castle_shim::env("CASTLE_SCENE");
  g_track = castle_shim::env("CASTLE_TRACK");
  g_pir_scene = castle_shim::env("CASTLE_PIR_SCENE");
  castle_web::mirror_show_state(g_scene, g_track, g_pir_scene);

  // /api/health's counters. The device reads them out of NVS at boot; here
  // they are given, so the reply is deterministic and every branch of
  // reason_str() is reachable from a test.
  castle_health::g_boots = (uint32_t) castle_shim::env_ul("CASTLE_BOOTS", 3);
  castle_health::g_crashes = (uint32_t) castle_shim::env_ul("CASTLE_CRASHES", 0);
  castle_health::g_reason = esp_reset_reason();

  // /api/bootlog reads the ring, and an unallocated ring answers with a
  // different sentence entirely. One captured line exercises the header,
  // the chunked body and the terminator.
  castle_log::init();
  castle_log::capture(3, "emu", "up");
}

int method_code(const std::string &m) {
  if (m == "GET") return HTTP_GET;
  if (m == "POST") return HTTP_POST;
  if (m == "PUT") return HTTP_PUT;
  if (m == "DELETE") return HTTP_DELETE;
  if (m == "HEAD") return HTTP_HEAD;
  if (m == "PATCH") return HTTP_PATCH;
  if (m == "OPTIONS") return HTTP_OPTIONS;
  return -1;
}

/// Exactly `n` bytes off stdin, or false at end of input.
bool read_exact(std::string &out, size_t n) {
  out.assign(n, '\0');
  return n == 0 || fread(&out[0], 1, n, stdin) == n;
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

void write_response(const castle_shim::Response &r) {
  // The abort rides as a header of its own: the handler returning ESP_FAIL
  // under a half-sent body is httpd closing the socket on the device, and
  // there is no status line left to say so (A8). No handler sets it in the
  // ordinary course, so the pair comparison never sees it.
  printf("%d %zu %zu\n", r.status, r.body.size(),
         r.hdrs.size() + (r.aborted ? 2 : 1));
  printf("Content-Type: %s\n", r.type.c_str());
  if (r.aborted) printf("X-Castle-Aborted: 1\n");
  for (const auto &h : r.hdrs) printf("%s: %s\n", h.name.c_str(), h.value.c_str());
  fwrite(r.body.data(), 1, r.body.size(), stdout);
  fflush(stdout);
}

const char *action_name(castle_web::ActionType t) {
  switch (t) {
    case castle_web::ActionType::NONE: return "NONE";
    case castle_web::ActionType::PLAY: return "PLAY";
    case castle_web::ActionType::SCENE: return "SCENE";
    case castle_web::ActionType::STOP: return "STOP";
    case castle_web::ActionType::VOLUME: return "VOLUME";
    case castle_web::ActionType::LIGHT: return "LIGHT";
    case castle_web::ActionType::PIRCFG: return "PIRCFG";
    case castle_web::ActionType::RESTART: return "RESTART";
    case castle_web::ActionType::SHOW: return "SHOW";
    case castle_web::ActionType::BLACKOUT: return "BLACKOUT";
  }
  return "NONE";
}

/// ONE 200 ms interval tick, in castle_sd_common.yaml's own order: mirror
/// the audio clock, publish the snapshot, rate-limit the dropped-frame
/// line, drain the mailbox, record what was drained, run it. (C6)
///
/// The half that is NOT the firmware — run_scene, the media player calls,
/// the pixel scripts — is modelled by the few string moves below, and only
/// those: every line the device shares with this program (mirror_audio,
/// mirror_show_state, note_light_evictions, take_pending, record_action,
/// restart_audio_clock) is the real firmware header being run. Note the
/// order: the snapshot is published BEFORE the mailbox is drained, so a
/// command applied on this tick is only visible to /api/status on the next
/// one — the same 200 ms of lag the porch has.
castle_web::Action tick(long long now_us, bool playing, bool sounding) {
  if (castle_web::mirror_audio(playing, sounding, now_us) && g_scene == "stop" &&
      !g_track.empty())
    g_track.clear();
  castle_web::mirror_show_state(g_scene, g_track, g_pir_scene);
  castle_web::note_light_evictions(now_us);
  const castle_web::Action act = castle_web::take_pending();
  if (act.type == castle_web::ActionType::NONE) return act;
  castle_web::record_action(act.type, act.arg, now_us);
  switch (act.type) {
    case castle_web::ActionType::PLAY:
      // A raw file has no scene: the YAML publishes "stop" so a live light
      // frame does not stop the file (v5.52).
      g_track = act.arg;
      g_scene = "stop";
      castle_web::restart_audio_clock(now_us);
      break;
    case castle_web::ActionType::SCENE:
      // run_scene publishes current_scene; a scene script does NOT publish
      // current_track (only scene_stop clears it), so the track name a
      // previous /api/play left survives a scene on the device.
      if (act.arg == "stop") {
        g_scene = "stop";
        g_track.clear();
      } else if (act.arg != "halt") {
        g_scene = act.arg;
      }
      if (act.arg != "halt" && act.arg != "stop") castle_web::restart_audio_clock(now_us);
      break;
    case castle_web::ActionType::STOP:
    case castle_web::ActionType::BLACKOUT:
      g_scene = "stop";
      g_track.clear();
      break;
    case castle_web::ActionType::SHOW:
      castle_web::g_show_on.store(act.arg == "1");
      if (act.arg != "1") {   // quiet means the scene it was mid-way through
        g_scene = "stop";
        g_track.clear();
      }
      break;
    case castle_web::ActionType::VOLUME:
      castle_web::g_volume.store(atoi(act.arg.c_str()));
      break;
    case castle_web::ActionType::PIRCFG: {
      const size_t p1 = act.arg.find('|');
      const size_t p2 = act.arg.rfind('|');
      const std::string a = act.arg.substr(0, p1);
      const std::string c = act.arg.substr(p1 + 1, p2 - p1 - 1);
      const std::string sc = act.arg.substr(p2 + 1);
      if (!a.empty()) castle_web::g_pir_armed.store(a == "1" || a == "true" || a == "on");
      if (!c.empty()) castle_web::g_pir_cooldown.store(atoi(c.c_str()));
      if (!sc.empty()) g_pir_scene = sc;
      break;
    }
    default:   // LIGHT is a counter, RESTART reboots the board
      break;
  }
  return act;
}

int serve() {
  std::string line;
  while (read_line(line)) {
    if (line == "QUIT") break;
    if (line.compare(0, 5, "TICK ") == 0) {
      long long now_us = 0;
      int playing = 0, sounding = 0;
      if (sscanf(line.c_str() + 5, "%lld %d %d", &now_us, &playing, &sounding) != 3) {
        fprintf(stderr, "web_check: bad tick line %s\n", line.c_str());
        return 2;
      }
      const castle_web::Action act = tick(now_us, playing != 0, sounding != 0);
      printf("%s %zu\n", action_name(act.type), act.arg.size());
      fwrite(act.arg.data(), 1, act.arg.size(), stdout);
      fflush(stdout);
      continue;
    }
    char method[16] = {0};
    unsigned long urilen = 0, declared = 0, actual = 0, port = 0;
    if (sscanf(line.c_str(), "%15s %lu %lu %lu %lu", method, &urilen, &declared,
               &actual, &port) != 5) {
      fprintf(stderr, "web_check: bad request line %s\n", line.c_str());
      return 2;
    }
    std::string uri, body;
    if (!read_exact(uri, urilen) || !read_exact(body, actual)) {
      fprintf(stderr, "web_check: short request\n");
      return 2;
    }
    const int m = method_code(method);
    if (m < 0) {
      fprintf(stderr, "web_check: unknown method %s\n", method);
      return 2;
    }
    write_response(castle_shim::dispatch((uint16_t) port, m, uri, declared, body));
  }
  return 0;
}

/// safe_name / url_decode / json_escape, one name per line in, three
/// answers per line out. The name is fed raw: url_decode's output is what
/// safe_name judges, exactly as name_from_uri arranges on the device.
int rules() {
  std::string line, name;
  while (read_line(line)) {
    if (line == "QUIT") break;
    const size_t n = strtoul(line.c_str(), nullptr, 10);
    if (!read_exact(name, n)) return 2;
    // url_decode takes a `const char *`, so a raw NUL would end its input
    // there — which is faithful: a request target is a C string on the
    // board and cannot carry one. %00 is how a NUL gets into a name, and
    // it lands in the DECODED string, which safe_name then judges whole.
    const std::string dec = castle_web::url_decode(name.c_str());
    // json_escape gets the raw bytes because on the device it is handed a
    // filename read off the FAT, not a decoded URL (h_list, h_status).
    const std::string esc = castle_web::json_escape(name);
    printf("%d %zu %zu\n", castle_web::safe_name(dec) ? 1 : 0, dec.size(), esc.size());
    fwrite(dec.data(), 1, dec.size(), stdout);
    fwrite(esc.data(), 1, esc.size(), stdout);
    fflush(stdout);
  }
  return 0;
}

}  // namespace

int main(int argc, char **argv) {
  if (castle_shim::card_root().empty()) {
    fprintf(stderr, "web_check: set CASTLE_CARD to the directory standing in "
                    "for the SD card\n");
    return 2;
  }
  const bool rules_mode = argc > 1 && strcmp(argv[1], "--rules") == 0;
  seed_from_env();
  if (rules_mode) return rules();
  // The upload worker's job, run in line after each request (A9): the
  // device has a task for this, the harness has this one thread. The
  // firmware side is identical either way — h_put queues and returns.
  castle_shim::drain_async = []() { castle_web::upload_pump(); };
  castle_web::start();
  if (castle_shim::server_on(80) == nullptr ||
      castle_shim::server_on(8080) == nullptr) {
    fprintf(stderr, "web_check: castle_web::start() left a server unstarted\n");
    return 2;
  }
  const int rc = serve();
  // Every async request must have been completed, or the device's server
  // would have run out of sockets and stopped accepting connections.
  if (castle_shim::async_open() != 0) {
    fprintf(stderr, "web_check: %d async request(s) never completed\n",
            castle_shim::async_open());
    return 2;
  }
  return rc;
}
