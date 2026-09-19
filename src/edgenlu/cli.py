"""The edgenlu command line: check, generate, train, doctor, eval, export and parse."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import answers, doctor as doctor_report, evaluate, model as bundle
from .calibrate import TARGET_ACCURACY
from .decode import decode
from .features import DEFAULT_BUCKETS
from .generator import generate
from .parser import load_examples_file, load_spec, tokenize
from .schema import NUMBER, SpecError
from .train import read_jsonl, train, write_jsonl

# A file here is the final exam. Reading it during the improvement loop is how a model
# comes to look good on paper and fail on the device.
HELDOUT_DIR = "eval"
HELDOUT_PREFIX = "heldout"


def _guard_heldout(paths, allow: bool) -> None:
    """Stop a held-out file being trained on, tuned on or picked apart by accident."""
    if allow:
        return
    for raw in paths or []:
        path = Path(raw)
        if HELDOUT_DIR in path.parts and path.name.startswith(HELDOUT_PREFIX):
            raise SpecError(
                f"{path} looks like a held-out final test set. Measuring against it while "
                "you improve the model spends the only honest number you have. "
                "Pass --allow-heldout if you really mean it."
            )


def _spec_with_extras(file, extra, allow_heldout: bool = False):
    """Load a commands file and fold in every answer-first file it should train on."""
    spec = load_spec(file)
    if extra:
        spec.extra_files.extend(str(p) for p in extra)
    _guard_heldout(spec.extra_files, allow_heldout)
    if spec.extra_files:
        answers.apply(spec, answers.load(spec.extra_files, spec, register=True))
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="edgenlu",
        description="Offline command understanding for tiny devices.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_extra(target):
        target.add_argument(
            "--extra",
            action="append",
            default=[],
            metavar="FILE",
            help="an answer-first file of extra sentences, repeatable",
        )

    check = sub.add_parser("check", help="validate a commands file and print a summary")
    check.add_argument("file", help="path to the commands file")
    add_extra(check)
    check.add_argument(
        "--allow-heldout", action="store_true", help="allow a held-out file to be used"
    )

    gen = sub.add_parser("generate", help="write training examples as JSON lines")
    gen.add_argument("file", help="path to the commands file")
    gen.add_argument("-n", type=int, default=200, help="examples per command (default 200)")
    gen.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    gen.add_argument("-o", "--out", default="-", help="output file, or - for standard output")
    add_extra(gen)
    gen.add_argument(
        "--allow-heldout", action="store_true", help="allow a held-out file to be used"
    )

    tr = sub.add_parser("train", help="train a model bundle from a commands file")
    tr.add_argument("file", help="path to the commands file")
    tr.add_argument("-n", type=int, default=1500, help="examples per command (default 1500)")
    tr.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    tr.add_argument("-o", "--out", default="out/model", help="where to write the bundle")
    add_extra(tr)
    tr.add_argument(
        "--dev",
        default=None,
        metavar="FILE",
        help="an answer-first file to fit the temperature and the cut-off on, never "
        "trained on (default: a slice of the generated data)",
    )
    tr.add_argument(
        "--cutoff-target",
        type=float,
        default=TARGET_ACCURACY,
        help=f"how often an accepted answer has to be right (default {TARGET_ACCURACY})",
    )
    tr.add_argument(
        "--table-size",
        type=int,
        default=None,
        help="hash table buckets, a power of two (default 16384)",
    )
    tr.add_argument(
        "--splits",
        default=None,
        help="where to write the dev and test splits (default: a 'splits' folder next to "
        "the bundle)",
    )
    tr.add_argument(
        "--allow-heldout", action="store_true", help="allow a held-out file to be used"
    )

    doc = sub.add_parser(
        "doctor",
        help="train, measure on a dev file and say where the model is weak",
    )
    doc.add_argument("file", help="path to the commands file")
    add_extra(doc)
    doc.add_argument("--dev", required=True, metavar="FILE", help="an answer-first dev file")
    doc.add_argument("-n", type=int, default=1500, help="examples per command (default 1500)")
    doc.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    doc.add_argument(
        "-o", "--out", default=None, help="keep the trained bundle here instead of a temp folder"
    )
    doc.add_argument(
        "--cutoff-target",
        type=float,
        default=TARGET_ACCURACY,
        help=f"how often an accepted answer has to be right (default {TARGET_ACCURACY})",
    )
    doc.add_argument(
        "--table-size",
        type=int,
        default=None,
        help="hash table buckets, a power of two (default 16384)",
    )
    doc.add_argument("--json", action="store_true", help="print the report as JSON")
    doc.add_argument(
        "--allow-heldout", action="store_true", help="allow a held-out file to be used"
    )

    ev = sub.add_parser("eval", help="measure a bundle on a test set")
    ev.add_argument("model", help="path to the model bundle")
    ev.add_argument("--data", required=True, help="a .jsonl split or a hand-written .yaml set")
    ev.add_argument(
        "--cutoff",
        type=float,
        default=None,
        help="try a cut-off of your own instead of the one in the bundle",
    )
    ev.add_argument("--errors", type=int, default=10, help="how many mistakes to list")
    ev.add_argument(
        "--summary",
        action="store_true",
        help="print the aggregate numbers only, never a sentence from the set",
    )

    ex = sub.add_parser("export", help="write a bundle out for the C runtime")
    ex.add_argument("model", help="path to the model bundle")
    ex.add_argument("-o", "--out", default="out/device", help="where to write the blob")

    ps = sub.add_parser("parse", help="read one sentence with a trained bundle")
    ps.add_argument("model", help="path to the model bundle")
    ps.add_argument("text", nargs="+", help="the sentence to read")

    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return _check(args)
        if args.command == "generate":
            return _generate(args)
        if args.command == "train":
            return _train(args)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "eval":
            return _eval(args)
        if args.command == "export":
            return _export(args)
        return _parse(args)
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _check(args) -> int:
    spec = _spec_with_extras(args.file, args.extra, args.allow_heldout)
    total = sum(len(c.examples) for c in spec.commands)
    print(f"{args.file}: ok")
    if spec.extra_files:
        print("extra sentences: " + ", ".join(spec.extra_files))
    print(f"languages: {', '.join(spec.languages)}")
    print(f"slots: {len(spec.slot_types)}")
    for name, slot_type in spec.slot_types.items():
        if slot_type.kind == NUMBER:
            print(f"  {name}: number, range {slot_type.min} to {slot_type.max}")
        else:
            forms = sum(len(v) for v in slot_type.values.values())
            print(f"  {name}: {len(slot_type.values)} values, {forms} words")
    print(f"commands: {len(spec.commands)}")
    for command in spec.commands:
        slots = ", ".join(
            s.name if s.required else f"{s.name} (optional)" for s in command.slots
        ) or "none"
        print(f"  {command.name}: {len(command.examples)} examples, slots: {slots}")
    print(f"examples: {total}")
    print(
        f"word lists: {len(spec.fillers)} fillers, {len(spec.droppable)} droppable, "
        f"{len(spec.equivalents)} equivalent groups, {len(spec.none_examples)} none sentences"
    )
    print(f"unsure below: {spec.fallback.unsure_below}, on unsure: {spec.fallback.on_unsure}")
    print(f"none command: {'on' if spec.fallback.none_command else 'off'}")
    return 0


def _generate(args) -> int:
    spec = _spec_with_extras(args.file, args.extra, args.allow_heldout)
    examples = generate(spec, n_per_command=args.n, seed=args.seed)
    lines = [json.dumps(e.as_dict(), ensure_ascii=False) for e in examples]

    if args.out == "-":
        for line in lines:
            print(line)
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {len(lines)} examples to {out}", file=sys.stderr)

    counts = Counter(e.command for e in examples)
    for name, count in counts.most_common():
        print(f"  {name}: {count}", file=sys.stderr)
    return 0


def _table_size(args) -> int | None:
    """The hash table size a command asked for, or None when it is not a power of two."""
    size = args.table_size if args.table_size else DEFAULT_BUCKETS
    if size < 2 or size & (size - 1):
        return None
    return size


def _dev_set(args, spec):
    """The hand-written dev set, loaded without teaching the model a single word of it."""
    if not getattr(args, "dev", None):
        return None
    _guard_heldout([args.dev], args.allow_heldout)
    return answers.load([args.dev], spec, register=False)


def _train(args) -> int:
    spec = _spec_with_extras(args.file, args.extra, args.allow_heldout)
    out_dir = Path(args.out)
    table_size = _table_size(args)
    if table_size is None:
        print("error: --table-size must be a power of two", file=sys.stderr)
        return 1
    dev = _dev_set(args, spec)
    if dev is not None:
        print(f"dev set: {len(dev)} hand-written sentences from {args.dev}")
    result = train(
        spec,
        out_dir,
        n_per_command=args.n,
        seed=args.seed,
        table_size=table_size,
        dev_examples=dev,
        target=args.cutoff_target,
    )

    splits = Path(args.splits) if args.splits else out_dir.parent / "splits"
    if dev is not None:
        write_jsonl(dev, splits / "dev.jsonl")
        print(
            f"dev split written to {splits}. Every generated sentence was trained on: "
            f"{args.dev} is the holdout."
        )
    else:
        write_jsonl(result.split.dev, splits / "dev.jsonl")
        write_jsonl(result.split.test, splits / "test.jsonl")
        print(f"splits written to {splits}")

    total = 0
    print("bundle:")
    for name, size in bundle.sizes(out_dir):
        total += size
        print(f"  {name}: {size / 1024:.1f} KB")
    print(f"  total: {total / 1024:.1f} KB")
    print(f"trained in {result.seconds:.1f} s")
    return 0


def _doctor(args) -> int:
    import tempfile

    spec = _spec_with_extras(args.file, args.extra, args.allow_heldout)
    table_size = _table_size(args)
    if table_size is None:
        print("error: --table-size must be a power of two", file=sys.stderr)
        return 1
    dev = _dev_set(args, spec)
    if not dev:
        print("error: the dev file has no sentences", file=sys.stderr)
        return 1

    def run(out_dir) -> dict:
        result = train(
            spec,
            out_dir,
            n_per_command=args.n,
            seed=args.seed,
            table_size=table_size,
            dev_examples=dev,
            target=args.cutoff_target,
            log=lambda *a: None,
        )
        loaded = bundle.load(out_dir)
        return doctor_report.report(spec, loaded, dev, result)

    if args.out:
        data = run(Path(args.out))
    else:
        with tempfile.TemporaryDirectory(prefix="edgenlu-doctor-") as temp:
            data = run(Path(temp) / "model")

    data["dev"] = str(args.dev)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        for line in doctor_report.lines(data):
            print(line)
    return 0


def _load_data(path: str, spec):
    if str(path).endswith((".yaml", ".yml")):
        return load_examples_file(path, spec)
    return read_jsonl(path)


def _eval(args) -> int:
    loaded = bundle.load(args.model)
    examples = _load_data(args.data, loaded.spec)
    cutoff = args.cutoff if args.cutoff is not None else loaded.unsure_below
    report, answers = evaluate.report(loaded, examples, cutoff=cutoff)

    print(f"{args.data}: {report.count} sentences")
    print(f"intent accuracy:       {report.intent_accuracy * 100:.1f}%")
    print(
        f"slot f1:               {report.slot_f1 * 100:.1f}% "
        f"(precision {report.slot_precision * 100:.1f}%, "
        f"recall {report.slot_recall * 100:.1f}%)"
    )
    print(f"full command accuracy: {report.full_accuracy * 100:.1f}%")
    print(f"ece:                   {report.ece:.3f}")
    if not args.summary:
        print()
        print("reliability")
        print("  band         count   mean conf   accuracy")
        for row in report.buckets:
            if not row.count:
                continue
            print(
                f"  {row.low:.1f} to {row.high:.1f}  {row.count:6d}      "
                f"{row.mean_confidence:.3f}      {row.accuracy * 100:5.1f}%"
            )
    print()
    cut = report.cutoff
    print(f"cut-off:               {cut.value:.3f}")
    print(f"sent to unsure:        {cut.unsure_rate * 100:.1f}%")
    print(f"wrong answers caught:  {cut.wrong_caught * 100:.1f}%")
    print(f"accepted answers right:{cut.accepted_accuracy * 100:.1f}% of {cut.accepted}")

    if args.summary:
        return 0

    worst = evaluate.failures(answers, args.errors)
    if worst:
        print()
        print(f"worst {len(worst)} mistakes, most confident first")
        for item in worst:
            sentence = " ".join(item.example.tokens)
            print(f"  [{item.confidence:.2f}] {sentence}")
            print(f"        {evaluate.why(item)}")
    return 0


def _export(args) -> int:
    from .export import export

    result = export(args.model, args.out)
    print(f"wrote {result['path']}")
    print(f"  {result['bytes'] / 1024:.1f} KB, {result['classes']} classes, "
          f"{result['labels']} tags, {result['table_size']} buckets")
    print(f"  plus model_data.h and model_data.c in {args.out}")
    return 0


def _parse(args) -> int:
    loaded = bundle.load(args.model)
    tokens = tokenize(" ".join(args.text))
    if not tokens:
        print("error: nothing to read", file=sys.stderr)
        return 1

    probabilities = loaded.intent_probabilities(tokens)
    best = int(probabilities.argmax())
    command = loaded.classes[best]
    tags, slot_probability = loaded.tag(tokens)
    result = decode(tokens, tags, command, loaded.spec)
    confidence = loaded.confidence(float(probabilities[best]), slot_probability)

    line = f"{result.call()} confidence={confidence:.2f}"
    if result.missing:
        line += f" missing: {', '.join(result.missing)}"
    if confidence < loaded.unsure_below:
        print(f"unsure (best guess: {line})")
    else:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
