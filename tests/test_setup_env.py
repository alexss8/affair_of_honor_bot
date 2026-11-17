import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "setup_env.py"


def _load_setup_env():
    spec = importlib.util.spec_from_file_location("setup_env", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_env_writes_all_settings(tmp_path, monkeypatch):
    setup_env = _load_setup_env()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(setup_env, "getpass", lambda prompt: "123:TOKEN")
    # ADMIN_ID, DEFAULT_HP, TOP_LIMIT, DUEL_TIMEOUT_SEC, INVITE_TTL_SEC, DB_PATH, LOG_FILE
    answers = iter(["777", "3", "15", "120", "90", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    setup_env.main()

    env = dict(line.split("=", 1) for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines())
    assert env == {
        "BOT_TOKEN": "123:TOKEN",
        "ADMIN_ID": "777",
        "DEFAULT_HP": "3",
        "TOP_LIMIT": "15",
        "DUEL_TIMEOUT_SEC": "120",
        "INVITE_TTL_SEC": "90",
        "DB_PATH": "duels.db",
        "LOG_FILE": "bot.log",
    }


def test_setup_env_covers_every_setting_from_env_example(tmp_path, monkeypatch):
    """Мастер не отстаёт от .env.example: каждая настройка из примера попадает в .env."""
    setup_env = _load_setup_env()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(setup_env, "getpass", lambda prompt: "123:TOKEN")
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    setup_env.main()

    example = SCRIPT.parent.parent / ".env.example"
    expected = {line.split("=", 1)[0] for line in example.read_text(encoding="utf-8").splitlines() if "=" in line}
    written = {line.split("=", 1)[0] for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines()}
    assert written == expected
