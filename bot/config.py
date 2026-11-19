"""Настройки бота из .env с проверкой при старте: все ошибки сразу и понятным текстом."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Код выхода при ошибке в настройках (EX_CONFIG из sysexits.h). systemd-служба не перезапускает бота
# с этим кодом (RestartPreventExitStatus в scripts/deploy.sh) — ошибку нужно исправить в .env.
EXIT_CONFIG_ERROR = 78

_TOKEN_RE = re.compile(r"^\d+:[\w-]{30,}$")


class ConfigError(ValueError):
    """Ошибки в настройках. Сообщение перечисляет все найденные проблемы."""


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_id: int | None = None
    default_hp: int = 5
    top_limit: int = 10
    duel_timeout_sec: int = 300
    invite_ttl_sec: int = 300
    db_path: str = "duels.db"
    log_file: str = "bot.log"
    debug_tools: bool = False


def load_config() -> Config:
    """Читает настройки из окружения и .env (или файла из переменной окружения ENV_FILE — для тестового бота).

    Пустое значение равносильно отсутствию переменной (берётся значение по умолчанию).

    Raises:
        ConfigError: в настройках есть ошибки; в сообщении — каждая с именем переменной.
    """
    errors: list[str] = []
    env_file = os.getenv("ENV_FILE", "").strip()
    if env_file and not Path(env_file).is_file():
        errors.append(f"ENV_FILE={env_file!r}: файла нет")
    load_dotenv(env_file or ".env")
    defaults = Config(bot_token="")

    def raw(name: str) -> str | None:
        value = os.getenv(name, "").strip()
        return value or None

    def int_setting(name: str, default: int, low: int, high: int) -> int:
        value = raw(name)
        if value is None:
            return default
        try:
            number = int(value)
        except ValueError:
            errors.append(f"{name}={value!r}: нужно целое число")
            return default
        if not low <= number <= high:
            errors.append(f"{name}={number}: допустимо от {low} до {high}")
            return default
        return number

    def path_setting(name: str, default: str) -> str:
        value = raw(name) or default
        if not Path(value).parent.is_dir():
            errors.append(f"{name}={value!r}: папки {str(Path(value).parent)!r} не существует")
        return value

    token = raw("BOT_TOKEN") or ""
    if not token:
        errors.append("BOT_TOKEN не задан — возьмите токен у @BotFather")
    elif not _TOKEN_RE.match(token):
        errors.append("BOT_TOKEN не похож на токен от @BotFather (ожидается вид 123456789:AAE...)")

    admin_raw = raw("ADMIN_ID")
    admin_id = None
    if admin_raw is not None:
        if admin_raw.isdigit():
            admin_id = int(admin_raw)
        else:
            errors.append(f"ADMIN_ID={admin_raw!r}: нужен числовой Telegram ID (узнать можно у @userinfobot)")

    debug_tools = int_setting("DEBUG_TOOLS", 0, 0, 1) == 1
    if debug_tools and admin_id is None:
        errors.append("DEBUG_TOOLS=1: нужен ADMIN_ID — команда /debug доступна только админу")

    config = Config(
        bot_token=token,
        admin_id=admin_id,
        default_hp=int_setting("DEFAULT_HP", defaults.default_hp, 1, 20),
        top_limit=int_setting("TOP_LIMIT", defaults.top_limit, 1, 50),
        duel_timeout_sec=int_setting("DUEL_TIMEOUT_SEC", defaults.duel_timeout_sec, 10, 86400),
        invite_ttl_sec=int_setting("INVITE_TTL_SEC", defaults.invite_ttl_sec, 10, 86400),
        db_path=path_setting("DB_PATH", defaults.db_path),
        log_file=path_setting("LOG_FILE", defaults.log_file),
        debug_tools=debug_tools,
    )
    if errors:
        raise ConfigError("Ошибки в настройках (.env):\n" + "\n".join(f"  • {e}" for e in errors))
    return config
