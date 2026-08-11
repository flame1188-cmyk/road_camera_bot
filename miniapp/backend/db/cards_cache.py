"""
Кэш карточек ДТП в PostgreSQL (Этап 3).

Заменяет in-memory LRU из data_cache.py на персистентное SQL-хранилище:
- Переживает рестарт приложения (карточки не нужно заново скачивать).
- Разделяется между всеми воркерами (APP_WORKERS=2 — больше не проблема).
- Удаляется автоматически по TTL (expires_at < NOW()).

Дизайн:
- Если PostgreSQL готов (is_db_ready() == True) — операции идут в БД,
  in-memory LRU из data_cache.py используется как L2-кэш для ускорения
  повторных обращений внутри одного процесса.
- Если PostgreSQL НЕ готов — операции идут только in-memory,
  поведение идентично тому, что было до Этапа 3.

Ключ кэша: (reg_code, dat_hash) где dat_hash = MD5 от
отсортированного списка "m.YYYY" дат, склеенных через ','.
Сортировка гарантирует, что ["1.2026","2.2026"] и ["2.2026","1.2026"]
дадут одинаковый хэш.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, List, Optional, Tuple

from psycopg.types.json import Json

from .connection import get_pool, is_db_ready

logger = logging.getLogger(__name__)

# TTL берётся из env CARDS_CACHE_TTL_SECONDS (по умолчанию 3600 = 1 час).
# См. config.py → раздел «PostgreSQL-кэш (Этап 3+)».
try:
    from config import CARDS_CACHE_TTL_SECONDS
    DEFAULT_TTL_SECONDS = CARDS_CACHE_TTL_SECONDS
except Exception:
    # На случай если config.py недоступен (например, в изолированном тесте)
    DEFAULT_TTL_SECONDS = 3600

logger.info(
    f"cards_cache: TTL={DEFAULT_TTL_SECONDS}s "
    f"(env CARDS_CACHE_TTL_SECONDS)"
)


# ====================================================================
# Хэширование ключа
# ====================================================================
def _make_dat_hash(dat_list: List[str]) -> str:
    """
    Вычисляет MD5-хэш от отсортированного списка дат.

    Сортировка гарантирует стабильный ключ независимо от порядка месяцев.
    Пример: ["1.2026", "2.2026"] → MD5("1.2026,2.2026")
            ["2.2026", "1.2026"] → MD5("1.2026,2.2026")  (та же запись)
    """
    sorted_dats = sorted(dat_list)
    raw = ",".join(sorted_dats)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ====================================================================
# GET — чтение из кэша
# ====================================================================
async def get_cached_cards(
    reg_code: str,
    dat_list: List[str],
) -> Optional[Tuple[List[dict], List[str]]]:
    """
    Возвращает (cards, errors) из БД или None, если записи нет / протухла.

    Дополнительно кладёт результат в in-memory LRU из data_cache.py —
    повторные вызовы в том же процессе будут идти без SQL-запроса.
    """
    if not dat_list:
        return None

    if not is_db_ready():
        return None  # fallback на in-memory в caller'е

    pool = get_pool()
    if pool is None:
        return None

    dat_hash = _make_dat_hash(dat_list)

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT payload, errors, total_cards, expires_at
                FROM dtp_cards_cache
                WHERE reg_code = %(reg)s
                  AND dat_hash = %(hash)s
                  AND expires_at > NOW()
                """,
                params={"reg": reg_code, "hash": dat_hash},
                prepare=False,
            )
            row = await cur.fetchone()

        if row is None:
            # === Фаза 1.6: Prometheus metric — MISS ===
            try:
                from ..middleware.metrics import record_cache_miss
                record_cache_miss("cards")
            except Exception:
                pass
            return None

        cards = list(row["payload"]) if row["payload"] else []
        errors = list(row["errors"]) if row["errors"] else []

        # L2-кэш in-memory для ускорения повторных обращений в этом процессе
        try:
            # Импортируем here чтобы избежать циклической зависимости на старте
            from data_cache import data_cache as _memory_cache
            _memory_cache.put(reg_code, dat_list, cards, errors)
        except Exception:
            pass  # in-memory L2 — nice-to-have, не обязательно

        logger.debug(
            f"cards_cache: HIT reg={reg_code} hash={dat_hash[:8]}.. "
            f"({len(cards)} ДТП)"
        )
        # === Фаза 1.6: Prometheus metric — HIT ===
        try:
            from ..middleware.metrics import record_cache_hit
            record_cache_hit("cards")
        except Exception:
            pass
        return cards, errors

    except Exception as exc:
        logger.warning(
            f"cards_cache: get_cached_cards failed (reg={reg_code}): {exc}"
        )
        return None


# ====================================================================
# PUT — сохранение в кэш
# ====================================================================
async def put_cached_cards(
    reg_code: str,
    dat_list: List[str],
    cards: List[dict],
    errors: List[str],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    source: str = "api",
) -> None:
    """
    Сохраняет карточки в БД (upsert: INSERT ... ON CONFLICT DO UPDATE).

    Тоже обновляет in-memory LRU из data_cache.py для консистентности.
    """
    if not dat_list or not cards:
        return

    # In-memory LRU обновляем ВСЕГДА — даже если БД недоступна,
    # чтобы within-process кэширование работало.
    try:
        from data_cache import data_cache as _memory_cache
        _memory_cache.put(reg_code, dat_list, cards, errors)
    except Exception:
        pass

    if not is_db_ready():
        return  # в БД не пишем, но в in-memory уже положили

    pool = get_pool()
    if pool is None:
        return

    dat_hash = _make_dat_hash(dat_list)

    try:
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO dtp_cards_cache (
                    reg_code, dat_hash, dat_list, payload, errors,
                    total_cards, source, created_at, expires_at
                ) VALUES (
                    %(reg)s, %(hash)s, %(dat_list)s, %(payload)s, %(errors)s,
                    %(total)s, %(source)s, NOW(),
                    NOW() + (%(ttl)s || ' seconds')::INTERVAL
                )
                ON CONFLICT (reg_code, dat_hash) DO UPDATE SET
                    dat_list = EXCLUDED.dat_list,
                    payload = EXCLUDED.payload,
                    errors = EXCLUDED.errors,
                    total_cards = EXCLUDED.total_cards,
                    source = EXCLUDED.source,
                    created_at = NOW(),
                    expires_at = NOW() + (%(ttl)s || ' seconds')::INTERVAL
                """,
                params={
                    "reg": reg_code,
                    "hash": dat_hash,
                    "dat_list": Json(dat_list),
                    "payload": Json(cards),
                    "errors": Json(errors),
                    "total": len(cards),
                    "source": source,
                    "ttl": str(ttl_seconds),
                },
            )
            await conn.commit()

        logger.debug(
            f"cards_cache: PUT reg={reg_code} hash={dat_hash[:8]}.. "
            f"({len(cards)} ДТП, TTL={ttl_seconds}s, source={source})"
        )

    except Exception as exc:
        logger.warning(
            f"cards_cache: put_cached_cards failed (reg={reg_code}): {exc}"
        )


# ====================================================================
# INVALIDATE BY REGION
# ====================================================================
async def invalidate_region(reg_code: str) -> int:
    """
    Удаляет ВСЕ записи кэша для заданного региона (из БД + in-memory).

    Возвращает количество удалённых строк из БД.
    """
    # Сначала in-memory — быстрый путь, не зависит от БД
    memory_removed = 0
    try:
        from data_cache import data_cache as _memory_cache
        memory_removed = _memory_cache.invalidate_by_region(reg_code)
    except Exception:
        pass

    if not is_db_ready():
        return memory_removed

    pool = get_pool()
    if pool is None:
        return memory_removed

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM dtp_cards_cache WHERE reg_code = %(reg)s",
                params={"reg": reg_code},
                prepare=False,
            )
            await conn.commit()
            db_removed = cur.rowcount or 0

        if db_removed or memory_removed:
            logger.info(
                f"cards_cache: invalidate_region({reg_code}) — "
                f"DB={db_removed}, memory={memory_removed}"
            )
        return max(db_removed, memory_removed)

    except Exception as exc:
        logger.warning(
            f"cards_cache: invalidate_region failed (reg={reg_code}): {exc}"
        )
        return memory_removed


# ====================================================================
# CLEANUP OLD — удаление протухших записей
# ====================================================================
async def cleanup_old_cards() -> int:
    """
    Физически удаляет протухшие записи (expires_at < NOW()).

    Вызывается из background-задачи (см. main.py _cleanup_loop —
    расширить, либо создать отдельный цикл для cards_cache).
    """
    if not is_db_ready():
        return 0

    pool = get_pool()
    if pool is None:
        return 0

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM dtp_cards_cache WHERE expires_at < NOW()",
                prepare=False,
            )
            await conn.commit()
            removed = cur.rowcount or 0

        if removed > 0:
            logger.info(
                f"cards_cache: cleanup_old_cards — удалено {removed} "
                f"протухших записей"
            )
        return removed

    except Exception as exc:
        logger.warning(f"cards_cache: cleanup_old_cards failed: {exc}")
        return 0


# ====================================================================
# STATS — для диагностики (/health/db/cards)
# ====================================================================
async def get_cache_stats() -> dict:
    """
    Возвращает статистику кэша для диагностики.
    """
    if not is_db_ready():
        return {
            "configured": False,
            "ready": False,
            "reason": "DATABASE_URL not set or pool not ready",
        }

    pool = get_pool()
    if pool is None:
        return {
            "configured": True,
            "ready": False,
            "reason": "pool is None",
        }

    try:
        async with pool.connection() as conn:
            # Общая статистика
            cur = await conn.execute(
                """
                SELECT
                    COUNT(*) AS total_entries,
                    COUNT(*) FILTER (WHERE expires_at > NOW()) AS valid_entries,
                    COALESCE(SUM(total_cards) FILTER (WHERE expires_at > NOW()), 0) AS total_cards_cached,
                    COUNT(DISTINCT reg_code) FILTER (WHERE expires_at > NOW()) AS regions_cached,
                    MIN(expires_at) FILTER (WHERE expires_at > NOW()) AS oldest_expiry,
                    MAX(expires_at) FILTER (WHERE expires_at > NOW()) AS newest_expiry
                FROM dtp_cards_cache
                """,
                prepare=False,
            )
            row = await cur.fetchone()

            # Top-5 регионов по размеру кэша (для диагностики)
            cur = await conn.execute(
                """
                SELECT reg_code, COUNT(*) AS entries, SUM(total_cards) AS cards
                FROM dtp_cards_cache
                WHERE expires_at > NOW()
                GROUP BY reg_code
                ORDER BY cards DESC NULLS LAST
                LIMIT 5
                """,
                prepare=False,
            )
            top_regions = await cur.fetchall()

        return {
            "configured": True,
            "ready": True,
            "total_entries": row["total_entries"] if row else 0,
            "valid_entries": row["valid_entries"] if row else 0,
            "total_cards_cached": row["total_cards_cached"] if row else 0,
            "regions_cached": row["regions_cached"] if row else 0,
            "oldest_expiry": row["oldest_expiry"].isoformat()
            if row and row["oldest_expiry"]
            else None,
            "newest_expiry": row["newest_expiry"].isoformat()
            if row and row["newest_expiry"]
            else None,
            "top_regions": [
                {
                    "reg_code": r["reg_code"],
                    "entries": r["entries"],
                    "cards": r["cards"],
                }
                for r in (top_regions or [])
            ],
        }

    except Exception as exc:
        return {
            "configured": True,
            "ready": False,
            "reason": f"stats query failed: {exc}",
        }
