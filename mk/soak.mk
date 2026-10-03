# Unattended runs against real hardware, each ending in a verdict
# (docs/SOAK.md). The Makefile includes this; they live apart because they are
# the only targets that watch a live castle for hours and build nothing, and
# they share one convention: HOST names the castle (else CASTLE_HOST, then
# devices.toml) and ARGS passes any other flag straight through.
#
#   make soak HOST=192.168.1.20 HOURS=72 ARGS='--drive show'
#   make power-cycle HOST=192.168.1.20 CYCLES=50 \
#       OFF='kasa --host 192.168.1.30 off' ON='kasa --host 192.168.1.30 on'
#
# OFF and ON are commands, each run as a program and its arguments with no
# shell (tools/operator_cmd.py) — the same rule as the soak's --disrupt-cmd.

.PHONY: soak power-cycle

soak:
	@$(PY) tools/soak.py $(HOST) --hours $(or $(HOURS),72) $(ARGS)

power-cycle:
	@$(PY) tools/power_cycle.py $(HOST) --off-cmd "$(OFF)" --on-cmd "$(ON)" --cycles $(or $(CYCLES),50) $(ARGS)
