import asyncio
import os
import re
import sqlite3
import ccxt
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = None # Сохраняем ваш ID для вечерних сообщений

# Инициализация биржи (только чтение цены, без ключей)
exchange = ccxt.binance()
MINSK_TZ = pytz.timezone('Europe/Minsk')

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
    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    cursor.execute('''
        INSERT INTO predictions (user_id, date, min_price, max_price)
        VALUES (?, ?, ?, ?)
    ''', (user_id, today, min_p, max_p))
    conn.commit()
    conn.close()

def get_today_range(user_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    cursor.execute('SELECT id, min_price, max_price FROM predictions WHERE user_id = ? AND date = ? ORDER BY id DESC LIMIT 1', (user_id, today))
    row = cursor.fetchone()
    conn.close()
    return row

def get_pending_predictions():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    cursor.execute('SELECT id, user_id, min_price, max_price FROM predictions WHERE date = ? AND close_price IS NULL', (today,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def update_close(pred_id, user_id, close_price, prediction, is_correct):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE predictions 
        SET close_price = ?, prediction = ?, is_correct = ?
        WHERE id = ? AND user_id = ?
    ''', (close_price, prediction, is_correct, pred_id, user_id))
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
    global CHAT_ID
    CHAT_ID = message.chat.id
    await message.answer(
        "🤖 Привет! Я твой ИИ-помощник по BTC.\n\n"
        "Пришли мне два числа (например: 64000 66000).\n"
        "В 23:59 я САМ проверю цену закрытия на бирже и запишу результат.\n"
        "Команда /price покажет текущую цену.\n"
        "Команда /stats покажет статистику."
    )

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    total, correct = get_stats(message.from_user.id)
    if total == 0:
        await message.answer("📊 Пока нет завершенных прогнозов.")
    else:
        percent = (correct / total) * 100
        await message.answer(f"📊 Статистика:\nВсего: {total}\nУгадал: {correct}\nТочность: {percent:.1f}%")

@dp.message(Command("price"))
async def cmd_price(message: types.Message):
    try:
        ticker = exchange.fetch_ticker('BTC/USDT')
        current_price = float(ticker['last'])
        await message.answer(f"💰 Текущая цена BTC: {current_price} USDT")
    except Exception as e:
        await message.answer(f"Не удалось узнать цену. Ошибка: {e}")

@dp.message()
async def handle_text(message: types.Message):
    text = message.text.lower()
    user_id = message.from_user.id
    numbers = re.findall(r'\d+', text)
    
    if not numbers:
        await message.answer("Я не вижу цифр. Пришли мне два числа (например: 64000 66000).")
        return

    if len(numbers) >= 2 and "закрытие" not in text:
        min_p = float(numbers[0])
        max_p = float(numbers[1])
        save_range(user_id, min_p, max_p)
        
        mid = (min_p + max_p) / 2
        await message.answer(
            f"✅ Записал диапазон: {min_p} - {max_p}.\n"
            f"Середина: {mid}.\n"
            f"⏰ В 23:59 я сам проверю цену закрытия и дам прогноз."
        )
        return

    await message.answer("Я вижу цифры, но не понял. Пришли 2 числа (например: 64000 66000).")

# --- АВТОМАТИЧЕСКАЯ ПРОВЕРКА В 23:59 ---
async def check_results():
    print("Запуск проверки результатов...")
    pending = get_pending_predictions()
    if not pending:
        print("Нет прогнозов для проверки.")
        return

    try:
        ticker = exchange.fetch_ticker('BTC/USDT')
        close_price = float(ticker['last'])
        print(f"Цена закрытия: {close_price}")
    except Exception as e:
        print(f"Ошибка получения цены: {e}")
        return

    for pred_id, user_id, min_p, max_p in pending:
        mid = (min_p + max_p) / 2
        if close_price < mid:
            prediction = "SHORT"
            is_correct = 1 if close_price < mid else 0
        else:
            prediction = "LONG"
            is_correct = 1 if close_price > mid else 0
        
        update_close(pred_id, user_id, close_price, prediction, is_correct)
        
        if CHAT_ID:
            result_text = f"🔔 Итоги дня:\nЦена закрытия: {close_price}\nМой прогноз был: {prediction}\nРезультат: {'✅ Угадал!' if is_correct else '❌ Не угадал.'}"
            await bot.send_message(chat_id=CHAT_ID, text=result_text)

async def handle_web(request):
    return web.Response(text="Bot is running!")

async def main():
    scheduler = AsyncIOScheduler(timezone=MINSK_TZ)
    scheduler.add_job(check_results, 'cron', hour=23, minute=59)
    scheduler.start()

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
