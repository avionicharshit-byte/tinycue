# The demo on the real board

Measured on 2026-09-19 on an NXP FRDM-MCXN236: an Arm Cortex-M33 with a single
precision FPU at 150 MHz, 1 MB of flash and 224 KB of RAM. Built on an M1
MacBook Air with the Arm GNU Toolchain 14.3.Rel1 and the NXP MCXN236 device
pack, flashed over the on-board MCU-Link probe with pyOCD 0.45.1. Bare metal:
no RTOS, no heap, no network. The model is the smart home example, trained the
way `make model` trains it, with the extra and dev sentence packs, and exported
as a format 2 blob that carries the training vocabulary and the fitted gate.

Nothing in `runtime/` was changed to make this work. The same two files that
build for the desktop and for the ESP32 built for the M33 with no warnings and
no new macros.

## Build

| | with TCUE_FAST_EXP | plain double |
| --- | --- | --- |
| flash image | 289,064 bytes, 27.6% of 1,048,576 | 287,512 bytes, 27.4% |
| static RAM | 21,032 bytes, 9.2% of 229,376, leaving 208,344 | the same |
| model blob | 255,012 bytes (249.0 KB), `const`, read in flash | the same |
| scratch buffer the runtime asked for | 10,744 bytes | the same |

`arm-none-eabi-size` reports 288,952 text plus 112 data for the fast build. The
blob is the bulk of it: `arm-none-eabi-nm` puts `tcue_model_data` at 0x8368 with
a size of 0x3e424, inside the flash region, and `.data` is 104 bytes in total,
so nothing copies the weights into RAM at startup. Of the 21,032 bytes of static
RAM, 16,384 are the scratch buffer the firmware hands the runtime, 2,048 are the
reserved stack and 1,028 the reserved heap, which nothing uses.

Against the format 1 blob the image grew by 47,712 bytes and static RAM by 56.
The blob itself accounts for 45,372 of the flash; the rest is the runtime's gate
code and the four gate fields the JSON line now carries.

## Parse time

| | TCUE_FAST_EXP | plain double |
| --- | --- | --- |
| fastest of the 31 sentences | 2,524 us | 6,766 us |
| mean | 5,129 us | 13,248 us |
| slowest | 8,455 us | 22,903 us |

Timed with the DWT cycle counter around `tcue_parse` alone, so the serial does
not count. The mean is 769,442 cycles for the fast build and 1,987,272 for the
plain one. The counts repeat exactly run to run, because nothing else runs on
the chip. The M33 has a single precision FPU only, so every double in the
forward pass is software: `-DTCUE_FAST_EXP`, which does the two exponentials in
the CRF forward pass in single precision, is worth 2.6 times here. Both builds
were flashed and both were run over the same 31 sentences; the answers and the
confidences were identical to six decimals.

The fast mean is 324 microseconds slower than it was on the format 1 blob, which
is the wider vocabulary and the unknown word lookup.

The board is left running the voice firmware, not this one. See
[demo/voice](../voice).

## The 31 sentences

The same file the ESP32 demo used, `demo/esp32_round/sentences.txt`. Ten come
from `eval/heldout_smart_home.yaml`, five are out of scope, three carry typos,
two leave out a required slot and one asks for a number outside the allowed
range.

"matched desktop" means the board's command, slot values, missing slots and
unsure flag are the same as the desktop C command line tool, and the confidence
agrees to within 1e-3. The "new words" column is the board's own count of words
in the sentence that no training sentence used.

| sentence | command | slots | missing | confidence | new words | unsure | microseconds | matched desktop |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flick the bedroom lamp on | set_light | room=bedroom, state=on | - | 0.952 | 0 | no | 5299 | yes |
| kitchen bulb off kar dena | set_light | room=kitchen, state=off | - | 0.591 | 0 | yes | 5348 | yes |
| bathroom me lihgt jalao | set_light | room=bathroom, state=on | - | 0.761 | 1 | yes | 4280 | yes |
| crank the kitchen fan up | set_fan | room=kitchen, speed=up | - | 0.993 | 0 | no | 5353 | yes |
| hall ka pankha dheere chalao | set_fan | room=living_room, speed=down | - | 0.973 | 0 | no | 5428 | yes |
| buzz me after seven minutes | set_timer | minutes=7 | - | 0.987 | 0 | no | 5328 | yes |
| ek sau bees minute ka reminder | set_timer | minutes=120 | - | 0.981 | 0 | no | 6416 | yes |
| temperature kya chal raha hai | none | - | - | 0.612 | 1 | yes | 5381 | yes |
| whats the temp reading | show | what=temperature | - | 0.922 | 0 | no | 4185 | yes |
| just the time thanks | show | what=time | - | 0.668 | 0 | yes | 4104 | yes |
| i am a fan of that movie | none | - | - | 0.766 | 1 | yes | 6955 | yes |
| how many minutes in an hour | set_timer | - | minutes | 0.797 | 0 | yes | 6191 | yes |
| batti gul ho gayi thi kal raat | none | - | - | 0.923 | 0 | no | 7274 | yes |
| light years are a distance not a time | none | - | - | 0.852 | 2 | no | 8455 | yes |
| so tell me about yourself | none | - | - | 0.814 | 1 | yes | 5199 | yes |
| turn on the bedroom light | set_light | state=on, room=bedroom | - | 0.997 | 0 | no | 5366 | yes |
| switch the kitchen light off | set_light | room=kitchen, state=off | - | 0.998 | 0 | no | 5466 | yes |
| make the hall fan faster | set_fan | room=living_room, speed=up | - | 0.994 | 0 | no | 5279 | yes |
| set a timer for twenty minutes | set_timer | minutes=20 | - | 0.998 | 0 | no | 6377 | yes |
| what is the temperature | show | what=temperature | - | 0.885 | 0 | no | 4196 | yes |
| rasoi mein light jala do | set_light | room=kitchen, state=on | - | 0.991 | 0 | no | 5373 | yes |
| kamre ka fan tez karo | set_fan | room=bedroom, speed=up | - | 0.998 | 0 | no | 5260 | yes |
| das minute ka timer laga do | set_timer | minutes=10 | - | 0.999 | 0 | no | 6373 | yes |
| nami kitni hai | show | what=humidity | - | 0.972 | 0 | no | 2927 | yes |
| fan up karo | set_fan | speed=up | - | 0.997 | 0 | no | 2524 | yes |
| turrn on the bedrom light | set_light | state=on, room=bedroom | - | 0.988 | 1 | no | 5369 | yes |
| swich the kitchen ligt off | set_light | room=kitchen, state=off | - | 0.948 | 2 | no | 5411 | yes |
| timr for 8 minutes | set_timer | minutes=8 | - | 0.991 | 1 | no | 3873 | yes |
| light band karo | set_light | state=off | room | 0.990 | 0 | no | 3034 | yes |
| turn the light on | set_light | state=on | room | 0.996 | 0 | no | 4038 | yes |
| timer 900 minutes | set_timer | - | minutes | 1.000 | 0 | no | 2942 | yes |

31 of 31 matched, on both builds. The worst confidence gap against the desktop
C tool was **0**: every one of the 31 confidences was identical to all six
printed decimals, against both the plain build and the fast one, and so were the
margin, the unknown word count and the carrier flag. The desktop tool itself is
checked against Python on 842 sentences by `tests/test_c_parity.py`, which is
where the float16 and float32 gaps are measured; that was not repeated here.

The answers are the same ones the ESP32 gave, so the reading of them in
`demo/esp32_round/board-results.md` still stands. Seven of the 31 now go to
unsure, against five on the format 1 blob, and the one confident wrong answer in
the old table, "how many minutes in an hour" at 0.94, is caught at 0.80.

## Next to the ESP32

| | FRDM-MCXN236 | classic ESP32 |
| --- | --- | --- |
| core | Cortex-M33, 150 MHz | Xtensa LX6, 240 MHz |
| flash image | 289,064 bytes | 588,492 bytes |
| static RAM | 21,032 bytes | 40,884 bytes |
| parse, fastest | 2,524 us | 2,995 us |
| parse, mean | 5,129 us | 5,106 us |
| parse, slowest | 8,455 us | 7,866 us |
| mean in cycles | 769,442 | about 1,225,000 |

The two flash numbers are not the same measurement. The ESP32 image is an
Arduino sketch and carries the Arduino core and the display library; this one is
bare metal and carries the model, the runtime and four NXP drivers. The model
blob, 255,012 bytes, is the same on both.

The wall clock times are almost the same, which flatters the ESP32: it is
running 1.6 times faster to get there. Per cycle the M33 does the same parse in
about a third fewer cycles, which is the FPU and the Thumb-2 code doing their
job, and the 150 MHz ceiling is what closes the gap again. Both are inside the
10 ms target, with less room than before: the slowest sentence on this board is
now 8.5 ms.

## What is not verified

- Nobody watched the LED. On an earlier build the output register was read back
  over the debug probe and the pin did follow the answer, low for unsure or not
  a command and high otherwise. That readback was not repeated on this one, and
  nobody looked at the board either time.
- Only the smart home model was run on this board. The robot model fits the same
  flash with room to spare but was not flashed.
- The DWT cycle counter was live on every run, so the SysTick fallback path in
  the firmware has never actually been exercised.
