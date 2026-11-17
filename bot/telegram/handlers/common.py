from aiogram import Router
from aiogram.filters import IS_MEMBER, IS_NOT_MEMBER, ChatMemberUpdatedFilter, Command
from aiogram.types import ChatMemberUpdated, Message

from bot.config import Config
from bot.storage.base import BaseRepository
from bot.telegram.safe_api import safe_answer
from bot.telegram.ui import texts

router = Router(name="common")


def _help_text(config: Config) -> str:
    return texts.render_help(config.default_hp, config.top_limit, config.duel_timeout_sec)


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_NOT_MEMBER >> IS_MEMBER))
async def on_bot_added(event: ChatMemberUpdated, config: Config):
    greeting = "👋 Всем привет! Я готов проводить дуэли в этом чате.\n\n"
    await safe_answer(event, greeting + _help_text(config))


@router.message(Command("help", "start"))
async def cmd_help(message: Message, config: Config):
    await safe_answer(message, _help_text(config))


@router.message(Command("top"))
async def cmd_top(message: Message, repo: BaseRepository, config: Config):
    stats = repo.get_top(message.chat.id, config.top_limit)
    await safe_answer(message, texts.render_top(stats, config.top_limit))
