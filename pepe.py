import sqlite3
import datetime
import pytz
import time
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

TOKEN = "8019928524:AAEA_SYAPK21AQmWR1aAB_u6dqvJq4vUaW8"
DB_PATH = "stats.db"
TIMEZONE = pytz.timezone("Europe/Moscow")

# Ранги для модерации (назначаются вручную)
RANKS = {
    0: "👤 Пользователь",
    1: "🔰 Стажёр",
    2: "🛡 Младший модератор",
    3: "⚔ Модератор",
    4: "📺 Модератор Twitch",
    5: "🎬 Модератор YouTube",
    6: "🎵 Модератор TikTok",
    7: "🔱 Заместитель",
    8: "👑 Владелец"
}

# Уровни активности (выдаются автоматически)
LEVEL_NAMES = {
    0: "🌟 Без уровня",
    1: "🌱 Новичок",
    2: "📚 Начинающий",
    3: "⚡ Активный",
    4: "🧠 Эксперт",
    5: "🏆 Легенда",
    6: "🔥 Безумец"
}

RANK_REQUIREMENTS = {
    "warn": 1,
    "mute": 2,
    "kick": 3,
    "ban": 7,
    "unwarn": 2,
    "unmute": 2,
    "unban": 7,
    "setrank": 8
}

# Лимиты репутации в сутки
REP_PLUS_LIMIT = 5
REP_MINUS_LIMIT = 3

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # Включаем WAL-режим для уменьшения блокировок
    conn.execute("PRAGMA journal_mode=WAL;")
    # Таблица статистики пользователей
    conn.execute('''CREATE TABLE IF NOT EXISTS user_stats (
        chat_id INTEGER,
        user_id INTEGER,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        total INTEGER DEFAULT 0,
        daily INTEGER DEFAULT 0,
        weekly INTEGER DEFAULT 0,
        last_date TEXT,
        last_week INTEGER,
        exp REAL DEFAULT 0,
        first_seen INTEGER,
        last_active INTEGER,
        rank INTEGER DEFAULT 0,
        warns INTEGER DEFAULT 0,
        reputation_plus INTEGER DEFAULT 0,
        reputation_minus INTEGER DEFAULT 0,
        last_reputation_date TEXT,
        plus_given_today INTEGER DEFAULT 0,
        minus_given_today INTEGER DEFAULT 0,
        clan_id INTEGER DEFAULT NULL,
        level INTEGER DEFAULT 0,
        monthly INTEGER DEFAULT 0,
        last_month INTEGER,
        awards TEXT DEFAULT '',
        PRIMARY KEY (chat_id, user_id)
    )''')
    # Таблица кланов
    conn.execute('''CREATE TABLE IF NOT EXISTS clans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        name TEXT UNIQUE,
        tag TEXT,
        leader_id INTEGER,
        created_at INTEGER,
        FOREIGN KEY(chat_id, leader_id) REFERENCES user_stats(chat_id, user_id)
    )''')
    # Проверка и добавление недостающих колонок в user_stats
    cursor = conn.execute("PRAGMA table_info(user_stats)")
    columns = [col[1] for col in cursor.fetchall()]
    if "exp" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN exp REAL DEFAULT 0")
    if "first_seen" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN first_seen INTEGER")
    if "last_active" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN last_active INTEGER")
    if "rank" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN rank INTEGER DEFAULT 0")
    if "warns" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN warns INTEGER DEFAULT 0")
    if "reputation_plus" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN reputation_plus INTEGER DEFAULT 0")
    if "reputation_minus" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN reputation_minus INTEGER DEFAULT 0")
    if "last_reputation_date" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN last_reputation_date TEXT")
    if "plus_given_today" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN plus_given_today INTEGER DEFAULT 0")
    if "minus_given_today" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN minus_given_today INTEGER DEFAULT 0")
    if "clan_id" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN clan_id INTEGER DEFAULT NULL")
    if "level" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN level INTEGER DEFAULT 0")
    if "monthly" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN monthly INTEGER DEFAULT 0")
    if "last_month" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN last_month INTEGER")
    if "awards" not in columns:
        conn.execute("ALTER TABLE user_stats ADD COLUMN awards TEXT DEFAULT ''")
    conn.commit()
    conn.close()

def get_current_date():
    return datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d")

def get_current_week():
    dt = datetime.datetime.now(TIMEZONE)
    year, week, _ = dt.isocalendar()
    return year * 100 + week

def get_current_month():
    dt = datetime.datetime.now(TIMEZONE)
    return dt.year * 100 + dt.month

def format_time_ago(timestamp):
    if timestamp is None:
        return "никогда"
    now = time.time()
    diff = now - timestamp
    if diff < 60:
        return "только что"
    elif diff < 3600:
        minutes = int(diff // 60)
        return f"{minutes} мин. назад"
    elif diff < 86400:
        hours = int(diff // 3600)
        return f"{hours} ч. назад"
    else:
        days = int(diff // 86400)
        hours = int((diff % 86400) // 3600)
        return f"{days} дн. {hours} ч. назад"

def format_duration(seconds):
    if seconds is None:
        return "неизвестно"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    return f"{days} дн. {hours} ч."

def calculate_exp(text):
    if not text:
        return 0.0
    letter_count = sum(1 for ch in text if ch.isalpha())
    return (letter_count // 3) * 0.30

def update_level(chat_id, user_id, total, daily, weekly, monthly, conn=None):
    """Повышает уровень пользователя, если достигнуты новые критерии.
       Если передан conn, использует его, иначе создаёт новое соединение."""
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True

    cur = conn.execute("SELECT level FROM user_stats WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    row = cur.fetchone()
    if not row:
        if close_conn:
            conn.close()
        return
    current_level = row["level"]
    new_level = current_level

    # Уровень 1: первое сообщение (total >= 1)
    if total >= 1 and new_level < 1:
        new_level = 1
    # Уровень 2: total >= 1000
    if total >= 1000 and new_level < 2:
        new_level = 2
    # Уровень 3: daily >= 5000
    if daily >= 5000 and new_level < 3:
        new_level = 3
    # Уровень 4: weekly >= 15000
    if weekly >= 15000 and new_level < 4:
        new_level = 4
    # Уровень 5: weekly >= 35000
    if weekly >= 35000 and new_level < 5:
        new_level = 5
    # Уровень 6: monthly >= 100000
    if monthly >= 100000 and new_level < 6:
        new_level = 6

    if new_level > current_level:
        conn.execute("UPDATE user_stats SET level=? WHERE chat_id=? AND user_id=?", (new_level, chat_id, user_id))
        if close_conn:
            conn.commit()

    if close_conn:
        conn.close()

async def update_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ["group", "supergroup"]:
        return

    chat_id = chat.id
    user_id = user.id
    username = user.username or ""
    first_name = user.first_name or ""
    last_name = user.last_name or ""

    exp_add = calculate_exp(update.message.text)

    current_date = get_current_date()
    current_week = get_current_week()
    current_month = get_current_month()
    current_timestamp = int(time.time())

    conn = get_db_connection()
    try:
        cur = conn.execute(
            """SELECT total, daily, weekly, monthly, last_date, last_week, last_month, exp, 
                      first_seen, last_active, warns, rank, reputation_plus, reputation_minus, clan_id, level, awards
               FROM user_stats WHERE chat_id=? AND user_id=?""",
            (chat_id, user_id)
        )
        row = cur.fetchone()

        if row is None:
            # Новый пользователь
            conn.execute(
                """INSERT INTO user_stats 
                   (chat_id, user_id, username, first_name, last_name, total, daily, weekly, monthly,
                    last_date, last_week, last_month, exp, first_seen, last_active, 
                    warns, rank, reputation_plus, reputation_minus, clan_id, level, awards) 
                   VALUES (?,?,?,?,?,1,1,1,1,?,?,?,?,?,?,0,0,0,0,NULL,0,'')""",
                (chat_id, user_id, username, first_name, last_name,
                 current_date, current_week, current_month, exp_add,
                 current_timestamp, current_timestamp)
            )
            # Проверим уровень (первое сообщение -> уровень 1)
            update_level(chat_id, user_id, 1, 1, 1, 1, conn=conn)
        else:
            total = row["total"]
            daily = row["daily"]
            weekly = row["weekly"]
            monthly = row["monthly"]
            last_date = row["last_date"]
            last_week = row["last_week"]
            last_month = row["last_month"]
            exp = row["exp"]
            first_seen = row["first_seen"]
            warns = row["warns"]
            rank = row["rank"]
            rep_plus = row["reputation_plus"]
            rep_minus = row["reputation_minus"]
            clan_id = row["clan_id"]
            level = row["level"]
            awards = row["awards"]

            # Сброс daily при смене дня
            if last_date != current_date:
                daily = 0
                last_date = current_date
            # Сброс weekly при смене недели
            if last_week != current_week:
                weekly = 0
                last_week = current_week
            # Сброс monthly при смене месяца
            if last_month != current_month:
                monthly = 0
                last_month = current_month

            total += 1
            daily += 1
            weekly += 1
            monthly += 1
            exp += exp_add

            conn.execute(
                """UPDATE user_stats SET total=?, daily=?, weekly=?, monthly=?, last_date=?, last_week=?, last_month=?,
                   username=?, first_name=?, last_name=?, exp=?, last_active=?, warns=?, rank=?, 
                   reputation_plus=?, reputation_minus=?, clan_id=?, level=?, awards=?
                   WHERE chat_id=? AND user_id=?""",
                (total, daily, weekly, monthly, last_date, last_week, last_month,
                 username, first_name, last_name, exp, current_timestamp, warns, rank,
                 rep_plus, rep_minus, clan_id, level, awards, chat_id, user_id)
            )

            # Проверяем повышение уровня
            update_level(chat_id, user_id, total, daily, weekly, monthly, conn=conn)

        conn.commit()
    finally:
        conn.close()

# ---------- Команды статистики ----------
async def top_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    reset_daily_if_needed(chat.id)
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT user_id, username, first_name, last_name, daily 
           FROM user_stats 
           WHERE chat_id=? AND daily>0 
           ORDER BY daily DESC LIMIT 10""",
        (chat.id,)
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Сегодня ещё никто не писал.")
        return
    text = "🏆 Топ-10 за сегодня:\n"
    for i, row in enumerate(rows, 1):
        name = row["first_name"] or row["username"] or str(row["user_id"])
        text += f"{i}. {name} — {row['daily']} сообщ.\n"
    await update.message.reply_text(text)

async def top_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    reset_weekly_if_needed(chat.id)
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT user_id, username, first_name, last_name, weekly 
           FROM user_stats 
           WHERE chat_id=? AND weekly>0 
           ORDER BY weekly DESC LIMIT 10""",
        (chat.id,)
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("На этой неделе ещё никто не писал.")
        return
    text = "📅 Топ-10 за неделю:\n"
    for i, row in enumerate(rows, 1):
        name = row["first_name"] or row["username"] or str(row["user_id"])
        text += f"{i}. {name} — {row['weekly']} сообщ.\n"
    await update.message.reply_text(text)

async def stat_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT user_id, username, first_name, last_name, total 
           FROM user_stats 
           WHERE chat_id=? 
           ORDER BY total DESC LIMIT 10""",
        (chat.id,)
    ).fetchall()
    total_users = conn.execute("SELECT COUNT(*) FROM user_stats WHERE chat_id=?", (chat.id,)).fetchone()[0]
    conn.close()
    if not rows:
        await update.message.reply_text("Статистика пока пуста.")
        return
    text = "🏅 Топ-10 за всё время:\n"
    for i, row in enumerate(rows, 1):
        name = row["first_name"] or row["username"] or str(row["user_id"])
        text += f"{i}. {name} — {row['total']} сообщ.\n"
    keyboard = [[InlineKeyboardButton("📋 Показать всех участников", callback_data="show_all")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)

async def show_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat = update.effective_chat
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT user_id, username, first_name, last_name, total 
           FROM user_stats 
           WHERE chat_id=? 
           ORDER BY total DESC""",
        (chat.id,)
    ).fetchall()
    conn.close()
    if not rows:
        await query.edit_message_text("Нет данных.")
        return
    text = "📊 Все участники (сообщения за всё время):\n"
    for i, row in enumerate(rows, 1):
        name = row["first_name"] or row["username"] or str(row["user_id"])
        text += f"{i}. {name} — {row['total']}\n"
        if len(text) > 3500:
            text += "..."
            break
    await query.edit_message_text(text)

async def my_exp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    conn = get_db_connection()
    row = conn.execute("SELECT exp FROM user_stats WHERE chat_id=? AND user_id=?", (chat.id, user.id)).fetchone()
    conn.close()
    if row:
        exp = row["exp"]
        await update.message.reply_text(f"📈 Ваш опыт в этом чате: {exp:.2f}")
    else:
        await update.message.reply_text("У вас пока нет опыта в этом чате.")

async def get_user_profile_text(chat_id, target_user, context, is_admin=False):
    conn = get_db_connection()
    row = conn.execute(
        """SELECT u.total, u.daily, u.weekly, u.monthly, u.exp, u.first_seen, u.last_active, 
                  u.warns, u.rank, u.level, u.reputation_plus, u.reputation_minus, u.clan_id, 
                  c.name as clan_name, u.awards
           FROM user_stats u
           LEFT JOIN clans c ON u.clan_id = c.id
           WHERE u.chat_id=? AND u.user_id=?""",
        (chat_id, target_user.id)
    ).fetchone()
    conn.close()

    if not row:
        return None

    total = row["total"]
    daily = row["daily"]
    weekly = row["weekly"]
    monthly = row["monthly"]
    exp = row["exp"]
    first_seen = row["first_seen"]
    last_active = row["last_active"]
    warns = row["warns"]
    rank = row["rank"]
    level = row["level"]
    rep_plus = row["reputation_plus"] or 0
    rep_minus = row["reputation_minus"] or 0
    clan_name = row["clan_name"]
    awards_raw = row["awards"] or ""

    now = time.time()
    if first_seen:
        first_duration = format_duration(now - first_seen)
        first_date_str = datetime.datetime.fromtimestamp(first_seen, TIMEZONE).strftime("%d.%m.%Y")
    else:
        first_duration = "неизвестно"
        first_date_str = "неизвестно"

    last_active_str = format_time_ago(last_active) if last_active else "никогда"

    name = target_user.first_name or target_user.username or str(target_user.id)
    rank_name = RANKS.get(rank, f"Ранг {rank}")
    level_name = LEVEL_NAMES.get(level, f"Уровень {level}")

    # Формируем список наград
    awards_list = [a.strip() for a in awards_raw.split(';') if a.strip()]
    awards_text = "\n".join(f"   • {award}" for award in awards_list) if awards_list else "—"

    text = f"👤 <b>{name}</b>\n"
    if is_admin:
        text += "🔰 <b>Телеграм-админ этого чата</b>\n"
    text += f"🏷 <b>Ранг:</b> {rank_name}\n"
    text += f"🎚 <b>Уровень:</b> {level_name} ({level})\n"
    text += f"⚠️ <b>Предупреждения:</b> {warns}/3\n"
    text += f"🏷 <b>Клан:</b> {clan_name if clan_name else '—'}\n"
    text += f"\n📊 <b>Статистика активности:</b>\n"
    text += f"└ Сообщений за сегодня: {daily}\n"
    text += f"└ За неделю: {weekly}\n"
    text += f"└ За месяц: {monthly}\n"
    text += f"└ За всё время: {total}\n"
    text += f"└ Опыт: {exp:.2f}\n"
    text += f"\n🕐 <b>Первое появление:</b> {first_date_str} ({first_duration})\n"
    text += f"🕐 <b>Последний актив:</b> {last_active_str}\n"
    text += f"\n🏷 <b>Репутация:</b> +{rep_plus} | -{rep_minus}\n"
    text += f"\n🏅 <b>Награды:</b>\n{awards_text}\n"

    return text

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    is_admin = False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        is_admin = member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except:
        pass

    text = await get_user_profile_text(chat.id, user, context, is_admin)
    if text is None:
        await update.message.reply_text("У вас пока нет статистики в этом чате.")
    else:
        await update.message.reply_text(text, parse_mode="HTML")

async def get_target_user_for_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user

    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                username = message.text[entity.offset:entity.offset+entity.length].lstrip('@')
                conn = get_db_connection()
                row = conn.execute(
                    "SELECT user_id, username, first_name, last_name FROM user_stats WHERE chat_id=? AND LOWER(username)=? LIMIT 1",
                    (message.chat.id, username.lower())
                ).fetchone()
                conn.close()
                if row:
                    try:
                        member = await context.bot.get_chat_member(message.chat.id, row["user_id"])
                        return member.user
                    except TelegramError:
                        return type('User', (), {
                            'id': row['user_id'],
                            'username': row['username'],
                            'first_name': row['first_name'],
                            'last_name': row['last_name']
                        })()
                else:
                    await message.reply_text("Пользователь с таким username не найден в статистике. Используйте ответ на сообщение.")
                    return None
    await message.reply_text("Чтобы посмотреть статистику другого пользователя, ответьте на его сообщение или укажите @username.")
    return None

async def cmd_whois(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    target_user = await get_target_user_for_stats(update, context)
    if not target_user:
        return

    is_admin = False
    try:
        member = await context.bot.get_chat_member(chat.id, target_user.id)
        is_admin = member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except TelegramError:
        pass

    text = await get_user_profile_text(chat.id, target_user, context, is_admin)
    if text is None:
        await update.message.reply_text(f"У пользователя {target_user.first_name or target_user.username or target_user.id} нет статистики в этом чате.")
    else:
        await update.message.reply_text(text, parse_mode="HTML")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет!"
    )

# ---------- Функции сброса daily/weekly/monthly (для топов) ----------
def reset_daily_if_needed(chat_id):
    current_date = get_current_date()
    conn = get_db_connection()
    conn.execute(
        "UPDATE user_stats SET daily=0, last_date=? WHERE chat_id=? AND last_date!=?",
        (current_date, chat_id, current_date)
    )
    conn.commit()
    conn.close()

def reset_weekly_if_needed(chat_id):
    current_week = get_current_week()
    conn = get_db_connection()
    conn.execute(
        "UPDATE user_stats SET weekly=0, last_week=? WHERE chat_id=? AND last_week!=?",
        (current_week, chat_id, current_week)
    )
    conn.commit()
    conn.close()

# ---------- Работа с рангами ----------
async def ensure_owner_rank(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status == 'creator':
            conn = get_db_connection()
            cur = conn.execute("SELECT rank FROM user_stats WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            row = cur.fetchone()
            if not row or row["rank"] < 8:
                conn.execute("INSERT OR IGNORE INTO user_stats (chat_id, user_id, username, first_name, last_name) VALUES (?,?,?,?,?)",
                             (chat_id, user_id, member.user.username or "", member.user.first_name or "", member.user.last_name or ""))
                conn.execute("UPDATE user_stats SET rank=8 WHERE chat_id=? AND user_id=?", (chat_id, user_id))
                conn.commit()
            conn.close()
    except Exception as e:
        print(f"Error in ensure_owner_rank: {e}")

async def get_user_rank(chat_id: int, user_id: int) -> int:
    conn = get_db_connection()
    row = conn.execute("SELECT rank FROM user_stats WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
    conn.close()
    return row["rank"] if row else 0

async def check_target_rank(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user) -> bool:
    issuer = update.effective_user
    chat = update.effective_chat
    if not issuer or not chat:
        return False

    issuer_rank = await get_user_rank(chat.id, issuer.id)
    target_rank = await get_user_rank(chat.id, target_user.id)

    try:
        target_member = await context.bot.get_chat_member(chat.id, target_user.id)
        if target_member.status == 'creator':
            await update.message.reply_text("❌ Нельзя применить наказание к создателю чата.")
            return False
    except:
        pass

    if issuer_rank >= 8:
        return True

    if target_rank >= issuer_rank:
        await update.message.reply_text(
            f"❌ Вы не можете наказать пользователя с более высоким или равным рангом.\n"
            f"Ваш ранг: {RANKS[issuer_rank]}, его ранг: {RANKS.get(target_rank, 'неизвестно')}"
        )
        return False

    return True

async def check_rank(update: Update, context: ContextTypes.DEFAULT_TYPE, required_rank: int) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status == 'creator':
            await ensure_owner_rank(chat.id, user.id, context)
            return True
    except:
        pass
    rank = await get_user_rank(chat.id, user.id)
    if rank >= required_rank:
        return True
    else:
        await update.message.reply_text(f"❌ У вас недостаточно прав. Требуется ранг: {RANKS[required_rank]}")
        return False

async def set_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_rank(update, context, 8):
        return
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /setrank @username ранг (1-8)")
        return
    target_username = args[0].lstrip('@')
    try:
        new_rank = int(args[1])
        if new_rank < 1 or new_rank > 8:
            await update.message.reply_text("Ранг должен быть от 1 до 8.")
            return
    except ValueError:
        await update.message.reply_text("Ранг должен быть числом.")
        return
    try:
        chat_members = await context.bot.get_chat_administrators(chat.id)
        target_user = None
        for admin in chat_members:
            if admin.user.username and admin.user.username.lower() == target_username.lower():
                target_user = admin.user
                break
        if not target_user:
            await update.message.reply_text("Пользователь не найден. Убедитесь, что username правильный.")
            return
    except Exception as e:
        await update.message.reply_text(f"Ошибка поиска: {e}")
        return
    conn = get_db_connection()
    conn.execute("INSERT OR IGNORE INTO user_stats (chat_id, user_id, username, first_name, last_name) VALUES (?,?,?,?,?)",
                 (chat.id, target_user.id, target_user.username or "", target_user.first_name or "", target_user.last_name or ""))
    conn.execute("UPDATE user_stats SET rank=? WHERE chat_id=? AND user_id=?", (new_rank, chat.id, target_user.id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Пользователю {target_user.first_name} установлен ранг {RANKS[new_rank]}")

async def admins_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT user_id, username, first_name, last_name, rank FROM user_stats WHERE chat_id=? AND rank>0 ORDER BY rank DESC, total DESC",
        (chat.id,)
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("В этом чате пока нет назначенных администраторов.")
        return
    text = "👥 **Администраторы чата:**\n"
    for row in rows:
        name = row["first_name"] or row["username"] or str(row["user_id"])
        rank_name = RANKS.get(row["rank"], f"Ранг {row['rank']}")
        text += f"• {name} — {rank_name}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def sync_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    is_creator = False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        is_creator = member.status == 'creator'
    except:
        pass
    rank = await get_user_rank(chat.id, user.id)
    if not (is_creator or rank >= 8):
        await update.message.reply_text("❌ Только владелец может использовать эту команду.")
        return

    await update.message.reply_text("🔄 Синхронизация администраторов...")
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        count_updated = 0
        for admin in admins:
            if admin.status == 'creator':
                conn = get_db_connection()
                conn.execute("INSERT OR IGNORE INTO user_stats (chat_id, user_id, username, first_name, last_name) VALUES (?,?,?,?,?)",
                             (chat.id, admin.user.id, admin.user.username or "", admin.user.first_name or "", admin.user.last_name or ""))
                conn.execute("UPDATE user_stats SET rank=8 WHERE chat_id=? AND user_id=?", (chat.id, admin.user.id))
                conn.commit()
                conn.close()
                count_updated += 1
        await update.message.reply_text(f"✅ Синхронизация завершена. Обновлён создатель (ранг 8).")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка синхронизации: {e}")

def parse_duration(duration_str: str) -> int | None:
    if not duration_str:
        return None
    duration_str = duration_str.lower().strip()
    match = re.match(r'^(\d+(?:\.\d+)?)\s*([чдм]|час|часов|дн|дней|мин|минут)?$', duration_str)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2) or 'ч'
    if unit in ['м', 'мин', 'минут']:
        return int(value * 60)
    elif unit in ['ч', 'час', 'часов']:
        return int(value * 3600)
    elif unit in ['д', 'дн', 'дней']:
        return int(value * 86400)
    return None

# ---------- Функции наказаний ----------
async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user, duration_seconds: int = None, reason: str = ""):
    chat = update.effective_chat
    permissions = {
        'can_send_messages': False,
        'can_send_media_messages': False,
        'can_send_polls': False,
        'can_send_other_messages': False,
        'can_add_web_page_previews': False,
        'can_change_info': False,
        'can_invite_users': False,
        'can_pin_messages': False,
    }
    try:
        if duration_seconds:
            until_date = int(time.time() + duration_seconds)
            await context.bot.restrict_chat_member(chat.id, target_user.id, permissions=permissions, until_date=until_date)
            text = f"🔇 Пользователь {target_user.first_name} замучен на {duration_seconds//3600} ч. {reason}"
        else:
            await context.bot.restrict_chat_member(chat.id, target_user.id, permissions=permissions)
            text = f"🔇 Пользователь {target_user.first_name} замучен бессрочно. {reason}"
        await update.message.reply_text(text)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Ошибка мута: {e}")

async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user):
    chat = update.effective_chat
    permissions = {
        'can_send_messages': True,
        'can_send_media_messages': True,
        'can_send_polls': True,
        'can_send_other_messages': True,
        'can_add_web_page_previews': True,
        'can_change_info': False,
        'can_invite_users': False,
        'can_pin_messages': False,
    }
    try:
        await context.bot.restrict_chat_member(chat.id, target_user.id, permissions=permissions)
        await update.message.reply_text(f"🔊 Пользователь {target_user.first_name} размучен.")
    except TelegramError as e:
        await update.message.reply_text(f"❌ Ошибка размута: {e}")

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user, duration_seconds: int = None, reason: str = ""):
    chat = update.effective_chat
    try:
        if duration_seconds:
            until_date = int(time.time() + duration_seconds)
            await context.bot.ban_chat_member(chat.id, target_user.id, until_date=until_date)
            text = f"🔨 Пользователь {target_user.first_name} забанен на {duration_seconds//86400} дн. {reason}"
        else:
            await context.bot.ban_chat_member(chat.id, target_user.id)
            text = f"🔨 Пользователь {target_user.first_name} забанен навсегда. {reason}"
        await update.message.reply_text(text)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Ошибка бана: {e}")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user):
    chat = update.effective_chat
    try:
        await context.bot.unban_chat_member(chat.id, target_user.id, only_if_banned=True)
        await update.message.reply_text(f"✅ Пользователь {target_user.first_name} разбанен.")
    except TelegramError as e:
        await update.message.reply_text(f"❌ Ошибка разбана: {e}")

async def kick_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user, reason: str = ""):
    chat = update.effective_chat
    try:
        await context.bot.ban_chat_member(chat.id, target_user.id, until_date=int(time.time() + 35))
        await context.bot.unban_chat_member(chat.id, target_user.id)
        await update.message.reply_text(f"👢 Пользователь {target_user.first_name} кикнут. {reason}")
    except TelegramError as e:
        await update.message.reply_text(f"❌ Ошибка кика: {e}")

# ---------- Предупреждения ----------
async def warn_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user, reason: str = ""):
    chat = update.effective_chat
    issuer_rank = await get_user_rank(chat.id, update.effective_user.id)
    conn = get_db_connection()
    row = conn.execute("SELECT warns FROM user_stats WHERE chat_id=? AND user_id=?", (chat.id, target_user.id)).fetchone()
    current_warns = row["warns"] if row else 0
    if current_warns >= 2 and issuer_rank < 2:
        await update.message.reply_text("❌ У вас недостаточно прав, чтобы выдать третье предупреждение (требуется младший модератор).")
        conn.close()
        return
    new_warns = current_warns + 1
    conn.execute("INSERT OR IGNORE INTO user_stats (chat_id, user_id, username, first_name, last_name) VALUES (?,?,?,?,?)",
                 (chat.id, target_user.id, target_user.username or "", target_user.first_name or "", target_user.last_name or ""))
    conn.execute("UPDATE user_stats SET warns=? WHERE chat_id=? AND user_id=?", (new_warns, chat.id, target_user.id))
    conn.commit()
    conn.close()
    if new_warns >= 3:
        keyboard = [
            [
                InlineKeyboardButton("🔇 Мут на 12 суток", callback_data=f"punish_mute_{target_user.id}"),
                InlineKeyboardButton("🔨 Бан на 12 суток", callback_data=f"punish_ban_{target_user.id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"⚠️ Пользователь {target_user.first_name} получил 3-е предупреждение! Выберите действие:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(f"⚠️ Пользователю {target_user.first_name} выдано предупреждение ({new_warns}/3). {reason}")

async def unwarn_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user):
    chat = update.effective_chat
    conn = get_db_connection()
    row = conn.execute("SELECT warns FROM user_stats WHERE chat_id=? AND user_id=?", (chat.id, target_user.id)).fetchone()
    if not row or row["warns"] == 0:
        await update.message.reply_text(f"У пользователя {target_user.first_name} нет предупреждений.")
        conn.close()
        return
    new_warns = row["warns"] - 1
    conn.execute("UPDATE user_stats SET warns=? WHERE chat_id=? AND user_id=?", (new_warns, chat.id, target_user.id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ У пользователя {target_user.first_name} снято одно предупреждение. Осталось: {new_warns}")

async def punish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("punish_"):
        return
    _, action, user_id_str = data.split('_')
    target_user_id = int(user_id_str)
    chat = update.effective_chat
    try:
        target_user = await context.bot.get_chat_member(chat.id, target_user_id)
        target_user = target_user.user
    except:
        target_user = type('User', (), {'id': target_user_id, 'first_name': str(target_user_id)})()
    if action == "mute":
        await mute_user(update, context, target_user, duration_seconds=12*86400, reason="(автоматически за 3 варна)")
    elif action == "ban":
        await ban_user(update, context, target_user, duration_seconds=12*86400, reason="(автоматически за 3 варна)")
    conn = get_db_connection()
    conn.execute("UPDATE user_stats SET warns=0 WHERE chat_id=? AND user_id=?", (chat.id, target_user_id))
    conn.commit()
    conn.close()
    await query.edit_message_text(f"✅ Наказание применено к {target_user.first_name}, предупреждения сброшены.")

async def get_target_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.reply_to_message:
        return message.reply_to_message.from_user
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                username = message.text[entity.offset:entity.offset+entity.length].lstrip('@')
                await message.reply_text("Пожалуйста, используйте ответ на сообщение пользователя для этой команды.")
                return None
    return None

# ---------- Команда выдачи наград (только для владельца) ----------
async def cmd_award(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем права (только владелец, ранг 8)
    if not await check_rank(update, context, 8):
        return

    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    # Определяем целевого пользователя
    target_user = await get_target_from_message(update, context)
    if not target_user:
        # Если не ответ, ищем упоминание
        if context.args:
            # Первый аргумент может быть @username
            first_arg = context.args[0]
            if first_arg.startswith('@'):
                username = first_arg.lstrip('@')
                conn = get_db_connection()
                row = conn.execute(
                    "SELECT user_id, username, first_name, last_name FROM user_stats WHERE chat_id=? AND LOWER(username)=? LIMIT 1",
                    (chat.id, username.lower())
                ).fetchone()
                conn.close()
                if row:
                    # Создаём объект пользователя
                    target_user = type('User', (), {
                        'id': row['user_id'],
                        'username': row['username'],
                        'first_name': row['first_name'],
                        'last_name': row['last_name']
                    })()
                    # Убираем @username из аргументов
                    context.args = context.args[1:]
                else:
                    await update.message.reply_text("Пользователь с таким username не найден в статистике.")
                    return
            else:
                await update.message.reply_text("Чтобы наградить, ответьте на сообщение пользователя или укажите @username.")
                return
        else:
            await update.message.reply_text("Чтобы наградить, ответьте на сообщение пользователя или укажите @username.")
            return

    # Получаем текст награды (оставшиеся аргументы)
    if not context.args:
        await update.message.reply_text("Укажите текст награды.\nПример: /award @username За активность")
        return

    award_text = " ".join(context.args).strip()
    if len(award_text) > 200:
        await update.message.reply_text("Текст награды слишком длинный (макс. 200 символов).")
        return

    # Добавляем награду в БД
    conn = get_db_connection()
    # Убедимся, что пользователь есть в таблице
    conn.execute(
        "INSERT OR IGNORE INTO user_stats (chat_id, user_id, username, first_name, last_name) VALUES (?,?,?,?,?)",
        (chat.id, target_user.id, target_user.username or "", target_user.first_name or "", target_user.last_name or "")
    )
    # Получаем текущие награды
    row = conn.execute("SELECT awards FROM user_stats WHERE chat_id=? AND user_id=?", (chat.id, target_user.id)).fetchone()
    current_awards = row["awards"] or ""
    if current_awards:
        new_awards = current_awards + "; " + award_text
    else:
        new_awards = award_text
    conn.execute(
        "UPDATE user_stats SET awards=? WHERE chat_id=? AND user_id=?",
        (new_awards, chat.id, target_user.id)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Пользователю {target_user.first_name or target_user.username} выдана награда: «{award_text}»")

# ---------- Обработчики команд модерации ----------
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_rank(update, context, 2):
        return
    target = await get_target_from_message(update, context)
    if not target:
        return
    if not await check_target_rank(update, context, target):
        return
    args = context.args
    duration = None
    reason = ""
    if args:
        dur = parse_duration(args[0])
        if dur is not None:
            duration = dur
            reason = " ".join(args[1:])
        else:
            reason = " ".join(args)
    await mute_user(update, context, target, duration, reason)

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_rank(update, context, 2):
        return
    target = await get_target_from_message(update, context)
    if not target:
        return
    if not await check_target_rank(update, context, target):
        return
    await unmute_user(update, context, target)

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_rank(update, context, 7):
        return
    target = await get_target_from_message(update, context)
    if not target:
        return
    if not await check_target_rank(update, context, target):
        return
    args = context.args
    duration = None
    reason = ""
    if args:
        dur = parse_duration(args[0])
        if dur is not None:
            duration = dur
            reason = " ".join(args[1:])
        else:
            reason = " ".join(args)
    await ban_user(update, context, target, duration, reason)

async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_rank(update, context, 7):
        return
    target = await get_target_from_message(update, context)
    if not target:
        return
    if not await check_target_rank(update, context, target):
        return
    await unban_user(update, context, target)

async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_rank(update, context, 3):
        return
    target = await get_target_from_message(update, context)
    if not target:
        return
    if not await check_target_rank(update, context, target):
        return
    reason = " ".join(context.args) if context.args else ""
    await kick_user(update, context, target, reason)

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_rank(update, context, 1):
        return
    target = await get_target_from_message(update, context)
    if not target:
        return
    if not await check_target_rank(update, context, target):
        return
    reason = " ".join(context.args) if context.args else ""
    await warn_user(update, context, target, reason)

async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_rank(update, context, 2):
        return
    target = await get_target_from_message(update, context)
    if not target:
        return
    if not await check_target_rank(update, context, target):
        return
    await unwarn_user(update, context, target)

# ---------- Система репутации ----------
async def handle_reputation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message.reply_to_message:
        return False
    if not message.from_user:
        return False

    text = message.text.strip()
    if text not in ('+', '-'):
        return False

    giver = message.from_user
    receiver = message.reply_to_message.from_user
    if not receiver:
        await message.reply_text("Не удалось определить пользователя.")
        return True

    if giver.id == receiver.id:
        await message.reply_text("❌ Нельзя изменять репутацию самому себе.")
        return True

    chat_id = message.chat.id
    today = get_current_date()

    conn = get_db_connection()

    for user in (giver, receiver):
        conn.execute(
            """INSERT OR IGNORE INTO user_stats 
               (chat_id, user_id, username, first_name, last_name, last_reputation_date) 
               VALUES (?,?,?,?,?,?)""",
            (chat_id, user.id, user.username or "", user.first_name or "", user.last_name or "", today)
        )

    giver_row = conn.execute(
        "SELECT plus_given_today, minus_given_today, last_reputation_date FROM user_stats WHERE chat_id=? AND user_id=?",
        (chat_id, giver.id)
    ).fetchone()

    last_rep_date = giver_row["last_reputation_date"]
    plus_given = giver_row["plus_given_today"] or 0
    minus_given = giver_row["minus_given_today"] or 0

    if last_rep_date != today:
        plus_given = 0
        minus_given = 0
        conn.execute(
            "UPDATE user_stats SET plus_given_today=0, minus_given_today=0, last_reputation_date=? WHERE chat_id=? AND user_id=?",
            (today, chat_id, giver.id)
        )

    if text == '+':
        if plus_given >= REP_PLUS_LIMIT:
            await message.reply_text(f"❌ Вы сегодня уже использовали {REP_PLUS_LIMIT} повышений репутации. Лимит исчерпан.")
            conn.close()
            return True
        conn.execute(
            "UPDATE user_stats SET reputation_plus = reputation_plus + 1 WHERE chat_id=? AND user_id=?",
            (chat_id, receiver.id)
        )
        conn.execute(
            "UPDATE user_stats SET plus_given_today = plus_given_today + 1 WHERE chat_id=? AND user_id=?",
            (chat_id, giver.id)
        )
        await message.reply_text(f"✅ Репутация пользователя {receiver.first_name or receiver.username} повышена.")
    else:
        if minus_given >= REP_MINUS_LIMIT:
            await message.reply_text(f"❌ Вы сегодня уже использовали {REP_MINUS_LIMIT} понижений репутации. Лимит исчерпан.")
            conn.close()
            return True
        conn.execute(
            "UPDATE user_stats SET reputation_minus = reputation_minus + 1 WHERE chat_id=? AND user_id=?",
            (chat_id, receiver.id)
        )
        conn.execute(
            "UPDATE user_stats SET minus_given_today = minus_given_today + 1 WHERE chat_id=? AND user_id=?",
            (chat_id, giver.id)
        )
        await message.reply_text(f"✅ Репутация пользователя {receiver.first_name or receiver.username} понижена.")

    conn.commit()
    conn.close()
    return True

# ---------- Система кланов ----------
async def cmd_createclan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Укажите название клана.\nИспользование: /createclan <название>")
        return

    raw_name = " ".join(args).strip()
    parts = raw_name.split(maxsplit=1)
    if parts and parts[0].lower() == "клан":
        if len(parts) > 1:
            clan_name = parts[1].strip()
        else:
            clan_name = ""
    else:
        clan_name = raw_name

    if not clan_name:
        await update.message.reply_text("Название клана не может быть пустым.")
        return

    if len(clan_name) > 50:
        await update.message.reply_text("Название клана слишком длинное (макс. 50 символов).")
        return

    conn = get_db_connection()

    cur = conn.execute("SELECT clan_id FROM user_stats WHERE chat_id=? AND user_id=?", (chat.id, user.id))
    row = cur.fetchone()
    if row and row["clan_id"] is not None:
        await update.message.reply_text("Вы уже состоите в клане. Выйдите из текущего клана, чтобы создать новый.")
        conn.close()
        return

    cur = conn.execute("SELECT id FROM clans WHERE chat_id=? AND name=?", (chat.id, clan_name))
    if cur.fetchone():
        await update.message.reply_text("Клан с таким названием уже существует в этом чате.")
        conn.close()
        return

    now = int(time.time())
    cursor = conn.execute(
        "INSERT INTO clans (chat_id, name, leader_id, created_at) VALUES (?,?,?,?)",
        (chat.id, clan_name, user.id, now)
    )
    clan_id = cursor.lastrowid

    conn.execute(
        "UPDATE user_stats SET clan_id=? WHERE chat_id=? AND user_id=?",
        (clan_id, chat.id, user.id)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Клан «{clan_name}» успешно создан! Вы стали его лидером.")

async def cmd_joinclan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Укажите название клана.\nИспользование: /joinclan <название>")
        return
    clan_name = " ".join(args).strip()

    conn = get_db_connection()

    cur = conn.execute("SELECT clan_id FROM user_stats WHERE chat_id=? AND user_id=?", (chat.id, user.id))
    row = cur.fetchone()
    if row and row["clan_id"] is not None:
        await update.message.reply_text("Вы уже состоите в клане. Выйдите из текущего клана, чтобы вступить в другой.")
        conn.close()
        return

    cur = conn.execute("SELECT id FROM clans WHERE chat_id=? AND name=?", (chat.id, clan_name))
    clan = cur.fetchone()
    if not clan:
        await update.message.reply_text("Клан с таким названием не найден в этом чате.")
        conn.close()
        return
    clan_id = clan["id"]

    conn.execute(
        "UPDATE user_stats SET clan_id=? WHERE chat_id=? AND user_id=?",
        (clan_id, chat.id, user.id)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Вы вступили в клан «{clan_name}».")

async def cmd_leaveclan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    conn = get_db_connection()

    cur = conn.execute(
        "SELECT clan_id FROM user_stats WHERE chat_id=? AND user_id=?",
        (chat.id, user.id)
    )
    row = cur.fetchone()
    if not row or row["clan_id"] is None:
        await update.message.reply_text("Вы не состоите в клане.")
        conn.close()
        return
    clan_id = row["clan_id"]

    cur = conn.execute("SELECT leader_id FROM clans WHERE id=?", (clan_id,))
    clan = cur.fetchone()
    if clan and clan["leader_id"] == user.id:
        await update.message.reply_text("❌ Лидер не может покинуть клан. Сначала передайте лидерство или удалите клан (используйте /deleteclan).")
        conn.close()
        return

    conn.execute(
        "UPDATE user_stats SET clan_id=NULL WHERE chat_id=? AND user_id=?",
        (chat.id, user.id)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text("✅ Вы покинули клан.")

async def cmd_deleteclan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    conn = get_db_connection()

    cur = conn.execute(
        "SELECT clan_id FROM user_stats WHERE chat_id=? AND user_id=?",
        (chat.id, user.id)
    )
    row = cur.fetchone()
    if not row or row["clan_id"] is None:
        await update.message.reply_text("Вы не состоите в клане.")
        conn.close()
        return
    clan_id = row["clan_id"]

    cur = conn.execute("SELECT leader_id, name FROM clans WHERE id=?", (clan_id,))
    clan = cur.fetchone()
    if not clan:
        await update.message.reply_text("Ошибка: клан не найден в базе.")
        conn.close()
        return
    if clan["leader_id"] != user.id:
        await update.message.reply_text("❌ Только лидер клана может его удалить.")
        conn.close()
        return

    conn.execute(
        "UPDATE user_stats SET clan_id=NULL WHERE chat_id=? AND clan_id=?",
        (chat.id, clan_id)
    )
    conn.execute("DELETE FROM clans WHERE id=?", (clan_id,))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Клан «{clan['name']}» успешно удалён.")

async def cmd_myclan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    conn = get_db_connection()
    cur = conn.execute(
        """SELECT c.*, u.first_name as leader_name, u.username as leader_username,
                  (SELECT COUNT(*) FROM user_stats WHERE clan_id = c.id) as members_count,
                  (SELECT SUM(exp) FROM user_stats WHERE clan_id = c.id) as total_exp
           FROM clans c
           LEFT JOIN user_stats u ON c.leader_id = u.user_id AND u.chat_id = c.chat_id
           WHERE c.chat_id=? AND c.leader_id=?""",
        (chat.id, user.id)
    )
    clan = cur.fetchone()
    if not clan:
        cur = conn.execute(
            "SELECT clan_id FROM user_stats WHERE chat_id=? AND user_id=?", (chat.id, user.id)
        )
        row = cur.fetchone()
        if not row or row["clan_id"] is None:
            await update.message.reply_text("Вы не состоите в клане.")
            conn.close()
            return
        clan_id = row["clan_id"]
        cur = conn.execute(
            """SELECT c.*, u.first_name as leader_name, u.username as leader_username,
                      (SELECT COUNT(*) FROM user_stats WHERE clan_id = c.id) as members_count,
                      (SELECT SUM(exp) FROM user_stats WHERE clan_id = c.id) as total_exp
               FROM clans c
               LEFT JOIN user_stats u ON c.leader_id = u.user_id AND u.chat_id = c.chat_id
               WHERE c.id=?""",
            (clan_id,)
        )
        clan = cur.fetchone()

    if not clan:
        await update.message.reply_text("Информация о клане не найдена.")
        conn.close()
        return

    members = conn.execute(
        """SELECT user_id, first_name, username, exp FROM user_stats 
           WHERE clan_id=? ORDER BY exp DESC LIMIT 10""",
        (clan["id"],)
    ).fetchall()
    conn.close()

    created_at = datetime.datetime.fromtimestamp(clan["created_at"], TIMEZONE).strftime("%d.%m.%Y %H:%M")
    leader_name = clan["leader_name"] or clan["leader_username"] or str(clan["leader_id"])
    text = f"🏰 <b>Клан: {clan['name']}</b>\n"
    text += f"👤 Лидер: {leader_name}\n"
    text += f"📅 Создан: {created_at}\n"
    text += f"👥 Участников: {clan['members_count']}\n"
    text += f"⭐ Суммарный опыт: {clan['total_exp']:.2f}\n\n"
    text += "<b>Топ участников по опыту:</b>\n"
    for i, m in enumerate(members, 1):
        name = m["first_name"] or m["username"] or str(m["user_id"])
        text += f"{i}. {name} — {m['exp']:.2f}\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def cmd_claninfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Укажите название клана.\nИспользование: /claninfo <название>")
        return
    clan_name = " ".join(args).strip()

    conn = get_db_connection()
    cur = conn.execute(
        """SELECT c.*, u.first_name as leader_name, u.username as leader_username,
                  (SELECT COUNT(*) FROM user_stats WHERE clan_id = c.id) as members_count,
                  (SELECT SUM(exp) FROM user_stats WHERE clan_id = c.id) as total_exp
           FROM clans c
           LEFT JOIN user_stats u ON c.leader_id = u.user_id AND u.chat_id = c.chat_id
           WHERE c.chat_id=? AND c.name=?""",
        (chat.id, clan_name)
    )
    clan = cur.fetchone()
    if not clan:
        await update.message.reply_text("Клан с таким названием не найден.")
        conn.close()
        return

    members = conn.execute(
        """SELECT user_id, first_name, username, exp FROM user_stats 
           WHERE clan_id=? ORDER BY exp DESC LIMIT 10""",
        (clan["id"],)
    ).fetchall()
    conn.close()

    created_at = datetime.datetime.fromtimestamp(clan["created_at"], TIMEZONE).strftime("%d.%m.%Y %H:%M")
    leader_name = clan["leader_name"] or clan["leader_username"] or str(clan["leader_id"])
    text = f"🏰 <b>Клан: {clan['name']}</b>\n"
    text += f"👤 Лидер: {leader_name}\n"
    text += f"📅 Создан: {created_at}\n"
    text += f"👥 Участников: {clan['members_count']}\n"
    text += f"⭐ Суммарный опыт: {clan['total_exp']:.2f}\n\n"
    text += "<b>Топ участников по опыту:</b>\n"
    for i, m in enumerate(members, 1):
        name = m["first_name"] or m["username"] or str(m["user_id"])
        text += f"{i}. {name} — {m['exp']:.2f}\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def cmd_clantop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    conn = get_db_connection()
    rows = conn.execute(
        """SELECT c.id, c.name, c.leader_id, 
                  COUNT(m.user_id) as members_count,
                  COALESCE(SUM(m.exp), 0) as total_exp
           FROM clans c
           LEFT JOIN user_stats m ON c.id = m.clan_id
           WHERE c.chat_id=?
           GROUP BY c.id
           ORDER BY total_exp DESC
           LIMIT 10""",
        (chat.id,)
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("В этом чате ещё нет кланов.")
        return

    text = "🏆 <b>Топ-10 кланов по опыту</b>\n"
    for i, row in enumerate(rows, 1):
        text += f"{i}. {row['name']} — опыт: {row['total_exp']:.2f} (участников: {row['members_count']})\n"
    await update.message.reply_text(text, parse_mode="HTML")

# ---------- Обработчик русских команд ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    lower_text = text.lower()

    chat = update.effective_chat
    user = update.effective_user
    if chat and user and chat.type in ["group", "supergroup"]:
        await ensure_owner_rank(chat.id, user.id, context)

    if await handle_reputation(update, context):
        return

    if lower_text == "стата вся":
        await stat_all(update, context)
    elif lower_text == "топ дня":
        await top_day(update, context)
    elif lower_text == "топ неделя":
        await top_week(update, context)
    elif lower_text == "кто я":
        await whoami(update, context)
    elif lower_text.startswith("кто ты"):
        await cmd_whois(update, context)
    elif lower_text.startswith("!админы") or lower_text.startswith("/admins"):
        await admins_list(update, context)
    elif lower_text.startswith("!reloadadmin") or lower_text.startswith("/reloadadmin"):
        await sync_admins(update, context)
    elif lower_text.startswith("наградить"):
        # Формат: наградить @username текст (или в ответ на сообщение)
        args = text[9:].strip().split()
        context.args = args
        await cmd_award(update, context)
    elif lower_text.startswith("мут"):
        args = text[3:].strip().split()
        context.args = args
        await cmd_mute(update, context)
    elif lower_text.startswith("снять мут"):
        args = text[9:].strip().split()
        context.args = args
        await cmd_unmute(update, context)
    elif lower_text.startswith("бан"):
        args = text[3:].strip().split()
        context.args = args
        await cmd_ban(update, context)
    elif lower_text.startswith("снять бан"):
        args = text[9:].strip().split()
        context.args = args
        await cmd_unban(update, context)
    elif lower_text.startswith("кик"):
        args = text[3:].strip().split()
        context.args = args
        await cmd_kick(update, context)
    elif lower_text.startswith("варн"):
        args = text[4:].strip().split()
        context.args = args
        await cmd_warn(update, context)
    elif lower_text.startswith("снять варн"):
        args = text[10:].strip().split()
        context.args = args
        await cmd_unwarn(update, context)
    elif lower_text.startswith("!создать клан") or lower_text.startswith("/createclan"):
        args = text.split(maxsplit=2)
        if len(args) < 2:
            await update.message.reply_text("Укажите название клана.")
        else:
            context.args = args[1:]
            await cmd_createclan(update, context)
    elif lower_text.startswith("!вступить в клан") or lower_text.startswith("/joinclan"):
        args = text.split(maxsplit=2)
        if len(args) < 2:
            await update.message.reply_text("Укажите название клана.")
        else:
            context.args = args[1:]
            await cmd_joinclan(update, context)
    elif lower_text.startswith("!покинуть клан") or lower_text.startswith("/leaveclan"):
        await cmd_leaveclan(update, context)
    elif lower_text.startswith("!удалить клан") or lower_text.startswith("/deleteclan"):
        await cmd_deleteclan(update, context)
    elif lower_text.startswith("!мой клан") or lower_text.startswith("/myclan"):
        await cmd_myclan(update, context)
    elif lower_text.startswith("!инфо клана") or lower_text.startswith("/claninfo"):
        args = text.split(maxsplit=2)
        if len(args) < 2:
            await update.message.reply_text("Укажите название клана.")
        else:
            context.args = args[1:]
            await cmd_claninfo(update, context)
    elif lower_text.startswith("!топ кланов") or lower_text.startswith("/clantop"):
        await cmd_clantop(update, context)
    else:
        await update_stats(update, context)

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stat_all", stat_all))
    app.add_handler(CommandHandler("top_day", top_day))
    app.add_handler(CommandHandler("top_week", top_week))
    app.add_handler(CommandHandler("myexp", my_exp))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("whois", cmd_whois))
    app.add_handler(CommandHandler("admins", admins_list))
    app.add_handler(CommandHandler("reloadadmin", sync_admins))
    app.add_handler(CommandHandler("setrank", set_rank))
    app.add_handler(CommandHandler("award", cmd_award))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))
    app.add_handler(CommandHandler("createclan", cmd_createclan))
    app.add_handler(CommandHandler("joinclan", cmd_joinclan))
    app.add_handler(CommandHandler("leaveclan", cmd_leaveclan))
    app.add_handler(CommandHandler("deleteclan", cmd_deleteclan))
    app.add_handler(CommandHandler("myclan", cmd_myclan))
    app.add_handler(CommandHandler("claninfo", cmd_claninfo))
    app.add_handler(CommandHandler("clantop", cmd_clantop))

    app.add_handler(CallbackQueryHandler(show_all_callback, pattern="^show_all$"))
    app.add_handler(CallbackQueryHandler(punish_callback, pattern="^punish_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()