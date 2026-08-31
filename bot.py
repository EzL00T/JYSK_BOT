import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from database import (
    init_db,
    register_user,
    get_shift,
    add_boxes,
    set_boxes,
    set_extra_hours,
    set_status,
    get_month_shifts,
    get_today_leaderboard,
    get_monthly_leaderboard,
    get_bonus_percent
)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
BASE_NORM = 543
BOXES_PER_EXTRA_HOUR = 75
TIMEZONE = ZoneInfo("Europe/Sofia")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class ShiftEditStates(StatesGroup):
    waiting_for_exact_boxes = State()
    waiting_for_custom_hours = State()

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Сегодня"), KeyboardButton(text="📅 За месяц")],
        [KeyboardButton(text="📋 Таблица смен"), KeyboardButton(text="🏆 Лидерборды")],
        [KeyboardButton(text="⏱ Доп. часы"), KeyboardButton(text="🏖 Статус дня")],
        [KeyboardButton(text="✏️ Задать точное число")]
    ],
    resize_keyboard=True
)

leaderboard_inline_kb = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="🥇 Топ за сегодня", callback_data="lb_today"),
        InlineKeyboardButton(text="🏆 Топ за месяц", callback_data="lb_month")
    ]
])

def calculate_norm(extra_hours: float) -> int:
    return int(BASE_NORM + (extra_hours * BOXES_PER_EXTRA_HOUR))

def format_shift_report(shift: dict) -> str:
    status = shift.get("status", "WORK")
    date_str = shift["shift_date"]
    boxes = shift.get("boxes", 0)
    extra_hours = shift.get("extra_hours", 0.0)

    if status == "VACATION":
        return f"📅 *Дата:* `{date_str}`\n🏖 *Статус:* Отпуск\n_День заморожен и не влияет на общую норму._"
    if status == "SICK":
        return f"📅 *Дата:* `{date_str}`\n🤒 *Статус:* Больничный\n_День заморожен и не влияет на общую норму._"
    if status == "OFF":
        return f"📅 *Дата:* `{date_str}`\n🏠 *Статус:* Выходной\n_Смена не учитывается в расчёте нормы._"

    day_norm = calculate_norm(extra_hours)
    percent = (boxes / day_norm) * 100 if day_norm > 0 else 0
    bonus_pct = get_bonus_percent(percent)
    status_label = "🔥 Сверхурочная смена" if status == "OVERTIME_DAY" else "💼 Рабочая смена"

    msg = [
        f"📅 *Дата:* `{date_str}` ({status_label})",
        f"⏱ *Доп. часы:* +{extra_hours:g} ч. (Норма: {day_norm} кор.)" if extra_hours > 0 else f"🎯 *Норма:* {day_norm} кор.",
        f"📦 *Собрано:* {boxes} / {day_norm} кор.",
        f"📈 *Эффективность:* {percent:.1f}%",
        f"💰 *Бонус к ЗП:* **+{bonus_pct:.1f}%**" if bonus_pct > 0 else "💰 *Бонус к ЗП:* 0.0%"
    ]
    return "\n".join(msg)

def get_today_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏱ +1 ч", callback_data="add_hour_1"),
            InlineKeyboardButton(text="⏱ +2 ч", callback_data="add_hour_2"),
            InlineKeyboardButton(text="⏱ Сброс часов", callback_data="reset_hours")
        ],
        [
            InlineKeyboardButton(text="✏️ Ввести точно", callback_data="btn_exact_boxes"),
            InlineKeyboardButton(text="🏖 Статус дня", callback_data="btn_change_status")
        ]
    ])

def get_status_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💼 Рабочий день", callback_data="set_status_WORK")],
        [InlineKeyboardButton(text="🔥 Сверхурочный (вместо выходного)", callback_data="set_status_OVERTIME_DAY")],
        [InlineKeyboardButton(text="🏖 Отпуск", callback_data="set_status_VACATION")],
        [InlineKeyboardButton(text="🤒 Больничный", callback_data="set_status_SICK")],
        [InlineKeyboardButton(text="🏠 Выходной", callback_data="set_status_OFF")]
    ])

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_name = message.from_user.full_name or message.from_user.first_name
    await register_user(message.from_user.id, user_name, message.from_user.username)
    
    await message.answer(
        "👋 Бот учета смен и бонусов JYSK готов к работе!\n\n"
        "• Отправляйте количество коробок числом (например `150`).\n"
        "• Базовая норма: *543 коробки* (+75 кор./доп. час).\n"
        "• Бонусы начисляются автоматически по официальной шкале.",
        reply_markup=main_keyboard,
        parse_mode="Markdown"
    )

@dp.message(F.text == "📊 Сегодня")
@dp.message(Command("today"))
async def show_today(message: Message, state: FSMContext):
    await state.clear()
    shift = await get_shift(message.from_user.id)
    await message.answer(
        format_shift_report(shift),
        reply_markup=get_today_inline_kb(),
        parse_mode="Markdown"
    )

@dp.message(F.text == "🏖 Статус дня")
async def ask_status(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выберите статус для сегодняшней смены:", reply_markup=get_status_inline_kb())

@dp.message(F.text == "⏱ Доп. часы")
async def ask_hours(message: Message, state: FSMContext):
    await state.set_state(ShiftEditStates.waiting_for_custom_hours)
    await message.answer("Введите количество дополнительных часов за сегодня (например: `1`, `1.5` или `2`):", parse_mode="Markdown")

@dp.message(F.text == "✏️ Задать точное число")
async def ask_exact(message: Message, state: FSMContext):
    await state.set_state(ShiftEditStates.waiting_for_exact_boxes)
    await message.answer("Введите точное итоговое количество коробок за сегодня:")

@dp.message(ShiftEditStates.waiting_for_exact_boxes, F.text.regexp(r"^\d+$"))
async def process_exact_boxes(message: Message, state: FSMContext):
    exact_boxes = int(message.text)
    user_name = message.from_user.full_name or message.from_user.first_name
    await register_user(message.from_user.id, user_name, message.from_user.username)
    shift = await set_boxes(message.from_user.id, exact_boxes)
    await state.clear()
    await message.answer(f"✅ Точное число сохранено!\n\n{format_shift_report(shift)}", reply_markup=get_today_inline_kb(), parse_mode="Markdown")

@dp.message(ShiftEditStates.waiting_for_custom_hours)
async def process_custom_hours(message: Message, state: FSMContext):
    text = message.text.replace(",", ".")
    try:
        hours = float(text)
        if hours < 0 or hours > 12:
            raise ValueError
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число часов (от 0 до 12).")
        return
    shift = await set_extra_hours(message.from_user.id, hours)
    await state.clear()
    await message.answer(f"✅ Доп. часы обновлены (+{hours:g} ч.)!\n\n{format_shift_report(shift)}", reply_markup=get_today_inline_kb(), parse_mode="Markdown")

@dp.message(F.text == "📅 За месяц")
@dp.message(Command("month"))
async def show_month(message: Message, state: FSMContext):
    await state.clear()
    records = await get_month_shifts(message.from_user.id)
    if not records:
        await message.answer("В этом месяце пока нет сохраненных записей.")
        return

    work_shifts = [r for r in records if r["status"] in ("WORK", "OVERTIME_DAY")]
    vacation_count = sum(1 for r in records if r["status"] == "VACATION")
    sick_count = sum(1 for r in records if r["status"] == "SICK")
    overtime_days = sum(1 for r in records if r["status"] == "OVERTIME_DAY")

    total_boxes = sum(r["boxes"] for r in work_shifts)
    total_norm = sum(calculate_norm(r["extra_hours"]) for r in work_shifts)
    avg_percent = (total_boxes / total_norm) * 100 if total_norm > 0 else 0
    month_bonus = get_bonus_percent(avg_percent)

    report = (
        f"📊 *Сводка за текущий месяц:*\n\n"
        f"💼 Отработано смен: *{len(work_shifts)}*" + (f" (из них сверхурочных: {overtime_days})" if overtime_days else "") + "\n"
        f"🏖 Дней отпуска: *{vacation_count}* | 🤒 Больничных: *{sick_count}*\n"
        f"📦 Всего коробок: *{total_boxes}*\n"
        f"🎯 Общая норма: *{total_norm}*\n"
        f"📈 Средняя эффективность: *{avg_percent:.1f}%*\n"
        f"💰 *Итоговый бонус к зарплате: +{month_bonus:.1f}%*"
    )
    await message.answer(report, parse_mode="Markdown")

@dp.message(F.text == "📋 Таблица смен")
async def show_month_table(message: Message, state: FSMContext):
    await state.clear()
    records = await get_month_shifts(message.from_user.id)
    if not records:
        await message.answer("В этом месяце пока нет данных для формирования таблицы.")
        return

    lines = ["```text", " Дата  | Кор. | Норм |   %   | Бонус", "───────┼──────┼──────┼───────┼──────"]
    
    total_b = 0
    total_n = 0

    for r in records:
        d = r["shift_date"][5:]  # MM-DD
        st = r["status"]
        if st == "VACATION":
            lines.append(f"{d}  | 🏖 Отпуск")
        elif st == "SICK":
            lines.append(f"{d}  | 🤒 Больничный")
        elif st == "OFF":
            lines.append(f"{d}  | 🏠 Выходной")
        else:
            norm = calculate_norm(r["extra_hours"])
            boxes = r["boxes"]
            pct = (boxes / norm) * 100 if norm > 0 else 0
            b_pct = get_bonus_percent(pct)
            total_b += boxes
            total_n += norm
            lines.append(f"{d}  | {boxes:>4} | {norm:>4} |{pct:>5.1f}% | {b_pct:>4.1f}%")

    lines.append("───────┴──────┴──────┴───────┴──────")
    total_pct = (total_b / total_n) * 100 if total_n > 0 else 0
    lines.append(f"Итог:  | {total_b:>4} | {total_n:>4} |{total_pct:>5.1f}% | {get_bonus_percent(total_pct):>4.1f}%")
    lines.append("```")

    await message.answer("\n".join(lines), parse_mode="Markdown")

@dp.message(F.text == "🏆 Лидерборды")
async def show_leaderboard_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выберите лидерборд:", reply_markup=leaderboard_inline_kb)

@dp.callback_query(F.data == "lb_today")
async def cb_lb_today(callback: CallbackQuery):
    rows = await get_today_leaderboard()
    if not rows:
        await callback.message.answer("За сегодня еще никто не внес собранные коробки.")
        await callback.answer()
        return

    data = []
    for r in rows:
        norm = calculate_norm(r["extra_hours"])
        pct = (r["boxes"] / norm) * 100 if norm > 0 else 0
        bonus = get_bonus_percent(pct)
        name = r["full_name"] or r["username"] or "Сотрудник"
        data.append((pct, r["boxes"], norm, bonus, name))

    data.sort(key=lambda x: x[0], reverse=True)
    medals = ["🥇", "🥈", "🥉"]

    lines = ["🏆 *Лидерборд за сегодня:*\n"]
    for i, (pct, boxes, norm, bonus, name) in enumerate(data, start=1):
        icon = medals[i - 1] if i <= 3 else f"*{i}.*"
        lines.append(f"{icon} {name}: *{pct:.1f}%* ({boxes}/{norm} кор.) ➔ Бонус *+{bonus:.1f}%*")

    await callback.message.answer("\n".join(lines), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "lb_month")
async def cb_lb_month(callback: CallbackQuery):
    rows = await get_monthly_leaderboard()
    if not rows:
        await callback.message.answer("За этот месяц данных пока нет.")
        await callback.answer()
        return

    user_stats = defaultdict(lambda: {"boxes": 0, "norm": 0, "shifts": 0, "name": ""})
    for r in rows:
        name = r["full_name"] or r["username"] or "Сотрудник"
        norm = calculate_norm(r["extra_hours"])
        user_stats[name]["boxes"] += r["boxes"]
        user_stats[name]["norm"] += norm
        user_stats[name]["shifts"] += 1
        user_stats[name]["name"] = name

    data = []
    for stats in user_stats.values():
        total_b = stats["boxes"]
        total_n = stats["norm"]
        pct = (total_b / total_n) * 100 if total_n > 0 else 0
        bonus = get_bonus_percent(pct)
        data.append((pct, total_b, stats["shifts"], bonus, stats["name"]))

    data.sort(key=lambda x: x[0], reverse=True)
    medals = ["🥇", "🥈", "🥉"]

    lines = ["🏆 *Лидерборд за текущий месяц:*\n"]
    for i, (pct, boxes, shifts, bonus, name) in enumerate(data, start=1):
        icon = medals[i - 1] if i <= 3 else f"*{i}.*"
        lines.append(f"{icon} {name}: *{pct:.1f}%* ({shifts} смен, {boxes} кор.) ➔ Бонус *+{bonus:.1f}%*")

    await callback.message.answer("\n".join(lines), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("add_hour_"))
async def cb_add_hour(callback: CallbackQuery):
    hours_to_add = float(callback.data.split("_")[-1])
    shift = await get_shift(callback.from_user.id)
    new_hours = shift["extra_hours"] + hours_to_add
    updated = await set_extra_hours(callback.from_user.id, new_hours)
    await callback.message.edit_text(format_shift_report(updated), reply_markup=get_today_inline_kb(), parse_mode="Markdown")
    await callback.answer(f"Добавлено +{hours_to_add} ч.")

@dp.callback_query(F.data == "reset_hours")
async def cb_reset_hours(callback: CallbackQuery):
    updated = await set_extra_hours(callback.from_user.id, 0)
    await callback.message.edit_text(format_shift_report(updated), reply_markup=get_today_inline_kb(), parse_mode="Markdown")
    await callback.answer("Часы сброшены.")

@dp.callback_query(F.data == "btn_change_status")
async def cb_change_status(callback: CallbackQuery):
    await callback.message.edit_text("Выберите новый статус смены:", reply_markup=get_status_inline_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith("set_status_"))
async def cb_set_status(callback: CallbackQuery):
    status = callback.data.replace("set_status_", "")
    updated = await set_status(callback.from_user.id, status)
    await callback.message.edit_text(format_shift_report(updated), reply_markup=get_today_inline_kb(), parse_mode="Markdown")
    await callback.answer("Статус обновлен!")

@dp.callback_query(F.data == "btn_exact_boxes")
async def cb_btn_exact(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ShiftEditStates.waiting_for_exact_boxes)
    await callback.message.answer("Введите точное итоговое число коробок за сегодня:")
    await callback.answer()

@dp.message(F.text.regexp(r"^\d+$"))
async def process_boxes_input(message: Message, state: FSMContext):
    await state.clear()
    boxes = int(message.text)
    if boxes <= 0 or boxes > 3000:
        await message.answer("Введите корректное число коробок.")
        return

    user_name = message.from_user.full_name or message.from_user.first_name
    await register_user(message.from_user.id, user_name, message.from_user.username)
    shift = await add_boxes(message.from_user.id, boxes)
    await message.answer(
        f"✅ Добавлено: +{boxes} кор.\n\n{format_shift_report(shift)}",
        reply_markup=get_today_inline_kb(),
        parse_mode="Markdown"
    )

@dp.message()
async def fallback(message: Message):
    await message.answer("Отправьте количество коробок числом или выберите действие в меню.")

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
