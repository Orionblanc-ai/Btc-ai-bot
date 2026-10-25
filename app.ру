import asyncio
import os
import re
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

TOKEN = os.environ.get("TELEGRAM_TOKEN")
user_data = {}

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 Привет! Я твой ИИ-помощник по BTC.\n\n"
        "Я стал умнее! Теперь ты можешь просто прислать мне два числа через пробел или запятую (например: 64000 66000), и я пойму, что это ночной диапазон.\n\n"
        "А вечером просто напиши цену закрытия (например: 65000)."
    )

@dp.message()
async def handle_text(message: types.Message):
    text = message.text.lower()
    user_id = message.from_user.id

    # Ищем все числа в сообщении
    numbers = re.findall(r'\d+', text)
    
    if not numbers:
        await message.answer("Я не вижу здесь цифр. Пришли мне два числа (мин и макс) или цену закрытия.")
        return

    # Если прислали два числа (например, "64000 66000")
    if len(numbers) >= 2 and "закрытие" not in text and "цена" not in text:
        min_p = float(numbers[0])
        max_p = float(numbers[1])
        user_data[user_id] = {"min": min_p, "max": max_p}
        
        mid = (min_p + max_p) / 2
        await message.answer(
            f"✅ Принял ночной диапазон: от {min_p} до {max_p}.\n"
            f"Середина: {mid}.\n"
            f"📊 Жду вечернюю цену закрытия, чтобы дать прогноз."
        )
        return

    # Если прислали цену закрытия (одно число или есть слово "закрытие")
    if user_id in user_data and "min" in user_data[user_id]:
        close_price = float(numbers[0])
        min_p = user_data[user_id]["min"]
        max_p = user_data[user_id]["max"]
        mid = (min_p + max_p) / 2
        
        if close_price < mid:
            result = f"📉 Цена {close_price} ниже середины ({mid}). Мой прогноз: SHORT."
        else:
            result = f"📈 Цена {close_price} выше середины ({mid}). Мой прогноз: LONG."
        
        await message.answer(
            f"🔔 Закрытие {close_price} принято.\n"
            f"{result}\n"
            f"📝 Записал в статистику."
        )
        # Сбрасываем данные на новый день
        del user_data[user_id] 
        return

    await message.answer("Я вижу цифры, но не понял логику. Пришли мне диапазон (2 числа) или цену закрытия (1 число).")

async def handle_web(request):
    return web.Response(text="Bot is running!")

async def main():
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
