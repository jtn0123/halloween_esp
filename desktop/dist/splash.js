// The splash page: shows what the supervisors are doing, offers the log and
// a retry when a start fails. The Rust side navigates the window to Castle
// Radio once it answers; this page only reads the status of both servers
// (Castle Radio, and the cue desk studio beside it).
"use strict";

const invoke = (cmd) => window.__TAURI__.core.invoke(cmd);
const $ = (id) => document.getElementById(id);

function radioText(status) {
  if (status.state === "starting") return status.detail;
  if (status.state === "running") return "Castle Radio is ready — opening…";
  if (status.state === "idle") return "Castle Radio is stopped.";
  if (status.state === "failed") return status.message;
  return "Starting Castle Radio…";
}

function deskText(status) {
  if (status.state === "running") return "Light desk ready.";
  if (status.state === "failed") return `Light desk unavailable: ${status.message}`;
  if (status.state === "starting") return "Light desk starting…";
  return "";
}

function show({ radio, desk }) {
  const failed = radio.state === "failed";
  $("message").textContent = radioText(radio);
  $("message").classList.toggle("error", failed);
  $("spinner").hidden = failed || radio.state === "idle";
  $("retry").hidden = !(failed || radio.state === "idle");
  $("desk").textContent = deskText(desk);
}

async function poll() {
  try {
    show(await invoke("castle_status"));
  } catch (error) {
    show({ radio: { state: "failed", message: String(error) }, desk: { state: "idle" } });
  }
}

$("retry").addEventListener("click", async () => {
  await invoke("castle_start");
  poll();
});
$("log").addEventListener("click", () => invoke("castle_open_log"));
invoke("castle_release")
  .then((release) => { $("version").textContent = `Castle Tools ${release.tag}`; })
  .catch(() => {});
poll();
setInterval(poll, 500);
