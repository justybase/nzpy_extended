import asyncio
import os
import sys

# Automatically add the root directory to the path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import nzpy_extended

# Connection configuration
HOST = os.getenv("NZ_HOST", "127.0.0.1")
PORT = int(os.getenv("NZ_PORT", "5480"))
USER = os.getenv("NZ_USER", "admin")
PASSWORD = os.getenv("NZ_PASSWORD", "password")
DATABASE = os.getenv("NZ_DATABASE", "SYSTEM")

async def test_protocol():
    print(f"--- Binary vs text protocol test ---")
    
    conn = await nzpy_extended.connect(
        user=USER, password=PASSWORD, host=HOST, port=PORT, database=DATABASE
    )
    
    async with conn.cursor() as cursor:
        print("\n1. Test plain SELECT (without ANALYZE):")
        # Clear debug codes
        conn._proto_codes = []
        await cursor.execute("SELECT 1 as COL1")
        await cursor.fetchall()
        print(f"Codes used: {conn._proto_codes}")
        
        print("\n2. Test SELECT with LIMIT (from benchmark):")
        conn._proto_codes = []
        await cursor.execute("SELECT (RANDOM()*100)::INT COL1 FROM JUST_DATA..FACTPRODUCTINVENTORY LIMIT 10")
        await cursor.fetchall()
        print(f"Codes used: {conn._proto_codes}")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(test_protocol())
