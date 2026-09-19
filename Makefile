# Top level helpers. The Python side is driven by the edgenlu command, not by make.
PYTHON ?= .venv/bin/python
EDGENLU ?= .venv/bin/edgenlu
SPEC ?= examples/smart_home.yaml
MODEL ?= out/model
DEVICE ?= out/device
SKETCH ?= demo/esp32_round
NXP_DEMO ?= demo/nxp_mcxn236
VOICE_FW ?= demo/voice/nxp_mic_stream
FQBN ?= esp32:esp32:esp32
PORT ?= /dev/cu.usbserial-0001

# Build the desktop command line tool.
cli:
	$(MAKE) -C runtime

# Train the example model and export it for the device.
model:
	$(EDGENLU) train $(SPEC) -n 1500 --seed 0 -o $(MODEL)
	$(EDGENLU) export $(MODEL) -o $(DEVICE)

# Copy the runtime and the exported model into the sketch folder. The copies are build
# output, not source, so they are not in git. Run this before building the demo.
demo-sync:
	@test -f $(DEVICE)/model_data.c || { echo "run 'make model' first"; exit 1; }
	cp runtime/edgenlu.c runtime/edgenlu.h $(SKETCH)/
	cp $(DEVICE)/model_data.c $(DEVICE)/model_data.h $(SKETCH)/
	@echo "synced runtime and model into $(SKETCH)"

demo-build: demo-sync
	arduino-cli compile --fqbn $(FQBN) $(SKETCH)

demo-flash: demo-build
	arduino-cli upload --fqbn $(FQBN) -p $(PORT) $(SKETCH)

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

test:
	.venv/bin/pytest -q

clean:
	$(MAKE) -C runtime clean
	$(MAKE) -C $(NXP_DEMO) clean
	$(MAKE) -C $(VOICE_FW) clean
	rm -f $(SKETCH)/edgenlu.c $(SKETCH)/edgenlu.h $(SKETCH)/model_data.c $(SKETCH)/model_data.h

.PHONY: cli model demo-sync demo-build demo-flash nxp-build nxp-flash \
        voice-model voice-build voice-flash test clean
