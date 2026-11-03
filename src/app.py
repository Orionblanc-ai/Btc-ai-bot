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
    # Таблица для активов (крипта, акции, металлы)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT,
            amount REAL,
            buy_price REAL,
            asset_type TEXT,
            location TEXT
        )
    ''')
    # Таблица для баланса биржи
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
    cursor.execute('INSERT INTO portfolio (asset, amount, buy_price, asset_type, location) VALUES (?, ?, ?, ?, ?)', (asset, amount, buy_price, asset_type, location))
    conn.commit()
    conn.close()

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
    cursor.execute('SELECT asset, amount, buy_price, asset_type, location FROM portfolio')
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
            price_per_ounce = float(hist['Close'].iloc[-1])
            return price_per_ounce / 31.1035
        return None
    except:
        return None

# --- БОТ ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global CHAT_ID
    CHAT_ID = message.chat.id
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Мой портфель", callback_data="show_portfolio")
    builder.button(text="❓ Как вводить данные", callback_data="help_add")
    builder.button(text="🗑 Очистить всё", callback_data="clear_db")
    
    await message.answer(
        "🤖 Привет! Я твой личный портфельный менеджер.\n\n"
        "Я умею считать общий капитал, разделяя его на биржевой счет и физические активы.\n\n"
        "Команды:\n"
        "• <code>На бирже 2000</code> (установить баланс)\n"
        "• <code>Купил 0.001 BTC по 75000</code>\n"
        "• <code>Купил 10 грамм золота по 80</code>\n"
        "• <code>Купил 5 Славянка по 350</code>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query()
async def handle_callbacks(callback: CallbackQuery):
    if callback.data == "show_portfolio":
        await show_portfolio(callback.message)
        await callback.answer()
    elif callback.data == "help_add":
        await callback.message.answer(
            "Вводи данные так:\n\n"
            "1. <b>Баланс биржи:</b> <code>На бирже 2000</code>\n"
            "2. <b>Крипта:</b> <code>Купил 0.001 BTC по 75000</code>\n"
            "3. <b>Золото:</b> <code>Купил 10 грамм золота по 80</code>\n"
            "4. <b>Славянка:</b> <code>Купил 5 Славянка по 350</code>",
            parse_mode="HTML"
        )
        await callback.answer()
    elif callback.data == "clear_db":
        clear_db()
        await callback.message.answer("🗑 Все данные очищены. Начинаем с чистого листа.")
        await callback.answer()

@dp.message()
async def handle_text(message: types.Message):
    text = message.text.strip()
    
    # 1. Установка баланса биржи
    balance_match = re.search(r'на бирже\s+([\d.,]+)', text, re.IGNORECASE)
    if balance_match:
        amount = float(balance_match.group(1).replace(',', '.'))
        set_exchange_balance(amount)
        await message.answer(f"✅ Баланс биржи установлен: <b>{amount:,.2f} USD</b>", parse_mode="HTML")
        return

    # 2. Добавление актива
    match = re.search(r'купил\s+([\d.,]+)\s+([a-zA-Zа-яА-Я\s]+)\s+по\s+([\d.,]+)', text, re.IGNORECASE)
    if match:
        amount = float(match.group(1).replace(',', '.'))
        asset = match.group(2).strip().upper()
        buy_price = float(match.group(3).replace(',', '.'))
        
        asset_type = 'crypto'
        location = 'exchange'
        
        if "ЗОЛОТ" in asset or "GOLD" in asset:
            asset = 'GOLD'
            asset_type = 'metal'
            location = 'physical'
        elif "СЛАВЯНКА" in asset or "МОНЕТ" in asset:
            asset = 'СЛАВЯНКА'
            asset_type = 'coin'
            location = 'physical'
        elif len(asset) <= 5 and not asset in ['BTC', 'ETH', 'SOL', 'XRP']: 
            asset_type = 'stock'
            
        add_asset(asset, amount, buy_price, asset_type, location)
        await message.answer(f"✅ Добавлено: <b>{amount} {asset}</b> по цене <b>{buy_price}</b>.", parse_mode="HTML")
        return
        
    if "портфель" in text.lower():
        await show_portfolio(message)
        return
        
    await message.answer("Я не понял. Напиши «Портфель» или добавь актив: «Купил 0.1 BTC по 75000».")

async def show_portfolio(message: types.Message):
    portfolio = get_portfolio()
    exchange_balance = get_exchange_balance()
    
    if not portfolio and exchange_balance == 0:
        await message.answer("Твой портфель пуст. Начни с команды: «На бирже 2000» или «Купил 0.1 BTC по 75000».")
        return
        
    gold_price_g = get_gold_price_per_gram()
    
    exchange_crypto_value = 0
    physical_value = 0
    
    text_exchange = "🏦 <b>Биржевой счет:</b>\n"
    text_physical = "🏠 <b>Физические активы:</b>\n"
    
    has_exchange_assets = False
    has_physical_assets = False

    for asset, amount, buy_price, asset_type, location in portfolio:
        current_price = None
        
        if asset_type == 'crypto':
            current_price = get_crypto_price(f"{asset}/USD")
        elif asset_type == 'stock':
            try:
                hist = yf.Ticker(asset).history(period="1d")
                if not hist.empty:
                    current_price = float(hist['Close'].iloc[-1])
            except:
                pass
        elif asset_type == 'metal':
            current_price = gold_price_g
        elif asset_type == 'coin':
            current_price = buy_price 
            
        if current_price:
            current_total = amount * current_price
            profit = (current_price - buy_price) * amount
            sign = "🟢" if profit >= 0 else "🔴"
            
            line = (
                f"🔸 {asset}: {amount} ед.\n"
                f"   Сейчас: {current_price:,.2f} | Стоимость: {current_total:,.2f} {sign} ({profit:+,.2f})\n"
            )
            
            if location == 'exchange':
                text_exchange += line
                exchange_crypto_value += current_total
                has_exchange_assets = True
            else:
                text_physical += line
                physical_value += current_total
                has_physical_assets = True

    if not has_exchange_assets:
        text_exchange += "   (Пусто)\n"
    if not has_physical_assets:
        text_physical += "   (Пусто)\n"
        
    # Считаем общий капитал
    total_capital = exchange_balance + physical_value
    
    final_text = "📊 <b>Твой инвестиционный портфель:</b>\n\n"
    final_text += text_exchange
    final_text += f"   💵 Свободный баланс (или общий счет): {exchange_balance:,.2f} USD\n"
    final_text += f"   📈 Стоимость крипты: {exchange_crypto_value:,.2f} USD\n\n"
    
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
    
    print("Бот-портфель запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
