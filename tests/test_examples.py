"""Keep the scripts in ``examples/`` importable and their CLIs working."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.py"))


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"example_{path.stem}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_examples_exist() -> None:
    assert len(EXAMPLES) >= 5


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_help(path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _load(path)
    assert module.__doc__  # the docstring is the usage text
    with pytest.raises(SystemExit) as exc:
        module.main(["--help"])
    assert exc.value.code == 0
    assert module.__doc__.splitlines()[0] in capsys.readouterr().out
