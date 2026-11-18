"""Ограничение частоты алертов админу: одинаковая ошибка — не чаще раза в ALERT_COOLDOWN_SEC."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage
from aiogram.types import ErrorEvent, Update
from helpers import ALICE, TOKEN, build_dispatcher, make_msg_update

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.services.alert_throttle import ALERT_COOLDOWN_SEC, AlertThrottle, error_key
from bot.telegram.handlers import router
from bot.telegram.handlers.errors import global_error_handler

ADMIN = 777


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def raise_here(message: str) -> Exception:
    try:
        raise RuntimeError(message)
    except RuntimeError as e:
        return e


def raise_elsewhere(message: str) -> Exception:
    try:
        raise RuntimeError(message)
    except RuntimeError as e:
        return e


def raise_value_error(message: str) -> Exception:
    try:
        raise ValueError(message)
    except ValueError as e:
        return e


def test_cooldown_is_ten_minutes():
    assert ALERT_COOLDOWN_SEC == 600


def test_first_alert_passes_repeats_are_counted_until_cooldown():
    clock = FakeClock()
    alerts = AlertThrottle(cooldown_sec=600, clock=clock)

    assert alerts.register("k") == 0
    clock.now += 1
    assert alerts.register("k") is None
    clock.now += 598
    assert alerts.register("k") is None

    clock.now = 1000.0 + 600
    assert alerts.register("k") == 2

    clock.now += 1
    assert alerts.register("k") is None
    clock.now += 600
    assert alerts.register("k") == 1


def test_different_keys_are_throttled_independently():
    alerts = AlertThrottle(cooldown_sec=600, clock=FakeClock())

    assert alerts.register("a") == 0
    assert alerts.register("b") == 0
    assert alerts.register("a") is None


def test_error_key_is_type_and_place_not_message():
    assert error_key(raise_here("user 1")) == error_key(raise_here("user 2"))
    assert error_key(raise_here("x")) != error_key(raise_elsewhere("x"))
    assert error_key(raise_here("x")) != error_key(raise_value_error("x"))


def test_error_key_without_traceback():
    assert error_key(RuntimeError("a")) == error_key(RuntimeError("b"))
    assert error_key(RuntimeError("a")) != error_key(ValueError("a"))


@pytest.fixture
def handler_env():
    bot = AsyncMock(spec=Bot)
    clock = FakeClock()
    alerts = AlertThrottle(cooldown_sec=600, clock=clock)
    logger = logging.getLogger("alerts_test")
    return bot, clock, alerts, logger


async def call_handler(bot, alerts, logger, exc: Exception, admin_id: int | None = ADMIN) -> None:
    event = ErrorEvent(update=Update(update_id=1), exception=exc)
    config = Config(bot_token=TOKEN, admin_id=admin_id)
    await global_error_handler(event, logger=logger, bot=bot, config=config, alerts=alerts)


async def test_handler_sends_same_error_once_per_cooldown_with_repeat_count(handler_env):
    bot, clock, alerts, logger = handler_env

    await call_handler(bot, alerts, logger, raise_here("boom 1"))
    await call_handler(bot, alerts, logger, raise_here("boom 2"))
    await call_handler(bot, alerts, logger, raise_here("boom 3"))
    assert bot.send_message.await_count == 1
    first_text = bot.send_message.await_args.args[1]
    assert "boom 1" in first_text
    assert "повторялась" not in first_text

    clock.now += 600
    await call_handler(bot, alerts, logger, raise_here("boom 4"))
    assert bot.send_message.await_count == 2
    second_text = bot.send_message.await_args.args[1]
    assert "boom 4" in second_text
    assert "С прошлого алерта повторялась ещё 2 раза." in second_text


async def test_handler_different_errors_are_not_suppressed(handler_env):
    bot, _, alerts, logger = handler_env

    await call_handler(bot, alerts, logger, raise_here("a"))
    await call_handler(bot, alerts, logger, raise_elsewhere("b"))
    assert bot.send_message.await_count == 2


async def test_handler_logs_every_error_even_when_alert_suppressed(handler_env, caplog):
    bot, _, alerts, logger = handler_env

    with caplog.at_level(logging.ERROR, logger="alerts_test"):
        await call_handler(bot, alerts, logger, raise_here("a"))
        await call_handler(bot, alerts, logger, raise_here("a"))
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 2


async def test_handler_failed_send_still_starts_cooldown(handler_env):
    bot, _, alerts, logger = handler_env
    bot.send_message.side_effect = RuntimeError("telegram down")

    await call_handler(bot, alerts, logger, raise_here("a"))
    await call_handler(bot, alerts, logger, raise_here("a"))
    assert bot.send_message.await_count == 1


async def test_dispatcher_passes_throttle_to_error_handler():
    repo = MagicMock()
    repo.get_top.side_effect = RuntimeError("db is broken")
    logger = logging.getLogger("alerts_test")
    config = Config(bot_token=TOKEN, admin_id=ADMIN)
    dp = build_dispatcher(config, repo, DuelEngine(logger=logger, default_hp=5), logger)
    bot = AsyncMock(spec=Bot)
    bot.id = 999
    try:
        for update_id in (1, 2):
            await dp.feed_update(bot, make_msg_update(update_id, ALICE, "/top"))
    finally:
        router._parent_router = None

    assert bot.send_message.await_count == 1
    assert bot.send_message.await_args.args[0] == ADMIN
    assert "db is broken" in bot.send_message.await_args.args[1]


async def test_handler_without_admin_and_network_errors_send_nothing(handler_env):
    bot, _, alerts, logger = handler_env

    await call_handler(bot, alerts, logger, raise_here("a"), admin_id=None)
    net = TelegramNetworkError(method=SendMessage(chat_id=1, text="x"), message="Connection reset")
    await call_handler(bot, alerts, logger, net)
    bot.send_message.assert_not_awaited()
