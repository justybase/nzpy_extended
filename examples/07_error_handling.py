import asyncio
import sys
sys.path.insert(0, '.')
import nzpy_extended as nzpy


async def main():
    conn = await nzpy.connect(
        user="admin",
        password="password",
        host="127.0.0.1",
        port=5480,
        database="JUST_DATA",
    )
    try:
        cur = conn.cursor()

        try:
            await cur.execute("SELECT 1/0")
            await cur.fetchall()
        except nzpy.ProgrammingError as e:
            print("Divide-by-zero => ProgrammingError:", e)

        try:
            await cur.execute("SELECT * FROM nonexistent_table")
            await cur.fetchall()
        except nzpy.ProgrammingError as e:
            print("Bad table => ProgrammingError:", e)

        await conn.close()

        try:
            cur2 = conn.cursor()
            await cur2.execute("SELECT 1")
        except nzpy.InterfaceError as e:
            print("Closed connection => InterfaceError:", e)
    except Exception:
        pass  # cleanup already done above


asyncio.run(main())
