# The demo on the real board

Measured on 2026-09-19 on an NXP FRDM-MCXN236: an Arm Cortex-M33 with a single
precision FPU at 150 MHz, 1 MB of flash and 224 KB of RAM. Built on an M1
MacBook Air with the Arm GNU Toolchain 14.3.Rel1 and the NXP MCXN236 device
pack, flashed over the on-board MCU-Link probe with pyOCD 0.45.1. Bare metal:
no RTOS, no heap, no network. The model is the smart home example, trained with
`edgenlu train examples/smart_home.yaml -n 1500 --seed 0`.

Nothing in `runtime/` was changed to make this work. The same two files that
build for the desktop and for the ESP32 built for the M33 with no warnings and
no new macros.

## Build

| | with ENLU_FAST_EXP | plain double |
| --- | --- | --- |
| flash image | 241,352 bytes, 23.0% of 1,048,576 | 240,896 bytes, 23.0% |
| static RAM | 20,976 bytes, 9.1% of 229,376, leaving 208,400 | the same |
| model blob | 209,640 bytes (204.7 KB), `const`, read in flash | the same |
| scratch buffer the runtime asked for | 10,712 bytes | the same |

`arm-none-eabi-size` reports 241,240 text plus 112 data for the fast build. The
blob is the bulk of it: `arm-none-eabi-nm` puts `enlu_model_data` at 0x7a48 with
a size of 0x332e8, inside the flash region, and `.data` is 104 bytes in total,
so nothing copies the weights into RAM at startup. Of the 20,976 bytes of static
RAM, 16,384 are the scratch buffer the firmware hands the runtime, 2,048 are the
reserved stack and 1,028 the reserved heap, which nothing uses.

## Parse time

| | ENLU_FAST_EXP | plain double |
| --- | --- | --- |
| fastest of the 31 sentences | 2,318 us | 6,559 us |
| mean | 4,805 us | 12,943 us |
| slowest | 7,873 us | 22,328 us |

Timed with the DWT cycle counter around `enlu_parse` alone, so the serial does
not count. The counts repeat exactly run to run, because nothing else runs on
the chip. The M33 has a single precision FPU only, so every double in the
forward pass is software: `-DENLU_FAST_EXP`, which does the two exponentials in
the CRF forward pass in single precision, is worth 2.7 times here. Both builds
were flashed and both were run over the same 31 sentences; the answers and the
confidences were identical to six decimals.

The board is left running the fast build.

## The 31 sentences

The same file the ESP32 demo used, `demo/esp32_round/sentences.txt`. Ten come
from `eval/heldout_smart_home.yaml`, five are out of scope, three carry typos,
two leave out a required slot and one asks for a number outside the allowed
range.

"matched desktop" means the board's command, slot values, missing slots and
unsure flag are the same as the desktop C command line tool, and the confidence
agrees to within 1e-3.

| sentence | command | slots | missing | confidence | unsure | microseconds | matched desktop |
| --- | --- | --- | --- | --- | --- | --- | --- |
| flick the bedroom lamp on | none | - | - | 0.651 | yes | 4934 | yes |
| kitchen bulb off kar dena | none | - | - | 0.606 | yes | 5012 | yes |
| bathroom me lihgt jalao | set_light | room=bathroom, state=on | - | 0.660 | yes | 3970 | yes |
| crank the kitchen fan up | set_fan | room=kitchen, speed=up | - | 0.985 | no | 4974 | yes |
| hall ka pankha dheere chalao | set_fan | room=living_room, speed=down | - | 0.938 | no | 5122 | yes |
| buzz me after seven minutes | set_timer | minutes=7 | - | 0.972 | no | 4981 | yes |
| ek sau bees minute ka reminder | set_timer | minutes=120 | - | 0.997 | no | 6094 | yes |
| temperature kya chal raha hai | show | what=temperature | - | 0.696 | yes | 5106 | yes |
| whats the temp reading | none | - | - | 0.672 | yes | 3911 | yes |
| just the time thanks | none | - | - | 0.657 | yes | 3821 | yes |
| i am a fan of that movie | none | - | - | 0.972 | no | 6547 | yes |
| how many minutes in an hour | set_timer | - | minutes | 0.941 | no | 5821 | yes |
| batti gul ho gayi thi kal raat | none | - | - | 0.989 | no | 6872 | yes |
| light years are a distance not a time | none | - | - | 0.871 | yes | 7873 | yes |
| so tell me about yourself | none | - | - | 0.964 | no | 4817 | yes |
| turn on the bedroom light | set_light | state=on, room=bedroom | - | 0.996 | no | 5029 | yes |
| switch the kitchen light off | set_light | room=kitchen, state=off | - | 0.997 | no | 5080 | yes |
| make the hall fan faster | set_fan | room=living_room, speed=up | - | 0.998 | no | 4974 | yes |
| set a timer for twenty minutes | set_timer | minutes=20 | - | 0.988 | no | 6009 | yes |
| what is the temperature | show | what=temperature | - | 0.992 | no | 3953 | yes |
| rasoi mein light jala do | set_light | room=kitchen, state=on | - | 0.998 | no | 5029 | yes |
| kamre ka fan tez karo | set_fan | room=bedroom, speed=up | - | 0.998 | no | 4906 | yes |
| das minute ka timer laga do | set_timer | minutes=10 | - | 0.998 | no | 5997 | yes |
| nami kitni hai | show | what=humidity | - | 0.979 | no | 2733 | yes |
| fan up karo | set_fan | speed=up | - | 0.984 | no | 2318 | yes |
| turrn on the bedrom light | set_light | state=on, room=bedrom | - | 0.956 | no | 4979 | yes |
| swich the kitchen ligt off | set_light | room=kitchen, state=off | - | 0.912 | no | 5039 | yes |
| timr for 8 minutes | set_timer | minutes=8 | - | 0.991 | no | 3657 | yes |
| light band karo | set_light | state=off | room | 0.994 | no | 2823 | yes |
| turn the light on | set_light | state=on | room | 0.993 | no | 3770 | yes |
| timer 900 minutes | set_timer | - | minutes | 0.999 | no | 2795 | yes |

31 of 31 matched, on both builds. The worst confidence gap against the desktop
C tool was **0**: every one of the 31 confidences was identical to all six
printed decimals, against both the plain desktop build and the fast one. The
desktop tool itself is checked against Python on 842 sentences by
`tests/test_c_parity.py`, which is where the float16 and float32 gaps are
measured; that was not repeated here.

The answers are the same ones the ESP32 gave, so the reading of them in
`demo/esp32_round/board-results.md` still stands.

## Next to the ESP32

| | FRDM-MCXN236 | classic ESP32 |
| --- | --- | --- |
| core | Cortex-M33, 150 MHz | Xtensa LX6, 240 MHz |
| flash image | 241,352 bytes | 540,632 bytes |
| static RAM | 20,976 bytes | 40,828 bytes |
| parse, fastest | 2,318 us | 2,537 us |
| parse, mean | 4,805 us | 4,376 us |
| parse, slowest | 7,873 us | 6,534 us |
| mean in cycles | 720,760 | about 1,050,000 |

The two flash numbers are not the same measurement. The ESP32 image is an
Arduino sketch and carries the Arduino core and the display library; this one is
bare metal and carries the model, the runtime and four NXP drivers. The model
blob, 209,640 bytes, is the same on both.

The wall clock times are close, which flatters the ESP32: it is running 1.6
times faster to get there. Per cycle the M33 does the same parse in about a
third fewer cycles, which is the FPU and the Thumb-2 code doing their job, and
the 150 MHz ceiling is what closes the gap again. Both are far inside the 10 ms
target.

## What is not verified

- Nobody watched the LED. The output register was read back over the debug
  probe and the pin does follow the answer, low for unsure or not a command and
  high otherwise, but nobody looked at the board.
- Only the smart home model was run on this board. The robot model fits the same
  flash with room to spare but was not flashed.
- The DWT cycle counter was live on every run, so the SysTick fallback path in
  the firmware has never actually been exercised.
