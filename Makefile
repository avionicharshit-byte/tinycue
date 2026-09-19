# Top level helpers. The Python side is driven by the edgenlu command, not by make.
PYTHON ?= .venv/bin/python
EDGENLU ?= .venv/bin/edgenlu
SPEC ?= examples/smart_home.yaml
MODEL ?= out/model
DEVICE ?= out/device
SKETCH ?= demo/esp32_round
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

test:
	.venv/bin/pytest -q

clean:
	$(MAKE) -C runtime clean
	rm -f $(SKETCH)/edgenlu.c $(SKETCH)/edgenlu.h $(SKETCH)/model_data.c $(SKETCH)/model_data.h

.PHONY: cli model demo-sync demo-build demo-flash test clean
