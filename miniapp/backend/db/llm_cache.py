"""
Кэш LLM-summary в PostgreSQL (Sprint 2).

Хранит готовые LLM-резюме, чтобы повторные запросы по тому же региону+
периоду+провайдеру+промпту не вызывали LLM заново (53 сек + 429 risk).

Зачем:
  LLM-summary — самое дорогое место пайплайна:
  - ~53 сек latency на GLM-4.7-Flash
  - 429 Too Many Requests при 3+ одновременных (free-тариф)
  - $0.001/request на paid-тарифе

  Если 2 пользователя запускают аналитику по одному и тому же региону+
  периоду — summary ПО БАЙТОВО идентичный (детерминированный вход:
  comparison + clusters_ctx + cross_tables_ctx). Кэш позволяет:
  - 2-й пользователь получает ответ мгновенно (<100 мс вместо 53 сек)
  - Не тратится quota LLM
  - Снижается риск 429

  Также кэш переживает рестарт приложения (state в PostgreSQL, не in-memory).

Ключ кэша:
  SHA-256 от:
    reg_code | dat_hash | provider | prompt_hash | llm_version

  Где:
    - dat_hash — MD5 от сортированного списка дат (как в excel_cache)
    - provider — 'free' / 'paid' (разные модели → разные ответы)
    - prompt_hash — MD5 от финального промпта (system + clusters_ctx +
      cross_tables_ctx). Если меняется SYSTEM_PROMPT или формат таблиц —
      кэш инвалидируется автоматически.
    - llm_version — env LLM_CACHE_VERSION (по умолчанию '1'). Позволяет
      принудительно инвалидировать кэш при релизе новой версии промпта.

Что кэшируется:
  - start_llm_summary — ДА (детерминированный вход)
  - ask_llm_question — НЕТ (каждый вопрос уникальный)

TTL:
  По умолчанию 24 часа (86400 сек). Настраивается через
  env LLM_CACHE_TTL_SECONDS.

Fallback:
  Если БД недоступна — все операции no-op, LLM вызывается как раньше.
"""
from __future__ import annotations

import hashlib
import logging
from typing import List, Optional, Tuple

from .connection import get_pool, is_db_ready

logger = logging.getLogger(__name__)

# TTL берётся из env LLM_CACHE_TTL_SECONDS (по умолчанию 86400 = 24 часа).
try:
    from config import LLM_CACHE_TTL_SECONDS
    DEFAULT_TTL_SECONDS = LLM_CACHE_TTL_SECONDS
except Exception:
    DEFAULT_TTL_SECONDS = 86400

# Версия кэша — позволяет принудительно инвалидировать все записи
# (например, при глобальном изменении SYSTEM_PROMPT или формата таблиц).
try:
    from config import LLM_CACHE_VERSION
    _CACHE_VERSION = str(LLM_CACHE_VERSION)
except Exception:
    _CACHE_VERSION = "1"

logger.info(
    f"llm_cache: TTL={DEFAULT_TTL_SECONDS}s, version={_CACHE_VERSION} "
    f"(env LLM_CACHE_TTL_SECONDS, LLM_CACHE_VERSION)"
)


# ====================================================================
# Хэширование ключей
# ====================================================================
def _make_dat_hash(dat_list: List[str]) -> str:
    """
    Вычисляет MD5-хэш от отсортированного списка дат.
    Совпадает с алгоритмом в cards_cache.py / excel_cache.py.
    """
    if not dat_list:
        return ""
    sorted_dats = sorted(dat_list)
    raw = ",".join(sorted_dats)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _make_prompt_hash(
    clusters_ctx: str,
    cross_tables_ctx: str,
    system_prompt: str = "",
) -> str:
    """
    Вычисляет MD5-хэш от контекста промпта.

    Если меняется SYSTEM_PROMPT или формат cross_tables/clusters —
    хэш меняется, кэш инвалидируется автоматически.
    """
    h = hashlib.md5()
    h.update(system_prompt.encode("utf-8"))
    h.update(b"\x00")
    h.update(clusters_ctx.encode("utf-8"))
    h.update(b"\x00")
    h.update(cross_tables_ctx.encode("utf-8"))
    return h.hexdigest()


def make_cache_key(
    reg_code: str,
    dat_list: List[str],
    provider: str,
    clusters_ctx: str,
    cross_tables_ctx: str,
    system_prompt: str = "",
) -> Tuple[str, str, str]:
    """
    Вычисляет (cache_key, dat_hash, prompt_hash) для запроса.

    Возвращает кортеж, чтобы вызывающий код мог логировать dat_hash
    и prompt_hash отдельно (для диагностики).
    """
    dat_hash = _make_dat_hash(dat_list)
    prompt_hash = _make_prompt_hash(clusters_ctx, cross_tables_ctx, system_prompt)

    raw_key = f"{reg_code}|{dat_hash}|{provider}|{prompt_hash}|{_CACHE_VERSION}"
    cache_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    return cache_key, dat_hash, prompt_hash


# ====================================================================
# GET — чтение summary из кэша
# ====================================================================
async def get_cached_summary(
    reg_code: str,
    dat_list: List[str],
    provider: str,
    clusters_ctx: str,
    cross_tables_ctx: str,
    system_prompt: str = "",
) -> Optional[str]:
    """
    Возвращает кэшированный summary или None, если записи нет / протухла.

    Параметры:
      reg_code, dat_list — ключ региона+периода.
      provider — 'free' / 'paid'.
      clusters_ctx, cross_tables_ctx — контекст промпта (для prompt_hash).
      system_prompt — системный промпт (для prompt_hash, опционально).
    """
    if not dat_list or not reg_code:
        return None

    if not is_db_ready():
        return None

    pool = get_pool()
    if pool is None:
        return None

    cache_key, dat_hash, prompt_hash = make_cache_key(
        reg_code, dat_list, provider,
        clusters_ctx, cross_tables_ctx, system_prompt,
    )

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT summary_text, EXTRACT(EPOCH FROM (NOW() - created_at)) AS age_sec
                FROM llm_cache
                WHERE cache_key = %(key)s
                  AND expires_at > NOW()
                """,
                params={"key": cache_key},
                prepare=False,
            )
            row = await cur.fetchone()

        if row is None:
            logger.info(
                f"llm_cache: MISS reg={reg_code} hash={dat_hash[:8]}.. "
                f"provider={provider} → calling LLM"
            )
            try:
                from ..middleware.metrics import record_cache_miss
                record_cache_miss("llm_summary")
            except Exception:
                pass
            return None

        summary_text = row["summary_text"]
        age_sec = int(row["age_sec"]) if row["age_sec"] else 0
        age_h = age_sec / 3600.0

        logger.info(
            f"llm_cache: HIT reg={reg_code} hash={dat_hash[:8]}.. "
            f"provider={provider} ({len(summary_text)} символов, "
            f"возраст {age_h:.1f} ч)"
        )
        try:
            from ..middleware.metrics import record_cache_hit
            record_cache_hit("llm_summary")
        except Exception:
            pass
        return summary_text

    except Exception as exc:
        logger.warning(
            f"llm_cache: get_cached_summary failed (reg={reg_code}): {exc}"
        )
        return None


# ====================================================================
# PUT — сохранение summary в кэш
# ====================================================================
async def put_cached_summary(
    reg_code: str,
    dat_list: List[str],
    provider: str,
    summary_text: str,
    clusters_ctx: str,
    cross_tables_ctx: str,
    system_prompt: str = "",
    clusters_count: Optional[int] = None,
    total_dtp: Optional[int] = None,
    region_name: str = "",
    period_label: str = "",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    """
    Сохраняет summary в БД (upsert по cache_key).
    """
    if not dat_list or not summary_text or not reg_code:
        return

    if not is_db_ready():
        return

    pool = get_pool()
    if pool is None:
        return

    cache_key, dat_hash, prompt_hash = make_cache_key(
        reg_code, dat_list, provider,
        clusters_ctx, cross_tables_ctx, system_prompt,
    )

    try:
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO llm_cache (
                    cache_key, reg_code, dat_hash, provider,
                    summary_text, prompt_hash,
                    clusters_count, total_dtp,
                    region_name, period_label,
                    created_at, expires_at
                ) VALUES (
                    %(key)s, %(reg)s, %(dhash)s, %(prov)s,
                    %(text)s, %(phash)s,
                    %(cc)s, %(td)s,
                    %(rn)s, %(pl)s,
                    NOW(),
                    NOW() + (%(ttl)s || ' seconds')::INTERVAL
                )
                ON CONFLICT (cache_key) DO UPDATE SET
                    reg_code = EXCLUDED.reg_code,
                    dat_hash = EXCLUDED.dat_hash,
                    provider = EXCLUDED.provider,
                    summary_text = EXCLUDED.summary_text,
                    prompt_hash = EXCLUDED.prompt_hash,
                    clusters_count = EXCLUDED.clusters_count,
                    total_dtp = EXCLUDED.total_dtp,
                    region_name = EXCLUDED.region_name,
                    period_label = EXCLUDED.period_label,
                    created_at = NOW(),
                    expires_at = NOW() + (%(ttl)s || ' seconds')::INTERVAL
                """,
                params={
                    "key": cache_key,
                    "reg": reg_code,
                    "dhash": dat_hash,
                    "prov": provider,
                    "text": summary_text,
                    "phash": prompt_hash,
                    "cc": clusters_count,
                    "td": total_dtp,
                    "rn": region_name,
                    "pl": period_label,
                    "ttl": str(ttl_seconds),
                },
            )
            await conn.commit()

        logger.info(
            f"llm_cache: PUT reg={reg_code} hash={dat_hash[:8]}.. "
            f"provider={provider} ({len(summary_text)} символов, "
            f"TTL={ttl_seconds}s)"
        )

    except Exception as exc:
        logger.warning(
            f"llm_cache: put_cached_summary failed (reg={reg_code}): {exc}"
        )


# ====================================================================
# Cleanup — удаление протухших записей
# ====================================================================
async def cleanup_expired_llm_cache() -> int:
    """
    Удаляет протухшие записи из llm_cache.
    Возвращает количество удалённых строк.

    Вызывается из background-задачи (как cleanup_old_tasks).
    """
    if not is_db_ready():
        return 0

    pool = get_pool()
    if pool is None:
        return 0

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM llm_cache WHERE expires_at < NOW()",
                prepare=False,
            )
            deleted = cur.rowcount or 0
            await conn.commit()

        if deleted > 0:
            logger.info(f"llm_cache: cleanup — удалено {deleted} протухших записей")
        return deleted

    except Exception as exc:
        logger.warning(f"llm_cache: cleanup failed: {exc}")
        return 0
