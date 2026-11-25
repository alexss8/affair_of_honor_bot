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


class ConfirmAction(StrEnum):
    """Кнопки «Да / Отмена» под необратимым действием админа."""

    CONFIRM = "confirm"
    CANCEL = "cancel"


class TopResetCb(CallbackData, prefix="topreset"):
    """Кнопки подтверждения сброса топа (/resettop); нажать может только admin_id — кто вызвал команду."""

    action: ConfirmAction
    admin_id: int


class TournamentEndCb(CallbackData, prefix="tend"):
    """Кнопки подтверждения закрытия турнира (/tend); нажать может только admin_id — кто вызвал команду.

    tournament_id — какой турнир закрываем: старая карточка не закроет турнир, открытый после неё.
    """

    action: ConfirmAction
    admin_id: int
    tournament_id: int
