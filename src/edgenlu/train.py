"""Train the two models and write the bundle.

The intent model is multinomial logistic regression with L2 over the hashed features.
The slot model is a linear chain CRF trained by CRFsuite with lbfgs.

The generated data is split 80/10/10 by FRAME, not by sentence. A frame is one of the
hand-written sentences, so every sentence in dev and test grew from a phrasing the trained
model never saw. Splitting by sentence would put near copies on both sides and the numbers
would be flattering nonsense.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import features as F
from . import model as bundle
from .calibrate import (
    TARGET_ACCURACY,
    Cutoff,
    choose_cutoff,
    cutoff_table,
    fit_slot_power,
    fit_temperature,
)
from .decode import decode
from .generator import generate
from .schema import NONE_COMMAND, Example, Spec

C1 = 0.1
C2 = 0.1
MAX_CRF_ITERATIONS = 100
L2_STRENGTH = 1.0
MAX_INTENT_ITERATIONS = 400


@dataclass
class Split:
    """The three parts of the generated data."""

    train: list[Example] = field(default_factory=list)
    dev: list[Example] = field(default_factory=list)
    test: list[Example] = field(default_factory=list)


def split_by_frame(examples, seed: int = 0) -> Split:
    """Hold out whole phrasings, a tenth for dev and a tenth for test, per command."""
    rng = random.Random(seed)
    frames: dict[str, list[str]] = {}
    for example in examples:
        frames.setdefault(example.command, [])
        if example.frame not in frames[example.command]:
            frames[example.command].append(example.frame)

    dev_frames: set[str] = set()
    test_frames: set[str] = set()
    for command, names in frames.items():
        names = sorted(names)
        rng.shuffle(names)
        total = len(names)
        if total < 6:
            continue
        # A tenth, but never fewer than two phrasings. With a dozen hand-written
        # sentences one held-out phrasing is pure luck, and calibrating on luck gives
        # a cut-off that means nothing.
        held = max(2, round(total * 0.1))
        test_frames.update(names[:held])
        dev_frames.update(names[held : held * 2])

    out = Split()
    for example in examples:
        if example.frame in test_frames:
            out.test.append(example)
        elif example.frame in dev_frames:
            out.dev.append(example)
        else:
            out.train.append(example)
    return out


def train_intent(
    examples, classes: list[str], table_size: int, seed: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """Fit the linear intent model and hand back its weights as one row per class."""
    from sklearn.linear_model import LogisticRegression

    index = {name: i for i, name in enumerate(classes)}
    matrix = F.intent_matrix([e.tokens for e in examples], table_size)
    labels = np.array([index[e.command] for e in examples])

    # The default penalty is already L2. Naming it triggers a deprecation warning.
    classifier = LogisticRegression(
        C=L2_STRENGTH,
        solver="lbfgs",
        max_iter=MAX_INTENT_ITERATIONS,
        random_state=seed,
    )
    classifier.fit(matrix, labels)

    weights = np.zeros((len(classes), table_size), dtype=np.float64)
    bias = np.zeros(len(classes), dtype=np.float64)
    present = list(classifier.classes_)
    raw = np.atleast_2d(classifier.coef_)
    raw_bias = np.atleast_1d(classifier.intercept_)
    if len(present) == 2 and raw.shape[0] == 1:
        # Two classes come back as one row. Spread it over both.
        weights[present[1]] = raw[0]
        bias[present[1]] = raw_bias[0]
        weights[present[0]] = -raw[0]
        bias[present[0]] = -raw_bias[0]
    else:
        for row, label in enumerate(present):
            weights[label] = raw[row]
            bias[label] = raw_bias[row]
    return weights, bias


def train_slots(examples, spec: Spec, path, seed: int = 0) -> None:
    """Fit the CRF tagger and write it where CRFsuite wants it."""
    import pycrfsuite

    gaz = F.gazetteer(spec)
    rng = random.Random(seed)
    trainer = pycrfsuite.Trainer(verbose=False)
    for example in examples:
        items = F.sentence_features(example.tokens, gaz, rng, F.GAZETTEER_DROPOUT)
        trainer.append(items, list(example.tags))
    trainer.select("lbfgs", "crf1d")
    trainer.set_params(
        {
            "c1": C1,
            "c2": C2,
            "max_iterations": MAX_CRF_ITERATIONS,
            "feature.possible_transitions": True,
        }
    )
    trainer.train(str(path))


@dataclass
class TrainResult:
    """What one training run produced."""

    directory: Path
    split: Split
    classes: list[str]
    temperature: float
    slot_power: float
    cutoff: object
    seconds: float
    examples: int
    # Every word the two models were actually shown. Used to spot unknown words.
    vocabulary: set = field(default_factory=set)
    # What each cut-off on a grid would have done to the calibration set.
    trade_off: list = field(default_factory=list)
    # True when the cut-off was fitted on a hand-written dev file, not the generated split.
    hand_written_dev: bool = False
    target: float = TARGET_ACCURACY


def train(
    spec: Spec,
    out_dir,
    n_per_command: int = 1500,
    seed: int = 0,
    table_size: int = F.DEFAULT_BUCKETS,
    log=print,
    dev_examples=None,
    target: float = TARGET_ACCURACY,
) -> TrainResult:
    """Generate, split, fit both models, calibrate and save the bundle.

    With `dev_examples`, the temperature, the tagger power and the cut-off are fitted on
    that hand-written set instead of the generated dev split, and the generated dev
    frames go back into training. Nothing in `dev_examples` is ever trained on.
    """
    started = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    examples = generate(spec, n_per_command=n_per_command, seed=seed)
    split = split_by_frame(examples, seed=seed)
    hand_written = list(dev_examples or [])

    if hand_written:
        training = split.train + split.dev
        calibration = hand_written
        log(
            f"generated {len(examples)} examples: {len(training)} train, "
            f"{len(split.test)} test, calibrating on {len(calibration)} hand-written sentences"
        )
    else:
        training = split.train
        calibration = split.dev
        log(
            f"generated {len(examples)} examples: "
            f"{len(split.train)} train, {len(split.dev)} dev, {len(split.test)} test"
        )

    classes = spec.command_names()
    weights, bias = train_intent(training, classes, table_size, seed=seed)
    train_slots(training, spec, out_dir / bundle.SLOTS_FILE, seed=seed)

    meta_extra = {
        "training": {
            "n_per_command": n_per_command,
            "seed": seed,
            "extra_files": list(spec.extra_files),
            "dev_sentences": len(hand_written),
            "target_accepted_accuracy": float(target),
        }
    }

    temperature = 1.0
    cutoff = None
    trade_off: list[Cutoff] = []
    if calibration:
        scores = _scores(calibration, weights, bias, table_size)
        index = {name: i for i, name in enumerate(classes)}
        labels = np.array([index[e.command] for e in calibration])
        temperature = fit_temperature(scores, labels)
        log(f"temperature: {temperature:.3f}")

    bundle.save(
        out_dir,
        spec,
        classes,
        weights,
        bias,
        temperature,
        1.0,
        table_size,
        unsure_below=0.0,
        extra=meta_extra,
    )

    loaded = bundle.load(out_dir)
    slot_power = 1.0
    if calibration:
        intent_probabilities, slot_probabilities, correct = _dev_parts(loaded, calibration, spec)
        slot_power = fit_slot_power(intent_probabilities, slot_probabilities, correct)
        log(f"tagger probability power: {slot_power:.3f}")
        confidences = [
            i * (s ** slot_power) for i, s in zip(intent_probabilities, slot_probabilities)
        ]
        cutoff = choose_cutoff(confidences, correct, target=target)
        trade_off = cutoff_table(confidences, correct)
        where = "hand-written dev" if hand_written else "the generated dev split"
        if isinstance(spec.fallback.unsure_below, float):
            chosen = float(spec.fallback.unsure_below)
            log(f"cut-off pinned by the commands file: {chosen:.3f}")
        else:
            chosen = cutoff.value
            log(
                f"cut-off chosen on {where}: {chosen:.3f}, "
                f"{cutoff.unsure_rate * 100:.1f}% of inputs go to unsure, "
                f"{cutoff.wrong_caught * 100:.1f}% of wrong answers caught, "
                f"accepted answers right {cutoff.accepted_accuracy * 100:.1f}% of the time"
            )
            if not cutoff.reached_target:
                log(
                    f"warning: no cut-off reached {target * 100:.0f}% on {where}, "
                    "so everything goes to unsure"
                )
        for row in _trade_off_lines(trade_off, chosen, target):
            log(row)
        bundle.save(
            out_dir,
            spec,
            classes,
            weights,
            bias,
            temperature,
            slot_power,
            table_size,
            unsure_below=chosen,
            extra=meta_extra,
        )

    seconds = time.time() - started
    return TrainResult(
        directory=out_dir,
        split=split,
        classes=classes,
        temperature=temperature,
        slot_power=slot_power,
        cutoff=cutoff,
        seconds=seconds,
        examples=len(examples),
        vocabulary={token for e in training for token in e.tokens},
        trade_off=trade_off,
        hand_written_dev=bool(hand_written),
        target=float(target),
    )


def _trade_off_lines(rows, chosen: float, target: float) -> list[str]:
    """The cut-off trade-off as printable lines, so the choice can be seen."""
    if not rows:
        return []
    out = [
        f"cut-off trade-off (target {target * 100:.0f}% of accepted answers right)",
        "  cut-off   to unsure   accepted right   wrong caught",
    ]
    for row in rows:
        mark = " <- chosen" if abs(row.value - chosen) < 1e-9 else ""
        out.append(
            f"  {row.value:7.2f}   {row.unsure_rate * 100:8.1f}%   "
            f"{row.accepted_accuracy * 100:13.1f}%   {row.wrong_caught * 100:11.1f}%{mark}"
        )
    if not any(abs(row.value - chosen) < 1e-9 for row in rows):
        out.append(f"  chosen cut-off {chosen:.3f} sits between two rows of this table")
    return out


def _scores(examples, weights, bias, table_size) -> np.ndarray:
    matrix = F.intent_matrix([e.tokens for e in examples], table_size)
    return np.asarray(matrix @ weights.T) + bias


def _dev_parts(loaded: bundle.Model, examples, spec: Spec):
    """The two halves of the confidence on dev, and whether each answer was right."""
    intent_probabilities = []
    slot_probabilities = []
    correct = []
    for example in examples:
        probabilities = loaded.intent_probabilities(example.tokens)
        best = int(np.argmax(probabilities))
        command = loaded.classes[best]
        tags, slot_probability = loaded.tag(example.tokens)
        result = decode(example.tokens, tags, command, spec)
        intent_probabilities.append(float(probabilities[best]))
        slot_probabilities.append(slot_probability)
        correct.append(command == example.command and result.slots == dict(example.slots))
    return intent_probabilities, slot_probabilities, correct


def write_jsonl(examples, path) -> int:
    """Write a split out so `edgenlu eval` can read it back."""
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e.as_dict(), ensure_ascii=False) for e in examples]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def read_jsonl(path) -> list[Example]:
    """Read a split back in."""
    import json

    out: list[Example] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        out.append(
            Example(
                tokens=record["tokens"],
                tags=record["tags"],
                command=record.get("command", NONE_COMMAND),
                slots=record.get("slots", {}),
                text=" ".join(record["tokens"]),
            )
        )
    return out
