# Talking to the boards

Say a command out loud near the NXP board and the ESP32's round display shows
what it understood, about a second later.

**Be honest about which part runs where.** The speech to text runs on the Mac,
with [Vosk](https://alphacephei.com/vosk/), offline. Only the command
understanding runs on the chips. edge-nlu turns text into a command, slot values
and a confidence; it is not a speech recogniser and this demo does not make it
one. What the two chips do here is exactly what they do in the other demos: read
a sentence, answer in under 6 milliseconds.

The flow:

1. The **FRDM-MCXN236** captures its on-board microphone at 16 kHz and streams
   the audio to the Mac over the MCU-Link virtual COM port, in framed packets.
2. The **Mac** runs Vosk against a word list built from the commands file, so the
   recogniser can only produce words this domain uses.
3. Each finished sentence goes **back to both boards**. The ESP32 parses it and
   draws the answer on the round display. The NXP board parses the same sentence
   on the same serial link it is streaming audio on, and lights its red LED when
   the answer is unsure.

Both boards ran the same sentence and agreed to all six printed decimals of
confidence, every time.

## Run it

Both boards plugged in over USB. Once, to fetch the model and flash the board:

```sh
make model                 # train and export, if out/device is not there yet
make voice-model           # download the Vosk model into .cache/vosk, 54 MB
make voice-flash           # build the microphone firmware and flash the NXP board
```

Then, to listen:

```sh
.venv/bin/python demo/voice/listen.py
```

Speak a command and stop. Try **"turn on the bedroom light"** first. Ctrl-C to
stop.

The Python side needs `vosk`, `pyserial` and, only for `--source mac`,
`sounddevice`:

```sh
uv pip install --python .venv/bin/python vosk pyserial sounddevice
```

Useful options:

| option | what it does |
| --- | --- |
| `--spec examples/robot.yaml` | build the vocabulary from a different commands file |
| `--source mac` | use the Mac's own microphone instead of the board |
| `--mac-device "MacBook Air Microphone"` | pick the input device, so it does not grab a headset |
| `--wav 'out/voice/say/*.wav'` | read WAV files instead of a microphone |
| `--no-display` | do not talk to the ESP32 |
| `--no-board-parse` | stream audio only, do not send text back to the NXP board |
| `--mic-gain 4` | change the board's fixed microphone gain, 1 to 64 |
| `--save-wav out/voice/last.wav` | where to keep the last utterance, the default |
| `--json out/voice/run.jsonl` | one JSON line per utterance |

Neither board is reset when its port is opened, so whatever is on the round
display stays there.

`linktest.py` measures the link before you trust the audio:

```sh
.venv/bin/python demo/voice/linktest.py throughput --seconds 30
.venv/bin/python demo/voice/linktest.py rate --seconds 15
.venv/bin/python demo/voice/linktest.py record --seconds 5 -o out/voice/room.wav
```

## The board firmware

[nxp_mic_stream](nxp_mic_stream) is the FRDM-MCXN236 side: bare metal, no RTOS,
built the same way as [demo/nxp_mcxn236](../nxp_mcxn236) and sharing its
`board_clock.c`. It carries the whole edge-nlu runtime and model as well as the
microphone, so it both streams audio and parses sentences.

- MICFIL reads the PDM microphone on **channel 1** at 16 kHz. Channels 0 and 1
  are the two clock edges of one data line, not two microphones, and on this
  board only channel 1 carries bits.
- The MICFIL interrupt drops samples into a 4096 sample ring buffer, so a
  5 millisecond `enlu_parse` in the main loop costs no audio. The main loop
  drains the ring into packets.
- A one pole running mean is subtracted from every sample and a fixed gain
  applied, on top of the hardware DC remover.
- The link runs at **1,000,000 baud**. The LPUART is clocked at 12 MHz, so the
  divider is exactly 12 and the baud rate is not an approximation.

One packet is 20 milliseconds of audio:

```
 0  4  sync   a5 5a e1 1e
 4  1  type   1 audio, 2 json, 3 counter test
 5  1  spare
 6  2  seq    per type, wraps at 16 bits
 8  2  aux    audio: overruns so far
10  2  len    payload bytes
12  n  payload
    2  sum    16 bit sum of everything after the sync word
```

Losing bytes costs 20 milliseconds of audio: the host hunts for the sync word
again and checks the sum before accepting a packet.

Text goes the other way on the same link. A line starting with `!` is a control
command (`!audio`, `!test`, `!stop`, `!gain <n>`, `!ping`, `!reset`); anything
else is a sentence to parse, and the answer comes back as a type 2 frame
carrying the same JSON the other demos print.

## Measured

On an M1 MacBook Air, FRDM-MCXN236 at 150 MHz, classic ESP32, smart home model.

### The link

| | |
| --- | --- |
| baud | 1,000,000 on the MCU-Link VCOM |
| counter test | 30 s, 4,599 packets, **0 missing**, 0 pattern breaks |
| measured throughput | 100,258 bytes a second on the wire, 802 kbit/s |
| that is | the full ceiling for 1 Mbaud with 8N1, 100,000 bytes a second |
| audio needs | 32,700 bytes a second, 33% of the link |
| sample rate | 16,004 Hz measured over 15 s, host and board agreeing to 0.01% |
| FIFO overflow, underflow | 0 and 0 |

1,000,000 baud worked first time, so the 8 kHz and mu-law fallbacks in the brief
were never needed. The three times of headroom is what lets the board stop
sending for five milliseconds to parse a sentence without losing a sample.

### Microphone level, at the gain that ships (2)

| | 20 ms window rms | peak sample |
| --- | --- | --- |
| quiet room | 329, range 255 to 680 | 3,614 |
| speech, from the Mac speakers a short distance away | up to 4,784, p95 2,082 | 15,868 |

No clipped samples, no overruns, no lost packets. Speech sits 6 to 15 times
above the room floor in rms, and peaks at about half of full scale. Gain 4 was
measured first and peaked at 31,716, which is one loud word away from clipping,
which is why the default is 2. `--mic-gain` changes it live.

### Speech to text, ten synthesised commands

Ten commands spoken by macOS `say`, converted to 16 kHz mono WAV, fed through
the same Vosk and grammar code as the live path, and the text sent to the ESP32.

| said | Vosk heard | ESP32 decided | confidence | right |
| --- | --- | --- | --- | --- |
| turn on the bedroom light | turn on the bedroom **night** | `set_light(state=on, room=bedroom)` | 0.945 | yes |
| switch the kitchen light off | switch the kitchen light off | `set_light(room=kitchen, state=off)` | 0.997 | yes |
| please put the living room lights on | please put the living room lights on | `set_light(room=living_room, state=on)` | 0.997 | yes |
| make the bedroom fan faster | make the bedroom fan faster | `set_fan(room=bedroom, speed=up)` | 0.999 | yes |
| fan speed down | fan speed down | `set_fan(speed=down)` | 0.999 | yes |
| speed up the fan in the kitchen | speed up the fan in the kitchen | `set_fan(speed=up, room=kitchen)` | 0.997 | yes |
| set a timer for ten minutes | set a timer for ten minutes | `set_timer(minutes=10)` | 0.996 | yes |
| remind me in twenty minutes | remind me in twenty minutes | `set_timer(minutes=20)` | 0.998 | yes |
| what is the temperature | what is the temperature | `show(what=temperature)` | 0.992 | yes |
| show me the time | show me the time | `show(what=time)` | 0.997 | yes |

**10 of 10 commands and slots right.** One word was misheard, "light" as
"night", and it did not matter because "light" is a carrier word here and not a
slot value. The board answered in 42 to 95 milliseconds of round trip including
the 115200 baud serial.

These ten are the commands file's own example sentences, so they are the easy
case. See the limits below for what happens on wording it has not seen.

### The whole thing, out loud

Five of those commands played out of the Mac's built-in speakers at the volume
the machine was already set to, heard by the NXP microphone, recognised on the
Mac, and sent to both boards.

| said | heard | decided | endpoint wait | boards | total |
| --- | --- | --- | --- | --- | --- |
| turn on the bedroom light | turn on the bedroom night | `set_light(state=on, room=bedroom)` 0.945 | 924 ms | 42 ms | 966 ms |
| switch the kitchen light off | kitchen light off | `set_light(room=kitchen, state=off)` 0.999 | 751 ms | 44 ms | 795 ms |
| make the bedroom fan faster | make the bedroom fan faster | `set_fan(room=bedroom, speed=up)` 0.999 | 915 ms | 45 ms | 960 ms |
| set a timer for ten minutes | that a timer for ten minutes | `set_timer(minutes=10)` 0.992 | 841 ms | 44 ms | 885 ms |
| what is the temperature | what is the temperature | `show(what=temperature)` 0.992 | 1054 ms | 45 ms | 1099 ms |

**5 of 5 right.** Delay from the last sound of the sentence to the answer:
**795 to 1099 milliseconds, mean 941**. Over 3,100 audio packets arrived with
none missing, no resyncs after the first, and no board overruns.

Almost all of that delay is Vosk deciding the sentence has ended. Its
`--endpoint.rule2.min-trailing-silence` is 0.5 seconds, and the rest is decoding
lag. The two boards together account for 44 milliseconds of the 941, and the
parse itself for 3 to 6 of those. Lowering the trailing silence in the model's
`conf/model.conf` is the knob if a second feels long.

## The grammar

The word list handed to Vosk is built from the commands file, every time, by
`spec_vocabulary` and `spec_phrases` in `listen.py`. Nothing in the script knows
a single word of any domain: `--spec examples/robot.yaml` gives a robot
vocabulary.

It collects every token of every example sentence, every slot surface form and
canonical value, the equivalent word groups, the filler and droppable words, the
out-of-scope sentences, the English spelling of every number from 0 to 180, and
`[unk]`. For the smart home file that is **329 words**.

It then adds the **134 example sentences as whole phrases**. That is worth more
than everything else put together. A flat bag of words lets the decoder emit any
sequence, and it pays a cost per word, so it drops the short ones: "turn on the
bedroom light" comes back as "bedroom light". Measured on the ten sentences
above:

| grammar | word recall | exactly right |
| --- | --- | --- |
| no grammar at all, open vocabulary | 80.4% | 6 of 10 |
| the 329 words as a flat bag | 54.9% | 1 of 10 |
| the same words plus the 134 example sentences | **98.0%** | **9 of 10** |

The phrases do not overfit. On five sentences built from grammar words in
combinations that appear in no example ("turn on the kitchen light", "make the
living room fan slower"), the phrase grammar still won: 81.5% word recall
against 55.6% for the flat bag and 77.8% for open vocabulary.

## Known limits

- **The Hinglish half cannot be heard at all.** Vosk's English models have no
  entry for 152 of the 481 words in the smart home file, so those words can
  never come out of the recogniser however clearly they are said. A word that is
  not in the grammar is not a matching failure, it is deafness, and no amount of
  work further down the pipeline can rescue it. The dropped list is printed on
  every run and includes every Hinglish slot value: `rasoi`, `batti`, `tez`,
  `pankha`, `kamre`, `baithak`, `gusalkhana`, `jalao`, `chalu`, `nami`, `garmi`,
  `samay`, `kya`, `karo`, `kar`, `dheere`, `badha`, `bujha`, `chalis`,
  `pandrah`, `sau` and the rest. So the model's best feature, that it
  understands Hinglish typed in Latin letters, is invisible through this
  microphone. Typing those sentences into `demo/send.py` still works.
  Fixing it needs a recogniser with a Hindi or code-mixed lexicon, or
  proxy spellings that an English lexicon can reach.
- **The vocabulary is closed.** The held-out test file deliberately uses words
  the commands file never lists, "lamp", "bulb", "flick", "illuminate", so those
  sentences cannot be recognised here either. That is the trade a
  grammar-constrained recogniser makes, and it is the right one for a fixed
  command set: the alternative, open vocabulary, was measurably worse on the
  commands that matter.
- **About a second, not half a second.** The delay is dominated by Vosk's
  endpointer, not by anything on a chip.
- **The numbers above were measured with synthesised speech played through a
  speaker**, not a human voice. A person speaking close to the board will be
  louder and will probably want `--mic-gain 1`.
- **One microphone, no beamforming, no noise suppression.** The quiet room here
  sat at 1% of full scale. A noisy room has not been tried.
- **Nobody has watched the round display during a voice run.** The ESP32 answers
  with the right JSON, and the drawing code is the same code as the other demo,
  but the screen has not been checked by eye.
