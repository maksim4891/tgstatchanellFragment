import asyncio
import sqlite3
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ChatJoinRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- НАСТРОЙКИ ---
API_TOKEN = "8425462771:AAGrg5ihUDsiRiaB6GvCZ_MJiS8hcW6KeJo"
ADMIN_ID = 1178979444

logging.basicConfig(level=logging.INFO)

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, url TEXT, title TEXT, type TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS requests (user_id INTEGER, chat_id TEXT, PRIMARY KEY (user_id, chat_id))')
    cursor.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)')
    cursor.execute("INSERT OR IGNORE INTO settings VALUES ('start_text', 'Привет! Этот бот предназначен для скачивания игр')")
    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect('bot_data.db')
    res = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return res[0] if res else "Пусто"

# --- СОСТОЯНИЯ ---
class AdminStates(StatesGroup):
    edit_text = State()
    add_channel = State()
    broadcast = State()

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Сохранение заявки
@dp.chat_join_request()
async def track_join_requests(update: ChatJoinRequest):
    conn = sqlite3.connect('bot_data.db')
    conn.execute("INSERT OR REPLACE INTO requests (user_id, chat_id) VALUES (?, ?)", (update.from_user.id, str(update.chat.id)))
    conn.commit()
    conn.close()

# --- КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ---
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    conn = sqlite3.connect('bot_data.db')
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,))
    conn.commit()
    conn.close()
    text = get_setting('start_text')
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Скачать", callback_data="download")]])
    await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "download")
async def download_click(callback: types.CallbackQuery):
    conn = sqlite3.connect('bot_data.db')
    ads = conn.execute("SELECT url, title FROM channels WHERE type='ads'").fetchall()
    conn.close()
    if not ads:
        await callback.answer("Каналы не настроены", show_alert=True)
        return
    kb_list = [[InlineKeyboardButton(text=title, url=url)] for url, title in ads]
    kb_list.append([InlineKeyboardButton(text="✅ Проверить подписку/заявку", callback_data="check_subs")])
    await callback.message.edit_text("Для доступа подпишитесь или подайте заявку в каналы:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))

@dp.callback_query(F.data == "check_subs")
async def check_subs(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    conn = sqlite3.connect('bot_data.db')
    ads_ids = conn.execute("SELECT chat_id FROM channels WHERE type='ads'").fetchall()
    all_ok = True
    for (chat_id,) in ads_ids:
        res = conn.execute("SELECT 1 FROM requests WHERE user_id=? AND chat_id=?", (user_id, str(chat_id))).fetchone()
        if res: continue
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ['member', 'administrator', 'creator', 'restricted']: continue
            else: all_ok = False; break
        except: all_ok = False; break
    if all_ok:
        prizes = conn.execute("SELECT url, title FROM channels WHERE type='prize'").fetchall()
        kb_prize = [[InlineKeyboardButton(text=title, url=url)] for url, title in prizes]
        await callback.message.edit_text("🎉 Проверка пройдена! Ссылки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_prize))
    else:
        await callback.answer("❌ Вы не подписаны или не подали заявку!", show_alert=True)
    conn.close()

# --- АДМИН-ПАНЕЛЬ ---
@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_menu(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton(text="📝 Текст", callback_data="adm_text"), InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="➕ Рекламный", callback_data="adm_add_ads"), InlineKeyboardButton(text="➕ Призовой", callback_data="adm_add_prize")],
        [InlineKeyboardButton(text="❌ Удалить рекламный", callback_data="del_list_ads")],
        [InlineKeyboardButton(text="❌ Удалить призовой", callback_data="del_list_prize")]
    ])
    await message.answer("⚙️ Админ-панель:", reply_markup=kb)

# --- УДАЛЕНИЕ КАНАЛОВ (ПО ВЫБОРУ) ---
@dp.callback_query(F.data.startswith("del_list_"), F.from_user.id == ADMIN_ID)
async def list_channels_for_delete(callback: types.CallbackQuery):
    c_type = callback.data.split("_")[2]
    conn = sqlite3.connect('bot_data.db')
    channels = conn.execute("SELECT id, title FROM channels WHERE type=?", (c_type,)).fetchall()
    conn.close()
    if not channels:
        await callback.answer("Список пуст", show_alert=True)
        return
    buttons = []
    for c_id, title in channels:
        buttons.append([InlineKeyboardButton(text=f"Удалить: {title}", callback_data=f"drop_{c_id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_admin")])
    await callback.message.edit_text(f"Выберите {'рекламный' if c_type=='ads' else 'призовой'} канал для удаления:", 
                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("drop_"), F.from_user.id == ADMIN_ID)
async def drop_channel(callback: types.CallbackQuery):
    c_id = callback.data.split("_")[1]
    conn = sqlite3.connect('bot_data.db')
    conn.execute("DELETE FROM channels WHERE id=?", (c_id,))
    conn.commit(); conn.close()
    await callback.answer("✅ Канал удален!")
    await admin_menu(callback.message) # Возвращаемся в меню

@dp.callback_query(F.data == "back_to_admin", F.from_user.id == ADMIN_ID)
async def back_to_admin(callback: types.CallbackQuery):
    await admin_menu(callback.message)

# --- ОСТАЛЬНЫЕ ФУНКЦИИ ---
@dp.callback_query(F.data == "adm_stats", F.from_user.id == ADMIN_ID)
async def admin_stats(callback: types.CallbackQuery):
    conn = sqlite3.connect('bot_data.db')
    u = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    a = conn.execute("SELECT COUNT(*) FROM channels WHERE type='ads'").fetchone()[0]
    p = conn.execute("SELECT COUNT(*) FROM channels WHERE type='prize'").fetchone()[0]
    conn.close()
    await callback.message.answer(f"📈 Статистика:\nЮзеров: {u}\nРекламных: {a}\nПризовых: {p}")
    await callback.answer()

@dp.callback_query(F.data.startswith("adm_add_"), F.from_user.id == ADMIN_ID)
async def add_ch_start(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(c_type='ads' if 'ads' in callback.data else 'prize')
    await callback.message.answer("Формат: ID | Ссылка | Название")
    await state.set_state(AdminStates.add_channel)

@dp.message(AdminStates.add_channel)
async def add_ch_save(message: types.Message, state: FSMContext):
    try:
        parts = [i.strip() for i in message.text.split('|')]
        cid, url, title = parts[0], parts[1], parts[2]
        if not cid.startswith("-"): cid = f"-100{cid}"
        data = await state.get_data()
        conn = sqlite3.connect('bot_data.db')
        conn.execute("INSERT INTO channels (chat_id, url, title, type) VALUES (?, ?, ?, ?)", (cid, url, title, data['c_type']))
        conn.commit(); conn.close()
        await message.answer("✅ Добавлено!")
    except: await message.answer("❌ Ошибка!")
    await state.clear()

@dp.callback_query(F.data == "adm_broadcast", F.from_user.id == ADMIN_ID)
async def broad_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Текст рассылки:")
    await state.set_state(AdminStates.broadcast)

@dp.message(AdminStates.broadcast)
async def broad_exec(message: types.Message, state: FSMContext):
    conn = sqlite3.connect('bot_data.db'); users = conn.execute("SELECT user_id FROM users").fetchall(); conn.close()
    c = 0
    for (uid,) in users:
        try: await bot.send_message(uid, message.text); c += 1; await asyncio.sleep(0.05)
        except: continue
    await message.answer(f"✅ Готово! Получили: {c}"); await state.clear()

@dp.callback_query(F.data == "adm_text", F.from_user.id == ADMIN_ID)
async def text_edit(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Новый приветственный текст:"); await state.set_state(AdminStates.edit_text)

@dp.message(AdminStates.edit_text)
async def text_save(message: types.Message, state: FSMContext):
    conn = sqlite3.connect('bot_data.db'); conn.execute("UPDATE settings SET value=? WHERE key='start_text'", (message.text,)); conn.commit(); conn.close()
    await message.answer("✅ Текст обновлен!"); await state.clear()

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())

