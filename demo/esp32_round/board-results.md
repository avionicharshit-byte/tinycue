# The demo on the real board

Measured on 2026-09-20 on a classic ESP32 dev board (4 MB flash, no PSRAM) with a 1.28
inch round GC9A01 display, flashed from an M1 MacBook Air with arduino-cli 1.5.1 and the
esp32 core 3.3.11, FQBN `esp32:esp32:esp32`. The model is the smart home example, trained
the way `make model` trains it, with the extra and dev sentence packs, and exported as a
format 2 blob that carries the training vocabulary and the fitted gate.

## Build

| | |
| --- | --- |
| sketch flash | 930,492 bytes, 71% of the 1,310,720 byte app partition |
| static RAM | 119,660 bytes, 36% of 327,680, leaving 208,020 for locals |
| model blob | 255,012 bytes (249.0 KB) |
| scratch buffer the runtime asked for | 10,744 bytes |
| free heap, reported by the board | 231,684 bytes, the same at boot and after every sentence |

The board is built with `-DTCUE_FAST_EXP` (see `build_opt.h`), which does the two
exponentials inside the forward pass in single precision. That roughly halves the parse
time on a chip with no hardware double. On all 842 desktop parity sentences the fast
build gives the same answers as the plain one and the same confidence to six decimals.

The screen is drawn with LVGL 9.6.0 (`lv_conf.h`, found through `-DLV_CONF_INCLUDE_SIMPLE`
in `build_opt.h`), which is what the anti-aliased type, the sweeping rim arc and the
animations cost: 342,052 bytes of flash and 78,776 bytes of static RAM against the
Adafruit_GFX version this replaced, and 78,812 bytes off the free heap. Two 240 x 30
partial draw buffers are 28,800 of that RAM and LVGL's own heap is 48 KB. None of it is
tinycue: the same blob and the same `tinycue.c` answer exactly as they did before, which
is what the table at the bottom is for. The `arduino/TinyCue` SerialCommands example,
which has no display at all, is still 353,252 bytes.

## Parse time

| | microseconds |
| --- | --- |
| fastest of the 31 sentences | 3,159 |
| mean | 5,153 |
| slowest | 7,742 |

Measured on the board with `micros()` around `tcue_parse` alone, so the serial and the
drawing are not in it. The same model on the desktop CLI averages 6.4 microseconds.
Moving the screen to LVGL did not move these: the mean was 5,106 microseconds before and
5,153 after, which is run to run noise, and still well inside the 10 ms target.

## The 31 sentences

Ten come from `eval/heldout_smart_home.yaml`, five are out of scope, three carry typos,
two leave out a required slot and one asks for a number outside the allowed range. The
rest are plain English and Hinglish.

"matched desktop" means the board's command, slot values, missing slots and unsure flag
are the same as the desktop C command line tool, and the confidence agrees to within
1e-3. The "new words" column is the board's own count of words in the sentence that no
training sentence used, which is what the gate leans on.

| sentence | command | slots | missing | confidence | new words | unsure | microseconds | matched desktop |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flick the bedroom lamp on | set_light | room=bedroom, state=on | - | 0.952 | 0 | no | 5427 | yes |
| kitchen bulb off kar dena | set_light | room=kitchen, state=off | - | 0.591 | 0 | yes | 5445 | yes |
| bathroom me lihgt jalao | set_light | room=bathroom, state=on | - | 0.761 | 1 | yes | 4424 | yes |
| crank the kitchen fan up | set_fan | room=kitchen, speed=up | - | 0.993 | 0 | no | 5481 | yes |
| hall ka pankha dheere chalao | set_fan | room=living_room, speed=down | - | 0.973 | 0 | no | 5468 | yes |
| buzz me after seven minutes | set_timer | minutes=7 | - | 0.987 | 0 | no | 5268 | yes |
| ek sau bees minute ka reminder | set_timer | minutes=120 | - | 0.981 | 0 | no | 6269 | yes |
| temperature kya chal raha hai | none | - | - | 0.612 | 1 | yes | 5379 | yes |
| whats the temp reading | show | what=temperature | - | 0.922 | 0 | no | 4260 | yes |
| just the time thanks | show | what=time | - | 0.668 | 0 | yes | 4219 | yes |
| i am a fan of that movie | none | - | - | 0.766 | 1 | yes | 6416 | yes |
| how many minutes in an hour | set_timer | - | minutes | 0.797 | 0 | yes | 6015 | yes |
| batti gul ho gayi thi kal raat | none | - | - | 0.923 | 0 | no | 6769 | yes |
| light years are a distance not a time | none | - | - | 0.852 | 2 | no | 7742 | yes |
| so tell me about yourself | none | - | - | 0.814 | 1 | yes | 5116 | yes |
| turn on the bedroom light | set_light | state=on, room=bedroom | - | 0.997 | 0 | no | 5492 | yes |
| switch the kitchen light off | set_light | room=kitchen, state=off | - | 0.998 | 0 | no | 5506 | yes |
| make the hall fan faster | set_fan | room=living_room, speed=up | - | 0.994 | 0 | no | 5412 | yes |
| set a timer for twenty minutes | set_timer | minutes=20 | - | 0.998 | 0 | no | 6175 | yes |
| what is the temperature | show | what=temperature | - | 0.885 | 0 | no | 4273 | yes |
| rasoi mein light jala do | set_light | room=kitchen, state=on | - | 0.991 | 0 | no | 5414 | yes |
| kamre ka fan tez karo | set_fan | room=bedroom, speed=up | - | 0.998 | 0 | no | 5365 | yes |
| das minute ka timer laga do | set_timer | minutes=10 | - | 0.999 | 0 | no | 6267 | yes |
| nami kitni hai | show | what=humidity | - | 0.972 | 0 | no | 3230 | yes |
| fan up karo | set_fan | speed=up | - | 0.997 | 0 | no | 3159 | yes |
| turrn on the bedrom light | set_light | state=on, room=bedroom | - | 0.988 | 1 | no | 5508 | yes |
| swich the kitchen ligt off | set_light | room=kitchen, state=off | - | 0.948 | 2 | no | 5423 | yes |
| timr for 8 minutes | set_timer | minutes=8 | - | 0.991 | 1 | no | 4027 | yes |
| light band karo | set_light | state=off | room | 0.990 | 0 | no | 3418 | yes |
| turn the light on | set_light | state=on | room | 0.996 | 0 | no | 4198 | yes |
| timer 900 minutes | set_timer | - | minutes | 1.000 | 0 | no | 3200 | yes |

31 of 31 matched. Against the desktop C tool the confidence, the margin, the unknown word
count and the carrier flag were **identical** on every sentence, to all six printed
decimals. Against Python reading its own float32 weights the worst confidence gap was
1.88e-04, on "how many minutes in an hour", which is the price of halving the weights in
the blob. Seven of the 31 go to the unsure screen.

## What the board got right and wrong

The model answers are the model's, not the runtime's: the desktop agrees with every one
of them. What the new blob changed:

- "flick the bedroom lamp on" is now read correctly, `set_light(room=bedroom, state=on)`
  at 0.95, where the old blob called it no command at 0.65. "flick" and "lamp" are in the
  extra sentence pack now, so the sentence has no new words in it at all.
- "how many minutes in an hour" is still read as a timer with the minute count missing,
  but at 0.80 instead of 0.94, so the cut-off now catches it. That was the one confident
  wrong answer in the old table.
- "i am a fan of that movie" and "so tell me about yourself" are still no command, and now
  go to unsure at 0.77 and 0.81 instead of being accepted at 0.97 and 0.96. The gate is
  paying for the honesty with coverage, which is the trade it was fitted for.
- "temperature kya chal raha hai" went the wrong way: it is now no command at 0.61 rather
  than `show(what=temperature)` at 0.70. Both go to unsure, so the device asks again
  either way, but the best guess got worse.
- "turrn on the bedrom light" now returns `room=bedroom`, the listed value, where the old
  blob passed the typo through as `room=bedrom`. The wider vocabulary reaches the right
  value through the character n-grams.
- "timer 900 minutes" still refuses the number: 900 is outside the 1 to 180 range the
  commands file allows, so the slot comes back missing rather than wrong.

## The screen

Drawn with LVGL 9.6.0 and looked at by eye on 2026-09-20. The panel shows, from the top:
the sentence it was given in grey, a 64 pixel icon for the command, the slot values large
in the state colour, and the command name with the confidence on one line under that. The
rim arc is the confidence and its colour is the verdict, green acted on, amber asking,
grey not a command.

What moves: the rim sweeps from nothing to the confidence over 460 ms, the icon scales up
past its size and settles back, a six ring halo comes up behind it, and the lines fade
and rise in 60 ms apart. An unsure answer tips the icon twice instead of settling, which
is the only motion difference between asking and acting.

**Everything is laid out inside a 200 pixel circle, not the full 240.** The module's
bezel covers the rim, so text at the very top or bottom of the frame is never seen. That
was found by looking, not by calculation: the first LVGL layout put the sentence at y=22
and the confidence at y=196 and neither was visible on the glass.

Sending `!idle` puts the waiting screen back up, the wordmark with a slow turning rim,
which is what a recording should start from.

The icons are built from rounded boxes and a rotation, so there is no icon font and no
bitmap to keep in step with the model. Two 240 x 30 partial draw buffers are all the
drawing needs and nothing is allocated per sentence: the free heap is the same after 31
sentences as at boot.
