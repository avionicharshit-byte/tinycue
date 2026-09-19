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
from .calibrate import choose_cutoff, fit_slot_power, fit_temperature
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


def train(
    spec: Spec,
    out_dir,
    n_per_command: int = 1500,
    seed: int = 0,
    table_size: int = F.DEFAULT_BUCKETS,
    log=print,
) -> TrainResult:
    """Generate, split, fit both models, calibrate and save the bundle."""
    started = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    examples = generate(spec, n_per_command=n_per_command, seed=seed)
    split = split_by_frame(examples, seed=seed)
    log(
        f"generated {len(examples)} examples: "
        f"{len(split.train)} train, {len(split.dev)} dev, {len(split.test)} test"
    )

    classes = spec.command_names()
    weights, bias = train_intent(split.train, classes, table_size, seed=seed)
    train_slots(split.train, spec, out_dir / bundle.SLOTS_FILE, seed=seed)

    temperature = 1.0
    cutoff = None
    if split.dev:
        scores = _scores(split.dev, weights, bias, table_size)
        index = {name: i for i, name in enumerate(classes)}
        labels = np.array([index[e.command] for e in split.dev])
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
        extra={"training": {"n_per_command": n_per_command, "seed": seed}},
    )

    loaded = bundle.load(out_dir)
    slot_power = 1.0
    if split.dev:
        intent_probabilities, slot_probabilities, correct = _dev_parts(loaded, split.dev, spec)
        slot_power = fit_slot_power(intent_probabilities, slot_probabilities, correct)
        log(f"tagger probability power: {slot_power:.3f}")
        confidences = [
            i * (s ** slot_power) for i, s in zip(intent_probabilities, slot_probabilities)
        ]
        cutoff = choose_cutoff(confidences, correct)
        if isinstance(spec.fallback.unsure_below, float):
            chosen = float(spec.fallback.unsure_below)
            log(f"cut-off pinned by the commands file: {chosen:.3f}")
        else:
            chosen = cutoff.value
            log(
                f"cut-off chosen on dev: {chosen:.3f}, "
                f"{cutoff.unsure_rate * 100:.1f}% of inputs go to unsure, "
                f"{cutoff.wrong_caught * 100:.1f}% of wrong answers caught, "
                f"accepted answers right {cutoff.accepted_accuracy * 100:.1f}% of the time"
            )
            if not cutoff.reached_target:
                log("warning: no cut-off reached 99% on dev, so everything goes to unsure")
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
            extra={"training": {"n_per_command": n_per_command, "seed": seed}},
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
    )


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
