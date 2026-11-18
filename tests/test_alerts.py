"""Ограничение частоты алертов админу: одинаковая ошибка — не чаще раза в ALERT_COOLDOWN_SEC."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import ErrorEvent, Update
from helpers import ALICE, ATTACK_TOP, TOKEN, build_dispatcher, make_callback_update, make_msg_update

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.services.alert_throttle import ALERT_COOLDOWN_SEC, AlertThrottle, error_key
from bot.telegram.handlers import router
from bot.telegram.handlers.errors import ERROR_NOTICE_COOLDOWN_SEC, global_error_handler
from bot.telegram.ui.texts import ERROR_NOTICE

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


async def call_handler(
    bot,
    alerts,
    logger,
    exc: Exception,
    admin_id: int | None = ADMIN,
    update: Update | None = None,
    error_notices: AlertThrottle | None = None,
) -> None:
    update = Update(update_id=1) if update is None else Update.model_validate(update.model_dump(), context={"bot": bot})
    event = ErrorEvent(update=update, exception=exc)
    config = Config(bot_token=TOKEN, admin_id=admin_id)
    notices = AlertThrottle(cooldown_sec=ERROR_NOTICE_COOLDOWN_SEC) if error_notices is None else error_notices
    await global_error_handler(event, logger=logger, bot=bot, config=config, alerts=alerts, error_notices=notices)


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


# --- 5.9: игрок узнаёт о сбое ---


def player_notices(bot) -> list:
    """Ответы игрокам: ответ на нажатие и сообщения в чат (алерт админу идёт через bot.send_message)."""
    return [
        c.args[0] for c in bot.call_args_list if c.args and isinstance(c.args[0], (AnswerCallbackQuery, SendMessage))
    ]


def command_update(update_id: int, chat_id: int = -1001) -> Update:
    return make_msg_update(update_id, ALICE, "/top", chat_id=chat_id)


def test_error_notice_cooldown_is_five_minutes():
    assert ERROR_NOTICE_COOLDOWN_SEC == 300


async def test_button_press_gets_alert_without_exception_text(handler_env):
    bot, _, alerts, logger = handler_env

    await call_handler(
        bot, alerts, logger, raise_here("secret details"), update=make_callback_update(1, ALICE, ATTACK_TOP)
    )

    [notice] = player_notices(bot)
    assert isinstance(notice, AnswerCallbackQuery)
    assert notice.text == ERROR_NOTICE
    assert notice.show_alert
    assert "secret" not in notice.text


async def test_failed_command_gets_reply_once_per_chat_per_cooldown(handler_env):
    bot, _, alerts, logger = handler_env
    clock = FakeClock()
    notices = AlertThrottle(cooldown_sec=ERROR_NOTICE_COOLDOWN_SEC, clock=clock)

    async def fail(update: Update) -> None:
        await call_handler(bot, alerts, logger, raise_here("secret details"), update=update, error_notices=notices)

    await fail(command_update(1))
    await fail(command_update(2))
    clock.now += ERROR_NOTICE_COOLDOWN_SEC - 1
    await fail(command_update(3))
    assert len(player_notices(bot)) == 1

    await fail(command_update(4, chat_id=-2002))  # другой чат ограничивается отдельно
    clock.now += 1
    await fail(command_update(5))

    sent = player_notices(bot)
    assert [m.chat_id for m in sent] == [-1001, -2002, -1001]
    assert all(m.text == ERROR_NOTICE and m.reply_parameters for m in sent)
    assert all("secret" not in m.text for m in sent)


async def test_plain_message_error_gets_no_reply(handler_env):
    bot, _, alerts, logger = handler_env

    await call_handler(bot, alerts, logger, raise_here("a"), update=make_msg_update(1, ALICE, "привет"))

    assert player_notices(bot) == []


async def test_player_is_notified_without_admin(handler_env):
    bot, _, alerts, logger = handler_env

    await call_handler(bot, alerts, logger, raise_here("a"), admin_id=None, update=command_update(1))

    assert len(player_notices(bot)) == 1
    bot.send_message.assert_not_awaited()


async def test_suppressed_alert_does_not_suppress_player_notice(handler_env):
    bot, _, alerts, logger = handler_env

    for update_id in (1, 2):
        await call_handler(
            bot, alerts, logger, raise_here("a"), update=make_callback_update(update_id, ALICE, ATTACK_TOP)
        )

    assert len(player_notices(bot)) == 2
    assert bot.send_message.await_count == 1


@pytest.mark.parametrize(
    "exc",
    [
        TelegramNetworkError(method=SendMessage(chat_id=1, text="x"), message="Connection reset"),
        TelegramForbiddenError(method=SendMessage(chat_id=1, text="x"), message="bot was kicked"),
        TelegramRetryAfter(method=SendMessage(chat_id=1, text="x"), message="Flood control exceeded", retry_after=5),
    ],
    ids=["network", "forbidden", "retry_after"],
)
async def test_player_gets_nothing_on_telegram_trouble(handler_env, exc):
    bot, _, alerts, logger = handler_env

    await call_handler(bot, alerts, logger, exc, update=command_update(1))
    await call_handler(bot, alerts, logger, exc, update=make_callback_update(2, ALICE, ATTACK_TOP))

    assert player_notices(bot) == []


async def test_dispatcher_notifies_player_on_failed_move():
    engine = MagicMock()
    engine.get_session.side_effect = RuntimeError("engine is broken")
    logger = logging.getLogger("alerts_test")
    dp = build_dispatcher(Config(bot_token=TOKEN, admin_id=ADMIN), MagicMock(), engine, logger)
    bot = AsyncMock(spec=Bot)
    bot.id = 999
    try:
        await dp.feed_update(bot, make_callback_update(1, ALICE, ATTACK_TOP))
    finally:
        router._parent_router = None

    [notice] = player_notices(bot)
    assert isinstance(notice, AnswerCallbackQuery)
    assert notice.text == ERROR_NOTICE
    assert "engine is broken" in bot.send_message.await_args.args[1]
