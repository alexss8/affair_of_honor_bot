import datetime
import logging
from unittest.mock import AsyncMock, patch
import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, ForumTopicCreated, Message, MessageEntity, Update, User

from config import Config
from engine import DuelEngine
from handlers import router
from repository import SQLiteRepository

@pytest.fixture
def bot_env(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "scenario.db"))
    repo.init()
    logger = logging.getLogger("scenario")
    config = Config(bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", default_hp=2)
    engine = DuelEngine(repo=repo, logger=logger, default_hp=config.default_hp)

    bot = AsyncMock(spec=Bot)
    bot.id = 999
    bot.get_chat.return_value = Chat(id=10, type="private", first_name="Alice", username="alice")

    dp = Dispatcher()
    dp["config"] = config
    dp["repo"] = repo
    dp["engine"] = engine
    dp["logger"] = logger
    dp.include_router(router)

    yield dp, bot, engine, repo
    router._parent_router = None

def make_callback_update(update_id: int, user: User, data: str, msg_id: int = 500, chat_id: int = -1001) -> Update:
    chat = Chat(id=chat_id, type="supergroup", title="Test Group")
    msg = Message(message_id=msg_id, date=datetime.datetime.now(), chat=chat, text="Battle")
    cb = CallbackQuery(id=str(update_id), from_user=user, chat_instance="123", message=msg, data=data)
    return Update(update_id=update_id, callback_query=cb)

def make_msg_update(
    update_id: int,
    user: User,
    text: str,
    entities: list[MessageEntity] | None = None,
    reply_to: Message | None = None,
    chat_id: int = -1001,
) -> Update:
    chat = Chat(id=chat_id, type="supergroup", title="Test Group")
    msg = Message(
        message_id=100 + update_id,
        date=datetime.datetime.now(),
        chat=chat,
        from_user=user,
        text=text,
        entities=entities,
        reply_to_message=reply_to,
    )
    return Update(update_id=update_id, message=msg)

async def test_challenge_by_username_and_text_mention(bot_env):
    dp, bot, _, repo = bot_env
    alice = User(id=10, is_bot=False, first_name="Alice", username="alice")
    bob = User(id=20, is_bot=False, first_name="Bob", username="bob")
    hidden_user = User(id=30, is_bot=False, first_name="Скрытный Гном")

    await dp.feed_update(bot, make_msg_update(1, bob, "Всем привет в таверне!"))
    assert repo.get_user_by_username(-1001, "@bob") == (20, "@bob")

    bot.reset_mock()
    entities_mention = [
        MessageEntity(type="bot_command", offset=0, length=5),
        MessageEntity(type="mention", offset=6, length=4),
    ]
    await dp.feed_update(bot, make_msg_update(2, alice, "/duel @bob", entities=entities_mention))
    bot.assert_called()

    bot.reset_mock()
    entities_text_mention = [
        MessageEntity(type="bot_command", offset=0, length=5),
        MessageEntity(type="text_mention", offset=6, length=13, user=hidden_user),
    ]
    await dp.feed_update(bot, make_msg_update(3, alice, "/duel Скрытный Гном", entities=entities_text_mention))
    bot.assert_called()

    bot.reset_mock()
    chat = Chat(id=-1001, type="supergroup", title="Test Group")
    topic_header = Message(
        message_id=1,
        date=datetime.datetime.now(),
        chat=chat,
        from_user=bob,
        forum_topic_created=ForumTopicCreated(name="Ристалище", icon_color=7322096),
    )
    entities_cmd = [MessageEntity(type="bot_command", offset=0, length=5)]
    await dp.feed_update(bot, make_msg_update(4, alice, "/duel", entities=entities_cmd, reply_to=topic_header))
    assert "Укажи соперника" in str(bot.call_args)

async def test_cancel_command_scenario(bot_env):
    dp, bot, engine, repo = bot_env
    alice = User(id=10, is_bot=False, first_name="Alice", username="alice")
    bob = User(id=20, is_bot=False, first_name="Bob", username="bob")
    stranger = User(id=30, is_bot=False, first_name="Troll")

    cmd_cancel_ent = [MessageEntity(type="bot_command", offset=0, length=7)]

    # 1. Вызов /cancel, когда нет активных дуэлей
    bot.reset_mock()
    await dp.feed_update(bot, make_msg_update(1, alice, "/cancel", entities=cmd_cancel_ent))
    assert "нет активных дуэлей" in str(bot.call_args)

    # 2. Запускаем бой между Алисой и Бобом
    engine.start_duel(chat_id=-1001, msg_id=500, p1_id=10, p1_name="@alice", p2_id=20, p2_name="@bob")

    # 3. Чужак пытается отменить чужую дуэль
    bot.reset_mock()
    await dp.feed_update(bot, make_msg_update(2, stranger, "/cancel", entities=cmd_cancel_ent))
    assert engine.get_session(-1001, 500) is not None

    # 4. Алиса отменяет бой
    bot.reset_mock()
    await dp.feed_update(bot, make_msg_update(3, alice, "/cancel", entities=cmd_cancel_ent))
    assert engine.get_session(-1001, 500) is None
    assert engine.is_user_busy(-1001, 10) is False
    assert engine.is_user_busy(-1001, 20) is False
    assert repo.get_top(-1001) == []

async def test_full_duel_and_surrender_scenario(bot_env):
    dp, bot, engine, repo = bot_env
    alice = User(id=10, is_bot=False, first_name="Alice", username="alice")
    bob = User(id=20, is_bot=False, first_name="Bob", username="bob")
    stranger = User(id=30, is_bot=False, first_name="Troll")

    # 1. Чужак пытается принять чужой вызов
    await dp.feed_update(bot, make_callback_update(1, stranger, "accept_10_20"))
    assert engine.get_session(-1001, 500) is None

    # 2. Боб принимает вызов от Алисы
    await dp.feed_update(bot, make_callback_update(2, bob, "accept_10_20"))
    session = engine.get_session(-1001, 500)
    assert session is not None
    assert session.p1.hp == 2

    # 3. Боб (защитник) пытается нажать кнопку удара
    await dp.feed_update(bot, make_callback_update(3, bob, "act_top"))
    assert session.p2.choice is None

    # 4. Алиса бьет сверху, проверяем, что кнопка показала ожидание Боба
    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(4, alice, "act_top"))
        last_markup = mock_edit.call_args.kwargs["reply_markup"]
        footer_btn = last_markup.inline_keyboard[-1][0]
        assert "⏳ Ждем @bob" in footer_btn.text

        # 5. Боб блокирует снизу -> Боб теряет 1 HP, перед результатом была фаза броска кубиков
        await dp.feed_update(bot, make_callback_update(5, bob, "def_bot"))
        assert session.p2.hp == 1
        all_texts = [
            call.kwargs.get("text") or (call.args[0] if call.args else "")
            for call in mock_edit.call_args_list
        ]
        assert any("Бросаем кубики судьбы" in t for t in all_texts)

    # 6. Боб нажимает "Сдаться" первый раз
    await dp.feed_update(bot, make_callback_update(6, bob, "surrender"))
    assert session.surrender_confirm_id == 20
    assert engine.get_session(-1001, 500) is not None

    # 7. Боб подтверждает сдачу вторым нажатием
    await dp.feed_update(bot, make_callback_update(7, bob, "surrender"))
    assert engine.get_session(-1001, 500) is None

    # 8. Проверяем статистику в БД
    top = repo.get_top(-1001)
    assert top[0].nickname == "@alice" and top[0].wins == 1
    assert top[1].nickname == "@bob" and top[1].losses == 1
