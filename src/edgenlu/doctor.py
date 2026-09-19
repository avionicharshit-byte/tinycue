"""Say, in plain words, where a trained model is weak and what to write next.

Everything here is measured on a hand-written dev set, never on a final test set. The
report is meant to be read by someone who has never seen the code, and the same numbers
come out as JSON so an agent can act on them without parsing prose.

The most useful line is nearly always "unknown words": a word in a failing sentence that
appears nowhere in the training data cannot help the model, because the features are
hashed word and character n-grams with no idea that two words are related.
"""

from __future__ import annotations

from collections import Counter

from . import evaluate
from .schema import NONE_COMMAND, Spec

# Below this many distinct phrasings, a command is running on luck.
THIN_FRAMES = 12
# How many of each list to show.
TOP_WORDS = 8
TOP_PAIRS = 6
TOP_SLOT_ERRORS = 8
TOP_MISTAKES = 8


def _frames(spec: Spec, name: str) -> int:
    """How many distinct hand-written phrasings a command has behind it."""
    if name == NONE_COMMAND:
        return len(spec.none_examples)
    command = spec.command(name)
    return len({e.frame for e in command.examples}) if command else 0


def _slot_problems(answers) -> Counter:
    """Slot mistakes on sentences whose command was read right, keyed for grouping."""
    counts: Counter = Counter()
    for item in answers:
        if not item.intent_right or item.full_right:
            continue
        want = dict(item.example.slots)
        got = dict(item.slots)
        for name in want:
            if name not in got:
                counts[(name, "missed", str(want[name]), "")] += 1
            elif got[name] != want[name]:
                counts[(name, "wrong", str(want[name]), str(got[name]))] += 1
        for name in got:
            if name not in want:
                counts[(name, "invented", "", str(got[name]))] += 1
    return counts


def report(spec: Spec, model, dev_examples, result, cutoff: float | None = None) -> dict:
    """Every number and every suggestion, as one plain dictionary."""
    answers = evaluate.run(model, dev_examples)
    cut = model.unsure_below if cutoff is None else float(cutoff)
    overall, _ = evaluate.report(model, dev_examples, cutoff=cut)
    vocabulary = set(result.vocabulary)

    names = list(spec.command_names())
    commands = []
    for name in names:
        items = [a for a in answers if a.example.command == name]
        commands.append(
            {
                "name": name,
                "dev_sentences": len(items),
                "accuracy": sum(a.full_right for a in items) / len(items) if items else None,
                "intent_accuracy": (
                    sum(a.intent_right for a in items) / len(items) if items else None
                ),
                "frames": _frames(spec, name),
            }
        )
    commands.sort(key=lambda row: (row["accuracy"] is None, row["accuracy"] or 0.0))

    confusions = Counter(
        (a.example.command, a.command) for a in answers if not a.intent_right
    )
    confusion_rows = [
        {"gold": gold, "read_as": got, "count": count}
        for (gold, got), count in confusions.most_common(TOP_PAIRS)
    ]

    slot_rows = [
        {"slot": slot, "kind": kind, "want": want, "got": got, "count": count}
        for (slot, kind, want, got), count in _slot_problems(answers).most_common(
            TOP_SLOT_ERRORS
        )
    ]

    per_command_words: dict[str, Counter] = {}
    for item in answers:
        if item.full_right:
            continue
        missing = [t for t in item.example.tokens if t not in vocabulary]
        if not missing:
            continue
        per_command_words.setdefault(item.example.command, Counter()).update(missing)
    unknown_rows = [
        {
            "command": name,
            "words": [{"word": w, "count": c} for w, c in counter.most_common(TOP_WORDS)],
        }
        for name, counter in sorted(
            per_command_words.items(), key=lambda pair: -sum(pair[1].values())
        )
    ]

    confident = sorted(
        (a for a in answers if not a.full_right and a.confidence >= cut),
        key=lambda a: -a.confidence,
    )
    confident_rows = [
        {
            "text": " ".join(a.example.tokens),
            "confidence": round(a.confidence, 3),
            "why": evaluate.why(a),
        }
        for a in confident[:TOP_MISTAKES]
    ]

    thin = [
        {"name": row["name"], "frames": row["frames"]}
        for row in commands
        if row["frames"] < THIN_FRAMES
    ]

    unsure = [a for a in answers if a.confidence < cut]
    unsure_right = sum(a.full_right for a in unsure)

    unknown_share = (
        sum(a.signals.get("unknown_share", 0.0) for a in answers) / len(answers)
        if answers
        else 0.0
    )

    out = {
        "spec": spec.source,
        "extra_files": list(spec.extra_files),
        "dev_sentences": len(dev_examples),
        "training_sentences": len(result.split.train) + len(result.split.dev),
        "training_words": len(vocabulary),
        "cutoff": round(cut, 3),
        "target_accepted_accuracy": result.target,
        "overall": {
            "intent_accuracy": round(overall.intent_accuracy, 4),
            "slot_f1": round(overall.slot_f1, 4),
            "full_accuracy": round(overall.full_accuracy, 4),
            "ece": round(overall.ece, 4),
            "unsure_rate": round(overall.cutoff.unsure_rate, 4),
            "accepted_accuracy": round(overall.cutoff.accepted_accuracy, 4),
            "wrong_caught": round(overall.cutoff.wrong_caught, 4),
            "accepted": overall.cutoff.accepted,
        },
        "commands": commands,
        "confusions": confusion_rows,
        "slot_errors": slot_rows,
        "unknown_words": unknown_rows,
        "confident_mistakes": confident_rows,
        "thin_commands": thin,
        "unsure": {
            "count": len(unsure),
            "share": round(len(unsure) / len(answers), 4) if answers else 0.0,
            "would_have_been_right": unsure_right,
        },
        "gate": _gate_block(result, unknown_share),
    }
    out["next"] = _next_steps(out)
    return out


def _gate_block(result, unknown_share: float) -> dict:
    """How the unsure gate was built and how well it holds up on unfamiliar words."""
    from . import gate as gate_module

    calibrator = getattr(result, "calibrator", None)
    health = dict(getattr(result, "gate_health", {}) or {})
    return {
        "features": (
            [gate_module.FEATURE_NAMES[i] for i in calibrator.feature_ids]
            if calibrator is not None
            else []
        ),
        "weights": [round(float(w), 4) for w in calibrator.weights] if calibrator else [],
        "bias": round(float(calibrator.bias), 4) if calibrator else 0.0,
        "dev": health.get("dev", {}),
        "stressed": health.get("stress", {}),
        "dev_unknown_share": round(float(unknown_share), 4),
    }


def _gate_lines(data: dict) -> list[str]:
    """The calibration health in plain words, for someone who has not read the code."""
    block = data.get("gate") or {}
    dev = block.get("dev") or {}
    stressed = block.get("stressed") or {}
    out: list[str] = []

    if not block.get("features"):
        out.append(
            "the confidence is the plain product of the two probabilities, with no gate "
            "fitted, so nothing here knows about words the model has never seen"
        )
        return out

    out.append(
        "the confidence is built from " + ", ".join(block["features"]) + ", "
        f"and {block['dev_unknown_share'] * 100:.0f}% of the words in your dev set are "
        "words no training sentence uses"
    )
    if dev.get("accepted_accuracy") is not None:
        out.append(
            f"when the model says it is sure it is right "
            f"{dev['accepted_accuracy'] * 100:.0f}% of the time on your dev set, over the "
            f"{dev['accepted']} of {dev['count']} sentences it accepted"
        )
    if stressed.get("accepted_accuracy") is not None:
        out.append(
            f"on the same sentences with words swapped for ones nobody has ever written "
            f"that drops to {stressed['accepted_accuracy'] * 100:.0f}%, and it accepts "
            f"{stressed['accepted']} of {stressed['count']} of them"
        )
    return out


def _next_steps(data: dict) -> list[str]:
    """The shortest list of things that would move the numbers, most useful first."""
    steps: list[str] = []

    for row in data["unknown_words"][:3]:
        words = ", ".join(f'"{w["word"]}"' for w in row["words"][:5])
        steps.append(
            f"add sentences for '{row['command']}' that use the words {words}. "
            "The model has never seen them, so they can only hurt."
        )

    for row in data["confusions"][:3]:
        steps.append(
            f"'{row['gold']}' was read as '{row['read_as']}' {row['count']} times. "
            f"Write more '{row['gold']}' sentences, and a few '{row['read_as']}' ones "
            "that use the same wording, so the difference is visible."
        )

    for row in data["slot_errors"][:3]:
        if row["kind"] == "missed":
            steps.append(
                f"slot '{row['slot']}' lost the value '{row['want']}' {row['count']} times. "
                "Add sentences that say that value in other words, or mark the new wording "
                f"as [the words]({row['slot']}) so it is learnt."
            )
        elif row["kind"] == "wrong":
            steps.append(
                f"slot '{row['slot']}' read '{row['got']}' where '{row['want']}' was meant, "
                f"{row['count']} times. Add sentences that hold both values apart."
            )
        else:
            steps.append(
                f"slot '{row['slot']}' was invented as '{row['got']}' {row['count']} times. "
                "Add 'none' sentences and sentences for this command without that slot."
            )

    for row in data["thin_commands"]:
        steps.append(
            f"'{row['name']}' has only {row['frames']} different phrasings behind it. "
            f"Write more until it has at least {THIN_FRAMES}."
        )

    share = data["unsure"]["share"]
    if share > 0.25:
        steps.append(
            f"{share * 100:.0f}% of dev sentences fall to unsure, and "
            f"{data['unsure']['would_have_been_right']} of those were right anyway. "
            "More varied training sentences raise confidence; lowering the target is the "
            "second-best answer."
        )

    stressed = (data.get("gate") or {}).get("stressed") or {}
    accepted = stressed.get("accepted_accuracy")
    if accepted is not None and accepted < data["target_accepted_accuracy"]:
        steps.append(
            f"with unfamiliar words in them, the answers the model accepts are right "
            f"{accepted * 100:.0f}% of the time, under the "
            f"{data['target_accepted_accuracy'] * 100:.0f}% target. The lever is more "
            "training sentences using more words, not a higher cut-off."
        )

    if data["overall"]["accepted_accuracy"] < data["target_accepted_accuracy"]:
        steps.append(
            "accepted answers are right less often than the target, so the cut-off could "
            "not reach it. Fix the failures above before trusting this model."
        )

    if not steps:
        steps.append("nothing stands out. Write a harder dev set, or ship it.")
    return steps


def lines(data: dict) -> list[str]:
    """The report as plain English, one line at a time."""
    out: list[str] = []
    overall = data["overall"]
    out.append(f"commands file: {data['spec']}")
    if data["extra_files"]:
        out.append("extra sentences: " + ", ".join(data["extra_files"]))
    out.append(
        f"{data['dev_sentences']} dev sentences against {data['training_sentences']} "
        f"training sentences made of {data['training_words']} different words"
    )
    out.append("")
    out.append(
        f"full command accuracy {overall['full_accuracy'] * 100:.1f}%, "
        f"command right {overall['intent_accuracy'] * 100:.1f}%, "
        f"slot f1 {overall['slot_f1'] * 100:.1f}%, ece {overall['ece']:.3f}"
    )
    out.append(
        f"cut-off {data['cutoff']:.3f}: {overall['unsure_rate'] * 100:.1f}% go to unsure, "
        f"the {overall['accepted']} accepted answers are right "
        f"{overall['accepted_accuracy'] * 100:.1f}% of the time, "
        f"{overall['wrong_caught'] * 100:.1f}% of wrong answers caught"
    )

    gate_rows = _gate_lines(data)
    if gate_rows:
        out.append("")
        out.append("how honest the confidence is")
        for row in gate_rows:
            out.append("  " + row)

    out.append("")
    out.append("per command, worst first")
    out.append("  command        dev   accuracy   command right   phrasings")
    for row in data["commands"]:
        if row["accuracy"] is None:
            out.append(f"  {row['name']:<14} {row['dev_sentences']:>4}   no dev sentences")
            continue
        out.append(
            f"  {row['name']:<14} {row['dev_sentences']:>4}   "
            f"{row['accuracy'] * 100:7.1f}%   {row['intent_accuracy'] * 100:12.1f}%   "
            f"{row['frames']:>9}"
        )

    if data["confusions"]:
        out.append("")
        out.append("most confused pairs")
        for row in data["confusions"]:
            out.append(f"  {row['gold']} read as {row['read_as']}: {row['count']}")

    if data["slot_errors"]:
        out.append("")
        out.append("slot value errors")
        for row in data["slot_errors"]:
            if row["kind"] == "missed":
                what = f"missed {row['want']}"
            elif row["kind"] == "wrong":
                what = f"read {row['got']} where {row['want']} was meant"
            else:
                what = f"invented {row['got']}"
            out.append(f"  {row['slot']}: {what} ({row['count']})")

    if data["unknown_words"]:
        out.append("")
        out.append("words in failing sentences that are in no training sentence")
        for row in data["unknown_words"]:
            words = ", ".join(f"{w['word']} ({w['count']})" for w in row["words"])
            out.append(f"  {row['command']}: {words}")

    if data["confident_mistakes"]:
        out.append("")
        out.append("wrong and sure of it, so the user never sees a question")
        for row in data["confident_mistakes"]:
            out.append(f"  [{row['confidence']:.2f}] {row['text']}")
            out.append(f"        {row['why']}")

    if data["thin_commands"]:
        out.append("")
        out.append("commands with too few phrasings")
        for row in data["thin_commands"]:
            out.append(f"  {row['name']}: {row['frames']}")

    out.append("")
    out.append(
        f"unsure: {data['unsure']['count']} of {data['dev_sentences']} sentences "
        f"({data['unsure']['share'] * 100:.1f}%), "
        f"{data['unsure']['would_have_been_right']} of them would have been right"
    )

    out.append("")
    out.append("what to do next")
    for index, step in enumerate(data["next"], start=1):
        out.append(f"  {index}. {step}")
    return out
