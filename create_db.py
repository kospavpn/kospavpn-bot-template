import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from database.models import Base

DATABASE_URL = "sqlite+aiosqlite:///./test.db"   # замени на свой файл

async def main():
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print("Таблицы созданы (или уже существуют).")

if __name__ == "__main__":
    asyncio.run(main())
