import asyncio
import os
import re
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Берем токен из настроек Render (Environment Variables)
TOKEN = os.environ.get("TELEGRAM_TOKEN")

user_data = {}

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 Привет! Я твой ИИ-помощник.\n\n"
        "Отправь мне данные за ночь в формате:\n"
        "Минимум 64000, Максимум 66000\n\n"
        "А вечером напиши:\n"
        "Закрытие 65000"
    )

@dp.message()
async def handle_text(message: types.Message):
    text = message.text.lower()
    user_id = message.from_user.id

    numbers = re.findall(r'\d+', text.replace(' ', ''))
    
    if not numbers:
        await message.answer("Я не вижу цифр. Напиши, например: Минимум 64000, Максимум 66000")
        return

    if "минимум" in text or "максимум" in text or "max" in text or "min" in text:
        if len(numbers) >= 2:
            min_p = float(numbers[0])
            max_p = float(numbers[1])
            user_data[user_id] = {"min": min_p, "max": max_p}
            
            await message.answer(
                f"✅ Записал: Мин {min_p}, Макс {max_p}.\n"
                f"📊 Жду вечернюю цену закрытия."
            )
            return

    if "закрытие" in text or "цена" in text or "close" in text:
        if user_id in user_data and "min" in user_data[user_id]:
            close_price = float(numbers[0])
            min_p = user_data[user_id]["min"]
            max_p = user_data[user_id]["max"]
            
            mid = (min_p + max_p) / 2
            
            if close_price < mid:
                result = "📉 Цена ниже середины диапазона. Прогноз: SHORT."
            else:
                result = "📈 Цена выше середины диапазона. Прогноз: LONG."
            
            await message.answer(
                f"🔔 Закрытие {close_price} принято.\n"
                f"{result}\n"
                f"📝 Записал в статистику."
            )
        else:
            await message.answer("Сначала пришли мне Минимум и Максимум за ночь.")
        return

    await message.answer("Я тебя не понял. Напиши 'Минимум X, Максимум Y' или 'Закрытие Z'.")

async def handle_web(request):
    return web.Response(text="Bot is running!")

async def main():
    app = web.Application()
    app.router.add_get('/', handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    
    print("Бот запущен и слушает порт 10000...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
