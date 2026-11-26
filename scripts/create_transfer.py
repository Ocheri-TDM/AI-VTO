"""Create a private ZIP64 migration package from the current Windows installation.

The source API/frontend must be stopped; the local PostgreSQL must be running.
No physical active cluster, machine-bound venv, browser profile or old test logs
are copied. Output is a new directory outside the repository.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from restore_transfer import (
    FORMAT,
    database_snapshot,
    digest,
    port_open,
    verify_storage_references,
)
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings


def node_executable():
    bundled = LOCAL / "node/node.exe"
    return str(bundled) if bundled.is_file() else shutil.which("node")


def copy_tree(source, target, *, skip=None):
    source = Path(source).resolve()
    if not source.is_dir():
        raise RuntimeError(f"Required directory missing: {source.name}")
    copied = 0
    def failed(error):
        raise error

    for directory, subdirs, files in os.walk(source, followlinks=False, onerror=failed):
        directory = Path(directory)
        relative = directory.relative_to(source)
        subdirs[:] = [name for name in subdirs if not (skip and skip(relative / name))]
        for name in subdirs:
            path = directory / name
            if path.is_symlink() or os.path.isjunction(path):
                raise RuntimeError(f"Unexpected link in runtime directory: {name}")
        outdir = target / relative
        outdir.mkdir(parents=True, exist_ok=True)
        for name in files:
            path = directory / name
            if skip and skip(relative / name):
                continue
            if path.is_symlink() or os.path.isjunction(path):
                raise RuntimeError(f"Unexpected linked file in runtime directory: {name}")
            shutil.copy2(path, outdir / name)
            copied += 1
    print(f"Copied {source.name}: {copied} files", flush=True)


def copy_worktree(stage):
    names = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT)
    for relative in sorted(set(names.decode("utf-8").split("\0")) - {""}):
        source = ROOT / relative
        if source.is_file():
            if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
                raise RuntimeError("Unexpected linked source file")
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    remote = subprocess.run(["git", "config", "--local", "--get", "remote.origin.url"], cwd=ROOT,
                            capture_output=True, text=True, check=False).stdout.strip()
    parsed = urlsplit(remote)
    if parsed.scheme in ("http", "https") and (parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise RuntimeError("Git remote contains embedded credentials; prepare a credential-free remote before export")
    if (ROOT / ".git/index.lock").exists() or (ROOT / ".git/objects/info/alternates").exists():
        raise RuntimeError("Git repository is being updated or uses external object storage")
    copy_tree(ROOT / ".git", stage / ".git")
    shutil.copy2(ROOT / ".env", stage / ".env")
    # Always use the fresh machine's local API during local frontend builds.
    (stage / "apps/web/.env.production.local").write_text("API_BASE_URL=http://127.0.0.1:8000\n", encoding="utf-8")


async def dump_database(stage, settings):
    import asyncpg

    url = make_url(settings.database_url)
    if url.host != "127.0.0.1" or url.port != 55432 or url.database != "souvenir" or not url.password:
        raise RuntimeError("Export expects the project's local PostgreSQL, not a remote database")
    connection = await asyncpg.connect(host=url.host, port=url.port, database=url.database,
                                       user=url.username, password=url.password)
    dump = stage / ".migration/souvenir.dump"
    tools = LOCAL / "postgres-tools"
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            await connection.execute("SET LOCAL TIME ZONE 'UTC'; SET LOCAL DateStyle TO 'ISO, YMD'")
            snapshot = await connection.fetchval("SELECT pg_export_snapshot()")
            manifest = await database_snapshot(connection)
            storage_refs = await verify_storage_references(connection, settings.presentation_storage)
            env = {**os.environ, "PGPASSWORD": url.password}
            await asyncio.to_thread(subprocess.run, [str(tools / "pg_dump.exe"), "--host", url.host, "--port", str(url.port),
                            "--username", url.username, "--no-password", "--dbname", url.database,
                            "--format=custom", "--no-owner", "--no-privileges", "--snapshot", snapshot,
                            "--file", str(dump)], check=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        await connection.close()
    with (stage / ".migration/dump-toc.txt").open("wb") as stream:
        await asyncio.to_thread(subprocess.run, [str(tools / "pg_restore.exe"), "--list", str(dump)], check=True, stdout=stream,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    print(f"SQL SNAPSHOT COMPLETE: {len(manifest['tables'])} tables, {dump.stat().st_size} bytes", flush=True)
    return manifest, storage_refs


def prepare_dependencies(stage):
    base = Path(sys.base_prefix).resolve()
    python = stage / ".local/python312"
    python.mkdir(parents=True, exist_ok=True)
    for name in ("python.exe", "pythonw.exe", "python3.dll", "python312.dll", "vcruntime140.dll",
                 "vcruntime140_1.dll", "LICENSE.txt", "NEWS.txt"):
        shutil.copy2(base / name, python / name)
    for name in ("DLLs", "Lib", "include", "libs", "tcl"):
        copy_tree(base / name, python / name, skip=lambda p: "__pycache__" in p.parts or
                  "site-packages" in p.parts or p.suffix == ".pyc")
    packages = json.loads(subprocess.check_output([sys.executable, "-m", "pip", "list", "--format=json"], text=True))
    pinned = {p["name"].casefold(): f"{p['name']}=={p['version']}" for p in packages
              if p["name"].casefold() != "souvenir-search-api"}
    # The live venv was installed with PEP 517 build isolation; build tools
    # are not necessarily installed in it. Include them for offline restore.
    pinned.setdefault("setuptools", "setuptools==80.9.0")
    pinned.setdefault("wheel", "wheel==0.45.1")
    requirements = sorted(pinned.values())
    pins = stage / ".migration/requirements.lock"
    pins.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    wheels = stage / ".migration/wheels"
    wheels.mkdir()
    subprocess.run([sys.executable, "-m", "pip", "download", "--disable-pip-version-check", "--no-deps",
                    "--only-binary=:all:", "--dest", str(wheels), "-r", str(pins)], check=True)
    node = node_executable()
    if not node:
        raise RuntimeError("Node.js runtime not found")
    copy_tree(Path(node).parent, stage / ".local/node")
    copy_tree(ROOT / "apps/web/node_modules", stage / "apps/web/node_modules",
              skip=lambda p: ".cache" in p.parts)
    return len(requirements)


def write_archive(stage, output):
    archive = output / "AI-VTO.zip"
    temporary = output / "AI-VTO.partial.zip"
    files = sorted(path for path in stage.rglob("*") if path.is_file())
    with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as z:
        for n, path in enumerate(files, 1):
            name = "AI-VTO/" + path.relative_to(stage).as_posix()
            z.write(path, name)
            if n % 5000 == 0 or path.stat().st_size > 512 * 1024**2:
                print(f"ZIP: {n}/{len(files)} files", flush=True)
    with zipfile.ZipFile(temporary) as z:
        if z.testzip() is not None or len(z.infolist()) != len(files):
            raise RuntimeError("ZIP integrity check failed")
    temporary.replace(archive)
    sha = digest(archive)
    (output / "AI-VTO.zip.sha256.txt").write_text(sha + "  AI-VTO.zip\n", encoding="ascii")
    print(f"ARCHIVE VERIFIED: {archive.stat().st_size} bytes, {len(files)} files", flush=True)
    return archive, sha, len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise RuntimeError("Provide a new output directory outside the source repository")
    settings = Settings()
    if port_open(settings.api_port) or port_open(3000):
        raise RuntimeError("Stop API and frontend before taking the coordinated storage/database snapshot")
    if not port_open(55432) or settings.presentation_storage.resolve() != (LOCAL / "presentations").resolve():
        raise RuntimeError("Local PostgreSQL or portable storage is not prepared")
    stage = output / "rehearsal" / "AI-VTO"
    (stage / ".migration").mkdir(parents=True)
    (stage / ".local").mkdir()
    copy_worktree(stage)
    copy_tree(settings.presentation_storage, stage / ".local/presentations")
    storage = stage / ".local/presentations"
    hashes = {p.relative_to(storage).as_posix(): digest(p) for p in sorted(storage.rglob("*")) if p.is_file()}
    if any(digest(settings.presentation_storage / name) != sha for name, sha in hashes.items()):
        raise RuntimeError("Source presentation storage changed during export")
    db, refs = asyncio.run(dump_database(stage, settings))
    for name in ("postgres-bin", "postgres-tools", "ollama", "ollama-models"):
        copy_tree(LOCAL / name, stage / ".local" / name)
    for path in [*LOCAL.glob("request-activity.jsonl*"), LOCAL / "vto-reset-receipt.json"]:
        if path.is_file() and not path.is_symlink():
            shutil.copy2(path, stage / ".local" / path.name)
    pin_count = prepare_dependencies(stage)
    manifest = {"format": FORMAT, "archive_id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
                "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "database": db, "storage_references": refs, "storage_sha256": hashes,
                "dump_sha256": digest(stage / ".migration/souvenir.dump"),
                "dump_bytes": (stage / ".migration/souvenir.dump").stat().st_size,
                "python": sys.version.split()[0], "python_pins": pin_count,
                "node": subprocess.check_output([node_executable(), "--version"], text=True).strip()}
    (stage / ".migration/manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    archive, sha, files = write_archive(stage, output)
    shutil.copy2(stage / "docs/transfer-to-new-pc.md", output / "INSTRUCTION.md")
    (output / "transfer-summary.json").write_text(json.dumps({"archive_bytes": archive.stat().st_size,
        "sha256": sha, "files": files, "tables": {k: v["rows"] for k, v in db["tables"].items()},
        "storage_files": len(hashes), "storage_references": refs["references"],
        "missing_source_refs": len(refs["missing"]), "verified_links": refs["verified_links"],
        "python_pins": pin_count, "git_head": manifest["git_head"]}, indent=2), encoding="utf-8")
    print(f"Archive: {archive}", flush=True)


if __name__ == "__main__":
    main()
