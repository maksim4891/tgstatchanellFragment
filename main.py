import asyncio
import logging
import io
import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime, timedelta
import aiosqlite

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настройка графиков для работы на сервере без монитора
matplotlib.use('Agg')

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8425462771:AAGrg5ihUDsiRiaB6GvCZ_MJiS8hcW6KeJo" # <-- Вставь сюда свой токен от BotFather
DB_NAME = "bot_stats.db"

logging.basicConfig(level=logging.INFO)

# --- СОСТОЯНИЯ ---
class Form(StatesGroup):
    waiting_for_folder_name = State()
    waiting_for_channels = State()

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS folders (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY, folder_id INTEGER, tg_id TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS stats (tg_id TEXT, date TEXT, count INTEGER)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_stats ON stats(tg_id, date)")
        await db.commit()

async def update_stats(bot: Bot):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT DISTINCT tg_id FROM channels")
        channels = await cursor.fetchall()
        today = datetime.now().strftime("%Y-%m-%d")
        
        for (tg_id,) in channels:
            try:
                count = await bot.get_chat_member_count(tg_id)
                await db.execute("INSERT OR REPLACE INTO stats (tg_id, date, count) VALUES (?, ?, ?)", (tg_id, today, count))
            except Exception as e:
                logging.error(f"Ошибка сбора для {tg_id}: {e}")
        await db.commit()

# --- ГРАФИКА ---
def generate_stats_image(df: pd.DataFrame, folder_name: str):
    df['date'] = pd.to_datetime(df['date'])
    plt.figure(figsize=(10, 6))
    
    for tg_id, group in df.groupby('tg_id'):
        group = group.sort_values('date')
        plt.plot(group['date'], group['count'], marker='.', label=tg_id)

    plt.title(f"Статистика: {folder_name}")
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()

    image_buffer = io.BytesIO()
    plt.savefig(image_buffer, format='png', dpi=100)
    plt.close()
    image_buffer.seek(0)
    return types.BufferedInputFile(image_buffer.read(), filename="stats.png")

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def start(message: types.Message):
    await show_main_menu(message)

async def show_main_menu(message: types.Message):
    builder = InlineKeyboardBuilder()
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT name FROM folders WHERE user_id = ?", (message.from_user.id,))
        folders = await cursor.fetchall()
        for (name,) in folders:
            builder.row(types.InlineKeyboardButton(text=f"📂 {name}", callback_data=f"f_{name}"))
    
    builder.row(types.InlineKeyboardButton(text="➕ Создать папку", callback_data="create_folder"))
    
    text = "Ваши папки:"
    if message.reply_markup: # Если это вызов из кнопки "Назад"
        await message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "create_folder")
async def create_folder_step1(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите название новой папки:")
    await state.set_state(Form.waiting_for_folder_name)

@dp.message(Form.waiting_for_folder_name)
async def create_folder_step2(message: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO folders (user_id, name) VALUES (?, ?)", (message.from_user.id, message.text))
        await db.commit()
    await state.clear()
    await message.answer(f"Папка '{message.text}' создана!")
    await show_main_menu(message)

@dp.callback_query(F.data.startswith("f_"))
async def manage_folder(callback: types.CallbackQuery):
    folder_name = callback.data[2:]
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="📈 График", callback_data=f"pic_{folder_name}"),
        types.InlineKeyboardButton(text="📊 Текст", callback_data=f"txt_{folder_name}")
    )
    builder.row(
        types.InlineKeyboardButton(text="➕ Канал", callback_data=f"add_{folder_name}"),
        types.InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{folder_name}")
    )
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu"))
    await callback.message.edit_text(f"Папка: {folder_name}", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("add_"))
async def add_channels_step1(callback: types.CallbackQuery, state: FSMContext):
    folder_name = callback.data[4:]
    await state.update_data(folder_name=folder_name)
    await callback.message.answer("Пришлите ссылки на каналы (через пробел):")
    await state.set_state(Form.waiting_for_channels)

@dp.message(Form.waiting_for_channels)
async def add_channels_step2(message: types.Message, state: FSMContext):
    data = await state.get_data()
    folder_name = data['folder_name']
    channels = message.text.replace('\n', ' ').split()
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT id FROM folders WHERE user_id = ? AND name = ?", (message.from_user.id, folder_name))
        res = await cursor.fetchone()
        if res:
            f_id = res[0]
            for ch in channels:
                if not ch.startswith('@'): ch = '@' + ch
                await db.execute("INSERT INTO channels (folder_id, tg_id) VALUES (?, ?)", (f_id, ch))
            await db.commit()
    
    await state.clear()
    await message.answer("Готово!")
    await show_main_menu(message)

@dp.callback_query(F.data.startswith("del_"))
async def delete_folder(callback: types.CallbackQuery):
    folder_name = callback.data[4:]
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM folders WHERE user_id = ? AND name = ?", (callback.from_user.id, folder_name))
        await db.commit()
    await show_main_menu(callback.message)

@dp.callback_query(F.data == "main_menu")
async def back_to_main(callback: types.CallbackQuery):
    await show_main_menu(callback.message)

@dp.callback_query(F.data.startswith("txt_"))
async def show_stats_text(callback: types.CallbackQuery):
    folder_name = callback.data[4:]
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT c.tg_id FROM channels c JOIN folders f ON c.folder_id = f.id WHERE f.user_id = ? AND f.name = ?"
        cursor = await db.execute(query, (callback.from_user.id, folder_name))
        channels = await cursor.fetchall()
        
        if not channels: return await callback.answer("Пусто")
        
        report = f"📊 Папка: {folder_name}\n"
        for (tg_id,) in channels:
            try:
                count = await bot.get_chat_member_count(tg_id)
                report += f"• {tg_id}: {count}\n"
            except: report += f"• {tg_id}: нет доступа\n"
        await callback.message.answer(report)

@dp.callback_query(F.data.startswith("pic_"))
async def show_stats_pic(callback: types.CallbackQuery):
    folder_name = callback.data[4:]
    async with aiosqlite.connect(DB_NAME) as db:
        query = """
            SELECT s.date, s.tg_id, s.count FROM stats s
            JOIN channels c ON s.tg_id = c.tg_id
            JOIN folders f ON c.folder_id = f.id
            WHERE f.user_id = ? AND f.name = ?
        """
        async with db.execute(query, (callback.from_user.id, folder_name)) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        return await callback.answer("Данных для графика пока нет. Подождите 24ч.")

    df = pd.DataFrame(rows, columns=['date', 'tg_id', 'count'])
    loop = asyncio.get_running_loop()
    image = await loop.run_in_executor(None, generate_stats_image, df, folder_name)
    await callback.message.answer_photo(photo=image)

# --- ГЛАВНЫЙ ЗАПУСК ---
async def main():
    # 1. Инициализируем базу данных
    await init_db()
    
    # 2. УДАЛЯЕМ ВЕБХУК (Это решение ошибки 401)
    await bot.delete_webhook(drop_pending_updates=True)
    
    # 3. Запускаем планировщик
    scheduler = AsyncIOScheduler()
    scheduler.add_job(update_stats, 'interval', hours=12, args=[bot]) # Каждые 12 часов
    scheduler.start()
    
    # 4. Запускаем опрос (Polling)
    print("Бот успешно запущен через Polling!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
