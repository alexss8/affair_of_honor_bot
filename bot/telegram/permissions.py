"""Права на служебные команды в группе (/resettop, турниры)."""

from aiogram import Bot
from aiogram.enums import ChatMemberStatus

from bot.config import Config
from bot.telegram.safe_api import safe_get_chat_member

_GROUP_ADMIN_STATUSES = {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int, config: Config) -> bool:
    """Владелец бота (ADMIN_ID) или админ этой группы; права проверяются у Telegram."""
    if config.admin_id and user_id == config.admin_id:
        return True
    member = await safe_get_chat_member(bot, chat_id, user_id)
    return member is not None and member.status in _GROUP_ADMIN_STATUSES
