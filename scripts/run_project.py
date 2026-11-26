"""Windows local launcher and a safe, live HTTP activity console."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psutil
from pydantic import ValidationError
from pydantic_settings import SettingsError

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
WEB = ROOT / "apps/web"
sys.path.insert(0, str(ROOT / "apps/api"))

from app.config import Settings


def listening(port):
    with socket.socket() as probe:
        probe.settimeout(.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def owned_listener(port, kind):
    for connection in psutil.net_connections(kind="tcp"):
        if connection.status != psutil.CONN_LISTEN or connection.laddr.port != port or not connection.pid:
            continue
        process = psutil.Process(connection.pid)
        for candidate in [process, *process.parents()]:
            executable = Path(candidate.exe())
            command = candidate.cmdline()
            if kind == "api" and executable == ROOT / ".venv/Scripts/python.exe" and command[-2:] == ["-m", "app"]:
                return True
            if kind == "web" and str(WEB / "node_modules/next/dist/bin/next") in command:
                return True
            if kind == "db" and executable.is_relative_to(LOCAL / "postgres-bin"):
                return True
            if kind == "llm" and executable == LOCAL / "ollama/ollama.exe":
                return True
    return False


def guard_port(port, kind):
    if listening(port) and not owned_listener(port, kind):
        raise RuntimeError(f"Порт {port} занят другим процессом. Он не был остановлен.")


def wait_http(url, process=None, seconds=180):
    deadline = time.monotonic() + seconds
    with httpx.Client(timeout=3, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process and process.poll() is not None:
                raise RuntimeError("Сервис завершился при запуске. Подробности в .local/*-launcher.log.")
            try:
                response = client.get(url)
                if response.status_code == 200:
                    return response
            except httpx.HTTPError:
                pass
            time.sleep(.5)
    raise RuntimeError(f"Сервис не стал доступен: {url}. Проверьте .local/*-launcher.log.")


def spawn(arguments, cwd, log, env):
    with log.open("ab") as output:
        return subprocess.Popen(arguments, cwd=cwd, env=env, stdout=output, stderr=output,
                                creationflags=subprocess.CREATE_NO_WINDOW)


@contextmanager
def startup_lock():
    import msvcrt

    with (LOCAL / "project-start.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"1")
            handle.flush()
        acquired = False
        deadline = time.monotonic() + 240
        while not acquired:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Другой запуск ещё выполняется. Проверьте его консоль.") from None
                time.sleep(.5)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def needs_build():
    build = WEB / ".next/BUILD_ID"
    if not build.is_file():
        return True
    inputs = [*WEB.glob("*.json"), *WEB.glob("next.config.*"), *WEB.glob(".env*"), ROOT / ".env"]
    inputs.extend(path for path in (WEB / "src").rglob("*") if path.is_file())
    return any(path.is_file() and path.stat().st_mtime > build.stat().st_mtime for path in inputs)


def prepare(settings):
    node = shutil.which("node")
    next_cli = WEB / "node_modules/next/dist/bin/next"
    if not node or not next_cli.is_file():
        raise RuntimeError("Нужны установленный Node.js и зависимости apps/web (npm ci).")
    if settings.api_host not in ("127.0.0.1", "localhost", "0.0.0.0"):
        raise RuntimeError("Локальный запуск требует API_HOST=127.0.0.1, localhost или 0.0.0.0.")
    api_origin = f"http://127.0.0.1:{settings.api_port}"
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "API_BASE_URL": api_origin}
    for port, kind in [(55432, "db"), (11434, "llm"), (settings.api_port, "api"), (3000, "web")]:
        guard_port(port, kind)
    for script, name, port in [("local_db.py", "PostgreSQL", 55432), ("local_llm.py", "Ollama", 11434)]:
        if listening(port):
            print(f"{name}: уже работает", flush=True)
            continue
        print(f"{name}: запуск…", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       check=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
    wait_http("http://127.0.0.1:11434/api/version")
    api = None
    if not listening(settings.api_port):
        print("API: запуск…", flush=True)
        api_env = {**env, "REQUEST_ACTIVITY_LOGGING": "true", "REQUEST_ACTIVITY_LOG": str(settings.request_activity_log)}
        api = spawn([sys.executable, "-m", "app"], ROOT / "apps/api", LOCAL / "api-launcher.log", api_env)
    health = wait_http(api_origin + "/api/health", api).json()
    if not health.get("request_activity_logging"):
        raise RuntimeError("API уже запущен без журнала. Остановите его и повторите запуск через start-project.bat.")
    web = None
    if not listening(3000):
        if needs_build():
            print("Frontend: собираю текущий код…", flush=True)
            build = spawn([node, str(next_cli), "build"], WEB, LOCAL / "web-build-launcher.log", env)
            if build.wait(timeout=300) != 0:
                raise RuntimeError("Не удалось собрать frontend. Проверьте .local/web-build-launcher.log.")
        print("Frontend: запуск…", flush=True)
        web = spawn([node, str(next_cli), "start", str(WEB), "--hostname", "127.0.0.1", "--port", "3000"],
                    WEB, LOCAL / "web-launcher.log", env)
    wait_http("http://127.0.0.1:3000/", web)
    wait_http("http://127.0.0.1:3000/api/health", web)
    print("\nГотово: http://127.0.0.1:3000", flush=True)


def format_activity(record):
    local = datetime.fromisoformat(record["timestamp"]).astimezone(timezone(timedelta(hours=5)))
    name = json.dumps(record.get("name") or "—", ensure_ascii=False)
    identifier = json.dumps(record.get("user_id") or "—", ensure_ascii=False)
    # JSON quoting also prevents names from inserting terminal controls/new lines.
    route = json.dumps(record["route"], ensure_ascii=False)
    return (f"{local:%Y-%m-%d %H:%M:%S} UTC+5 | {record['method']:<7} {route} | "
            f"{record['status']} | {record['duration_ms']:.1f} ms | "
            f"Name={name} Id={identifier} | {record['phase']}")


class JournalTail:
    def __init__(self, path):
        self.path, self.position, self.identity, self.partial = path, 0, None, b""
        if path.exists():
            stat = path.stat()
            self.position, self.identity = stat.st_size, (stat.st_dev, stat.st_ino)

    def read(self):
        try:
            with self.path.open("rb") as source:
                stat = os.fstat(source.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if identity != self.identity or stat.st_size < self.position:
                    self.position, self.partial = 0, b""
                self.identity = identity
                source.seek(self.position)
                data = source.read(65536)
                self.position = source.tell()
        except (FileNotFoundError, PermissionError):
            return []
        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except (ValueError, UnicodeError):
                continue
        return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Запустить и проверить сервисы без просмотра журнала")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Этот launcher предназначен для Windows.")
    LOCAL.mkdir(exist_ok=True)
    try:
        try:
            settings = Settings()
        except (ValidationError, SettingsError) as exc:
            raise RuntimeError(f"Не удалось прочитать настройки .env ({type(exc).__name__}).") from None
        print("Souvenir Studio — локальный запуск", flush=True)
        with startup_lock():
            prepare(settings)
        if args.once:
            return 0
        tail = JournalTail(settings.request_activity_log)
        print(f"Журнал: {settings.request_activity_log}\nВремя UTC+5. Ctrl+C закрывает журнал; сервисы продолжат работать.\n", flush=True)
        while True:
            for record in tail.read():
                print(format_activity(record), flush=True)
            time.sleep(.25)
    except KeyboardInterrupt:
        print("\nПросмотр журнала завершён. Сервисы продолжают работать.")
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError, psutil.Error) as exc:
        print(f"Ошибка запуска: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
