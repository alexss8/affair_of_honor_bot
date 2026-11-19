"""Отладочный стенд для тестового бота (DEBUG_TOOLS=1, см. TEST_STAND.md): команда /debug только для ADMIN_ID.

Роутер подключается раньше корневого (`include_handlers`): ход за бота-соперника делается до обычного
`handle_move`, после чего нажатие уходит дальше (SkipHandler) — бой идёт по настоящему коду, а не по копии.
"""

import contextlib
import logging
import random
from typing import Any

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.domain.models import MoveKind, Zone
from bot.services.duel_service import DuelService
from bot.telegram.callbacks import DebugAction, DebugCb, MoveCb
from bot.telegram.duel_timer import open_battle
from bot.telegram.handlers.battle import SLOW_HINT_SEC
from bot.telegram.middlewares import Handler, log_context_middleware
from bot.telegram.rate_limiter import RateLimiter
from bot.telegram.safe_api import safe_answer, safe_answer_callback
from bot.telegram.ui import keyboards, texts

# Настоящие Telegram ID положительны — с живым игроком бот-соперник не совпадёт.
# У каждого соперника свой ID: статистика считается по ID, общий склеил бы их в топе в одну строку.
BOT_OPPONENT_ID = -1
EVIL_OPPONENT_ID = -2
_OPPONENTS = {
    DebugAction.BOT_DUEL: (BOT_OPPONENT_ID, "🤖 Бот-дуэлянт"),
    DebugAction.EVIL_NAME_DUEL: (EVIL_OPPONENT_ID, "<b>Tom & Jerry</b> 🤖 " + "Я" * 40),
}
_OPPONENT_IDS = frozenset(opponent_id for opponent_id, _ in _OPPONENTS.values())
FLOOD_RETRY_SEC = 30


INCOMING_PREVIEW_LEN = 80

logger = logging.getLogger("duel_bot")


def _is_admin(event: TelegramObject, config: Config) -> bool:
    user = getattr(event, "from_user", None)
    return user is not None and user.id == config.admin_id


async def log_incoming_middleware(handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
    """Строка в лог на каждое входящее нажатие и сообщение (и чужое): видно, что именно нажал игрок."""
    if isinstance(event, CallbackQuery):
        what = f"нажатие {event.data}"
    else:
        text = " ".join((getattr(event, "text", None) or "").split())
        what = f"сообщение «{text[:INCOMING_PREVIEW_LEN]}»"
    user = getattr(event, "from_user", None)
    logger.info("Входящее: %s от %s", what, user.id if user else "-")
    return await handler(event, data)


router = Router(name="debug")
router.message.filter(_is_admin)
router.callback_query.filter(_is_admin)
# Outer middlewares run for every event reaching this router, before its filters; log_context goes first
for observer in (router.message, router.callback_query):
    observer.outer_middleware(log_context_middleware)
    observer.outer_middleware(log_incoming_middleware)


@router.message(Command("debug"))
async def cmd_debug(message: Message):
    await safe_answer(message, texts.DEBUG_MENU, reply_markup=keyboards.debug_keyboard())


@router.callback_query(DebugCb.filter(F.action.in_(set(_OPPONENTS))))
async def handle_bot_duel(
    cb: CallbackQuery, callback_data: DebugCb, engine: DuelEngine, service: DuelService, config: Config
):
    chat_id = cb.message.chat.id
    if engine.is_user_busy(chat_id, cb.from_user.id):
        return await safe_answer_callback(cb, texts.DEBUG_BUSY, show_alert=True)
    await safe_answer_callback(cb)
    card = await safe_answer(cb.message, texts.DEBUG_CARD_PLACEHOLDER)
    if card is None:
        return
    opponent_id, opponent_name = _OPPONENTS[callback_data.action]
    session = engine.start_duel(
        chat_id, card.message_id, cb.from_user.id, texts.user_nick(cb.from_user), opponent_id, opponent_name
    )
    await open_battle(card, session, engine, service, config.duel_timeout_sec)


@router.callback_query(MoveCb.filter())
async def bot_opponent_moves_first(cb: CallbackQuery, engine: DuelEngine):
    """Бот выбирает ход раньше игрока, поэтому ход игрока сразу рассчитывает раунд в обычном handle_move."""
    session = engine.get_session(cb.message.chat.id, cb.message.message_id)
    opponent = next((p for p in (session.p1, session.p2) if p.id in _OPPONENT_IDS), None) if session else None
    if opponent is not None and opponent.choice is None and session.get_player(cb.from_user.id) is not None:
        kind = MoveKind.ATTACK if opponent.id == session.attacker_id else MoveKind.DEFEND
        engine.choose(session, opponent, kind, random.choice(list(Zone)))
    raise SkipHandler


async def _answer_flood(bot: Bot, method: SendMessage) -> Any:
    raise TelegramRetryAfter(method=method, message="debug: simulated flood", retry_after=FLOOD_RETRY_SEC)


@router.callback_query(DebugCb.filter(F.action == DebugAction.FLOOD))
async def handle_flood(cb: CallbackQuery, bot: Bot, limiter: RateLimiter):
    """Прогоняет через лимитер запрос, на который «Telegram» ответил 429 — срабатывает настоящая реакция лимитера.

    Если чат уже притормаживает, новая пауза не накладывается: подставной запрос простоял бы в очереди до конца
    паузы, ответ на нажатие опоздал бы, а лимит группы снизился бы ещё раз.
    """
    chat_id = cb.message.chat.id
    wait_sec = limiter.estimate_wait(chat_id)
    if wait_sec >= SLOW_HINT_SEC:
        return await safe_answer_callback(cb, texts.render_debug_already_paused(wait_sec), show_alert=True)
    # Answer first: the callback query expires while the request below waits for a slot
    await safe_answer_callback(cb, texts.render_debug_flood(FLOOD_RETRY_SEC), show_alert=True)
    with contextlib.suppress(TelegramRetryAfter):  # 429 пробрасывается лимитером дальше, как и настоящий
        await limiter(_answer_flood, bot, SendMessage(chat_id=chat_id, text="debug"))
