# The demo on the real board

Measured on 2026-09-19 on a classic ESP32 dev board (4 MB flash, no PSRAM) with a 1.28
inch round GC9A01 display, flashed from an M1 MacBook Air with arduino-cli 1.5.1 and the
esp32 core 3.3.11, FQBN `esp32:esp32:esp32`. The model is the smart home example, trained
the way `make model` trains it, with the extra and dev sentence packs, and exported as a
format 2 blob that carries the training vocabulary and the fitted gate.

## Build

| | |
| --- | --- |
| sketch flash | 588,492 bytes, 44% of the 1,310,720 byte app partition |
| static RAM | 40,884 bytes, 12% of 327,680, leaving 286,796 for locals |
| model blob | 255,012 bytes (249.0 KB) |
| scratch buffer the runtime asked for | 10,744 bytes |
| free heap, reported by the board | 310,496 bytes, the same at boot and after every sentence |

The board is built with `-DENLU_FAST_EXP` (see `build_opt.h`), which does the two
exponentials inside the forward pass in single precision. That roughly halves the parse
time on a chip with no hardware double. On all 842 desktop parity sentences the fast
build gives the same answers as the plain one and the same confidence to six decimals.

Against the format 1 blob this sketch grew by 47,860 bytes of flash, 56 bytes of static
RAM and 32 bytes of scratch. Almost all of the flash is the blob itself, 45,372 bytes
larger: the extra sentence packs widened the vocabulary and the gate added the training
word hashes. The remaining 2,488 bytes are the runtime's gate code, the four gate fields
the JSON line now carries and the one extra line on the unsure screen.

## Parse time

| | microseconds |
| --- | --- |
| fastest of the 31 sentences | 2,995 |
| mean | 5,106 |
| slowest | 7,866 |

Measured on the board with `micros()` around `enlu_parse` alone, so the serial and the
drawing are not in it. The same model on the desktop CLI averages 6.4 microseconds. The
mean is 730 microseconds slower than on the format 1 blob, which is the wider vocabulary
and the unknown word lookup, and still well inside the 10 ms target.

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
| flick the bedroom lamp on | set_light | room=bedroom, state=on | - | 0.952 | 0 | no | 5612 | yes |
| kitchen bulb off kar dena | set_light | room=kitchen, state=off | - | 0.591 | 0 | yes | 5372 | yes |
| bathroom me lihgt jalao | set_light | room=bathroom, state=on | - | 0.761 | 1 | yes | 4323 | yes |
| crank the kitchen fan up | set_fan | room=kitchen, speed=up | - | 0.993 | 0 | no | 5451 | yes |
| hall ka pankha dheere chalao | set_fan | room=living_room, speed=down | - | 0.973 | 0 | no | 5473 | yes |
| buzz me after seven minutes | set_timer | minutes=7 | - | 0.987 | 0 | no | 5230 | yes |
| ek sau bees minute ka reminder | set_timer | minutes=120 | - | 0.981 | 0 | no | 6270 | yes |
| temperature kya chal raha hai | none | - | - | 0.612 | 1 | yes | 5361 | yes |
| whats the temp reading | show | what=temperature | - | 0.922 | 0 | no | 4157 | yes |
| just the time thanks | show | what=time | - | 0.668 | 0 | yes | 4166 | yes |
| i am a fan of that movie | none | - | - | 0.766 | 1 | yes | 6412 | yes |
| how many minutes in an hour | set_timer | - | minutes | 0.797 | 0 | yes | 5985 | yes |
| batti gul ho gayi thi kal raat | none | - | - | 0.923 | 0 | no | 6859 | yes |
| light years are a distance not a time | none | - | - | 0.852 | 2 | no | 7866 | yes |
| so tell me about yourself | none | - | - | 0.814 | 1 | yes | 5027 | yes |
| turn on the bedroom light | set_light | state=on, room=bedroom | - | 0.997 | 0 | no | 5362 | yes |
| switch the kitchen light off | set_light | room=kitchen, state=off | - | 0.998 | 0 | no | 5467 | yes |
| make the hall fan faster | set_fan | room=living_room, speed=up | - | 0.994 | 0 | no | 5348 | yes |
| set a timer for twenty minutes | set_timer | minutes=20 | - | 0.998 | 0 | no | 6179 | yes |
| what is the temperature | show | what=temperature | - | 0.885 | 0 | no | 4227 | yes |
| rasoi mein light jala do | set_light | room=kitchen, state=on | - | 0.991 | 0 | no | 5364 | yes |
| kamre ka fan tez karo | set_fan | room=bedroom, speed=up | - | 0.998 | 0 | no | 5349 | yes |
| das minute ka timer laga do | set_timer | minutes=10 | - | 0.999 | 0 | no | 6324 | yes |
| nami kitni hai | show | what=humidity | - | 0.972 | 0 | no | 3114 | yes |
| fan up karo | set_fan | speed=up | - | 0.997 | 0 | no | 2995 | yes |
| turrn on the bedrom light | set_light | state=on, room=bedroom | - | 0.988 | 1 | no | 5397 | yes |
| swich the kitchen ligt off | set_light | room=kitchen, state=off | - | 0.948 | 2 | no | 5347 | yes |
| timr for 8 minutes | set_timer | minutes=8 | - | 0.991 | 1 | no | 3881 | yes |
| light band karo | set_light | state=off | room | 0.990 | 0 | no | 3280 | yes |
| turn the light on | set_light | state=on | room | 0.996 | 0 | no | 4042 | yes |
| timer 900 minutes | set_timer | - | minutes | 1.000 | 0 | no | 3034 | yes |

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

Not verified by eye. Nobody looked at the display while these sentences ran, so the
layout, the colours and the confidence ring are untested. What is known is that the
drawing code ran on every sentence without crashing or leaking: the free heap is the same
after 31 sentences as it was at boot, and every reply came back.

The unsure screen now adds one line under the percentage, "2 new words", when the board
counted any word it has never seen. That line is drawn from the same bounded buffer as
the rest and is skipped when the count is zero. It has not been looked at either.

The drawing is deliberately plain. It writes straight to the panel instead of building a
frame buffer, so it needs no heap at all, and every string it draws is bounded.
