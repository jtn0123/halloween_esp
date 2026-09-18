// The card's WRITE plane: PUT and DELETE, and the task that does the work.
//
// Split from sd_web.h in v5.61 (the 500-line rule, and A9 gave the seam a
// second reason to exist). sd_web.h's own note draws the line at "control
// in" vs "bytes out"; this is the third thing either of them did — "bytes
// IN", which is the only work on the castle that takes minutes rather than
// milliseconds. sd_web.h includes this and registers h_put/h_delete from
// its start(), the same arrangement sd_web_ota.h and sd_web_site.h have.
//
// A9 (v5.61) — WHY THERE IS A TASK IN HERE.
//
// esp_http_server runs ONE task per server instance and answers requests
// strictly one at a time from it. An upload is a request that holds that
// task for as long as the bytes take: `make publish` pushing a 2 MB scene
// track over WiFi-to-SD is 5-20 seconds, a desk page is a couple more, and
// for the whole of it /api/status, /api/stop and /api/blackout were not
// slow — they were UNANSWERED. castle_link gives a status poll 2 seconds
// and a mutation 4 before it calls the castle down, so the desk went red
// mid-publish every time, and the one command you actually want during a
// botched publish (stop the show) was the one command that could not get
// through. The emulator has rehearsed this since it was written: it is
// what `castle_emu --serial` IS (tests/test_emu_modes.py).
//
// The fix is ESP-IDF's own: httpd_req_async_handler_begin() hands the
// handler a request copy that OWNS the socket, so the httpd task can
// return immediately and go back to answering. The copy is queued to the
// worker below, which reads the body, writes the card and sends the reply
// from its own stack. The control task is free within microseconds of the
// headers arriving; the upload takes exactly as long as it always did.
//
// Priority 4 and core 0, the same reasoning as sd_web_stream.h: above the
// ESPHome loop task (1) so a busy show cannot stall a publish, below the
// audio band (5) so a publish cannot stall the show, and off the core the
// loop and its decode run on.
#pragma once

#include <esp_http_server.h>
#include <esp_rom_crc.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <array>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

#include "esphome/core/log.h"
#include "sd_audio.h"
#include "sd_space.h"
#include "sd_web_state.h"   // g_scenes_dirty, the publish bell (J1)
#include "sd_web_util.h"

// reply_err/reply_json/TAG come from sd_web.h, which includes this header
// after defining them (the same arrangement as sd_web_site.h).
namespace castle_web {

// ── uploads: PUT /api/files/<name>, /api/site/<name>, /api/scenes/<name> ─
/// Into `<path>.part` first; the real name changes hands only once every
/// byte is on the card. A WiFi drop at 80% of a re-send used to take the
/// PREVIOUS good copy down with it (the old code opened the real name for
/// writing and unlinked it on failure). The studio side was fixed for this
/// class in 3ccdd8b; this is the device side.
inline esp_err_t write_body(httpd_req_t *req, const char *path) {
  // B3/E3: refuse what cannot fit, before the first byte. Re-read the
  // free-space cache so a just-finished upload is not 507'd. A
  // Content-Length that lies high is an IDF close; we can refuse a low one.
  unsigned sd_total = 0;
  unsigned sd_free = 0;
  sd_space_kb(sd_total, sd_free, true);
  if (sd_total > 0 && req->content_len / 1024 + 64 > sd_free)
    return reply_err(req, "507 Insufficient Storage", "not enough room on the card");
  const std::string part = std::string(path) + ".part";
  FILE *f = fopen(part.c_str(), "wb");
  if (f == nullptr) return reply_err(req, "500 Internal Server Error", "cannot create file");
  static constexpr size_t CHUNK = 8192;
  // nothrow: exceptions are off, and a full heap must answer 500, not abort.
  const auto buf = std::unique_ptr<std::array<char, CHUNK>>(new (std::nothrow) std::array<char, CHUNK>);
  if (buf == nullptr) {
    fclose(f);
    return reply_err(req, "500 Internal Server Error", "no memory");
  }
  size_t remaining = req->content_len;
  size_t written = 0;
  unsigned chunks = 0;
  uint32_t crc = 0;
  bool ok = true;
  while (remaining > 0) {
    const int got = httpd_req_recv(req, buf->data(), remaining < CHUNK ? remaining : CHUNK);
    if (got <= 0 || fwrite(buf->data(), 1, got, f) != (size_t) got) { ok = false; break; }
    // B5: a cheap running checksum, returned to the sender — "bytes
    // matched" catches truncation but not a bad SD sector, which is a live
    // hypothesis in docs/ISSUE-scene-start-audio.md. sd_sync compares.
    crc = esp_rom_crc32_le(crc, (const uint8_t *) buf->data(), got);
    remaining -= got;
    written += got;
    if ((++chunks & 3u) == 0) vTaskDelay(1);  // feed the watchdog every 32 KB
  }
  fclose(f);
  if (!ok) {
    unlink(part.c_str());  // the sidecar only; whatever `path` held still plays
    ESP_LOGE(TAG, "upload of %s failed at %u bytes", path, (unsigned) written);
    return reply_err(req, "500 Internal Server Error", "short write");
  }
  // A11 (v5.61): FAT's rename refuses to overwrite, so the old copy has to
  // get out of the way — and the way it used to get out of the way was
  // unlink(path). That is a deletion done on the STRENGTH OF A PROMISE: if
  // the rename that was supposed to follow it failed (a full FAT directory,
  // a card that went read-only, the volume lock that produced the EAGAIN
  // remount() exists for), the sidecar was dropped too and the castle was
  // left with NO copy of a file it had had a perfectly good one of a
  // millisecond earlier. The 500 "rename failed" told the desk the upload
  // had not landed; it did not mention that the previous show's track was
  // gone with it, and the next scene played silence.
  //
  // So the old copy is MOVED aside rather than deleted, and it only stops
  // existing once its replacement is in place. Three renames, and at every
  // instant between them at least one complete copy of the file is on the
  // card under a name this code knows:
  const std::string keep = std::string(path) + ".old";
  unlink(keep.c_str());                      // a leftover from a lost power
  const bool had_old = rename(path, keep.c_str()) == 0;
  if (rename(part.c_str(), path) != 0) {
    ESP_LOGE(TAG, "rename %s -> %s failed (errno %d)", part.c_str(), path, errno);
    unlink(part.c_str());
    // Put it back. If THIS fails there is nothing else to try, and the
    // file is still on the card under `.old` for a human to find — which
    // is why the name is a plain suffix and not a temp file in a hidden
    // directory.
    if (had_old && rename(keep.c_str(), path) != 0)
      ESP_LOGE(TAG, "could not restore %s from %s", path, keep.c_str());
    return reply_err(req, "500 Internal Server Error", "rename failed");
  }
  if (had_old) unlink(keep.c_str());   // the new copy is in place: let it go
  ESP_LOGI(TAG, "uploaded %s (%u KB)", path, (unsigned) (written / 1024));
  // J1 (grade report 2026-09-17 pm): the show's manifest just changed, so the
  // scene id list /api/scene and /api/pir validate against is stale. It is
  // re-read on the main loop, not here — this is the upload TASK, and the
  // manifest is card I/O whose result the httpd task reads (sd_web_state.h
  // g_scenes_dirty, drained by castle_sd_common.yaml's 200 ms interval into
  // `seed_scene_ids`). One name, not the whole directory: a .cue or an mp3
  // changes what a scene DOES, and only show.man changes which scenes exist.
  static constexpr char kManifest[] = "/scenes/show.man";
  const size_t plen = strlen(path);
  if (plen >= sizeof(kManifest) - 1 &&
      strcmp(path + plen - (sizeof(kManifest) - 1), kManifest) == 0) {
    ESP_LOGI(TAG, "show.man republished — the scene list will be re-read");
    g_scenes_dirty.store(true);
  }
  sd_space_kb(sd_total, sd_free, true);   // the card just shrank; /api/status reads this
  std::array<char, 220> body{};
  snprintf(body.data(), body.size(), R"({"path":"%s","bytes":%u,"crc32":"%08lx"})",
           path, (unsigned) written, (unsigned long) crc);
  return reply_json(req, body.data());
}

// ── the upload worker (A9) ──────────────────────────────────────────────

/// One queued upload: the request copy that owns the socket, and where the
/// bytes are going. A fixed buffer rather than a std::string because the
/// job travels through a FreeRTOS queue by value — "/sd/scenes/" plus
/// safe_name's 99-byte ceiling is 110, so 160 is room to spare.
struct UploadJob {
  httpd_req_t *req{nullptr};
  char path[160]{};
};

inline QueueHandle_t g_upload_q = nullptr;

/// Take ONE job off the queue and finish it, or return having done nothing.
/// Separate from the task loop so the host harness — which has a single
/// thread and no scheduler — can run the worker's work in line
/// (tests/cxx/web_check.cpp). Same bytes out; different moment.
inline void upload_pump() {
  UploadJob job{};
  if (g_upload_q == nullptr) return;
  if (xQueueReceive(g_upload_q, &job, portMAX_DELAY) != pdTRUE) return;
  write_body(job.req, job.path);
  // MUST happen on every path: an async request never marked complete
  // keeps its socket forever, and after max_open_sockets of them the
  // server stops accepting connections altogether ("error in accept (23)").
  httpd_req_async_handler_complete(job.req);
}

inline void upload_task(void *) {
  for (;;) upload_pump();
}

/// Called once from castle_web::start(). A failure here is not fatal: h_put
/// falls back to writing on the httpd task, which is what every build
/// before v5.61 did.
inline void upload_start() {
  if (g_upload_q != nullptr) return;
  // As deep as the control plane has sockets (sd_web.h, max_open_sockets),
  // so a queue-full is something no client can actually provoke — four
  // sockets cannot offer a fifth upload. It is still handled below rather
  // than asserted away.
  g_upload_q = xQueueCreate(4, sizeof(UploadJob));
  if (g_upload_q == nullptr) {
    ESP_LOGE(TAG, "no queue for the upload worker — uploads will hold the API");
    return;
  }
  // 6144 to match the httpd task this work moved off: FATFS plus our own
  // 8 KB heap chunk buffer. Pinned to core 0, priority 4 (see the header).
  if (xTaskCreatePinnedToCore(upload_task, "castle_upload", 6144, nullptr, 4,
                              nullptr, 0) != pdPASS) {
    vQueueDelete(g_upload_q);
    g_upload_q = nullptr;
    ESP_LOGE(TAG, "no task for the upload worker — uploads will hold the API");
  }
}

/// Hand the request to the worker, or do it here if there is no worker.
/// Returns what the HTTPD TASK should return — ESP_OK once the copy is
/// queued, because the reply is no longer this task's to send.
inline esp_err_t upload_offload(httpd_req_t *req, const std::string &path) {
  if (g_upload_q == nullptr || path.size() >= sizeof(UploadJob::path))
    return write_body(req, path.c_str());
  httpd_req_t *copy = nullptr;
  if (httpd_req_async_handler_begin(req, &copy) != ESP_OK || copy == nullptr)
    return write_body(req, path.c_str());   // no copy, no hand-off
  UploadJob job{};
  job.req = copy;
  snprintf(job.path, sizeof(job.path), "%s", path.c_str());
  // Zero wait: blocking here would be the very stall this exists to remove.
  if (xQueueSend(g_upload_q, &job, 0) != pdTRUE) {
    const esp_err_t r = write_body(copy, path.c_str());
    httpd_req_async_handler_complete(copy);
    return r;
  }
  return ESP_OK;   // the worker owns the socket, the reply and the card now
}

/// Which card directory a /api/files, /api/site or /api/scenes route addresses, and the
/// prefix to cut off the URI: the one switch h_put and h_delete share, so a
/// file that can be put somewhere can be deleted from the same place (until
/// v5.47 DELETE knew only the root, and a renamed scene stranded its old
/// 2 MB track on the card — grade report 2026-09-06 J4).
inline void route_dir(const httpd_req_t *req, const char *&dir, const char *&prefix) {
  dir = ""; prefix = "/api/files/";
  if (strncmp(req->uri, "/api/site/", 10) == 0) { dir = "site/"; prefix = "/api/site/"; }
  if (strncmp(req->uri, "/api/scenes/", 12) == 0) { dir = "scenes/"; prefix = "/api/scenes/"; }
}

/// PUT into /sd, /sd/site or /sd/scenes depending on the route. The scenes
/// directory is where the show's own tracks live (see audio_sd.yaml).
inline esp_err_t h_put(httpd_req_t *req) {
  if (!castle_sd::g_mounted) return reply_err(req, "503 Service Unavailable", "no SD card");
  if (req->content_len == 0)
    return reply_err(req, "400 Bad Request", "empty body");
  const char *dir = nullptr; const char *prefix = nullptr;
  route_dir(req, dir, prefix);
  // E3: a desk page has a known plausible size (3.3 MB today); a mistake
  // must not eat the card. The free-space check in write_body bounds the
  // rest.
  if (strcmp(dir, "site/") == 0 && req->content_len > 8u * 1024 * 1024)
    return reply_err(req, "413 Payload Too Large", "site file too large");
  std::string name = name_from_uri(req, prefix);
  if (!safe_name(name)) return reply_err(req, "400 Bad Request", "bad filename");
  // J5 (grade report 2026-09-17): the two names this route CANNOT be trusted
  // with, because it makes them itself. Uploading `X` writes `X.part` and, on
  // success, unlinks `X.old` — so a PUT of `song.mp3.part` destroys the
  // in-flight copy of `song.mp3`, and a PUT of `song.mp3.old` is a file the
  // next upload of `song.mp3` deletes without being asked to. Neither is a
  // name anything in this repo publishes, which is exactly why the refusal is
  // cheap; the alternative is a data-loss bug nobody would think to look for.
  // Mirrored in tools/castle_emu_upload.py.
  if (name.size() >= 5 && name.compare(name.size() - 5, 5, ".part") == 0)
    return reply_err(req, "400 Bad Request", "reserved suffix");
  if (name.size() >= 4 && name.compare(name.size() - 4, 4, ".old") == 0)
    return reply_err(req, "400 Bad Request", "reserved suffix");
  if (dir[0] != '\0') {
    std::string d = std::string("/sd/") + dir;
    d.pop_back();   // mkdir without the trailing slash
    mkdir(d.c_str(), 0775);
  }
  const std::string path = std::string("/sd/") + dir + name;
  // Everything above is cheap and must stay on the httpd task: a 400 for a
  // bad name has to come back as fast as it always did. The BYTES are what
  // moves off it (A9).
  return upload_offload(req, path);
}

inline esp_err_t h_delete(httpd_req_t *req) {
  if (!castle_sd::g_mounted) return reply_err(req, "503 Service Unavailable", "no SD card");
  const char *dir = nullptr; const char *prefix = nullptr;
  route_dir(req, dir, prefix);
  std::string name = name_from_uri(req, prefix);
  if (!safe_name(name)) return reply_err(req, "400 Bad Request", "bad filename");
  const std::string path = std::string("/sd/") + dir + name;
  if (unlink(path.c_str()) != 0) return reply_err(req, "404 Not Found", "no such file");
  ESP_LOGI(TAG, "deleted %s", path.c_str());
  unsigned t = 0, f = 0;
  sd_space_kb(t, f, true);   // the only other way free space moves
  return reply_json(req, R"({"deleted":true})");
}


}  // namespace castle_web
