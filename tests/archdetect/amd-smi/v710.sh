#!/bin/bash
# output from AMD Radeon Pro V710 (NAVI32, gfx1101) system,
# produced by: amd-smi static --asic
cat <<'ASIC'
GPU: 0
    ASIC:
        MARKET_NAME: NAVI32
        VENDOR_ID: 0x1002
        VENDOR_NAME: Advanced Micro Devices Inc. [AMD/ATI]
        SUBVENDOR_ID: 0x1002
        DEVICE_ID: 0x7461
        SUBSYSTEM_ID: 0x0e34
        REV_ID: 0x00
        ASIC_SERIAL: 0xF7BD1622840AAC82
        OAM_ID: N/A
        NUM_COMPUTE_UNITS: 54
        TARGET_GRAPHICS_VERSION: gfx1101
ASIC
exit 0
