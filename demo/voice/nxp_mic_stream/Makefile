# tinycue voice demo, board side: the FRDM-MCXN236 streams its microphone and
# parses the sentences the Mac sends back. Bare metal, same build as
# demo/nxp_mcxn236.
#
# Nothing from the NXP SDK is copied into this repository. The SDK, the CMSIS
# headers and the Arm toolchain are used where they already sit; point NXP
# somewhere else if yours live elsewhere:
#
#   make NXP=/path/to/nxp-boards
#
# board_clock.c and board_clock.h are shared with demo/nxp_mcxn236. The runtime
# and the exported model are copied in from ../../../runtime and
# ../../../out/device. Run `make model` at the repository root first.

NXP     ?= $(HOME)/nxp-boards
TC      ?= $(NXP)/toolchain/bin
SDK     ?= $(NXP)/sdk/n236/devices/MCXN236
CMSIS   ?= $(NXP)/sdk/cmsis/CMSIS/Core/Include
PYOCD   ?= $(HOME)/.local/bin/pyocd
PROBE   ?= PEV113PYXXYP5
TARGET  ?= mcxn236vdf

ROOT    ?= $(CURDIR)/../../..
RUNTIME ?= $(ROOT)/runtime
DEVICE  ?= $(ROOT)/out/device
SHARED  ?= $(ROOT)/demo/nxp_mcxn236

# 12 MHz over 12 is exactly 1 Mbaud. 921600 and 460800 are the fallbacks.
BAUD ?= 1000000

# Single precision exponentials in the CRF forward pass. The M33 has a single
# precision FPU only, so every double is software. Set to 0 for plain doubles.
FAST_EXP ?= 1

CC   := $(TC)/arm-none-eabi-gcc
SIZE := $(TC)/arm-none-eabi-size
CPU  := -mcpu=cortex-m33 -mthumb -mfloat-abi=hard -mfpu=fpv5-sp-d16

CFLAGS := $(CPU) -Os -g3 -Wall -Wextra -ffunction-sections -fdata-sections \
          -DCPU_MCXN236VDF -DSTREAM_BAUD=$(BAUD)U \
          -I. -I$(SHARED) -I$(SDK) -I$(SDK)/drivers -I$(SDK)/periph1 -I$(CMSIS)
ifeq ($(FAST_EXP),1)
CFLAGS += -DTCUE_FAST_EXP
endif

LDFLAGS := $(CPU) -T$(SDK)/gcc/MCXN236_flash.ld --specs=nano.specs --specs=nosys.specs \
           -Wl,--gc-sections -Wl,-Map=voice.map
LDLIBS  := -lm

DRIVERS := fsl_clock.c fsl_gpio.c fsl_lpuart.c fsl_lpflexcomm.c fsl_spc.c \
           fsl_reset.c fsl_pdm.c fsl_common.c fsl_common_arm.c

SRCS := main.c board_clock.c tinycue.c model_data.c system_MCXN236.c $(DRIVERS)
OBJDIR := build/fast$(FAST_EXP)
OBJS := $(addprefix $(OBJDIR)/,$(SRCS:.c=.o)) $(OBJDIR)/startup_MCXN236.o

vpath %.c $(SHARED) $(SDK) $(SDK)/drivers
vpath %.S $(SDK)/gcc

all: voice.elf

# The runtime and the model are build inputs, not sources kept here.
tinycue.c: $(RUNTIME)/tinycue.c
	cp $< $@
tinycue.h: $(RUNTIME)/tinycue.h
	cp $< $@
model_data.c: $(DEVICE)/model_data.c
	cp $< $@
model_data.h: $(DEVICE)/model_data.h
	cp $< $@

$(DEVICE)/model_data.c $(DEVICE)/model_data.h:
	@echo "missing $@: run 'make model' at the repository root first"; exit 1

sync: tinycue.c tinycue.h model_data.c model_data.h

$(OBJS): | $(OBJDIR)
$(OBJDIR)/main.o $(OBJDIR)/tinycue.o $(OBJDIR)/model_data.o: tinycue.h model_data.h

$(OBJDIR):
	mkdir -p $(OBJDIR)

$(OBJDIR)/%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

$(OBJDIR)/%.o: %.S
	$(CC) $(CFLAGS) -c -o $@ $<

voice.elf: $(OBJS)
	$(CC) $(OBJS) $(LDFLAGS) $(LDLIBS) -o $@
	$(SIZE) $@

flash: voice.elf
	$(PYOCD) flash -u $(PROBE) -t $(TARGET) $<

clean:
	rm -rf $(OBJDIR) voice.elf voice.map tinycue.c tinycue.h model_data.c model_data.h

.PHONY: all sync flash clean
