import asyncio
import os
import re
import sqlite3
import ccxt
import requests
import yfinance as yf
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import CallbackQuery
from datetime import datetime
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = None 

exchange = ccxt.kraken()
MINSK_TZ = pytz.timezone('Europe/Minsk')

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT UNIQUE,
            amount REAL,
            avg_buy_price REAL,
            asset_type TEXT,
            location TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exchange_balance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value REAL
        )
    ''')
    conn.commit()
    conn.close()

def add_asset(asset, amount, buy_price, asset_type, location):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, amount, avg_buy_price FROM portfolio WHERE asset = ?', (asset,))
    row = cursor.fetchone()
    
    if row:
        current_id, current_amount, current_avg_price = row
        new_total_amount = current_amount + amount
        new_avg_price = ((current_amount * current_avg_price) + (amount * buy_price)) / new_total_amount
        cursor.execute('UPDATE portfolio SET amount = ?, avg_buy_price = ? WHERE id = ?', (new_total_amount, new_avg_price, current_id))
    else:
        cursor.execute('INSERT INTO portfolio (asset, amount, avg_buy_price, asset_type, location) VALUES (?, ?, ?, ?, ?)', (asset, amount, buy_price, asset_type, location))
    conn.commit()
    conn.close()

def sell_asset(asset, amount):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, amount, avg_buy_price FROM portfolio WHERE asset = ?', (asset,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return "not_found"
    current_id, current_amount, current_avg_price = row
    
    if amount > current_amount:
        conn.close()
        return "too_much"
    if amount == current_amount:
        cursor.execute('DELETE FROM portfolio WHERE id = ?', (current_id,))
        conn.commit()
        conn.close()
        return "all_sold"
    else:
        new_amount = current_amount - amount
        cursor.execute('UPDATE portfolio SET amount = ? WHERE id = ?', (new_amount, current_id))
        conn.commit()
        conn.close()
        return "part_sold"

def set_exchange_balance(amount):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM exchange_balance')
    cursor.execute('INSERT INTO exchange_balance (amount) VALUES (?)', (amount,))
    conn.commit()
    conn.close()

def get_exchange_balance():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT amount FROM exchange_balance ORDER BY id DESC LIMIT 1')
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0.0

def get_portfolio():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT asset, amount, avg_buy_price, asset_type, location FROM portfolio')
    rows = cursor.fetchall()
    conn.close()
    return rows

def set_setting(key, value):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()

def get_setting(key, default=0.0):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default

def clear_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM portfolio')
    cursor.execute('DELETE FROM exchange_balance')
    cursor.execute('DELETE FROM settings')
    conn.commit()
    conn.close()

init_db()

# --- ПЛАН ---
PLAN_DATA = {
    (2026, 11): 2393.38, (2026, 12): 2642.98, (2027, 1): 2907.56, (2027, 2): 3188.01, (2027, 3): 3485.29, (2027, 4): 3800.41, (2027, 5): 4134.43, (2027, 6): 4488.50, (2027, 7): 4863.81, (2027, 8): 5261.64, (2027, 9): 5683.34, (2027, 10): 6130.34, (2027, 11): 6604.16, (2027, 12): 7106.41, (2028, 1): 7638.79, (2028, 2): 8203.12, (2028, 3): 8801.31, (2028, 4): 9435.39, (2028, 5): 10107.51, (2028, 6): 10819.96, (2028, 7): 11575.16, (2028, 8): 12375.67, (2028, 9): 13224.21, (2028, 10): 14123.66, (2028, 11): 15077.08, (2028, 12): 16087.70, (2029, 1): 17158.97, (2029, 2): 18294.50, (2029, 3): 19498.17, (2029, 4): 20774.06, (2029, 5): 22126.51, (2029, 6): 23560.10, (2029, 7): 25079.70, (2029, 8): 26690.49, (2029, 9): 28397.92, (2029, 10): 30207.79, (2029, 11): 32126.26, (2029, 12): 34159.83, (2030, 1): 36315.42, (2030, 2): 38600.35, (2030, 3): 41022.37, (2030, 4): 43589.71, (2030, 5): 46311.09, (2030, 6): 49195.76, (2030, 7): 52253.51, (2030, 8): 55494.72, (2030, 9): 58930.40, (2030, 10): 62572.22, (2030, 11): 66432.56, (2030, 12): 68528.53
}
TOTAL_GOAL = 100000.0

START_DATE = datetime(2026, 11, 1)
END_DATE = datetime(2030, 12, 31)
TOTAL_DAYS = (END_DATE - START_DATE).days
DAILY_GROWTH = TOTAL_GOAL / TOTAL_DAYS

def get_plan_for_month(year, month):
    return PLAN_DATA.get((year, month), 0.0)

def get_expected_total_goal():
    now = datetime.now(MINSK_TZ).replace(tzinfo=None)
    if now < START_DATE: return 0.0
    if now > END_DATE: return TOTAL_GOAL
    days_passed = (now - START_DATE).days
    return days_passed * DAILY_GROWTH

# --- ЦЕНЫ ---
def get_crypto_price(symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker['last'])
    except: return None

def get_gold_price_usd():
    try:
        ticker = yf.Ticker("GC=F")
        hist = ticker.history(period="1d")
        if not hist.empty:
            price_per_ounce = float(hist['Close'].iloc[-1])
            price_per_gram = price_per_ounce / 31.1035
            spread = get_setting('gold_spread', 5.0)
            return price_per_gram * (1 + spread / 100)
    except: pass
    return get_setting('gold_last_price', 0.0)

def get_silver_price_usd():
    try:
        ticker = yf.Ticker("SI=F")
        hist = ticker.history(period="1d")
        if not hist.empty:
            price_per_ounce = float(hist['Close'].iloc[-1])
            price_per_gram = price_per_ounce / 31.1035
            spread = get_setting('silver_spread', 30.0)
            return price_per_gram * (1 + spread / 100)
    except: pass
    return get_setting('silver_last_price', 0.0)

def parse_asset_name(raw_name):
    name = raw_name.strip().upper()
    translations = {'ВТС': 'BTC', 'БИТКОИН': 'BTC', 'ЕТН': 'ETH', 'ЭФИР': 'ETH', 'СОЛ': 'SOL', 'СОЛАНА': 'SOL'}
    if name in translations: name = translations[name]
    
    if "ЗОЛОТ" in name or "GOLD" in name: return 'GOLD', 'metal', 'physical'
    elif "СЛАВЯНКА" in name or "МОНЕТ" in name: return 'СЛАВЯНКА', 'coin', 'physical'
    elif len(name) <= 5 and name not in ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'USDT']: return name, 'stock', 'exchange'
    else: return name, 'crypto', 'exchange'

def clean_number(val_str):
    val_str = val_str.replace(' ', '')
    if ',' in val_str:
        val_str = val_str.replace('.', '').replace(',', '.')
        return float(val_str)
    if '.' in val_str:
        parts = val_str.split('.')
        if len(parts) == 2 and len(parts[1]) == 3:
            val_str = parts[0] + parts[1]
    return float(val_str)

# --- БОТ ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global CHAT_ID
    CHAT_ID = message.chat.id
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Мой портфель", callback_data="show_portfolio")
    builder.button(text="🎯 План-Факт", callback_data="show_plan_fact")
    builder.button(text="❓ Как вводить", callback_data="help_add")
    builder.button(text="🗑 Очистить всё", callback_data="clear_db")
    builder.adjust(2, 2)
    
    await message.answer(
        "👋 <b>Добро пожаловать в Orion 2.0!</b>\n\n"
        "Я твой личный инвест-помощник. Вот что я умею:\n\n"
        "💼 <b>Считать портфель:</b> складываю крипту, акции и физические активы (золото, монеты).\n"
        "📈 <b>Следить за рынком:</b> беру актуальные цены с бирж и НБРБ.\n"
        "🎯 <b>Сравнивать с планом:</b> показываю, идёшь ли ты по графику к цели в 100 000$.\n"
        "📅 <b>Присылать отчет:</b> каждое 1-е число в 10:00.\n\n"
        "👇 <b>Нажми кнопки ниже, чтобы начать!</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query()
async def handle_callbacks(callback: CallbackQuery):
    if callback.data == "show_portfolio":
        await show_portfolio(callback.message)
        await callback.answer()
    elif callback.data == "show_plan_fact":
        await show_plan_fact(callback.message)
        await callback.answer()
    elif callback.data == "help_add":
        await callback.message.answer(
            "📝 <b>Как вводить данные:</b>\n\n"
            "1. Установи сумму на бирже: <code>На бирже 2000</code>\n"
            "2. Купи актив: <code>Купил 0,001 ВТС по 78000</code>\n"
            "3. Купи физику: <code>Купил 4 грамма золота за 700</code>\n\n"
            "Чтобы посмотреть портфель, нажми кнопку «📊 Мой портфель».",
            parse_mode="HTML"
        )
        await callback.answer()
    elif callback.data == "clear_db":
        clear_db()
        await callback.message.answer("🗑 <b>Все данные очищены.</b> Теперь можно начать заново.", parse_mode="HTML")
        await callback.answer()

@dp.message()
async def handle_text(message: types.Message):
    text = message.text.strip().lower()
    
    balance_match = re.search(r'(?:баланс\s+биржи|баланс\s+на\s+бирже|на\s+бирже)\s+([\d\s.,]+)', text)
    if balance_match:
        try:
            amount = clean_number(balance_match.group(1))
            set_exchange_balance(amount)
            await message.answer(f"✅ Общая сумма на бирже установлена: <b>{int(amount):,} USD</b>", parse_mode="HTML")
        except: await message.answer("Не смог распознать сумму.")
        return

    sell_match = re.search(r'продал\s+(.+?)\s+(?:по|за)\s+([\d\s.,]+)', text, re.IGNORECASE)
    if sell_match:
        part1 = sell_match.group(1)
        try:
            sell_price = clean_number(sell_match.group(2))
            numbers = re.findall(r'[\d\s.,]+', part1)
            if not numbers: return
            amount = clean_number(numbers[0])
            raw_name = re.sub(r'[\d\s.,]+', '', part1).strip()
            asset_name, _, _ = parse_asset_name(raw_name)
                
            result = sell_asset(asset_name, amount)
            if result == "not_found": await message.answer(f"❌ У тебя нет актива <b>{asset_name}</b>.", parse_mode="HTML")
            elif result == "too_much": await message.answer(f"❌ Ты пытаешься продать больше, чем есть.", parse_mode="HTML")
            elif result == "all_sold": await message.answer(f"✅ Продано всё: <b>{amount} {asset_name}</b>.", parse_mode="HTML")
            else: await message.answer(f"✅ Продано: <b>{amount} {asset_name}</b>.", parse_mode="HTML")
        except: pass
        return

    match = re.search(r'купил\s+(.+?)\s+(по|за)\s+([\d\s.,]+)', text, re.IGNORECASE)
    if match:
        part1 = match.group(1) 
        prep = match.group(2).lower()
        try:
            val = clean_number(match.group(3))
            numbers = re.findall(r'[\d\s.,]+', part1)
            if not numbers: return
            amount = clean_number(numbers[0])
            
            raw_name = re.sub(r'[\d\s.,]+', '', part1).strip()
            asset_name, asset_type, location = parse_asset_name(raw_name)
            
            if "СЛАВЯНКА" in raw_name.upper() or "МОНЕТ" in raw_name.upper():
                asset_name = 'СЛАВЯНКА'
                asset_type = 'coin'
                location = 'physical'

            if prep == "за" or location == 'physical':
                unit_price = val / amount
            else:
                unit_price = val
                
            add_asset(asset_name, amount, unit_price, asset_type, location)
            await message.answer(f"✅ Добавлено: <b>{amount} {asset_name}</b> (по {int(unit_price)}$)", parse_mode="HTML")
        except: pass
        return
        
    if "портфель" in text: await show_portfolio(message)
    elif "план" in text: await show_plan_fact(message)
    else: await message.answer("Я не понял. Напиши «Портфель» или «План».")

async def show_portfolio(message: types.Message):
    portfolio = get_portfolio()
    total_exchange_balance = get_exchange_balance()
    if not portfolio and total_exchange_balance == 0:
        await message.answer("Портфель пуст. Начни с: «На бирже 2000»")
        return
        
    gold_price = get_gold_price_usd()
    silver_price = get_silver_price_usd()
    
    exchange_assets_value = 0
    physical_value = 0
    text_exchange = "🏦 <b>Биржевой счет:</b>\n"
    text_physical = "🏠 <b>Физические активы:</b>\n"
    has_exchange, has_physical = False, False

    for asset, amount, avg_buy_price, asset_type, location in portfolio:
        current_price = None
        if asset_type == 'crypto': current_price = get_crypto_price(f"{asset}/USD")
        elif asset_type == 'stock':
            try:
                hist = yf.Ticker(asset).history(period="1d")
                if not hist.empty: current_price = float(hist['Close'].iloc[-1])
            except: pass
        elif asset_type == 'metal': current_price = gold_price
        elif asset_type == 'coin': current_price = silver_price 
            
        if current_price and current_price > 0:
            current_total = amount * current_price
            profit = current_total - (amount * avg_buy_price)
            sign = "🟢" if profit >= 0 else "🔴"
            line = f"🔸 <b>{asset}</b>: {amount}\n   Вход: {int(avg_buy_price)}$ | Тек: {int(current_price)}$ ({sign} {int(profit):+}$)\n"
            if location == 'exchange':
                text_exchange += line
                exchange_assets_value += current_total
                has_exchange = True
            else:
                text_physical += line
                physical_value += current_total
                has_physical = True

    if not has_exchange: text_exchange += "   (Пусто)\n"
    if not has_physical: text_physical += "   (Пусто)\n"
    
    total_capital = total_exchange_balance + physical_value
    
    final_text = f"📊 <b>Твой портфель:</b>\n\n"
    final_text += f"🏦 На бирже: <b>{int(total_exchange_balance):,} USD</b>\n"
    final_text += f"{text_exchange}\n"
    final_text += f"{text_physical}\n"
    final_text += f"💰 <b>ОБЩИЙ КАПИТАЛ: {int(total_capital):,} USD</b>"
    
    await message.answer(final_text, parse_mode="HTML")

async def show_plan_fact(message: types.Message):
    portfolio = get_portfolio()
    total_exchange_balance = get_exchange_balance()
    
    now = datetime.now(MINSK_TZ)
    current_year = now.year
    current_month = now.month
    
    plan_exchange = get_plan_for_month(current_year, current_month)
    
    gold_price = get_gold_price_usd()
    silver_price = get_silver_price_usd()
    
    physical_value = 0
    for asset, amount, avg_buy_price, asset_type, location in portfolio:
        if location == 'physical':
            current_price = None
            if asset_type == 'metal': current_price = gold_price
            elif asset_type == 'coin': current_price = silver_price
            if current_price and current_price > 0: physical_value += amount * current_price

    fact_total = total_exchange_balance + physical_value
    
    diff_exchange = total_exchange_balance - plan_exchange
    sign_ex = "🟢" if diff_exchange >= 0 else "🔴"
    
    plan_total_linear = get_expected_total_goal()
    diff_total = fact_total - plan_total_linear
    sign_total = "🟢" if diff_total >= 0 else "🔴"
    
    remaining_to_goal = TOTAL_GOAL - fact_total
    if remaining_to_goal < 0: remaining_to_goal = 0
    
    today_str = now.strftime("%d.%m.%Y")
    
    progress = fact_total / TOTAL_GOAL
    bar_length = 10
    filled = int(progress * bar_length)
    bar = "█" * filled + "░" * (bar_length - filled)
    
    text = (
        f"🎯 <b>План-Факт на {today_str}</b>\n\n"
        f"🏦 <b>Биржа:</b>\n"
        f"   Факт: {int(total_exchange_balance):,} USD\n"
        f"   План: {int(plan_exchange):,} USD\n"
        f"   PnL: <b>{int(diff_exchange):+} USD</b> {sign_ex}\n\n"
        f"💰 <b>Общий капитал:</b>\n"
        f"   Факт: {int(fact_total):,} USD\n"
        f"   План: {int(plan_total_linear):,} USD\n"
        f"   PnL: <b>{int(diff_total):+} USD</b> {sign_total}\n\n"
        f"🏆 <b>Цель:</b> {int(TOTAL_GOAL):,} USD\n"
        f"   Осталось: <b>{int(remaining_to_goal):,} USD</b>\n"
        f"📈 Прогресс: [{bar}] {progress*100:.0f}%"
    )
    await message.answer(text, parse_mode="HTML")

# --- АВТОМАТИЧЕСКИЙ ОТЧЕТ 1-ГО ЧИСЛА ---
async def monthly_report():
    if not CHAT_ID:
        return
    
    portfolio = get_portfolio()
    total_exchange_balance = get_exchange_balance()
    
    gold_price = get_gold_price_usd()
    silver_price = get_silver_price_usd()
    
    physical_value = 0
    for asset, amount, avg_buy_price, asset_type, location in portfolio:
        if location == 'physical':
            current_price = None
            if asset_type == 'metal': current_price = gold_price
            elif asset_type == 'coin': current_price = silver_price
            if current_price and current_price > 0: physical_value += amount * current_price

    total_capital = total_exchange_balance + physical_value
    now = datetime.now(MINSK_TZ)
    
    # Берем план на текущий месяц (так как отчет 1-го числа)
    plan_exchange = get_plan_for_month(now.year, now.month)
    
    text = (
        f"📅 <b>Ежемесячный отчет ({now.strftime('%d.%m.%Y')})</b>\n\n"
        f"💼 <b>Твой текущий капитал:</b> {int(total_capital):,} USD\n"
        f"🏦 <b>На бирже:</b> {int(total_exchange_balance):,} USD\n\n"
        f"🎯 <b>План на этот месяц (биржа):</b> {int(plan_exchange):,} USD\n"
        f"🏆 <b>Общая цель:</b> {int(TOTAL_GOAL):,} USD\n\n"
        f"Не забывай пополнять портфель и следить за балансом! 🚀"
    )
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML")

async def handle_web(request):
    return web.Response(text="Bot is running!")

async def main():
    app = web.Application()
    app.router.add_get('/', handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    
    # Настройка планировщика
    scheduler = AsyncIOScheduler(timezone=MINSK_TZ)
    # Отчет 1-го числа каждого месяца в 10:00
    scheduler.add_job(monthly_report, 'cron', day=1, hour=10, minute=0)
    scheduler.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
