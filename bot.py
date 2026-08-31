import asyncio
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

from database import init_db, add_boxes, get_today_stats, get_month_stats

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
NORM = 543

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Сегодня"), KeyboardButton(text="📅 За месяц")]
    ],
    resize_keyboard=True
)

def format_shift_report(boxes: int, date_str: str) -> str:
    percent = (boxes / NORM) * 100
    bonus_boxes = max(0, boxes - NORM)
    bonus_percent = max(0.0, percent - 100.0)

    msg = (
        f"📅 *Дата:* `{date_str}`\n"
        f"📦 *Собрано:* {boxes} / {NORM} кор.\n"
        f"📈 *Выполнение:* {percent:.1f}%\n"
    )

    if percent >= 100:
        msg += f"🔥 *Бонус:* +{bonus_percent:.1f}% (+{bonus_boxes} кор. сверх нормы)"
    else:
        remaining = NORM - boxes
        msg += f"⏳ *До нормы осталось:* {remaining} кор."

    return msg

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Отправляй число коробок сообщением (например, `200` или `560`), "
        "и бот сохранит результат смены и рассчитает процент нормы (543 кор.).",
        reply_markup=main_keyboard,
        parse_mode="Markdown"
    )

@dp.message(F.text == "📊 Сегодня")
@dp.message(Command("today"))
async def show_today(message: Message):
    today = datetime.now().strftime("%Y-%m-%d")
    boxes = await get_today_stats(message.from_user.id)
    if boxes == 0:
        await message.answer("Сегодня записей еще нет. Отправь количество коробок числом.")
    else:
        await message.answer(format_shift_report(boxes, today), parse_mode="Markdown")

@dp.message(F.text == "📅 За месяц")
@dp.message(Command("month"))
async def show_month(message: Message):
    records = await get_month_stats(message.from_user.id)
    if not records:
        await message.answer("В этом месяце пока нет сохраненных смен.")
        return

    total_boxes = sum(r[1] for r in records)
    total_norm = len(records) * NORM
    avg_percent = (total_boxes / total_norm) * 100 if total_norm > 0 else 0
    total_bonus_boxes = sum(max(0, r[1] - NORM) for r in records)

    lines = [f"• `{date}`: {boxes} кор. ({(boxes/NORM)*100:.1f}%)" for date, boxes in records]
    history_text = "\n".join(lines[-10:])

    report = (
        f"📊 *Статистика за месяц:*\n\n"
        f"🗓 Отработано смен: *{len(records)}*\n"
        f"📦 Всего коробок: *{total_boxes}*\n"
        f"📈 Средний процент: *{avg_percent:.1f}%*\n"
        f"🎁 Всего сверх нормы: *+{total_bonus_boxes} кор.*\n\n"
        f"*Последние смены:*\n{history_text}"
    )
    await message.answer(report, parse_mode="Markdown")

@dp.message(F.text.regexp(r"^\d+$"))
async def process_boxes(message: Message):
    boxes_input = int(message.text)
    
    if boxes_input <= 0 or boxes_input > 3000:
        await message.answer("Введите корректное число коробок.")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    total_today = await add_boxes(message.from_user.id, boxes_input)
    
    response = (
        f"✅ Добавлено: +{boxes_input} кор.\n\n"
        f"{format_shift_report(total_today, today)}"
    )
    await message.answer(response, parse_mode="Markdown")

@dp.message()
async def fallback(message: Message):
    await message.answer("Просто отправь число (количество коробок), чтобы добавить к сегодняшней смене.")

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
            
