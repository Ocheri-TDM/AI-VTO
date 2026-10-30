import asyncio
import sys

import uvicorn

from app.config import Settings
from app.main import create_app


def main() -> None:
    settings = Settings()
    config = uvicorn.Config(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # One API process owns the in-process scheduler; jobs remain durable in PostgreSQL.
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
            runner.run(server.serve())
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
