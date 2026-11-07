"""Main-database acceptance phases; isolated provider tests never contact VTO."""


async def run(phase, state, root, ledger, public):
    if phase == "postgres-doc":
        from quality.nav_recovery_postgres import document_race
        return await document_race(state,public)
    if phase == "postgres":
        from quality.nav_recovery_postgres import run as postgres
        return await postgres(state,root,ledger,public)
    if phase in {"deploy", "restart", "web"}:
        from quality.chat_workflow_acceptance import run as services
        return await services(phase, root, ledger, public)
    if phase in {"before", "browser"}:
        from quality.nav_recovery_browser import run as browser
        return await browser(state, root, ledger, public, phase == "before")
    if phase in {"http", "race", "browser-draft", "verify", "cleanup", "sample"}:
        from quality.nav_recovery_http import run as http
        return await http(phase, state, root, ledger, public)
    raise ValueError(phase)
