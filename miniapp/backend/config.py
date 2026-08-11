"""
Конфигурация Mini App backend.

Подгружает существующие переменные окружения gibdd-bot (TELEGRAM_BOT_TOKEN,
LLM_API_KEY, TARGET_API_TIMEOUT и т.д.) через существующий config.py
и добавляет свои для веб-слоя.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import field_validator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Централизованные настройки приложения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === Telegram ===
    telegram_bot_token: str = Field(
        default="", description="Токен бота от @BotFather"
    )
    allowed_user_ids: str = Field(
        default="",
        description="Список Telegram user IDs через запятую (пусто = всем)",
    )

    # === Web ===
    cors_origins: str = (
        "http://localhost:5173,https://web.telegram.org,"
        "https://a.telegram.org,https://bot*.bothost.tech"
    )
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    app_workers: int = 1

    # === LLM (пробрасывается в llm_analyzer) ===
    llm_api_key: str = ""
    llm_model: str = "glm-4.7-flash"
    enable_news_search: bool = True

    # === GIBDD API ===
    target_api_timeout: int = 120
    log_level: str = "INFO"

    # === Пути ===
    camera_data_dir: str = "data"
    tasks_dir: str = ""  # вычисляется автоматически

    # === Bothost ===
    bothost_domain: str = Field(
        default="",
        description="Домен вида bot1234.bothost.tech, который выдал bothost",
    )

    # === Phase 2: Scalability settings ===
    max_concurrent_tasks: int = Field(
        default=5,
        description=(
            "Максимум одновременных execute_task() (Semaphore). "
            "3 для 2-10 пользователей, 5 для 10-30, 8 для 30+."
        ),
    )
    max_inmemory_tasks: int = Field(
        default=50,
        description=(
            "Размер in-memory LRU _tasks (50 = ~400 MB максимум RAM)."
        ),
    )
    rate_limit_per_minute: int = Field(
        default=60,
        description="Лимит запросов в минуту на пользователя (slowapi).",
    )
    log_format: str = Field(
        default="text",
        description="Формат логов: text или json (для Loki/ELK).",
    )

    # === Database (опционально) ===
    database_url: str = Field(
        default="",
        description=(
            "Connection string PostgreSQL. Если задан — задачи и аудит-лог "
            "персистятся в БД; иначе работает in-memory fallback."
        ),
    )
    db_pool_min: int = Field(default=2, description="Минимальный размер пула")
    db_pool_max: int = Field(
        default=30,
        description=(
            "Максимальный размер пула соединений PostgreSQL. "
            "Раньше было 5 (Phase 1: 15) — критично мало при 10+ "
            "одновременных пользователях: long-poll эндпоинты держат "
            "соединение по 25-60 сек. Для 10 пользователей — 15, "
            "для 30 — 30 (Phase 2 default), для 50+ — 40-50."
        ),
    )
    db_connect_timeout: int = Field(
        default=30, description="Таймаут подключения (сек)"
    )

    @property
    def db_enabled(self) -> bool:
        """True если DATABASE_URL задан и пул должен быть создан."""
        return bool(self.database_url.strip())

    # === Вычисляемые ===
    @property
    def allowed_user_ids_list(self) -> List[int]:
        if not self.allowed_user_ids.strip():
            return []
        return [
            int(uid.strip())
            for uid in self.allowed_user_ids.split(",")
            if uid.strip().isdigit()
        ]

    @property
    def cors_origins_list(self) -> List[str]:
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def gibdd_root(self) -> Path:
        """
        Абсолютный путь к корню существующего проекта gibdd-bot.
        miniapp/backend/config.py → gibdd-bot/
        """
        return Path(__file__).resolve().parents[3]

    @property
    def tasks_path(self) -> Path:
        """Директория для временных файлов задач (всегда в data/tasks/)."""
        if self.tasks_dir:
            return Path(self.tasks_dir)
        return self.gibdd_root / "data" / "tasks"

    @property
    def webhook_url(self) -> str:
        """URL webhook для Telegram (на основе bothost_domain)."""
        if not self.bothost_domain:
            return ""
        return f"https://{self.bothost_domain}/bot/webhook"

    @field_validator("telegram_bot_token")
    @classmethod
    def _validate_token(cls, v: str) -> str:
        # В Mini App токен обязателен только если включена авторизация
        # через initData. При разработке можно оставить пустым.
        if v and ":" not in v:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN должен быть в формате '123456:ABC-DEF'"
            )
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton-доступ к настройкам (кешируется на уровне процесса)."""
    # Загружаем .env из корня gibdd-bot
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if env_path.exists():
        os.environ.setdefault("PYTHONPATH", str(env_path.parent))

    return Settings()


# Глобальный экземпляр для удобного импорта
settings = get_settings()
