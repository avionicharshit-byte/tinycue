# Build the desktop command line tool. The runtime itself is two files with no
# dependencies beyond libc and libm, so a device build just compiles tinycue.c.
CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -std=c99 -pedantic
LDLIBS = -lm

all: tinycue_cli

tinycue_cli: cli.o tinycue.o
	$(CC) $(CFLAGS) -o $@ cli.o tinycue.o $(LDLIBS)

cli.o: cli.c tinycue.h
	$(CC) $(CFLAGS) -c -o $@ cli.c

tinycue.o: tinycue.c tinycue.h
	$(CC) $(CFLAGS) -c -o $@ tinycue.c

clean:
	rm -f tinycue_cli cli.o tinycue.o

.PHONY: all clean
