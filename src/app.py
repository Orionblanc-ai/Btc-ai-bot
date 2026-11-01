import asyncio
import os
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

exchange = ccxt.kraken()
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
            bot_sl REAL,
            bot_tp REAL,
            user_prediction TEXT,
            user_tp_percent REAL,
            user_sl_price REAL,
            user_tp_price REAL,
            model2_prediction TEXT,
            model2_tp_percent REAL,
            model2_sl_price REAL,
            model2_tp_price REAL,
            status_bot TEXT DEFAULT 'pending',
            status_user TEXT DEFAULT 'pending',
            status_model2 TEXT DEFAULT 'pending',
            result TEXT DEFAULT 'pending'
        )
    ''')
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect('bot_data.db')

def update_bot_prediction(date, direction, entry, sl, tp):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO daily_predictions (date, bot_prediction, bot_entry, bot_sl, bot_tp) 
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET bot_prediction=excluded.bot_prediction, bot_entry=excluded.bot_entry, bot_sl=excluded.bot_sl, bot_tp=excluded.bot_tp
    ''', (date, direction, entry, sl, tp))
    conn.commit()
    conn.close()

def update_user_prediction(date, direction):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE daily_predictions SET user_prediction = ? WHERE date = ?', (direction, date))
    conn.commit()
    conn.close()

def update_user_levels(date, percent):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT bot_entry, user_prediction FROM daily_predictions WHERE date = ?', (date,))
    row = cursor.fetchone()
    if row and row[0]:
        entry = row[0]
        direction = row[1]
        if direction == "LONG":
            sl = entry * 0.985
            tp = entry * (1 + percent/100)
        else:
            sl = entry * 1.015
            tp = entry * (1 - percent/100)
            
        cursor.execute('UPDATE daily_predictions SET user_tp_percent = ?, user_sl_price = ?, user_tp_price = ? WHERE date = ?', (percent, sl, tp, date))
    conn.commit()
    conn.close()

def update_model2_prediction(date, direction):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE daily_predictions SET model2_prediction = ? WHERE date = ?', (direction, date))
    conn.commit()
    conn.close()

def update_model2_levels(date, percent):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT bot_entry, model2_prediction FROM daily_predictions WHERE date = ?', (date,))
    row = cursor.fetchone()
    if row and row[0]:
        entry = row[0]
        direction = row[1]
        if direction == "LONG":
            sl = entry * 0.985
            tp = entry * (1 + percent/100)
        else:
            sl = entry * 1.015
            tp = entry * (1 - percent/100)
            
        cursor.execute('UPDATE daily_predictions SET model2_tp_percent = ?, model2_sl_price = ?, model2_tp_price = ? WHERE date = ?', (percent, sl, tp, date))
    conn.commit()
    conn.close()

def get_today_data():
    conn = get_db()
    cursor = conn.cursor()
    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    cursor.execute('SELECT * FROM daily_predictions WHERE date = ?', (today,))
    row = cursor.fetchone()
    conn.close()
    return row

def get_stats():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT result, COUNT(*) FROM daily_predictions WHERE result != "pending" GROUP BY result')
    rows = cursor.fetchall()
    conn.close()
    return dict(rows)

init_db()

# --- ФУНКЦИИ ---
def get_price_and_symbol(text):
    symbol = 'BTC/USD'
    name = 'BTC'
    if "eth" in text or "эфир" in text:
        symbol = 'ETH/USD'
        name = 'ETH'
    elif "sol" in text or "солана" in text:
        symbol = 'SOL/USD'
        name = 'SOL'
        
    try:
        ticker = exchange.fetch_ticker(symbol)
        price = float(ticker['last'])
        formatted_price = f"{price:,.2f}".replace(',', ' ')
        return f"💰 Текущая цена <b>{name}</b>: <b>{formatted_price} USD</b>"
    except Exception as e:
        return f"Ошибка получения цены: {e}"

def get_rsi_prediction():
    try:
        ohlcv = exchange.fetch_ohlcv('BTC/USD', timeframe='1h', limit=100)
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
        
        recent_highs = [candle[2] for candle in ohlcv[-14:]]
        recent_lows = [candle[3] for candle in ohlcv[-14:]]
        volatility = (sum(recent_highs) - sum(recent_lows)) / 14
        if volatility == 0: volatility = current_price * 0.015
        
        if rsi < 35:
            sl = current_price - (volatility * 1.5)
            tp = current_price + (volatility * 3)
            return "LONG", current_price, sl, tp, f"RSI = {rsi:.2f} (перепродан)"
        elif rsi > 65:
            sl = current_price + (volatility * 1.5)
            tp = current_price - (volatility * 3)
            return "SHORT", current_price, sl, tp, f"RSI = {rsi:.2f} (перекуплен)"
        else:
            return "WAIT", current_price, 0, 0, f"RSI = {rsi:.2f} (нет сигнала)"
    except Exception as e:
        return "ERROR", 0, 0, 0, f"Ошибка: {e}"

# --- БОТ ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global CHAT_ID
    CHAT_ID = message.chat.id
    
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Узнать цену BTC", callback_data="get_price")
    builder.button(text="📈 Прогноз сейчас", callback_data="get_forecast")
    builder.button(text="📊 Статистика", callback_data="get_report")
    builder.button(text="❓ Помощь", callback_data="get_help")
    builder.adjust(2, 2)
    
    await message.answer(
        "🤖 Привет! Я твой продвинутый ИИ-помощник.\n\n"
        "Я анализирую рынок в 11:30 и слежу за стопами/тейками каждые 5 минут.",
        reply_markup=builder.as_markup()
    )

@dp.callback_query()
async def handle_callbacks(callback: CallbackQuery):
    date = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    
    if callback.data == "get_price":
        await callback.message.answer(get_price_and_symbol("btc"), parse_mode="HTML")
        await callback.answer()
    elif callback.data == "get_help":
        await callback.message.answer("📖 <b>Инструкция:</b>\n1. Бот сам даст прогноз в 11:30.\n2. Выбери свой прогноз и тейк.\n3. Бот будет следить за ценой каждые 5 минут и сообщит, если сработает стоп или тейк.", parse_mode="HTML")
        await callback.answer()
    elif callback.data == "get_forecast":
        await send_forecast(callback.message, is_manual=True)
        await callback.answer()
    elif callback.data == "get_report":
        stats = get_stats()
        await callback.message.answer(f"📊 Бот: {stats.get('BOT', 0)} | Вы: {stats.get('USER', 0)} | Тот: {stats.get('MODEL_2', 0)}", parse_mode="HTML")
        await callback.answer()
        
    elif callback.data == "user_LONG" or callback.data == "user_SHORT":
        prediction = callback.data.split("_")[1]
        update_user_prediction(date, prediction)
        builder = InlineKeyboardBuilder()
        for p in [1, 2, 3, 5, 10]:
            builder.button(text=f"+{p}%", callback_data=f"user_tp_{p}")
        builder.button(text="Свой вариант", callback_data="user_tp_custom")
        builder.adjust(3, 2)
        await callback.message.answer(f"✅ Твой прогноз: <b>{prediction}</b>.\nНа сколько далеко твой тейк-профит?", reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.answer()

    elif callback.data == "model2_LONG" or callback.data == "model2_SHORT":
        prediction = callback.data.split("_")[1]
        update_model2_prediction(date, prediction)
        builder = InlineKeyboardBuilder()
        for p in [1, 2, 3, 5, 10]:
            builder.button(text=f"+{p}%", callback_data=f"model2_tp_{p}")
        builder.button(text="Свой вариант", callback_data="model2_tp_custom")
        builder.adjust(3, 2)
        await callback.message.answer(f"✅ Прогноз The One Trade: <b>{prediction}</b>.\nНа сколько далеко их тейк?", reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.answer()

    elif callback.data.startswith("user_tp_"):
        val = callback.data.split("_")[2]
        if val == "custom":
            await callback.message.answer("Напиши в чат просто число (например: 7), и я запишу тейк +7%.")
        else:
            update_user_levels(date, float(val))
            await callback.message.answer(f"✅ Записал твой тейк: +{val}%. Бот будет следить за ценой!")
        await callback.answer()

    elif callback.data.startswith("model2_tp_"):
        val = callback.data.split("_")[2]
        if val == "custom":
            await callback.message.answer("Напиши в чат просто число (например: 7), и я запишу тейк Тота +7%.")
        else:
            update_model2_levels(date, float(val))
            await callback.message.answer(f"✅ Записал тейк The One Trade: +{val}%. Бот будет следить за ценой!")
        await callback.answer()

@dp.message()
async def handle_text(message: types.Message):
    text = message.text.lower().strip()
    date = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    
    if any(word in text for word in ["цена", "курс", "price", "стоит"]):
        await message.answer(get_price_and_symbol(text), parse_mode="HTML")
        return
        
    if "мой прогноз" in text:
        builder = InlineKeyboardBuilder()
        builder.button(text="🟢 Мой прогноз LONG", callback_data="user_LONG")
        builder.button(text="🔴 Мой прогноз SHORT", callback_data="user_SHORT")
        await message.answer("Выбери свой прогноз на сегодня:", reply_markup=builder.as_markup())
        return
        
    if "the one" in text or "traide" in text or "trade" in text or text == "тот":
        builder = InlineKeyboardBuilder()
        builder.button(text="🟢 The One Trade: LONG", callback_data="model2_LONG")
        builder.button(text="🔴 The One Trade: SHORT", callback_data="model2_SHORT")
        await message.answer("Выбери прогноз от The One Trade:", reply_markup=builder.as_markup())
        return

    if text.isdigit():
        percent = float(text)
        update_user_levels(date, percent)
        update_model2_levels(date, percent)
        await message.answer(f"✅ Записал тейк +{percent}% для твоего прогноза и для The One Trade.")
        return

    await message.answer("Я тебя не понял. Используй меню ниже.")

# --- УТРЕННИЙ ПРОГНОЗ ---
async def send_forecast(message_obj, is_manual=False):
    direction, price, sl, tp, reason = get_rsi_prediction()
    if direction == "WAIT":
        await message_obj.answer(f"⏳ <b>Анализ рынка:</b>\n\n<i>{reason}</i>\nСигнала на вход нет.", parse_mode="HTML")
        return
    if direction == "ERROR":
        await message_obj.answer("Ошибка получения данных с биржи.")
        return

    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    update_bot_prediction(today, direction, price, sl, tp)
    
    f_price = f"{price:,.2f}".replace(',', ' ')
    f_sl = f"{sl:,.2f}".replace(',', ' ')
    f_tp = f"{tp:,.2f}".replace(',', ' ')
    
    text = (
        f"🌅 <b>Мой анализ:</b>\n\n"
        f"📊 Прогноз: <b>{direction}</b>\n"
        f"💰 Цена входа: <b>{f_price} USD</b>\n"
        f"🛑 Мой стоп: <i>{f_sl} USD</i>\n"
        f"🎯 Мой тейк: <i>{f_tp} USD</i>\n"
        f"💡 Причина: <i>{reason}</i>\n\n"
        f"<b>Сделай свой выбор:</b>"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 Мой: LONG", callback_data="user_LONG")
    builder.button(text="🔴 Мой: SHORT", callback_data="user_SHORT")
    builder.button(text="🟢 Тот: LONG", callback_data="model2_LONG")
    builder.button(text="🔴 Тот: SHORT", callback_data="model2_SHORT")
    builder.adjust(2, 2)
    
    await message_obj.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

async def morning_prediction():
    if CHAT_ID:
        await send_forecast(bot, is_manual=False)

# --- ФОНОВЫЙ МОНИТОРИНГ ---
async def monitor_market():
    date = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    data = get_today_data()
    if not data or not data[1]:
        return

    try:
        ohlcv = exchange.fetch_ohlcv('BTC/USD', timeframe='5m', limit=2)
        last_candle = ohlcv[-1]
        high = last_candle[2]
        low = last_candle[3]
    except Exception as e:
        print(f"Ошибка мониторинга: {e}")
        return

    updates = []

    if data[13] == 'pending' and data[3] and data[4]:
        if data[1] == 'LONG':
            if low <= data[3]: updates.append(('status_bot', 'loss', "Бот: Стоп-лосс сработал ❌"))
            elif high >= data[4]: updates.append(('status_bot', 'win', "Бот: Тейк-профит достигнут ✅"))
        elif data[1] == 'SHORT':
            if high >= data[3]: updates.append(('status_bot', 'loss', "Бот: Стоп-лосс сработал ❌"))
            elif low <= data[4]: updates.append(('status_bot', 'win', "Бот: Тейк-профит достигнут ✅"))

    if data[14] == 'pending' and data[7] and data[8]:
        if data[5] == 'LONG':
            if low <= data[7]: updates.append(('status_user', 'loss', "Вы: Стоп-лосс сработал ❌"))
            elif high >= data[8]: updates.append(('status_user', 'win', "Вы: Тейк-профит достигнут ✅"))
        elif data[5] == 'SHORT':
            if high >= data[7]: updates.append(('status_user', 'loss', "Вы: Стоп-лосс сработал ❌"))
            elif low <= data[8]: updates.append(('status_user', 'win', "Вы: Тейк-профит достигнут ✅"))

    if data[15] == 'pending' and data[11] and data[12]:
        if data[9] == 'LONG':
            if low <= data[11]: updates.append(('status_model2', 'loss', "The One Trade: Стоп-лосс сработал ❌"))
            elif high >= data[12]: updates.append(('status_model2', 'win', "The One Trade: Тейк-профит достигнут ✅"))
        elif data[9] == 'SHORT':
            if high >= data[11]: updates.append(('status_model2', 'loss', "The One Trade: Стоп-лосс сработал ❌"))
            elif low <= data[12]: updates.append(('status_model2', 'win', "The One Trade: Тейк-профит достигнут ✅"))

    if updates:
        conn = get_db()
        cursor = conn.cursor()
        msg_text = ""
        for field, status, msg in updates:
            cursor.execute(f'UPDATE daily_predictions SET {field} = ? WHERE date = ?', (status, date))
            msg_text += msg + "\n"
        conn.commit()
        conn.close()
        
        if CHAT_ID:
            await bot.send_message(chat_id=CHAT_ID, text=f"🚨 <b>Внимание!</b>\n\n{msg_text}", parse_mode="HTML")

async def handle_web(request):
    return web.Response(text="Bot is running!")

async def main():
    scheduler = AsyncIOScheduler(timezone=MINSK_TZ)
    scheduler.add_job(morning_prediction, 'cron', hour=11, minute=30)
    scheduler.add_job(monitor_market, 'cron', minute='*/5')
    scheduler.start()

    app = web.Application()
    app.router.add_get('/', handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    
    print("Бот запущен с мониторингом...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
