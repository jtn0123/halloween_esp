# The browser suite (web/test/e2e). The Makefile includes this; it lives
# apart because it is the one target that drives real browsers: separate from
# `check` because it needs a built page and a browser binary, and it takes an
# order of magnitude longer than everything else put together. It drives the
# real studio server against a scratch tracks directory, and a run is silent:
# Chromium with --mute-audio, WebKit through the fixture's own mute
# (web/test/e2e/webkit.ts).
#
# `playwright install` is idempotent and near-instant once the browsers are
# cached — running it here turns the two tribal setup steps ("build the
# page, install the browser") into the target itself. Two browsers, two
# runs: WebKit is Safari's engine and the desktop app's macOS window
# (docs/PRODUCTION-TODO.md §5.1), and one run per browser keeps each on its
# own fresh server and scratch tracks, as the specs expect.
#
#   make e2e
#   CASTLE_E2E_PORT=8821 make e2e      beside another suite

.PHONY: e2e

e2e: preview
	@cd web && node -e "require('@playwright/test')" 2>/dev/null \
		|| { echo "e2e needs its deps first: cd web && npm ci"; exit 1; }
	@if command -v cargo >/dev/null 2>&1; then \
		(cd core && cargo build --release --quiet --bin studio) \
			|| { echo "e2e: the Rust studio failed to build — fix it rather than testing a stale binary"; exit 1; }; \
	fi
	@cd web && npx playwright install chromium webkit
	@cd web && npx playwright test --project=chromium
	@cd web && npx playwright test --project=webkit
