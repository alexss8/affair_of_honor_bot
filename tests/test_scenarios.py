import asyncio
import contextlib
import datetime
import logging
import sqlite3
import time
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, EditMessageText
from aiogram.types import Chat, ForumTopicCreated, Message, MessageEntity
from helpers import (
    ACCEPT,
    ALICE,
    ATTACK_TOP,
    BOB,
    CHAT_ID,
    DECLINE,
    DEFEND_BOT,
    DEFEND_TOP,
    REVOKE,
    SURRENDER,
    TOKEN,
    TROLL,
    FakeClock,
    build_dispatcher,
    cancel_timers,
    make_callback_update,
    make_invite,
    make_msg_update,
)

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.domain.models import DuelPhase, MoveKind, Zone
from bot.storage.sqlite_repo import SQLiteRepository
from bot.telegram.duel_cards import refresh_card
from bot.telegram.handlers import router
from bot.telegram.rate_limiter import RateLimiter
from bot.telegram.ui import texts


@pytest.fixture
def bot_env(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "scenario.db"))
    repo.init()
    logger = logging.getLogger("scenario")
    config = Config(bot_token=TOKEN, default_hp=2)
    engine = DuelEngine(logger=logger, default_hp=config.default_hp)

    bot = AsyncMock(spec=Bot)
    bot.id = 999
    dp = build_dispatcher(config, repo, engine, logger)

    yield dp, bot, engine, repo
    cancel_timers(engine)
    router._parent_router = None


def _timeout_config(seconds: float) -> Config:
    return Config(bot_token=TOKEN, default_hp=3, duel_timeout_sec=seconds)


async def test_revoke_invite_scenario(bot_env):
    dp, bot, engine, _ = bot_env
    make_invite(engine)

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(1, TROLL, REVOKE))
        await dp.feed_update(bot, make_callback_update(2, BOB, REVOKE))
        mock_edit.assert_not_awaited()

        await dp.feed_update(bot, make_callback_update(3, ALICE, REVOKE))
        mock_edit.assert_awaited_once()
        assert "отозвал свой вызов" in mock_edit.call_args.kwargs["text"]
    assert engine.get_invite(CHAT_ID, 500) is None
    assert engine.get_session(CHAT_ID, 500) is None


async def test_decline_invite_only_by_target(bot_env):
    dp, bot, engine, _ = bot_env
    make_invite(engine)

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(1, ALICE, DECLINE))
        mock_edit.assert_not_awaited()
        await dp.feed_update(bot, make_callback_update(2, BOB, DECLINE))
        assert "отклонен" in mock_edit.call_args.kwargs["text"]
    assert engine.get_invite(CHAT_ID, 500) is None


async def test_duel_command_registers_invite(bot_env):
    dp, bot, engine, _ = bot_env
    sent = Message(message_id=777, date=datetime.datetime.now(), chat=Chat(id=CHAT_ID, type="supergroup"), text="x")
    entities = [MessageEntity(type="bot_command", offset=0, length=5)]
    reply_to = Message(
        message_id=1, date=datetime.datetime.now(), chat=Chat(id=CHAT_ID, type="supergroup"), from_user=BOB, text="hi"
    )

    with patch.object(Message, "answer", new=AsyncMock(return_value=sent)):
        await dp.feed_update(bot, make_msg_update(1, ALICE, "/duel", entities=entities, reply_to=reply_to))

    invite = engine.get_invite(CHAT_ID, 777)
    assert invite is not None
    assert (invite.challenger_id, invite.challenger_name) == (10, "@alice")
    assert (invite.target_id, invite.target_name) == (20, "@bob")


async def test_challenge_by_username_and_text_mention(bot_env):
    dp, bot, _, repo = bot_env
    hidden_user = ALICE.model_copy(update={"id": 30, "first_name": "Скрытный Гном", "username": None})

    await dp.feed_update(bot, make_msg_update(1, BOB, "Всем привет в таверне!"))
    assert repo.get_user_by_username(CHAT_ID, "@bob") == (20, "@bob")

    bot.reset_mock()
    entities_mention = [
        MessageEntity(type="bot_command", offset=0, length=5),
        MessageEntity(type="mention", offset=6, length=4),
    ]
    await dp.feed_update(bot, make_msg_update(2, ALICE, "/duel @bob", entities=entities_mention))
    bot.assert_called()

    bot.reset_mock()
    entities_text_mention = [
        MessageEntity(type="bot_command", offset=0, length=5),
        MessageEntity(type="text_mention", offset=6, length=13, user=hidden_user),
    ]
    await dp.feed_update(bot, make_msg_update(3, ALICE, "/duel Скрытный Гном", entities=entities_text_mention))
    bot.assert_called()

    bot.reset_mock()
    chat = Chat(id=CHAT_ID, type="supergroup", title="Test Group")
    topic_header = Message(
        message_id=1,
        date=datetime.datetime.now(),
        chat=chat,
        from_user=BOB,
        forum_topic_created=ForumTopicCreated(name="Ристалище", icon_color=7322096),
    )
    entities_cmd = [MessageEntity(type="bot_command", offset=0, length=5)]
    await dp.feed_update(bot, make_msg_update(4, ALICE, "/duel", entities=entities_cmd, reply_to=topic_header))
    assert "Укажи соперника" in str(bot.call_args)


async def test_cancel_command_scenario(bot_env):
    dp, bot, engine, repo = bot_env
    cmd_cancel_ent = [MessageEntity(type="bot_command", offset=0, length=7)]

    bot.reset_mock()
    await dp.feed_update(bot, make_msg_update(1, ALICE, "/cancel", entities=cmd_cancel_ent))
    assert "нет активных дуэлей" in str(bot.call_args)

    engine.start_duel(chat_id=CHAT_ID, msg_id=500, p1_id=10, p1_name="@alice", p2_id=20, p2_name="@bob")

    bot.reset_mock()
    await dp.feed_update(bot, make_msg_update(2, TROLL, "/cancel", entities=cmd_cancel_ent))
    assert engine.get_session(CHAT_ID, 500) is not None

    bot.reset_mock()
    await dp.feed_update(bot, make_msg_update(3, ALICE, "/cancel", entities=cmd_cancel_ent))
    assert engine.get_session(CHAT_ID, 500) is None
    assert engine.is_user_busy(CHAT_ID, 10) is False
    assert engine.is_user_busy(CHAT_ID, 20) is False
    assert repo.get_top(CHAT_ID) == []


async def test_full_duel_and_surrender_scenario(bot_env):
    dp, bot, engine, repo = bot_env
    make_invite(engine)

    await dp.feed_update(bot, make_callback_update(1, TROLL, ACCEPT))
    assert engine.get_session(CHAT_ID, 500) is None

    await dp.feed_update(bot, make_callback_update(2, BOB, ACCEPT))
    session = engine.get_session(CHAT_ID, 500)
    assert session is not None
    assert session.p1.hp == 2
    assert session.p1.name == "@alice"

    await dp.feed_update(bot, make_callback_update(3, BOB, ATTACK_TOP))
    assert session.p2.choice is None

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(4, ALICE, ATTACK_TOP))
        last_markup = mock_edit.call_args.kwargs["reply_markup"]
        footer_btn = last_markup.inline_keyboard[-1][0]
        assert "⏳ Ждем @bob" in footer_btn.text
        text_after_alice = mock_edit.call_args.kwargs["text"]
        assert "✅ Атакует: <b>@alice</b>" in text_after_alice
        assert "🟢 Защищается: <b>@bob</b>" in text_after_alice
        assert "Бой начался! Выбирайте действия." in text_after_alice

        await dp.feed_update(bot, make_callback_update(5, BOB, DEFEND_BOT))
        assert session.p2.hp == 1
        all_texts = [call.kwargs.get("text") for call in mock_edit.call_args_list]
        all_footers = [
            call.kwargs["reply_markup"].inline_keyboard[-1][0].text
            for call in mock_edit.call_args_list
            if call.kwargs.get("reply_markup")
        ]
        assert any("Бросок кубиков" in f for f in all_footers)
        round_result_text = all_texts[-1]
        assert "Бой начался! Выбирайте действия." not in round_result_text
        assert "🔴 Атакует: <b>@bob</b>" in round_result_text
        assert "🟢 Защищается: <b>@alice</b>" in round_result_text

        await dp.feed_update(bot, make_callback_update(6, ALICE, DEFEND_TOP))
        text_round2_alice = mock_edit.call_args.kwargs["text"]
        assert session.last_log in text_round2_alice
        assert "✅ Защищается: <b>@alice</b>" in text_round2_alice

    await dp.feed_update(bot, make_callback_update(7, BOB, SURRENDER))
    assert session.surrender_confirm_id == 20
    assert engine.get_session(CHAT_ID, 500) is not None

    await dp.feed_update(bot, make_callback_update(8, BOB, SURRENDER))
    assert engine.get_session(CHAT_ID, 500) is None

    top = repo.get_top(CHAT_ID)
    assert top[0].nickname == "@alice" and top[0].wins == 1
    assert top[1].nickname == "@bob" and top[1].losses == 1


async def test_duel_timeout_victory_by_points(bot_env):
    dp, bot, engine, repo = bot_env
    dp["config"] = _timeout_config(0.05)
    make_invite(engine, msg_id=601)

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT, msg_id=601))
        session = engine.get_session(CHAT_ID, 601)
        assert session is not None
        session.p2.hp = 1

        await asyncio.sleep(0.1)

        assert engine.get_session(CHAT_ID, 601) is None
        assert engine.is_user_busy(CHAT_ID, 10) is False
        assert engine.is_user_busy(CHAT_ID, 20) is False
        last_text = mock_edit.call_args.kwargs["text"]
        assert "Время дуэли вышло!" in last_text
        assert "Победитель: <b>@alice</b>" in last_text

    top = repo.get_top(CHAT_ID)
    assert len(top) == 2
    assert top[0].nickname == "@alice" and top[0].wins == 1
    assert top[1].nickname == "@bob" and top[1].losses == 1


async def test_duel_timeout_draw_when_hp_equal(bot_env):
    dp, bot, engine, repo = bot_env
    dp["config"] = _timeout_config(0.05)
    make_invite(engine, msg_id=602)

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT, msg_id=602))
        assert engine.get_session(CHAT_ID, 602) is not None

        await asyncio.sleep(0.1)

        assert engine.get_session(CHAT_ID, 602) is None
        last_text = mock_edit.call_args.kwargs["text"]
        assert "ОКОНЧЕНА ВНИЧЬЮ" in last_text
        assert "Рейтинг игроков не изменился" in last_text

    assert repo.get_top(CHAT_ID) == []


async def test_double_accept_click_starts_only_one_duel(bot_env):
    dp, bot, engine, _ = bot_env
    make_invite(engine, msg_id=700)

    with (
        patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit,
        patch.object(engine, "start_duel", wraps=engine.start_duel) as spy_start,
    ):
        await asyncio.gather(
            dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT, msg_id=700)),
            dp.feed_update(bot, make_callback_update(2, BOB, ACCEPT, msg_id=700)),
        )

        assert spy_start.call_count == 1
        assert engine.get_session(CHAT_ID, 700) is not None
        edited_texts = [call.kwargs["text"] for call in mock_edit.call_args_list]
        assert not any("уже вступил в другой бой" in t for t in edited_texts)
    assert "Дуэль уже началась" in str(bot.call_args_list)


async def test_accept_uses_names_from_invite_without_get_chat(bot_env):
    dp, bot, engine, _ = bot_env
    make_invite(engine, msg_id=701, challenger=(10, "Сэр Ланселот"))

    with patch.object(Message, "edit_text", new=AsyncMock()):
        await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT, msg_id=701))

    session = engine.get_session(CHAT_ID, 701)
    assert session is not None
    assert session.p1.name == "Сэр Ланселот"
    bot.get_chat.assert_not_called()


async def test_accept_unknown_invite_is_rejected(bot_env):
    """Приглашения нет (например, бот перезапускался) — дуэль не начинается."""
    dp, bot, engine, _ = bot_env

    await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT, msg_id=702))

    assert engine.get_session(CHAT_ID, 702) is None
    assert "больше не действителен" in str(bot.call_args_list)


async def test_expired_invite_cannot_be_accepted(bot_env):
    dp, bot, engine, _ = bot_env
    engine.invite_ttl_sec = 60
    make_invite(engine, msg_id=703, now=0)  # истёк давным-давно

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT, msg_id=703))
        assert "истёк" in mock_edit.call_args.kwargs["text"]

    assert engine.get_session(CHAT_ID, 703) is None
    assert engine.get_invite(CHAT_ID, 703) is None


async def test_accept_when_player_already_busy(bot_env):
    dp, bot, engine, _ = bot_env
    engine.start_duel(CHAT_ID, 900, 10, "@alice", 30, "Troll")
    make_invite(engine, msg_id=704)

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT, msg_id=704))
        assert "уже вступил в другой бой" in mock_edit.call_args.kwargs["text"]

    assert engine.get_session(CHAT_ID, 704) is None
    assert engine.get_invite(CHAT_ID, 704) is None


async def test_stale_old_format_button_is_answered(bot_env):
    dp, bot, engine, _ = bot_env

    await dp.feed_update(bot, make_callback_update(1, BOB, "accept_10_20"))

    assert "кнопка устарела" in str(bot.call_args_list)


async def test_surrender_during_roll_wins_over_round_resolution(bot_env):
    """Сдача во время «броска кубиков»: раунд не рассчитывается, итог — сдача."""
    dp, bot, engine, repo = bot_env
    session = engine.start_duel(CHAT_ID, 500, 10, "@alice", 20, "@bob")
    session.p1.choice = "top"

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        move_task = asyncio.create_task(dp.feed_update(bot, make_callback_update(1, BOB, DEFEND_TOP)))
        await asyncio.sleep(0.05)  # раунд в фазе ROLLING, обработчик ждёт ROLL_DELAY_SEC
        await dp.feed_update(bot, make_callback_update(2, BOB, SURRENDER))
        await dp.feed_update(bot, make_callback_update(3, BOB, SURRENDER))
        await move_task

        assert "белый флаг" in mock_edit.call_args.kwargs["text"]
    assert session.p2.hp == 2  # урон раунда не применялся
    top = repo.get_top(CHAT_ID)
    assert top[0].nickname == "@alice" and top[0].wins == 1


async def test_stale_card_refresh_does_not_overwrite_outcome(bot_env):
    """Правка карточки, «опоздавшая» после конца боя, не затирает итоговую карточку."""
    _, _, engine, _ = bot_env
    session = engine.start_duel(CHAT_ID, 500, 10, "@alice", 20, "@bob")
    message = AsyncMock()
    engine.surrender(session, session.p2)

    assert await refresh_card(message, session) is True
    message.edit_text.assert_not_called()
    message.edit_reply_markup.assert_not_called()


async def test_timer_is_cancelled_when_duel_ends(bot_env):
    dp, bot, engine, _ = bot_env
    make_invite(engine, msg_id=710)

    with patch.object(Message, "edit_text", new=AsyncMock()):
        await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT, msg_id=710))
        session = engine.get_session(CHAT_ID, 710)
        timer = session.timeout_task
        assert timer is not None and not timer.done()

        await dp.feed_update(bot, make_callback_update(2, BOB, SURRENDER, msg_id=710))
        await dp.feed_update(bot, make_callback_update(3, BOB, SURRENDER, msg_id=710))
        await asyncio.sleep(0)

    assert timer.cancelled() or timer.done()
    assert engine.get_session(CHAT_ID, 710) is None


async def _start_duel_and_invite(dp, bot, engine) -> None:
    """Бой @alice vs @bob в сообщении 720 (с таймером) и непринятый вызов Troll → Eve в 721."""
    make_invite(engine, msg_id=720)
    with patch.object(Message, "edit_text", new=AsyncMock()):
        await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT, msg_id=720))
    make_invite(engine, msg_id=721, challenger=(30, "Troll"), target=(40, "Eve"))


def _restart(dp, engine, repo):
    """Новый процесс бота над той же БД: свежий движок и Dispatcher."""
    router._parent_router = None
    new_engine = DuelEngine(logger=engine.logger, default_hp=engine.default_hp)
    return build_dispatcher(dp["config"], repo, new_engine, engine.logger), new_engine


async def test_shutdown_pauses_cards_and_startup_resumes_duel(bot_env):
    dp, bot, engine, repo = bot_env
    await _start_duel_and_invite(dp, bot, engine)
    session = engine.get_session(CHAT_ID, 720)
    with patch.object(Message, "edit_text", new=AsyncMock()):
        await dp.feed_update(bot, make_callback_update(2, ALICE, ATTACK_TOP, msg_id=720))
        await dp.feed_update(bot, make_callback_update(3, BOB, DEFEND_BOT, msg_id=720))
    assert session.p2.hp == 1
    remaining = session.deadline - time.time()

    bot.reset_mock()
    await dp.emit_shutdown(bot=bot, **dp.workflow_data)
    edits = {c.kwargs["message_id"]: c.kwargs for c in bot.edit_message_text.await_args_list}
    assert set(edits) == {720, 721}
    assert all("⏸" in e["text"] and e["reply_markup"] is None for e in edits.values())
    assert engine.sessions == {} and engine.invites == {}

    await asyncio.sleep(0.3)  # бот «лежит»: время боя не должно идти
    dp2, engine2 = _restart(dp, engine, repo)
    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp2.emit_startup(bot=bot, **dp2.workflow_data)
    cards = {c.kwargs["text"]: c.kwargs["reply_markup"] for c in mock_edit.await_args_list}
    assert len(cards) == 2 and all(markup is not None for markup in cards.values())
    assert any("ДУЭЛЬ" in text and "⏸" not in text for text in cards)

    restored = engine2.get_session(CHAT_ID, 720)
    assert restored.p2.hp == 1 and restored.attacker_id == BOB.id
    assert restored.timeout_task is not None and not restored.timeout_task.done()
    assert abs((restored.deadline - time.time()) - remaining) < 0.2
    assert engine2.get_invite(CHAT_ID, 721) is not None

    with patch.object(Message, "edit_text", new=AsyncMock()):
        await dp2.feed_update(bot, make_callback_update(4, BOB, ATTACK_TOP, msg_id=720))
        await dp2.feed_update(bot, make_callback_update(5, ALICE, DEFEND_TOP, msg_id=720))
    assert restored.phase is DuelPhase.CHOOSING and restored.attacker_id == ALICE.id
    cancel_timers(engine2)


async def test_startup_resolves_round_that_was_rolling(bot_env):
    dp, bot, engine, repo = bot_env
    session = engine.start_duel(CHAT_ID, 730, 10, "@alice", 20, "@bob")
    session.p2.hp = 1
    engine.choose(session, session.p1, MoveKind.ATTACK, Zone.TOP)
    engine.choose(session, session.p2, MoveKind.DEFEND, Zone.BOT)
    await dp.emit_shutdown(bot=bot, **dp.workflow_data)

    dp2, engine2 = _restart(dp, engine, repo)
    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp2.emit_startup(bot=bot, **dp2.workflow_data)
    assert "ОКОНЧЕНА" in mock_edit.call_args.kwargs["text"]
    assert engine2.sessions == {}
    assert [s.wins for s in repo.get_top(CHAT_ID)] == [1, 0]


async def test_startup_drops_gone_cards_and_expired_invites(bot_env):
    dp, bot, engine, repo = bot_env
    engine.start_duel(CHAT_ID, 740, 10, "@alice", 20, "@bob")
    make_invite(engine, msg_id=741, challenger=(30, "Troll"), target=(40, "Eve"), now=time.time() - 10_000)
    await dp.emit_shutdown(bot=bot, **dp.workflow_data)

    dp2, engine2 = _restart(dp, engine, repo)

    async def edit(text, **kwargs):
        if "ДУЭЛЬ" in text:  # сообщение с боем удалили, пока бот лежал
            raise TelegramBadRequest(method=AsyncMock(), message="Bad Request: message to edit not found")

    with patch.object(Message, "edit_text", new=AsyncMock(side_effect=edit)) as mock_edit:
        await dp2.emit_startup(bot=bot, **dp2.workflow_data)
    assert engine2.sessions == {} and engine2.invites == {}
    assert engine2.is_user_busy(CHAT_ID, 10) is False
    assert any("истёк" in c.kwargs["text"] for c in mock_edit.await_args_list)


async def test_shutdown_marks_cards_interrupted_when_snapshot_fails(bot_env):
    """Если снимок не сохранился, бой восстановить нельзя: карточки помечаются прерванными (4.1)."""
    dp, bot, engine, repo = bot_env
    await _start_duel_and_invite(dp, bot, engine)
    timer = engine.get_session(CHAT_ID, 720).timeout_task

    bot.reset_mock()
    with patch.object(SQLiteRepository, "save_suspended", side_effect=sqlite3.OperationalError("disk I/O error")):
        await dp.emit_shutdown(bot=bot, **dp.workflow_data)
    await asyncio.sleep(0)

    edits = {c.kwargs["message_id"]: c.kwargs for c in bot.edit_message_text.await_args_list}
    assert set(edits) == {720, 721}
    assert "прерван перезапуском" in edits[720]["text"] and "рейтинг не изменён" in edits[720]["text"]
    assert "@alice" in edits[720]["text"] and "@bob" in edits[720]["text"]
    assert "перезапуск" in edits[721]["text"] and "/duel" in edits[721]["text"]
    assert all(e["chat_id"] == CHAT_ID and e["reply_markup"] is None for e in edits.values())
    assert engine.sessions == {} and engine.invites == {}
    assert timer.cancelled()
    assert repo.get_top(CHAT_ID) == []


# --- 5.6: один открытый вызов от игрока и вызов, истекающий сам ---


def _duel_command(update_id: int, user=ALICE, target=BOB):
    reply_to = Message(
        message_id=1,
        date=datetime.datetime.now(),
        chat=Chat(id=CHAT_ID, type="supergroup"),
        from_user=target,
        text="hi",
    )
    entities = [MessageEntity(type="bot_command", offset=0, length=5)]
    return make_msg_update(update_id, user, "/duel", entities=entities, reply_to=reply_to)


def _card(message_id: int = 777) -> Message:
    return Message(
        message_id=message_id, date=datetime.datetime.now(), chat=Chat(id=CHAT_ID, type="supergroup"), text="x"
    )


async def test_second_duel_command_while_invite_open_is_refused(bot_env):
    dp, bot, engine, _ = bot_env

    with (
        patch.object(Message, "answer", new=AsyncMock(return_value=_card())) as mock_answer,
        patch.object(Message, "reply", new=AsyncMock()) as mock_reply,
    ):
        await dp.feed_update(bot, _duel_command(1))
        await dp.feed_update(bot, _duel_command(2, target=TROLL))

    assert mock_answer.await_count == 1
    assert "открытый вызов" in mock_reply.call_args.args[0]
    assert len(engine.invites) == 1


async def test_simultaneous_duel_commands_create_one_card(bot_env):
    dp, bot, engine, _ = bot_env

    async def slow_send(*args, **kwargs):
        await asyncio.sleep(0.05)  # первая карточка ещё в пути, когда приходит второй /duel
        return _card()

    with (
        patch.object(Message, "answer", new=AsyncMock(side_effect=slow_send)) as mock_answer,
        patch.object(Message, "reply", new=AsyncMock()),
    ):
        await asyncio.gather(dp.feed_update(bot, _duel_command(1)), dp.feed_update(bot, _duel_command(2)))

    assert mock_answer.await_count == 1
    assert len(engine.invites) == 1


async def test_failed_card_send_does_not_block_next_duel(bot_env):
    dp, bot, engine, _ = bot_env
    gone = TelegramBadRequest(method=AsyncMock(), message="Bad Request: chat not found")

    with patch.object(Message, "answer", new=AsyncMock(side_effect=[gone, _card()])) as mock_answer:
        await dp.feed_update(bot, _duel_command(1))
        await dp.feed_update(bot, _duel_command(2))

    assert mock_answer.await_count == 2
    assert engine.get_invite(CHAT_ID, 777) is not None


async def test_unanswered_invite_expires_by_itself(bot_env):
    dp, bot, engine, _ = bot_env
    engine.invite_ttl_sec = 0.05

    with (
        patch.object(Message, "answer", new=AsyncMock(return_value=_card())) as mock_answer,
        patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit,
    ):
        await dp.feed_update(bot, _duel_command(1))
        await asyncio.sleep(0.15)

        assert mock_edit.call_args.kwargs["text"] == texts.INVITE_EXPIRED
        assert mock_edit.call_args.kwargs["reply_markup"] is None
        assert engine.invites == {}

        await dp.feed_update(bot, _duel_command(2))  # можно бросить новый вызов
    assert mock_answer.await_count == 2


async def test_accepted_invite_is_not_marked_expired(bot_env):
    dp, bot, engine, _ = bot_env
    engine.invite_ttl_sec = 0.05

    with (
        patch.object(Message, "answer", new=AsyncMock(return_value=_card())),
        patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit,
    ):
        await dp.feed_update(bot, _duel_command(1))
        await dp.feed_update(bot, make_callback_update(2, BOB, ACCEPT, msg_id=777))
        await asyncio.sleep(0.15)

    assert engine.get_session(CHAT_ID, 777) is not None
    assert all(c.kwargs["text"] != texts.INVITE_EXPIRED for c in mock_edit.await_args_list)


async def test_restored_invite_expires_by_itself(bot_env):
    dp, bot, engine, repo = bot_env
    make_invite(engine, msg_id=750, now=time.time() - engine.invite_ttl_sec + 0.05)
    await dp.emit_shutdown(bot=bot, **dp.workflow_data)

    dp2, engine2 = _restart(dp, engine, repo)
    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp2.emit_startup(bot=bot, **dp2.workflow_data)
        assert engine2.get_invite(CHAT_ID, 750) is not None
        await asyncio.sleep(0.15)

    assert mock_edit.call_args.kwargs["text"] == texts.INVITE_EXPIRED
    assert engine2.invites == {}


# --- 5.8: подсказка в ответе на ход, когда бот притормаживает ---

SLOW_HINT = "⏳ Выбор принят! Telegram притормаживает бота — слишком много боёв сразу, карточка обновится через ~{} с"


async def _limiter_paused_for(seconds: int) -> RateLimiter:
    """Лимитер, у которого чат CHAT_ID на паузе после ответа 429."""
    clock = FakeClock()
    limiter = RateLimiter(clock=clock, sleep=clock.sleep)

    async def flood(bot, method):
        raise TelegramRetryAfter(method=method, message="Flood control exceeded", retry_after=seconds)

    with contextlib.suppress(TelegramRetryAfter):
        await limiter(flood, None, EditMessageText(chat_id=CHAT_ID, message_id=500, text="x"))
    return limiter


def _callback_answers(bot) -> list[AnswerCallbackQuery]:
    return [c.args[0] for c in bot.call_args_list if c.args and isinstance(c.args[0], AnswerCallbackQuery)]


async def _start_battle(dp, bot, engine) -> None:
    make_invite(engine)
    await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT))
    bot.reset_mock()


@pytest.mark.parametrize(("pause", "expected"), [(10, SLOW_HINT.format(10)), (3, SLOW_HINT.format(3))])
async def test_move_reply_warns_when_chat_is_slowed_down(bot_env, pause, expected):
    dp, bot, engine, _ = bot_env
    await _start_battle(dp, bot, engine)
    dp["limiter"] = await _limiter_paused_for(pause)

    await dp.feed_update(bot, make_callback_update(2, ALICE, ATTACK_TOP))

    [answer] = _callback_answers(bot)
    assert answer.text == expected
    assert not answer.show_alert


@pytest.mark.parametrize("pause", [None, 2])
async def test_move_reply_is_plain_without_noticeable_wait(bot_env, pause):
    dp, bot, engine, _ = bot_env
    await _start_battle(dp, bot, engine)
    if pause is not None:
        dp["limiter"] = await _limiter_paused_for(pause)

    await dp.feed_update(bot, make_callback_update(2, ALICE, ATTACK_TOP))

    [answer] = _callback_answers(bot)
    assert answer.text == "Выбор принят! Ждем соперника."


async def test_wrong_move_reply_has_no_slow_hint(bot_env):
    dp, bot, engine, _ = bot_env
    await _start_battle(dp, bot, engine)
    dp["limiter"] = await _limiter_paused_for(10)

    await dp.feed_update(bot, make_callback_update(2, BOB, ATTACK_TOP))

    [answer] = _callback_answers(bot)
    assert answer.text == "Сейчас твой ход ЗАЩИЩАТЬСЯ! Выбери один из блоков."
    assert answer.show_alert
