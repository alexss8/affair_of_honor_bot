from aiogram import Router
from aiogram.types import CallbackQuery

from bot.telegram.safe_api import safe_answer_callback

router = Router(name="fallback")


@router.callback_query()
async def handle_stale_button(cb: CallbackQuery):
    """Кнопки, которые никто не обработал (например, старого формата после обновления бота)."""
    await safe_answer_callback(cb, "Эта кнопка устарела. Начни новый вызов: /duel", show_alert=True)
