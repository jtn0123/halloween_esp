// The splash page: shows what the supervisors are doing, offers the log and
// a retry when a start fails. The Rust side navigates the window to Castle
// Radio once it answers; this page only reads the status of both servers
// (Castle Radio, and the cue desk studio beside it) and of the setup that
// runs before them on a release's first launch (src-tauri/src/setup.rs).
"use strict";

const invoke = (cmd) => window.__TAURI__.core.invoke(cmd);
const $ = (id) => document.getElementById(id);

// setup.rs Progress.reason (bundle.rs Reason, snake_case).
const SETUP_WHY = {
  first_run: "Setting up Castle Tools for the first time.",
  unfinished: "Finishing Castle Tools' setup, which was interrupted.",
  update: "Castle Tools was updated — bringing its tools up to date.",
  repair: "Repairing Castle Tools: setting its tools up again.",
};

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

// A setup that could not finish, in words an owner can act on.
function setupFailedText(setup) {
  const why = setup.message.replace(/\.$/, "");
  return `Setup could not finish: ${why}. Check the internet connection, then ` +
    "choose Try again — it picks up where it stopped. If it keeps failing, " +
    "Repair sets the tools up again from scratch, and Open log has every detail.";
}

// True while a setup runs: the setup panel replaces the spinner and message.
function showSetup(setup) {
  const running = setup.state === "running";
  $("setup").hidden = !running;
  if (!running) return false;
  $("setup-why").textContent = SETUP_WHY[setup.reason] || SETUP_WHY.first_run;
  // An update re-uses what is already downloaded; the others may not.
  $("setup-net").hidden = setup.reason === "update";
  const bar = $("setup-bar");
  if (setup.steps > 0) {
    bar.max = setup.steps;
    bar.value = setup.step;
    $("setup-step").textContent = `Step ${setup.step} of ${setup.steps}: ${setup.what}`;
  } else {
    bar.removeAttribute("value"); // indeterminate until the installer counts
    $("setup-step").textContent = setup.what;
  }
  return true;
}

function show({ radio, desk, setup }) {
  const settingUp = showSetup(setup);
  const failed = radio.state === "failed";
  const setupFailed = setup.state === "failed";
  $("message").hidden = settingUp;
  $("message").textContent = failed && setupFailed ? setupFailedText(setup) : radioText(radio);
  $("message").classList.toggle("error", failed);
  $("spinner").hidden = settingUp || failed || radio.state === "idle";
  $("retry").hidden = !(failed || radio.state === "idle");
  $("repair").hidden = !(failed && (radio.repair || setupFailed));
  $("desk").textContent = settingUp ? "" : deskText(desk);
}

async function poll() {
  try {
    show(await invoke("castle_status"));
  } catch (error) {
    show({
      radio: { state: "failed", message: String(error) },
      desk: { state: "idle" },
      setup: { state: "idle" },
    });
  }
}

$("retry").addEventListener("click", async () => {
  await invoke("castle_start");
  poll();
});
$("repair").addEventListener("click", async () => {
  $("repair").hidden = true;
  await invoke("castle_repair");
  poll();
});
$("log").addEventListener("click", () => invoke("castle_open_log"));
invoke("castle_release")
  .then((release) => { $("version").textContent = `Castle Tools ${release.tag}`; })
  .catch(() => {});
poll();
setInterval(poll, 500);
