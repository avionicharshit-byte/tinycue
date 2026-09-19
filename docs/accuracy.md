# How edge-nlu is measured

Two test sets, because they answer different questions, and the gap between them is the
honest picture of where the tool stands.

- **The generated test split.** `edgenlu train` holds out whole phrasings before it
  generates, so no sentence in the test split grew from a phrasing the model trained on.
  This measures how well the model covers variations of wording you did write.
- **The hand-written held-out files.** `eval/heldout_smart_home.yaml` and
  `eval/heldout_robot.yaml` share no wording with the commands files at all. They carry
  typos, missing slots, out-of-range numbers and deliberate synonyms ("seize", "unclamp",
  "terminate motion") that appear in no example. This measures wording the model has
  never seen, and it is the number to judge the tool by.

Both models were trained with `edgenlu train <spec> -n 1500 --seed 0` on an M1 MacBook
Air. Nothing was trained on the `eval/` files.

## Full results

| | smart home | robot |
| --- | --- | --- |
| commands | 4 plus `none` | 6 plus `none` |
| hand-written examples | 56 | 72 |
| bundle size | 305 KB | 376 KB |
| device blob size | 205 KB | 263 KB |
| training time | 4.7 s | 4.6 s |
| chosen unsure cut-off | 0.903 | 0.852 |
| **generated test split** | 1018 sentences | 1627 sentences |
| intent accuracy | 92.8% | 81.7% |
| slot F1 | 98.2% | 97.6% |
| full command accuracy | 92.7% | 80.9% |
| ECE | 0.033 | 0.100 |
| sent to unsure | 31.9% | 64.4% |
| wrong answers caught | 100.0% | 98.4% |
| accepted answers right | 100.0% | 99.1% |
| **hand-written held-out set** | 144 sentences | 98 sentences |
| intent accuracy | 77.1% | 42.9% |
| slot F1 | 88.8% | 78.9% |
| full command accuracy | 75.7% | 39.8% |
| ECE | 0.090 | 0.166 |
| sent to unsure | 48.6% | 87.8% |
| wrong answers caught | 85.7% | 94.9% |
| accepted answers right | 93.2% | 75.0% |

Reproduce a column with:

```sh
.venv/bin/edgenlu train examples/smart_home.yaml -n 1500 --seed 0 -o out/model
.venv/bin/edgenlu eval out/model --data out/splits/test.jsonl
.venv/bin/edgenlu eval out/model --data eval/heldout_smart_home.yaml
```

`train` writes `dev.jsonl` and `test.jsonl` into a `splits` folder next to the bundle, so
evaluate a bundle against the split its own training run produced. Training the robot spec
into the same output folder overwrites them.

## Reading the two blocks together

On phrasings that vary what you wrote, the smart home model is good: 92.7% of commands
fully right, and every wrong answer it produced fell below the cut-off. On wording it has
never seen it is not good, and the useful part is that it knows. It throws away almost
half the answers and catches 86% of the ones that would have been wrong, so what reaches
your code is right 93% of the time.

The robot model shows where the approach runs out. Its `stop`, `grab` and `release`
commands carry no slots, so nothing but the literal words identifies them, and the
held-out file deliberately uses synonyms that appear nowhere in the commands file. A model
with no word embeddings cannot reach those. More example sentences is the only lever
available today, which is why a generator for varied training sentences is the next piece
of work.

## What span trimming changed

A tagged span that matches no listed value is now retried a word shorter, so "fan up karo"
gives `speed=up` instead of `speed=up karo`. Full command accuracy on the generated split
went from 91.5% to 92.7% and on the held-out set from 74.3% to 75.7%. The cut-off then
refitted lower, from 0.920 to 0.903, because on dev data it reaches 100% accepted accuracy
sooner. That is worse on the held-out set, where the fallback used to catch 97.3% of the
wrong answers and now catches 85.7%.

That trade is the open problem: the cut-off is fitted on generated dev data, and the
held-out file is harder than that data.

## How the confidence is built

The intent model's probability is temperature scaled on a held-out split. The tagger's
sequence probability gets a fitted exponent. The two are multiplied, and the cut-off is
chosen automatically as the lowest value whose accepted answers are right at least 99% of
the time on dev. Write a number under `fallback.unsure_below` in the commands file to pin
it yourself instead.

## Proving the C runtime against Python

`tests/test_c_parity.py` trains both example specs, exports both blobs, builds the C
command line tool, and runs every sentence of both held-out files plus 300 generated
sentences per spec through Python and through C: 842 sentences. It asserts the same
command, the same slot values, the same missing slots, the same unsure flag and the
confidence to within 1e-3.

It runs Python twice: once with the float32 weights it trained, and once with those
weights rounded to the float16 the blob carries, which is what the C side actually reads.

| Python reference | decision mismatches | worst confidence gap |
| --- | --- | --- |
| float16, the weights the blob carries | 0 of 842 | 7.8e-07 |
| float32, Python's own weights | 0 of 842 | 2.3e-04 |

int8 weights with a per class scale were measured too. No decision changed there either,
but the confidence moved by up to 8.1e-03, over the 1e-3 bar, so the blob ships float16.

On the desktop the C runtime averages 7.5 microseconds a sentence over those 842
sentences, timed around `enlu_parse` alone on an M1 MacBook Air.

## On the boards

Both boards ran the same 31 sentences from `demo/esp32_round/sentences.txt`: ten from the
smart home held-out file, five out of scope, three with typos, two missing a required slot
and one with a number outside the allowed range.

All 31 matched the desktop C tool on both boards. On the Cortex-M33 every confidence was
identical to all six printed decimals. Per-sentence tables are in
[demo/esp32_round/board-results.md](../demo/esp32_round/board-results.md) and
[demo/nxp_mcxn236/board-results.md](../demo/nxp_mcxn236/board-results.md).
