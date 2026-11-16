import html
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from models import DuelSession, Player, PlayerStats

DICE_EMOJI = '<tg-emoji emoji-id="5427009714745517609">🎲</tg-emoji>'

def escape_nick(user) -> str:
    raw = f"@{user.username}" if user.username else user.first_name
    return html.escape(raw)

def invite_keyboard(p1_id: int, p2_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚔️ Принять вызов", callback_data=f"accept_{p1_id}_{p2_id}"),
            InlineKeyboardButton(text="🏳️ Отказаться", callback_data=f"decline_{p1_id}_{p2_id}"),
        ],
        [
            InlineKeyboardButton(text="🚫 Отозвать вызов", callback_data=f"revoke_{p1_id}_{p2_id}"),
        ],
    ])

def render_invite(p1_name: str, p2_name: str) -> str:
    return f"🔥 <b>{p1_name}</b> вызывает <b>{p2_name}</b> на дуэль! ⚔️"

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

    return InlineKeyboardMarkup(inline_keyboard=[
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
    ])

def render_battle(session: DuelSession, log: str = "", is_rolling: bool = False) -> str:
    def hp_bar(hp: int) -> str:
        return "❤️" * hp + "🖤" * (session.max_hp - hp)

    active_log = log or session.last_log
    log_block = f"📜 <i>{active_log}</i>\n\n" if active_log else ""

    att_icon = "✅" if session.attacker.choice else "🔴"
    def_icon = "✅" if session.defender.choice else "🟢"
    rolling_line = f"\n\n{DICE_EMOJI} <i>Бросаем кубики судьбы...</i>" if is_rolling else ""

    return (
        f"⚔️ <b>ДУЭЛЬ</b> ⚔️\n\n"
        f"{session.p1.name}: {hp_bar(session.p1.hp)}\n"
        f"{session.p2.name}: {hp_bar(session.p2.hp)}\n\n"
        f"{log_block}"
        f"{att_icon} Атакует: <b>{session.attacker.name}</b>\n"
        f"{def_icon} Защищается: <b>{session.defender.name}</b>"
        f"{rolling_line}"
    )

def render_top(stats: list[PlayerStats], limit: int = 10) -> str:
    if not stats:
        return "🏆 В этом чате еще не было дуэлей. Станьте первыми!"
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 <b>ТОП-{limit} ДУЭЛЯНТОВ ЧАТА</b> 🏆\n"]
    for i, s in enumerate(stats, 1):
        prefix = medals[i - 1] if i <= 3 else f"<b>{i}.</b>"
        lines.append(f"{prefix} {s.nickname} — <b>{s.wins}</b> побед / {s.losses} пор. ({s.winrate}%)")
    return "\n".join(lines)

def render_victory(duel_id: int, winner: Player, reason: str, stats: list[PlayerStats], limit: int = 10, icon: str = "🏆") -> str:
    return (
        f"{icon} <b>ДУЭЛЬ #{duel_id} ОКОНЧЕНА!</b>\n\n"
        f"📜 <i>{reason}</i>\n\n"
        f"Победитель: <b>{winner.name}</b> (+1 в рейтинг)\n\n"
        f"{render_top(stats, limit)}"
    )

def render_draw(reason: str, stats: list[PlayerStats], limit: int = 10, icon: str = "🤝") -> str:
    return (
        f"{icon} <b>ДУЭЛЬ ОКОНЧЕНА ВНИЧЬЮ!</b>\n\n"
        f"📜 <i>{reason}</i>\n\n"
        f"Рейтинг игроков не изменился.\n\n"
        f"{render_top(stats, limit)}"
    )

def render_help(default_hp: int, top_limit: int) -> str:
    return (
        "⚔️ <b>ДУЭЛЬНЫЙ БОТ — ПРАВИЛА И КОМАНДЫ</b>\n\n"
        "<b>Как начать бой:</b>\n"
        "• Напишите <code>/duel @ник</code> (выбрав соперника из списка)\n"
        "• Или ответьте (Reply) на любое сообщение соперника командой /duel\n\n"
        "<b>Правила дуэли:</b>\n"
        f"• У каждого бойца по <b>{default_hp} жизней</b>.\n"
        "• Игроки ходят по очереди: один атакует (сверху или снизу), второй защищается.\n"
        "• Выбор делается вслепую через кнопки под сообщением.\n"
        "• Если блок совпал с ударом — урон не проходит. Если нет — защитник теряет 1 жизнь.\n"
        "• На весь бой отводится <b>5 минут</b>. Если время выйдет, победит боец с большим запасом жизней.\n\n"
        "<b>Команды чата:</b>\n"
        "/duel @ник — вызвать на бой\n"
        "/cancel — отменить свой текущий бой (если соперник пропал)\n"
        f"/top — топ-{top_limit} дуэлянтов чата\n"
        "/help — показать эту инструкцию"
    )
