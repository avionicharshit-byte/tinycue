# Build the desktop command line tool. The runtime itself is two files with no
# dependencies beyond libc and libm, so a device build just compiles edgenlu.c.
CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -std=c99 -pedantic
LDLIBS = -lm

all: enlu_cli

enlu_cli: cli.o edgenlu.o
	$(CC) $(CFLAGS) -o $@ cli.o edgenlu.o $(LDLIBS)

cli.o: cli.c edgenlu.h
	$(CC) $(CFLAGS) -c -o $@ cli.c

edgenlu.o: edgenlu.c edgenlu.h
	$(CC) $(CFLAGS) -c -o $@ edgenlu.c

clean:
	rm -f enlu_cli cli.o edgenlu.o

.PHONY: all clean
