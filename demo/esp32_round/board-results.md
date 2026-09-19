# The demo on the real board

Measured on 2026-09-19 on a classic ESP32 dev board (4 MB flash, no PSRAM) with a 1.28
inch round GC9A01 display, flashed from an M1 MacBook Air with arduino-cli 1.5.1 and the
esp32 core 3.3.11, FQBN `esp32:esp32:esp32`. The model is the smart home example, trained
with `edgenlu train examples/smart_home.yaml -n 1500 --seed 0`.

## Build

| | |
| --- | --- |
| sketch flash | 540,632 bytes, 41% of the 1,310,720 byte app partition |
| static RAM | 40,828 bytes, 12% of 327,680, leaving 286,852 for locals |
| model blob | 209,640 bytes (204.7 KB) |
| scratch buffer the runtime asked for | 10,712 bytes |
| free heap, reported by the board | 310,552 bytes, the same before and after every sentence |

The board is built with `-DENLU_FAST_EXP` (see `build_opt.h`), which does the two
exponentials inside the forward pass in single precision. That roughly halves the parse
time on a chip with no hardware double. On all 842 desktop parity sentences the fast
build gives the same answers as the plain one and the same confidence to six decimals.

## Parse time

| | microseconds |
| --- | --- |
| fastest of the 31 sentences | 2,537 |
| mean | 4,376 |
| slowest | 6,534 |

Measured on the board with `micros()` around `enlu_parse` alone, so the serial and the
drawing are not in it. The same model on the desktop CLI averages 7.5 microseconds.

## The 31 sentences

Ten come from `eval/heldout_smart_home.yaml`, five are out of scope, three carry typos,
two leave out a required slot and one asks for a number outside the allowed range. The
rest are plain English and Hinglish.

"matched desktop" means the board's command, slot values and unsure flag are the same as
the desktop C command line tool and as Python, and the confidence agrees to within 1e-3.
Against the desktop C tool the confidence was in fact identical to all six printed
decimals on every sentence.

| sentence | command | slots | missing | confidence | unsure | microseconds | matched desktop |
| --- | --- | --- | --- | --- | --- | --- | --- |
| flick the bedroom lamp on | none | - | - | 0.651 | yes | 4766 | yes |
| kitchen bulb off kar dena | none | - | - | 0.606 | yes | 4575 | yes |
| bathroom me lihgt jalao | set_light | room=bathroom, state=on | - | 0.660 | yes | 3679 | yes |
| crank the kitchen fan up | set_fan | room=kitchen, speed=up | - | 0.985 | no | 4588 | yes |
| hall ka pankha dheere chalao | set_fan | room=living_room, speed=down | - | 0.938 | no | 4761 | yes |
| buzz me after seven minutes | set_timer | minutes=7 | - | 0.972 | no | 4536 | yes |
| ek sau bees minute ka reminder | set_timer | minutes=120 | - | 0.997 | no | 5431 | yes |
| temperature kya chal raha hai | show | what=temperature | - | 0.696 | yes | 4772 | yes |
| whats the temp reading | none | - | - | 0.672 | yes | 3584 | yes |
| just the time thanks | none | - | - | 0.657 | yes | 3446 | yes |
| i am a fan of that movie | none | - | - | 0.972 | no | 5524 | yes |
| how many minutes in an hour | set_timer | - | minutes | 0.941 | no | 5043 | yes |
| batti gul ho gayi thi kal raat | none | - | - | 0.989 | no | 5856 | yes |
| light years are a distance not a time | none | - | - | 0.871 | yes | 6534 | yes |
| so tell me about yourself | none | - | - | 0.964 | no | 4217 | yes |
| turn on the bedroom light | set_light | state=on, room=bedroom | - | 0.996 | no | 4652 | yes |
| switch the kitchen light off | set_light | room=kitchen, state=off | - | 0.997 | no | 4688 | yes |
| make the hall fan faster | set_fan | room=living_room, speed=up | - | 0.998 | no | 4573 | yes |
| set a timer for twenty minutes | set_timer | minutes=20 | - | 0.988 | no | 5319 | yes |
| what is the temperature | show | what=temperature | - | 0.992 | no | 3661 | yes |
| rasoi mein light jala do | set_light | room=kitchen, state=on | - | 0.998 | no | 4600 | yes |
| kamre ka fan tez karo | set_fan | room=bedroom, speed=up | - | 0.998 | no | 4509 | yes |
| das minute ka timer laga do | set_timer | minutes=10 | - | 0.998 | no | 5396 | yes |
| nami kitni hai | show | what=humidity | - | 0.979 | no | 2733 | yes |
| fan up karo | set_fan | speed=up | - | 0.984 | no | 2537 | yes |
| turrn on the bedrom light | set_light | state=on, room=bedrom | - | 0.956 | no | 4581 | yes |
| swich the kitchen ligt off | set_light | room=kitchen, state=off | - | 0.912 | no | 4618 | yes |
| timr for 8 minutes | set_timer | minutes=8 | - | 0.991 | no | 3408 | yes |
| light band karo | set_light | state=off | room | 0.994 | no | 2826 | yes |
| turn the light on | set_light | state=on | room | 0.993 | no | 3529 | yes |
| timer 900 minutes | set_timer | - | minutes | 0.999 | no | 2726 | yes |

31 of 31 matched. The worst confidence gap against any of the four references (the plain
desktop C build, the fast desktop C build, Python with the float16 weights the blob
carries, and Python with its own float32 weights) was 1.22e-04, and that one is against
float32 Python, which is the price of halving the weights.

## What the board got right and wrong

The model answers are the model's, not the runtime's: the desktop agrees with every one
of them. Worth reading anyway:

- "flick the bedroom lamp on" and "kitchen bulb off kar dena" are read as no command at
  all, at 0.65 and 0.61, and both go to the unsure screen. The words "flick", "lamp" and
  "bulb" are in no example sentence.
- "how many minutes in an hour" is read as a timer with the minute count missing, at
  0.94. That is a confident wrong answer and the cut-off does not catch it.
- "timer 900 minutes" correctly refuses the number: 900 is outside the 1 to 180 range the
  commands file allows, so the slot comes back missing rather than wrong.
- "turrn on the bedrom light" keeps working through the typos, and hands back
  `room=bedrom` because that is not a listed room, which is the open vocabulary path.

## The screen

Not verified by eye. Nobody looked at the display while these sentences ran, so the
layout, the colours and the confidence ring are untested. What is known is that the
drawing code ran on every sentence without crashing or leaking: the free heap is the same
after 31 sentences as it was at boot, and every reply came back.

The drawing is deliberately plain. It writes straight to the panel instead of building a
frame buffer, so it needs no heap at all, and every string it draws is bounded.
