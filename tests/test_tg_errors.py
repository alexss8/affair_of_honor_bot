import datetime
import logging
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from config import Config
from engine import DuelEngine
from handlers import router
from repository import SQLiteRepository
from tg_utils import safe_answer_callback, safe_edit_text, safe_reply

@pytest.fixture
def env(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "errors.db"))
    repo.init()
    logger = logging.getLogger("err_test")
    config = Config(bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", default_hp=5)
    engine = DuelEngine(repo=repo, logger=logger, default_hp=5)

    bot = AsyncMock(spec=Bot)
    bot.id = 999

    dp = Dispatcher()
    dp["config"] = config
    dp["repo"] = repo
    dp["engine"] = engine
    dp["logger"] = logger
    dp.include_router(router)

    yield dp, bot, engine, repo
    router._parent_router = None

EDIT_ERROR_CASES = [
    (
        TelegramBadRequest(method=EditMessageText(chat_id=-1001, text="x"), message="Bad Request: message is not modified"),
        True,
        True,
    ),
    (
        TelegramNetworkError(method=EditMessageText(chat_id=-1001, text="x"), message="Connection reset"),
        True,
        True,
    ),
    (
        TelegramServerError(method=EditMessageText(chat_id=-1001, text="x"), message="Bad Gateway 502"),
        True,
        True,
    ),
    (
        TelegramBadRequest(method=EditMessageText(chat_id=-1001, text="x"), message="Bad Request: message to edit not found"),
        False,
        False,
    ),
    (
        TelegramBadRequest(method=EditMessageText(chat_id=-1001, text="x"), message="Bad Request: message can't be edited"),
        False,
        False,
    ),
    (
        TelegramNotFound(method=EditMessageText(chat_id=-1001, text="x"), message="Not Found"),
        False,
        False,
    ),
    (
        TelegramForbiddenError(method=EditMessageText(chat_id=-1001, text="x"), message="Forbidden: bot was kicked"),
        False,
        False,
    ),
]

@pytest.mark.parametrize("exc, expected_ok, session_should_survive", EDIT_ERROR_CASES)
async def test_edit_text_error_contract_and_session_lifecycle(env, exc, expected_ok, session_should_survive):
    dp, bot, engine, _ = env
    alice = User(id=10, is_bot=False, first_name="Alice")

    # 1. Проверяем напрямую контракт safe_edit_text
    direct_msg = MagicMock()
    direct_msg.chat.id = -1001
    direct_msg.edit_text = AsyncMock(side_effect=exc)
    assert await safe_edit_text(direct_msg, "Text") is expected_ok

    # 2. Проверяем поведение реального боя в Dispatcher при этой же ошибке
    engine.start_duel(chat_id=-1001, msg_id=777, p1_id=10, p1_name="Alice", p2_id=20, p2_name="Bob")

    chat = Chat(id=-1001, type="supergroup", title="Test")
    battle_msg = Message(message_id=777, date=datetime.datetime.now(), chat=chat, text="Battle")
    cb = CallbackQuery(id="1", from_user=alice, chat_instance="123", message=battle_msg, data="act_top")
    upd = Update(update_id=1, callback_query=cb)

    with patch.object(Message, "edit_text", new=AsyncMock(side_effect=exc)):
        await dp.feed_update(bot, upd)

    session_alive = engine.get_session(-1001, 777) is not None
    assert session_alive is session_should_survive
    assert engine.is_user_busy(-1001, 10) is session_should_survive
    assert engine.is_user_busy(-1001, 20) is session_should_survive

async def test_ignore_query_too_old():
    cb = MagicMock()
    cb.answer = AsyncMock(
        side_effect=TelegramBadRequest(
            method=AnswerCallbackQuery(callback_query_id="1"),
            message="Bad Request: query is too old and response timeout expired",
        )
    )
    await safe_answer_callback(cb, "Ok")
    cb.answer.assert_awaited_once()

async def test_fallback_to_answer_when_replied_message_deleted():
    msg = MagicMock()
    msg.reply = AsyncMock(
        side_effect=TelegramBadRequest(
            method=SendMessage(chat_id=-1001, text="x"),
            message="Bad Request: message to be replied not found",
        )
    )
    msg.answer = AsyncMock()

    await safe_reply(msg, "Fallback text")

    msg.reply.assert_awaited_once()
    msg.answer.assert_awaited_once_with("Fallback text", parse_mode="HTML")

async def test_retry_after_flood_control():
    msg = MagicMock()
    msg.chat.id = -1001
    msg.edit_text = AsyncMock(
        side_effect=[
            TelegramRetryAfter(
                method=EditMessageText(chat_id=-1001, text="x"),
                message="Flood control exceeded",
                retry_after=0.01,
            ),
            True,
        ]
    )
    assert await safe_edit_text(msg, "Updated after rate limit") is True
    assert msg.edit_text.await_count == 2
