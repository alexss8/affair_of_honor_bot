from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.domain.models import DuelSession


def invite_keyboard(p1_id: int, p2_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚔️ Принять вызов", callback_data=f"accept_{p1_id}_{p2_id}"),
                InlineKeyboardButton(text="🏳️ Отказаться", callback_data=f"decline_{p1_id}_{p2_id}"),
            ],
            [
                InlineKeyboardButton(text="🚫 Отозвать вызов", callback_data=f"revoke_{p1_id}_{p2_id}"),
            ],
        ]
    )


def battle_keyboard(session: DuelSession, is_rolling: bool = False) -> InlineKeyboardMarkup:
    if is_rolling:
        footer_text = "🎲 Бросок кубиков..."
    elif session.surrender_confirm_id is not None:
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
                InlineKeyboardButton(text="🛡 Блок сверху", callback_data="def_top", style="success"),
                InlineKeyboardButton(text="⚔️ Удар сверху", callback_data="act_top", style="danger"),
            ],
            [
                InlineKeyboardButton(text="🛡 Блок снизу", callback_data="def_bot", style="success"),
                InlineKeyboardButton(text="⚔️ Удар снизу", callback_data="act_bot", style="danger"),
            ],
            [
                InlineKeyboardButton(text=footer_text, callback_data="surrender"),
            ],
        ]
    )
