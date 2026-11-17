"""Обновление на сервере: scripts/update.sh (git pull + рестарт без переустановки).

Сквозной тест в настоящих git-репозиториях; su, systemctl, journalctl, id и sleep подменены заглушками через PATH.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ("update.sh", "backup_db.py")


def _find_bash() -> str | None:
    git = shutil.which("git")
    if os.name == "nt" and git:  # Git Bash: <Git>/cmd/git.exe -> <Git>/bin/bash.exe
        candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if candidate.exists():
            return str(candidate)
    return shutil.which("bash")


BASH = _find_bash()
pytestmark = pytest.mark.skipif(BASH is None or shutil.which("git") is None, reason="нужны bash и git")

STUBS = {
    # su - botuser -c "<команда>" — выполняем команду от текущего пользователя
    "su": 'exec bash -c "$4"\n',
    "id": 'echo "${FAKE_UID:-0}"\n',
    "sleep": "exit 0\n",
    "systemctl": 'echo "systemctl $*" >> "$CALLS"\nif [ "$1" = "is-active" ]; then exit "${FAKE_ACTIVE_RC:-0}"; fi\n',
    "journalctl": 'echo "journalctl $*" >> "$CALLS"\n',
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _write_exec(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


@pytest.fixture
def server(tmp_path, monkeypatch):
    """origin (bare), рабочая копия разработчика и копия «на сервере» со своим venv и базой."""
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.com")
    monkeypatch.delenv("DB_PATH", raising=False)

    origin, dev = tmp_path / "origin.git", tmp_path / "dev"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True, capture_output=True)
    (dev / "scripts").mkdir()
    for name in SCRIPTS:
        shutil.copy(ROOT / "scripts" / name, dev / "scripts" / name)
    (dev / "requirements.txt").write_text("aiogram\n", encoding="utf-8")
    _git(dev, "add", "-A")
    _git(dev, "commit", "-q", "-m", "init")
    _git(dev, "push", "-q", "origin", "HEAD:main")

    home = tmp_path / "home"
    app = home / "duel_bot"
    subprocess.run(["git", "clone", "-q", str(origin), str(app)], check=True, capture_output=True)
    sqlite3.connect(app / "duels.db").close()
    venv_bin = app / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    python = Path(sys.executable).as_posix()
    _write_exec(venv_bin / "python", f'exec "{python}" "$@"\n')
    _write_exec(venv_bin / "pip", 'echo "pip $*" >> "$CALLS"\n')

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in STUBS.items():
        _write_exec(stubs / name, body)
    calls = tmp_path / "calls.log"
    calls.touch()
    return {"dev": dev, "app": app, "home": home, "stubs": stubs, "calls": calls}


def _push_change(dev: Path, filename: str, text: str) -> None:
    (dev / filename).write_text(text, encoding="utf-8")
    _git(dev, "add", "-A")
    _git(dev, "commit", "-q", "-m", f"change {filename}")
    _git(dev, "push", "-q", "origin", "HEAD:main")


def _run_update(server, **extra_env) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "STUBS": server["stubs"].as_posix(),
        "CALLS": server["calls"].as_posix(),
        "PYTHONIOENCODING": "utf-8",
        **extra_env,
    }
    # Заглушки — первыми в PATH. В Git Bash путь нужно перевести в его формат (C:/x -> /c/x).
    prepend = 'PATH="$(cygpath -u "$STUBS" 2>/dev/null || echo "$STUBS"):$PATH" exec bash "$0"'
    script = (server["app"] / "scripts" / "update.sh").as_posix()
    return subprocess.run(
        [BASH, "-c", prepend, script], env=env, capture_output=True, text=True, encoding="utf-8", timeout=120
    )


def _calls(server) -> str:
    return server["calls"].read_text(encoding="utf-8")


def test_update_pulls_code_backs_up_and_restarts(server):
    _push_change(server["dev"], "bot.txt", "v2")

    result = _run_update(server)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (server["app"] / "bot.txt").read_text(encoding="utf-8") == "v2"
    assert list((server["home"] / "backups").glob("duels_*.db")), "бэкап перед обновлением"
    calls = _calls(server)
    assert "systemctl restart duelbot" in calls and "systemctl is-active --quiet duelbot" in calls
    assert "pip " not in calls  # requirements.txt не менялся
    assert "change bot.txt" in result.stdout


def test_update_installs_requirements_only_when_changed(server):
    _push_change(server["dev"], "requirements.txt", "aiogram\naiohttp\n")

    result = _run_update(server)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "pip install -r requirements.txt" in _calls(server)


def test_update_without_new_commits_does_not_restart(server):
    result = _run_update(server)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "restart" not in _calls(server)
    assert "Обновлений нет" in result.stdout


def test_update_reports_failed_start_with_rollback_commit(server):
    old = _git(server["app"], "rev-parse", "HEAD")
    _push_change(server["dev"], "bot.txt", "broken")

    result = _run_update(server, FAKE_ACTIVE_RC="3")

    assert result.returncode != 0
    assert "journalctl -u duelbot" in _calls(server)
    assert old in result.stdout  # команда отката с прежним коммитом


def test_update_requires_root(server):
    _push_change(server["dev"], "bot.txt", "v2")

    result = _run_update(server, FAKE_UID="1000")

    assert result.returncode != 0
    assert not (server["app"] / "bot.txt").exists()
    assert _calls(server) == ""


def test_update_stops_when_backup_fails(server):
    (server["app"] / "duels.db").unlink()
    _push_change(server["dev"], "bot.txt", "v2")

    result = _run_update(server)

    assert result.returncode != 0
    assert not (server["app"] / "bot.txt").exists()
    assert "restart" not in _calls(server)
