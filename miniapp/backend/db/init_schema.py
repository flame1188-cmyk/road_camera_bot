"""
Инициализация схемы БД (CREATE TABLE IF NOT EXISTS).

Запуск вручную (для проверки):
    cd /home/z/my-project/gibdd-bot
    python -m miniapp.backend.db.init_schema

При обычном запуске приложения схема применяется автоматически
в init_pool() (см. connection.py).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Добавляем корень gibdd-bot в sys.path (для прямого запуска)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from miniapp.backend.db.connection import init_pool, close_pool, health_check, is_db_ready

logger = logging.getLogger(__name__)


async def main() -> int:
    """Создаёт пул, применяет схему, выводит health-check."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("=== init_schema: старт ===")
    ready = await init_pool()
    if not ready:
        logger.error(
            "Не удалось подключиться к БД. "
            "Проверьте DATABASE_URL в miniapp/backend/.env"
        )
        return 1

    # Дополнительная проверка через health_check
    health = await health_check()
    logger.info(f"Health-check: {health}")

    await close_pool()
    logger.info("=== init_schema: готово ===")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
