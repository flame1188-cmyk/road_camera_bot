"""
Кэш очагов концентрации ДТП в PostgreSQL (Этап 4).

По аналогии с cards_cache.py — персистентное SQL-хранилище для
финального результата расчёта очагов (clusters_state.result).

Зачем:
  Расчёт очагов — длительная операция (15-30 сек):
  OSM Overpass + классификация + кластеризация + динамика vs АППГ.
  Если два пользователя запускают очаги по одному и тому же
  региону+периоду — второй сейчас ждёт зря, результат идентичен.

  Кэш позволяет переиспользовать результат между:
  - разными пользователями
  - разными сессиями одного пользователя
  - перезапусками приложения

Ключ кэша:
  (reg_code, current_dat_hash, prev_dat_hash)

  где dat_hash = MD5 от отсортированного списка "m.YYYY" дат.
  Сортировка гарантирует стабильный ключ независимо от порядка месяцев.

  prev_dat_hash = NULL если АППГ не используется.

Что кэшируется:
  1. payload — финальный сериализованный result (clusters_state.result),
     словарь с clusters/preclusters/dynamics_summary/... Размер 50-200 KB.
  2. raw_clusters — сырые очаги с cards внутри (нужны для продвинутой
     карты со слоями/попапами и Excel-выгрузки с детализацией ДТП).
     Размер 1-2 MB.
  3. raw_preclusters — сырые предочаги с cards внутри. Размер 0.5-1 MB.

  Если raw_clusters/raw_preclusters не закэшированы (старая запись
  или сбой сериализации) — при cache hit вернётся только payload,
  карта упадёт в fallback (simple map), Excel вернёт None. Это
  самовосстанавливается: после протухания TTL следующий PUT сохранит
  всё целиком.

TTL:
  По умолчанию 6 часов (21600 сек) — очаги стабильнее карточек,
  данные ГИБДД для закрытых периодов уже не меняются.
  Настраивается через env CLUSTERS_CACHE_TTL_SECONDS.

Fallback:
  Если БД недоступна — все операции no-op, расчёт идёт как раньше
  (без кэша, in-memory only).
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, List, Optional, Tuple

from psycopg.types.json import Json

from .connection import get_pool, is_db_ready

logger = logging.getLogger(__name__)

# TTL берётся из env CLUSTERS_CACHE_TTL_SECONDS (по умолчанию 21600 = 6 часов).
# См. config.py → раздел «PostgreSQL-кэш (Этап 3+)».
try:
    from config import CLUSTERS_CACHE_TTL_SECONDS
    DEFAULT_TTL_SECONDS = CLUSTERS_CACHE_TTL_SECONDS
except Exception:
    DEFAULT_TTL_SECONDS = 21600

logger.info(
    f"clusters_cache: TTL={DEFAULT_TTL_SECONDS}s "
    f"(env CLUSTERS_CACHE_TTL_SECONDS)"
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
    if not dat_list:
        return ""
    sorted_dats = sorted(dat_list)
    raw = ",".join(sorted_dats)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _make_prev_dat_hash(prev_dat_list: Optional[List[str]]) -> Optional[str]:
    """
    Хэш для прошлого периода. Возвращает None если АППГ не используется.
    """
    if not prev_dat_list:
        return None
    return _make_dat_hash(prev_dat_list)


def _compute_prev_dat_list(dat_list: List[str]) -> List[str]:
    """
    Вычисляет dat_list прошлого года: ['1.2026', ...] → ['1.2025', ...]
    (вспомогательная функция для согласованности с ensure_prev_cards).
    """
    prev = []
    for dat in dat_list:
        try:
            m, y = dat.split(".")
            prev.append(f"{m}.{int(y) - 1}")
        except Exception:
            continue
    return prev


# ====================================================================
# GET — чтение из кэша
# ====================================================================
async def get_cached_clusters(
    reg_code: str,
    current_dat_list: List[str],
    prev_dat_list: Optional[List[str]] = None,
) -> Optional[dict]:
    """
    Возвращает сохранённый результат из БД или None, если записи нет / протухла.

    Что вернётся (dict с тремя ключами):
        {
            "result": {...},          # task.clusters_state.result
            "raw_clusters": [...],    # сырые очаги с cards внутри (или None)
            "raw_preclusters": [...], # сырые предочаги с cards внутри (или None)
        }

    Где result — это именно тот dict, который кладётся в
    task.clusters_state.result:
        {
            "total_clusters": int,
            "total_lost": int,
            "total_prev_matched": int,
            "total_preclusters": int,
            "current_total_dtp": int,
            "current_deaths": int,
            "current_injured": int,
            "dynamics": {...},
            "clusters": [...],
            "preclusters": [...],
            "has_prev_data": bool,
            "prev_label": str | None,
            "current_label": str,
            "region_name": str,
        }

    raw_clusters / raw_preclusters могут быть None для старых записей
    (созданных до добавления этих колонок). В этом случае caller
    должен либо упасть в fallback, либо пересчитать raw-данные.
    """
    if not current_dat_list:
        return None

    if not is_db_ready():
        return None

    pool = get_pool()
    if pool is None:
        return None

    current_hash = _make_dat_hash(current_dat_list)
    prev_hash = _make_prev_dat_hash(prev_dat_list)

    try:
        async with pool.connection() as conn:
            # Запрос с учётом NULL prev_hash: если prev_dat_list=None,
            # ищем запись где prev_dat_hash IS NULL.
            if prev_hash is None:
                cur = await conn.execute(
                    """
                    SELECT payload, raw_clusters, raw_preclusters,
                           total_clusters, total_preclusters,
                           has_prev_data
                    FROM clusters_cache
                    WHERE reg_code = %(reg)s
                      AND current_dat_hash = %(curr)s
                      AND prev_dat_hash IS NULL
                      AND expires_at > NOW()
                    """,
                    params={"reg": reg_code, "curr": current_hash},
                    prepare=False,
                )
            else:
                cur = await conn.execute(
                    """
                    SELECT payload, raw_clusters, raw_preclusters,
                           total_clusters, total_preclusters,
                           has_prev_data
                    FROM clusters_cache
                    WHERE reg_code = %(reg)s
                      AND current_dat_hash = %(curr)s
                      AND prev_dat_hash = %(prev)s
                      AND expires_at > NOW()
                    """,
                    params={
                        "reg": reg_code,
                        "curr": current_hash,
                        "prev": prev_hash,
                    },
                    prepare=False,
                )
            row = await cur.fetchone()

        if row is None:
            # === Фаза 1.6: Prometheus metric — MISS ===
            try:
                from ..middleware.metrics import record_cache_miss
                record_cache_miss("clusters")
            except Exception:
                pass
            return None

        payload = row["payload"]
        if not payload:
            return None

        # raw_clusters / raw_preclusters могут быть NULL для старых записей
        raw_clusters = row["raw_clusters"] or None
        raw_preclusters = row["raw_preclusters"] or None

        has_raw = bool(raw_clusters or raw_preclusters)
        logger.info(
            f"clusters_cache: HIT reg={reg_code} "
            f"curr={current_hash[:8]}.. prev={prev_hash[:8] if prev_hash else 'none'}.. "
            f"({row['total_clusters']} очагов, "
            f"{row['total_preclusters']} предочагов, "
            f"raw={'yes' if has_raw else 'no'})"
        )
        # === Фаза 1.6: Prometheus metric — HIT ===
        try:
            from ..middleware.metrics import record_cache_hit
            record_cache_hit("clusters")
        except Exception:
            pass
        return {
            "result": dict(payload),
            "raw_clusters": list(raw_clusters) if raw_clusters else None,
            "raw_preclusters": list(raw_preclusters) if raw_preclusters else None,
        }

    except Exception as exc:
        logger.warning(
            f"clusters_cache: get_cached_clusters failed (reg={reg_code}): {exc}"
        )
        return None


# ====================================================================
# PUT — сохранение в кэш
# ====================================================================
def _json_safe(obj: Any) -> Any:
    """
    Рекурсивно конвертирует объект в JSON-безопасную форму.

    Проблема: raw_clusters содержит tuples (center, first_coords,
    last_coords) и, теоретически, может содержать datetime/Decimal
    в cards. json.dumps падает на таких типах.

    Решение: deep-copy с заменой tuple → list и fallback на str()
    для неизвестных типов. Это безопасно для round-trip через JSONB,
    т.к. код-потребитель (_serialize_cluster, build_*_excel_data,
    report_generator) использует индексацию [0]/[1], а не tuple-сравнения.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, tuple):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, list):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    # datetime, Decimal, set, etc. — конвертируем в строку
    return str(obj)


async def put_cached_clusters(
    reg_code: str,
    current_dat_list: List[str],
    prev_dat_list: Optional[List[str]],
    result: dict,
    raw_clusters: Optional[List[dict]] = None,
    raw_preclusters: Optional[List[dict]] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    """
    Сохраняет result + raw_clusters + raw_preclusters в БД
    (upsert: INSERT ... ON CONFLICT DO UPDATE).

    Параметры:
      result          — task.clusters_state.result (полный сериализованный dict).
      raw_clusters    — task.raw_clusters (сырые очаги с cards внутри).
                        Нужны для продвинутой карты и Excel.
      raw_preclusters — task.raw_preclusters (сырые предочаги с cards).
      ttl_seconds     — срок жизни записи.

    Если raw_clusters/raw_preclusters не переданы (None) — сохраняем
    только result. Это обратный сценарий: при cache hit caller получит
    result, но карта/Excel упадут в fallback. Такое допустимо только
    для legacy-вызовов; новый код должен всегда передавать raw-данные.
    """
    if not current_dat_list or not result:
        return

    if not is_db_ready():
        return

    pool = get_pool()
    if pool is None:
        return

    current_hash = _make_dat_hash(current_dat_list)
    prev_hash = _make_prev_dat_hash(prev_dat_list)

    # Извлекаем сводные метрики для быстрой диагностики в /health/db/clusters
    total_clusters = int(result.get("total_clusters", 0) or 0)
    total_preclusters = int(result.get("total_preclusters", 0) or 0)
    has_prev_data = bool(result.get("has_prev_data", False))
    current_label = str(result.get("current_label", "") or "")
    prev_label = result.get("prev_label")
    region_name = str(result.get("region_name", "") or "")

    # Конвертируем raw-данные в JSON-безопасную форму.
    # _json_safe делает deep-copy + конвертирует tuples/Decimal/datetime.
    # Если что-то пошло не так — логируем warning и сохраняем только result
    # (карта упадёт в fallback, но кэш самого result валиден).
    raw_clusters_json: Optional[dict] = None
    raw_preclusters_json: Optional[dict] = None
    try:
        if raw_clusters:
            raw_clusters_json = Json(_json_safe(raw_clusters))
        if raw_preclusters:
            raw_preclusters_json = Json(_json_safe(raw_preclusters))
    except Exception as exc:
        logger.warning(
            f"clusters_cache: _json_safe failed (reg={reg_code}): {exc}. "
            f"Saving result without raw_clusters."
        )
        raw_clusters_json = None
        raw_preclusters_json = None

    try:
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO clusters_cache (
                    reg_code, current_dat_hash, prev_dat_hash,
                    current_dat_list, prev_dat_list,
                    payload, raw_clusters, raw_preclusters,
                    total_clusters, total_preclusters, has_prev_data,
                    current_label, prev_label, region_name,
                    created_at, expires_at
                ) VALUES (
                    %(reg)s, %(curr)s, %(prev)s,
                    %(curr_list)s, %(prev_list)s,
                    %(payload)s, %(raw_c)s, %(raw_p)s,
                    %(tc)s, %(tpc)s, %(hpd)s,
                    %(cl)s, %(pl)s, %(rn)s,
                    NOW(),
                    NOW() + (%(ttl)s || ' seconds')::INTERVAL
                )
                ON CONFLICT (reg_code, current_dat_hash,
                             COALESCE(prev_dat_hash, ''::text))
                DO UPDATE SET
                    current_dat_list = EXCLUDED.current_dat_list,
                    prev_dat_list = EXCLUDED.prev_dat_list,
                    payload = EXCLUDED.payload,
                    raw_clusters = EXCLUDED.raw_clusters,
                    raw_preclusters = EXCLUDED.raw_preclusters,
                    total_clusters = EXCLUDED.total_clusters,
                    total_preclusters = EXCLUDED.total_preclusters,
                    has_prev_data = EXCLUDED.has_prev_data,
                    current_label = EXCLUDED.current_label,
                    prev_label = EXCLUDED.prev_label,
                    region_name = EXCLUDED.region_name,
                    created_at = NOW(),
                    expires_at = NOW() + (%(ttl)s || ' seconds')::INTERVAL
                """,
                params={
                    "reg": reg_code,
                    "curr": current_hash,
                    "prev": prev_hash,
                    "curr_list": Json(current_dat_list),
                    "prev_list": Json(prev_dat_list) if prev_dat_list else None,
                    "payload": Json(result),
                    "raw_c": raw_clusters_json,
                    "raw_p": raw_preclusters_json,
                    "tc": total_clusters,
                    "tpc": total_preclusters,
                    "hpd": has_prev_data,
                    "cl": current_label,
                    "pl": prev_label,
                    "rn": region_name,
                    "ttl": str(ttl_seconds),
                },
            )
            await conn.commit()

        # Размер raw-данных для лога (грубо — длина JSON-строки)
        raw_size = 0
        if raw_clusters:
            try:
                raw_size += len(json.dumps(_json_safe(raw_clusters), default=str))
            except Exception:
                pass
        if raw_preclusters:
            try:
                raw_size += len(json.dumps(_json_safe(raw_preclusters), default=str))
            except Exception:
                pass

        logger.info(
            f"clusters_cache: PUT reg={reg_code} "
            f"curr={current_hash[:8]}.. prev={prev_hash[:8] if prev_hash else 'none'}.. "
            f"({total_clusters} очагов, {total_preclusters} предочагов, "
            f"raw={'yes' if raw_clusters_json or raw_preclusters_json else 'no'}, "
            f"~{raw_size // 1024} KB, TTL={ttl_seconds}s)"
        )

    except Exception as exc:
        logger.warning(
            f"clusters_cache: put_cached_clusters failed (reg={reg_code}): {exc}"
        )


# ====================================================================
# INVALIDATE BY REGION
# ====================================================================
async def invalidate_region(reg_code: str) -> int:
    """
    Удаляет ВСЕ записи кэша очагов для заданного региона.

    Используется когда данные ГИБДД по региону обновились и нужно
    форсировать перерасчёт.

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
                "DELETE FROM clusters_cache WHERE reg_code = %(reg)s",
                params={"reg": reg_code},
                prepare=False,
            )
            await conn.commit()
            removed = cur.rowcount or 0

        if removed > 0:
            logger.info(
                f"clusters_cache: invalidate_region({reg_code}) — "
                f"удалено {removed} записей"
            )
        return removed

    except Exception as exc:
        logger.warning(
            f"clusters_cache: invalidate_region failed (reg={reg_code}): {exc}"
        )
        return 0


# ====================================================================
# CLEANUP OLD — удаление протухших записей
# ====================================================================
async def cleanup_old_clusters() -> int:
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
                "DELETE FROM clusters_cache WHERE expires_at < NOW()",
                prepare=False,
            )
            await conn.commit()
            removed = cur.rowcount or 0

        if removed > 0:
            logger.info(
                f"clusters_cache: cleanup_old_clusters — удалено {removed} "
                f"протухших записей"
            )
        return removed

    except Exception as exc:
        logger.warning(f"clusters_cache: cleanup_old_clusters failed: {exc}")
        return 0


# ====================================================================
# STATS — для диагностики (/health/db/clusters)
# ====================================================================
async def get_cache_stats() -> dict:
    """
    Возвращает статистику кэша очагов для диагностики.
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
                    COALESCE(SUM(total_clusters) FILTER (WHERE expires_at > NOW()), 0) AS total_clusters_cached,
                    COALESCE(SUM(total_preclusters) FILTER (WHERE expires_at > NOW()), 0) AS total_preclusters_cached,
                    COUNT(*) FILTER (WHERE expires_at > NOW() AND has_prev_data) AS entries_with_prev,
                    COUNT(DISTINCT reg_code) FILTER (WHERE expires_at > NOW()) AS regions_cached,
                    MIN(expires_at) FILTER (WHERE expires_at > NOW()) AS oldest_expiry,
                    MAX(expires_at) FILTER (WHERE expires_at > NOW()) AS newest_expiry
                FROM clusters_cache
                """,
                prepare=False,
            )
            row = await cur.fetchone()

            # Top-5 регионов по размеру кэша
            cur = await conn.execute(
                """
                SELECT reg_code,
                       COUNT(*) AS entries,
                       SUM(total_clusters) AS clusters,
                       SUM(total_preclusters) AS preclusters,
                       MAX(region_name) AS region_name
                FROM clusters_cache
                WHERE expires_at > NOW()
                GROUP BY reg_code
                ORDER BY clusters DESC NULLS LAST
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
            "total_clusters_cached": row["total_clusters_cached"] if row else 0,
            "total_preclusters_cached": row["total_preclusters_cached"] if row else 0,
            "entries_with_prev": row["entries_with_prev"] if row else 0,
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
                    "clusters": r["clusters"],
                    "preclusters": r["preclusters"],
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
