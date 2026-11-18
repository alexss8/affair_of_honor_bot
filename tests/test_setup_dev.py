"""Локальное окружение разработчика: scripts/setup_dev.py."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "setup_dev.py"


def _load_setup_dev():
    spec = importlib.util.spec_from_file_location("setup_dev", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_venv_python_path_per_platform(tmp_path):
    setup_dev = _load_setup_dev()
    assert setup_dev.venv_python(tmp_path / ".venv", windows=True) == tmp_path / ".venv" / "Scripts" / "python.exe"
    assert setup_dev.venv_python(tmp_path / ".venv", windows=False) == tmp_path / ".venv" / "bin" / "python"


def test_old_python_is_rejected_without_creating_venv(tmp_path, monkeypatch, capsys):
    setup_dev = _load_setup_dev()
    monkeypatch.setattr(setup_dev, "MIN_PYTHON", (99, 0))
    venv_dir = tmp_path / ".venv"

    code = setup_dev.main(["--venv", str(venv_dir), "--requirements", str(tmp_path / "req.txt")])

    assert code != 0
    assert not venv_dir.exists()
    assert "py -99.0 scripts/setup_dev.py" in capsys.readouterr().err


def test_recreate_refuses_to_delete_non_venv_folder(tmp_path, capsys):
    setup_dev = _load_setup_dev()
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "important.txt").write_text("не трогать", encoding="utf-8")
    requirements = tmp_path / "req.txt"
    requirements.write_text("", encoding="utf-8")

    code = setup_dev.main(["--venv", str(folder), "--requirements", str(requirements), "--recreate"])

    assert code != 0
    assert (folder / "important.txt").exists()
    assert "pyvenv.cfg" in capsys.readouterr().err


def test_russian_output_survives_non_utf8_redirected_stdout():
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}  # как перенаправленный вывод на Windows

    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, env=env)

    assert "UnicodeEncodeError" not in result.stderr.decode("utf-8", errors="replace")
    assert result.returncode == 0
    assert "окружение" in result.stdout.decode("utf-8")


def test_creates_reuses_and_recreates_venv(tmp_path):
    setup_dev = _load_setup_dev()
    venv_dir = tmp_path / ".venv"
    requirements = tmp_path / "req.txt"
    requirements.write_text("# без пакетов: тест не ходит в сеть\n", encoding="utf-8")
    args = ["--venv", str(venv_dir), "--requirements", str(requirements)]

    assert setup_dev.main(args) == 0
    python = setup_dev.venv_python(venv_dir)
    assert python.is_file()
    assert subprocess.run([str(python), "-m", "pip", "check"], capture_output=True).returncode == 0

    marker = venv_dir / "marker.txt"
    marker.write_text("x", encoding="utf-8")
    assert setup_dev.main(args) == 0
    assert marker.exists(), "повторный запуск не должен пересоздавать окружение"

    assert setup_dev.main([*args, "--recreate"]) == 0
    assert not marker.exists()
    assert python.is_file()
