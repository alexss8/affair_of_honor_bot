"""Проверка конфига при старте: понятные ошибки вместо трейсбеков."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from bot import config as config_module
from bot.config import EXIT_CONFIG_ERROR, Config, ConfigError, load_config

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
SETTINGS = (
    "BOT_TOKEN",
    "ADMIN_ID",
    "DEFAULT_HP",
    "TOP_LIMIT",
    "DUEL_TIMEOUT_SEC",
    "INVITE_TTL_SEC",
    "DB_PATH",
    "LOG_FILE",
)


@pytest.fixture
def env(monkeypatch):
    """Чистое окружение без настоящего .env разработчика."""
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: None)
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    return monkeypatch


def test_defaults_with_only_token(env):
    assert load_config() == Config(bot_token=TOKEN)


def test_all_settings_are_read(env, tmp_path):
    env.setenv("ADMIN_ID", " 777 ")
    env.setenv("DEFAULT_HP", "3")
    env.setenv("TOP_LIMIT", "15")
    env.setenv("DUEL_TIMEOUT_SEC", "120")
    env.setenv("INVITE_TTL_SEC", "60")
    env.setenv("DB_PATH", str(tmp_path / "x.db"))
    env.setenv("LOG_FILE", str(tmp_path / "x.log"))

    cfg = load_config()

    assert (cfg.admin_id, cfg.default_hp, cfg.top_limit) == (777, 3, 15)
    assert (cfg.duel_timeout_sec, cfg.invite_ttl_sec) == (120, 60)
    assert (cfg.db_path, cfg.log_file) == (str(tmp_path / "x.db"), str(tmp_path / "x.log"))


def test_empty_values_fall_back_to_defaults(env):
    env.setenv("ADMIN_ID", "")
    env.setenv("DEFAULT_HP", "")
    assert load_config() == Config(bot_token=TOKEN)


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("BOT_TOKEN", "", "BOT_TOKEN не задан"),
        ("BOT_TOKEN", "вставьте_ваш_токен_сюда", "BOT_TOKEN не похож на токен"),
        ("ADMIN_ID", "@alice", "ADMIN_ID"),
        ("ADMIN_ID", "-5", "ADMIN_ID"),
        ("DEFAULT_HP", "five", "DEFAULT_HP"),
        ("DEFAULT_HP", "0", "DEFAULT_HP=0: допустимо от 1 до 20"),
        ("TOP_LIMIT", "500", "TOP_LIMIT"),
        ("DUEL_TIMEOUT_SEC", "5m", "DUEL_TIMEOUT_SEC"),
        ("INVITE_TTL_SEC", "-1", "INVITE_TTL_SEC"),
        ("DB_PATH", "no_such_dir/duels.db", "DB_PATH"),
        ("LOG_FILE", "no_such_dir/bot.log", "LOG_FILE"),
    ],
)
def test_invalid_setting_is_reported_by_name(env, name, value, expected):
    env.setenv(name, value)
    with pytest.raises(ConfigError, match=expected):
        load_config()


def test_all_errors_are_reported_at_once_without_leaking_token(env):
    env.setenv("BOT_TOKEN", "secret-not-a-token")
    env.setenv("DEFAULT_HP", "x")
    env.setenv("TOP_LIMIT", "0")

    with pytest.raises(ConfigError) as exc:
        load_config()

    message = str(exc.value)
    assert all(name in message for name in ("BOT_TOKEN", "DEFAULT_HP", "TOP_LIMIT"))
    assert "secret-not-a-token" not in message


def test_bot_exits_with_config_error_code_and_message(tmp_path):
    """`python -m bot` с битым .env: понятное сообщение и код 78, без трейсбека."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **dict.fromkeys(SETTINGS, "")}
    env["DEFAULT_HP"] = "много"
    env["PYTHONPATH"] = str(ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "bot"], cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8"
    )

    assert result.returncode == EXIT_CONFIG_ERROR == 78
    assert "BOT_TOKEN не задан" in result.stderr and "DEFAULT_HP" in result.stderr
    assert "Traceback" not in result.stderr
