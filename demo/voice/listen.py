#!/usr/bin/env python3
"""Listen to a microphone, turn speech into text, and let the boards understand it.

Speech to text runs here on the Mac, with Vosk, offline. The command
understanding runs on the chips: the recognised sentence goes to the ESP32,
which parses it with the tinycue runtime and draws the answer on its round
display, and to the NXP board, which parses it too and sets its red LED.

    .venv/bin/python demo/voice/listen.py
    .venv/bin/python demo/voice/listen.py --source mac
    .venv/bin/python demo/voice/listen.py --wav out/voice/say/*.wav

The audio comes from the FRDM-MCXN236 running demo/voice/nxp_mic_stream, which
streams 16 kHz 16 bit mono in small framed packets, or from the Mac's own
microphone with --source mac, or from WAV files with --wav.

The word list Vosk is allowed to output is built from the commands file, so the
recogniser can only produce words this domain uses. Nothing here knows about any
one domain: point --spec at a different commands file and the vocabulary follows.

Neither board is reset when its port is opened.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# demo/send.py owns the one way to open a board's port without resetting it.
sys.path.insert(0, str(REPO_ROOT / "demo"))

from send import open_port, read_reply  # noqa: E402

SYNC = b"\xa5\x5a\xe1\x1e"
HEADER_BYTES = 12
FRAME_AUDIO = 1
FRAME_JSON = 2
FRAME_TEST = 3
MAX_PAYLOAD = 2048

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 320  # 20 ms, the same size the board sends


# ----------------------------------------------------------------- framing


class FrameReader:
    """Pull whole frames out of a byte stream, and find the sync word again if
    bytes are lost. A frame is sync, type, spare, seq, aux, length, payload and
    a 16 bit sum over everything after the sync word."""

    def __init__(self, port):
        self.port = port
        self.buffer = bytearray()
        self.resyncs = 0
        self.lost_bytes = 0
        self.seen = {}      # frame type -> how many arrived
        self.gaps = {}      # frame type -> packets missing by sequence number
        self._last_seq = {}

    def read(self) -> list[tuple[int, int, int, bytes]]:
        """One read from the port, then every whole frame it completed."""
        waiting = self.port.in_waiting
        data = self.port.read(waiting if waiting else 1)
        if data:
            self.buffer += data
        return self.drain()

    def drain(self) -> list[tuple[int, int, int, bytes]]:
        out = []
        while True:
            at = self.buffer.find(SYNC)
            if at < 0:
                # A sync word can straddle two reads, so keep the last 3 bytes.
                if len(self.buffer) > 3:
                    self.lost_bytes += len(self.buffer) - 3
                    del self.buffer[:-3]
                return out
            if at > 0:
                self.lost_bytes += at
                self.resyncs += 1
                del self.buffer[:at]
            if len(self.buffer) < HEADER_BYTES:
                return out

            kind = self.buffer[4]
            seq = int.from_bytes(self.buffer[6:8], "little")
            aux = int.from_bytes(self.buffer[8:10], "little")
            length = int.from_bytes(self.buffer[10:12], "little")
            if kind not in (FRAME_AUDIO, FRAME_JSON, FRAME_TEST) or length > MAX_PAYLOAD:
                self._skip_one()
                continue

            need = HEADER_BYTES + length + 2
            if len(self.buffer) < need:
                return out
            payload = bytes(self.buffer[HEADER_BYTES:HEADER_BYTES + length])
            got = int.from_bytes(self.buffer[need - 2:need], "little")
            want = (sum(self.buffer[4:HEADER_BYTES]) + sum(payload)) & 0xFFFF
            if got != want:
                self._skip_one()
                continue

            del self.buffer[:need]
            self._count(kind, seq)
            out.append((kind, seq, aux, payload))

    def _skip_one(self):
        self.resyncs += 1
        self.lost_bytes += 1
        del self.buffer[:1]

    def _count(self, kind: int, seq: int):
        self.seen[kind] = self.seen.get(kind, 0) + 1
        last = self._last_seq.get(kind)
        if last is not None:
            missing = (seq - last - 1) & 0xFFFF
            if missing:
                self.gaps[kind] = self.gaps.get(kind, 0) + missing
        self._last_seq[kind] = seq


# ------------------------------------------------------------------ grammar


WORD_SPLIT = re.compile(r"[^a-z']+")


def _words(text: str) -> list[str]:
    """Vosk words are lowercase letters and apostrophes, so split on the rest.

    Splitting rather than stripping matters: a canonical value written
    living_room is two spoken words, and stripping the underscore would invent
    one word nobody says.
    """
    return [w for w in WORD_SPLIT.split(str(text).lower()) if w]


def spec_vocabulary(spec, max_number: int = 180) -> list[str]:
    """Every word a sentence for this commands file could be made of.

    Example sentences, slot surface forms and canonical values, equivalent word
    groups, filler and droppable words, the out-of-scope sentences, and the
    English spelling of every number in range. No domain word is written here.
    """
    from tinycue.numbers import english_words
    from tinycue.parser import tokenize

    words: set[str] = set()

    def add(text):
        for token in tokenize(str(text)):
            words.update(_words(token))

    for command in spec.commands:
        for example in command.examples:
            for token in example.tokens:
                words.update(_words(token))

    for slot_type in spec.slot_types.values():
        for canonical, surface in slot_type.surfaces():
            add(canonical)
            add(surface)

    for group in spec.equivalents:
        for member in group:
            add(member)
    for filler in spec.fillers:
        add(filler)
    for word in spec.droppable:
        add(word)
    for sentence in spec.none_examples:
        add(sentence)

    for value in range(0, max_number + 1):
        spelled = english_words(value)
        if spelled:
            add(spelled)

    return sorted(words)


def spec_phrases(spec) -> list[str]:
    """Whole example sentences, to give the decoder sentence shape.

    A flat bag of words lets the decoder emit any sequence, and it then drops
    the short function words: "turn on the bedroom light" comes back as
    "bedroom light". The same sentences as phrases cost nothing and measured
    98% word recall against 55% for the bag alone.
    """
    out = set()
    for command in spec.commands:
        for example in command.examples:
            phrase = " ".join(_words(" ".join(example.tokens)))
            if phrase:
                out.add(phrase)
    for sentence in spec.none_examples:
        phrase = " ".join(_words(sentence))
        if phrase:
            out.add(phrase)
    return sorted(out)


def build_grammar(spec, model, quiet: bool = False) -> tuple[list[str], list[str]]:
    """The entries to hand Vosk, and the words it has never heard of.

    A word the acoustic model does not know cannot be put in the grammar, and a
    word outside the grammar can never come out of the recogniser. So the
    unknown ones are dropped here, loudly, rather than failing later. A phrase
    is dropped whole if any of its words is unknown.
    """
    wanted = spec_vocabulary(spec)
    seen: dict[str, bool] = {}

    def known(word: str) -> bool:
        if word not in seen:
            seen[word] = model.vosk_model_find_word(word) >= 0
        return seen[word]

    words = [w for w in wanted if known(w)]
    dropped = [w for w in wanted if not known(w)]
    phrases = [p for p in spec_phrases(spec) if all(known(w) for w in p.split())]

    if dropped and not quiet:
        print(f"vosk does not know {len(dropped)} of {len(wanted)} words, "
              f"so they can never be heard:", file=sys.stderr)
        print("  " + " ".join(dropped), file=sys.stderr)
    return phrases + words + ["[unk]"], dropped


# ------------------------------------------------------------------ sources


class BoardSource:
    """Audio from the FRDM-MCXN236, plus text in and JSON out on the same port."""

    name = "nxp"
    segmented = False

    def __init__(self, path: str, baud: int, settle: float):
        self.port = open_port(path, baud, settle)
        self.reader = FrameReader(self.port)
        self.replies: list[dict] = []
        self.overruns = 0
        self.path = path

    def _take(self, frames) -> list[bytes]:
        audio = []
        for kind, _seq, aux, payload in frames:
            if kind == FRAME_AUDIO:
                self.overruns = max(self.overruns, aux)
                audio.append(payload)
            elif kind == FRAME_JSON:
                try:
                    self.replies.append(json.loads(payload.decode("utf-8", "replace")))
                except json.JSONDecodeError:
                    pass
        return audio

    def chunks(self):
        while True:
            for payload in self._take(self.reader.read()):
                yield payload

    def pump(self):
        """Read without consuming audio, used while waiting for a JSON reply."""
        self._take(self.reader.read())

    def send_text(self, text: str):
        self.port.write((text + "\n").encode("utf-8"))
        self.port.flush()

    def wait_reply(self, timeout: float = 4.0) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.replies:
                return self.replies.pop(0)
            self.pump()
        return None

    def close(self):
        self.port.close()

    def stats(self) -> str:
        return (f"audio packets {self.reader.seen.get(FRAME_AUDIO, 0)}, "
                f"missing {self.reader.gaps.get(FRAME_AUDIO, 0)}, "
                f"resyncs {self.reader.resyncs}, board overruns {self.overruns}")


class MacSource:
    """The Mac's own microphone, for comparison and when no board is plugged in."""

    name = "mac"
    segmented = False

    def __init__(self, device=None):
        import queue

        import sounddevice

        self._queue: queue.Queue = queue.Queue()
        self._stream = sounddevice.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=CHUNK_SAMPLES,
            device=device,
            callback=lambda data, frames, t, status: self._queue.put(bytes(data)),
        )
        self._stream.start()
        self.device = self._stream.device

    def chunks(self):
        while True:
            yield self._queue.get()

    def pump(self):
        pass

    def send_text(self, text: str):
        pass

    def wait_reply(self, timeout: float = 4.0):
        return None

    def close(self):
        self._stream.stop()
        self._stream.close()

    def stats(self) -> str:
        return f"mac microphone, device {self.device}"


class WavSource:
    """WAV files fed through exactly the same recogniser, for repeatable tests.

    One file is one utterance, whatever the endpointer thinks of the pauses
    inside it, so the caller can line results up with what was said.
    """

    name = "wav"
    segmented = True

    def __init__(self, paths):
        self.paths = list(paths)
        self.current = None

    def chunks(self):
        for path in self.paths:
            self.current = path
            with wave.open(str(path), "rb") as w:
                if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1:
                    raise SystemExit(
                        f"{path}: need 16 kHz mono, got {w.getframerate()} Hz "
                        f"and {w.getnchannels()} channels")
                while True:
                    data = w.readframes(CHUNK_SAMPLES)
                    if not data:
                        break
                    yield data
            # A little digital silence to let the decoder settle, then a
            # marker: one file is one utterance, whatever the endpointer thinks.
            for _ in range(15):
                yield b"\x00\x00" * CHUNK_SAMPLES
            yield None

    def pump(self):
        pass

    def send_text(self, text: str):
        pass

    def wait_reply(self, timeout: float = 4.0):
        return None

    def close(self):
        pass

    def stats(self) -> str:
        return f"{len(self.paths)} wav files"


# --------------------------------------------------------------------- run


def save_wav(path: Path, audio: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio)


def find_port(pattern: str, what: str) -> str:
    found = sorted(glob.glob(pattern))
    if not found:
        raise SystemExit(f"no {what} at {pattern}, is the board plugged in?")
    return found[0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="speak to the tinycue boards")
    parser.add_argument("--mic-port", default=None,
                        help="the NXP board streaming audio")
    parser.add_argument("--mic-baud", type=int, default=1000000)
    parser.add_argument("--mic-gain", type=int, default=None,
                        help="set the board's fixed microphone gain, 1 to 64")
    parser.add_argument("--display-port", default=None,
                        help="the ESP32 with the round display")
    parser.add_argument("--display-baud", type=int, default=115200)
    parser.add_argument("--spec", default="examples/smart_home.yaml",
                        help="the commands file the vocabulary comes from")
    parser.add_argument("--model", default=".cache/vosk/vosk-model-small-en-in-0.4",
                        help="the Vosk model directory")
    parser.add_argument("--source", choices=("nxp", "mac"), default="nxp")
    parser.add_argument("--mac-device", default=None,
                        help="with --source mac, the input device name or number")
    parser.add_argument("--wav", nargs="+", default=None,
                        help="read these WAV files instead of a microphone")
    parser.add_argument("--no-display", action="store_true",
                        help="do not talk to the ESP32")
    parser.add_argument("--no-board-parse", action="store_true",
                        help="do not send the sentence back to the NXP board")
    parser.add_argument("--save-wav", default="out/voice/last.wav",
                        help="keep the last utterance here, '' to keep none")
    parser.add_argument("--seconds", type=float, default=None,
                        help="stop after this long")
    parser.add_argument("--settle", type=float, default=1.0,
                        help="seconds to wait after opening a port")
    parser.add_argument("--json", default=None, help="write one JSON line per utterance")
    args = parser.parse_args(argv)

    import vosk

    vosk.SetLogLevel(-1)

    from tinycue import load_spec

    model_path = Path(args.model)
    if not model_path.is_dir():
        raise SystemExit(f"no Vosk model at {model_path}, run 'make voice-model'")

    spec = load_spec(args.spec)
    model = vosk.Model(str(model_path))
    grammar, dropped = build_grammar(spec, model)
    phrases = sum(1 for g in grammar if " " in g)
    print(f"grammar: {len(grammar) - 1 - phrases} words and {phrases} example "
          f"sentences from {args.spec}, {len(dropped)} words dropped")

    recognizer = vosk.KaldiRecognizer(model, SAMPLE_RATE, json.dumps(grammar))
    recognizer.SetWords(True)

    if args.wav:
        paths = []
        for pattern in args.wav:
            paths.extend(sorted(glob.glob(pattern)) or [pattern])
        source = WavSource(paths)
    elif args.source == "mac":
        device = args.mac_device
        if device is not None and device.isdigit():
            device = int(device)
        source = MacSource(device)
        print(f"listening on the mac microphone, device {source.device}")
    else:
        path = args.mic_port or find_port("/dev/cu.usbmodem*", "NXP board")
        source = BoardSource(path, args.mic_baud, args.settle)
        if args.mic_gain:
            source.send_text(f"!gain {args.mic_gain}")
        print(f"listening on {path} at {args.mic_baud} baud")

    display = None
    if not args.no_display:
        path = args.display_port or find_port("/dev/cu.usbserial-*", "ESP32")
        display = open_port(path, args.display_baud, args.settle)
        # open_port waits 0.2 s a read, which would hide the real round trip.
        display.timeout = 0.02
        print(f"display on {path}")

    wav_path = Path(args.save_wav) if args.save_wav else None
    records = []
    pending: list[str] = []
    utterance = bytearray()
    partial_shown = ""
    started = time.time()
    status = 0

    print("speak a command. ctrl-c to stop.")
    try:
        for chunk in source.chunks():
            if args.seconds and time.time() - started > args.seconds:
                break

            # None means the source finished a segment, so finalise here rather
            # than waiting for the endpointer to notice the silence.
            if chunk is None:
                pending.append(json.loads(recognizer.FinalResult()).get("text", ""))
                heard = " ".join(p for p in pending if p)
                pending.clear()
            else:
                utterance += chunk
                if len(utterance) > SAMPLE_RATE * 2 * 20:
                    del utterance[:SAMPLE_RATE * 2]
                if not recognizer.AcceptWaveform(chunk):
                    text = json.loads(recognizer.PartialResult()).get("partial", "")
                    if text != partial_shown:
                        partial_shown = text
                        sys.stdout.write("\r  ... " + text[:90].ljust(90))
                        sys.stdout.flush()
                    continue
                heard = json.loads(recognizer.Result()).get("text", "")
                if source.segmented:
                    # A pause inside one file is not the end of the sentence.
                    pending.append(heard)
                    partial_shown = ""
                    continue

            final_at = time.time()
            partial_shown = ""
            sys.stdout.write("\r" + " " * 100 + "\r")
            words = [w for w in heard.split() if w != "[unk]"]
            if not words:
                utterance.clear()
                continue
            sentence = " ".join(words)

            if wav_path:
                save_wav(wav_path, bytes(utterance))
            utterance.clear()

            record = {"heard": sentence, "final_at": final_at}
            if isinstance(source, WavSource) and source.current:
                record["wav"] = str(source.current)
            if display is not None:
                display.write((sentence + "\n").encode("utf-8"))
                display.flush()
            if not args.no_board_parse:
                source.send_text(sentence)

            if display is not None:
                reply = read_reply(display)
                record["display"] = reply
                record["display_ms"] = round((time.time() - final_at) * 1000, 1)
            board = source.wait_reply(2.0) if not args.no_board_parse else None
            if board is not None:
                record["board"] = board

            record["answer_at"] = time.time()
            records.append(record)
            answer = record.get("display") or record.get("board") or {}
            slots = ", ".join(f"{k}={v}" for k, v in (answer.get("slots") or {}).items())
            print(f'heard "{sentence}"')
            print(f"  -> {answer.get('command', '?')}({slots}) "
                  f"confidence={answer.get('confidence', 0):.3f} "
                  f"unsure={answer.get('unsure')} "
                  f"in {record.get('display_ms', 0):.0f} ms")
    except KeyboardInterrupt:
        print()
    finally:
        sys.stdout.write("\r" + " " * 100 + "\r")
        print(source.stats())
        source.close()
        if display is not None:
            display.close()

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
            encoding="utf-8")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
