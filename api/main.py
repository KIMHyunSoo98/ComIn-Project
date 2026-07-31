"""
FastAPI 앱 진입점.
lifespan에서 보고서 이력 테이블을 만들고 research 라우터를 등록한다.

실행: 루트에서 `uv run uvicorn api.main:app --reload`
문서: http://127.0.0.1:8000/docs
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.database import Base, engine
from api.models import research_model  # noqa: F401  Base에 테이블 등록용 import
from api.routers import research_router
from observability.tracing import setup_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing()
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="ComIn API", lifespan=lifespan)
app.include_router(research_router.router)
