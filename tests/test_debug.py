"""Отладочный стенд (/debug): включается только флагом DEBUG_TOOLS, доступен только ADMIN_ID, бой с ботом
идёт по настоящему коду хода, имитация 429 срабатывает в настоящем лимитере."""

import datetime
import logging
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, MessageEntity
from helpers import (
    ALICE,
    ATTACK_TOP,
    BOB,
    CHAT_ID,
    DEFEND_BOT,
    TOKEN,
    build_dispatcher,
    callback_answers,
    cancel_timers,
    make_callback_update,
    make_msg_update,
    sent_messages,
)

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.storage.sqlite_repo import SQLiteRepository
from bot.telegram.callbacks import DebugAction, DebugCb
from bot.telegram.handlers import debug, router
from bot.telegram.rate_limiter import GROUP_FALLBACK_LIMIT, RateLimiter
from bot.telegram.ui import texts

CARD_ID = 900
BOT_DUEL = DebugCb(action=DebugAction.BOT_DUEL).pack()
EVIL_DUEL = DebugCb(action=DebugAction.EVIL_NAME_DUEL).pack()
FLOOD = DebugCb(action=DebugAction.FLOOD).pack()
DEBUG_COMMAND = [MessageEntity(type="bot_command", offset=0, length=6)]


def _build(tmp_path, *, debug_tools: bool):
    repo = SQLiteRepository(str(tmp_path / "debug.db"))
    repo.init()
    logger = logging.getLogger("debug-stand")
    config = Config(bot_token=TOKEN, admin_id=ALICE.id, default_hp=2, debug_tools=debug_tools)
    engine = DuelEngine(logger=logger, default_hp=config.default_hp)
    limiter = RateLimiter()
    bot = AsyncMock(spec=Bot)
    bot.id = 999
    dp = build_dispatcher(config, repo, engine, logger, limiter)
    return dp, bot, engine, limiter


@pytest.fixture
def stand(tmp_path):
    dp, bot, engine, limiter = _build(tmp_path, debug_tools=True)
    yield dp, bot, engine, limiter
    cancel_timers(engine)
    router._parent_router = None
    debug.router._parent_router = None


@pytest.fixture
def stand_off(tmp_path):
    dp, bot, engine, _ = _build(tmp_path, debug_tools=False)
    yield dp, bot
    router._parent_router = None


def _card() -> Message:
    return Message(message_id=CARD_ID, date=datetime.datetime.now(), chat=Chat(id=CHAT_ID, type="supergroup"))


async def _start_bot_duel(dp, bot, data: str = BOT_DUEL) -> AsyncMock:
    with (
        patch.object(Message, "answer", new=AsyncMock(return_value=_card())),
        patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit,
    ):
        await dp.feed_update(bot, make_callback_update(1, ALICE, data))
    return mock_edit


async def test_debug_command_is_absent_without_flag(stand_off):
    dp, bot = stand_off
    await dp.feed_update(bot, make_msg_update(1, ALICE, "/debug", entities=DEBUG_COMMAND))
    assert sent_messages(bot) == []


async def test_debug_menu_only_for_admin(stand):
    dp, bot, _, _ = stand

    await dp.feed_update(bot, make_msg_update(1, BOB, "/debug", entities=DEBUG_COMMAND))
    assert sent_messages(bot) == []

    await dp.feed_update(bot, make_msg_update(2, ALICE, "/debug", entities=DEBUG_COMMAND))
    [menu] = sent_messages(bot)
    buttons = [row[0].callback_data for row in menu.reply_markup.inline_keyboard]
    assert buttons == [BOT_DUEL, EVIL_DUEL, FLOOD]


async def test_debug_buttons_ignore_non_admin(stand):
    dp, bot, engine, _ = stand
    with patch.object(Message, "answer", new=AsyncMock(return_value=_card())):
        await dp.feed_update(bot, make_callback_update(1, BOB, BOT_DUEL))
    assert engine.sessions == {}


async def test_bot_duel_each_player_move_resolves_round(stand):
    dp, bot, engine, _ = stand
    mock_edit = await _start_bot_duel(dp, bot)
    session = engine.get_session(CHAT_ID, CARD_ID)
    assert (session.p1.id, session.p2.id) == (ALICE.id, debug.BOT_OPPONENT_ID)
    assert session.timeout_task is not None
    mock_edit.assert_awaited_once()  # карточка боя показана

    # Алиса атакует — бот уже выбрал блок, раунд рассчитан, атакует бот
    with patch.object(Message, "edit_text", new=AsyncMock()):
        await dp.feed_update(bot, make_callback_update(2, ALICE, ATTACK_TOP, msg_id=CARD_ID))
    assert session.attacker_id == debug.BOT_OPPONENT_ID
    assert (session.p1.choice, session.p2.choice) == (None, None)

    # Алиса защищается — бот уже выбрал удар, раунд рассчитан, атакует снова Алиса (если бой не кончился)
    with patch.object(Message, "edit_text", new=AsyncMock()):
        await dp.feed_update(bot, make_callback_update(3, ALICE, DEFEND_BOT, msg_id=CARD_ID))
    assert [a.text for a in callback_answers(bot)][-2:] == ["Выбор принят!", "Выбор принят!"]
    assert session.p1.choice is None and session.p2.choice is None
    assert session.attacker_id == ALICE.id or not session.is_active


async def test_bot_does_not_move_on_wrong_move_kind_twice(stand):
    """Неверный по роли ход игрока: бот выбирает один раз, повторные нажатия его выбор не меняют."""
    dp, bot, engine, _ = stand
    await _start_bot_duel(dp, bot)
    session = engine.get_session(CHAT_ID, CARD_ID)

    await dp.feed_update(bot, make_callback_update(2, ALICE, DEFEND_BOT, msg_id=CARD_ID))
    first_choice = session.p2.choice
    await dp.feed_update(bot, make_callback_update(3, ALICE, DEFEND_BOT, msg_id=CARD_ID))

    assert first_choice is not None and session.p2.choice == first_choice
    assert session.attacker_id == ALICE.id
    assert callback_answers(bot)[-1].show_alert is True  # «Сейчас твой ход АТАКОВАТЬ!»


async def test_evil_name_is_escaped_in_card(stand):
    dp, bot, engine, _ = stand
    mock_edit = await _start_bot_duel(dp, bot, EVIL_DUEL)

    text = mock_edit.call_args.kwargs["text"]
    assert "&lt;b&gt;Tom &amp; Jerry&lt;/b&gt;" in text
    assert "<b>Tom" not in text


async def test_second_bot_duel_is_refused_while_busy(stand):
    dp, bot, engine, _ = stand
    await _start_bot_duel(dp, bot)
    await _start_bot_duel(dp, bot)

    assert len(engine.sessions) == 1
    assert callback_answers(bot)[-1].text == texts.DEBUG_BUSY


async def test_flood_button_pauses_chat_in_real_limiter(stand):
    dp, bot, _, limiter = stand
    assert limiter.estimate_wait(CHAT_ID) == 0

    await dp.feed_update(bot, make_callback_update(1, ALICE, FLOOD))

    assert limiter.estimate_wait(CHAT_ID) > debug.FLOOD_RETRY_SEC - 5
    assert limiter.group_limit == GROUP_FALLBACK_LIMIT
    assert callback_answers(bot)[-1].text == texts.render_debug_flood(debug.FLOOD_RETRY_SEC)
