"""
Кэш готовых Excel-файлов в PostgreSQL (Этап 5).

Хранит байты двух файлов (Файл 1 «ДТП» + Файл 2 «Участники»),
чтобы повторные запросы по тому же региону+периоду не пересчитывали
5-8 секунд excel_generator.generate_both_files().

Зачем:
  Excel — производное от cards (через gibdd_parser.build_file1_data +
  build_file2_data + excel_generator.generate_both_files). Расчёт идёт
  5-8 секунд на каждом запросе. Если у двух пользователей один и тот же
  регион+период — второй сейчас ждёт зря, файлы побайтово идентичны.

  Кэш позволяет переиспользовать готовые байты между:
  - разными пользователями
  - разными сессиями одного пользователя
  - перезапусками приложения

Ключ кэша:
  (reg_code, dat_hash) — СОВПАДАЕТ с ключом dtp_cards_cache.
  Это не случайно: Excel — производное от cards, поэтому одинаковый
  ключ гарантирует консистентность. dat_hash = MD5 от отсортированного
  списка "m.YYYY" дат.

Что кэшируется:
  - file1_bytes — Файл 1 «ДТП» (XLSX, ~500 KB)
  - file2_bytes — Файл 2 «Участники» (XLSX, ~1 MB)
  - Метаданные (total_dtp, total_dead, total_injured, region_name,
    period_label) для диагностики в /health/db/excel.

TTL:
  По умолчанию 24 часа (86400 сек) — совпадает с рекомендуемым TTL
  cards для закрытых периодов. Настраивается через env
  EXCEL_CACHE_TTL_SECONDS.

  Excel валиден до тех пор, пока валидны cards, из которых он
  сгенерирован. Если CARDS_CACHE_TTL_SECONDS < EXCEL_CACHE_TTL_SECONDS,
  возможна ситуация: cards протухли, excel ещё нет. Это безопасно —
  excel_bytes остаются корректными (они уже сгенерированы из валидных
  cards), а при cache miss cards перечитаются и excel_cache обновится
  автоматически.

Fallback:
  Если БД недоступна — все операции no-op, Excel генерируется как раньше
  (без кэша, 5-8 сек).
"""
from __future__ import annotations

import hashlib
import logging
from typing import List, Optional, Tuple

from psycopg.types.json import Json

from .connection import get_pool, is_db_ready

logger = logging.getLogger(__name__)

# TTL берётся из env EXCEL_CACHE_TTL_SECONDS (по умолчанию 86400 = 24 часа).
# См. config.py → раздел «PostgreSQL-кэш (Этап 3+)».
try:
    from config import EXCEL_CACHE_TTL_SECONDS
    DEFAULT_TTL_SECONDS = EXCEL_CACHE_TTL_SECONDS
except Exception:
    DEFAULT_TTL_SECONDS = 86400

logger.info(
    f"excel_cache: TTL={DEFAULT_TTL_SECONDS}s "
    f"(env EXCEL_CACHE_TTL_SECONDS)"
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

    ВНИМАНИЕ: эта же функция используется в cards_cache.py — если менять
    алгоритм, менять везде одновременно (иначе ключи разъедутся).
    """
    if not dat_list:
        return ""
    sorted_dats = sorted(dat_list)
    raw = ",".join(sorted_dats)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ====================================================================
# GET — чтение из кэша
# ====================================================================
async def get_cached_excel(
    reg_code: str,
    dat_list: List[str],
) -> Optional[Tuple[bytes, bytes, dict]]:
    """
    Возвращает (file1_bytes, file2_bytes, metadata) из БД или None,
    если записи нет / протухла.

    metadata — словарь с полями:
        total_dtp, total_dead, total_injured, region_name, period_label
    (нужны только для логов/диагностики; bytes — основная ценность).
    """
    if not dat_list:
        return None

    if not is_db_ready():
        return None

    pool = get_pool()
    if pool is None:
        return None

    dat_hash = _make_dat_hash(dat_list)

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT file1_bytes, file2_bytes,
                       file1_size, file2_size,
                       total_dtp, total_dead, total_injured,
                       region_name, period_label
                FROM excel_cache
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
                record_cache_miss("excel")
            except Exception:
                pass
            return None

        file1_bytes = bytes(row["file1_bytes"])
        file2_bytes = bytes(row["file2_bytes"])
        if not file1_bytes or not file2_bytes:
            # Защита от битых записей — вряд ли возможно, но проверим
            logger.warning(
                f"excel_cache: HIT reg={reg_code} hash={dat_hash[:8]}.. "
                f"but bytes empty — skipping"
            )
            return None

        metadata = {
            "total_dtp": row["total_dtp"],
            "total_dead": row["total_dead"],
            "total_injured": row["total_injured"],
            "region_name": row["region_name"],
            "period_label": row["period_label"],
            "file1_size": row["file1_size"],
            "file2_size": row["file2_size"],
        }

        total_kb = (len(file1_bytes) + len(file2_bytes)) // 1024
        logger.info(
            f"excel_cache: HIT reg={reg_code} hash={dat_hash[:8]}.. "
            f"(Файл 1={row['file1_size']} байт, "
            f"Файл 2={row['file2_size']} байт, всего ~{total_kb} KB, "
            f"{row['total_dtp']} ДТП)"
        )
        # === Фаза 1.6: Prometheus metric ===
        try:
            from ..middleware.metrics import record_cache_hit
            record_cache_hit("excel")
        except Exception:
            pass
        return file1_bytes, file2_bytes, metadata

    except Exception as exc:
        logger.warning(
            f"excel_cache: get_cached_excel failed (reg={reg_code}): {exc}"
        )
        return None


# ====================================================================
# PUT — сохранение в кэш
# ====================================================================
async def put_cached_excel(
    reg_code: str,
    dat_list: List[str],
    file1_bytes: bytes,
    file2_bytes: bytes,
    total_dtp: int = 0,
    total_dead: int = 0,
    total_injured: int = 0,
    region_name: str = "",
    period_label: str = "",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    """
    Сохраняет file1_bytes + file2_bytes в БД (upsert).

    Параметры:
      reg_code, dat_list  — ключ кэша (тот же, что у dtp_cards_cache).
      file1_bytes         — Файл 1 «ДТП» (XLSX).
      file2_bytes         — Файл 2 «Участники» (XLSX).
      total_dtp/dead/injured — для диагностики в /health/db/excel.
      region_name, period_label — для диагностики.
      ttl_seconds         — срок жизни записи.
    """
    if not dat_list or not file1_bytes or not file2_bytes:
        return

    if not is_db_ready():
        return

    pool = get_pool()
    if pool is None:
        return

    dat_hash = _make_dat_hash(dat_list)

    try:
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO excel_cache (
                    reg_code, dat_hash, dat_list,
                    file1_bytes, file2_bytes, file1_size, file2_size,
                    total_dtp, total_dead, total_injured,
                    region_name, period_label,
                    created_at, expires_at
                ) VALUES (
                    %(reg)s, %(hash)s, %(dat_list)s,
                    %(f1)s, %(f2)s, %(f1_size)s, %(f2_size)s,
                    %(td)s, %(tdd)s, %(ti)s,
                    %(rn)s, %(pl)s,
                    NOW(),
                    NOW() + (%(ttl)s || ' seconds')::INTERVAL
                )
                ON CONFLICT (reg_code, dat_hash) DO UPDATE SET
                    dat_list = EXCLUDED.dat_list,
                    file1_bytes = EXCLUDED.file1_bytes,
                    file2_bytes = EXCLUDED.file2_bytes,
                    file1_size = EXCLUDED.file1_size,
                    file2_size = EXCLUDED.file2_size,
                    total_dtp = EXCLUDED.total_dtp,
                    total_dead = EXCLUDED.total_dead,
                    total_injured = EXCLUDED.total_injured,
                    region_name = EXCLUDED.region_name,
                    period_label = EXCLUDED.period_label,
                    created_at = NOW(),
                    expires_at = NOW() + (%(ttl)s || ' seconds')::INTERVAL
                """,
                params={
                    "reg": reg_code,
                    "hash": dat_hash,
                    "dat_list": Json(dat_list),
                    "f1": file1_bytes,
                    "f2": file2_bytes,
                    "f1_size": len(file1_bytes),
                    "f2_size": len(file2_bytes),
                    "td": total_dtp,
                    "tdd": total_dead,
                    "ti": total_injured,
                    "rn": region_name,
                    "pl": period_label,
                    "ttl": str(ttl_seconds),
                },
            )
            await conn.commit()

        total_kb = (len(file1_bytes) + len(file2_bytes)) // 1024
        logger.info(
            f"excel_cache: PUT reg={reg_code} hash={dat_hash[:8]}.. "
            f"(Файл 1={len(file1_bytes)} байт, "
            f"Файл 2={len(file2_bytes)} байт, всего ~{total_kb} KB, "
            f"{total_dtp} ДТП, TTL={ttl_seconds}s)"
        )

    except Exception as exc:
        logger.warning(
            f"excel_cache: put_cached_excel failed (reg={reg_code}): {exc}"
        )


# ====================================================================
# INVALIDATE BY REGION
# ====================================================================
async def invalidate_region(reg_code: str) -> int:
    """
    Удаляет ВСЕ записи кэша Excel для заданного региона.

    Используется когда данные ГИБДД по региону обновились и нужно
    форсировать перегенерацию. Также вызывается из cards_cache.
    invalidate_region() для согласованности cards ↔ excel.

    Возвращает количество удалённых строк.
    """
    if not is_db_ready():
        return 0

    pool = get_pool()
    if pool is None:
        return 0

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM excel_cache WHERE reg_code = %(reg)s",
                params={"reg": reg_code},
                prepare=False,
            )
            await conn.commit()
            removed = cur.rowcount or 0

        if removed > 0:
            logger.info(
                f"excel_cache: invalidate_region({reg_code}) — "
                f"удалено {removed} записей"
            )
        return removed

    except Exception as exc:
        logger.warning(
            f"excel_cache: invalidate_region failed (reg={reg_code}): {exc}"
        )
        return 0


# ====================================================================
# INVALIDATE BY KEY (reg_code, dat_hash)
# ====================================================================
async def invalidate_by_dat(reg_code: str, dat_list: List[str]) -> int:
    """
    Удаляет одну конкретную запись кэша Excel по ключу (reg_code, dat_hash).

    Используется когда обновились данные только за конкретный период
    (например, уточнили карточки за март 2026 — нет смысла сбрасывать
    весь регион).

    Возвращает количество удалённых строк (0 или 1).
    """
    if not dat_list or not is_db_ready():
        return 0

    pool = get_pool()
    if pool is None:
        return 0

    dat_hash = _make_dat_hash(dat_list)

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                DELETE FROM excel_cache
                WHERE reg_code = %(reg)s AND dat_hash = %(hash)s
                """,
                params={"reg": reg_code, "hash": dat_hash},
                prepare=False,
            )
            await conn.commit()
            removed = cur.rowcount or 0

        if removed > 0:
            logger.info(
                f"excel_cache: invalidate_by_dat({reg_code}, "
                f"hash={dat_hash[:8]}..) — удалено {removed} записей"
            )
        return removed

    except Exception as exc:
        logger.warning(
            f"excel_cache: invalidate_by_dat failed (reg={reg_code}): {exc}"
        )
        return 0


# ====================================================================
# CLEANUP OLD — удаление протухших записей
# ====================================================================
async def cleanup_old_excel() -> int:
    """
    Физически удаляет протухшие записи (expires_at < NOW()).

    Вызывается из background-задачи main.py (_cleanup_loop).
    """
    if not is_db_ready():
        return 0

    pool = get_pool()
    if pool is None:
        return 0

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM excel_cache WHERE expires_at < NOW()",
                prepare=False,
            )
            await conn.commit()
            removed = cur.rowcount or 0

        if removed > 0:
            logger.info(
                f"excel_cache: cleanup_old_excel — удалено {removed} "
                f"протухших записей"
            )
        return removed

    except Exception as exc:
        logger.warning(f"excel_cache: cleanup_old_excel failed: {exc}")
        return 0


# ====================================================================
# STATS — для диагностики (/health/db/excel)
# ====================================================================
async def get_cache_stats() -> dict:
    """
    Возвращает статистику кэша Excel для диагностики.
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
                    COALESCE(SUM(total_dtp) FILTER (WHERE expires_at > NOW()), 0) AS total_dtp_cached,
                    COALESCE(SUM(file1_size + file2_size) FILTER (WHERE expires_at > NOW()), 0) AS total_bytes,
                    COUNT(DISTINCT reg_code) FILTER (WHERE expires_at > NOW()) AS regions_cached,
                    MIN(expires_at) FILTER (WHERE expires_at > NOW()) AS oldest_expiry,
                    MAX(expires_at) FILTER (WHERE expires_at > NOW()) AS newest_expiry
                FROM excel_cache
                """,
                prepare=False,
            )
            row = await cur.fetchone()

            # Top-5 регионов по размеру кэша
            cur = await conn.execute(
                """
                SELECT reg_code,
                       COUNT(*) AS entries,
                       SUM(total_dtp) AS dtp,
                       SUM(file1_size + file2_size) AS bytes,
                       MAX(region_name) AS region_name
                FROM excel_cache
                WHERE expires_at > NOW()
                GROUP BY reg_code
                ORDER BY bytes DESC NULLS LAST
                LIMIT 5
                """,
                prepare=False,
            )
            top_regions = await cur.fetchall()

        total_bytes = row["total_bytes"] if row else 0
        return {
            "configured": True,
            "ready": True,
            "total_entries": row["total_entries"] if row else 0,
            "valid_entries": row["valid_entries"] if row else 0,
            "total_dtp_cached": row["total_dtp_cached"] if row else 0,
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / (1024 * 1024), 2),
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
                    "dtp": r["dtp"],
                    "bytes": r["bytes"],
                    "mb": round(r["bytes"] / (1024 * 1024), 2) if r["bytes"] else 0,
                    "region_name": r["region_name"],
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
