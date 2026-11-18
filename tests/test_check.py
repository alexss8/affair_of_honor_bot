"""Все проверки одной командой: scripts/check.py."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check.py"


def _load_check():
    spec = importlib.util.spec_from_file_location("check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_all_steps_pass(tmp_path, capsys):
    check = _load_check()
    log = tmp_path / "check.log"

    code = check.main([("first", _py("print('ok one')")), ("second", _py("print('ok two')"))], log)

    assert code == 0
    out = capsys.readouterr().out
    assert "✅ first" in out and "✅ second" in out
    assert "ok one" not in out
    text = log.read_text(encoding="utf-8")
    assert "ok one" in text and "ok two" in text


def test_failed_step_shows_tail_and_log_keeps_everything(tmp_path, capsys):
    check = _load_check()
    log = tmp_path / "check.log"
    noisy = "import sys\nfor i in range(100): print(f'line {i}')\nprint('ошибка ✗')\nsys.exit(3)"

    code = check.main([("good", _py("print('fine')")), ("bad", _py(noisy))], log)

    assert code != 0
    out = capsys.readouterr().out
    assert "✅ good" in out
    assert "❌ bad (exit 3)" in out
    assert "ошибка ✗" in out
    assert "line 99" in out
    assert "line 50\n" not in out
    text = log.read_text(encoding="utf-8")
    assert "fine" in text and "line 0\n" in text and "ошибка ✗" in text


def test_default_steps_cover_pytest_and_both_ruff_checks():
    check = _load_check()
    commands = [command[2:] for _, command in check.STEPS]
    assert commands == [["pytest", "-q"], ["ruff", "check", "."], ["ruff", "format", "--check", "."]]
