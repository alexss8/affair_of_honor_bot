import html
import math

from bot.domain.models import DuelInvite, DuelSession, OutcomeKind, Player, PlayerStats
from bot.services.duel_service import FinishedDuel

FALLBACK_NICK = "Дуэлянт"


def user_nick(user) -> str:
    """Отображаемый ник игрока (@username или имя) — сырой: экранируется только при вставке в HTML."""
    return f"@{user.username}" if user.username else (user.first_name or FALLBACK_NICK)


NAME_MAX = 24  # длиннее имя в подписях карточек и кнопках не показываем (= потолок псевдонима, 7.3)


def short_name(name: str) -> str:
    """Имя, обрезанное до NAME_MAX символов с «…» — чтобы длинный ник не ломал вёрстку карточки (сырое)."""
    if len(name) <= NAME_MAX:
        return name
    return name[: NAME_MAX - 1].rstrip() + "…"


def _h(text: str) -> str:
    """Сырой текст (ник, реплика раунда) для вставки в HTML-сообщение."""
    return html.escape(text)


def _name(name: str) -> str:
    """Имя для подписи в HTML-карточке: обрезанное и экранированное."""
    return _h(short_name(name))


def render_invite(p1_name: str, p2_name: str) -> str:
    return f"🔥 <b>{_name(p1_name)}</b> вызывает <b>{_name(p2_name)}</b> на дуэль! ⚔️"


INVITE_EXPIRED = "⌛ Вызов на дуэль истёк — соперник не ответил вовремя."
_PAUSE_NOTE = "\n\n⏸ <i>Бот перезапускается — {what} продолжится сразу после запуска, время на паузе.</i>"


def render_paused(session: DuelSession) -> str:
    return render_battle(session) + _PAUSE_NOTE.format(what="бой")


def render_invite_paused(invite: DuelInvite) -> str:
    return render_invite(invite.challenger_name, invite.target_name) + _PAUSE_NOTE.format(what="вызов")


def render_cancelled(session: DuelSession, canceler_name: str) -> str:
    return (
        f"🚫 <b>ДУЭЛЬ ОТМЕНЕНА</b>\n\n<i>Бой между {_name(session.p1.name)} и {_name(session.p2.name)} "
        f"аннулирован игроком {_name(canceler_name)} (без изменения рейтинга).</i>"
    )


def render_interrupted(session: DuelSession) -> str:
    return (
        f"⚠️ <b>БОЙ ПРЕРВАН</b>\n\n<i>Бой между {_name(session.p1.name)} и {_name(session.p2.name)} "
        "прерван перезапуском бота, рейтинг не изменён.</i>"
    )


def render_invite_interrupted(invite: DuelInvite) -> str:
    return (
        f"⚠️ Вызов {_name(invite.challenger_name)} → {_name(invite.target_name)} снят: бот перезапускался. "
        "Брось вызов заново: /duel"
    )


def render_battle(session: DuelSession) -> str:
    def hp_bar(hp: int) -> str:
        return "❤️" * hp + "🖤" * (session.max_hp - hp)

    log_block = f"<i>{_h(session.last_log)}</i>\n\n" if session.last_log else ""

    att_icon = "✅" if session.attacker.choice else "🔴"
    def_icon = "✅" if session.defender.choice else "🟢"

    return (
        f"⚔️ <b>ДУЭЛЬ</b> ⚔️\n\n"
        # Жизни перед именем: полоска фиксированной длины не рвётся, если имя не влезет в строку
        f"{hp_bar(session.p1.hp)} {_name(session.p1.name)}\n"
        f"{hp_bar(session.p2.hp)} {_name(session.p2.name)}\n\n"
        f"{log_block}"
        f"{att_icon} Атакует: <b>{_name(session.attacker.name)}</b>\n"
        f"{def_icon} Защищается: <b>{_name(session.defender.name)}</b>"
    )


def render_top(stats: list[PlayerStats], limit: int = 10) -> str:
    if not stats:
        return "🏆 В этом чате еще не было дуэлей. Станьте первыми!"
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 <b>ТОП-{limit} ДУЭЛЯНТОВ ЧАТА</b> 🏆\n"]
    for i, s in enumerate(stats, 1):
        prefix = medals[i - 1] if i <= 3 else f"<b>{i}.</b>"
        wins = plural(s.wins, "победа", "победы", "побед")
        lines.append(f"{prefix} {_name(s.nickname)} — <b>{s.wins}</b> {wins} / {s.losses} пор. ({s.winrate}%)")
    return "\n".join(lines)


def render_victory(
    duel_id: int, winner: Player, reason: str, stats: list[PlayerStats], limit: int = 10, icon: str = "🏆"
) -> str:
    return (
        f"{icon} <b>ДУЭЛЬ #{duel_id} ОКОНЧЕНА!</b>\n\n"
        f"<i>{_h(reason)}</i>\n\n"
        f"Победитель: <b>{_name(winner.name)}</b> (+1 в рейтинг)\n\n"
        f"{render_top(stats, limit)}"
    )


def render_draw(reason: str, stats: list[PlayerStats], limit: int = 10, icon: str = "🤝") -> str:
    return (
        f"{icon} <b>ДУЭЛЬ ОКОНЧЕНА ВНИЧЬЮ!</b>\n\n"
        f"<i>{_h(reason)}</i>\n\n"
        f"Рейтинг игроков не изменился.\n\n"
        f"{render_top(stats, limit)}"
    )


def render_outcome(finished: FinishedDuel, limit: int) -> str:
    """Итоговая карточка для любого исхода боя."""
    outcome = finished.outcome
    winner, loser = outcome.winner, outcome.loser
    if outcome.kind is OutcomeKind.DRAW:
        reason = f"⏰ Время дуэли вышло! У обоих бойцов осталось по {outcome.session.p1.hp} ❤️."
        return render_draw(reason, finished.top, limit, icon="⏰")
    if outcome.kind is OutcomeKind.SURRENDER:
        reason, icon = f"{short_name(loser.name)} выбросил белый флаг и сдался!", "🏳️"
    elif outcome.kind is OutcomeKind.TIMEOUT:
        reason = (
            f"⏰ Время дуэли вышло! {short_name(winner.name)} ({winner.hp} ❤️) "
            f"оказался сильнее {short_name(loser.name)} ({loser.hp} ❤️)."
        )
        icon = "⏰"
    else:
        reason, icon = outcome.session.last_log, "🏆"
    return render_victory(finished.duel_id, winner, reason, finished.top, limit, icon=icon)


DEBUG_MENU = (
    "🛠 <b>Отладочный стенд</b>\n\n"
    "🤖 <b>Дуэль с ботом</b> — бой против бота: он ходит сам, когда ходишь ты.\n"
    "😈 <b>Дуэль со злым именем</b> — то же, у соперника имя с HTML, эмодзи и длиной под 60 символов.\n"
    "🐌 <b>Имитировать 429</b> — лимитер получает ответ 429: чат на паузе, лимит групп откатывается."
)
DEBUG_BUSY = "Ты уже в бою — заверши его или /cancel."
DEBUG_CARD_PLACEHOLDER = "⚔️ Готовим бой…"


def render_debug_flood(retry_after: int) -> str:
    return f"Чат на паузе {retry_after} с — сделай ход в бою, увидишь подсказку о притормаживании."


def render_debug_already_paused(wait_sec: float) -> str:
    return f"Чат уже на паузе ещё ~{math.ceil(wait_sec)} с — новая пауза не нужна."


TOP_RESET_DENIED = "🛡 Обнулять топ могут только админы группы."
TOP_RESET_NOT_YOURS = "Эта кнопка не для вас."
TOP_RESET_CONFIRM = (
    "🧹 <b>Обнулить топ этого чата?</b>\n\n<i>Победы и поражения всех игроков будут стёрты, вернуть их нельзя.</i>"
)
TOP_RESET_CANCELLED = "↩️ Сброс топа отменён."


def render_top_reset_done(admin_name: str) -> str:
    return f"🧹 <b>Топ обнулён</b> админом {_name(admin_name)}. Ристалище ждёт новых героев! ⚔️"


ERROR_NOTICE = "⚠️ Что-то сломалось на нашей стороне. Попробуй ещё раз; если бой завис — /cancel"


def render_slow_choice(wait_sec: float) -> str:
    """Ответ на ход, когда карточка обновится не сразу: у чата пауза после 429 или длинная очередь."""
    return (
        "⏳ Выбор принят! Telegram притормаживает бота — слишком много боёв сразу, "
        f"карточка обновится через ~{math.ceil(wait_sec)} с"
    )


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение: plural(1, "жизнь", "жизни", "жизней") -> "жизнь"."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_duration(seconds: float) -> str:
    """300 -> "5 минут", 90 -> "90 секунд"."""
    total = int(seconds)
    if total >= 60 and total % 60 == 0:
        minutes = total // 60
        return f"{minutes} {plural(minutes, 'минута', 'минуты', 'минут')}"
    return f"{total} {plural(total, 'секунда', 'секунды', 'секунд')}"


def render_help(default_hp: int, top_limit: int, duel_timeout_sec: float) -> str:
    return (
        "⚔️ <b>ДУЭЛЬНЫЙ БОТ — ПРАВИЛА И КОМАНДЫ</b>\n\n"
        "<b>Как начать бой:</b>\n"
        "• Напишите <code>/duel @ник</code> (выбрав соперника из списка)\n"
        "• Или ответьте (Reply) на любое сообщение соперника командой /duel\n\n"
        "<b>Правила дуэли:</b>\n"
        f"• У каждого бойца по <b>{default_hp} {plural(default_hp, 'жизни', 'жизни', 'жизней')}</b>.\n"
        "• Игроки ходят по очереди: один атакует (сверху или снизу), второй защищается.\n"
        "• Выбор делается вслепую через кнопки под сообщением.\n"
        "• Если блок совпал с ударом — урон не проходит. Если нет — защитник теряет 1 жизнь.\n"
        f"• На весь бой отводится <b>{format_duration(duel_timeout_sec)}</b>. "
        "Если время выйдет, победит боец с большим запасом жизней.\n\n"
        "<b>Команды чата:</b>\n"
        "/duel @ник — вызвать на бой\n"
        "/cancel — отменить свой текущий бой (если соперник пропал)\n"
        f"/top — топ-{top_limit} дуэлянтов чата\n"
        "/help — показать эту инструкцию\n"
        "/resettop — обнулить топ чата (для админов)"
    )
