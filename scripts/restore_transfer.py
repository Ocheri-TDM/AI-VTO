"""Restore the private Windows x64 transfer archive into a new project folder.

Never attach to an existing cluster, drop a database or replace an initialized
data directory. The regular launcher remains separate from one-time restore.
"""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import re
import socket
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
MIGRATION = ROOT / ".migration"
DATA = LOCAL / "postgres-data"
BIN = LOCAL / "postgres-bin/node_modules/@embedded-postgres/windows-x64/native/bin"
TOOLS = LOCAL / "postgres-tools"
PYTHON = LOCAL / "python312/python.exe"
VENV = ROOT / ".venv/Scripts/python.exe"
RECEIPT = LOCAL / "transfer-restore-receipt.json"
FORMAT = "souvenir-windows-transfer-v1"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def quoted_identifier(value):
    return '"' + value.replace('"', '""') + '"'


async def database_snapshot(connection):
    """Only digests and counts leave this function, never row contents."""
    metadata = dict(await connection.fetchrow(
        "SELECT current_database() AS database, current_user AS role, "
        "current_setting('server_version') AS version, "
        "pg_encoding_to_char(encoding) AS encoding, datcollate AS collate, datctype AS ctype "
        "FROM pg_database WHERE datname=current_database()"
    ))
    tables = await connection.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    result = {}
    for item in tables:
        name = item["tablename"]
        relation = 'public.' + quoted_identifier(name)
        row = await connection.fetchrow(
            f"SELECT count(*) AS rows, md5(coalesce(string_agg(h,'' ORDER BY h),'')) AS fingerprint "
            f"FROM (SELECT md5(row_to_json(t)::text) AS h FROM {relation} t) hashes"
        )
        result[name] = dict(row)
    return {
        "metadata": metadata,
        "extensions": [dict(r) for r in await connection.fetch(
            "SELECT extname AS name,extversion AS version FROM pg_extension ORDER BY extname"
        )],
        "alembic": [r[0] for r in await connection.fetch("SELECT version_num FROM alembic_version ORDER BY version_num")],
        "tables": result,
    }


def storage_strings(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from storage_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from storage_strings(child)
    elif isinstance(value, str):
        normalized = value.replace("\\", "/")
        if normalized.startswith(("assets/", "documents/", "models/", "keys/")):
            yield normalized


async def verify_storage_references(connection, storage):
    from app.application.offer_tokens import TokenVault

    refs = set()
    for row in await connection.fetch("SELECT input::text AS i,result::text AS r FROM presentation_revisions"):
        refs.update(storage_strings(json.loads(row["i"])))
        refs.update(storage_strings(json.loads(row["r"])))
    for row in await connection.fetch("SELECT draft::text FROM presentation_documents"):
        refs.update(storage_strings(json.loads(row[0])))
    missing = []
    for ref in sorted(refs):
        target = (storage / ref).resolve()
        if not target.is_relative_to(storage.resolve()):
            raise RuntimeError("Invalid stored asset path")
        if not target.is_file():
            missing.append(ref)
    shares = await connection.fetch("SELECT encrypted_token,token_hash FROM commercial_offer_shares")
    vault = TokenVault(storage, create=False)
    valid = 0
    for share in shares:
        # Validate functionality of the copied key without exposing capabilities.
        token = vault.reveal(share["encrypted_token"])
        if vault.digest(token) != share["token_hash"]:
            raise RuntimeError("Published link key does not match the saved database")
        valid += 1
    return {"references": len(refs), "missing": missing, "verified_links": valid}


def read_manifest():
    manifest = json.loads((MIGRATION / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise RuntimeError("Unsupported archive format")
    return manifest


def matching_receipt(manifest):
    if not RECEIPT.is_file():
        return False
    value = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if value.get("archive_id") != manifest["archive_id"] or not (DATA / "PG_VERSION").is_file():
        raise RuntimeError("Restore receipt and data directory do not match. Do not overwrite the database.")
    return True


def check_files(manifest):
    dump = MIGRATION / "souvenir.dump"
    if digest(dump) != manifest["dump_sha256"]:
        raise RuntimeError("Database dump checksum mismatch. Extract the archive again into a new directory.")
    storage = LOCAL / "presentations"
    for relative, expected in manifest["storage_sha256"].items():
        path = (storage / relative).resolve()
        if not path.is_relative_to(storage.resolve()) or not path.is_file() or digest(path) != expected:
            raise RuntimeError("Presentation storage is incomplete or damaged. Extract the complete archive.")


def port_open(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def run(command, **kwargs):
    subprocess.run([str(arg) for arg in command], check=True, cwd=ROOT, **kwargs)


def prepare_python():
    if not PYTHON.is_file():
        raise RuntimeError("Bundled Python executable is missing")
    if not VENV.is_file():
        if (ROOT / ".venv").exists():
            raise RuntimeError("An incomplete .venv exists. Move it aside before retrying.")
        print("Preparing a fresh relocated Python environment...", flush=True)
        run([PYTHON, "-m", "venv", ROOT / ".venv"])
    # A copied original venv must never silently bind to the source installation.
    base = subprocess.check_output([str(VENV), "-c", "import sys; print(sys.base_prefix)"], text=True).strip()
    if Path(base).resolve() != PYTHON.parent.resolve():
        raise RuntimeError("The Python environment belongs to a different installation. Use a freshly extracted folder.")
    ready = LOCAL / "transfer-python-ready"
    if not ready.exists():
        wheels = MIGRATION / "wheels"
        print("Installing pinned dependencies from the archive (offline)...", flush=True)
        run([VENV, "-m", "pip", "install", "--disable-pip-version-check", "--no-index", "--find-links", wheels,
             "-r", MIGRATION / "requirements.lock"])
        run([VENV, "-m", "pip", "install", "--disable-pip-version-check", "--no-index", "--no-build-isolation",
             "--no-deps", "-e", ROOT / "apps/api"])
        run([VENV, "-m", "pip", "check"])
        ready.write_text("ok\n", encoding="utf-8")


async def restore_database(manifest):
    import asyncpg
    from app.config import Settings
    from sqlalchemy.engine import make_url

    settings = Settings()
    url = make_url(settings.database_url)
    recorded = manifest["database"]["metadata"]
    if (url.drivername != "postgresql+asyncpg" or url.host != "127.0.0.1" or url.port != 55432
            or url.database != recorded["database"] or url.username != recorded["role"]
            or not url.password or not re.fullmatch(r"[A-Za-z0-9_]+", url.database)
            or not re.fullmatch(r"[A-Za-z0-9_]+", url.username)):
        raise RuntimeError("Archive DATABASE_URL must target its own local PostgreSQL on port 55432")
    if settings.presentation_storage.resolve() != (LOCAL / "presentations").resolve():
        raise RuntimeError("Archive storage must be inside the extracted project")
    if recorded["encoding"] != "UTF8" or recorded["collate"] != "C" or recorded["ctype"] != "C":
        raise RuntimeError("Unexpected database encoding or locale")
    if matching_receipt(manifest):
        print("Database already restored; existing data will not be reimported.", flush=True)
        return
    if DATA.exists():
        raise RuntimeError("postgres-data already exists. Restore never destroys or replaces an existing cluster.")
    if port_open(55432):
        raise RuntimeError("Port 55432 is in use. Stop the other local PostgreSQL or use a different PC.")
    check_files(manifest)
    print("Initializing an empty PostgreSQL 17 cluster...", flush=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=LOCAL, delete=False) as stream:
        stream.write(url.password + "\n")
        password_file = Path(stream.name)
    try:
        run([BIN / "initdb.exe", "-D", DATA, "-U", url.username, "--encoding=UTF8", "--locale=C",
             "--auth=scram-sha-256", "--pwfile", password_file, "--no-instructions"])
    finally:
        password_file.unlink(missing_ok=True)
    command = [BIN / "pg_ctl.exe", "-D", DATA]
    flags = subprocess.CREATE_NO_WINDOW
    try:
        run([*command, "-l", LOCAL / "restore-postgres.log", "-o", "-p 55432 -h 127.0.0.1", "-w", "-t", "60", "start"],
            creationflags=flags)
        connection_options = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
        admin = await asyncpg.connect(database="postgres", **connection_options)
        try:
            await admin.execute(f"CREATE DATABASE {quoted_identifier(url.database)} TEMPLATE template0")
        finally:
            await admin.close()
        print("Restoring the logical database dump...", flush=True)
        env = {**os.environ, "PGPASSWORD": url.password}
        run([TOOLS / "pg_restore.exe", "--host", url.host, "--port", str(url.port), "--username", url.username,
             "--no-password", "--dbname", url.database, "--no-owner", "--no-privileges", "--exit-on-error",
             "--single-transaction", MIGRATION / "souvenir.dump"], env=env, creationflags=flags)
        connection = await asyncpg.connect(database=url.database, **connection_options)
        try:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                await connection.execute("SET LOCAL TIME ZONE 'UTC'; SET LOCAL DateStyle TO 'ISO, YMD'")
                actual = await database_snapshot(connection)
                if actual != manifest["database"]:
                    differing = sorted(name for name in set(actual["tables"]) | set(manifest["database"]["tables"])
                                       if actual["tables"].get(name) != manifest["database"]["tables"].get(name))
                    raise RuntimeError(f"Restored database verification failed: {differing or 'metadata/schema mismatch'}")
                storage = await verify_storage_references(connection, settings.presentation_storage)
                if storage != manifest["storage_references"]:
                    raise RuntimeError("Stored document links or assets did not survive restore")
        finally:
            await connection.close()
        receipt = {"archive_id": manifest["archive_id"], "restored_at": datetime.now(timezone.utc).isoformat(),
                   "tables": len(actual["tables"]), "storage_references": storage["references"],
                   "verified_links": storage["verified_links"]}
        RECEIPT.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(f"Verified {len(actual['tables'])} tables and {storage['verified_links']} saved link keys.", flush=True)
    finally:
        # Only the newly initialized, task-owned data directory is stopped.
        status = await asyncio.to_thread(subprocess.run, [str(arg) for arg in (*command, "status")],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=flags, check=False)
        if status.returncode == 0:
            await asyncio.to_thread(run, [*command, "-m", "fast", "-w", "stop"], creationflags=flags)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name != "nt" or struct.calcsize("P") != 8 or platform.machine().casefold() not in ("amd64", "x86_64"):
        raise RuntimeError("This transfer archive requires Windows x64")
    manifest = read_manifest()
    if not matching_receipt(manifest) and DATA.exists():
        raise RuntimeError("Restore refuses an existing or incomplete postgres-data directory")
    if args.database:
        asyncio.run(restore_database(manifest))
    else:
        prepare_python()
        run([VENV, __file__, "--database"])
        print("Transfer restored. Start the project with start-project.bat.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        # Do not print subprocess environment, database URL, or secret values.
        print(f"Restore error: {exc}", file=sys.stderr)
        sys.exit(1)
