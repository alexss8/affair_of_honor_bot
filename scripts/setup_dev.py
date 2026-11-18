"""Локальное окружение разработчика: .venv на Python 3.12+ с зависимостями для тестов.

Запуск из корня проекта (интерпретатор, которым запущен скрипт, станет Python окружения):

    py -3.12 scripts/setup_dev.py             # создать .venv или доустановить зависимости
    py -3.12 scripts/setup_dev.py --recreate  # удалить .venv и создать заново

На сервере окружение создаёт scripts/deploy.sh (папка venv/), этот скрипт — только для разработки.
"""

import argparse
import shutil
import subprocess
import sys
import venv
from pathlib import Path

MIN_PYTHON = (3, 12)
ROOT = Path(__file__).resolve().parent.parent


def venv_python(venv_dir: Path, windows: bool | None = None) -> Path:
    """Путь к интерпретатору внутри окружения."""
    if windows is None:
        windows = sys.platform == "win32"
    return venv_dir / "Scripts" / "python.exe" if windows else venv_dir / "bin" / "python"


def _fail(message: str) -> int:
    print(f"❌ {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    # Redirected output on Windows defaults to cp1252, which cannot encode the Russian messages;
    # line buffering keeps our messages in order with the output of the pip subprocesses.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description="Создаёт локальное окружение .venv для разработки бота")
    parser.add_argument("--venv", type=Path, default=ROOT / ".venv", help="папка окружения (по умолчанию .venv)")
    parser.add_argument(
        "--requirements", type=Path, default=ROOT / "requirements-dev.txt", help="файл зависимостей для установки"
    )
    parser.add_argument("--recreate", action="store_true", help="удалить окружение и создать заново")
    args = parser.parse_args(argv)

    if sys.version_info[:2] < MIN_PYTHON:
        need = ".".join(map(str, MIN_PYTHON))
        return _fail(
            f"Нужен Python {need}+, запущен {sys.version.split()[0]}. Запустите так: py -{need} scripts/setup_dev.py"
        )

    venv_dir: Path = args.venv
    if args.recreate and venv_dir.exists():
        # Guard against wiping an arbitrary folder passed via --venv by mistake.
        if not (venv_dir / "pyvenv.cfg").is_file():
            return _fail(f"{venv_dir} не похожа на окружение Python (нет pyvenv.cfg) — не удаляю")
        print(f"Удаляю {venv_dir}...")
        shutil.rmtree(venv_dir)

    python = venv_python(venv_dir)
    if python.is_file():
        print(f"Окружение уже есть: {venv_dir}")
    else:
        print(f"Создаю окружение {venv_dir} (Python {sys.version.split()[0]})...")
        venv.EnvBuilder(with_pip=True).create(venv_dir)

    pip = [str(python), "-m", "pip", "--disable-pip-version-check"]
    print(f"Ставлю зависимости из {args.requirements}...")
    try:
        subprocess.run([*pip, "install", "-q", "-r", str(args.requirements)], check=True)
        subprocess.run([*pip, "check"], check=True)
    except subprocess.CalledProcessError as e:
        return _fail(f"pip завершился с ошибкой (код {e.returncode}), подробности выше")

    print(
        "✅ Готово. Команды из корня проекта:\n"
        f"  {python} -m pytest -v\n"
        f"  {python} -m ruff check .\n"
        f"  {python} -m ruff format --check .\n"
        f"  {python} -m bot"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
