import aiosqlite
from datetime import datetime

DB_NAME = "warehouse.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shifts (
                user_id INTEGER,
                shift_date TEXT,
                boxes INTEGER,
                PRIMARY KEY (user_id, shift_date)
            )
        """)
        await db.commit()

async def add_boxes(user_id: int, boxes: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT boxes FROM shifts WHERE user_id = ? AND shift_date = ?",
            (user_id, today)
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            new_total = row[0] + boxes
            await db.execute(
                "UPDATE shifts SET boxes = ? WHERE user_id = ? AND shift_date = ?",
                (new_total, user_id, today)
            )
        else:
            new_total = boxes
            await db.execute(
                "INSERT INTO shifts (user_id, shift_date, boxes) VALUES (?, ?, ?)",
                (user_id, today, new_total)
            )

        await db.commit()
        return new_total

async def get_today_stats(user_id: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT boxes FROM shifts WHERE user_id = ? AND shift_date = ?",
            (user_id, today)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_month_stats(user_id: int, month_prefix: str = None) -> list:
    if not month_prefix:
        month_prefix = datetime.now().strftime("%Y-%m")
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT shift_date, boxes 
            FROM shifts 
            WHERE user_id = ? AND shift_date LIKE ? 
            ORDER BY shift_date ASC
            """,
            (user_id, f"{month_prefix}%")
        ) as cursor:
            return await cursor.fetchall()
          
