from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.domain.models import DuelSession, MoveKind, Zone
from bot.telegram.callbacks import InviteAction, InviteCb, MoveCb, SurrenderCb


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


def _move_button(text: str, kind: MoveKind, zone: Zone, style: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MoveCb(kind=kind, zone=zone).pack(), style=style)


def battle_keyboard(session: DuelSession) -> InlineKeyboardMarkup:
    if session.surrender_confirm_id is not None:
        footer_text = "⚠️ Точно сдаться? (нажми еще раз)"
    elif session.p1.choice and not session.p2.choice:
        footer_text = f"⏳ Ждем {session.p2.name}... (Сдаться)"
    elif session.p2.choice and not session.p1.choice:
        footer_text = f"⏳ Ждем {session.p1.name}... (Сдаться)"
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
