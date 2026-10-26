import asyncio
import os
import re
import sqlite3
import ccxt
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import CallbackQuery
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
        CREATE TABLE IF NOT EXISTS daily_predictions (
            date TEXT PRIMARY KEY,
            bot_prediction TEXT,
            bot_entry REAL,
            user_prediction TEXT,
            model_2_prediction TEXT,
            result TEXT DEFAULT 'pending'
        )
    ''')
    conn.commit()
    conn.close()

def update_bot_prediction(date, direction, entry):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO daily_predictions (date, bot_prediction, bot_entry) 
        VALUES (?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET bot_prediction=excluded.bot_prediction, bot_entry=excluded.bot_entry
    ''', (date, direction, entry))
    conn.commit()
    conn.close()

def update_user_prediction(date, direction):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE daily_predictions SET user_prediction = ? WHERE date = ?', (direction, date))
    conn.commit()
    conn.close()

def update_model2_prediction(date, direction):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE daily_predictions SET model_2_prediction = ? WHERE date = ?', (direction, date))
    conn.commit()
    conn.close()

def get_today_data():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    cursor.execute('SELECT bot_prediction, bot_entry, user_prediction, model_2_prediction, result FROM daily_predictions WHERE date = ?', (today,))
    row = cursor.fetchone()
    conn.close()
    return row

def get_stats():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT result, COUNT(*) FROM daily_predictions WHERE result != "pending" GROUP BY result')
    rows = cursor.fetchall()
    conn.close()
    return dict(rows)

init_db()

# --- РАСЧЕТ RSI ---
def get_rsi_prediction():
    try:
        ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=100)
        closes = [candle[4] for candle in ohlcv]
        period = 14
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            if diff > 0:
                gains.append(diff); losses.append(0)
            else:
                gains.append(0); losses.append(abs(diff))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        rsi = 100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))
        current_price = closes[-1]
        
        if rsi < 30: return "LONG", current_price, f"RSI = {rsi:.2f} (перепродан)"
        elif rsi > 70: return "SHORT", current_price, f"RSI = {rsi:.2f} (перекуплен)"
        else: return "WAIT", current_price, f"RSI = {rsi:.2f} (нет сигнала)"
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
    
    # Создаем главное меню с кнопками
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Узнать цену", callback_data="get_price")
    builder.button(text="📊 Моя статистика", callback_data="get_report")
    
    await message.answer(
        "🤖 Привет! Я твой продвинутый ИИ-помощник.\n\n"
        "Я анализирую рынок в 10:30. Ты можешь ввести свой прогноз и прогноз модели The One Trade.\n"
        "В 23:59 я подведу итоги и сравню, кто был прав.\n\n"
        "Используй кнопки ниже или пиши: «Мой прогноз» / «Тот».",
        reply_markup=builder.as_markup()
    )

@dp.callback_query()
async def handle_callbacks(callback: CallbackQuery):
    date = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    
    if callback.data == "get_price":
        try:
            ticker = exchange.fetch_ticker('BTC/USDT')
            current_price = float(ticker['last'])
            formatted_price = f"{current_price:,.2f}".replace(',', ' ')
            await callback.message.answer(f"💰 Текущая цена BTC: {formatted_price} USDT")
        except Exception as e:
            await callback.message.answer(f"Ошибка получения цены: {e}")
        await callback.answer()
        return

    if callback.data == "get_report":
        stats = get_stats()
        bot_wins = stats.get("BOT", 0)
        user_wins = stats.get("USER", 0)
        model2_wins = stats.get("MODEL_2", 0)
        await callback.message.answer(
            f"📊 Общая статистика побед:\n"
            f"🤖 Бот: {bot_wins}\n"
            f"👤 Вы: {user_wins}\n"
            f"🧠 The One Trade: {model2_wins}"
        )
        await callback.answer()
        return
        
    if callback.data.startswith("user_"):
        prediction = callback.data.split("_")[1]
        update_user_prediction(date, prediction)
        await callback.message.answer(f"✅ Твой прогноз на сегодня: {prediction}. Записал!")
        await callback.answer()
        
    elif callback.data.startswith("model2_"):
        prediction = callback.data.split("_")[1]
        update_model2_prediction(date, prediction)
        await callback.message.answer(f"✅ Прогноз The One Trade на сегодня: {prediction}. Записал!")
        await callback.answer()

@dp.message(Command("report"))
async def cmd_report(message: types.Message):
    stats = get_stats()
    await message.answer(f"📊 Бот: {stats.get('BOT', 0)} | Вы: {stats.get('USER', 0)} | Тот: {stats.get('MODEL_2', 0)}")

@dp.message()
async def handle_text(message: types.Message):
    text = message.text.lower().strip()
    
    # 1. Проверка на цену (теперь ловит любые варианты)
    if "цена" in text or "курс" in text or "price" in text or "стоит" in text:
        try:
            ticker = exchange.fetch_ticker('BTC/USDT')
            current_price = float(ticker['last'])
            formatted_price = f"{current_price:,.2f}".replace(',', ' ')
            await message.answer(f"💰 Текущая цена BTC: {formatted_price} USDT")
        except Exception as e:
            await message.answer(f"Ошибка: {e}")
        return
        
    # 2. Проверка на ваш прогноз
    if "мой прогноз" in text or "я думаю" in text:
        builder = InlineKeyboardBuilder()
        builder.button(text="🟢 Мой прогноз LONG", callback_data="user_LONG")
        builder.button(text="🔴 Мой прогноз SHORT", callback_data="user_SHORT")
        await message.answer("Выбери свой прогноз на сегодня:", reply_markup=builder.as_markup())
        return
        
    # 3. Проверка на The One Trade (включая слово "тот")
    if "the one" in text or "traide" in text or "trade" in text or "модель" in text or text == "тот":
        builder = InlineKeyboardBuilder()
        builder.button(text="🟢 The One Trade: LONG", callback_data="model2_LONG")
        builder.button(text="🔴 The One Trade: SHORT", callback_data="model2_SHORT")
        await message.answer("Выбери прогноз от The One Trade:", reply_markup=builder.as_markup())
        return

    await message.answer("Я тебя не понял. Напиши «Цена», «Мой прогноз» или «Тот».")

# --- УТРЕННИЙ ПРОГНОЗ В 10:30 ---
async def morning_prediction():
    direction, price, reason = get_rsi_prediction()
    if direction in ["ERROR", "WAIT"]:
        if CHAT_ID: await bot.send_message(chat_id=CHAT_ID, text=f"⏳ Анализ: {reason}. Сигнала нет.")
        return

    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    update_bot_prediction(today, direction, price)
    formatted_price = f"{price:,.2f}".replace(',', ' ')
    
    text = (
        f"🌅 Доброе утро! Мой анализ:\n\n"
        f"📊 Прогноз: {direction}\n"
        f"💰 Цена входа: {formatted_price} USDT\n"
        f"💡 Причина: {reason}\n\n"
        f"Напиши «Мой прогноз» и «Тот», чтобы я записал ваши мнения!"
    )
    if CHAT_ID: await bot.send_message(chat_id=CHAT_ID, text=text)

# --- ВЕЧЕРНЯЯ ПРОВЕРКА В 23:59 ---
async def evening_check():
    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    data = get_today_data()
    if not data: return

    bot_pred, entry, user_pred, model2_pred, _ = data
    ticker = exchange.fetch_ticker('BTC/USDT')
    close_price = float(ticker['last'])
    
    actual_direction = "LONG" if close_price > entry else "SHORT"
    
    winner = "NONE"
    if bot_pred == actual_direction: winner = "BOT"
    elif user_pred == actual_direction: winner = "USER"
    elif model2_pred == actual_direction: winner = "MODEL_2"
    
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE daily_predictions SET result = ? WHERE date = ?', (winner, today))
    conn.commit()
    conn.close()
    
    text = (
        f"🌙 Итоги дня:\n"
        f"Цена входа: {entry}\n"
        f"Цена закрытия: {close_price}\n\n"
        f"🤖 Бот: {bot_pred} {'✅' if bot_pred == actual_direction else '❌'}\n"
        f"👤 Вы: {user_pred or '—'} {'✅' if user_pred == actual_direction else '❌'}\n"
        f"🧠 The One Trade: {model2_pred or '—'} {'✅' if model2_pred == actual_direction else '❌'}\n\n"
    )
    if winner == "NONE":
        text += "🤷 Победила дружба (все ошиблись или не вводили прогноз)."
    else:
        text += f"🏆 Победитель: {winner}!"
        
    if CHAT_ID: await bot.send_message(chat_id=CHAT_ID, text=text)

async def handle_web(request):
    return web.Response(text="Bot is running!")

async def main():
    scheduler = AsyncIOScheduler(timezone=MINSK_TZ)
    scheduler.add_job(morning_prediction, 'cron', hour=10, minute=30)
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
