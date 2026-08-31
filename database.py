import aiosqlite
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DB_NAME = "warehouse.db"
TIMEZONE = ZoneInfo("Europe/Sofia")

# Сетка бонусов согласно шкале JYSK (Комисиониране)
BONUS_TABLE = {
    100: 0.0,
    105: 15.0,
    110: 20.0,
    115: 22.5,
    120: 25.0,
    121: 26.0,
    122: 27.0,
    123: 28.0,
    124: 29.0,
    125: 30.0,
}
for eff in range(126, 181):
    BONUS_TABLE[eff] = 30.0 + (eff - 125) * 0.5

def get_bonus_percent(efficiency: float) -> float:
    eff_int = int(efficiency)
    valid_steps = [step for step in sorted(BONUS_TABLE.keys()) if step <= eff_int]
    if not valid_steps:
        return 0.0
    return BONUS_TABLE[max(valid_steps)]

def get_today_date() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shifts (
                user_id INTEGER,
                shift_date TEXT,
                boxes INTEGER DEFAULT 0,
                extra_hours REAL DEFAULT 0,
                status TEXT DEFAULT 'WORK',
                PRIMARY KEY (user_id, shift_date)
            )
        """)
        await db.commit()

async def register_user(user_id: int, full_name: str, username: str = None):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO users (user_id, full_name, username)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                full_name = excluded.full_name,
                username = excluded.username
        """, (user_id, full_name, username))
        await db.commit()

async def get_shift(user_id: int, shift_date: str = None) -> dict:
    if not shift_date:
        shift_date = get_today_date()
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM shifts WHERE user_id = ? AND shift_date = ?",
            (user_id, shift_date)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return {"user_id": user_id, "shift_date": shift_date, "boxes": 0, "extra_hours": 0.0, "status": "WORK"}

async def add_boxes(user_id: int, boxes: int, shift_date: str = None) -> dict:
    if not shift_date:
        shift_date = get_today_date()
    shift = await get_shift(user_id, shift_date)
    new_boxes = shift["boxes"] + boxes
    return await set_boxes(user_id, new_boxes, shift_date)

async def set_boxes(user_id: int, boxes: int, shift_date: str = None) -> dict:
    if not shift_date:
        shift_date = get_today_date()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO shifts (user_id, shift_date, boxes, extra_hours, status)
            VALUES (?, ?, ?, 0, 'WORK')
            ON CONFLICT(user_id, shift_date) DO UPDATE SET
                boxes = excluded.boxes,
                status = CASE WHEN status IN ('FROZEN', 'OFF') THEN 'WORK' ELSE status END
        """, (user_id, shift_date, max(0, boxes)))
        await db.commit()
    return await get_shift(user_id, shift_date)

async def set_extra_hours(user_id: int, hours: float, shift_date: str = None) -> dict:
    if not shift_date:
        shift_date = get_today_date()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO shifts (user_id, shift_date, boxes, extra_hours, status)
            VALUES (?, ?, 0, ?, 'WORK')
            ON CONFLICT(user_id, shift_date) DO UPDATE SET extra_hours = excluded.extra_hours
        """, (user_id, shift_date, max(0.0, hours)))
        await db.commit()
    return await get_shift(user_id, shift_date)

async def set_status(user_id: int, status: str, shift_date: str = None) -> dict:
    if not shift_date:
        shift_date = get_today_date()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO shifts (user_id, shift_date, boxes, extra_hours, status)
            VALUES (?, ?, 0, 0, ?)
            ON CONFLICT(user_id, shift_date) DO UPDATE SET status = excluded.status
        """, (user_id, shift_date, status))
        await db.commit()
    return await get_shift(user_id, shift_date)

async def set_period_vacation(user_id: int, start_date: datetime, end_date: datetime) -> int:
    """Заполняет диапазон дат статусом FROZEN (отпуск / больничный)."""
    curr = start_date
    count = 0
    async with aiosqlite.connect(DB_NAME) as db:
        while curr <= end_date:
            d_str = curr.strftime("%Y-%m-%d")
            await db.execute("""
                INSERT INTO shifts (user_id, shift_date, boxes, extra_hours, status)
                VALUES (?, ?, 0, 0, 'FROZEN')
                ON CONFLICT(user_id, shift_date) DO UPDATE SET
                    status = 'FROZEN',
                    boxes = 0,
                    extra_hours = 0
            """, (user_id, d_str))
            count += 1
            curr += timedelta(days=1)
        await db.commit()
    return count

async def delete_shift(user_id: int, shift_date: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM shifts WHERE user_id = ? AND shift_date = ?", (user_id, shift_date))
        await db.commit()

async def get_month_shifts(user_id: int, month_prefix: str = None) -> list:
    if not month_prefix:
        month_prefix = datetime.now(TIMEZONE).strftime("%Y-%m")
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM shifts 
            WHERE user_id = ? AND shift_date LIKE ? 
            ORDER BY shift_date ASC
        """, (user_id, f"{month_prefix}%")) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_today_leaderboard() -> list:
    today = get_today_date()
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.full_name, u.username, s.boxes, s.extra_hours, s.status
            FROM shifts s
            JOIN users u ON s.user_id = u.user_id
            WHERE s.shift_date = ? AND s.status IN ('WORK', 'OVERTIME_DAY') AND s.boxes > 0
        """, (today,)) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

async def get_monthly_leaderboard(month_prefix: str = None) -> list:
    if not month_prefix:
        month_prefix = datetime.now(TIMEZONE).strftime("%Y-%m")
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.full_name, u.username, s.boxes, s.extra_hours, s.status
            FROM shifts s
            JOIN users u ON s.user_id = u.user_id
            WHERE s.shift_date LIKE ? AND s.status IN ('WORK', 'OVERTIME_DAY') AND s.boxes > 0
        """, (f"{month_prefix}%",)) as cursor:
            return [dict(r) for r in await cursor.fetchall()]
