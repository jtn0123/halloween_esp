# Pending firmware patches

Found by hardware-free validation while the S2 build sat at ~20 bytes of
static RAM headroom (see castle.yaml sdkconfig notes); apply with the next
firmware session, recompile, and update tools/castle_emu_wire.py in step
(tests/test_firmware_contract.py parses sd_web.h and will flag drift).

- json_names.patch — safe_name admits `"`, `\` and control bytes; such a
  name then breaks the JSON of /api/files and /api/status for every client.
- hashf noise parity (no patch file yet): firmware float32 hashf vs the
  desk double diverge per frame; replace with the same integer mix on both
  sides (see web/test/firmware_parity.mjs notes).
