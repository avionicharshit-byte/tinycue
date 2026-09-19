#!/usr/bin/env python3
"""Send sentences to the ESP32 demo and print the JSON it sends back.

    .venv/bin/python demo/send.py "turn on the bedroom light" "fan tez karo"
    .venv/bin/python demo/send.py -f sentences.txt
    .venv/bin/python demo/send.py -f sentences.txt --json replies.jsonl

DTR and RTS are cleared before the port is opened, so talking to the board does not
reset it and whatever is on the screen stays there.
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import json
import struct
import sys
import time
from pathlib import Path

import serial


TIOCMBIC = 0x8004746B
DTR = 0x002
RTS = 0x004


def open_port(path: str, baud: int, settle: float) -> serial.Serial:
    """Open the port with both handshake lines down, so the board keeps running."""
    port = serial.Serial()
    port.port = path
    port.baudrate = baud
    port.timeout = 0.2
    port.dtr = False
    port.rts = False
    port.open()
    try:
        fcntl.ioctl(port.fileno(), TIOCMBIC, struct.pack("I", DTR | RTS))
    except OSError:
        pass
    # Opening a USB serial port can still glitch the auto-reset line on some adapters.
    # Wait out a reboot if there was one, then throw away whatever the board said.
    time.sleep(settle)
    port.reset_input_buffer()
    return port


def read_reply(port: serial.Serial, timeout: float = 4.0) -> dict:
    """The next JSON object the board prints, or a note saying nothing came back."""
    deadline = time.time() + timeout
    buffer = b""
    while time.time() < deadline:
        buffer += port.read(256)
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            text = line.strip().decode("utf-8", "replace")
            if not text.startswith("{"):
                continue
            try:
                reply = json.loads(text)
                if "ready" in reply:
                    continue  # the banner the board prints when it boots
                return reply
            except json.JSONDecodeError:
                return {"error": "the board sent something that is not JSON", "raw": text}
    return {"error": "no reply before the timeout"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="talk to the edge-nlu ESP32 demo")
    parser.add_argument("text", nargs="*", help="sentences to send")
    parser.add_argument("-f", "--file", help="a file with one sentence per line")
    parser.add_argument("-p", "--port", default=None, help="serial port")
    parser.add_argument("-b", "--baud", type=int, default=115200)
    parser.add_argument("--json", default=None, help="also write the replies as JSON lines")
    parser.add_argument("--wait", type=float, default=0.15, help="pause between sentences")
    parser.add_argument("--settle", type=float, default=2.0, help="seconds to wait after opening")
    args = parser.parse_args(argv)

    lines = list(args.text)
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            if line.strip():
                lines.append(line.strip())
    if not lines:
        print("nothing to send", file=sys.stderr)
        return 2

    path = args.port
    if path is None:
        found = sorted(glob.glob("/dev/cu.usbserial-*"))
        if not found:
            print("no /dev/cu.usbserial-* found, is the board plugged in?", file=sys.stderr)
            return 2
        path = found[0]

    replies = []
    with open_port(path, args.baud, args.settle) as port:
        for text in lines:
            port.write((text + "\n").encode("utf-8"))
            port.flush()
            reply = read_reply(port)
            reply.setdefault("text", text)
            replies.append(reply)
            print(json.dumps(reply, ensure_ascii=False))
            time.sleep(args.wait)

    if args.json:
        Path(args.json).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in replies) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
