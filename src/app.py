import asyncio
import os
import re
import sqlite3
import ccxt
import pandas as pd
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = None 

exchange = ccxt.binance()
MINSK_TZ = pytz.timezone('Europe/Minsk')

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            direction TEXT,
            entry_price REAL,
            status TEXT DEFAULT 'pending' 
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            user_prediction TEXT,
            bot_prediction TEXT,
            result TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_bot_prediction(date, direction, price):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO bot_predictions (date, direction, entry_price) VALUES (?, ?, ?)', (date, direction, price))
    conn.commit()
    conn.close()

def get_pending_bot_prediction():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    cursor.execute('SELECT id, direction, entry_price FROM bot_predictions WHERE date = ? AND status = "pending" ORDER BY id DESC LIMIT 1', (today,))
    row = cursor.fetchone()
    conn.close()
    return row

def resolve_prediction(pred_id, result):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE bot_predictions SET status = ? WHERE id = ?', (result, pred_id))
    conn.commit()
    conn.close()

init_db()

# --- ИНДИКАТОРЫ ---
def get_rsi_prediction():
    try:
        # Берем 100 последних часовых свечей
        ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Считаем RSI (14 периодов)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        current_rsi = df['RSI'].iloc[-1]
        current_price = df['close'].iloc[-1]
        
        if current_rsi < 30:
            return "LONG", current_price, f"RSI = {current_rsi:.2f} (перепродан, ждем рост)"
        elif current_rsi > 70:
            return "SHORT", current_price, f"RSI = {current_rsi:.2f} (перекуплен, ждем падение)"
        else:
            # Если RSI в нейтральной зоне, бот не торгует, но мы можем дать легкий прогноз по тренду
            return "WAIT", current_price, f"RSI = {current_rsi:.2f} (нет четкого сигнала)"
    except Exception as e:
        print(f"Ошибка RSI: {e}")
        return "ERROR", 0, "Ошибка данных"

# --- БОТ ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global CHAT_ID
    CHAT_ID = message.chat.id
    await message.answer(
        "🤖 Привет! Я твой продвинутый ИИ-помощник.\n\n"
        "Теперь я сам анализирую рынок в 10:30 и даю прогноз на основе RSI!\n"
        "А в 23:59 я проверяю результат.\n\n"
        "Также ты можешь спросить: «Какая сейчас цена?» или «Статистика»."
    )

@dp.message()
async def handle_text(message: types.Message):
    text = message.text.lower().strip()
    
    if "цена" in text or "курс" in text:
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = f"{ticker['last']:,.2f}".replace(',', ' ')
        await message.answer(f"💰 Сейчас BTC стоит: {price} USDT")
        return

    # Если пользователь нажал кнопку "Угадал/Не угадал"
    if "✅" in text or "❌" in text:
        # Логика обработки нажатия кнопки будет здесь
        await message.answer("Я записал твой результат в статистику!")
        return

    await message.answer("Я тебя не понял. Напиши «Какая сейчас цена» или дождись моего утреннего прогноза в 10:30.")

# --- УТРЕННИЙ ПРОГНОЗ В 10:30 ---
async def morning_prediction():
    print("Делаю утренний прогноз...")
    direction, price, reason = get_rsi_prediction()
    
    if direction == "ERROR" or direction == "WAIT":
        if CHAT_ID:
            await bot.send_message(chat_id=CHAT_ID, text=f"⏳ Утренний анализ: {reason}. Сигнала на вход нет.")
        return

    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    save_bot_prediction(today, direction, price)
    formatted_price = f"{price:,.2f}".replace(',', ' ')
    
    # Создаем кнопки для пользователя
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Согласен с ботом", callback_data=f"agree_{direction}")
    builder.button(text="❌ Мой прогноз другой", callback_data=f"disagree_{direction}")

    text = (
        f"🌅 Доброе утро! Мой анализ на сегодня:\n\n"
        f"📊 Прогноз: {direction}\n"
        f"💰 Цена входа: {formatted_price} USDT\n"
        f"💡 Причина: {reason}\n\n"
        f"Ты согласен со мной?"
    )
    
    if CHAT_ID:
        await bot.send_message(chat_id=CHAT_ID, text=text, reply_markup=builder.as_markup())

# --- ВЕЧЕРНЯЯ ПРОВЕРКА В 23:59 ---
async def evening_check():
    print("Проверяю вечерние результаты...")
    pending = get_pending_bot_prediction()
    if not pending:
        return

    pred_id, direction, entry_price = pending
    ticker = exchange.fetch_ticker('BTC/USDT')
    close_price = float(ticker['last'])
    
    # Логика "Чистого знака"
    is_correct = False
    if direction == "LONG" and close_price > entry_price:
        is_correct = True
    elif direction == "SHORT" and close_price < entry_price:
        is_correct = True
        
    result_status = "correct" if is_correct else "wrong"
    resolve_prediction(pred_id, result_status)
    
    formatted_close = f"{close_price:,.2f}".replace(',', ' ')
    formatted_entry = f"{entry_price:,.2f}".replace(',', ' ')
    
    emoji = "✅ УГАДАЛ!" if is_correct else "❌ НЕ УГАДАЛ."
    
    text = (
        f"🌙 Итоги дня:\n"
        f"Мой прогноз был: {direction} (вход {formatted_entry})\n"
        f"Цена закрытия: {formatted_close}\n\n"
        f"Результат: {emoji}"
    )
    
    if CHAT_ID:
        await bot.send_message(chat_id=CHAT_ID, text=text)

async def handle_web(request):
    return web.Response(text="Bot is running!")

async def main():
    scheduler = AsyncIOScheduler(timezone=MINSK_TZ)
    # Утренний прогноз в 10:30
    scheduler.add_job(morning_prediction, 'cron', hour=10, minute=30)
    # Вечерняя проверка в 23:59
    scheduler.add_job(evening_check, 'cron', hour=23, minute=59)
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
