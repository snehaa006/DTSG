import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db, deps
from .config import get_settings
from .routers import chat, extract, health

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await db.connect(settings)
    deps.init(settings)
    try:
        yield
    finally:
        await deps.shutdown()
        await db.disconnect()


settings = get_settings()

app = FastAPI(
    title="DTSG",
    description="Dynamic Temporal State Graph — memory as a timeline of facts "
    "with changing validity.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(extract.router)


@app.get("/")
async def root():
    return {"service": "dtsg", "phase": 1, "docs": "/docs"}
