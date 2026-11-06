"""Vercel proxy + actual PostgreSQL acceptance; no Vercel account/login required.

prepare -> http -> browser -> race -> API restart -> verify -> cleanup.
Only exact recorded disposable IDs are mutable/deletable. Never print the ledger.
"""

import argparse
import asyncio
import hashlib
import json
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
import openpyxl
import psutil
from check_commercial_offers import call, edit_body, fingerprint, ready, save
from sqlalchemy import delete, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings
from app.database.connection import create_database
from app.database.models import PresentationDocument, User

ORIGIN = "https://souvenir-studio-three.vercel.app"
LEDGER = ROOT / ".local/vercel-offers-ledger.json"
PUBLIC = ROOT / "docs/screenshots/offer-lifecycle"
FIXTURES = ROOT / "apps/api/tests/fixtures/offer-lifecycle"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=["prepare", "http", "race", "verify", "cleanup", "peer"]
    )
    phase = parser.parse_args().phase
    if phase == "peer":
        import uvicorn
        from app.main import create_app

        app = create_app(
            Settings().model_copy(
                update={
                    "background_research_enabled": False,
                    "presentation_worker_enabled": False,
                }
            ),
            [],
        )
        # This disposable peer shares PostgreSQL locks but runs no scheduler,
        # startup recovery or render worker alongside the main application.
        await uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=8001,
                lifespan="off",
                access_log=False,
            )
        ).serve()
        return
    state = (
        json.loads(LEDGER.read_text("utf-8"))
        if LEDGER.exists()
        else {"run": secrets.token_hex(8), "users": [], "projects": [], "before": []}
    )
    engine, sessions = create_database(Settings().database_url)
    evidence = {
        "checks": [],
        "frontend_deployment": "NOT DEPLOYED: user requested preparation without Vercel login",
    }

    def check(label, valid):
        assert valid, label
        evidence["checks"].append({"check": label, "status": "PASS"})

    try:
        async with (
            httpx.AsyncClient(
                base_url=ORIGIN, headers={"Origin": ORIGIN}, timeout=180
            ) as c,
            httpx.AsyncClient(base_url=ORIGIN, timeout=180) as guest,
        ):
            if phase == "prepare":
                state["catalog_before"] = await fingerprint(sessions)
                state["key_hash"] = hashlib.sha256(
                    (
                        Settings().presentation_storage / "keys/commercial-offers.key"
                    ).read_bytes()
                ).hexdigest()
                while len(state["users"]) < 2:
                    c.cookies.clear()
                    name = (
                        "offers-deploy-" + state["run"] + "-" + str(len(state["users"]))
                    )
                    password = secrets.token_urlsafe(24)
                    user = await call(
                        c,
                        "POST",
                        "/api/auth/register",
                        json={"name": name, "password": password},
                    )
                    state["users"].append(
                        {
                            "id": user["id"],
                            "name": name,
                            "password": password,
                            "cookies": dict(c.cookies),
                        }
                    )
                    save(LEDGER, state)
                c.cookies.clear()
                response = await c.post(
                    "/api/auth/login",
                    json={k: state["users"][0][k] for k in ("name", "password")},
                )
                check("ordinary HTTPS login", response.status_code == 200)
                cookie = response.headers["set-cookie"].lower()
                check(
                    "host-only secure httponly lax cookie",
                    all(
                        k in cookie
                        for k in ("secure", "httponly", "samesite=lax", "path=/")
                    )
                    and "domain=" not in cookie,
                )
                state["users"][0]["cookies"] = dict(c.cookies)
                while len(state["projects"]) < 2:
                    chat = await call(
                        c,
                        "POST",
                        "/api/chats",
                        json={"project_name": "Offer lifecycle acceptance"},
                    )
                    project = next(
                        p
                        for p in await call(c, "GET", "/api/projects")
                        if p["chat_id"] == chat["id"]
                    )
                    state["projects"].append(project["id"])
                    if "project" not in state:
                        state["project"] = project
                    else:
                        state["race_project"] = project
                    save(LEDGER, state)
                state["selections"] = []
                for query, preferred in [
                    ("ручки от 250 шт", "vto:1934569"),
                    ("рюкзаки от 100 шт", "vto:1823109"),
                    ("бутылки", "vto:1883738"),
                ]:
                    r = (
                        await call(
                            c,
                            "POST",
                            f"/api/projects/{state['project']['id']}/requests",
                            json={"query": query},
                        )
                    )["research"]
                    p = next(
                        (p for p in r["products"] if p["id"] == preferred),
                        r["products"][0],
                    )
                    await call(
                        c,
                        "POST",
                        f"/api/projects/{state['project']['id']}/collection/items",
                        json={"offer_id": p["id"], "source_research_id": r["id"]},
                    )
                    state["selections"].append(
                        {"offer_id": p["id"], "research_id": r["id"]}
                    )
                    save(LEDGER, state)
                r = (
                    await call(
                        c,
                        "POST",
                        f"/api/projects/{state['race_project']['id']}/requests",
                        json={"query": "ручки"},
                    )
                )["research"]
                await call(
                    c,
                    "POST",
                    f"/api/projects/{state['race_project']['id']}/collection/items",
                    json={
                        "offer_id": state["selections"][0]["offer_id"],
                        "source_research_id": r["id"],
                    },
                )
                if state.get("before"):
                    save(PUBLIC / "before-403.json", state["before"])
                save(PUBLIC / "https-login.json", evidence)
                print(
                    "Prepared two exact-ID users/projects through the existing Vercel HTTPS proxy"
                )
                return
            c.cookies.update(state["users"][0]["cookies"])
            check(
                "exact disposable owner",
                (await call(c, "GET", "/api/auth/me"))["id"] == state["users"][0]["id"],
            )
            if phase == "cleanup":
                async with sessions() as db:
                    docs = list(
                        await db.scalars(
                            select(PresentationDocument.id).where(
                                PresentationDocument.project_id.in_(state["projects"]),
                                PresentationDocument.user_id == state["users"][0]["id"],
                            )
                        )
                    )
                for project in state["projects"]:
                    r = await c.delete("/api/projects/" + project)
                    assert r.status_code in (204, 404)
                async with sessions.begin() as db:
                    for item in state["users"]:
                        user = await db.get(User, item["id"])
                        assert user and user.display_name.startswith(
                            "offers-deploy-" + state["run"]
                        )
                        await db.execute(delete(User).where(User.id == item["id"]))
                storage = Path(Settings().presentation_storage).resolve()
                for identifier in docs:
                    folder = (storage / "documents" / identifier).resolve()
                    assert (
                        folder.parent == storage / "documents"
                        and folder.name == identifier
                    )
                    if folder.exists():
                        shutil.rmtree(folder)
                check(
                    "commercial index preserved",
                    state["catalog_before"] == await fingerprint(sessions),
                )
                check(
                    "managed encryption key preserved",
                    hashlib.sha256(
                        (storage / "keys/commercial-offers.key").read_bytes()
                    ).hexdigest()
                    == state["key_hash"],
                )
                save(
                    PUBLIC / "cleanup.json",
                    {
                        **evidence,
                        "deleted_user_ids": [u["id"] for u in state["users"]],
                        "deleted_project_ids": state["projects"],
                        "deleted_document_ids": docs,
                        "catalog_before": state["catalog_before"],
                        "catalog_after": await fingerprint(sessions),
                    },
                )
                LEDGER.unlink()
                print(
                    "Only exact task-owned IDs/renders removed; index and encryption key unchanged"
                )
                return
            if phase == "verify":
                d = await call(c, "GET", "/api/offers/" + state["primary"])
                check("same editor after API restart", d["id"] == state["primary"])
                check(
                    "same active URL after restart",
                    d["share"]["public_url"] == state["public_url"],
                )
                check(
                    "expiry unchanged after restart",
                    d["share"]["expires_at"] == state["expires_at"],
                )
                check(
                    "public still anonymous",
                    (
                        await guest.get(
                            state["public_url"].replace(ORIGIN, "/api/public", 1)
                        )
                    ).status_code
                    == 200,
                )
                save(PUBLIC / "restart.json", evidence)
                print("Restart persistence verified through Vercel")
                return
            if phase == "race":
                code = Path(__file__).resolve()
                assert not any(
                    x.status == "LISTEN" and x.laddr.port == 8001
                    for x in psutil.net_connections("tcp")
                )
                with (ROOT / ".local/offer-lifecycle-race-api.log").open("ab") as log:
                    process = await asyncio.to_thread(
                        subprocess.Popen,
                        [sys.executable, str(code), "peer"],
                        cwd=ROOT,
                        stdout=log,
                        stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                try:
                    async with httpx.AsyncClient(
                        base_url="http://127.0.0.1:8001",
                        cookies=state["users"][0]["cookies"],
                        headers={"Origin": "http://127.0.0.1:3000"},
                        timeout=60,
                    ) as other:
                        for _ in range(80):
                            try:
                                if (await other.get("/api/health")).status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            await asyncio.sleep(0.1)
                        project = state["race_project"]["id"]
                        collection = await call(
                            c, "GET", f"/api/projects/{project}/collection"
                        )
                        body = {"collection_version": collection["version"]}
                        a, b = await asyncio.gather(
                            c.post(f"/api/projects/{project}/offers", json=body),
                            other.post(f"/api/projects/{project}/offers", json=body),
                        )
                        check(
                            "two real API processes accepted",
                            a.status_code in (200, 201) and b.status_code in (200, 201),
                        )
                        check(
                            "two processes one offer ID",
                            a.json()["id"] == b.json()["id"],
                        )
                        async with sessions() as db:
                            count = await db.scalar(
                                text(
                                    "SELECT count(*) FROM presentation_documents WHERE project_id=:p AND is_primary_offer"
                                ),
                                {"p": project},
                            )
                        check("PostgreSQL unique primary", count == 1)
                        save(PUBLIC / "cross-process.json", evidence)
                        print(
                            "Cross-process get-or-create verified against real PostgreSQL"
                        )
                finally:
                    parent = psutil.Process(process.pid)
                    targets = parent.children(recursive=True) + [parent]
                    for p in targets:
                        try:
                            p.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    await asyncio.to_thread(psutil.wait_procs, targets, timeout=5)
                return
            collection = await call(
                c, "GET", f"/api/projects/{state['project']['id']}/collection"
            )
            check(
                "read GET does not create",
                (await call(c, "GET", f"/api/projects/{state['project']['id']}/offer"))
                is None,
            )
            d = await call(
                c,
                "POST",
                f"/api/projects/{state['project']['id']}/offers",
                json={"collection_version": collection["version"]},
            )
            state["primary"] = d["id"]
            save(LEDGER, state)
            path = "/api/offers/" + d["id"]
            check(
                "snapshot quantity 250 100 1",
                [i["quantity"] for i in d["draft"]["items"]] == [250, 100, 1],
            )
            body = edit_body(d)
            body["items"][1].update(unit_price="35224.51", quantity=101)
            d = await call(c, "PATCH", path, json=body)
            for _ in range(3):
                again = await call(
                    c,
                    "POST",
                    f"/api/projects/{state['project']['id']}/offers",
                    json={"collection_version": 0},
                )
                check(
                    "stable create/open " + str(_),
                    again["id"] == d["id"] and again["draft"] == d["draft"],
                )
            response = await c.patch(
                path,
                json=edit_body(d),
                headers={
                    "Origin": "https://attacker.vercel.app",
                    "X-Forwarded-Host": "souvenir-studio-three.vercel.app",
                    "X-Forwarded-Proto": "https",
                },
            )
            check(
                "untrusted origin and forged forwarding rejected",
                response.status_code == 403
                and response.json().get("code") == "ORIGIN_REJECTED",
            )
            async with httpx.AsyncClient(
                base_url=ORIGIN, cookies=state["users"][1]["cookies"]
            ) as foreign:
                check(
                    "foreign editor denied",
                    (await foreign.get(path)).status_code == 404,
                )
            await call(
                c,
                "POST",
                path + "/exports",
                json={"version": d["version"], "request_id": str(uuid4())},
            )
            d, _revision = await ready(c, path, d["version"])
            check(
                "private export does not publish",
                not d["share"]["active"] and d["share"]["expires_at"] is None,
            )
            d = await call(
                c,
                "POST",
                path + "/publish",
                json={
                    "version": d["version"],
                    "request_id": str(uuid4()),
                    "share_epoch": d["share"]["epoch"],
                },
            )
            check(
                "full trusted frontend URL",
                d["share"]["public_url"].startswith(ORIGIN + "/offers/"),
            )
            check(
                "seven exact days",
                (
                    datetime.fromisoformat(d["share"]["expires_at"])
                    - datetime.fromisoformat(d["share"]["activated_at"])
                ).total_seconds()
                == 604800,
            )
            state["public_url"] = d["share"]["public_url"]
            state["expires_at"] = d["share"]["expires_at"]
            save(LEDGER, state)
            public = "/api/public" + d["share"]["public_url"].removeprefix(ORIGIN)
            dto = await call(guest, "GET", public)
            check("anonymous public API", bool(dto["items"]))
            check(
                "guest write rejected",
                (await guest.patch(public, json={})).status_code == 405,
            )
            check(
                "unknown token denied",
                (await guest.get("/api/public/offers/" + "x" * 43)).status_code == 404,
            )
            FIXTURES.mkdir(parents=True, exist_ok=True)
            hashes = {}
            for ext in ("pdf", "xlsx"):
                r = await guest.get(dto[ext + "_url"])
                check("anonymous " + ext, r.status_code == 200)
                (FIXTURES / ("offer." + ext)).write_bytes(r.content)
                hashes[ext] = hashlib.sha256(r.content).hexdigest()
            workbook = openpyxl.load_workbook(FIXTURES / "offer.xlsx", data_only=True)
            check("same amount in xlsx", workbook.active["I3"].value == 3557675.51)
            body = edit_body(d)
            body["items"][0]["quantity"] = 251
            d = await call(c, "PATCH", path, json=body)
            await call(
                c,
                "POST",
                path + "/publish",
                json={
                    "version": d["version"],
                    "request_id": str(uuid4()),
                    "share_epoch": d["share"]["epoch"],
                },
            )
            d, _ = await ready(c, path, d["version"])
            check(
                "update same URL and deadline",
                d["share"]["public_url"] == state["public_url"]
                and d["share"]["expires_at"] == state["expires_at"],
            )
            check(
                "old download pin returns 409",
                (await guest.get(dto["pdf_url"])).status_code == 409,
            )
            before = (d["version"], len(d["revisions"]), d["share"])
            for _ in range(10):
                d = await call(c, "GET", path)
            check(
                "ten link reads create nothing",
                (d["version"], len(d["revisions"]), d["share"]) == before,
            )
            check(
                "catalog unchanged",
                state["catalog_before"] == await fingerprint(sessions),
            )
            save(PUBLIC / "http.json", evidence)
            save(
                FIXTURES / "manifest.json",
                {
                    "source": "Actual Vercel proxy and populated PostgreSQL; isolated acceptance document",
                    "revision_id": dto["revision_id"],
                    "snapshot_hash": dto["snapshot_hash"],
                    "sha256": hashes,
                    "items": [
                        {
                            k: i[k]
                            for k in (
                                "sku",
                                "title",
                                "unit_price",
                                "quantity",
                                "line_amount",
                            )
                        }
                        for i in dto["items"]
                    ],
                },
            )
            print(
                "Vercel HTTP lifecycle acceptance:",
                len(evidence["checks"]),
                "checks; keys/cookies omitted",
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
