"""Сброс топа чата админом: /resettop → подтверждение кнопкой."""

import logging
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import GetChatMember, SetMyCommands
from aiogram.types import (
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
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
from bot.telegram.callbacks import ConfirmAction, TopResetCb
from bot.telegram.handlers import debug, router
from bot.telegram.ui import texts

OTHER_CHAT = -2002
OWNER_ID = 777
CARD_ID = 900


def _confirm(admin: User) -> str:
    return TopResetCb(action=ConfirmAction.CONFIRM, admin_id=admin.id).pack()


def _cancel(admin: User) -> str:
    return TopResetCb(action=ConfirmAction.CANCEL, admin_id=admin.id).pack()


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


PLAYER_COMMANDS = ["duel", "cancel", "top", "ttop", "help"]
SERVICE_COMMANDS = ("/resettop", "/tstart", "/tend", "/status", "/admin", "/debug")


async def _start_bot(tmp_path, admin_id: int | None, set_commands: AsyncMock) -> AsyncMock:
    """Запускает main() без сети; возвращает подменённый start_polling."""
    config = Config(
        bot_token=TOKEN, admin_id=admin_id, db_path=str(tmp_path / "main.db"), log_file=str(tmp_path / "main.log")
    )
    start_polling = AsyncMock()
    with (
        patch.object(bot_main, "setup_logger", return_value=logging.getLogger("top_reset")),
        patch.object(Bot, "set_my_commands", set_commands),
        patch.object(Bot, "delete_webhook", AsyncMock()),
        patch.object(Dispatcher, "start_polling", start_polling),
    ):
        try:
            await bot_main.main(config)
        finally:
            router._parent_router = None
    return start_polling


def _menus(set_commands: AsyncMock) -> dict:
    return {
        c.kwargs["scope"].model_dump_json(exclude_none=True): [cmd.command for cmd in c.kwargs["commands"]]
        for c in set_commands.await_args_list
    }


GROUP_SCOPE = BotCommandScopeAllGroupChats().model_dump_json(exclude_none=True)
OWNER_SCOPE = BotCommandScopeChat(chat_id=OWNER_ID).model_dump_json(exclude_none=True)


async def test_service_commands_are_only_in_owner_private_menu(tmp_path):
    set_commands = AsyncMock()
    await _start_bot(tmp_path, OWNER_ID, set_commands)

    assert _menus(set_commands) == {GROUP_SCOPE: PLAYER_COMMANDS, OWNER_SCOPE: ["admin", "status"]}


async def test_no_owner_menu_without_admin_id(tmp_path):
    set_commands = AsyncMock()
    await _start_bot(tmp_path, None, set_commands)

    assert _menus(set_commands) == {GROUP_SCOPE: PLAYER_COMMANDS}


async def test_bot_starts_when_owner_never_wrote_to_bot(tmp_path):
    async def set_my_commands(commands, scope):
        if isinstance(scope, BotCommandScopeChat):
            raise TelegramBadRequest(SetMyCommands(commands=commands, scope=scope), "Bad Request: chat not found")

    start_polling = await _start_bot(tmp_path, OWNER_ID, AsyncMock(side_effect=set_my_commands))

    start_polling.assert_awaited_once()


def test_help_has_no_service_commands():
    text = texts.render_help(default_hp=3, top_limit=10, duel_timeout_sec=300)

    assert [cmd for cmd in SERVICE_COMMANDS if cmd in text] == []


@pytest.mark.parametrize("debug_tools", [False, True])
async def test_owner_gets_service_commands_in_private(tmp_path, debug_tools):
    repo = SQLiteRepository(str(tmp_path / "owner.db"))
    repo.init()
    logger = logging.getLogger("top_reset")
    config = Config(bot_token=TOKEN, admin_id=OWNER_ID, debug_tools=debug_tools)
    bot = AsyncMock(spec=Bot)
    bot.id = 999
    dp = build_dispatcher(config, repo, DuelEngine(logger=logger), logger)
    owner = User(id=OWNER_ID, is_bot=False, first_name="Owner")
    try:
        await dp.feed_update(bot, make_msg_update(1, owner, "/admin", private=True))
        await dp.feed_update(bot, make_msg_update(2, ALICE, "/admin", private=True))  # not the owner
        await dp.feed_update(bot, make_msg_update(3, owner, "/admin"))  # in a group
    finally:
        router._parent_router = None
        debug.router._parent_router = None

    (sent,) = sent_messages(bot)
    assert sent.chat_id == OWNER_ID
    assert sent.text == texts.render_owner_commands(debug_tools)
    assert ("/debug" in sent.text) is debug_tools
    assert "/resettop" in sent.text
    assert "/status" in sent.text
