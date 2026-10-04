# The light-show lab (demo/castle-radio/show_lab.py, docs/LIGHT-SHOW-*.md):
# a before/after page for choreography candidates, run by hand. The Makefile
# includes this; it lives apart because it is the only pair of targets that
# serves a page for a person to look at and builds nothing the castle runs.

.PHONY: show-lab show-lab-phone

# Opt-in and offline: candidates are written only under the ignored
# .radio-data/comparison/, never beside a prepared show, and nothing here
# talks to the castle. Adopting a candidate is a separate, deliberate change.
SHOW_LAB_PORT ?= 8894
show-lab:
	@$(PY) demo/castle-radio/show_lab.py
	@echo "open http://127.0.0.1:$(SHOW_LAB_PORT)/show-lab.html   (Ctrl-C stops the server)"
	@$(PY) demo/castle-radio/lab_server.py --port $(SHOW_LAB_PORT) --bind 127.0.0.1

# The same lab for a phone on the home network. A separate target because it
# lets every device on the LAN read the comparison directory — the lab's
# shows and its links to the songs — and add to its notes.jsonl (the page's
# flags; `show_lab.py --notes` prints them) for as long as it runs.
show-lab-phone:
	@$(PY) demo/castle-radio/show_lab.py
	@ip=$$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname); \
		echo "on your phone: http://$$ip:$(SHOW_LAB_PORT)/show-lab.html   (Ctrl-C stops the server)"
	@$(PY) demo/castle-radio/lab_server.py --port $(SHOW_LAB_PORT) --bind 0.0.0.0
