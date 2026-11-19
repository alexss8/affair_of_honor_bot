"""Типизированные callback_data для inline-кнопок (вместо ручной склейки и split("_"))."""

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData

from bot.domain.models import MoveKind, Zone


class InviteAction(StrEnum):
    ACCEPT = "accept"
    DECLINE = "decline"
    REVOKE = "revoke"


class InviteCb(CallbackData, prefix="inv"):
    """Кнопки приглашения. Кто кого вызвал, хранится в движке по (chat_id, message_id)."""

    action: InviteAction


class MoveCb(CallbackData, prefix="mv"):
    kind: MoveKind
    zone: Zone


class SurrenderCb(CallbackData, prefix="surrender"):
    pass


class DebugAction(StrEnum):
    BOT_DUEL = "bot_duel"
    EVIL_NAME_DUEL = "evil_duel"
    FLOOD = "flood"


class DebugCb(CallbackData, prefix="dbg"):
    """Кнопки отладочного стенда (/debug, только при DEBUG_TOOLS)."""

    action: DebugAction
