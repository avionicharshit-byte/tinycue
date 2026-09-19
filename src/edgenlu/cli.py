"""The edgenlu command line: check a commands file, or generate training examples from it."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .generator import generate
from .parser import load_spec
from .schema import NUMBER, SpecError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="edgenlu",
        description="Offline command understanding for tiny devices.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate a commands file and print a summary")
    check.add_argument("file", help="path to the commands file")

    gen = sub.add_parser("generate", help="write training examples as JSON lines")
    gen.add_argument("file", help="path to the commands file")
    gen.add_argument("-n", type=int, default=200, help="examples per command (default 200)")
    gen.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    gen.add_argument("-o", "--out", default="-", help="output file, or - for standard output")

    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return _check(args)
        return _generate(args)
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _check(args) -> int:
    spec = load_spec(args.file)
    total = sum(len(c.examples) for c in spec.commands)
    print(f"{args.file}: ok")
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
    print(f"unsure below: {spec.fallback.unsure_below}, on unsure: {spec.fallback.on_unsure}")
    print(f"none command: {'on' if spec.fallback.none_command else 'off'}")
    return 0


def _generate(args) -> int:
    spec = load_spec(args.file)
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


if __name__ == "__main__":
    raise SystemExit(main())
