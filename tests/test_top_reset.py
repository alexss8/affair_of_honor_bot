"""Сброс топа чата админом: /resettop → подтверждение кнопкой."""

import logging
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetChatMember
from aiogram.types import (
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    ChatMemberAdministrator,
    ChatMemberMember,
    Message,
    User,
)
from helpers import (
    ALICE,
    BOB,
    CHAT_ID,
    TOKEN,
    TROLL,
    build_dispatcher,
    callback_answers,
    make_callback_update,
    make_msg_update,
    sent_messages,
)

import bot.__main__ as bot_main
from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.domain.models import Player
from bot.storage.sqlite_repo import SQLiteRepository
from bot.telegram.callbacks import TopResetAction, TopResetCb
from bot.telegram.handlers import router
from bot.telegram.ui import texts

OTHER_CHAT = -2002
OWNER_ID = 777
CARD_ID = 900


def _confirm(admin: User) -> str:
    return TopResetCb(action=TopResetAction.CONFIRM, admin_id=admin.id).pack()


def _cancel(admin: User) -> str:
    return TopResetCb(action=TopResetAction.CANCEL, admin_id=admin.id).pack()


def _admin(user: User) -> ChatMemberAdministrator:
    return ChatMemberAdministrator.model_construct(user=user)


@pytest.fixture
def env(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "reset.db"))
    repo.init()
    for chat in (CHAT_ID, OTHER_CHAT):
        repo.record_duel(chat, Player(BOB.id, "Bob", 1), Player(TROLL.id, "Troll", 0))
    logger = logging.getLogger("top_reset")
    config = Config(bot_token=TOKEN, admin_id=OWNER_ID)
    engine = DuelEngine(logger=logger, default_hp=config.default_hp)

    admins = {ALICE.id}  # Alice is a group admin; Bob and Troll are plain members

    async def get_chat_member(chat_id, user_id):
        user = User(id=user_id, is_bot=False, first_name="U")
        return _admin(user) if user_id in admins else ChatMemberMember(user=user)

    bot = AsyncMock(spec=Bot)
    bot.id = 999
    bot.get_chat_member.side_effect = get_chat_member
    dp = build_dispatcher(config, repo, engine, logger)

    yield dp, bot, repo, admins
    router._parent_router = None


def test_reset_top_clears_only_its_chat(env):
    _, _, repo, _ = env

    assert repo.reset_top(CHAT_ID) == 2
    assert repo.get_top(CHAT_ID) == []
    assert len(repo.get_top(OTHER_CHAT)) == 2
    assert repo.reset_top(CHAT_ID) == 0


async def test_non_admin_gets_refusal_and_no_card(env):
    dp, bot, repo, _ = env

    await dp.feed_update(bot, make_msg_update(1, BOB, "/resettop"))

    sent = sent_messages(bot)
    assert [m.text for m in sent] == [texts.TOP_RESET_DENIED]
    assert sent[0].reply_markup is None
    assert len(repo.get_top(CHAT_ID)) == 2


async def test_group_admin_resets_top_after_confirm(env):
    dp, bot, repo, _ = env

    await dp.feed_update(bot, make_msg_update(1, ALICE, "/resettop"))
    (card,) = sent_messages(bot)
    assert card.text == texts.TOP_RESET_CONFIRM
    buttons = [b.callback_data for b in card.reply_markup.inline_keyboard[0]]
    assert buttons == [_confirm(ALICE), _cancel(ALICE)]
    assert len(repo.get_top(CHAT_ID)) == 2  # nothing is deleted before the confirmation

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(2, ALICE, _confirm(ALICE), msg_id=CARD_ID))

    assert repo.get_top(CHAT_ID) == []
    assert len(repo.get_top(OTHER_CHAT)) == 2
    assert mock_edit.call_args.kwargs["text"] == texts.render_top_reset_done("@alice")
    assert mock_edit.call_args.kwargs["reply_markup"] is None


async def test_bot_owner_resets_top_without_group_admin_rights(env):
    dp, bot, repo, _ = env
    owner = User(id=OWNER_ID, is_bot=False, first_name="Owner")

    await dp.feed_update(bot, make_msg_update(1, owner, "/resettop"))
    assert sent_messages(bot)[0].text == texts.TOP_RESET_CONFIRM

    with patch.object(Message, "edit_text", new=AsyncMock()):
        await dp.feed_update(bot, make_callback_update(2, owner, _confirm(owner), msg_id=CARD_ID))

    assert repo.get_top(CHAT_ID) == []


async def test_other_user_cannot_press_admin_buttons(env):
    dp, bot, repo, admins = env
    admins.add(BOB.id)  # even another admin cannot confirm someone else's reset

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(1, BOB, _confirm(ALICE), msg_id=CARD_ID))
        await dp.feed_update(bot, make_callback_update(2, TROLL, _cancel(ALICE), msg_id=CARD_ID))

    mock_edit.assert_not_awaited()
    answers = callback_answers(bot)
    assert [(a.text, a.show_alert) for a in answers] == [(texts.TOP_RESET_NOT_YOURS, True)] * 2
    assert len(repo.get_top(CHAT_ID)) == 2


async def test_cancel_keeps_top(env):
    dp, bot, repo, _ = env

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(1, ALICE, _cancel(ALICE), msg_id=CARD_ID))

    assert mock_edit.call_args.kwargs["text"] == texts.TOP_RESET_CANCELLED
    assert len(repo.get_top(CHAT_ID)) == 2


async def test_demoted_admin_cannot_confirm(env):
    dp, bot, repo, admins = env
    await dp.feed_update(bot, make_msg_update(1, ALICE, "/resettop"))
    admins.discard(ALICE.id)

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(2, ALICE, _confirm(ALICE), msg_id=CARD_ID))

    mock_edit.assert_not_awaited()
    assert [(a.text, a.show_alert) for a in callback_answers(bot)] == [(texts.TOP_RESET_DENIED, True)]
    assert len(repo.get_top(CHAT_ID)) == 2


async def test_rights_unknown_when_telegram_fails(env):
    dp, bot, repo, _ = env
    bot.get_chat_member.side_effect = TelegramNetworkError(GetChatMember(chat_id=CHAT_ID, user_id=ALICE.id), "down")

    await dp.feed_update(bot, make_msg_update(1, ALICE, "/resettop"))
    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(2, ALICE, _confirm(ALICE), msg_id=CARD_ID))

    assert [m.text for m in sent_messages(bot)] == [texts.TOP_RESET_DENIED]
    mock_edit.assert_not_awaited()
    assert len(repo.get_top(CHAT_ID)) == 2


async def test_resettop_is_in_the_menu_of_group_admins_only(tmp_path):
    config = Config(bot_token=TOKEN, db_path=str(tmp_path / "main.db"), log_file=str(tmp_path / "main.log"))
    set_commands = AsyncMock()

    with (
        patch.object(bot_main, "setup_logger", return_value=logging.getLogger("top_reset")),
        patch.object(Bot, "set_my_commands", set_commands),
        patch.object(Bot, "delete_webhook", AsyncMock()),
        patch.object(Dispatcher, "start_polling", AsyncMock()),
    ):
        try:
            await bot_main.main(config)
        finally:
            router._parent_router = None

    menus = {
        type(c.kwargs["scope"]): [cmd.command for cmd in c.kwargs["commands"]] for c in set_commands.await_args_list
    }
    players = ["duel", "cancel", "top", "help"]
    assert menus == {
        BotCommandScopeAllGroupChats: players,
        # Telegram shows admins this menu instead of the group one, so player commands must stay in it.
        BotCommandScopeAllChatAdministrators: [*players, "resettop"],
    }
