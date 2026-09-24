"""One-off development helper for the isolated cluster in .local (port 55432)."""
import asyncio

import asyncpg


async def main():
    connection = await asyncpg.connect("postgresql://souvenir:souvenir-test@127.0.0.1:55432/postgres")
    try:
        for name in ("souvenir", "souvenir_test"):
            exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name)
            if not exists:
                await connection.execute(f'CREATE DATABASE "{name}"')
        print(await connection.fetchval("SELECT version()"))
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
