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

# Настройка matplotlib для работы без GUI (обязательно для серверов)
matplotlib.use('Agg')

# --- НАСТРОЙКИ ---
TOKEN = "8425462771:AAGrg5ihUDsiRiaB6GvCZ_MJiS8hcW6KeJo"
DB_NAME = "bot_stats.db"

logging.basicConfig(level=logging.INFO)

# --- СОСТОЯНИЯ ---
class Form(StatesGroup):
    waiting_for_folder_name = State()
    waiting_for_channels = State()

# --- РАБОТА С БД И ИНИЦИАЛИЗАЦИЯ (прежние) ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS folders (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY, folder_id INTEGER, tg_id TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS stats (tg_id TEXT, date TEXT, count INTEGER)")
        await db.commit()

async def update_stats(bot: Bot):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT DISTINCT tg_id FROM channels")
        channels = await cursor.fetchall()
        today = datetime.now().strftime("%Y-%m-%d")
        
        for (tg_id,) in channels:
            try:
                count = await bot.get_chat_member_count(tg_id)
                # Избегаем дубликатов за один день
                await db.execute("INSERT OR REPLACE INTO stats (tg_id, date, count) VALUES (?, ?, ?)", (tg_id, today, count))
            except Exception as e:
                logging.error(f"Ошибка сбора для {tg_id}: {e}")
        await db.commit()

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- ФУНКЦИЯ ГЕНЕРАЦИИ ГРАФИКА (НОВОЕ) ---
def generate_stats_image(df: pd.DataFrame, folder_name: str):
    """
    Принимает DataFrame c колонками ['date', 'tg_id', 'count'].
    Возвращает объект BufferedInputFile с графиком.
    """
    # Преобразуем даты в формат datetime
    df['date'] = pd.to_datetime(df['date'])
    
    # Создаем холст
    plt.figure(figsize=(10, 6))
    
    # Группируем данные по каналам и рисуем линию для каждого
    for tg_id, group in df.groupby('tg_id'):
        # Сортируем внутри группы по дате
        group = group.sort_values('date')
        plt.plot(group['date'], group['count'], marker='.', label=tg_id)

    plt.title(f"Динамика подписчиков: папка '{folder_name}'")
    plt.xlabel("Дата")
    plt.ylabel("Подписчики")
    plt.grid(True, linestyle='--', alpha=0.7)
    plt.legend() # Показывает легенду (какой цвет - какой канал)
    
    # Поворачиваем даты на оси X, чтобы не перекрывались
    plt.xticks(rotation=45)
    plt.tight_layout()

    # Сохраняем график в буфер
    image_buffer = io.BytesIO()
    plt.savefig(image_buffer, format='png', dpi=100)
    plt.close() # Освобождаем память
    image_buffer.seek(0)
    
    # Конвертируем в формат, понятный aiogram
    return types.BufferedInputFile(image_buffer.read(), filename=f"stats_{folder_name}.png")

# --- ОСТАЛЬНЫЕ ХЕНДЛЕРЫ (без изменений) ---
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
    if message.chat.type == 'private':
        await message.answer("Ваши папки:", reply_markup=builder.as_markup())
    else:
        # Если вызвано через edit_text из колбэка
        await message.edit_text("Ваши папки:", reply_markup=builder.as_markup())

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
        types.InlineKeyboardButton(text="➕ Добавить", callback_data=f"add_{folder_name}"),
        types.InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{folder_name}")
    )
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu"))
    await callback.message.edit_text(f"Папка: {folder_name}", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("add_"))
async def add_channels_step1(callback: types.CallbackQuery, state: FSMContext):
    folder_name = callback.data[4:]
    await state.update_data(folder_name=folder_name)
    await callback.message.answer("Перечислите каналы (напр. @channel1 @channel2):")
    await state.set_state(Form.waiting_for_channels)

@dp.message(Form.waiting_for_channels)
async def add_channels_step2(message: types.Message, state: FSMContext):
    data = await state.get_data()
    folder_name = data['folder_name']
    channels = message.text.replace('\n', ' ').split()
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT id FROM folders WHERE user_id = ? AND name = ?", (message.from_user.id, folder_name))
        f_id_row = await cursor.fetchone()
        if f_id_row:
            f_id = f_id_row[0]
            for ch in channels:
                if not ch.startswith('@'): ch = '@'+ch
                await db.execute("INSERT INTO channels (folder_id, tg_id) VALUES (?, ?)", (f_id, ch))
            await db.commit()
    
    await state.clear()
    await message.answer(f"Каналы добавлены в '{folder_name}'!")
    await show_main_menu(message)

@dp.callback_query(F.data.startswith("del_"))
async def delete_folder(callback: types.CallbackQuery):
    folder_name = callback.data[4:]
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM folders WHERE user_id = ? AND name = ?", (callback.from_user.id, folder_name))
        await db.commit()
    await callback.answer("Папка удалена")
    await show_main_menu(callback.message)

@dp.callback_query(F.data == "main_menu")
async def back_to_main(callback: types.CallbackQuery):
    await show_main_menu(callback.message)

# --- ВЫВОД ТЕКСТОВОЙ СТАТИСТИКИ (Старый stat_, теперь txt_) ---
@dp.callback_query(F.data.startswith("txt_"))
async def show_stats_text(callback: types.CallbackQuery):
    folder_name = callback.data[4:]
    user_id = callback.from_user.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT c.tg_id FROM channels c 
            JOIN folders f ON c.folder_id = f.id 
            WHERE f.user_id = ? AND f.name = ?
        """, (user_id, folder_name))
        channels = await cursor.fetchall()
        
        if not channels:
            return await callback.answer("В папке нет каналов")

        await callback.answer("Загружаю цифры...")
        report = f"📊 Отчет (7 дней): {folder_name}\n\n"
        week_ago_str = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        for (tg_id,) in channels:
            try:
                curr = await bot.get_chat_member_count(tg_id)
                cursor_old = await db.execute("SELECT count FROM stats WHERE tg_id = ? AND date <= ? ORDER BY date DESC LIMIT 1", (tg_id, week_ago_str))
                old_row = await cursor_old.fetchone()
                old_count = old_row[0] if old_row else curr
                
                diff = curr - old_count
                diff_str = f"(+{diff})" if diff >= 0 else f"({diff})"
                report += f"🔹 {tg_id}: {curr} {diff_str}\n"
            except:
                report += f"🔹 {tg_id}: Ошибка доступа\n"

        await callback.message.answer(report)

# --- ВЫВОД СТАТИСТИКИ-КАРТИНКИ (НОВОЕ, pic_) ---
@dp.callback_query(F.data.startswith("pic_"))
async def show_stats_pic(callback: types.CallbackQuery):
    folder_name = callback.data[4:]
    user_id = callback.from_user.id
    
    # 1. Загружаем данные из БД в Pandas
    async with aiosqlite.connect(DB_NAME) as db:
        # SQL запрос сразу объединяет таблицы: получаем историю ТОЛЬКО для каналов из этой папки
        query = """
            SELECT s.date, s.tg_id, s.count
            FROM stats s
            JOIN channels c ON s.tg_id = c.tg_id
            JOIN folders f ON c.folder_id = f.id
            WHERE f.user_id = ? AND f.name = ?
            ORDER BY s.date ASC
        """
        async with db.execute(query, (user_id, folder_name)) as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        return await callback.answer("Нет накопленной истории данных для графика. Подождите пару дней.")

    await callback.answer("Генерирую график...")

    # 2. Конвертируем в DataFrame
    df = pd.DataFrame(rows, columns=['date', 'tg_id', 'count'])

    # 3. Генерируем изображение в отдельном потоке (чтобы не вешать бота)
    loop = asyncio.get_running_loop()
    # image_file станет types.BufferedInputFile
    image_file = await loop.run_in_executor(None, generate_stats_image, df, folder_name)

    # 4. Отправляем фото
    await callback.message.answer_photo(photo=image_file, caption=f"Визуализация статистики для папки '{folder_name}'")


# --- ЗАПУСК ---
async def main():
    await init_db()
    
    scheduler = AsyncIOScheduler()
    # Сбор данных раз в сутки (в 00:01)
    scheduler.add_job(update_stats, 'cron', hour=0, minute=1, args=[bot])
    # Для теста можно включить сбор раз в час:
    # scheduler.add_job(update_stats, 'interval', hours=1, args=[bot])
    scheduler.start()
    
    # Запускаем polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен")
