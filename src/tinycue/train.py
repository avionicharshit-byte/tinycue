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
from . import gate
from . import model as bundle
from . import stress as stress_data
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

# How many roughed up copies of each dev sentence the gate is fitted on, and how many
# generated training sentences get one copy each. Both are there so the calibrator sees
# words the model has never met; see stress.py.
STRESS_COPIES = 3
STRESS_GENERATED = 300
# Folds for the cross-validation that picks the cut-off. Rows are grouped by the sentence
# they came from, so a copy never sits in a different fold from its original.
GATE_FOLDS = 5


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
    # The gate weights that went into the bundle, or None when the old product was kept.
    calibrator: object = None
    # Plain numbers about how well the gate holds up, for the doctor report.
    gate_health: dict = field(default_factory=dict)


def train(
    spec: Spec,
    out_dir,
    n_per_command: int = 1500,
    seed: int = 0,
    table_size: int = F.DEFAULT_BUCKETS,
    log=print,
    dev_examples=None,
    target: float = TARGET_ACCURACY,
    gate_features=gate.CORE_FEATURES,
    stress_copies: int = STRESS_COPIES,
    stress_generated: int = STRESS_GENERATED,
    folds: int = GATE_FOLDS,
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
        # A hand-written dev set is a better holdout than any slice of generated data, so
        # nothing generated is held back: every sentence goes into training.
        split = Split(train=list(examples))
        training = split.train
        calibration = hand_written
        log(
            f"generated {len(examples)} training examples, calibrating on "
            f"{len(calibration)} hand-written sentences"
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

    words = {token for e in training for token in e.tokens}
    vocabulary = gate.hash_words(words)

    def write(power: float, cut: float, calibrator=None) -> None:
        bundle.save(
            out_dir,
            spec,
            classes,
            weights,
            bias,
            temperature,
            power,
            table_size,
            unsure_below=cut,
            extra=meta_extra,
            vocabulary=vocabulary,
            calibrator=calibrator,
        )

    write(1.0, 0.0)
    loaded = bundle.load(out_dir)
    slot_power = 1.0
    calibrator = None
    health: dict = {}
    if calibration:
        intent_probabilities, slot_probabilities, correct = _dev_parts(loaded, calibration, spec)
        slot_power = fit_slot_power(intent_probabilities, slot_probabilities, correct)
        log(f"tagger probability power: {slot_power:.3f}")
        write(slot_power, 0.0)
        loaded = bundle.load(out_dir)

        fit_set = _gate_set(
            calibration, training, words, seed, stress_copies, stress_generated
        )
        vectors, labels, groups, kinds = _gate_rows(loaded, fit_set)
        if gate_features:
            calibrator = gate.fit(vectors, labels, feature_ids=list(gate_features))
        if calibrator is not None:
            scores = gate.out_of_fold(
                vectors, labels, groups, feature_ids=list(gate_features), folds=folds
            )
            fitted_on = (
                f"{len(calibration)} dev sentences and "
                f"{len(fit_set) - len(calibration)} roughed up copies"
            )
            log(
                f"gate: {len(calibrator.weights)} weights over "
                + ", ".join(gate.FEATURE_NAMES[i] for i in calibrator.feature_ids)
                + f", fitted on {fitted_on}"
            )
        else:
            scores = [
                i * (s ** slot_power) for i, s in zip(intent_probabilities, slot_probabilities)
            ]
            labels = list(correct)
            kinds = ["dev"] * len(labels)
            log("gate: no calibrator, the confidence is the old product")

        cutoff = choose_cutoff(scores, labels, target=target)
        trade_off = cutoff_table(scores, labels)
        where = "hand-written dev" if hand_written else "the generated dev split"
        if calibrator is not None:
            where += " plus the roughed up copies, out of fold"
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
        health = _gate_health(scores, labels, kinds, chosen)
        write(slot_power, chosen, calibrator)

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
        vocabulary=words,
        trade_off=trade_off,
        hand_written_dev=bool(hand_written),
        target=float(target),
        calibrator=calibrator,
        gate_health=health,
    )


def _gate_set(calibration, training, words, seed, copies, generated):
    """The sentences the gate is fitted on: the dev set and roughed up copies of it.

    Copies of a sample of the generated training sentences come too. The model has seen
    those, so on their own they teach nothing, but once a carrier word is a word nobody
    has ever written they are exactly the case the gate exists for.
    """
    out = list(calibration)
    if copies > 0:
        out.extend(stress_data.stress(calibration, words, seed=seed, copies=copies))
    if generated > 0 and training:
        rng = random.Random(seed + 7)
        sample = training if len(training) <= generated else rng.sample(training, generated)
        out.extend(stress_data.stress(sample, words, seed=seed + 1, copies=1))
    return out


def _gate_rows(loaded: bundle.Model, examples):
    """Feature vectors, whether each answer was right, its group and where it came from."""
    vectors = []
    labels = []
    groups = []
    kinds = []
    for example in examples:
        reading = loaded.read(example.tokens)
        right = reading.command == example.command and reading.decoded.slots == dict(
            example.slots
        )
        vectors.append(gate.feature_vector(reading.signals))
        labels.append(bool(right))
        groups.append(example.frame or " ".join(example.tokens))
        kinds.append("dev" if example.labelled else "stress")
    return vectors, labels, groups, kinds


def _gate_health(scores, labels, kinds, cut: float) -> dict:
    """How often an accepted answer is right, on plain sentences and on roughed up ones."""

    def measure(wanted: str) -> dict:
        picked = [i for i, kind in enumerate(kinds) if kind == wanted]
        accepted = [i for i in picked if scores[i] >= cut]
        return {
            "count": len(picked),
            "accepted": len(accepted),
            "accepted_accuracy": (
                sum(1 for i in accepted if labels[i]) / len(accepted) if accepted else None
            ),
        }

    return {"dev": measure("dev"), "stress": measure("stress")}


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
    """Write a split out so `tinycue eval` can read it back."""
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
