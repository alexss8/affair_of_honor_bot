"""Контекст в логах: каждая запись при обработке апдейта несёт chat_id, вне апдейта — «chat=-»."""

import io
import logging
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from helpers import ACCEPT, BOB, CHAT_ID, TOKEN, build_dispatcher, cancel_timers, make_callback_update, make_invite

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.logger import is_error_line, make_formatter
from bot.storage.sqlite_repo import SQLiteRepository
from bot.telegram.handlers import router


@pytest.fixture
def log_env(tmp_path):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(make_formatter())
    logger = logging.getLogger("log_context_test")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    repo = SQLiteRepository(str(tmp_path / "log.db"))
    repo.init()
    engine = DuelEngine(logger=logger, default_hp=3)
    dp = build_dispatcher(Config(bot_token=TOKEN), repo, engine, logger)
    bot = AsyncMock(spec=Bot)
    bot.id = 999

    yield dp, bot, engine, logger, stream
    cancel_timers(engine)
    router._parent_router = None
    logger.removeHandler(handler)
    repo.close()


def _line_with(stream: io.StringIO, text: str) -> str:
    lines = [line for line in stream.getvalue().splitlines() if text in line]
    assert lines, f"в логе нет строки с {text!r}:\n{stream.getvalue()}"
    return lines[-1]


async def test_log_inside_update_carries_chat_id(log_env):
    dp, bot, engine, _, stream = log_env
    make_invite(engine)

    await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT))

    assert f"| chat={CHAT_ID} |" in _line_with(stream, "Начата дуэль")


async def test_handler_error_is_logged_with_chat_id(log_env):
    dp, bot, engine, _, stream = log_env

    with patch.object(engine, "get_invite", side_effect=RuntimeError("boom")):
        await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT))

    line = _line_with(stream, "Непредвиденная ошибка")
    assert f"| chat={CHAT_ID} |" in line
    assert is_error_line(line)


async def test_context_does_not_leak_outside_update(log_env):
    dp, bot, engine, logger, stream = log_env
    make_invite(engine)
    await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT))

    logger.info("после апдейта")

    assert "| chat=- |" in _line_with(stream, "после апдейта")
