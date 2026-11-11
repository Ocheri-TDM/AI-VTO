"""Local acceptance-only second API. No lifespan, crawler, render worker or recovery.

Faults are opt-in CLI arguments for an exact disposable document/mutation.
Never use this server for normal deployment.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from app.config import Settings
from app.main import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", required=True)
    parser.add_argument("--crash-mutation")
    parser.add_argument("--rollback-mutation")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    settings = Settings().model_copy(
        update={
            "background_research_enabled": False,
            "presentation_worker_enabled": False,
        }
    )
    if args.baseline:
        root = Path(__file__).resolve().parents[1]
        source = subprocess.run(
            ["git", "show", "HEAD:apps/api/app/main.py"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
        namespace = {
            "__name__": "selection_baseline",
            "__file__": str(root / "apps/api/app/main.py"),
        }
        exec(compile(source, namespace["__file__"], "exec"), namespace)  # noqa: S102 -- Fixed trusted Git HEAD source, no remote/user code.
        app = namespace["create_app"](settings, [])
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=8002,
            lifespan="off",
            access_log=False,
            log_level="critical",
        )
        return
    app = create_app(settings, [])
    original = app.state.client_selections.mutate

    async def mutate(token, cookies, csrf, body):
        if str(body.mutation_id) == args.rollback_mutation:
            import app.application.client_selections as implementation

            allocate = implementation.next_owner_event

            async def rollback_after_event(db, user, document, *rest, **kw):
                if document != args.document:
                    raise ValueError("Fault scope mismatch")
                await allocate(db, user, document, *rest, **kw)
                raise RuntimeError("Acceptance-only rollback after event allocation")

            implementation.next_owner_event = rollback_after_event
            try:
                return await original(token, cookies, csrf, body)
            finally:
                implementation.next_owner_event = allocate
        result = await original(token, cookies, csrf, body)
        if str(body.mutation_id) == args.crash_mutation:
            # A real abrupt process exit after the transaction committed and
            # before an HTTP success or any in-process delivery can occur.
            os._exit(91)
        return result

    app.state.client_selections.mutate = mutate
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8001,
        lifespan="off",
        access_log=False,
        log_level="critical",
    )


if __name__ == "__main__":
    main()
