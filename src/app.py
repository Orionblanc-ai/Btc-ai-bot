import asyncio
import os
import re
import sqlite3
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from datetime import datetime

TOKEN = os.environ.get("TELEGRAM_TOKEN")

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            min_price REAL,
            max_price REAL,
            close_price REAL,
            prediction TEXT,
            is_correct INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def save_range(user_id, min_p, max_p):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('''
        INSERT INTO predictions (user_id, date, min_price, max_price)
        VALUES (?, ?, ?, ?)
    ''', (user_id, today, min_p, max_p))
    conn.commit()
    conn.close()

def get_today_range(user_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('SELECT id, min_price, max_price FROM predictions WHERE user_id = ? AND date = ? ORDER BY id DESC LIMIT 1', (user_id, today))
    row = cursor.fetchone()
    conn.close()
    return row

def update_close(user_id, close_price, prediction, is_correct):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('''
        UPDATE predictions 
        SET close_price = ?, prediction = ?, is_correct = ?
        WHERE user_id = ? AND date = ? AND id = (SELECT MAX(id) FROM predictions WHERE user_id = ? AND date = ?)
    ''', (close_price, prediction, is_correct, user_id, today, user_id, today))
    conn.commit()
    conn.close()

def get_stats(user_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*), SUM(is_correct) FROM predictions WHERE user_id = ? AND is_correct IS NOT NULL', (user_id,))
    total, correct = cursor.fetchone()
    conn.close()
    return total or 0, correct or 0

init_db()
# -------------------

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 Привет! Я твой ИИ-помощник по BTC.\n\n"
        "Пришли мне два числа (например: 64000 66000).\n"
        "Вечером пришли цену закрытия (например: 65000).\n"
        "Команда /stats покажет мою статистику."
    )

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    total, correct = get_stats(message.from_user.id)
    if total == 0:
        await message.answer("📊 Пока нет завершенных прогнозов.")
    else:
        percent = (correct / total) * 100
        await message.answer(f"📊 Статистика:\nВсего: {total}\nУгадал: {correct}\nТочность: {percent:.1f}%")

@dp.message()
async def handle_text(message: types.Message):
    text = message.text.lower()
    user_id = message.from_user.id
    numbers = re.findall(r'\d+', text)
    
    if not numbers:
        await message.answer("Я не вижу цифр. Пришли мне два числа или цену закрытия.")
        return

    # Если прислали два числа (ночной диапазон)
    if len(numbers) >= 2 and "закрытие" not in text and "цена" not in text:
        min_p = float(numbers[0])
        max_p = float(numbers[1])
        save_range(user_id, min_p, max_p)
        
        mid = (min_p + max_p) / 2
        await message.answer(
            f"✅ Записал диапазон: {min_p} - {max_p}.\n"
            f"Середина: {mid}.\n"
            f"📊 Жду цену закрытия."
        )
        return

    # Если прислали цену закрытия
    row = get_today_range(user_id)
    if row:
        close_price = float(numbers[0])
        pred_id, min_p, max_p = row
        mid = (min_p + max_p) / 2
        
        if close_price < mid:
            prediction = "SHORT"
            is_correct = 1 if close_price < (min_p + max_p)/2 else 0 
            result = f"📉 Цена {close_price} ниже середины ({mid}). Прогноз: SHORT."
        else:
            prediction = "LONG"
            is_correct = 1 if close_price > (min_p + max_p)/2 else 0
            result = f"📈 Цена {close_price} выше середины ({mid}). Прогноз: LONG."
        
        update_close(user_id, close_price, prediction, is_correct)
        
        await message.answer(
            f"🔔 Закрытие {close_price} принято.\n"
            f"{result}\n"
            f"📝 Записал в базу. Проверь статистику командой /stats."
        )
        return

    await message.answer("Я вижу цифры, но не понял логику. Пришли 2 числа (диапазон) или 1 число (закрытие).")

async def handle_web(request):
    return web.Response(text="Bot is running!")

async def main():
    app = web.Application()
    app.router.add_get('/', handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
