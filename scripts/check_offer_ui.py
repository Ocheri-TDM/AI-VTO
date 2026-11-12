"""Exact-ID UI acceptance using the existing populated-DB acceptance harness."""

import asyncio
import json
import sys

import check_chat_workflow as common
import check_client_selection as acceptance

ROOT = acceptance.ROOT
LEDGER = ROOT / ".local/offer-ui-ledger.json"
PUBLIC = ROOT / "docs/screenshots/offer-ui"


async def main():
    acceptance.LEDGER, acceptance.PUBLIC = LEDGER, PUBLIC
    phase = sys.argv[1]
    passwords = {}
    original = common.call

    async def capture(client, method, path, **kwargs):
        result = await original(client, method, path, **kwargs)
        if path == "/api/auth/register":
            body = kwargs["json"]
            passwords[body["name"]] = body["password"]
        return result

    common.call = acceptance.baseline.call = capture
    if phase in ("before", "after"):
        from check_offer_ui_browser import run

        return await run(
            json.loads(LEDGER.read_text("utf-8")), ROOT, LEDGER, PUBLIC, phase
        )
    if phase == "setup":
        from quality import client_selection_http

        client_selection_http.call = capture
    await acceptance.main()
    if passwords:
        state = json.loads(LEDGER.read_text("utf-8"))
        for user in state["users"]:
            if user["name"] in passwords:
                user["password"] = passwords[user["name"]]
        acceptance.baseline.save(LEDGER, state)


if __name__ == "__main__":
    asyncio.run(main())
