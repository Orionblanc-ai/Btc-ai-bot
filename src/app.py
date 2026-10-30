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

# Используем Kraken (не блокирует серверы Render)
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

# --- ФУНКЦИЯ ПОЛУЧЕНИЯ ЦЕНЫ ---
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

# --- РАСЧЕТ RSI И СТОПОВ ---
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
        
        # Волатильность для стопов
        recent_highs = [candle[2] for candle in ohlcv[-14:]]
        recent_lows = [candle[3] for candle in ohlcv[-14:]]
        volatility = (sum(recent_highs) - sum(recent_lows)) / 14
        if volatility == 0: volatility = current_price * 0.015
        
        # Новые уровни: 35 и 65
        if rsi < 35:
            stop_loss = current_price - (volatility * 1.5)
            take_profit = current_price + (volatility * 3)
            return "LONG", current_price, stop_loss, take_profit, f"RSI = {rsi:.2f} (перепродан)"
        elif rsi > 65:
            stop_loss = current_price + (volatility * 1.5)
            take_profit = current_price - (volatility * 3)
            return "SHORT", current_price, stop_loss, take_profit, f"RSI = {rsi:.2f} (перекуплен)"
        else:
            return "WAIT", current_price, 0, 0, f"RSI = {rsi:.2f} (нет сигнала)"
    except Exception as e:
        print(f"Ошибка RSI: {e}")
        return "ERROR", 0, 0, 0, "Ошибка данных"

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
    builder.button(text="📊 Моя статистика", callback_data="get_report")
    builder.button(text="❓ Помощь", callback_data="get_help")
    builder.adjust(2, 2) # Красиво выстраиваем кнопки в 2 ряда
    
    await message.answer(
        "🤖 Привет! Я твой продвинутый ИИ-помощник.\n\n"
        "Я анализирую рынок в 11:30 (считаю RSI и стопы).\n"
        "Ты можешь запросить анализ в любой момент кнопкой «Прогноз сейчас».\n\n"
        "В 23:59 я подведу итоги и сравню, кто был прав.",
        reply_markup=builder.as_markup()
    )

@dp.callback_query()
async def handle_callbacks(callback: CallbackQuery):
    date = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    
    if callback.data == "get_price":
        await callback.message.answer(get_price_and_symbol("btc"), parse_mode="HTML")
        await callback.answer()
        
    elif callback.data == "get_help":
        await callback.message.answer(
            "📖 <b>Как пользоваться ботом:</b>\n\n"
            "1. Я сам пришлю прогноз в 11:30.\n"
            "2. Нажми «Мой прогноз» (LONG/SHORT) и «Тот» для The One Trade.\n"
            "3. В 23:59 я пришлю итоговый отчет.\n"
            "4. Кнопка «Цена» покажет курс BTC, ETH или SOL.", 
            parse_mode="HTML"
        )
        await callback.answer()
        
    elif callback.data == "get_forecast":
        direction, price, sl, tp, reason = get_rsi_prediction()
        if direction == "WAIT":
            await callback.message.answer(f"⏳ <b>Анализ рынка:</b>\n\n<i>{reason}</i>\nСигнала на вход нет.", parse_mode="HTML")
        elif direction == "ERROR":
            await callback.message.answer("Ошибка получения данных с биржи.")
        else:
            f_price = f"{price:,.2f}".replace(',', ' ')
            f_sl = f"{sl:,.2f}".replace(',', ' ')
            f_tp = f"{tp:,.2f}".replace(',', ' ')
            text = (
                f"📈 <b>Анализ рынка:</b>\n\n"
                f"📊 Прогноз: <b>{direction}</b>\n"
                f"💰 Цена входа: <b>{f_price} USD</b>\n"
                f"🛑 Стоп-лосс: <i>{f_sl} USD</i>\n"
                f"🎯 Тейк-профит: <i>{f_tp} USD</i>\n"
                f"💡 Причина: <i>{reason}</i>"
            )
            await callback.message.answer(text, parse_mode="HTML")
        await callback.answer()
        
    elif callback.data == "get_report":
        stats = get_stats()
        bot_wins = stats.get("BOT", 0)
        user_wins = stats.get("USER", 0)
        model2_wins = stats.get("MODEL_2", 0)
        await callback.message.answer(
            f"📊 <b>Общая статистика побед:</b>\n"
            f"🤖 Бот: {bot_wins}\n"
            f"👤 Вы: {user_wins}\n"
            f"🧠 The One Trade: {model2_wins}", parse_mode="HTML"
        )
        await callback.answer()
        
    elif callback.data.startswith("user_"):
        prediction = callback.data.split("_")[1]
        update_user_prediction(date, prediction)
        await callback.message.answer(f"✅ Твой прогноз на сегодня: <b>{prediction}</b>. Записал!", parse_mode="HTML")
        await callback.answer()
        
    elif callback.data.startswith("model2_"):
        prediction = callback.data.split("_")[1]
        update_model2_prediction(date, prediction)
        await callback.message.answer(f"✅ Прогноз The One Trade на сегодня: <b>{prediction}</b>. Записал!", parse_mode="HTML")
        await callback.answer()

@dp.message(Command("report"))
async def cmd_report(message: types.Message):
    stats = get_stats()
    await message.answer(f"📊 Бот: {stats.get('BOT', 0)} | Вы: {stats.get('USER', 0)} | Тот: {stats.get('MODEL_2', 0)}")

@dp.message()
async def handle_text(message: types.Message):
    text = message.text.lower().strip()
    
    if any(word in text for word in ["цена", "курс", "price", "стоит"]):
        await message.answer(get_price_and_symbol(text), parse_mode="HTML")
        return
        
    if "мой прогноз" in text or "я думаю" in text:
        builder = InlineKeyboardBuilder()
        builder.button(text="🟢 Мой прогноз LONG", callback_data="user_LONG")
        builder.button(text="🔴 Мой прогноз SHORT", callback_data="user_SHORT")
        await message.answer("Выбери свой прогноз на сегодня:", reply_markup=builder.as_markup())
        return
        
    if "the one" in text or "traide" in text or "trade" in text or "модель" in text or text == "тот":
        builder = InlineKeyboardBuilder()
        builder.button(text="🟢 The One Trade: LONG", callback_data="model2_LONG")
        builder.button(text="🔴 The One Trade: SHORT", callback_data="model2_SHORT")
        await message.answer("Выбери прогноз от The One Trade:", reply_markup=builder.as_markup())
        return

    await message.answer("Я тебя не понял. Используй меню ниже или напиши «Цена».")

# --- УТРЕННИЙ ПРОГНОЗ В 11:30 С КНОПКАМИ ---
async def morning_prediction():
    direction, price, sl, tp, reason = get_rsi_prediction()
    
    if direction in ["ERROR", "WAIT"]:
        if CHAT_ID: await bot.send_message(chat_id=CHAT_ID, text=f"⏳ <b>Утренний анализ:</b>\n\n<i>{reason}</i>\nСигнала на вход нет.", parse_mode="HTML")
        return

    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    update_bot_prediction(today, direction, price)
    
    f_price = f"{price:,.2f}".replace(',', ' ')
    f_sl = f"{sl:,.2f}".replace(',', ' ')
    f_tp = f"{tp:,.2f}".replace(',', ' ')
    
    text = (
        f"🌅 <b>Доброе утро! Мой анализ:</b>\n\n"
        f"📊 Прогноз: <b>{direction}</b>\n"
        f"💰 Цена входа: <b>{f_price} USD</b>\n"
        f"🛑 Стоп-лосс: <i>{f_sl} USD</i>\n"
        f"🎯 Тейк-профит: <i>{f_tp} USD</i>\n"
        f"💡 Причина: <i>{reason}</i>\n\n"
        f"<b>Сделай свой выбор:</b>"
    )
    
    # Сразу добавляем кнопки в утренний прогноз
    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 Мой: LONG", callback_data="user_LONG")
    builder.button(text="🔴 Мой: SHORT", callback_data="user_SHORT")
    builder.button(text="🟢 Тот: LONG", callback_data="model2_LONG")
    builder.button(text="🔴 Тот: SHORT", callback_data="model2_SHORT")
    builder.adjust(2, 2)
    
    if CHAT_ID: await bot.send_message(chat_id=CHAT_ID, text=text, reply_markup=builder.as_markup(), parse_mode="HTML")

# --- ВЕЧЕРНЯЯ ПРОВЕРКА В 23:59 ---
async def evening_check():
    today = datetime.now(MINSK_TZ).strftime("%Y-%m-%d")
    data = get_today_data()
    if not data: return

    bot_pred, entry, user_pred, model2_pred, _ = data
    try:
        ticker = exchange.fetch_ticker('BTC/USD')
        close_price = float(ticker['last'])
    except:
        return
    
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
    
    f_entry = f"{entry:,.2f}".replace(',', ' ')
    f_close = f"{close_price:,.2f}".replace(',', ' ')
    
    text = (
        f"🌙 <b>Итоги дня:</b>\n"
        f"Цена входа: {f_entry}\n"
        f"Цена закрытия: {f_close}\n\n"
        f"🤖 Бот: <b>{bot_pred}</b> {'✅' if bot_pred == actual_direction else '❌'}\n"
        f"👤 Вы: <b>{user_pred or '—'}</b> {'✅' if user_pred == actual_direction else '❌'}\n"
        f"🧠 The One Trade: <b>{model2_pred or '—'}</b> {'✅' if model2_pred == actual_direction else '❌'}\n\n"
    )
    if winner == "NONE":
        text += "🤷 Победила дружба (все ошиблись или не вводили прогноз)."
    else:
        text += f"🏆 Победитель: <b>{winner}!</b>"
        
    if CHAT_ID: await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML")

async def handle_web(request):
    return web.Response(text="Bot is running!")

async def main():
    scheduler = AsyncIOScheduler(timezone=MINSK_TZ)
    # Время изменено на 11:30
    scheduler.add_job(morning_prediction, 'cron', hour=11, minute=30)
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
