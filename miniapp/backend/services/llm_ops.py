"""
LLM-аналитика: summary + Q&A.

- start_llm_summary() — асинхронная генерация LLM-резюме с timeout 5 мин
- _run_llm_summary_inner() — внутренняя логика summary (промпт + вызов LLM)
- ask_llm_question() — синхронный (но длительный) ответ на вопрос
- get_llm_providers_status() — статус доступности провайдеров (free/paid)

Провайдеры:
- "free" — ZhipuAI/GLM (LLM_API_KEY, дефолт)
- "paid" — DeepSeek (LLM_PAID_API_KEY/LLM_PAID_API_URL)

=== Sprint 2: LLM_SEMAPHORE + LLM cache ===

LLM_SEMAPHORE:
    Ограничивает количество одновременных LLM-вызовов в одном процессе.
    Защищает от 429 Too Many Requests на free-тарифе (GLM-4.7-Flash RPM~30).
    Настраивается через env LLM_MAX_CONCURRENT (по умолчанию 2).
    При превышении лимита coroutine ждёт в очереди (FIFO).

    ВНИМАНИЕ: существующий rate-limiter в llm_analyzer._do_llm_request
    (_last_llm_call_time + _MIN_LLM_INTERVAL=5.0) имеет race condition —
    два coroutine, начавшие вызов одновременно, оба пройдут проверку
    elapsed >= 5.0 и оба пойдут в LLM. Semaphore решает эту проблему
    на уровне выше (coroutine не начнёт подготовку промпта, пока не
    получит слот).

LLM cache:
    Кэширует summary в PostgreSQL (таблица llm_cache, TTL=24h).
    Cache key = SHA-256(reg_code | dat_hash | provider | prompt_hash | version).
    При cache hit — LLM не вызывается, ответ возвращается мгновенно (<100 мс).
    Кэшируется ТОЛЬКО start_llm_summary (детерминированный вход).
    ask_llm_question НЕ кэшируется (каждый вопрос уникальный).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

from . import _imports
from .analytics_ops import _get_cross_tables, ensure_comparison
from .models import AnalysisState, AnalysisStatus, Task
from .pipeline import ensure_cards, ensure_prev_cards

logger = logging.getLogger(__name__)


# ============================================================
# Sprint 2: LLM_SEMAPHORE — лимит одновременных LLM-вызовов
# ============================================================
# Защищает от 429 Too Many Requests на free-тарифе (GLM-4.7-Flash RPM~30).
# При превышении лимита coroutine ждёт в очереди (FIFO по умолчанию).
#
# ВНИМАНИЕ: создаётся ОДИН раз при импорте модуля. Если env
# LLM_MAX_CONCURRENT меняется в runtime (например, через monkeypatch
# в тестах) — semaphore НЕ пересоздаётся. Для тестов, которым нужно
# другое значение, используйте pytest-фикстуру, которая патчит
# _LLM_SEMAPHORE напрямую.
def _init_llm_semaphore() -> asyncio.Semaphore:
    """Создаёт semaphore с размером из env LLM_MAX_CONCURRENT."""
    try:
        # lazy import config (чтобы не падать, если config не настроен)
        config = _imports._import_module("config")
        limit = getattr(config, "LLM_MAX_CONCURRENT", 2)
    except Exception:
        limit = int(os.getenv("LLM_MAX_CONCURRENT", "2"))
    if limit < 1:
        limit = 1
    logger.info(
        f"LLM_SEMAPHORE: initialized with limit={limit} "
        f"(env LLM_MAX_CONCURRENT)"
    )
    return asyncio.Semaphore(limit)


_LLM_SEMAPHORE: asyncio.Semaphore = _init_llm_semaphore()


async def start_llm_summary(task: Task, provider: str = "free") -> None:
    """
    Асинхронная генерация LLM-резюме.

    provider: "free" (ZhipuAI/GLM) или "paid" (DeepSeek).

    Внутри использует asyncio.wait_for с max duration (5 минут), чтобы
    при зависании LLM (бесконечные 5xx-ретраи, потеря соединения)
    операция гарантированно завершилась с понятной ошибкой, а не висела
    в статусе RUNNING вечно.

    === Sprint 2 ===
    Оборачивает реальную работу в _LLM_SEMAPHORE, чтобы ограничить
    одновременные LLM-вызовы. Если слот занят — ждёт в очереди.
    """
    state = task.llm_summary_state
    state.status = AnalysisStatus.RUNNING
    state.progress = 5
    state.stage = "Подготовка данных..."
    state.started_at = datetime.now(timezone.utc)
    state.error = None
    state.result = None

    # Защита от зависания: максимум 5 минут на всю операцию.
    # Если LLM не ответил за 5 мин — что-то не так (сервис недоступен,
    # бесконечные ретраи,超大 промпт) — лучше упасть с понятной ошибкой.
    MAX_LLM_DURATION_SEC = 300

    # Sprint 2: проверяем кэш ДО получения semaphore.
    # Если cache hit — LLM не нужен, semaphore не нужен, ответ мгновенный.
    # Это позволяет 100+ одновременных cache hit без блокировок.
    try:
        cached = await _check_llm_cache(task, provider, state)
        if cached:
            return  # cache hit — done
    except Exception as exc:
        logger.warning(
            f"Task {task.id}: LLM cache check failed, proceeding to LLM: {exc}"
        )

    # Sprint 2: получаем слот semaphore.
    # Логируем ожидание, если слот занят (видно в логах, что кто-то ждёт).
    if _LLM_SEMAPHORE._value <= 0:  # type: ignore[attr-defined]
        # _bound_value хранит исходный лимит semaphore (Python 3.10+).
        # Если атрибут недоступен — показываем только "full".
        limit_str = str(getattr(_LLM_SEMAPHORE, "_bound_value", "?"))
        logger.info(
            f"Task {task.id}: LLM_SEMAPHORE full "
            f"(limit={limit_str}), waiting for slot..."
        )

    async with _LLM_SEMAPHORE:
        logger.debug(
            f"Task {task.id}: LLM_SEMAPHORE acquired "
            f"(available={_LLM_SEMAPHORE._value})"  # type: ignore[attr-defined]
        )

        try:
            # Запускаем реальную работу в task и ограничиваем по времени.
            # Используем shield, чтобы wait_for cancel не отменил сам task
            # (он продолжит работать в фоне, но результат уже не запишется).
            try:
                await asyncio.wait_for(
                    _run_llm_summary_inner(task, provider, state),
                    timeout=MAX_LLM_DURATION_SEC,
                )
            except asyncio.TimeoutError:
                elapsed = int(
                    (datetime.now(timezone.utc) - state.started_at).total_seconds()
                )
                err_msg = (
                    f"LLM-анализ превысил максимально допустимое время "
                    f"({MAX_LLM_DURATION_SEC} сек, прошло {elapsed} сек). "
                    f"Возможно, сервис нейросети перегружен или промпт слишком большой. "
                    f"Попробуйте ещё раз через несколько минут или используйте "
                    f"другой провайдер."
                )
                logger.error(
                    f"Task {task.id}: LLM summary timeout after {elapsed}s"
                )
                state.status = AnalysisStatus.FAILED
                state.error = err_msg
                state.stage = "Превышено время ожидания"
                state.finished_at = datetime.now(timezone.utc)

        except Exception as exc:
            logger.exception(f"Task {task.id}: LLM summary failed")
            state.status = AnalysisStatus.FAILED
            state.error = str(exc)
            state.stage = "Ошибка"
            state.finished_at = datetime.now(timezone.utc)


async def _check_llm_cache(
    task: Task, provider: str, state: AnalysisState,
) -> bool:
    """
    Sprint 2: проверяет LLM cache перед вызовом LLM.

    Возвращает True, если найден cache hit (state уже заполнен результатом).
    Возвращает False, если cache miss (нужно вызывать LLM).

    Логика:
    1. Готовит clusters_ctx и cross_tables_ctx (как в _run_llm_summary_inner).
    2. Вычисляет cache_key.
    3. Запрашивает summary из БД.
    4. Если hit — заполняет state.result и завершает операцию.
    """
    # Проверяем доступность БД и провайдера
    config = _imports._import_module("config")
    if provider == "paid":
        if not (config.LLM_PAID_API_KEY and config.LLM_PAID_API_URL):
            return False
    else:
        if not config.LLM_API_KEY:
            return False

    # Готовим clusters_ctx
    clusters_ctx = ""
    if task.clusters_state.status == AnalysisStatus.DONE and task.clusters_state.result:
        llm_module = _imports._import_module("llm_analyzer")
        fake_clusters = [
            {
                "road": c.get("road", ""),
                "zone_type": c.get("zone_type", ""),
                "total_accidents": c.get("total_accidents", 0),
                "deaths": c.get("deaths", 0),
                "injured": c.get("injured", 0),
                "dominant_type": c.get("dominant_type") or "",
                "type_counter": c.get("type_counter", {}),
                "start_pos": c.get("start_pos"),
                "end_pos": c.get("end_pos"),
                "dates": c.get("dates", []),
                "dynamics": c.get("dynamics", {}),
                "_is_lost": c.get("is_lost", False),
                "_is_prev_matched": c.get("is_prev_matched", False),
            }
            for c in task.clusters_state.result.get("clusters", [])
        ]
        clusters_ctx = llm_module.format_clusters_for_prompt(
            fake_clusters, max_clusters=10,
        )

    # Готовим cross_tables_ctx (только для free)
    cross_tables_ctx = ""
    if provider == "free":
        try:
            # Sprint 3.1: гарантируем, что task.cards есть, прежде чем
            # считать cross_tables. Если cache hit был на старой задаче
            # после рестарта — cards может быть пустым.
            cards_result = await ensure_cards(task)
            if not cards_result.get("ok"):
                logger.warning(
                    f"Task {task.id}: LLM cache check — ensure_cards failed: "
                    f"{cards_result.get('error')}"
                )
                return False

            # Гарантируем comparison (нужно для cross_tables)
            comp_result = await ensure_comparison(task)
            if comp_result.get("ok"):
                llm_module = _imports._import_module("llm_analyzer")
                analytics_module = _imports._import_module("analytics")
                current_cross = _get_cross_tables(task, prev=False)
                prev_cross = _get_cross_tables(task, prev=True) if task.prev_cards else None
                cross_tables_ctx = llm_module.format_cross_tables_for_prompt(
                    current_cross, prev_cross,
                    task.period_label,
                    task.prev_label or "",
                )
                stats = analytics_module.calculate_statistical_metrics(current_cross)
                stats_text = llm_module.format_statistical_metrics_for_prompt(stats)
                if stats_text and not stats_text.endswith("(недостаточно данных для статистического анализа)"):
                    cross_tables_ctx += "\n\n" + stats_text
        except Exception as exc:
            logger.warning(f"Task {task.id}: cache check cross-tables failed: {exc}")

    # Получаем SYSTEM_PROMPT (для prompt_hash)
    llm_module = _imports._import_module("llm_analyzer")
    system_prompt = getattr(llm_module, "SYSTEM_PROMPT", "")

    # Запрашиваем кэш
    try:
        from ..db.llm_cache import get_cached_summary
        cached = await get_cached_summary(
            reg_code=task.region_code,
            dat_list=task.dat_list,
            provider=provider,
            clusters_ctx=clusters_ctx,
            cross_tables_ctx=cross_tables_ctx,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        logger.warning(f"Task {task.id}: llm_cache GET failed: {exc}")
        return False

    if not cached:
        return False

    # Cache hit — заполняем state и завершаем
    state.result = {
        "text": cached,
        "provider": provider,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "from_cache": True,  # маркер для UI/логов
    }
    state.status = AnalysisStatus.DONE
    state.progress = 100
    state.stage = "Готово (из кэша)"
    state.finished_at = datetime.now(timezone.utc)

    # Сохраняем clusters_ctx и cross_tables_ctx для последующего PUT
    # (после cache hit мы НЕ вызываем LLM, но хотим обновить TTL записи).
    # Используем приватные атрибуты, чтобы не менять модель Task.
    task._cache_clusters_ctx = clusters_ctx  # type: ignore[attr-defined]
    task._cache_cross_tables_ctx = cross_tables_ctx  # type: ignore[attr-defined]
    task._cache_system_prompt = system_prompt  # type: ignore[attr-defined]

    # Обновляем TTL в фоне (touch existing record)
    try:
        from ..db.llm_cache import put_cached_summary
        asyncio.create_task(put_cached_summary(
            reg_code=task.region_code,
            dat_list=task.dat_list,
            provider=provider,
            summary_text=cached,
            clusters_ctx=clusters_ctx,
            cross_tables_ctx=cross_tables_ctx,
            system_prompt=system_prompt,
            clusters_count=len(task.clusters_state.result.get("clusters", []))
                if task.clusters_state.status == AnalysisStatus.DONE and task.clusters_state.result
                else None,
            total_dtp=len(task.cards) if task.cards else None,
            region_name=task.region_name,
            period_label=task.period_label,
        ))
    except Exception as exc:
        logger.debug(f"Task {task.id}: llm_cache TTL refresh failed: {exc}")

    # Sprint 6: при cache-hit тоже персистим сессию в llm_sessions —
    # иначе после рестарта приложения llm_cache может протухнуть (24ч TTL),
    # и пользователь увидит пустое резюме. А по task_id резюме будет
    # доступно через llm_sessions (без TTL).
    try:
        from ..db.repository import save_llm_session
        asyncio.create_task(save_llm_session(
            task_id=task.id,
            user_id=task.user_id,
            summary_text=cached,
            summary_provider=provider,
            summary_generated_at=state.finished_at,
        ))
    except Exception as exc:
        logger.debug(f"Task {task.id}: save_llm_session (cache-hit) failed: {exc}")

    logger.info(
        f"Task {task.id}: LLM summary done (from cache, {provider}) — "
        f"LLM call skipped"
    )
    return True


async def _run_llm_summary_inner(
    task: Task, provider: str, state: AnalysisState,
) -> None:
    """Внутренняя логика LLM-саммари — вынесена, чтобы можно было
    обернуть в asyncio.wait_for для max duration."""
    config = _imports._import_module("config")

    # Проверяем доступность провайдера
    if provider == "paid":
        if not (config.LLM_PAID_API_KEY and config.LLM_PAID_API_URL):
            raise RuntimeError(
                "Платный LLM-провайдер не настроен "
                "(LLM_PAID_API_KEY/LLM_PAID_API_URL)"
            )
    else:
        if not config.LLM_API_KEY:
            raise RuntimeError(
                "Бесплатный LLM-провайдер не настроен (LLM_API_KEY)"
            )

    state.progress = 10
    state.stage = "Восстановление данных задачи..."
    # Sprint 3.1: гарантируем, что task.cards есть (восстанавливаем из
    # cards_cache, если задача была выгружена из in-memory LRU или
    # после рестарта контейнера).
    cards_result = await ensure_cards(task)
    if not cards_result.get("ok"):
        raise RuntimeError(
            cards_result.get(
                "error",
                "Не удалось восстановить карточки текущего периода",
            )
        )

    state.stage = "Загрузка данных за прошлый год..."
    if not task.prev_cards_loaded:
        await ensure_prev_cards(task)

    state.progress = 20
    state.stage = "Расчёт сравнительных метрик..."
    comp_result = await ensure_comparison(task)
    if not comp_result.get("ok"):
        # Sprint 3.1: улучшенное сообщение — объясняем, что делать.
        err = comp_result.get("error", "Не удалось рассчитать comparison")
        if "Карточки текущего периода не загружены" in err:
            err = (
                "Карточки текущего периода не загружены. "
                "Возможно, данные устарели в кэше. "
                "Попробуйте создать новую задачу для этого региона."
            )
        raise RuntimeError(err)
    comparison = comp_result["comparison"]

    state.progress = 35
    state.stage = "Расчёт очагов ДТП для контекста..."

    # Используем готовые очаги, если уже рассчитаны
    clusters_ctx = ""
    if task.clusters_state.status == AnalysisStatus.DONE and task.clusters_state.result:
        llm_module = _imports._import_module("llm_analyzer")
        # Передаём ВСЕ очаги (а не только топ-10), чтобы format_clusters_for_prompt
        # могла разделить их по категориям (повторные/новые/исчезнувшие).
        # Раньше брали [:10] и LLM видел «солянку» из текущих и прошлых очагов.
        # Теперь метод сам сортирует и режет по max_clusters в каждой категории.
        fake_clusters = [
            {
                "road": c.get("road", ""),
                "zone_type": c.get("zone_type", ""),
                "total_accidents": c.get("total_accidents", 0),
                "deaths": c.get("deaths", 0),
                "injured": c.get("injured", 0),
                # None (смешанный тип) -> пустая строка для UI
                "dominant_type": c.get("dominant_type") or "",
                "type_counter": c.get("type_counter", {}),
                "start_pos": c.get("start_pos"),
                "end_pos": c.get("end_pos"),
                "dates": c.get("dates", []),
                # Передаём dynamics (status, prev_total, matched_prev_numbers, neighbors)
                # и флаги is_lost/is_prev_matched — по ним LLM поймёт, к какой
                # категории относится очаг.
                "dynamics": c.get("dynamics", {}),
                "_is_lost": c.get("is_lost", False),
                "_is_prev_matched": c.get("is_prev_matched", False),
            }
            for c in task.clusters_state.result.get("clusters", [])
        ]
        clusters_ctx = llm_module.format_clusters_for_prompt(
            fake_clusters, max_clusters=10,
        )

    state.progress = 50
    state.stage = "Формирование промпта..."

    llm_module = _imports._import_module("llm_analyzer")
    analytics_module = _imports._import_module("analytics")

    # Кросс-таблицы (только для бесплатного метода)
    # Phase 3.1: используем _get_cross_tables(task) — он кэширует результат
    # в task.cross_tables по id(task.cards). При повторных LLM-запросах
    # или Q&A по той же задаче — cache hit, ~0 ms вместо ~38 ms.
    cross_tables_ctx = ""
    if provider == "free":
        try:
            current_cross = _get_cross_tables(task, prev=False)
            prev_cross = _get_cross_tables(task, prev=True) if task.prev_cards else None
            cross_tables_ctx = llm_module.format_cross_tables_for_prompt(
                current_cross, prev_cross,
                task.period_label,
                task.prev_label or "",
            )
            # Этап 2: статистические метрики (severity rates, Z-score, χ²)
            stats = analytics_module.calculate_statistical_metrics(current_cross)
            stats_text = llm_module.format_statistical_metrics_for_prompt(stats)
            if stats_text and not stats_text.endswith("(недостаточно данных для статистического анализа)"):
                cross_tables_ctx += "\n\n" + stats_text
        except Exception as exc:
            logger.warning(f"Cross-tables failed: {exc}")

    state.progress = 60
    state.stage = (
        "Запрос к нейросети (15-60 сек)... "
        "Не закрывайте вкладку."
    )

    # Диагностическое логирование: размер промпта и кросс-таблиц.
    # После добавления 7 новых кросс-таблиц (БДД-факторы + профиль ТС)
    # промпт может вырасти до ~50k+ символов, что вызывает 500-е ошибки
    # у GLM-4.7-Flash. Логируем состав, чтобы видеть, какие таблицы
    # раздули промпт.
    logger.info(
        f"Task {task.id}: LLM prompt sizes — "
        f"clusters_ctx={len(clusters_ctx)} симв., "
        f"cross_tables_ctx={len(cross_tables_ctx)} симв., "
        f"provider={provider}"
    )

    # Вызываем LLM с уменьшенным числом ретраев (3 вместо 5) — для summary
    # долгие ретраи (7.5 мин) плохой UX, лучше быстро упасть и дать
    # пользователю кнопку «Повторить».
    summary = await llm_module.get_ai_summary(
        comparison=comparison,
        reg_name=task.region_name,
        current_label=task.period_label,
        prev_label=task.prev_label or "прошлый период",
        raw_supplement="",
        news_context="",
        clusters_context=clusters_ctx,
        cross_tables_context=cross_tables_ctx,
        provider=provider,
        current_cards=task.cards if provider == "paid" else None,
        prev_cards=task.prev_cards if provider == "paid" else None,
        max_retries=3,
    )

    state.result = {
        "text": summary,
        "provider": provider,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    state.status = AnalysisStatus.DONE
    state.progress = 100
    state.stage = "Готово"
    state.finished_at = datetime.now(timezone.utc)

    logger.info(f"Task {task.id}: LLM summary done ({provider})")

    # Sprint 2: сохраняем summary в кэш (fire-and-forget).
    # Если БД недоступна — put_cached_summary сам становится no-op.
    # Если сохранение упадёт — это не должно влиять на успешный результат.
    try:
        from ..db.llm_cache import put_cached_summary
        system_prompt = getattr(llm_module, "SYSTEM_PROMPT", "")
        clusters_count = (
            len(task.clusters_state.result.get("clusters", []))
            if task.clusters_state.status == AnalysisStatus.DONE and task.clusters_state.result
            else None
        )
        asyncio.create_task(put_cached_summary(
            reg_code=task.region_code,
            dat_list=task.dat_list,
            provider=provider,
            summary_text=summary,
            clusters_ctx=clusters_ctx,
            cross_tables_ctx=cross_tables_ctx,
            system_prompt=system_prompt,
            clusters_count=clusters_count,
            total_dtp=len(task.cards) if task.cards else None,
            region_name=task.region_name,
            period_label=task.period_label,
        ))
    except Exception as exc:
        logger.debug(f"Task {task.id}: llm_cache PUT failed: {exc}")

    # Sprint 6: персистим сессию в llm_sessions (как в streaming-версии).
    try:
        from ..db.repository import save_llm_session
        asyncio.create_task(save_llm_session(
            task_id=task.id,
            user_id=task.user_id,
            summary_text=summary,
            summary_provider=provider,
            summary_generated_at=state.finished_at,
        ))
    except Exception as exc:
        logger.debug(f"Task {task.id}: save_llm_session failed: {exc}")


async def ask_llm_question(
    task: Task,
    question: str,
    provider: str = "free",
) -> Dict[str, Any]:
    """
    Синхронный (но длительный) ответ на вопрос пользователя.

    Не использует state-машину — просто вызывает LLM и возвращает ответ.
    """
    if not question or len(question.strip()) < 3:
        return {"ok": False, "error": "Слишком короткий вопрос"}

    try:
        config = _imports._import_module("config")
        if provider == "paid":
            if not (config.LLM_PAID_API_KEY and config.LLM_PAID_API_URL):
                return {"ok": False, "error": "Платный LLM не настроен"}
        else:
            if not config.LLM_API_KEY:
                return {"ok": False, "error": "Бесплатный LLM не настроен"}

        # Sprint 3.1: гарантируем, что task.cards есть (восстановление
        # из cards_cache для старых задач после рестарта).
        cards_result = await ensure_cards(task)
        if not cards_result.get("ok"):
            return {"ok": False, "error": cards_result.get("error")}

        # Гарантируем comparison
        comp_result = await ensure_comparison(task)
        if not comp_result.get("ok"):
            return {"ok": False, "error": comp_result.get("error")}
        comparison = comp_result["comparison"]

        llm_module = _imports._import_module("llm_analyzer")
        analytics_module = _imports._import_module("analytics")

        # Кросс-таблицы (только для бесплатного)
        # Phase 3.1: используем кэш через _get_cross_tables — при повторных
        # Q&A по той же задаче cross_tables уже посчитаны, ~0 ms вместо ~38 ms.
        cross_tables_ctx = ""
        if provider == "free":
            try:
                current_cross = _get_cross_tables(task, prev=False)
                cross_tables_ctx = llm_module.format_cross_tables_for_prompt(
                    current_cross, None,
                    task.period_label,
                    task.prev_label or "",
                )
                # Этап 2: статистические метрики (severity rates, Z-score, χ²)
                stats = analytics_module.calculate_statistical_metrics(current_cross)
                stats_text = llm_module.format_statistical_metrics_for_prompt(stats)
                if stats_text and not stats_text.endswith("(недостаточно данных для статистического анализа)"):
                    cross_tables_ctx += "\n\n" + stats_text
            except Exception as exc:
                # Не валить весь Q&A, если кросс-таблицы упали —
                # LLM ответит на основе comparison + clusters_context.
                # Но залогировать нужно, иначе ошибка будет невидимой.
                logger.warning(
                    f"Task {task.id}: Q&A cross-tables failed: {exc}"
                )

        # Очаги (если есть)
        clusters_ctx = ""
        if task.clusters_state.status == AnalysisStatus.DONE and task.clusters_state.result:
            # Передаём ВСЕ очаги с dynamics — format_clusters_for_prompt
            # сама разделит по категориям (повторные/новые/исчезнувшие).
            fake_clusters = [
                {
                    "road": c.get("road", ""),
                    "zone_type": c.get("zone_type", ""),
                    "total_accidents": c.get("total_accidents", 0),
                    "deaths": c.get("deaths", 0),
                    "injured": c.get("injured", 0),
                    # None (смешанный тип) -> пустая строка для UI
                    "dominant_type": c.get("dominant_type") or "",
                    "type_counter": c.get("type_counter", {}),
                    "start_pos": c.get("start_pos"),
                    "end_pos": c.get("end_pos"),
                    "dates": c.get("dates", []),
                    "dynamics": c.get("dynamics", {}),
                    "_is_lost": c.get("is_lost", False),
                    "_is_prev_matched": c.get("is_prev_matched", False),
                }
                for c in task.clusters_state.result.get("clusters", [])
            ]
            clusters_ctx = llm_module.format_clusters_for_prompt(
                fake_clusters, max_clusters=10,
            )

        # Преобразуем сохранённую историю Q&A (для UI) в формат OpenAI
        # и передаём в LLM — чтобы модель понимала follow-up-вопросы.
        # Берём последние 12 сообщений (6 пар Q&A), чтобы не раздувать промпт.
        history_for_llm: list[dict[str, str]] = []
        for h in task.llm_qa_history:
            q = h.get("question", "")
            a = h.get("answer", "")
            if q:
                history_for_llm.append({"role": "user", "content": q})
            if a:
                history_for_llm.append({"role": "assistant", "content": a})
        if len(history_for_llm) > 12:
            history_for_llm = history_for_llm[-12:]

        # Диагностическое логирование: видно, доходит ли история до LLM
        hist_total_chars = sum(len(m.get("content", "")) for m in history_for_llm)
        logger.info(
            f"Task {task.id}: LLM ask — "
            f"qa_history={len(task.llm_qa_history)} records, "
            f"history_for_llm={len(history_for_llm)} msgs, "
            f"history_chars={hist_total_chars}, "
            f"provider={provider}"
        )

        # Sprint 2: получаем слот semaphore для LLM-вызова.
        # Подготовка промпта (comparison, cross_tables, clusters) идёт БЕЗ
        # semaphore — это локальные вычисления, не требующие LLM.
        # Только сам HTTP-вызов к LLM — под semaphore.
        if _LLM_SEMAPHORE._value <= 0:  # type: ignore[attr-defined]
            logger.info(
                f"Task {task.id}: LLM_SEMAPHORE full (Q&A), waiting for slot..."
            )

        async with _LLM_SEMAPHORE:
            answer = await llm_module.get_ai_answer(
                question=question,
                comparison=comparison,
                reg_name=task.region_name,
                current_label=task.period_label,
                prev_label=task.prev_label or "прошлый период",
                raw_supplement="",
                news_context="",
                clusters_context=clusters_ctx,
                cross_tables_context=cross_tables_ctx,
                provider=provider,
                history=history_for_llm,
            )

        # Сохраняем в историю
        qa_timestamp = datetime.now(timezone.utc)
        task.llm_qa_history.append({
            "question": question,
            "answer": answer,
            "provider": provider,
            "timestamp": qa_timestamp.isoformat(),
        })
        # Ограничиваем историю 10 записями
        if len(task.llm_qa_history) > 10:
            task.llm_qa_history = task.llm_qa_history[-10:]

        # Sprint 6: персистим Q&A в llm_sessions (как в streaming-версии).
        try:
            from ..db.repository import append_qa_entry
            asyncio.create_task(append_qa_entry(
                task_id=task.id,
                user_id=task.user_id,
                question=question,
                answer=answer,
                provider=provider,
                timestamp=qa_timestamp,
            ))
        except Exception as exc:
            logger.debug(f"Task {task.id}: append_qa_entry (sync) failed: {exc}")

        return {"ok": True, "answer": answer, "provider": provider}

    except Exception as exc:
        logger.exception(f"Task {task.id}: LLM ask failed")
        return {"ok": False, "error": str(exc)}


def get_llm_providers_status() -> Dict[str, bool]:
    """Возвращает статус доступности LLM-провайдеров."""
    try:
        config = _imports._import_module("config")
        return {
            "free": bool(config.LLM_API_KEY),
            "paid": bool(
                getattr(config, "LLM_PAID_API_KEY", None)
                and getattr(config, "LLM_PAID_API_URL", None)
            ),
            "free_model": getattr(config, "LLM_MODEL", "glm-4-flash"),
            "paid_model": getattr(config, "LLM_PAID_MODEL", "deepseek-chat"),
        }
    except Exception:
        return {"free": False, "paid": False,
                "free_model": "", "paid_model": ""}


# ============================================================
# Sprint 4: Streaming LLM (SSE)
# ============================================================
# Две streaming-функции для генерации контента token-by-token:
#   - ask_llm_question_stream() — Q&A (не кэшируется)
#   - stream_llm_summary()      — резюме (с cache check + cache put)
#
# Обе возвращают AsyncIterator[str] — дельты контента.
# Caller (router) оборачивает в EventSourceResponse и эмитит SSE-события:
#   event: delta  data: "токен..."
#   event: done   data: "полный текст"
#   event: error  data: "сообщение об ошибке"
#
# Semaphore: обе функции приобретают _LLM_SEMAPHORE ТОЛЬКО на время
# HTTP-stream'а к LLM. Подготовка промпта (comparison, cross_tables,
# clusters) идёт БЕЗ semaphore — это локальные вычисления.

from typing import AsyncIterator


async def ask_llm_question_stream(
    task: Task,
    question: str,
    provider: str = "free",
) -> AsyncIterator[str]:
    """
    Streaming-версия ask_llm_question: yields дельты ответа по мере
    поступления от LLM.

    Семантика:
      - До первого yield: может поднять RuntimeError (нет ключа, не удалось
        подготовить промпт) или ValueError (короткий вопрос). Caller решает,
        как показать — обычно через SSE error event.
      - После первого yield: при обрыве потока просто завершаем генератор.
        Partial-результат НЕ сохраняется в history (сохраняем только полный).
      - Нормальное завершение: сохраняем Q&A в task.llm_qa_history, caller
        эмитит done event.

    НЕ кэшируется (каждый вопрос уникальный).
    """
    if not question or len(question.strip()) < 3:
        raise ValueError("Слишком короткий вопрос")
    if len(question) > 1000:
        raise ValueError("Слишком длинный вопрос (макс. 1000 символов)")

    config = _imports._import_module("config")
    if provider == "paid":
        if not (config.LLM_PAID_API_KEY and config.LLM_PAID_API_URL):
            raise RuntimeError("Платный LLM не настроен")
    else:
        if not config.LLM_API_KEY:
            raise RuntimeError("Бесплатный LLM не настроен")

    # Sprint 3.1: гарантируем task.cards (восстановление из cards_cache).
    cards_result = await ensure_cards(task)
    if not cards_result.get("ok"):
        raise RuntimeError(cards_result.get("error", "Не удалось загрузить cards"))

    # Гарантируем comparison
    comp_result = await ensure_comparison(task)
    if not comp_result.get("ok"):
        raise RuntimeError(comp_result.get("error", "Не удалось рассчитать comparison"))
    comparison = comp_result["comparison"]

    llm_module = _imports._import_module("llm_analyzer")
    analytics_module = _imports._import_module("analytics")

    # Кросс-таблицы (только для бесплатного)
    cross_tables_ctx = ""
    if provider == "free":
        try:
            current_cross = _get_cross_tables(task, prev=False)
            cross_tables_ctx = llm_module.format_cross_tables_for_prompt(
                current_cross, None,
                task.period_label,
                task.prev_label or "",
            )
            stats = analytics_module.calculate_statistical_metrics(current_cross)
            stats_text = llm_module.format_statistical_metrics_for_prompt(stats)
            if stats_text and not stats_text.endswith("(недостаточно данных для статистического анализа)"):
                cross_tables_ctx += "\n\n" + stats_text
        except Exception as exc:
            logger.warning(f"Task {task.id}: Q&A stream cross-tables failed: {exc}")

    # Очаги (если есть)
    clusters_ctx = ""
    if task.clusters_state.status == AnalysisStatus.DONE and task.clusters_state.result:
        fake_clusters = [
            {
                "road": c.get("road", ""),
                "zone_type": c.get("zone_type", ""),
                "total_accidents": c.get("total_accidents", 0),
                "deaths": c.get("deaths", 0),
                "injured": c.get("injured", 0),
                "dominant_type": c.get("dominant_type") or "",
                "type_counter": c.get("type_counter", {}),
                "start_pos": c.get("start_pos"),
                "end_pos": c.get("end_pos"),
                "dates": c.get("dates", []),
                "dynamics": c.get("dynamics", {}),
                "_is_lost": c.get("is_lost", False),
                "_is_prev_matched": c.get("is_prev_matched", False),
            }
            for c in task.clusters_state.result.get("clusters", [])
        ]
        clusters_ctx = llm_module.format_clusters_for_prompt(
            fake_clusters, max_clusters=10,
        )

    # История Q&A → формат OpenAI (последние 12 сообщений = 6 пар)
    history_for_llm: list[dict[str, str]] = []
    for h in task.llm_qa_history:
        q = h.get("question", "")
        a = h.get("answer", "")
        if q:
            history_for_llm.append({"role": "user", "content": q})
        if a:
            history_for_llm.append({"role": "assistant", "content": a})
    if len(history_for_llm) > 12:
        history_for_llm = history_for_llm[-12:]

    logger.info(
        f"Task {task.id}: LLM ask stream — "
        f"qa_history={len(task.llm_qa_history)} records, "
        f"history_for_llm={len(history_for_llm)} msgs, "
        f"provider={provider}"
    )

    # Semaphore — только на HTTP-стрим к LLM.
    if _LLM_SEMAPHORE._value <= 0:  # type: ignore[attr-defined]
        logger.info(
            f"Task {task.id}: LLM_SEMAPHORE full (Q&A stream), waiting for slot..."
        )

    accumulated = []
    # Sprint 5: явная ссылка на inner LLM-stream generator.
    # При cancel/disconnect от клиента — вызываем aclose(), что обрывает
    # HTTP-stream к ZhipuAI через async with client.stream(...) внутри
    # _do_llm_stream_request.
    llm_stream = llm_module.get_ai_answer_stream(
        question=question,
        comparison=comparison,
        reg_name=task.region_name,
        current_label=task.period_label,
        prev_label=task.prev_label or "прошлый период",
        raw_supplement="",
        news_context="",
        clusters_context=clusters_ctx,
        cross_tables_context=cross_tables_ctx,
        provider=provider,
        history=history_for_llm,
    )
    async with _LLM_SEMAPHORE:
        try:
            async for delta in llm_stream:
                accumulated.append(delta)
                yield delta
        except (asyncio.CancelledError, GeneratorExit):
            # Клиент отменил стрим (AbortController / http.disconnect).
            # Закрываем inner generator — это закрывает httpx.stream()
            # к ZhipuAI, экономя токены.
            logger.info(
                f"Task {task.id}: LLM ask stream cancelled by client "
                f"(partial_len={len(''.join(accumulated))})"
            )
            try:
                await llm_stream.aclose()
            except Exception:
                pass
            raise
        finally:
            try:
                await llm_stream.aclose()
            except Exception:
                pass

    # Stream завершился нормально — сохраняем в history.
    # Если стрим оборвался (exception), мы сюда не попадём — partial не сохраняем.
    full_answer = "".join(accumulated)
    if full_answer.strip():
        qa_timestamp = datetime.now(timezone.utc)
        task.llm_qa_history.append({
            "question": question,
            "answer": full_answer,
            "provider": provider,
            "timestamp": qa_timestamp.isoformat(),
        })
        if len(task.llm_qa_history) > 10:
            task.llm_qa_history = task.llm_qa_history[-10:]
        logger.info(
            f"Task {task.id}: LLM ask stream done — "
            f"answer_len={len(full_answer)}, saved to history"
        )

        # Sprint 6: персистим Q&A в llm_sessions.qa_history (atomic append).
        # Fire-and-forget — не роняем ответ при ошибке БД.
        try:
            from ..db.repository import append_qa_entry
            asyncio.create_task(append_qa_entry(
                task_id=task.id,
                user_id=task.user_id,
                question=question,
                answer=full_answer,
                provider=provider,
                timestamp=qa_timestamp,
            ))
        except Exception as exc:
            logger.debug(f"Task {task.id}: append_qa_entry failed: {exc}")
    else:
        logger.warning(
            f"Task {task.id}: LLM ask stream — empty answer (0 chunks), "
            f"NOT saved to history, provider={provider}"
        )


async def stream_llm_summary(
    task: Task, provider: str = "free",
) -> AsyncIterator[str]:
    """
    Streaming-версия start_llm_summary: yields дельты резюме по мере
    поступления от LLM.

    Семантика:
      1. Сразу ставит state.status = RUNNING, stage = "Подготовка данных..."
      2. Проверяет LLM cache. Если hit — обновляет state (DONE) и
         yield'ит весь кэшированный текст одним куском (мгновенно).
      3. Если cache miss: готовит промпт (comparison, cross_tables, clusters),
         ставит stage = "Генерация нейросетью...", acquire semaphore,
         стримит из LLM.
      4. На нормальное завершение: сохраняет в cache (fire-and-forget),
         обновляет state (DONE), accumulated текст уже у caller'а.

    На ошибку до первого yield: поднимает RuntimeError, state → FAILED.
    На обрыв потока: state оставляем RUNNING (caller может показать partial),
      но в cache НЕ сохраняем.
    """
    state = task.llm_summary_state
    state.status = AnalysisStatus.RUNNING
    state.progress = 5
    state.stage = "Подготовка данных..."
    state.started_at = datetime.now(timezone.utc)
    state.error = None
    state.result = None

    # Cache check (без semaphore — мгновенно)
    try:
        cached = await _check_llm_cache(task, provider, state)
        if cached:
            # cache hit — state уже DONE, текст уже в state.result
            # Эмитим весь текст одним delta-событием.
            cached_text = state.result.get("text", "") if state.result else ""
            if cached_text:
                logger.info(
                    f"Task {task.id}: LLM summary stream — cache hit, "
                    f"yielding {len(cached_text)} chars instantly"
                )
                yield cached_text
            return
    except Exception as exc:
        logger.warning(
            f"Task {task.id}: LLM cache check failed (stream), "
            f"proceeding to LLM: {exc}"
        )

    # Cache miss — готовим промпт (как в _run_llm_summary_inner, но без
    # финального вызова LLM).
    config = _imports._import_module("config")
    if provider == "paid":
        if not (config.LLM_PAID_API_KEY and config.LLM_PAID_API_URL):
            state.status = AnalysisStatus.FAILED
            state.error = "Платный LLM не настроен"
            state.stage = "Ошибка"
            state.finished_at = datetime.now(timezone.utc)
            raise RuntimeError("Платный LLM не настроен")
    else:
        if not config.LLM_API_KEY:
            state.status = AnalysisStatus.FAILED
            state.error = "Бесплатный LLM не настроен"
            state.stage = "Ошибка"
            state.finished_at = datetime.now(timezone.utc)
            raise RuntimeError("Бесплатный LLM не настроен")

    state.progress = 10
    state.stage = "Восстановление данных задачи..."
    cards_result = await ensure_cards(task)
    if not cards_result.get("ok"):
        err = cards_result.get("error", "Не удалось восстановить cards")
        state.status = AnalysisStatus.FAILED
        state.error = err
        state.stage = "Ошибка"
        state.finished_at = datetime.now(timezone.utc)
        raise RuntimeError(err)

    state.stage = "Загрузка данных за прошлый год..."
    if not task.prev_cards_loaded:
        await ensure_prev_cards(task)

    state.progress = 20
    state.stage = "Расчёт сравнительных метрик..."
    comp_result = await ensure_comparison(task)
    if not comp_result.get("ok"):
        err = comp_result.get("error", "Не удалось рассчитать comparison")
        state.status = AnalysisStatus.FAILED
        state.error = err
        state.stage = "Ошибка"
        state.finished_at = datetime.now(timezone.utc)
        raise RuntimeError(err)
    comparison = comp_result["comparison"]

    state.progress = 35
    state.stage = "Расчёт очагов ДТП для контекста..."
    clusters_ctx = ""
    if task.clusters_state.status == AnalysisStatus.DONE and task.clusters_state.result:
        llm_module = _imports._import_module("llm_analyzer")
        fake_clusters = [
            {
                "road": c.get("road", ""),
                "zone_type": c.get("zone_type", ""),
                "total_accidents": c.get("total_accidents", 0),
                "deaths": c.get("deaths", 0),
                "injured": c.get("injured", 0),
                "dominant_type": c.get("dominant_type") or "",
                "type_counter": c.get("type_counter", {}),
                "start_pos": c.get("start_pos"),
                "end_pos": c.get("end_pos"),
                "dates": c.get("dates", []),
                "dynamics": c.get("dynamics", {}),
                "_is_lost": c.get("is_lost", False),
                "_is_prev_matched": c.get("is_prev_matched", False),
            }
            for c in task.clusters_state.result.get("clusters", [])
        ]
        clusters_ctx = llm_module.format_clusters_for_prompt(
            fake_clusters, max_clusters=10,
        )

    state.progress = 50
    state.stage = "Формирование промпта..."
    llm_module = _imports._import_module("llm_analyzer")
    analytics_module = _imports._import_module("analytics")

    cross_tables_ctx = ""
    if provider == "free":
        try:
            current_cross = _get_cross_tables(task, prev=False)
            prev_cross = _get_cross_tables(task, prev=True) if task.prev_cards else None
            cross_tables_ctx = llm_module.format_cross_tables_for_prompt(
                current_cross, prev_cross,
                task.period_label,
                task.prev_label or "",
            )
            stats = analytics_module.calculate_statistical_metrics(current_cross)
            stats_text = llm_module.format_statistical_metrics_for_prompt(stats)
            if stats_text and not stats_text.endswith("(недостаточно данных для статистического анализа)"):
                cross_tables_ctx += "\n\n" + stats_text
        except Exception as exc:
            logger.warning(f"Task {task.id}: summary stream cross-tables failed: {exc}")

    logger.info(
        f"Task {task.id}: LLM summary stream prompt sizes — "
        f"clusters_ctx={len(clusters_ctx)} симв., "
        f"cross_tables_ctx={len(cross_tables_ctx)} симв., "
        f"provider={provider}"
    )

    state.progress = 60
    state.stage = "Генерация нейросетью (стрим)..."

    # Semaphore — только на HTTP-стрим.
    if _LLM_SEMAPHORE._value <= 0:  # type: ignore[attr-defined]
        logger.info(
            f"Task {task.id}: LLM_SEMAPHORE full (summary stream), waiting for slot..."
        )

    accumulated = []
    try:
        async with _LLM_SEMAPHORE:
            # Sprint 5: явная ссылка для надёжной отмены.
            llm_stream = llm_module.get_ai_summary_stream(
                comparison=comparison,
                reg_name=task.region_name,
                current_label=task.period_label,
                prev_label=task.prev_label or "прошлый период",
                raw_supplement="",
                news_context="",
                clusters_context=clusters_ctx,
                cross_tables_context=cross_tables_ctx,
                provider=provider,
                current_cards=task.cards if provider == "paid" else None,
                prev_cards=task.prev_cards if provider == "paid" else None,
            )
            try:
                async for delta in llm_stream:
                    accumulated.append(delta)
                    # Прогресс плавно растёт 60→90 во время стриминга
                    if state.progress < 90:
                        state.progress = min(90, state.progress + 1)
                    yield delta
            except (asyncio.CancelledError, GeneratorExit):
                # Клиент отменил стрим. Закрываем inner generator,
                # что закрывает httpx.stream() к ZhipuAI.
                logger.info(
                    f"Task {task.id}: LLM summary stream cancelled by client "
                    f"(partial_len={len(''.join(accumulated))})"
                )
                # State: оставляем RUNNING с пометкой — пользователь видит
                # partial-результат в UI, но в кэш НЕ сохраняем.
                state.status = AnalysisStatus.FAILED
                state.error = "Генерация прервана пользователем"
                state.stage = "Прервано"
                state.finished_at = datetime.now(timezone.utc)
                try:
                    await llm_stream.aclose()
                except Exception:
                    pass
                raise
            finally:
                try:
                    await llm_stream.aclose()
                except Exception:
                    pass
    except (asyncio.CancelledError, GeneratorExit):
        # Propagate up — router обработает.
        raise
    except Exception as exc:
        # Обрыв потока или 4xx/5xx от LLM
        state.status = AnalysisStatus.FAILED
        state.error = str(exc)[:500]
        state.stage = "Ошибка генерации"
        state.finished_at = datetime.now(timezone.utc)
        logger.exception(f"Task {task.id}: LLM summary stream failed")
        raise

    # Успешное завершение стрима
    full_summary = "".join(accumulated)
    state.result = {
        "text": full_summary,
        "provider": provider,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    state.status = AnalysisStatus.DONE
    state.progress = 100
    state.stage = "Готово"
    state.finished_at = datetime.now(timezone.utc)

    logger.info(
        f"Task {task.id}: LLM summary stream done ({provider}) — "
        f"{len(full_summary)} chars"
    )

    # Сохраняем в кэш (fire-and-forget) — как в _run_llm_summary_inner
    try:
        from ..db.llm_cache import put_cached_summary
        system_prompt = getattr(llm_module, "SYSTEM_PROMPT", "")
        clusters_count = (
            len(task.clusters_state.result.get("clusters", []))
            if task.clusters_state.status == AnalysisStatus.DONE and task.clusters_state.result
            else None
        )
        asyncio.create_task(put_cached_summary(
            reg_code=task.region_code,
            dat_list=task.dat_list,
            provider=provider,
            summary_text=full_summary,
            clusters_ctx=clusters_ctx,
            cross_tables_ctx=cross_tables_ctx,
            system_prompt=system_prompt,
            clusters_count=clusters_count,
            total_dtp=len(task.cards) if task.cards else None,
            region_name=task.region_name,
            period_label=task.period_label,
        ))
    except Exception as exc:
        logger.debug(f"Task {task.id}: llm_cache PUT (stream) failed: {exc}")

    # Sprint 6: персистим сессию в llm_sessions — после рестарта
    # приложения пользователь откроет задачу и сразу увидит резюме,
    # без перегенерации (даже если llm_cache протух, по task_id резюме
    # всё ещё доступно). Fire-and-forget — не роняем стрим при ошибке.
    try:
        from ..db.repository import save_llm_session
        asyncio.create_task(save_llm_session(
            task_id=task.id,
            user_id=task.user_id,
            summary_text=full_summary,
            summary_provider=provider,
            summary_generated_at=state.finished_at,
        ))
    except Exception as exc:
        logger.debug(f"Task {task.id}: save_llm_session (stream) failed: {exc}")

