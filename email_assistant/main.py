from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routers import agents, auth, n8n, status
from config import API_TITLE, API_VERSION, APP_ROLE, ENABLE_BACKGROUND_WORKERS
from db import init_db
from services.background_worker import mailbox_worker
from services.neo4j_service import verify_neo4j_connection


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    verify_neo4j_connection()
    should_start_worker = APP_ROLE == "dev_worker" and ENABLE_BACKGROUND_WORKERS
    if should_start_worker:
        mailbox_worker.start()
    try:
        yield
    finally:
        if should_start_worker:
            mailbox_worker.stop()


app = FastAPI(
    title=API_TITLE,
    description="OUMA v2 aligned API for mailbox orchestration and email agents",
    version=API_VERSION,
    lifespan=lifespan,
)
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(n8n.router)
app.include_router(status.router)
