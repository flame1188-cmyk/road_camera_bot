"""
Глобальный in-memory кэш загруженных данных ДТП.

Кэширует результат запросов к API ГИБДД по ключу (reg_code, dat_tuple).
Разделяется между всеми пользователями — если один пользователь
загрузил данные за регион/период, другие получат их мгновенно.

Используется:
  - _fetch_cards_for_period() в bot.py для основного и прошлого года
  - preload-загрузкой в фоне после выгрузки текущего периода

Типы записей:
  - "current": данные за запрошенный период (set при выгрузке)
  - "prev":    данные за прошлый год (set при анализе/очагах или preload)
"""

import logging
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# ========================
# Настройки кэша
# ========================
_MAX_ENTRIES = 100       # максимум записей в кэше

# TTL берётся из env CARDS_CACHE_TTL_SECONDS (по умолчанию 3600 = 1 час),
# чтобы in-memory LRU и PostgreSQL-кэш использовали одинаковое время жизни.
# Это важно: если TTL рассинхронизируется, в L1 будут записи, которых
# уже нет в L2 (и наоборот) — плохо для диагностики.
try:
    from config import CARDS_CACHE_TTL_SECONDS
    _TTL_SECONDS = CARDS_CACHE_TTL_SECONDS
except Exception:
    _TTL_SECONDS = 3600


class _DataCache:
    """Потокобезопасный LRU-кэш с TTL."""

    def __init__(self, max_entries: int = _MAX_ENTRIES, ttl: int = _TTL_SECONDS):
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._ttl = ttl
        # Счётчики попаданий/промахов для диагностики
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _make_key(reg_code: str, dat_list: list[str]) -> str:
        """Формирует ключ кэша из кода региона и списка дат."""
        return f"{reg_code}:{','.join(dat_list)}"

    def get(self, reg_code: str, dat_list: list[str]) -> tuple[list[dict], list[str]] | None:
        """
        Возвращает (cards, errors) из кэша или None, если запись
        отсутствует или просрочена.
        """
        key = self._make_key(reg_code, dat_list)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self.misses += 1
                return None

            if time.monotonic() - entry["ts"] > self._ttl:
                # Просрочена — удаляем
                del self._cache[key]
                self.misses += 1
                logger.debug(f"Кэш: запись {key} просрочена, удалена")
                return None

            # Перемещаем в конец (LRU)
            self._cache.move_to_end(key)
            self.hits += 1
            logger.debug(
                f"Кэш: HIT {key} "
                f"({len(entry['cards'])} ДТП, возраст {time.monotonic() - entry['ts']:.0f}с)"
            )
            return entry["cards"], entry["errors"]

    def put(self, reg_code: str, dat_list: list[str],
            cards: list[dict], errors: list[str]) -> None:
        """Сохраняет результат в кэш."""
        key = self._make_key(reg_code, dat_list)
        with self._lock:
            # Если ключ уже есть — обновляем
            if key in self._cache:
                self._cache.move_to_end(key)

            self._cache[key] = {
                "cards": cards,
                "errors": errors,
                "ts": time.monotonic(),
            }

            # Evict старых записей (LRU)
            while len(self._cache) > self._max_entries:
                evicted_key, _ = self._cache.popitem(last=False)
                logger.debug(f"Кэш: evict {evicted_key} (лимит {_max_entries})")

            logger.debug(
                f"Кэш: PUT {key} ({len(cards)} ДТП), "
                f"размер кэша: {len(self._cache)}/{self._max_entries}"
            )

    def has(self, reg_code: str, dat_list: list[str]) -> bool:
        """Быстрая проверка наличия валидной записи (без извлечения)."""
        return self.get(reg_code, dat_list) is not None

    def invalidate(self, reg_code: str, dat_list: list[str]) -> None:
        """Удаляет конкретную запись из кэша."""
        key = self._make_key(reg_code, dat_list)
        with self._lock:
            self._cache.pop(key, None)

    def invalidate_by_region(self, reg_code: str) -> int:
        """Удаляет ВСЕ записи кэша для заданного региона.

        Возвращает количество удалённых записей.
        Безопаснее чем selective invalidate — не зависит от точного
        совпадения формата dat_list.
        """
        prefix = f"{reg_code}:"
        removed = 0
        with self._lock:
            keys_to_remove = [k for k in self._cache if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._cache[k]
                removed += 1
        if removed:
            logger.info(f"Кэш: удалены {removed} записей региона {reg_code}")
        else:
            logger.debug(f"Кэш: нет записей региона {reg_code} для удаления")
        return removed

    def clear(self) -> None:
        """Очищает весь кэш и освобождает ссылки на карточки."""
        with self._lock:
            n = len(self._cache)
            self._cache.clear()
        logger.info(f"Кэш: полностью очищен ({n} записей удалено)")

    def stats_dict(self) -> dict[str, int]:
        """Статистика кэша для программного доступа."""
        with self._lock:
            now = time.monotonic()
            valid = sum(
                1 for e in self._cache.values()
                if now - e["ts"] <= self._ttl
            )
            total_cards = sum(len(e["cards"]) for e in self._cache.values())
            return {
                "entries": len(self._cache),
                "valid": valid,
                "total_cards_cached": total_cards,
            }

    def stats(self) -> str:
        """Статистика кэша в виде строки для логирования."""
        s = self.stats_dict()
        return (
            f"cache: {s['entries']}/{self._max_entries} записей, "
            f"hits={self.hits}, misses={self.misses}"
        )


# Глобальный экземпляр
data_cache = _DataCache()


# ============================================================
# Async-обёртки для PostgreSQL (Этап 3)
# ============================================================
# Эти функции пытаются сначала обратиться к БД через
# miniapp.backend.db.cards_cache. Если БД готова и запись есть —
# возвращают её. Если БД не готова — прозрачный fallback на
# существующий in-memory LRU (data_cache.get/put/etc).
#
# In-memory LRU всегда обновляется параллельно с БД (см. реализацию
# в cards_cache.py: put_cached_cards пишет и в БД, и в _memory_cache).
# Это даёт двухуровневый кэш:
#   L1 = in-memory (быстро, per-process, ограничен 100 записями)
#   L2 = PostgreSQL (персистентно, разделяется между воркерами)
#
# Все bot._fetch_cards_for_period и preload-функции переведены
# на эти async-обёртки.
async def get_async(reg_code: str, dat_list: list[str]) -> tuple[list[dict], list[str]] | None:
    """
    Async-версия get(): сначала БД, потом in-memory fallback.

    Возвращает (cards, errors) или None.
    """
    # 1. Сначала проверяем БД (L2) — там могут быть данные, которых
    #    ещё нет в in-memory L1 (например, после рестарта или если
    #    другой воркер их сохранил).
    try:
        from miniapp.backend.db.cards_cache import get_cached_cards
        db_result = await get_cached_cards(reg_code, dat_list)
        if db_result is not None:
            return db_result
    except Exception as e:
        logger.debug(f"data_cache.get_async: DB lookup failed: {e}")

    # 2. Fallback на in-memory LRU (L1)
    return data_cache.get(reg_code, dat_list)


async def put_async(
    reg_code: str,
    dat_list: list[str],
    cards: list[dict],
    errors: list[str],
    source: str = "api",
) -> None:
    """
    Async-версия put(): пишет и в БД (L2), и в in-memory (L1).

    Если БД недоступна — пишет только в in-memory, поведение
    идентично тому, что было до Этапа 3.
    """
    # 1. In-memory LRU — всегда (быстрый путь, не зависит от БД)
    data_cache.put(reg_code, dat_list, cards, errors)

    # 2. БД — если готова
    try:
        from miniapp.backend.db.cards_cache import put_cached_cards
        await put_cached_cards(reg_code, dat_list, cards, errors, source=source)
    except Exception as e:
        logger.debug(f"data_cache.put_async: DB write failed: {e}")


async def invalidate_by_region_async(reg_code: str) -> int:
    """
    Async-версия invalidate_by_region(): чистит БД и in-memory.

    Возвращает количество удалённых записей (max из DB/memory).
    """
    # 1. In-memory — синхронно, быстро
    memory_removed = data_cache.invalidate_by_region(reg_code)

    # 2. БД — если готова
    try:
        from miniapp.backend.db.cards_cache import invalidate_region
        db_removed = await invalidate_region(reg_code)
        return max(db_removed, memory_removed)
    except Exception as e:
        logger.debug(f"data_cache.invalidate_by_region_async: DB failed: {e}")
        return memory_removed


async def has_async(reg_code: str, dat_list: list[str]) -> bool:
    """Async-проверка наличия валидной записи в кэше."""
    return await get_async(reg_code, dat_list) is not None