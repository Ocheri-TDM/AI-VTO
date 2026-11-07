"""Right-side control acceptance, sharing the existing exact-ID cleanup ledger."""

import asyncio
import json
import sys

import check_clean_start as acceptance

acceptance.LEDGER = acceptance.ROOT / ".local/right-chat-ledger.json"
acceptance.PUBLIC = acceptance.ROOT / "docs/screenshots/right-chat"


async def main():
    if sys.argv[1] == "placement":
        from check_right_chat_browser import run

        state = json.loads(acceptance.LEDGER.read_text("utf-8"))
        return await run(state, acceptance.ROOT, acceptance.LEDGER, acceptance.PUBLIC)
    return await acceptance.main()


if __name__ == "__main__":
    asyncio.run(main())
