import asyncio
import os
import re
import sqlite3
import ccxt
import yfinance as yf
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import CallbackQuery

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = None 

exchange = ccxt.kraken()

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

def clear_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM portfolio')
    cursor.execute('DELETE FROM exchange_balance')
    conn.commit()
    conn.close()

init_db()

# --- ПОЛУЧЕНИЕ ЦЕН ---
def get_crypto_price(symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker['last'])
    except:
        return None

def get_gold_price_per_gram():
    try:
        ticker = yf.Ticker("GC=F")
        hist = ticker.history(period="1d")
        if not hist.empty:
            return float(hist['Close'].iloc[-1]) / 31.1035
        return None
    except:
        return None

# --- УМНЫЙ ПАРСЕР НАЗВАНИЙ ---
def parse_asset_name(raw_name):
    name = raw_name.strip().upper()
    translations = {
        'ВТС': 'BTC', 'БИТКОИН': 'BTC', 'БИТКОЙН': 'BTC',
        'ЕТН': 'ETH', 'ЭФИР': 'ETH', 'ЭФИРИУМ': 'ETH', 
        'СОЛ': 'SOL', 'СОЛАНА': 'SOL',
        'РИПЛ': 'XRP', 'ДОГЕ': 'DOGE'
    }
    if name in translations:
        name = translations[name]
        
    if "ЗОЛОТ" in name or "GOLD" in name:
        return 'GOLD', 'metal', 'physical'
    elif "СЛАВЯНКА" in name or "МОНЕТ" in name:
        return 'СЛАВЯНКА', 'coin', 'physical'
    elif len(name) <= 5 and name not in ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'USDT']:
        return name, 'stock', 'exchange'
    else:
        return name, 'crypto', 'exchange'

# --- БОТ ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global CHAT_ID
    CHAT_ID = message.chat.id
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Мой портфель", callback_data="show_portfolio")
    builder.button(text="❓ Как вводить", callback_data="help_add")
    builder.button(text="🗑 Очистить всё", callback_data="clear_db")
    
    await message.answer(
        "🤖 Привет! Я твой портфельный менеджер.\n\n"
        "Примеры команд:\n"
        "• <code>Баланс на бирже 2000</code>\n"
        "• <code>Купил 0,001 ВТС по 78000</code>\n"
        "• <code>Продал 0,0005 ВТС по 79000</code>\n"
        "• <code>Купил 1 грамм золота по 80</code>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query()
async def handle_callbacks(callback: CallbackQuery):
    if callback.data == "show_portfolio":
        await show_portfolio(callback.message)
        await callback.answer()
    elif callback.data == "help_add":
        await callback.message.answer("Пиши: «Баланс на бирже 2000», «Купил 0,1 ВТС по 75000» или «Продал 0,05 ВТС по 80000».")
        await callback.answer()
    elif callback.data == "clear_db":
        clear_db()
        await callback.message.answer("🗑 Все данные очищены.")
        await callback.answer()

@dp.message()
async def handle_text(message: types.Message):
    text = message.text.strip().lower().replace(',', '.')
    
    # 1. Баланс
    balance_match = re.search(r'(?:баланс\s+на\s+бирже|на\s+бирже)\s+([\d.]+)', text)
    if balance_match:
        amount = float(balance_match.group(1))
        set_exchange_balance(amount)
        await message.answer(f"✅ Баланс биржи установлен: <b>{amount:,.2f} USD</b>", parse_mode="HTML")
        return

    # 2. Продажа
    sell_match = re.search(r'продал\s+(.+?)\s+по\s+([\d.]+)', text, re.IGNORECASE)
    if sell_match:
        part1 = sell_match.group(1)
        sell_price = float(sell_match.group(2))
        
        numbers = re.findall(r'[\d.]+', part1)
        if not numbers:
            await message.answer("Не нашел количество.")
            return
        amount = float(numbers[0])
        
        raw_name = re.sub(r'[\d.]+', '', part1).strip()
        asset_name, _, _ = parse_asset_name(raw_name)
            
        result = sell_asset(asset_name, amount)
        
        if result == "not_found":
            await message.answer(f"❌ У тебя нет актива <b>{asset_name}</b> в портфеле.", parse_mode="HTML")
        elif result == "too_much":
            await message.answer(f"❌ Ты пытаешься продать {amount} {asset_name}, но у тебя меньше.", parse_mode="HTML")
        elif result == "all_sold":
            await message.answer(f"✅ Продано всё: <b>{amount} {asset_name}</b> по цене {sell_price}. Позиция закрыта.", parse_mode="HTML")
        else:
            await message.answer(f"✅ Продано: <b>{amount} {asset_name}</b> по цене {sell_price}. Остаток уменьшен.", parse_mode="HTML")
        return

    # 3. Покупка
    match = re.search(r'купил\s+(.+?)\s+по\s+([\d.]+)', text, re.IGNORECASE)
    if match:
        part1 = match.group(1) 
        buy_price = float(match.group(2))
        
        numbers = re.findall(r'[\d.]+', part1)
        if not numbers:
            await message.answer("Не нашел количество.")
            return
        amount = float(numbers[0])
        
        raw_name = re.sub(r'[\d.]+', '', part1).strip()
        asset_name, asset_type, location = parse_asset_name(raw_name)
            
        add_asset(asset_name, amount, buy_price, asset_type, location)
        await message.answer(f"✅ Добавлено/усреднено: <b>{asset_name}</b>. Цена покупки: <b>{buy_price}</b>.", parse_mode="HTML")
        return
        
    if "портфель" in text:
        await show_portfolio(message)
        return
        
    await message.answer("Я не понял. Пример: «Купил 0,001 ВТС по 75000» или «Продал 0,0005 ВТС по 80000».")

async def show_portfolio(message: types.Message):
    portfolio = get_portfolio()
    total_exchange_balance = get_exchange_balance()
    
    if not portfolio and total_exchange_balance == 0:
        await message.answer("Портфель пуст. Начни с: «Баланс на бирже 2000»")
        return
        
    gold_price_g = get_gold_price_per_gram()
    
    exchange_assets_value = 0
    physical_value = 0
    
    text_exchange = "🏦 <b>Биржевой счет:</b>\n"
    text_physical = "🏠 <b>Физические активы:</b>\n"
    
    has_exchange_assets = False
    has_physical_assets = False

    for asset, amount, avg_buy_price, asset_type, location in portfolio:
        current_price = None
        
        if asset_type == 'crypto':
            current_price = get_crypto_price(f"{asset}/USD")
        elif asset_type == 'stock':
            try:
                hist = yf.Ticker(asset).history(period="1d")
                if not hist.empty:
                    current_price = float(hist['Close'].iloc[-1])
            except: pass
        elif asset_type == 'metal':
            current_price = gold_price_g
        elif asset_type == 'coin':
            current_price = avg_buy_price 
            
        if current_price:
            current_total = amount * current_price
            total_buy_cost = amount * avg_buy_price
            profit = current_total - total_buy_cost
            profit_sign = "🟢" if profit >= 0 else "🔴"
            
            line = (
                f"🔸 <b>{asset}</b>: {amount} ед.\n"
                f"   Среди. покупка: {avg_buy_price:,.2f} | Сейчас: {current_price:,.2f}\n"
                f"   Стоимость: {current_total:,.2f} {profit_sign} ({profit:+,.2f})\n"
            )
            
            if location == 'exchange':
                text_exchange += line
                exchange_assets_value += current_total
                has_exchange_assets = True
            else:
                text_physical += line
                physical_value += current_total
                has_physical_assets = True

    if not has_exchange_assets: text_exchange += "   (Пусто)\n"
    if not has_physical_assets: text_physical += "   (Пусто)\n"
    
    free_cash = total_exchange_balance - exchange_assets_value
    if free_cash < 0:
        free_cash_text = "0.00 USD (все средства в активах)"
    else:
        free_cash_text = f"{free_cash:,.2f} USD 🟢"
    
    total_capital = total_exchange_balance + physical_value
    
    final_text = "📊 <b>Твой инвестиционный портфель:</b>\n\n"
    final_text += text_exchange
    final_text += f"   📈 Стоимость активов на бирже: {exchange_assets_value:,.2f} USD\n"
    final_text += f"   💵 <b>Другое (свободный кэш): {free_cash_text}</b>\n"
    final_text += f"   🏦 Всего на бирже (по вводу): {total_exchange_balance:,.2f} USD\n\n"
    
    final_text += text_physical
    final_text += f"   📈 Стоимость физики: {physical_value:,.2f} USD\n\n"
    
    final_text += f"💰 <b>ОБЩИЙ КАПИТАЛ: {total_capital:,.2f} USD</b>"
    
    await message.answer(final_text, parse_mode="HTML")

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
