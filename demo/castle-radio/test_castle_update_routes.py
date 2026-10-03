"""Castle Radio's "Update castle" routes (castle_update_routes.py), against
tools/castle_emu as the castle and tools/release_emu as GitHub: the card's
plan, the one job the button starts and its verdict, the cache that keeps
the card inside GitHub's hourly limit, and /radio/app/release, which the
desktop app's updater asks when its owner opted in to pre-releases."""

import os
import time
import unittest
from unittest.mock import patch

import radio_env  # noqa: F401 — the sandbox first, then tools/ on the path

# isort: split
import castle_update_routes as routes
import desktop_release as rel
import device_bridge
import release_emu
from castle_emu import CastleEmu
from test_server_fixes import JSON, _Caller, post

ROUTE = "/radio/castle/update"
BUTTON = b'{"action": "update"}'
IMAGE = b"\xe9" + bytes(range(256)) * 300


def get(route):
    caller = _Caller(path=route)
    caller.do_GET()
    return caller.answer()


class UpdateRoutes(unittest.TestCase):
    def setUp(self):
        for patcher in (
            patch.dict(os.environ, {"CASTLE_PRERELEASE": ""}),
            patch.dict(routes._OFFERS, clear=True),
            patch.dict(routes._APPS, clear=True),
            patch.dict(routes._JOB, {"running": False, "done": False}, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.github = release_emu.ReleaseEmu().start()
        self.addCleanup(self.github.stop)
        fetch = patch.object(rel, "http_fetch", self.github.fetch)
        fetch.start()
        self.addCleanup(fetch.stop)
        self.emu = CastleEmu(
            port=0, scenes=["vigil"], version="5.75", fw_variant="buyer"
        )
        self.emu.start()
        self.addCleanup(self.emu.server_close)
        self.addCleanup(self.emu.shutdown)
        self.emu.state.boot -= 3600
        host = patch.object(device_bridge, "HOST", f"127.0.0.1:{self.emu.port}")
        host.start()
        self.addCleanup(host.stop)

    def release(self, tag="v1.1.0", version="5.76"):
        self.github.publish(tag, release_emu.firmware_release(tag, version, IMAGE))

    def asked_github(self):
        return sum(h.endswith("/releases/latest") for h in self.github.hits)

    def wait_done(self):
        for _ in range(200):
            status, body = get(ROUTE)
            if body["job"].get("done"):
                return status, body
            time.sleep(0.05)
        self.fail("the update job never finished")

    def test_the_card_plan_names_both_versions_and_asks_github_once(self):
        self.release()
        status, body = get(ROUTE)
        self.assertEqual(status, 200)
        self.assertEqual(
            {k: body[k] for k in ("current", "available", "tag", "update", "board")},
            {
                "current": "5.75",
                "available": "5.76",
                "tag": "v1.1.0",
                "update": True,
                "board": "feather-s3-4m2p",
            },
        )
        get(ROUTE)
        self.assertEqual(self.asked_github(), 1, "the offer is remembered")

    def test_the_button_updates_the_castle_and_the_card_reads_the_verdict(self):
        self.release()
        self.emu.boots_as = "5.76"
        get(ROUTE)
        self.assertEqual(post(ROUTE, JSON, b"{}")[0], 400, "only the button's ask")
        self.assertEqual(post(ROUTE, None, BUTTON)[0], 415, "JSON, as no form posts")
        for name, value in (("TRIES", 80), ("EVERY", 0.05)):
            quick = patch.object(routes.castle_update, name, value)
            quick.start()
            self.addCleanup(quick.stop)
        status, answer = post(ROUTE, JSON, BUTTON)
        self.assertEqual((status, answer["job"]["running"]), (202, True))
        _, body = self.wait_done()
        self.assertEqual(body["job"]["error"], None)
        self.assertEqual(
            body["job"]["result"], "The castle now runs firmware 5.76 (it ran 5.75)."
        )
        self.assertEqual((body["current"], body["update"]), ("5.76", False))
        self.assertEqual(self.emu.version, "5.76")
        self.assertEqual(self.asked_github(), 1, "the button reused the card's offer")

    def test_a_refused_update_is_the_jobs_error(self):
        self.github.publish(
            "v1.1.0",
            release_emu.firmware_release("v1.1.0", "5.76", IMAGE, fw_variant="yard"),
        )
        status, body = get(ROUTE)
        self.assertEqual(status, 502)
        self.assertIn("never swaps", body["error"])
        self.assertEqual(post(ROUTE, JSON, BUTTON)[0], 202)
        _, body = self.wait_done()
        self.assertIn("never swaps", body["job"]["error"])
        self.assertEqual(self.emu.version, "5.75")

    def test_one_update_at_a_time_and_a_running_one_is_not_disturbed(self):
        routes._JOB.update(running=True, phase="Sending firmware")
        self.assertEqual(get(ROUTE), (200, {"job": dict(routes._JOB)}))
        status, answer = post(ROUTE, JSON, BUTTON)
        self.assertEqual(
            (status, answer["error"]), (409, "The castle is already being updated.")
        )
        self.assertEqual(self.github.hits, [], "nothing asked while it runs")

    def test_no_castle_or_no_github_is_said(self):
        with patch.object(device_bridge, "HOST", ""):
            status, body = get(ROUTE)
            self.assertEqual((status, body["error"]), (502, device_bridge.NO_CASTLE))
            self.assertEqual(post(ROUTE, JSON, BUTTON)[0], 400)
        self.github.stop()
        status, body = get(ROUTE)
        self.assertEqual(status, 502)
        self.assertIn("cannot reach GitHub", body["error"])

    def test_the_app_release_follows_the_owners_channel(self):
        self.github.publish("v1.1.0", {})
        self.github.publish("v1.2.0-rc.1", {})
        self.assertEqual(
            get("/radio/app/release"),
            (
                200,
                {
                    "tag": "v1.1.0",
                    "latest_json": f"https://github.com/{rel.REPO}/releases/download/v1.1.0/latest.json",
                    "prerelease": False,
                },
            ),
        )
        with patch.dict(os.environ, {"CASTLE_PRERELEASE": "1"}):
            status, body = get("/radio/app/release")
        self.assertEqual(
            (status, body["tag"], body["prerelease"]), (200, "v1.2.0-rc.1", True)
        )
        self.assertTrue(body["latest_json"].endswith("/v1.2.0-rc.1/latest.json"))
        routes._APPS.clear()
        self.github.stop()
        status, body = get("/radio/app/release")
        self.assertEqual(status, 502)
        self.assertIn("cannot reach GitHub", body["error"])


if __name__ == "__main__":
    unittest.main()
