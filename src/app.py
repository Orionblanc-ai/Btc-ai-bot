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

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = None 

exchange = ccxt.kraken()
MINSK_TZ = pytz.timezone('Europe/Minsk')

# Глобальная переменная для ожидания ответа о цене Славянки
waiting_for_slav_price = False

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

def add_asset(asset, amount, buy_price, asset_type, location="exchange"):
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

# --- ПЛАН ИЗ ВАШЕЙ ТАБЛИЦЫ ---
PLAN_DATA = {
    (2026, 11): 2393.38, (2026, 12): 2642.98, (2027, 1): 2907.56, (2027, 2): 3188.01, (2027, 3): 3485.29, (2027, 4): 3800.41, (2027, 5): 4134.43, (2027, 6): 4488.50, (2027, 7): 4863.81, (2027, 8): 5261.64, (2027, 9): 5683.34, (2027, 10): 6130.34, (2027, 11): 6604.16, (2027, 12): 7106.41, (2028, 1): 7638.79, (2028, 2): 8203.12, (2028, 3): 8801.31, (2028, 4): 9435.39, (2028, 5): 10107.51, (2028, 6): 10819.96, (2028, 7): 11575.16, (2028, 8): 12375.67, (2028, 9): 13224.21, (2028, 10): 14123.66, (2028, 11): 15077.08, (2028, 12): 16087.70, (2029, 1): 17158.97, (2029, 2): 18294.50, (2029, 3): 19498.17, (2029, 4): 20774.06, (2029, 5): 22126.51, (2029, 6): 23560.10, (2029, 7): 25079.70, (2029, 8): 26690.49, (2029, 9): 28397.92, (2029, 10): 30207.79, (2029, 11): 32126.26, (2029, 12): 34159.83, (2030, 1): 36315.42, (2030, 2): 38600.35, (2030, 3): 41022.37, (2030, 4): 43589.71, (2030, 5): 46311.09, (2030, 6): 49195.76, (2030, 7): 52253.51, (2030, 8): 55494.72, (2030, 9): 58930.40, (2030, 10): 62572.22, (2030, 11): 66432.56, (2030, 12): 68528.53
}
TOTAL_GOAL = 100000.0

def get_plan_for_month(year, month):
    return PLAN_DATA.get((year, month), 0.0)

# --- ПОЛУЧЕНИЕ ЦЕН ---
def get_crypto_price(symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker['last'])
    except: return None

def get_gold_price_usd():
    try:
        r = requests.get('https://api.nbrb.by/metals/ingots/prices', timeout=10)
        data = r.json()
        for item in data:
            if item.get('Metal') == 'Золото' and item.get('ItemType') == 'слиток':
                return float(item.get('NoCertificateDollars', 0))
        return None
    except: return None

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
    global CHAT_ID, waiting_for_slav_price
    CHAT_ID = message.chat.id
    waiting_for_slav_price = False
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Мой портфель", callback_data="show_portfolio")
    builder.button(text="🎯 План-Факт", callback_data="show_plan_fact")
    builder.button(text="❓ Как вводить", callback_data="help_add")
    builder.button(text="🗑 Очистить всё", callback_data="clear_db")
    builder.adjust(2, 2)
    
    await message.answer(
        "🤖 Привет! Я твой портфельный менеджер.\n\n"
        "Примеры команд:\n"
        "• <code>Баланс на бирже 1880</code>\n"
        "• <code>Купил 0,001 ВТС по 78 000</code>\n"
        "• <code>Купил 4 грамма золота за 700</code>\n"
        "• <code>Купил 3 славянки за 450</code>",
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
        await callback.message.answer("Пиши: «Баланс на бирже 1880», «Купил 0,1 ВТС по 75000» или «Купил 4 грамма золота за 700».")
        await callback.answer()
    elif callback.data == "clear_db":
        clear_db()
        await callback.message.answer("🗑 Все данные очищены.")
        await callback.answer()

@dp.message()
async def handle_text(message: types.Message):
    global waiting_for_slav_price
    text = message.text.strip().lower()
    
    # Если бот ждет цену Славянки
    if waiting_for_slav_price:
        try:
            price = clean_number(text)
            set_setting('slavyanka_price', price)
            waiting_for_slav_price = False
            await message.answer(f"✅ Цена выкупа Славянки сохранена: <b>{price:,.2f} USD</b>. Теперь я буду считать её стоимость по этой цене.", parse_mode="HTML")
        except:
            await message.answer("Пожалуйста, напиши просто число (например: 150).")
        return

    # 1. Баланс биржи
    balance_match = re.search(r'(?:баланс\s+биржи|баланс\s+на\s+бирже|на\s+бирже)\s+([\d\s.,]+)', text)
    if balance_match:
        try:
            amount = clean_number(balance_match.group(1))
            set_exchange_balance(amount)
            await message.answer(f"✅ Общая сумма на бирже установлена: <b>{amount:,.2f} USD</b>", parse_mode="HTML")
        except: await message.answer("Не смог распознать сумму.")
        return

    # 2. Продажа
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

    # 3. Покупка
    match = re.search(r'купил\s+(.+?)\s+(?:по|за)\s+([\d\s.,]+)', text, re.IGNORECASE)
    if match:
        part1 = match.group(1) 
        try:
            total_cost = clean_number(match.group(2))
            numbers = re.findall(r'[\d\s.,]+', part1)
            if not numbers: return
            amount = clean_number(numbers[0])
            
            raw_name = re.sub(r'[\d\s.,]+', '', part1).strip()
            asset_name, asset_type, location = parse_asset_name(raw_name)
            
            if location == 'physical':
                unit_price = total_cost / amount
            else:
                unit_price = total_cost
                
            add_asset(asset_name, amount, unit_price, asset_type, location)
            
            if asset_name == 'СЛАВЯНКА':
                waiting_for_slav_price = True
                await message.answer(f"✅ Добавлено: <b>{amount} СЛАВЯНКА</b>.\n\nА какая сейчас <b>цена выкупа</b> Славянки в долларах? Напиши числом (например: 150).", parse_mode="HTML")
            else:
                await message.answer(f"✅ Добавлено: <b>{amount} {asset_name}</b>. Средняя цена за 1 ед.: <b>{unit_price:,.2f}$</b>", parse_mode="HTML")
        except: pass
        return
        
    if "портфель" in text: await show_portfolio(message)
    elif "план" in text: await show_plan_fact(message)
    else: await message.answer("Я не понял. Напиши «Портфель» или «План».")

async def show_portfolio(message: types.Message):
    portfolio = get_portfolio()
    total_exchange_balance = get_exchange_balance()
    if not portfolio and total_exchange_balance == 0:
        await message.answer("Портфель пуст. Начни с: «Баланс на бирже 1880»")
        return
        
    gold_price_usd = get_gold_price_usd()
    slav_price = get_setting('slavyanka_price', 150.0)
    
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
        elif asset_type == 'metal': current_price = gold_price_usd
        elif asset_type == 'coin': current_price = slav_price 
            
        if current_price:
            current_total = amount * current_price
            profit = current_total - (amount * avg_buy_price)
            sign = "🟢" if profit >= 0 else "🔴"
            line = f"🔸 <b>{asset}</b>: {amount} ед.\n   Среди. покупка: {avg_buy_price:,.2f} | Сейчас: {current_price:,.2f}\n   Стоимость: {current_total:,.2f} {sign} ({profit:+,.2f})\n"
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
    
    free_cash = total_exchange_balance - exchange_assets_value
    free_cash_text = "0.00 USD" if free_cash < 0 else f"{free_cash:,.2f} USD"
    total_capital = total_exchange_balance + physical_value
    
    final_text = f"📊 <b>Твой портфель:</b>\n\n{text_exchange}"
    final_text += f"   📈 Активов на бирже: {exchange_assets_value:,.2f} USD\n"
    final_text += f"   💵 Свободный кэш: {free_cash_text}\n"
    final_text += f"   🏦 Всего на бирже (с кэшем): {total_exchange_balance:,.2f} USD\n\n{text_physical}"
    final_text += f"   📈 Физика: {physical_value:,.2f} USD\n\n"
    final_text += f"💰 <b>ОБЩИЙ КАПИТАЛ: {total_capital:,.2f} USD</b>"
    await message.answer(final_text, parse_mode="HTML")

async def show_plan_fact(message: types.Message):
    portfolio = get_portfolio()
    total_exchange_balance = get_exchange_balance()
    
    now = datetime.now(MINSK_TZ)
    current_year = now.year
    current_month = now.month
    
    plan_exchange = get_plan_for_month(current_year, current_month)
    
    gold_price_usd = get_gold_price_usd()
    slav_price = get_setting('slavyanka_price', 150.0)
    
    physical_value = 0
    for asset, amount, avg_buy_price, asset_type, location in portfolio:
        if location == 'physical':
            current_price = None
            if asset_type == 'metal': current_price = gold_price_usd
            elif asset_type == 'coin': current_price = slav_price
            if current_price: physical_value += amount * current_price

    fact_total = total_exchange_balance + physical_value
    
    diff_exchange = total_exchange_balance - plan_exchange
    sign_ex = "🟢" if diff_exchange >= 0 else "🔴"
    
    remaining_to_goal = TOTAL_GOAL - fact_total
    if remaining_to_goal < 0: remaining_to_goal = 0
    
    today_str = now.strftime("%d.%m.%Y")
    
    text = (
        f"🎯 <b>План-Факт на {today_str}</b>\n\n"
        f"🏦 <b>Биржа (план на месяц):</b> {plan_exchange:,.2f} USD\n"
        f"   Факт по бирже: {total_exchange_balance:,.2f} USD\n"
        f"   Отклонение: <b>{diff_exchange:+,.2f} USD</b> {sign_ex}\n\n"
        f"💰 <b>Общая цель:</b> {TOTAL_GOAL:,.2f} USD\n"
        f"   Общий факт: {fact_total:,.2f} USD\n"
        f"   Осталось накопить: <b>{remaining_to_goal:,.2f} USD</b>"
    )
    await message.answer(text, parse_mode="HTML")

async def handle_web(request):
    return web.Response(text="Bot is running!")

async def main():
    app = web.Application()
    app.router.add_get('/', handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
