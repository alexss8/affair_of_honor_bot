from collections.abc import Callable

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.domain.models import DuelSession, MoveKind, Zone
from bot.telegram.callbacks import (
    ConfirmAction,
    DebugAction,
    DebugCb,
    InviteAction,
    InviteCb,
    MoveCb,
    SurrenderCb,
    TopResetCb,
    TournamentEndCb,
)
from bot.telegram.ui.texts import short_name


def debug_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        ("🤖 Дуэль с ботом", DebugAction.BOT_DUEL),
        ("😈 Дуэль со злым именем", DebugAction.EVIL_NAME_DUEL),
        ("🐌 Имитировать 429", DebugAction.FLOOD),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=DebugCb(action=action).pack())] for text, action in buttons
        ]
    )


def invite_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚔️ Принять вызов", callback_data=InviteCb(action=InviteAction.ACCEPT).pack()),
                InlineKeyboardButton(text="🏳️ Отказаться", callback_data=InviteCb(action=InviteAction.DECLINE).pack()),
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Отозвать вызов", callback_data=InviteCb(action=InviteAction.REVOKE).pack()
                ),
            ],
        ]
    )


def _confirm_keyboard(confirm_text: str, make_cb: Callable[[ConfirmAction], CallbackData]) -> InlineKeyboardMarkup:
    """«Да / Отмена» под необратимым действием."""

    def button(text: str, action: ConfirmAction) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=make_cb(action).pack())

    return InlineKeyboardMarkup(
        inline_keyboard=[[button(confirm_text, ConfirmAction.CONFIRM), button("Отмена", ConfirmAction.CANCEL)]]
    )


def top_reset_keyboard(admin_id: int) -> InlineKeyboardMarkup:
    return _confirm_keyboard("🧹 Да, обнулить", lambda action: TopResetCb(action=action, admin_id=admin_id))


def tournament_end_keyboard(admin_id: int, tournament_id: int) -> InlineKeyboardMarkup:
    return _confirm_keyboard(
        "🏁 Да, закрыть",
        lambda action: TournamentEndCb(action=action, admin_id=admin_id, tournament_id=tournament_id),
    )


def _move_button(text: str, kind: MoveKind, zone: Zone, style: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MoveCb(kind=kind, zone=zone).pack(), style=style)


def battle_keyboard(session: DuelSession) -> InlineKeyboardMarkup:
    if session.surrender_confirm_id is not None:
        footer_text = "⚠️ Точно сдаться? (нажми еще раз)"
    elif session.p1.choice and not session.p2.choice:
        footer_text = f"⏳ Ждем {short_name(session.p2.name)}... (Сдаться)"
    elif session.p2.choice and not session.p1.choice:
        footer_text = f"⏳ Ждем {short_name(session.p1.name)}... (Сдаться)"
    else:
        footer_text = "🏳️ Сдаться"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _move_button("🛡 Блок сверху", MoveKind.DEFEND, Zone.TOP, "success"),
                _move_button("⚔️ Удар сверху", MoveKind.ATTACK, Zone.TOP, "danger"),
            ],
            [
                _move_button("🛡 Блок снизу", MoveKind.DEFEND, Zone.BOT, "success"),
                _move_button("⚔️ Удар снизу", MoveKind.ATTACK, Zone.BOT, "danger"),
            ],
            [
                InlineKeyboardButton(text=footer_text, callback_data=SurrenderCb().pack()),
            ],
        ]
    )
