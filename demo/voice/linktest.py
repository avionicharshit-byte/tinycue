#!/usr/bin/env python3
"""Measure the NXP audio link, and record from it, before trusting the audio.

    .venv/bin/python demo/voice/linktest.py throughput --seconds 30
    .venv/bin/python demo/voice/linktest.py record --seconds 5 -o out/voice/room.wav

`throughput` puts the board in counter mode: it fills every packet with a known
byte pattern instead of audio and sends them as fast as the port takes them.
That gives the real bytes a second, and the sequence numbers give the packet
loss, with no guessing about whether the audio itself was any good.

`record` takes a few seconds of audio through the same framing and prints the
level, so the gain can be set from a measurement.
"""

from __future__ import annotations

import argparse
import glob
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from listen import FRAME_AUDIO, FRAME_JSON, FRAME_TEST, FrameReader, open_port  # noqa: E402

SAMPLE_RATE = 16000


def find_port(given):
    if given:
        return given
    found = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not found:
        raise SystemExit("no /dev/cu.usbmodem*, is the NXP board plugged in?")
    return found[0]


def throughput(args) -> int:
    port = open_port(find_port(args.port), args.baud, args.settle)
    reader = FrameReader(port)
    port.write(b"!test\n")
    port.flush()
    time.sleep(0.3)
    port.reset_input_buffer()
    reader.buffer.clear()

    started = time.time()
    payload_bytes = 0
    bad_pattern = 0
    expect = None
    frames = 0
    while time.time() - started < args.seconds:
        for kind, _seq, _aux, data in reader.read():
            if kind != FRAME_TEST:
                continue
            frames += 1
            payload_bytes += len(data)
            if expect is None:
                expect = (data[0] + len(data)) & 0xFF
            else:
                if data[0] != expect:
                    bad_pattern += 1
                expect = (data[0] + len(data)) & 0xFF
    elapsed = time.time() - started
    port.write(b"!audio\n")
    port.flush()
    port.close()

    wire = payload_bytes + frames * 14
    lost = reader.gaps.get(FRAME_TEST, 0)
    print(f"counter test at {args.baud} baud for {elapsed:.1f} s")
    print(f"  packets     {frames}, {lost} missing by sequence number "
          f"({100.0 * lost / max(frames + lost, 1):.3f}%)")
    print(f"  payload     {payload_bytes} bytes, {payload_bytes / elapsed:.0f} B/s")
    print(f"  on the wire {wire} bytes, {wire / elapsed:.0f} B/s "
          f"({8 * wire / elapsed / 1000:.0f} kbit/s)")
    print(f"  resyncs {reader.resyncs}, bytes thrown away {reader.lost_bytes}, "
          f"pattern breaks {bad_pattern}")
    print(f"  16 kHz 16 bit mono needs 32000 B/s of payload, "
          f"{32700} B/s on the wire")
    return 0


def record(args) -> int:
    import numpy

    port = open_port(find_port(args.port), args.baud, args.settle)
    reader = FrameReader(port)
    port.write(b"!audio\n")
    port.flush()
    if args.gain:
        port.write(f"!gain {args.gain}\n".encode())
        port.flush()
    time.sleep(0.4)
    port.reset_input_buffer()
    reader.buffer.clear()

    wanted = int(SAMPLE_RATE * args.seconds) * 2
    audio = bytearray()
    overruns = 0
    started = time.time()
    while len(audio) < wanted and time.time() - started < args.seconds + 10:
        for kind, _seq, aux, data in reader.read():
            if kind == FRAME_AUDIO:
                audio += data
                overruns = max(overruns, aux)
            elif kind == FRAME_JSON:
                print("board:", data.decode("utf-8", "replace"))
    elapsed = time.time() - started
    port.close()

    audio = bytes(audio[:wanted])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio)

    a = numpy.frombuffer(audio, dtype="<i2").astype(float)
    body = a[1600:]  # skip the decimation filter start-up transient
    windows = numpy.array([body[i:i + 800].std() for i in range(0, len(body) - 800, 800)])
    clipped = int((numpy.abs(body) >= 32767).sum())
    print(f"saved {out}: {len(a)} samples, {len(a) / SAMPLE_RATE:.2f} s "
          f"in {elapsed:.2f} s of wall clock")
    print(f"  rms {body.std():.0f}, peak {numpy.abs(body).max():.0f}, "
          f"quietest 20 ms window {windows.min():.0f}, "
          f"median {numpy.median(windows):.0f}, loudest {windows.max():.0f}")
    print(f"  clipped samples {clipped}, board overruns {overruns}, "
          f"resyncs {reader.resyncs}, missing packets "
          f"{reader.gaps.get(FRAME_AUDIO, 0)}")
    return 0


def rate(args) -> int:
    """How many samples a second really arrive, board clock against wall clock.

    The window is bracketed by two !ping round trips. Timing it from the first
    packet instead reads high, because the port already holds a backlog then and
    those samples get charged to a window that has not elapsed yet.
    """
    import json as jsonlib

    port = open_port(find_port(args.port), args.baud, args.settle)
    reader = FrameReader(port)

    def ping():
        port.write(b"!ping\n")
        port.flush()
        deadline = time.time() + 3.0
        while time.time() < deadline:
            for kind, _seq, _aux, data in reader.read():
                if kind == FRAME_JSON:
                    return jsonlib.loads(data.decode("utf-8", "replace")), time.time()
        raise SystemExit("the board did not answer !ping")

    before, at_before = ping()
    samples = 0
    started = time.time()
    while time.time() - started < args.seconds:
        for kind, _seq, _aux, data in reader.read():
            if kind == FRAME_AUDIO:
                samples += len(data) // 2
    after, at_after = ping()
    port.close()

    wall = at_after - at_before
    board_samples = after["captured"] - before["captured"]
    board_seconds = after["seconds"] - before["seconds"]
    print(f"host  {samples} samples in {wall:.3f} s = {samples / wall:.1f} Hz")
    print(f"board {board_samples} samples in {board_seconds:.3f} board seconds "
          f"= {board_samples / board_seconds:.1f} Hz")
    print(f"  asked for {SAMPLE_RATE} Hz. the board clock is the core PLL, which is "
          f"an internal oscillator; the microphone clock is the 24 MHz crystal")
    print(f"  missing packets {reader.gaps.get(FRAME_AUDIO, 0)}, "
          f"resyncs {reader.resyncs}, fifo overflow {after['fifo_over']}, "
          f"fifo underflow {after['fifo_under']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="measure the NXP audio link")
    parser.add_argument("-p", "--port", default=None)
    parser.add_argument("-b", "--baud", type=int, default=1000000)
    parser.add_argument("--settle", type=float, default=1.0)
    sub = parser.add_subparsers(dest="what", required=True)

    t = sub.add_parser("throughput")
    t.add_argument("--seconds", type=float, default=30.0)
    t.set_defaults(run=throughput)

    r = sub.add_parser("record")
    r.add_argument("--seconds", type=float, default=5.0)
    r.add_argument("--gain", type=int, default=None)
    r.add_argument("-o", "--out", default="out/voice/room.wav")
    r.set_defaults(run=record)

    s = sub.add_parser("rate")
    s.add_argument("--seconds", type=float, default=20.0)
    s.set_defaults(run=rate)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
