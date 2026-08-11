"""
Единая точка входа для bothost.ru: FastAPI + Telegram bot (webhook mode).

Структура:
- FastAPI запускается на порту из $PORT (bothost отдаёт через env)
- Telegram-бот работает в webhook-режиме на /bot/webhook
- Mini App frontend раздаётся из /app (после `npm run build` в miniapp/frontend)
- Существующие модули gibdd-bot импортируются напрямую (мы в корне проекта)

Запуск локально (для разработки):
    PORT=8080 python main.py

Запуск на bothost:
    Главный файл в настройках bothost: main.py
    Переменные окружения: см. .env.example

После первого деплоя установите webhook:
    curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<BOTHOST_DOMAIN>/bot/webhook"
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Убеждаемся, что корень gibdd-bot в sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Импортируем существующий конфиг gibdd-bot
from config import (
    TELEGRAM_BOT_TOKEN,
    ALLOWED_USER_IDS,
    LLM_API_KEY,
    LOG_LEVEL,
    validate_config,
)

# Настраиваем логирование ДО остальных импортов
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# FastAPI
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# Telegram
from telegram import Update
from telegram.ext import Application

# Mini App backend
from miniapp.backend.main import app as miniapp_app
from miniapp.backend.config import settings as miniapp_settings
from miniapp.backend.db.connection import (
    init_pool as db_init_pool,
    close_pool as db_close_pool,
    is_db_ready as db_is_ready,
)


# ============================================================
# Константы
# ============================================================
PORT = int(os.environ.get("PORT", "8080"))
# Нормализуем домен: убираем возможные протоколы/слэши/порт,
# которые пользователь мог случайно добавить в BOTHOST_DOMAIN.
# Например: "https://bot1234.bothost.tech/" → "bot1234.bothost.tech"
_raw_domain = os.environ.get("BOTHOST_DOMAIN", "").strip()
for _proto in ("https://", "http://", "www."):
    if _raw_domain.startswith(_proto):
        _raw_domain = _raw_domain[len(_proto):]
BOTHOST_DOMAIN = _raw_domain.rstrip("/").split(":")[0]  # отбрасываем порт, если есть
WEBHOOK_PATH = "/bot/webhook"
WEBHOOK_URL = f"https://{BOTHOST_DOMAIN}{WEBHOOK_PATH}" if BOTHOST_DOMAIN else ""

# Путь к собранному фронтенду (после `npm run build`)
FRONTEND_DIST = _PROJECT_ROOT / "miniapp" / "frontend" / "dist"


# ============================================================
# Создание Telegram Application (через существующий bot._build_app)
# ============================================================
def _create_telegram_app() -> Application:
    """
    Создаёт Telegram Application, переиспользуя существующую
    фабрику _build_app() из bot.py.

    bot.py уже настроил все handler'ы (start, help, dtp, regions,
    callback_query, текстовые сообщения, локации, документы, error_handler).
    """
    import bot as bot_module

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN не задан. Укажите его в .env"
        )

    # Используем существующую фабрику
    app = bot_module._build_app(TELEGRAM_BOT_TOKEN)
    logger.info("Telegram Application создан (через bot._build_app)")
    return app


async def _set_bot_commands(app: Application) -> None:
    """Устанавливает меню команд бота (видно в /menu и при вводе /)."""
    from telegram import BotCommand

    commands = [
        BotCommand("start", "Запустить бота"),
        BotCommand("dtp", "Выгрузка ДТП через кнопки"),
        BotCommand("miniapp", "Открыть веб-приложение"),
        BotCommand("regions", "Список регионов"),
        BotCommand("help", "Справка"),
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("Меню команд бота установлено")
    except Exception as exc:
        logger.warning(f"Не удалось установить меню команд: {exc}")


async def _register_telegram_webhook() -> None:
    """
    Регистрирует webhook в Telegram через Bot API setWebhook.

    Использует прямой HTTP-запрос к api.telegram.org (через httpx),
    а не PTB updater — поэтому не требует extra `python-telegram-bot[webhooks]`.

    Telegram будет слать POST /bot/webhook на наш BOTHOST_DOMAIN,
    FastAPI принимает его и передаёт в tg_app.process_update().
    """
    if not WEBHOOK_URL:
        logger.warning(
            "BOTHOST_DOMAIN не задан — webhook URL не зарегистрирован в Telegram. "
            "Укажите BOTHOST_DOMAIN в .env, либо установите webhook вручную: "
            "curl 'https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<DOMAIN>/bot/webhook'"
        )
        return

    import httpx

    api_url = (
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    )
    payload = {
        "url": WEBHOOK_URL,
        "allowed_updates": [
            "message",
            "edited_message",
            "callback_query",
            "inline_query",
        ],
        "drop_pending_updates": False,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(api_url, json=payload)
            data = resp.json()
        if data.get("ok"):
            logger.info(
                f"Telegram webhook зарегистрирован: {WEBHOOK_URL} "
                f"(description: {data.get('description', 'ok')})"
            )
        else:
            logger.error(
                f"setWebhook failed: {data}. "
                f"Webhook URL: {WEBHOOK_URL}"
            )
    except Exception as exc:
        logger.warning(
            f"Не удалось зарегистрировать webhook через API: {exc}. "
            f"Установите вручную: "
            f"curl 'https://api.telegram.org/bot<TOKEN>/setWebhook?url={WEBHOOK_URL}'"
        )


# Глобальный экземпляр Telegram Application
tg_app: Application = None  # type: ignore


# ============================================================
# Lifespan: запуск и остановка Telegram-бота в webhook-режиме
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл: запуск Telegram-бота + инициализация Mini App."""
    global tg_app

    # Проверяем конфигурацию
    errors = validate_config()
    if errors:
        logger.error(f"Ошибки конфигурации: {errors}")
        # Не падаем — FastAPI поднимется, но бот работать не будет

    # Создаём директорию для задач
    miniapp_settings.tasks_path.mkdir(parents=True, exist_ok=True)

    # Запускаем Telegram-бота в webhook-режиме через FastAPI endpoint.
    #
    # ВАЖНО: мы НЕ используем tg_app.updater.start_webhook() — он запускает
    # внутренний HTTP-сервер PTB и требует extra `python-telegram-bot[webhooks]`.
    # Вместо этого FastAPI сам принимает POST /bot/webhook и вызывает
    # tg_app.process_update(update). Это стандартный паттерн интеграции
    # PTB + FastAPI, не требующий никаких extras.
    if TELEGRAM_BOT_TOKEN:
        try:
            tg_app = _create_telegram_app()
            # initialize() загружает bot info (get_me) — проверяет токен
            await tg_app.initialize()
            # start() запускает handlers, но НЕ запускает updater/polling
            await tg_app.start()

            # Явно регистрируем webhook в Telegram (FastAPI endpoint уже готов)
            await _register_telegram_webhook()

            # Устанавливаем меню команд бота
            await _set_bot_commands(tg_app)
        except Exception as exc:
            logger.exception(f"Не удалось запустить Telegram-бота: {exc}")
            tg_app = None
    else:
        logger.warning(
            "TELEGRAM_BOT_TOKEN не задан — бот не запущен. "
            "Mini App продолжит работать, но без webhook."
        )

    # Инициализация Mini App — регионы загружаются лениво при первом
    # обращении к /api/regions, чтобы не блокировать старт сервера
    # (API ГИБДД может тормозить с ретраями до 20 сек).
    logger.info("Mini App: стартовая инициализация пропущена — lazy loading")

    # === Инициализация пула PostgreSQL (опционально) ===
    # Если DATABASE_URL задан — создаём пул и применяем схему.
    # Если нет или не удалось подключиться — приложение продолжает работу
    # с in-memory хранилищем (см. db/repository.py).
    try:
        db_ready = await db_init_pool()
        if db_ready:
            logger.info("PostgreSQL: пул готов, задачи и аудит-лог персистятся")
        else:
            logger.info(
                "PostgreSQL: in-memory fallback активирован "
                "(DATABASE_URL не задан или подключение не удалось)"
            )
    except Exception as exc:
        logger.warning(
            f"PostgreSQL init failed: {exc} — продолжаем с in-memory fallback"
        )

    # === Sprint 5: Task recovery на startup ===
    # После рестарта сервера in-flight задачи (status='fetching'/'parsing'/
    # 'analytics'/'generating'/'running') остаются в этом статусе вечно —
    # рабочий процесс, который их обрабатывал, умер. Помечаем их как failed,
    # чтобы пользователь увидел ошибку и мог пересоздать задачу.
    try:
        from miniapp.backend.db.repository import recover_incomplete_tasks
        recovered = await recover_incomplete_tasks()
        if recovered > 0:
            logger.info(
                f"Sprint 5 recovery: {recovered} незавершённых задач "
                f"помечено как failed (прервано рестартом сервера)"
            )
    except Exception as exc:
        logger.warning(f"Sprint 5 recovery failed: {exc}")

    # === Фоновая задача: периодическая очистка старых задач ===
    # In-memory хранилище _tasks растёт без ограничений — каждая задача
    # держит мегабайты карточек ДТП, prev_cards, raw_clusters и т.д.
    # Без очистки долгоживущий сервер упадёт по OOM после ~50-100 задач.
    # Запускаем очистку каждые 2 часа, удаляем задачи старше 24 часов.
    # При наличии БД — очистка идёт и в in-memory, и в БД (см. db/repository.py).
    async def _cleanup_loop():
        while True:
            try:
                await asyncio.sleep(7200)  # 2 часа
                from miniapp.backend.services.gibdd_service import (
                    cleanup_old_tasks,
                )
                removed = await cleanup_old_tasks(max_age_hours=24)
                if removed > 0:
                    logger.info(
                        f"Cleanup: удалено {removed} старых задач "
                        f"(старше 24 часов)"
                    )
                # Этап 3: чистим протухшие карточки в dtp_cards_cache.
                # Записи с expires_at < NOW() игнорируются при SELECT,
                # но физически занимают место — удаляем.
                try:
                    from miniapp.backend.db.cards_cache import cleanup_old_cards
                    cards_removed = await cleanup_old_cards()
                    if cards_removed > 0:
                        logger.info(
                            f"Cleanup: удалено {cards_removed} протухших "
                            f"записей кэша карточек"
                        )
                except Exception as ce:
                    logger.warning(f"Cleanup cards_cache error: {ce}")

                # Этап 4: чистим протухшие очаги в clusters_cache.
                try:
                    from miniapp.backend.db.clusters_cache import (
                        cleanup_old_clusters,
                    )
                    clusters_removed = await cleanup_old_clusters()
                    if clusters_removed > 0:
                        logger.info(
                            f"Cleanup: удалено {clusters_removed} протухших "
                            f"записей кэша очагов"
                        )
                except Exception as ce:
                    logger.warning(f"Cleanup clusters_cache error: {ce}")

                # Этап 5: чистим протухшие Excel-файлы в excel_cache.
                # Записи с expires_at < NOW() игнорируются при SELECT,
                # но физически занимают место (1-2 MB каждая) — удаляем.
                try:
                    from miniapp.backend.db.excel_cache import (
                        cleanup_old_excel,
                    )
                    excel_removed = await cleanup_old_excel()
                    if excel_removed > 0:
                        logger.info(
                            f"Cleanup: удалено {excel_removed} протухших "
                            f"записей кэша Excel"
                        )
                except Exception as ce:
                    logger.warning(f"Cleanup excel_cache error: {ce}")

                # Sprint 2: чистим протухшие LLM-summary в llm_cache.
                # Записи с expires_at < NOW() игнорируются при SELECT,
                # но физически занимают место (5-10 KB каждая) — удаляем.
                try:
                    from miniapp.backend.db.llm_cache import (
                        cleanup_expired_llm_cache,
                    )
                    llm_removed = await cleanup_expired_llm_cache()
                    if llm_removed > 0:
                        logger.info(
                            f"Cleanup: удалено {llm_removed} протухших "
                            f"записей кэша LLM-summary"
                        )
                except Exception as ce:
                    logger.warning(f"Cleanup llm_cache error: {ce}")
            except asyncio.CancelledError:
                logger.info("Cleanup loop cancelled")
                break
            except Exception as exc:
                # Не роняем цикл при случайной ошибке
                logger.warning(f"Cleanup loop error: {exc}")
                await asyncio.sleep(60)

    cleanup_task = asyncio.create_task(_cleanup_loop())
    logger.info("Запущена фоновая очистка старых задач (каждые 2 часа)")

    yield

    # === Graceful shutdown ===
    logger.info("Останавливаемся...")

    # Останавливаем фоновую очистку
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    # Закрываем пул PostgreSQL
    try:
        await db_close_pool()
    except Exception as exc:
        logger.warning(f"Ошибка при закрытии пула БД: {exc}")

    if tg_app:
        try:
            # updater не запускали (нет start_webhook) — только stop+shutdown
            await tg_app.stop()
            await tg_app.shutdown()
            logger.info("Telegram-бот остановлен")
        except Exception as exc:
            logger.error(f"Ошибка при остановке бота: {exc}")

        # Закрываем HTTP-клиенты gibdd-bot
        try:
            from api_client import close_client
            await close_client()
        except Exception:
            pass
        try:
            from llm_analyzer import close_llm_client
            await close_llm_client()
        except Exception:
            pass

    logger.info("Сервер остановлен")


# ============================================================
# FastAPI приложение
# ============================================================
app = FastAPI(
    title="GIBDD Stat Bot + Mini App",
    description=(
        "Telegram-бот + Mini App для выгрузки и анализа данных ДТП "
        "из открытых данных ГИБДД (stat.gibdd.ru)."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=miniapp_settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Task-Id"],
)

# === Фаза 1.5: Rate limiting middleware ===
# Применяем лимит 60 req/min к /api/* (кроме exempt-эндпоинтов).
# Webhook /bot/webhook и /health* — не лимитируются.
#
# ⚠️ Sprint 4 FIX: используем PURE ASGI middleware вместо
# `app.middleware("http")(rate_limit_middleware)`.
# Starlette BaseHTTPMiddleware БУФЕРИЗУЕТ streaming responses (SSE/WebSocket),
# что ломает Sprint 4 streaming LLM — chunks доходят до клиента только
# после завершения стрима целиком. Pure ASGI middleware не трогает response
# body и пропускает SSE-стриминг без буферизации.
# Подробнее: https://github.com/encode/starlette/issues/919
from miniapp.backend.middleware.rate_limit import RateLimitASGIMiddleware
app.add_middleware(RateLimitASGIMiddleware)

# === Фаза 1.6: Prometheus metrics ===
# /metrics endpoint для скрапирования Prometheus.
# Метрики: http_requests_total, http_request_duration_seconds,
# gibdd_tasks_total, gibdd_tasks_in_progress, gibdd_cache_hits_total и др.
from miniapp.backend.middleware.metrics import setup_metrics
setup_metrics(app)

# Монтируем все роутеры Mini App под /api
app.mount("/api", miniapp_app)


# ============================================================
# Sprint 4: диагностическое логирование SSE-эндпоинтов
# Выводит при старте, зарегистрированы ли /stream маршруты,
# чтобы сразу видеть на проде, попал ли Sprint 4 в образ.
# ============================================================
try:
    _sse_routes = []
    for _route in miniapp_app.routes:
        _path = getattr(_route, "path", "")
        if "/stream" in _path and "/llm/" in _path:
            _methods = ",".join(sorted(getattr(_route, "methods", set()) or set()))
            _sse_routes.append(f"{_methods} {_path}")
    if _sse_routes:
        logger.info(f"Sprint 4: SSE endpoints registered ({len(_sse_routes)}):")
        for _r in _sse_routes:
            logger.info(f"  SSE: {_r}")
    else:
        logger.warning(
            "Sprint 4: SSE endpoints NOT registered! "
            "Проверьте miniapp/backend/routers/llm.py и sse-starlette в requirements.txt"
        )
except Exception as _e:
    logger.warning(f"Sprint 4: SSE diagnostic failed: {_e}")


# ============================================================
# Webhook для Telegram
# ============================================================
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    """Принимает updates от Telegram и передаёт в python-telegram-bot."""
    if tg_app is None:
        raise HTTPException(
            status_code=503,
            detail="Telegram bot not initialized",
        )

    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON: {exc}",
        )

    try:
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as exc:
        logger.exception(f"Ошибка обработки update: {exc}")
        # Возвращаем 200, чтобы Telegram не ретраил бесконечно
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)

    return JSONResponse({"ok": True})


@app.get(WEBHOOK_PATH)
async def telegram_webhook_info():
    """
    Диагностический GET на /bot/webhook.
    Telegram шлёт POST, но GET нужен, чтобы в браузере проверить, что
    маршрут действительно живёт в нашем FastAPI (а не отдаётся 404 от Traefik).
    """
    return {
        "ok": True,
        "service": "gibdd-bot-miniapp",
        "webhook_path": WEBHOOK_PATH,
        "webhook_url": WEBHOOK_URL,
        "bot_initialized": tg_app is not None,
        "bothost_domain": BOTHOST_DOMAIN or "not_set",
        "port": PORT,
        "hint": (
            "Если вы видите этот JSON — FastAPI работает и маршрут /bot/webhook "
            "существует. Telegram должен слать POST сюда. Если вместо этого "
            "вы видите '404 page not found' (plain text) — запрос не доходит "
            "до контейнера: проверьте опцию 'Использовать домен' в bothost."
        ),
    }


# ============================================================
# Health check
# ============================================================
@app.get("/health")
async def health():
    """Health-check для bothost / Docker / мониторинга."""
    return {
        "status": "ok",
        "service": "gibdd-bot-miniapp",
        "version": "1.0.0",
        "telegram_bot": "running" if tg_app else "stopped",
        "bothost_domain": BOTHOST_DOMAIN or "not_set",
        "database": "ready" if db_is_ready() else "fallback (in-memory)",
    }


@app.get("/health/db")
async def health_db():
    """Детальный health-check пула PostgreSQL (для диагностики)."""
    from miniapp.backend.db.connection import health_check as db_health_check
    return await db_health_check()


@app.get("/health/db/cards")
async def health_db_cards():
    """
    Статистика кэша карточек ДТП в PostgreSQL (Этап 3).

    Возвращает:
    - configured / ready — состояние БД
    - total_entries — всего записей в dtp_cards_cache (включая протухшие)
    - valid_entries — валидных записей (expires_at > NOW())
    - total_cards_cached — суммарное количество ДТП в валидных записях
    - regions_cached — сколько регионов имеют валидные записи
    - oldest_expiry / newest_expiry — диапазон TTL
    - top_regions — топ-5 регионов по размеру кэша
    """
    from miniapp.backend.db.cards_cache import get_cache_stats
    return await get_cache_stats()


@app.get("/health/db/clusters")
async def health_db_clusters():
    """
    Статистика кэша очагов концентрации ДТП в PostgreSQL (Этап 4).

    Возвращает:
    - configured / ready — состояние БД
    - total_entries — всего записей в clusters_cache (включая протухшие)
    - valid_entries — валидных записей (expires_at > NOW())
    - total_clusters_cached — суммарное количество очагов в валидных записях
    - total_preclusters_cached — суммарное количество предочагов
    - entries_with_prev — сколько записей используют АППГ-сравнение
    - regions_cached — сколько регионов имеют валидные записи
    - oldest_expiry / newest_expiry — диапазон TTL
    - top_regions — топ-5 регионов по размеру кэша
    """
    from miniapp.backend.db.clusters_cache import get_cache_stats
    return await get_cache_stats()


@app.get("/health/db/excel")
async def health_db_excel():
    """
    Статистика кэша готовых Excel-файлов в PostgreSQL (Этап 5).

    Возвращает:
    - configured / ready — состояние БД
    - total_entries — всего записей в excel_cache (включая протухшие)
    - valid_entries — валидных записей (expires_at > NOW())
    - total_dtp_cached — суммарное количество ДТП в валидных записях
    - total_bytes / total_mb — суммарный размер байтов в кэше
    - regions_cached — сколько регионов имеют валидные записи
    - oldest_expiry / newest_expiry — диапазон TTL
    - top_regions — топ-5 регионов по размеру кэша (с разбивкой по МБ)
    """
    from miniapp.backend.db.excel_cache import get_cache_stats
    return await get_cache_stats()


@app.get("/")
async def root():
    """Корневой endpoint с информацией о сервисе."""
    return {
        "name": "GIBDD Stat Bot + Mini App",
        "docs": "/docs",
        "health": "/health",
        "miniapp": "/app/" if FRONTEND_DIST.exists() else "frontend not built",
        "telegram_webhook": WEBHOOK_PATH,
    }


# ============================================================
# Mini App frontend (статика)
# ============================================================
if FRONTEND_DIST.exists():
    app.mount(
        "/app",
        StaticFiles(directory=str(FRONTEND_DIST), html=True),
        name="frontend",
    )
    logger.info(f"Frontend раздаётся из {FRONTEND_DIST}")

    # No-cache middleware для index.html — иначе Telegram WebView
    # кеширует HTML навсегда и не подхватывает новый JS-бандл при деплое.
    # Assets (с хешированными именами типа index-Dwtow6gx.js) кешируются
    # агрессивно — это безопасно, т.к. Vite меняет имя файла при любой правке.
    #
    # ⚠️ Sprint 4 FIX: pure ASGI middleware вместо `@app.middleware("http")`.
    # BaseHTTPMiddleware буферизует streaming responses (SSE/WebSocket).
    # Pure ASGI перехватывает send() и добавляет заголовки только для
    # http.response.start message — НЕ трогает body chunks, стриминг идёт
    # напрямую клиенту.
    class NoCacheIndexHTMLASGIMiddleware:
        """Pure ASGI: добавляет no-cache заголовки только для index.html.

        Не буферизует streaming responses (SSE/WebSocket).
        Перехватывает http.response.start message и добавляет заголовки
        ДО того, как body chunks начнут отправляться клиенту.
        """
        _TARGET_PATHS = frozenset({"/app", "/app/", "/app/index.html"})

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http":
                await self.app(scope, receive, send)
                return

            path = scope.get("path", "")
            if path not in self._TARGET_PATHS:
                # Не наш путь — пропускаем напрямую
                await self.app(scope, receive, send)
                return

            # Перехватываем send, чтобы добавить заголовки в response.start
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append([b"cache-control", b"no-cache, no-store, must-revalidate"])
                    headers.append([b"pragma", b"no-cache"])
                    headers.append([b"expires", b"0"])
                    message["headers"] = headers
                await send(message)

            await self.app(scope, receive, send_wrapper)

    app.add_middleware(NoCacheIndexHTMLASGIMiddleware)
else:
    logger.warning(
        f"Frontend не собран ({FRONTEND_DIST} не существует). "
        f"Запустите `cd miniapp/frontend && npm install && npm run build`"
    )

    @app.get("/app")
    async def frontend_not_built():
        return HTMLResponse(
            "<h1>Frontend не собран</h1>"
            "<p>Выполните:</p>"
            "<pre>cd miniapp/frontend\nnpm install\nnpm run build</pre>",
            status_code=503,
        )


# ============================================================
# Точка входа для запуска напрямую (python main.py)
# ============================================================
if __name__ == "__main__":
    import uvicorn

    logger.info(f"=== GIBDD Bot + Mini App запускается на порту {PORT} ===")
    if BOTHOST_DOMAIN:
        logger.info(
            f"BOTHOST_DOMAIN: {BOTHOST_DOMAIN} | "
            f"webhook URL: {WEBHOOK_URL} | "
            f"Mini App: /app/"
        )
    else:
        logger.warning(
            "BOTHOST_DOMAIN не задан — Telegram webhook и Mini App работать не будут. "
            "Укажите BOTHOST_DOMAIN (без https://) в .env"
        )
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        workers=1,  # на bothost один процесс
        log_level=LOG_LEVEL.lower(),
        access_log=True,
    )
