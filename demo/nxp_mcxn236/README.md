# edge-nlu on an NXP FRDM-MCXN236

The same model and the same runtime as the ESP32 demo, on a different chip: an
Arm Cortex-M33 at 150 MHz with 1 MB of flash and 224 KB of RAM. Bare metal, no
RTOS, no heap, no network. This is the portability proof: the runtime was not
changed for it.

Type a sentence into the debug serial port at 115200 and the board answers with
one JSON line. The red LED lights while the sentence is being read and stays lit
when the answer is unsure or is not a command.

```
{"text":"turn on the bedroom light","command":"set_light",
 "slots":{"state":"on","room":"bedroom"},"missing":[],"confidence":0.997211,
 "intent":0.999969,"slot":0.989763,"margin":0.999952,"unknown":0,
 "unknown_share":0.000000,"all_carrier_unknown":false,"unsure":false,
 "micros":5366,"cycles":804963}
```

`margin`, `unknown`, `unknown_share` and `all_carrier_unknown` are the evidence
the confidence gate used, the same four fields the desktop tool prints.

The measured numbers are in [board-results.md](board-results.md).

## What you need

- An FRDM-MCXN236 board. The on-board MCU-Link debug probe is the only cable.
- The Arm GNU toolchain for `arm-none-eabi`, the NXP MCXN236 device pack and the
  CMSIS core headers.
- [pyOCD](https://pyocd.io) to flash it.

The build expects those under `$(HOME)/nxp-boards`:

```
nxp-boards/toolchain/bin/arm-none-eabi-gcc
nxp-boards/sdk/n236/devices/MCXN236/     the device pack: headers, drivers, startup, linker script
nxp-boards/sdk/cmsis/CMSIS/Core/Include/ the CMSIS core headers
```

Point `NXP` somewhere else if yours live elsewhere, or set `TC`, `SDK` and
`CMSIS` one by one. Nothing from the SDK is copied into this repository.

## Build and flash

```sh
make model                          # at the repository root: train and export
make -C demo/nxp_mcxn236            # copies the runtime and the model in, builds
make -C demo/nxp_mcxn236 flash      # writes it over the MCU-Link probe
.venv/bin/python demo/send.py -p /dev/cu.usbmodem<probe>3 "turn on the bedroom light"
```

`make -C demo/nxp_mcxn236 flash PROBE=<id> TARGET=mcxn236vdf` picks a different
probe. `pyocd list` prints the ids.

`edgenlu.c`, `edgenlu.h`, `model_data.c` and `model_data.h` in this folder are
copies, refreshed from `runtime/` and `out/device/` whenever they change. They
are build output, not source, so they are not in git.

`FAST_EXP=0` builds the plain double version. The default, `FAST_EXP=1`, passes
`-DENLU_FAST_EXP` and does the two exponentials in the CRF forward pass in
single precision. On this chip that is 2.6 times faster, for the same answers to
six decimals. The M33 has a single precision FPU, so every double is software.

## What the board does

- `BOARD_BootClockPLL150M` in `board_clock.c` takes the core to 150 MHz off
  PLL0, with the core voltage, the flash wait states and the SRAM timing moved
  to match. No external crystal is involved. That file is cut down from the
  MCUXpresso Config Tools output for this board and keeps NXP's BSD-3 header;
  it is the only NXP code in the repository.
- The FPU is enabled by the SDK's `SystemInit` in the startup code, and the
  build passes `-mfloat-abi=hard -mfpu=fpv5-sp-d16`.
- LPUART4 on P1_8 and P1_9 at 115200 is the MCU-Link virtual COM port, the same
  port the debugger shows up on.
- `enlu_parse` is timed with the DWT cycle counter, which is exact at the
  instruction level. If the trace unit is not powered the firmware falls back to
  a free running SysTick; the boot banner says which one is in use.
- The model is a `const` array, so it is read straight out of flash. Only the
  10,744 byte scratch buffer and a few line buffers are in RAM.

## Not verified

Nobody watched the LED. The output register was read back over the debug probe
and the pin does follow the answer, but whether the light is visible from across
a room is unchecked.
