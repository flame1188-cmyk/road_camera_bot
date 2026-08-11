"""
Mini App FastAPI приложение (монтируется в корневое приложение под /api).

НЕ запускается напрямую — используйте main.py в корне проекта.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import analyze, cameras, dtp, np_bdd, parse, regions


app = FastAPI(
    title="GIBDD Mini App API",
    description="API для Mini App: регионы, парсинг, задачи выгрузки, очаги, точка, LLM, камеры.",
    version="0.3.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Роутеры (могут монтироваться в родительское приложение)
app.include_router(regions.router)
app.include_router(parse.router)
app.include_router(dtp.router)
app.include_router(analyze.router)
app.include_router(cameras.router)
app.include_router(np_bdd.router)


@app.get("/miniapp/health")
async def miniapp_health():
    """Внутренний health-check Mini App."""
    return {
        "status": "ok",
        "service": "gibdd-miniapp",
        "version": "0.1.0",
    }
