"""Все проверки перед коммитом одной командой: pytest, ruff check, ruff format --check.

Запуск из корня проекта интерпретатором окружения:

    .venv\\Scripts\\python scripts/check.py

Полный вывод всех шагов пишется в check.log, в консоль — по строке на шаг и хвост вывода упавшего шага.
Код выхода 0, только если прошли все шаги.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "check.log"
TAIL_LINES = 30
STEPS: list[tuple[str, list[str]]] = [
    ("pytest", [sys.executable, "-m", "pytest", "-q"]),
    ("ruff check", [sys.executable, "-m", "ruff", "check", "."]),
    ("ruff format", [sys.executable, "-m", "ruff", "format", "--check", "."]),
]


def _run(command: list[str]) -> tuple[int, str]:
    """Run a step in the project root and return its exit code and merged stdout/stderr."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode, result.stdout


def main(steps: list[tuple[str, list[str]]] | None = None, log_path: Path = LOG_PATH) -> int:
    # Redirected output on Windows defaults to cp1252, which cannot encode the marks and Russian text.
    sys.stdout.reconfigure(encoding="utf-8")
    failed = 0
    with log_path.open("w", encoding="utf-8") as log:
        for name, command in steps or STEPS:
            code, output = _run(command)
            log.write(f"===== {name} (exit {code}) =====\n{output}\n")
            if code == 0:
                print(f"✅ {name}")
                continue
            failed += 1
            print(f"❌ {name} (exit {code})")
            print("\n".join(output.splitlines()[-TAIL_LINES:]))
    print(f"Полный вывод: {log_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
