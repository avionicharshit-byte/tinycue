# Top level helpers. The Python side is driven by the tinycue command, not by make.
PYTHON ?= .venv/bin/python
TINYCUE ?= .venv/bin/tinycue
SPEC ?= examples/smart_home.yaml
EXTRA ?= examples/smart_home.extra.yaml
DEV ?= examples/smart_home.dev.yaml
MODEL ?= out/model
DEVICE ?= out/device
SKETCH ?= demo/esp32_round
ARDUINO_LIB ?= arduino/TinyCue
ARDUINO_EXAMPLE ?= $(ARDUINO_LIB)/examples/SerialCommands
STARTER ?= out/arduino-starter
NXP_DEMO ?= demo/nxp_mcxn236
VOICE_FW ?= demo/voice/nxp_mic_stream
FQBN ?= esp32:esp32:esp32
PORT ?= /dev/cu.usbserial-0001

# Build the desktop command line tool.
cli:
	$(MAKE) -C runtime

# Train the example model and export it for the device. The extra sentences are what
# the model actually learns from; the dev file fits the confidence and the cut-off and
# is never trained on.
model:
	$(TINYCUE) train $(SPEC) --extra $(EXTRA) --dev $(DEV) -n 1500 --seed 0 -o $(MODEL)
	$(TINYCUE) export $(MODEL) -o $(DEVICE)

# What to write next: per command accuracy, confusions and unknown words.
doctor:
	$(TINYCUE) doctor $(SPEC) --extra $(EXTRA) --dev $(DEV)

# Copy the runtime and the exported model into the sketch folder. The copies are build
# output, not source, so they are not in git. Run this before building the demo.
demo-sync:
	@test -f $(DEVICE)/model_data.c || { echo "run 'make model' first"; exit 1; }
	cp runtime/tinycue.c runtime/tinycue.h $(SKETCH)/
	cp $(DEVICE)/model_data.c $(DEVICE)/model_data.h $(SKETCH)/
	@echo "synced runtime and model into $(SKETCH)"

demo-build: demo-sync
	arduino-cli compile --fqbn $(FQBN) $(SKETCH)

demo-flash: demo-build
	arduino-cli upload --fqbn $(FQBN) -p $(PORT) $(SKETCH)

# --------------------------------------------------------------- Arduino library
# The library at arduino/TinyCue holds committed copies of the runtime, because somebody
# who downloads a ZIP of this repo has to get working files. tests/test_arduino_library.py
# fails if the copies ever drift, and this target is how you fix that.
arduino-sync:
	cp runtime/tinycue.c runtime/tinycue.h $(ARDUINO_LIB)/src/
	@echo "synced runtime into $(ARDUINO_LIB)/src"

# Rebuild the model the example sketch carries: the starter device `tinycue init` writes,
# with a small hash table, because the example is for reading and not for accuracy.
arduino-example-model:
	rm -rf $(STARTER)
	$(TINYCUE) init coffee -d $(STARTER)
	$(TINYCUE) train $(STARTER)/coffee.yaml --extra $(STARTER)/coffee.extra.yaml \
	    --dev $(STARTER)/coffee.dev.yaml --table-size 4096 --seed 0 -o $(STARTER)/model
	$(TINYCUE) export $(STARTER)/model -o $(STARTER)/device
	cp $(STARTER)/device/model_data.c $(STARTER)/device/model_data.h $(ARDUINO_EXAMPLE)/
	@echo "refreshed the example model in $(ARDUINO_EXAMPLE)"

arduino-build:
	arduino-cli compile --fqbn $(FQBN) --library $(ARDUINO_LIB) $(ARDUINO_EXAMPLE)

# The FRDM-MCXN236 demo has its own makefile and copies the runtime in itself.
nxp-build:
	$(MAKE) -C $(NXP_DEMO)

nxp-flash:
	$(MAKE) -C $(NXP_DEMO) flash

# The voice demo: microphone firmware for the NXP board, and the Vosk model the
# Mac side needs. The model is 54 MB of download, so it is cached, not committed.
VOSK_MODEL ?= vosk-model-small-en-in-0.4
VOSK_DIR ?= .cache/vosk

voice-model:
	@test -d $(VOSK_DIR)/$(VOSK_MODEL) && echo "$(VOSK_DIR)/$(VOSK_MODEL) is already here" || ( \
	  mkdir -p $(VOSK_DIR) && \
	  curl -fL -o $(VOSK_DIR)/$(VOSK_MODEL).zip \
	    https://alphacephei.com/vosk/models/$(VOSK_MODEL).zip && \
	  cd $(VOSK_DIR) && unzip -q -o $(VOSK_MODEL).zip && rm -f $(VOSK_MODEL).zip && \
	  echo "unpacked $(VOSK_DIR)/$(VOSK_MODEL)" )

voice-build:
	$(MAKE) -C $(VOICE_FW)

voice-flash:
	$(MAKE) -C $(VOICE_FW) flash

# ------------------------------------------------------------------ README assets
# Redraw the four README pictures, light and dark, from docs/assets/build_assets.py.
# Needs fonttools for the text metrics; add --png, which needs playwright and Chrome,
# to rebuild the PNG copies beside them.
readme-assets:
	.venv/bin/python docs/assets/build_assets.py --png

# Re-record the terminal demo in docs/assets. It runs docs/assets/demo.sh under
# asciinema, then turns the cast into an animated SVG with svg-term-cli, coloured by
# docs/assets/demo-theme.xresources. Needs asciinema and a network connection for npx.
demo-svg:
	PATH="$(CURDIR)/.venv/bin:$$PATH" asciinema rec --overwrite \
	    --output-format asciicast-v2 --window-size 108x28 \
	    --command "bash docs/assets/demo.sh" docs/assets/demo.cast
	npx --yes svg-term-cli --in docs/assets/demo.cast --out docs/assets/demo.svg \
	    --window --width 108 --height 28 --padding 18 \
	    --term xresources --profile ./docs/assets/demo-theme.xresources
	python3 docs/assets/label_svg.py docs/assets/demo.svg \
	    "tinycue: four real sentences through tinycue parse" \
	    "A recorded terminal session. tinycue init writes a starter coffee machine, tinycue train builds the model in about three seconds, and four sentences are parsed: make me two lattes gives brew with cups 2 and drink latte at confidence 0.99, teen cup chai bana do gives brew with cups 3 and drink tea at 0.98, who won the match last night gives none at 0.98, and kindly cease the brewing apparatus comes back unsure with a best guess of none at 0.83."

test:
	.venv/bin/pytest -q

clean:
	$(MAKE) -C runtime clean
	$(MAKE) -C $(NXP_DEMO) clean
	$(MAKE) -C $(VOICE_FW) clean
	rm -f $(SKETCH)/tinycue.c $(SKETCH)/tinycue.h $(SKETCH)/model_data.c $(SKETCH)/model_data.h

.PHONY: cli model doctor demo-sync demo-build demo-flash nxp-build nxp-flash \
        arduino-sync arduino-example-model arduino-build \
        voice-model voice-build voice-flash readme-assets demo-svg test clean
