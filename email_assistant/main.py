from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routers import agents, auth, status
from config import API_TITLE, API_VERSION
from db import init_db
from services.background_worker import mailbox_worker


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    mailbox_worker.start()
    try:
        yield
    finally:
        mailbox_worker.stop()


app = FastAPI(
    title=API_TITLE,
    description="OUMA v2 aligned API for mailbox orchestration and email agents",
    version=API_VERSION,
    lifespan=lifespan,
)
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(status.router)
