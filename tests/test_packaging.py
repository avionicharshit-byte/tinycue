"""What has to be true for `pip install` to give somebody a working tool.

Three things travel with the package and are easy to lose: the language files, the
starter templates and the two C runtime sources. A wheel missing any of them installs
fine and then fails on the user's first command.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile

import pytest

from conftest import PACKAGE_DIR, REPO_ROOT
from edgenlu import __version__, runtime_files, starter
from edgenlu.cli import main

# Paths that must be inside the wheel, as they are written in it.
WHEEL_FILES = (
    "edgenlu/langs/en.yaml",
    "edgenlu/langs/hinglish.yaml",
    "edgenlu/templates/commands.yaml",
    "edgenlu/templates/extra.yaml",
    "edgenlu/templates/dev.yaml",
    "edgenlu/runtime/edgenlu.h",
    "edgenlu/runtime/edgenlu.c",
)


# --------------------------------------------------------------- the version


def test_version_prints_and_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exit:
        main(["--version"])
    assert exit.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_the_version_in_the_package_matches_pyproject():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in text


# ------------------------------------------------------------- the C runtime


def test_the_runtime_sources_are_findable_from_the_package():
    directory = runtime_files.runtime_dir()
    for name in runtime_files.SOURCES:
        assert (directory / name).is_file()


def test_copy_runtime_writes_both_files(tmp_path):
    written = runtime_files.copy_runtime(tmp_path / "device")
    assert [p.name for p in written] == list(runtime_files.SOURCES)
    header = (tmp_path / "device" / "edgenlu.h").read_text(encoding="utf-8")
    assert "enlu_parse" in header
    assert header == (REPO_ROOT / "runtime" / "edgenlu.h").read_text(encoding="utf-8")


def test_export_offers_with_runtime(capsys):
    with pytest.raises(SystemExit) as exit:
        main(["export", "--help"])
    assert exit.value.code == 0
    assert "--with-runtime" in capsys.readouterr().out


# ------------------------------------------------------------ edgenlu init


def test_init_writes_three_files(tmp_path, capsys):
    assert main(["init", "coffee", "-d", str(tmp_path)]) == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["coffee.dev.yaml", "coffee.extra.yaml", "coffee.yaml"]
    assert "edgenlu train" in capsys.readouterr().out


def test_init_puts_the_name_in_the_comments(tmp_path):
    assert main(["init", "brewbot", "-d", str(tmp_path)]) == 0
    text = (tmp_path / "brewbot.yaml").read_text(encoding="utf-8")
    assert "brewbot.extra.yaml" in text
    assert "{name}" not in text


def test_init_refuses_to_overwrite(tmp_path, capsys):
    assert main(["init", "coffee", "-d", str(tmp_path)]) == 0
    assert main(["init", "coffee", "-d", str(tmp_path)]) == 1
    assert "already here" in capsys.readouterr().err


def test_init_refuses_a_name_that_would_not_be_a_filename(tmp_path, capsys):
    assert main(["init", "../escape", "-d", str(tmp_path)]) == 1
    assert "not a usable name" in capsys.readouterr().err


def test_the_starter_files_pass_check(tmp_path, capsys):
    assert main(["init", "coffee", "-d", str(tmp_path)]) == 0
    capsys.readouterr()
    spec, extra, _dev = starter.targets("coffee", tmp_path)
    assert main(["check", str(spec), "--extra", str(extra)]) == 0
    out = capsys.readouterr().out
    assert "ok" in out
    assert "commands: 3" in out


def test_the_starter_dev_file_loads_against_the_starter_spec(tmp_path):
    from edgenlu import answers
    from edgenlu.parser import load_spec

    assert main(["init", "coffee", "-d", str(tmp_path)]) == 0
    spec_path, extra, dev = starter.targets("coffee", tmp_path)
    spec = load_spec(spec_path)
    spec.extra_files.append(str(extra))
    answers.apply(spec, answers.load(spec.extra_files, spec, register=True))
    loaded = answers.load([dev], spec, register=False)
    assert len(loaded) >= 20


# ---------------------------------------------------------------- the wheel


def _build_wheel(out_dir):
    """Build a wheel however this machine can, or say why it cannot."""
    try:
        from hatchling.builders.wheel import WheelBuilder
    except ImportError:
        pass
    else:
        builder = WheelBuilder(str(REPO_ROOT))
        return next(iter(builder.build(directory=str(out_dir), versions=["standard"])))

    if shutil.which("uv"):
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        wheels = sorted(out_dir.glob("*.whl"))
        if wheels:
            return str(wheels[0])
    return None


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    built = _build_wheel(tmp_path_factory.mktemp("wheel"))
    if built is None:
        pytest.skip("no wheel builder here: install hatchling or uv")
    return zipfile.ZipFile(built).namelist()


def test_the_wheel_carries_the_language_and_runtime_files(wheel):
    missing = [name for name in WHEEL_FILES if name not in wheel]
    assert not missing, "missing from the wheel: " + ", ".join(missing)


def test_the_wheel_carries_every_language_file_in_the_source_tree(wheel):
    for path in sorted((PACKAGE_DIR / "langs").glob("*.yaml")):
        assert f"edgenlu/langs/{path.name}" in wheel


def test_the_runtime_copy_in_the_wheel_is_not_in_git():
    """One copy under version control. The wheel makes the second one at build time."""
    assert not (PACKAGE_DIR / "runtime").exists()
