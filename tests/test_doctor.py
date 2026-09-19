"""The doctor report, the hand-written dev set and the guard around the held-out files."""

from __future__ import annotations

import json

import pytest

from conftest import EXAMPLE_FILE, REPO_ROOT
from tinycue import answers, doctor
from tinycue import model as bundle
from tinycue.calibrate import TARGET_ACCURACY, cutoff_table
from tinycue.cli import main
from tinycue.parser import load_spec
from tinycue.train import train

SMALL_N = 150

EXTRA = """
- answer: set_fan(room=bedroom, speed=up)
  say:
    - crank the bedroom fan up
    - "bedroom fan [a notch higher](speed) please"
- answer: none
  say:
    - i am a big fan of cricket
"""

DEV = """
- answer: set_light(room=kitchen, state=on)
  say:
    - "[illuminate](state) the kitchen"
    - kitchen ki light chalu karo
- answer: set_fan(speed=down)
  say:
    - wind the fan down
- answer: set_timer(minutes=25)
  say:
    - twenty five minute timer
- answer: show(what=time)
  say:
    - what time is it
- answer: none
  say:
    - my house is small
    - tell me a story
"""


@pytest.fixture
def files(tmp_path):
    extra = tmp_path / "extra.yaml"
    extra.write_text(EXTRA, encoding="utf-8")
    dev = tmp_path / "dev.yaml"
    dev.write_text(DEV, encoding="utf-8")
    return extra, dev


@pytest.fixture
def checked(tmp_path, files):
    """One trained bundle plus its doctor report, shared by the tests below."""
    extra, dev = files
    spec = load_spec(EXAMPLE_FILE)
    spec.extra_files.append(str(extra))
    answers.apply(spec, answers.load(spec.extra_files, spec, register=True))
    dev_examples = answers.load([dev], spec, register=False)
    out = tmp_path / "model"
    result = train(
        spec,
        out,
        n_per_command=SMALL_N,
        seed=0,
        dev_examples=dev_examples,
        log=lambda *a: None,
    )
    return spec, result, doctor.report(spec, bundle.load(out), dev_examples, result)


def test_the_dev_file_is_never_trained_on(checked):
    spec, result, _ = checked
    dev_texts = {"illuminate the kitchen", "what time is it", "twenty five minute timer"}
    trained = {e.text for c in spec.commands for e in c.examples}
    assert not (dev_texts & trained)


def test_the_generated_dev_split_goes_back_into_training(checked):
    """With a hand-written dev set the generated one is spare data, not a holdout."""
    _, result, _ = checked
    assert result.hand_written_dev
    assert result.vocabulary
    assert len(result.vocabulary) > 200


def test_the_report_counts_every_command(checked):
    _, _, data = checked
    names = {row["name"] for row in data["commands"]}
    assert names == {"set_light", "set_fan", "set_timer", "show", "none"}
    assert sum(row["dev_sentences"] for row in data["commands"]) == data["dev_sentences"]


def test_the_report_names_words_the_model_has_never_seen(checked):
    _, _, data = checked
    words = {w["word"] for row in data["unknown_words"] for w in row["words"]}
    assert "illuminate" in words


def test_the_report_counts_phrasings_per_command(checked):
    _, _, data = checked
    by_name = {row["name"]: row for row in data["commands"]}
    # Fourteen hand-written plus the two the extra file added.
    assert by_name["set_fan"]["frames"] == 16
    assert by_name["set_light"]["frames"] == 14


def test_the_report_always_says_what_to_do_next(checked):
    _, _, data = checked
    assert data["next"]
    assert all(isinstance(step, str) and step for step in data["next"])


def test_the_report_prints_as_plain_english(checked):
    _, _, data = checked
    text = "\n".join(doctor.lines(data))
    assert "full command accuracy" in text
    assert "what to do next" in text
    assert "per command, worst first" in text


def test_the_cutoff_trade_off_table_is_ordered_and_honest():
    confidences = [0.1, 0.3, 0.5, 0.7, 0.9]
    correct = [False, False, True, True, True]
    rows = cutoff_table(confidences, correct)
    assert [row.value for row in rows] == sorted(row.value for row in rows)
    # A higher cut-off never sends fewer sentences to unsure.
    shares = [row.unsure_rate for row in rows]
    assert shares == sorted(shares)
    assert rows[0].unsure_rate == 0.0


def test_the_default_target_is_the_one_the_flag_documents():
    assert TARGET_ACCURACY == pytest.approx(0.97)


def test_doctor_runs_from_the_command_line(files, capsys):
    extra, dev = files
    code = main(
        [
            "doctor",
            str(EXAMPLE_FILE),
            "--extra",
            str(extra),
            "--dev",
            str(dev),
            "-n",
            str(SMALL_N),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "what to do next" in out
    assert "words in failing sentences" in out


def test_doctor_can_print_json_for_an_agent(files, capsys):
    extra, dev = files
    code = main(
        ["doctor", str(EXAMPLE_FILE), "--extra", str(extra), "--dev", str(dev), "-n",
         str(SMALL_N), "--json"]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dev_sentences"] == 7
    assert set(data) >= {
        "overall",
        "commands",
        "confusions",
        "slot_errors",
        "unknown_words",
        "confident_mistakes",
        "thin_commands",
        "unsure",
        "next",
    }


def test_doctor_refuses_a_held_out_file(capsys):
    held = REPO_ROOT / "eval/heldout_smart_home.yaml"
    assert main(["doctor", str(EXAMPLE_FILE), "--dev", str(held)]) == 1
    assert "held-out" in capsys.readouterr().err


def test_training_refuses_a_held_out_file_as_extra_sentences(tmp_path, capsys):
    held = REPO_ROOT / "eval/heldout_robot.yaml"
    code = main(
        ["train", str(EXAMPLE_FILE), "--extra", str(held), "-o", str(tmp_path / "m")]
    )
    assert code == 1
    assert "held-out" in capsys.readouterr().err


def test_the_guard_can_be_turned_off_on_purpose(tmp_path):
    """The guard is a seatbelt, not a lock. It has to be possible to mean it."""
    from tinycue.cli import _guard_heldout

    _guard_heldout([REPO_ROOT / "eval/heldout_robot.yaml"], allow=True)


def test_train_takes_a_dev_file_from_the_command_line(tmp_path, files, capsys):
    extra, dev = files
    code = main(
        [
            "train",
            str(EXAMPLE_FILE),
            "--extra",
            str(extra),
            "--dev",
            str(dev),
            "-n",
            str(SMALL_N),
            "-o",
            str(tmp_path / "model"),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "hand-written dev" in out
    assert "cut-off trade-off" in out
    meta = json.loads((tmp_path / "model" / "meta.json").read_text(encoding="utf-8"))
    assert meta["training"]["dev_sentences"] == 7
    assert meta["training"]["extra_files"] == [str(extra)]


def test_doctor_refuses_a_set_written_by_a_stranger(capsys):
    """eval/stranger*.yaml picks between methods. Fitting on it spends that too."""
    stranger = REPO_ROOT / "eval/stranger_smart_home.yaml"
    assert main(["doctor", str(EXAMPLE_FILE), "--dev", str(stranger)]) == 1
    assert "held-out" in capsys.readouterr().err


def test_training_refuses_a_stranger_file_as_extra_sentences(tmp_path, capsys):
    stranger = REPO_ROOT / "eval/stranger_robot.yaml"
    code = main(
        ["train", str(EXAMPLE_FILE), "--extra", str(stranger), "-o", str(tmp_path / "m")]
    )
    assert code == 1
    assert "held-out" in capsys.readouterr().err


def test_the_doctor_report_says_how_honest_the_confidence_is(files, tmp_path):
    """The gate block is what tells a reader the cut-off is not a guess."""
    from tinycue import doctor as doctor_report
    from tinycue import model as bundle
    from tinycue.cli import _spec_with_extras
    from tinycue.train import train
    from tinycue import answers as answer_files

    extra, dev = files
    spec = _spec_with_extras(EXAMPLE_FILE, [extra])
    dev_examples = answer_files.load([dev], spec, register=False)
    out = tmp_path / "gate_model"
    result = train(
        spec, out, n_per_command=120, seed=0, dev_examples=dev_examples, log=lambda *a: None
    )
    data = doctor_report.report(spec, bundle.load(out), dev_examples, result)
    assert "gate" in data
    assert data["gate"]["dev"]["count"] == len(dev_examples)
    text = "\n".join(doctor_report.lines(data))
    assert "how honest the confidence is" in text
