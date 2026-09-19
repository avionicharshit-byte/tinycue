"""Write a trained bundle out as one flat little-endian blob the C runtime reads in place.

`docs/model-format.md` says the same thing in prose. Change the two together.

The blob holds everything a device needs: the intent weights, the CRF the tagger learned,
the parts of the commands file decoding needs, and the number word tables. Nothing is
compressed and nothing needs unpacking, so the runtime can point straight at flash.

Intent weights are stored as IEEE 754 binary16. Measured on both example bundles, that
moves the reported confidence by at most 2.3e-4 and flips no decision. int8 with a per
class scale halves the size again but moves confidence by up to 8.1e-3, which is over the
1e-3 bar the parity test holds the runtime to.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from . import features as F
from .model import Model
from .numbers import FILLER_WORDS, NUMBER_WORDS
from .schema import NONE_COMMAND, NUMBER, OUTSIDE

MAGIC = b"TCUE"
BLOB_VERSION = 2
HEADER_SIZE = 32
DIRECTORY_ENTRY = 16
SECTION_ALIGN = 8

SECTION_STRINGS = 1
SECTION_INTENT = 2
SECTION_CRF = 3
SECTION_SPEC = 4
SECTION_NUMBERS = 5
SECTION_GATE = 6
SECTION_IDS = (
    SECTION_STRINGS,
    SECTION_INTENT,
    SECTION_CRF,
    SECTION_SPEC,
    SECTION_NUMBERS,
    SECTION_GATE,
)

WEIGHT_FLOAT16 = 1
KIND_VALUES = 0
KIND_NUMBER = 1
NO_INDEX = 0xFFFFFFFF

FNV64_OFFSET = 0xCBF29CE484222325
FNV64_PRIME = 0x100000001B3
MASK64 = 0xFFFFFFFFFFFFFFFF

BLOB_FILE = "model.bin"
HEADER_FILE = "model_data.h"
SOURCE_FILE = "model_data.c"


class ExportError(RuntimeError):
    """The bundle cannot be written out. The message says what stopped it."""


def fnv1a64(text) -> int:
    """FNV-1a over the UTF-8 bytes of a string, 64 bit. The C runtime does the same."""
    if isinstance(text, str):
        text = text.encode("utf-8")
    h = FNV64_OFFSET
    for byte in text:
        h = ((h ^ byte) * FNV64_PRIME) & MASK64
    return h


class Strings:
    """One pool of NUL terminated strings. Offset 0 is always the empty string."""

    def __init__(self) -> None:
        self.data = bytearray(b"\x00")
        self.seen: dict[bytes, int] = {b"": 0}

    def add(self, text) -> int:
        raw = str(text).encode("utf-8")
        if b"\x00" in raw:
            raise ExportError(f"a name contains a zero byte: {text!r}")
        if raw in self.seen:
            return self.seen[raw]
        offset = len(self.data)
        self.data.extend(raw)
        self.data.append(0)
        self.seen[raw] = offset
        return offset


class Section:
    """A section being built: arrays are placed in turn and their offsets handed back."""

    def __init__(self, head_size: int) -> None:
        self.head = bytearray(head_size)
        self.body = bytearray()
        self.head_size = head_size

    def place(self, data: bytes, align: int = 4) -> int:
        """Append an array and return where it sits, counted from the section start."""
        start = self.head_size + len(self.body)
        padding = (-start) % align
        self.body.extend(b"\x00" * padding)
        start += padding
        self.body.extend(data)
        return start

    def set_head(self, offset: int, value: int) -> None:
        struct.pack_into("<I", self.head, offset, value)

    def set_head_float(self, offset: int, value: float) -> None:
        struct.pack_into("<f", self.head, offset, float(value))

    def bytes(self) -> bytes:
        return bytes(self.head) + bytes(self.body)


def _u32_array(values) -> bytes:
    return np.asarray(list(values), dtype="<u4").tobytes()


def _f32_array(values) -> bytes:
    return np.asarray(list(values), dtype="<f4").tobytes()


def _intent_section(model: Model, strings: Strings) -> bytes:
    section = Section(32)
    weights = np.asarray(model.weights, dtype=np.float32).astype(np.float16)
    names = _u32_array(strings.add(c) for c in model.classes)
    bias = _f32_array(model.bias)
    packed = weights.astype("<f2").tobytes()

    section.set_head(0, model.table_size)
    section.set_head(4, len(model.classes))
    section.set_head_float(8, model.temperature)
    section.set_head(12, section.place(names))
    section.set_head(16, section.place(bias))
    section.set_head(20, section.place(packed))
    section.set_head(24, WEIGHT_FLOAT16)
    return section.bytes()


def crf_tables(model: Model) -> dict:
    """Everything the tagger knows, pulled out of CRFsuite and checked for collisions."""
    info = model.tagger.info()
    labels = sorted(info.labels, key=lambda name: int(info.labels[name]))
    label_index = {name: i for i, name in enumerate(labels)}

    attrs = sorted({attr for attr, _ in info.state_features})
    hashes = [fnv1a64(a) for a in attrs]
    clashes: dict[int, list[str]] = {}
    for attr, value in zip(attrs, hashes):
        clashes.setdefault(value, []).append(attr)
    collided = {v: names for v, names in clashes.items() if len(names) > 1}
    if collided:
        first = sorted(collided.items())[0]
        raise ExportError(
            f"two tagger features hash to the same 64 bit value: {first[1]}. "
            "The blob cannot tell them apart, so nothing was written."
        )

    grouped: dict[str, list[tuple[int, float]]] = {a: [] for a in attrs}
    for (attr, label), weight in info.state_features.items():
        grouped[attr].append((label_index[label], float(weight)))

    order = sorted(range(len(attrs)), key=lambda i: hashes[i])
    starts = [0]
    feature_labels: list[int] = []
    feature_weights: list[float] = []
    for i in order:
        for label, weight in sorted(grouped[attrs[i]]):
            feature_labels.append(label)
            feature_weights.append(weight)
        starts.append(len(feature_labels))

    count = len(labels)
    transitions = np.zeros((count, count), dtype=np.float32)
    for (source, target), weight in info.transitions.items():
        transitions[label_index[source], label_index[target]] = float(weight)

    return {
        "labels": labels,
        "hashes": [hashes[i] for i in order],
        "starts": starts,
        "feature_labels": feature_labels,
        "feature_weights": feature_weights,
        "transitions": transitions,
        "attributes": len(attrs),
    }


def _crf_section(tables: dict, strings: Strings) -> bytes:
    section = Section(40)
    labels = tables["labels"]
    section.set_head(0, len(labels))
    section.set_head(4, tables["attributes"])
    section.set_head(8, len(tables["feature_labels"]))
    section.set_head(12, section.place(_u32_array(strings.add(n) for n in labels)))
    section.set_head(16, section.place(tables["transitions"].astype("<f4").tobytes()))
    section.set_head(
        20, section.place(np.asarray(tables["hashes"], dtype="<u8").tobytes(), align=8)
    )
    section.set_head(24, section.place(_u32_array(tables["starts"])))
    section.set_head(
        28, section.place(np.asarray(tables["feature_labels"], dtype="<u2").tobytes(), align=2)
    )
    section.set_head(32, section.place(_f32_array(tables["feature_weights"])))
    return section.bytes()


def _spec_section(model: Model, labels: list[str], strings: Strings) -> bytes:
    spec = model.spec
    type_names = sorted(spec.slot_types)
    type_index = {name: i for i, name in enumerate(type_names)}
    gaz = F.gazetteer(spec)

    values = bytearray()
    forms = bytearray()
    gaz_words = bytearray()
    slot_types = bytearray()
    value_count = form_count = gaz_count = 0

    for name in type_names:
        slot_type = spec.slot_types[name]
        bounds = 0
        low = high = 0
        if slot_type.kind == NUMBER:
            if slot_type.min is not None:
                bounds |= 1
                low = int(slot_type.min)
            if slot_type.max is not None:
                bounds |= 2
                high = int(slot_type.max)
            kind = KIND_NUMBER
        else:
            kind = KIND_VALUES

        value_start = value_count
        if kind == KIND_VALUES:
            for canonical, surfaces in slot_type.values.items():
                form_start = form_count
                for surface in surfaces:
                    forms.extend(struct.pack("<I", strings.add(surface)))
                    form_count += 1
                values.extend(
                    struct.pack("<III", strings.add(canonical), len(surfaces), form_start)
                )
                value_count += 1

        gaz_start = gaz_count
        for word in sorted(gaz.get(name, ())):
            gaz_words.extend(struct.pack("<I", strings.add(word)))
            gaz_count += 1

        slot_types.extend(
            struct.pack(
                "<IIiiIIIIII",
                strings.add(name),
                kind,
                low,
                high,
                bounds,
                value_count - value_start,
                value_start,
                gaz_count - gaz_start,
                gaz_start,
                0,
            )
        )

    commands = bytearray()
    command_slots = bytearray()
    slot_count = 0
    for command in spec.commands:
        start = slot_count
        for slot in command.slots:
            command_slots.extend(
                struct.pack(
                    "<III",
                    strings.add(slot.name),
                    type_index[slot.type],
                    1 if slot.required else 0,
                )
            )
            slot_count += 1
        commands.extend(
            struct.pack("<IIII", strings.add(command.name), len(command.slots), start, 0)
        )

    order = {c.name: i for i, c in enumerate(spec.commands)}
    class_command = [order.get(name, NO_INDEX) for name in model.classes]

    label_type = []
    label_begin = bytearray()
    for label in labels:
        if label == OUTSIDE or len(label) < 3 or label[1] != "-":
            label_type.append(NO_INDEX)
            label_begin.append(0)
            continue
        label_type.append(type_index.get(label[2:], NO_INDEX))
        label_begin.append(1 if label.startswith("B-") else 0)

    section = Section(56)
    section.set_head(0, len(spec.commands))
    section.set_head(4, len(type_names))
    section.set_head_float(8, model.unsure_below)
    section.set_head_float(12, model.slot_power)
    section.set_head(16, section.place(bytes(commands)))
    section.set_head(20, section.place(bytes(command_slots)))
    section.set_head(24, section.place(bytes(slot_types)))
    section.set_head(28, section.place(bytes(values)))
    section.set_head(32, section.place(bytes(forms)))
    section.set_head(36, section.place(bytes(gaz_words)))
    section.set_head(40, section.place(_u32_array(class_command)))
    section.set_head(44, section.place(_u32_array(label_type)))
    section.set_head(48, section.place(bytes(label_begin), align=1))
    return section.bytes()


def _numbers_section(strings: Strings) -> bytes:
    words = sorted(NUMBER_WORDS)
    fillers = sorted(FILLER_WORDS)
    table = bytearray()
    for word in words:
        table.extend(struct.pack("<Ii", strings.add(word), int(NUMBER_WORDS[word])))

    section = Section(16)
    section.set_head(0, len(words))
    section.set_head(4, len(fillers))
    section.set_head(8, section.place(bytes(table)))
    section.set_head(12, section.place(_u32_array(strings.add(w) for w in fillers)))
    return section.bytes()


def _gate_section(model: Model) -> bytes:
    """The training vocabulary and the gate weights, the two things the cut-off needs.

    The vocabulary is a sorted array of 32 bit hashes, not words, so the device can say
    "the model has never seen this word" with a binary search and a few kilobytes. The
    weights are a handful of floats and an id each, so a model may use any subset of the
    signals and an older runtime still reads it.
    """
    from . import gate

    hashes = sorted(set(int(h) & 0xFFFFFFFF for h in model.vocabulary))
    calibrator = model.calibrator
    ids = list(calibrator.feature_ids) if calibrator is not None else []
    weights = list(calibrator.weights) if calibrator is not None else []
    bias = float(calibrator.bias) if calibrator is not None else 0.0
    if any(i < 0 or i >= gate.FEATURE_COUNT for i in ids):
        raise ExportError(f"a gate weight names a signal this format has no slot for: {ids}")

    section = Section(32)
    section.set_head(0, len(hashes))
    section.set_head(4, section.place(_u32_array(hashes)))
    section.set_head(8, len(weights))
    section.set_head(12, section.place(_u32_array(ids)))
    section.set_head(16, section.place(_f32_array(weights)))
    section.set_head_float(20, bias)
    section.set_head(24, gate.FEATURE_COUNT)
    return section.bytes()


def build_blob(model: Model) -> bytes:
    """The whole model as one byte string, ready for flash."""
    strings = Strings()
    tables = crf_tables(model)
    # The string pool is written last because every other section fills it.
    intent = _intent_section(model, strings)
    crf = _crf_section(tables, strings)
    spec = _spec_section(model, tables["labels"], strings)
    numbers = _numbers_section(strings)
    gate_body = _gate_section(model)
    pool = bytes(strings.data)

    bodies = {
        SECTION_STRINGS: pool,
        SECTION_INTENT: intent,
        SECTION_CRF: crf,
        SECTION_SPEC: spec,
        SECTION_NUMBERS: numbers,
        SECTION_GATE: gate_body,
    }

    out = bytearray()
    out.extend(MAGIC)
    out.extend(struct.pack("<IIIIIII", BLOB_VERSION, 0, len(SECTION_IDS), 0, 0, 0, 0))
    directory = len(out)
    out.extend(b"\x00" * (DIRECTORY_ENTRY * len(SECTION_IDS)))

    for slot, identifier in enumerate(SECTION_IDS):
        out.extend(b"\x00" * ((-len(out)) % SECTION_ALIGN))
        offset = len(out)
        body = bodies[identifier]
        out.extend(body)
        struct.pack_into(
            "<IIII", out, directory + slot * DIRECTORY_ENTRY, identifier, offset, len(body), 0
        )
    struct.pack_into("<I", out, 8, len(out))
    return bytes(out)


def c_source(blob: bytes, symbol: str = "tcue_model_data") -> tuple[str, str]:
    """The blob as a C header and a C file, so it can be linked straight into flash."""
    guard = symbol.upper() + "_H"
    header = (
        "/* Generated by tinycue export. Do not edit. */\n"
        f"#ifndef {guard}\n#define {guard}\n\n"
        "#include <stddef.h>\n\n"
        '#ifdef __cplusplus\nextern "C" {\n#endif\n\n'
        f"extern const unsigned char {symbol}[];\n"
        f"extern const size_t {symbol}_len;\n\n"
        "#ifdef __cplusplus\n}\n#endif\n\n"
        f"#endif /* {guard} */\n"
    )

    lines = []
    for start in range(0, len(blob), 16):
        chunk = blob[start : start + 16]
        lines.append("  " + "".join(f"0x{byte:02x}," for byte in chunk))
    body = "\n".join(lines)
    source = (
        "/* Generated by tinycue export. Do not edit. */\n"
        f'#include "{HEADER_FILE}"\n\n'
        "/* Aligned to 8 bytes: the runtime reads 32 and 64 bit fields straight out of it,\n"
        "   and on some devices an unaligned read of flash faults. */\n"
        f"const unsigned char {symbol}[] __attribute__((aligned(8))) = {{\n{body}\n}};\n\n"
        f"const size_t {symbol}_len = sizeof({symbol});\n"
    )
    return header, source


def export(model_dir, out_dir) -> dict:
    """Read a bundle and write model.bin, model_data.h and model_data.c next to each other."""
    from . import model as bundle

    loaded = bundle.load(model_dir)
    blob = build_blob(loaded)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / BLOB_FILE).write_bytes(blob)
    header, source = c_source(blob)
    (out / HEADER_FILE).write_text(header, encoding="utf-8")
    (out / SOURCE_FILE).write_text(source, encoding="utf-8")

    return {
        "bytes": len(blob),
        "classes": len(loaded.classes),
        "table_size": loaded.table_size,
        "labels": len(loaded.spec.tag_set()),
        "vocabulary": len(set(loaded.vocabulary)),
        "gate_weights": len(loaded.calibrator.weights) if loaded.calibrator else 0,
        "path": out / BLOB_FILE,
    }


def dequantised_weights(model: Model) -> np.ndarray:
    """The intent weights as the device will see them, for an apples to apples comparison."""
    return np.asarray(model.weights, dtype=np.float32).astype(np.float16).astype(np.float64)


def none_class(classes) -> int:
    """Where the 'no command' class sits, or -1."""
    return classes.index(NONE_COMMAND) if NONE_COMMAND in classes else -1
