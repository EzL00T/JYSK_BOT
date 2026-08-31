import asyncio
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
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
    set_period_vacation,
    delete_shift,
    get_month_shifts,
    get_today_leaderboard,
    get_monthly_leaderboard,
    get_bonus_percent,
    get_today_date
)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
BASE_NORM = 543
BOXES_PER_EXTRA_HOUR = 75
TIMEZONE = ZoneInfo("Europe/Sofia")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class BotStates(StatesGroup):
    waiting_for_custom_date = State()
    waiting_for_edit_boxes = State()
    waiting_for_edit_hours = State()
    waiting_for_vacation_range = State()

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Сегодня"), KeyboardButton(text="📅 За месяц")],
        [KeyboardButton(text="📋 Таблица смен"), KeyboardButton(text="🏆 Лидерборды")],
        [KeyboardButton(text="✏️ Редактировать день"), KeyboardButton(text="🏖 Отпуск / Больничный")]
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

def parse_date_str(date_text: str) -> str:
    """Парсит строки вида 'DD.MM', 'DD.MM.YYYY', 'YYYY-MM-DD' в формат 'YYYY-MM-DD'."""
    text = date_text.strip()
    now = datetime.now(TIMEZONE)
    # Формат DD.MM
    m1 = re.match(r"^(\d{1,2})[./\-](\d{1,2})$", text)
    if m1:
        d, m = int(m1.group(1)), int(m1.group(2))
        return datetime(now.year, m, d).strftime("%Y-%m-%d")
    # Формат DD.MM.YYYY
    m2 = re.match(r"^(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})$", text)
    if m2:
        d, m, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        return datetime(y, m, d).strftime("%Y-%m-%d")
    # Формат YYYY-MM-DD
    m3 = re.match(r"^(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})$", text)
    if m3:
        y, m, d = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))
        return datetime(y, m, d).strftime("%Y-%m-%d")
    return None

def format_shift_report(shift: dict) -> str:
    status = shift.get("status", "WORK")
    date_str = shift["shift_date"]
    boxes = shift.get("boxes", 0)
    extra_hours = shift.get("extra_hours", 0.0)

    if status == "FROZEN":
        return f"📅 *Дата:* `{date_str}`\n🏖 *Статус:* Отпуск / Больничный\n_День заморожен и не влияет на статистику нормы._"

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

def get_day_edit_kb(date_str: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Изменить коробки", callback_data=f"edit_box_{date_str}"),
            InlineKeyboardButton(text="⏱ Доп. часы", callback_data=f"edit_hrs_{date_str}")
        ],
        [
            InlineKeyboardButton(text="🔥 Сверхурочная", callback_data=f"st_ot_{date_str}"),
            InlineKeyboardButton(text="💼 Обычная", callback_data=f"st_wk_{date_str}")
        ],
        [
            InlineKeyboardButton(text="❌ Удалить запись", callback_data=f"del_{date_str}")
        ]
    ])

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_name = message.from_user.full_name or message.from_user.first_name
    await register_user(message.from_user.id, user_name, message.from_user.username)
    
    await message.answer(
        "👋 *Бот учета смен JYSK готов к работе!*\n\n"
        "• Отправляйте количество коробок числом (например: `150` или `560`).\n"
        "• Базовая норма: *543 коробки* (+75 кор./доп. час).\n"
        "• Выходные не требуют отметок (в норму идут только рабочие смены).\n"
        "• Любой день можно отредактировать через кнопку меню.",
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
        reply_markup=get_day_edit_kb(shift["shift_date"]),
        parse_mode="Markdown"
    )

@dp.message(F.text == "✏️ Редактировать день")
async def choose_date_to_edit(message: Message, state: FSMContext):
    await state.clear()
    today = datetime.now(TIMEZONE)
    yesterday = today - timedelta(days=1)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"📅 Сегодня ({today.strftime('%d.%m')})", callback_data=f"pick_date_{today.strftime('%Y-%m-%d')}"),
            InlineKeyboardButton(text=f"⏮ Вчера ({yesterday.strftime('%d.%m')})", callback_data=f"pick_date_{yesterday.strftime('%Y-%m-%d')}")
        ],
        [
            InlineKeyboardButton(text="🗓 Другая дата месяца", callback_data="pick_custom_date")
        ]
    ])
    await message.answer("Выберите день для просмотра или редактирования:", reply_markup=kb)

@dp.callback_query(F.data.startswith("pick_date_"))
async def cb_picked_date(callback: CallbackQuery, state: FSMContext):
    date_str = callback.data.replace("pick_date_", "")
    shift = await get_shift(callback.from_user.id, date_str)
    await callback.message.edit_text(
        format_shift_report(shift),
        reply_markup=get_day_edit_kb(date_str),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "pick_custom_date")
async def cb_ask_custom_date(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.waiting_for_custom_date)
    await callback.message.answer("Введите дату в формате `ДД.ММ` (например: `28.08` или `01.09`):", parse_mode="Markdown")
    await callback.answer()

@dp.message(BotStates.waiting_for_custom_date)
async def process_custom_date(message: Message, state: FSMContext):
    parsed = parse_date_str(message.text)
    if not parsed:
        await message.answer("Неверный формат. Попробуйте еще раз (например: `15.09`):")
        return
    await state.clear()
    shift = await get_shift(message.from_user.id, parsed)
    await message.answer(
        format_shift_report(shift),
        reply_markup=get_day_edit_kb(parsed),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("edit_box_"))
async def cb_edit_boxes_prompt(callback: CallbackQuery, state: FSMContext):
    date_str = callback.data.replace("edit_box_", "")
    await state.update_data(target_date=date_str)
    await state.set_state(BotStates.waiting_for_edit_boxes)
    await callback.message.answer(f"Введите точное итоговое число коробок за `{date_str}`:", parse_mode="Markdown")
    await callback.answer()

@dp.message(BotStates.waiting_for_edit_boxes, F.text.regexp(r"^\d+$"))
async def process_edit_boxes_save(message: Message, state: FSMContext):
    data = await state.get_data()
    target_date = data.get("target_date", get_today_date())
    boxes = int(message.text)
    
    user_name = message.from_user.full_name or message.from_user.first_name
    await register_user(message.from_user.id, user_name, message.from_user.username)
    
    shift = await set_boxes(message.from_user.id, boxes, target_date)
    await state.clear()
    await message.answer(
        f"✅ Данные обновлены!\n\n{format_shift_report(shift)}",
        reply_markup=get_day_edit_kb(target_date),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("edit_hrs_"))
async def cb_edit_hours_prompt(callback: CallbackQuery, state: FSMContext):
    date_str = callback.data.replace("edit_hrs_", "")
    await state.update_data(target_date=date_str)
    await state.set_state(BotStates.waiting_for_edit_hours)
    await callback.message.answer(f"Введите количество доп. часов за `{date_str}` (например: `0`, `1`, `1.5`, `2`):", parse_mode="Markdown")
    await callback.answer()

@dp.message(BotStates.waiting_for_edit_hours)
async def process_edit_hours_save(message: Message, state: FSMContext):
    data = await state.get_data()
    target_date = data.get("target_date", get_today_date())
    text = message.text.replace(",", ".")
    try:
        hours = float(text)
        if hours < 0 or hours > 12:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 0 до 12 часов.")
        return

    shift = await set_extra_hours(message.from_user.id, hours, target_date)
    await state.clear()
    await message.answer(
        f"✅ Доп. часы обновлены (+{hours:g} ч.)!\n\n{format_shift_report(shift)}",
        reply_markup=get_day_edit_kb(target_date),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("st_ot_"))
async def cb_set_overtime(callback: CallbackQuery):
    date_str = callback.data.replace("st_ot_", "")
    shift = await set_status(callback.from_user.id, "OVERTIME_DAY", date_str)
    await callback.message.edit_text(format_shift_report(shift), reply_markup=get_day_edit_kb(date_str), parse_mode="Markdown")
    await callback.answer("Установлен статус: Сверхурочная смена")

@dp.callback_query(F.data.startswith("st_wk_"))
async def cb_set_regular_work(callback: CallbackQuery):
    date_str = callback.data.replace("st_wk_", "")
    shift = await set_status(callback.from_user.id, "WORK", date_str)
    await callback.message.edit_text(format_shift_report(shift), reply_markup=get_day_edit_kb(date_str), parse_mode="Markdown")
    await callback.answer("Установлен статус: Обычная смена")

@dp.callback_query(F.data.startswith("del_"))
async def cb_delete_shift(callback: CallbackQuery):
    date_str = callback.data.replace("del_", "")
    await delete_shift(callback.from_user.id, date_str)
    await callback.message.edit_text(f"🗑 Запись за `{date_str}` удалена.", parse_mode="Markdown")
    await callback.answer()

@dp.message(F.text == "🏖 Отпуск / Больничный")
async def ask_vacation_range(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_vacation_range)
    await message.answer(
        "🏖 *Заморозка дней (Отпуск / Больничный)*\n\n"
        "Отправьте дату или диапазон дат, которые нужно заморозить.\n"
        "Примеры:\n"
        "• `05.09 - 15.09`\n"
        "• `01.09 по 07.09`\n"
        "• `10.09` _(если только один день)_",
        parse_mode="Markdown"
    )

@dp.message(BotStates.waiting_for_vacation_range)
async def process_vacation_range(message: Message, state: FSMContext):
    text = message.text.strip()
    parts = re.split(r"\s*(?:-|по|до|to)\s*", text, flags=re.IGNORECASE)
    
    if len(parts) == 1:
        d1_str = parse_date_str(parts[0])
        d2_str = d1_str
    elif len(parts) == 2:
        d1_str = parse_date_str(parts[0])
        d2_str = parse_date_str(parts[1])
    else:
        d1_str = None

    if not d1_str or not d2_str:
        await message.answer("Не удалось распознать даты. Напишите, например: `05.09 - 12.09`")
        return

    d1 = datetime.strptime(d1_str, "%Y-%m-%d")
    d2 = datetime.strptime(d2_str, "%Y-%m-%d")

    if d1 > d2:
        d1, d2 = d2, d1

    count = await set_period_vacation(message.from_user.id, d1, d2)
    await state.clear()
    await message.answer(
        f"✅ Успешно заморожено дней: *{count}* (с `{d1.strftime('%d.%m.%Y')}` по `{d2.strftime('%d.%m.%Y')}`).\n"
        f"Эти дни не будут снижать ваш процент нормы.",
        parse_mode="Markdown"
    )

@dp.message(F.text == "📅 За месяц")
@dp.message(Command("month"))
async def show_month(message: Message, state: FSMContext):
    await state.clear()
    records = await get_month_shifts(message.from_user.id)
    if not records:
        await message.answer("В этом месяце пока нет сохраненных смен.")
        return

    work_shifts = [r for r in records if r["status"] in ("WORK", "OVERTIME_DAY") and r["boxes"] > 0]
    frozen_count = sum(1 for r in records if r["status"] == "FROZEN")
    overtime_days = sum(1 for r in records if r["status"] == "OVERTIME_DAY")

    if not work_shifts:
        await message.answer(f"В этом месяце пока нет отработанных смен (замороженных дней: {frozen_count}).")
        return

    total_boxes = sum(r["boxes"] for r in work_shifts)
    total_norm = sum(calculate_norm(r["extra_hours"]) for r in work_shifts)
    avg_percent = (total_boxes / total_norm) * 100 if total_norm > 0 else 0
    month_bonus = get_bonus_percent(avg_percent)

    report = (
        f"📊 *Сводка за текущий месяц:*\n\n"
        f"💼 Отработано смен: *{len(work_shifts)}*" + (f" (сверхурочных: {overtime_days})" if overtime_days else "") + "\n"
        f"🏖 Заморожено (отпуск/больничный): *{frozen_count} дн.*\n"
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
        d = r["shift_date"][5:]
        st = r["status"]
        if st == "FROZEN":
            lines.append(f"{d}  | 🏖 Отпуск/Больничный")
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
        await callback.message.answer("За сегодня еще никто не вносил коробки.")
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
        reply_markup=get_day_edit_kb(shift["shift_date"]),
        parse_mode="Markdown"
    )

@dp.message()
async def fallback(message: Message):
    await message.answer("Отправьте число коробок сообщением или воспользуйтесь кнопками меню.")

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
