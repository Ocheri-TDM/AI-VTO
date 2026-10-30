"""Explicit one-time reset; never imported or called by application startup."""

# The standalone command must add the API source directory before app imports.
# ruff: noqa: E402

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import Base, SupplierOffer
from app.providers.vto import VTOProvider


async def preflight(settings):
    provider = VTOProvider(settings)
    try:
        parent = (await provider.request("https://vto.kz/wp-json/wc/store/products/1521733")).json()
        products = await provider.products(parent)
        if len(products) != len(parent["variations"]) or len(products) < 2:
            raise RuntimeError("VTO variation coverage not confirmed; database preserved")
        if not all(p.stock_quantity is not None and p.primary_image and p.brand and p.material
                   and p.original_price is not None for p in products):
            raise RuntimeError("VTO fields not confirmed; database preserved")
        with tempfile.TemporaryDirectory(prefix="vto-reset-preflight-") as directory:
            config = Settings(database_url="sqlite+aiosqlite:///" + (Path(directory) / "test.sqlite").as_posix())
            engine, sessions = create_database(config.database_url)
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
                index = IndexRepository(sessions, config)
                await index.ingest(products)
                summary = await index.ingest(products)
                if summary["products_changed"] != 0:
                    raise RuntimeError("VTO replay is not idempotent")
                async with sessions() as db:
                    if await db.scalar(select(func.count()).select_from(SupplierOffer)) != len(products):
                        raise RuntimeError("VTO preflight offer count mismatch")
            finally:
                await engine.dispose()
    finally:
        await provider.close()


async def reset(execute):
    settings = Settings()
    url = make_url(settings.database_url)
    target = (url.host, url.port, url.database, url.username)
    if target != ("127.0.0.1", 55432, "souvenir", "souvenir"):
        raise RuntimeError("Refusing reset: target differs from the verified project database")
    marker = ROOT / ".local/vto-reset-receipt.json"
    if marker.exists():
        raise RuntimeError("One-time VTO reset already recorded; use normal sync")
    await preflight(settings)
    if not execute:
        print("VTO preflight passed. Reset requires --execute-reset souvenir")
        return
    admin_url = url.set(database="postgres")
    engine, sessions = create_database(str(admin_url.render_as_string(hide_password=False)))
    try:
        async with engine.connect() as db:
            owner = await db.scalar(text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='souvenir'"))
            if owner != "souvenir":
                raise RuntimeError("Database owner mismatch")
            connections = await db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname='souvenir' AND backend_type='client backend'"))
            if connections:
                raise RuntimeError("Stop all application writers before reset")
    finally:
        await engine.dispose()
    backup_dir = Path.home() / "AI-VTO-backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"souvenir-before-vto-{timestamp}.dump"
    binaries = ROOT / ".local/postgres-tools"
    env = dict(os.environ, PGPASSWORD=url.password or "")
    args = ["-h", url.host, "-p", str(url.port), "-U", url.username]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    await asyncio.to_thread(subprocess.run, [str(binaries / "pg_dump.exe"), *args, "-Fc", "-f", str(backup), url.database],
                   check=True, env=env, creationflags=flags)
    listing = await asyncio.to_thread(subprocess.check_output, [str(binaries / "pg_restore.exe"), "--list", str(backup)], creationflags=flags)
    if backup.stat().st_size < 1024 or b"TABLE DATA" not in listing or b"supplier_offers" not in listing:
        raise RuntimeError("Backup structure verification failed")
    # Recheck and prohibit reconnects atomically before terminating only this DB.
    engine, _ = create_database(str(admin_url.render_as_string(hide_password=False)))
    try:
        async with engine.connect() as db:
            db = await db.execution_options(isolation_level="AUTOCOMMIT")
            await db.execute(text('ALTER DATABASE "souvenir" WITH ALLOW_CONNECTIONS false'))
            await db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='souvenir'"))
            await db.execute(text('DROP DATABASE "souvenir"'))
            await db.execute(text('CREATE DATABASE "souvenir" OWNER "souvenir"'))
    finally:
        await engine.dispose()
    await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "alembic", "-c", str(ROOT / "apps/api/alembic.ini"), "upgrade", "head"],
                   check=True, cwd=ROOT / "apps/api", creationflags=flags)
    engine, sessions = create_database(settings.database_url)
    try:
        async with sessions.begin() as db:
            # Historical migration creates a disabled owner for pre-auth projects.
            # The newly created empty database has no legacy projects to own.
            await db.execute(text("DELETE FROM users WHERE normalized_name='__legacy_local__' AND password_hash='!legacy-account-has-no-login!'"))
            if await db.scalar(text("SELECT count(*) FROM supplier_offers")) != 0:
                raise RuntimeError("New catalog is not empty")
            if await db.scalar(text("SELECT count(*) FROM catalog_sync_jobs")) != 0:
                raise RuntimeError("New queue is not empty")
    finally:
        await engine.dispose()
    marker.write_text(json.dumps({"database": target, "backup": str(backup), "dump_entries_verified": True,
                                  "completed_at": timestamp}, indent=2), encoding="utf-8")
    print(json.dumps({"reset": "completed", "backup": str(backup)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-reset", choices=["souvenir"])
    asyncio.run(reset(bool(parser.parse_args().execute_reset)))
