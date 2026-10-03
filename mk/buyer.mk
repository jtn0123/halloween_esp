# What a sold castle leaves the house with: its image and its SD card. The
# Makefile includes this; YAML_BUYER and DEVICE_BUYER are defined there.
#
#   make build-buyer / validate-buyer        the buyer image (castle_buyer.yaml)
#   make buyer-card                          its card, into ./buyer-card (gitignored)
#   make buyer-card OUT=/Volumes/CASTLE TAG=v0.2.0
#
# What buyer-card writes: scenes/shipped.yaml rendered in a sandbox (scenes/), the
# firmware's notices and the GPLv3 written source offer (licenses/). No
# castle, no songs, no site. TAG names the release the castle's firmware is
# from, for the offer. It refuses a directory that holds anything else.

.PHONY: build-buyer validate-buyer buyer-card

# The buyer image. No upload target on purpose: a buyer's castle is
# flashed from a release through the web flasher, never from this checkout.
validate-buyer: generate
	@$(ESPHOME_RUN) config $(YAML_BUYER) > /dev/null && echo "config OK (buyer)"

build-buyer: audio generate
	$(ESPHOME_RUN) compile $(YAML_BUYER)
	@$(PY) tools/check_image.py $(DEVICE_BUYER) --require

# The card (tools/buyer_card.py), made with no castle.
buyer-card:
	@$(PY) tools/buyer_card.py --out "$(or $(OUT),buyer-card)" $(if $(TAG),--tag "$(TAG)")
