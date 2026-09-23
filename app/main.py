from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, engine
from app.engine import try_autoclose
from app.models import Base, RoundState, User
from app.redisutil import close_redis
from app.routers import admin, auth, play, ws
from app.security import hash_password
from app.ws import redis_subscriber

STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        settings = get_settings()
        existing = (await session.execute(select(User).where(User.username == settings.admin_username))).scalar_one_or_none()
        if existing is None:
            session.add(
                User(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    role="admin",
                )
            )
        rnd = (await session.execute(select(RoundState).where(RoundState.id == 1))).scalar_one_or_none()
        if rnd is None:
            session.add(RoundState(id=1, phase="idle"))
        try:
            await session.commit()
        except Exception:
            await session.rollback()

    sub_task = asyncio.create_task(_safe_sub())
    close_task = asyncio.create_task(_autoclose_loop())
    yield
    sub_task.cancel()
    close_task.cancel()
    await close_redis()
    await engine.dispose()


async def _safe_sub() -> None:
    while True:
        try:
            await redis_subscriber()
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(1)


async def _autoclose_loop() -> None:
    while True:
        try:
            async with SessionLocal() as session:
                closed = await try_autoclose(session)
                if closed:
                    await session.commit()
                    from app.routers.admin import _fanout

                    await _fanout(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(0.25)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Level Up Round 2", lifespan=lifespan)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(play.router)
    app.include_router(ws.router)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/ready")
    async def ready():
        from sqlalchemy import text
        from app.redisutil import get_redis

        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        r = await get_redis()
        await r.ping()
        return {"ok": True}

    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

        @app.get("/")
        async def root():
            return FileResponse(STATIC / "login.html")

        @app.get("/play")
        async def play_page():
            return FileResponse(STATIC / "play.html")

        @app.get("/admin")
        async def admin_page():
            return FileResponse(STATIC / "admin.html")

    @app.middleware("http")
    async def no_store_api(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app


app = create_app()
