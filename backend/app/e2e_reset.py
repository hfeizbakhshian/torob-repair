"""Drop and recreate the public schema of the *test* database, for an E2E run.

Guarded so it can never be pointed at the demo database by accident: it refuses unless
the URL names a database whose name ends in `_test`.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings


async def main() -> int:
    url = settings.database_url
    database = url.rsplit("/", 1)[-1].split("?")[0]
    if not database.endswith("_test"):
        print(
            f"از پاک‌کردن «{database}» خودداری شد: این دستور فقط روی پایگاه تست "
            "(با نام پایان‌یافته به _test) مجاز است.",
            file=sys.stderr,
        )
        return 1

    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    await engine.dispose()
    print(f"schema پایگاه تست «{database}» بازنشانی شد.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
