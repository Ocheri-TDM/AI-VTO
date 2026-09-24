"""Manage the already prepared portable Windows PostgreSQL in .local.

Uses native subprocess arguments and hidden windows; no execution-policy changes.
Fresh checkouts should follow the Docker/local PostgreSQL setup in README.
"""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
DATA = LOCAL / "postgres-data"
BIN = LOCAL / "postgres-bin/node_modules/@embedded-postgres/windows-x64/native/bin"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "status"), default="start", nargs="?")
    args = parser.parse_args()
    if not (BIN / "postgres.exe").exists() or not (DATA / "PG_VERSION").exists():
        raise SystemExit("Portable PostgreSQL is not prepared. Follow README for Docker/local PostgreSQL setup.")
    command = [str(BIN / "pg_ctl.exe"), "-D", str(DATA)]
    flags = subprocess.CREATE_NO_WINDOW
    if args.action != "start":
        raise SystemExit(subprocess.call(command + (["-m", "fast", "stop"] if args.action == "stop" else ["status"]), creationflags=flags))
    if subprocess.run(command + ["status"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags).returncode == 0:
        print("Local PostgreSQL is already running on 127.0.0.1:55432.")
        return
    with (LOCAL / "postgres-stdout.log").open("ab") as out, (LOCAL / "postgres-stderr.log").open("ab") as err:
        process = subprocess.Popen([str(BIN / "postgres.exe"), "-D", str(DATA), "-p", "55432", "-h", "127.0.0.1"], stdout=out, stderr=err, creationflags=flags)
    (LOCAL / "postgres.pid").write_text(str(process.pid))
    print(f"Local PostgreSQL started: PID {process.pid}, port 55432.")


if __name__ == "__main__":
    main()
